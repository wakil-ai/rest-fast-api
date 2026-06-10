#!/usr/bin/env python3
"""
Reproduce the production "Chat stream failed (generic): 'error'" bug locally.

Background
----------
The frontend reports `Chat stream failed (generic): "'error'"`. The single-quote
signature is the string form of a Python ``KeyError('error')`` raised somewhere in
the streaming generation pipeline. ``app/utils/streaming.py`` catches *any*
exception and forwards ``str(e)`` to the client as an SSE event:

    data: {"type": "error", "error": "'error'"}

This harness hammers the streaming endpoint (``POST /api/v3/chat/agent/stream``)
in many different ways to try to make the backend emit that ``type=="error"``
event — and flags loudly when the value matches the KeyError signature.

Usage
-----
    python scripts/repro_chat_stream_error.py                 # run all strategies
    python scripts/repro_chat_stream_error.py --strategy concurrent_same_session
    python scripts/repro_chat_stream_error.py --count 30 --concurrency 12
    BASE_URL=http://localhost:8080 API_KEY=... python scripts/repro_chat_stream_error.py

NOTE: ``create_session`` requires the user to already exist (``POST
/api/v2/history/sessions`` 404s with "User ... not found" otherwise). Point
REPRO_USER_ID at a real user id (the Mongo ``_id`` in the ``users`` collection),
e.g. ``REPRO_USER_ID=5904877504 python scripts/repro_chat_stream_error.py``.

Strategies
----------
    baseline                  one normal streamed message (sanity check)
    sequential_many           N messages back-to-back in ONE session
    concurrent_same_session   N streams at once on the SAME session_id (thread/checkpointer race)
    concurrent_many_sessions  N streams at once across N sessions (load)
    burst                     rapid fire to provoke upstream rate-limit/error chunks
    big_file_context          oversized inline file_context (blow up the model context)
    many_fake_file_ids        attach many non-existent file_ids (retrieval errors)
    real_file_uploads         upload M files, attach them all to one streamed message
    weird_inputs              empty / unicode / control-char / giant single-line queries

Nothing here mutates production — it only talks to whatever BASE_URL you point it at.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
API_KEY_NAME = os.environ.get("API_KEY_NAME", "X-API-HBAI-KEY")
API_KEY = os.environ.get(
    "API_KEY",
    "wakilaic5IiK1AqkQJS4i7gs1h2dToqDAKFkRBPjA2IFviR1CT1BxF8k9TrfyTmhNgjVnMb",
)
USER_ID = os.environ.get("REPRO_USER_ID", "106762819921547900967")

STREAM_PATH = "/api/v3/chat/agent/stream"
# History routers are mounted under <API_PREFIX>/history (see app/main.py +
# app/api/v2/history/router.py). The previous "/api/v2/sessions" / "/api/v2/files"
# paths were missing the "/history" segment and 404'd before any stream ran.
SESSION_PATH = "/api/v2/history/sessions"
FILE_PATH = "/api/v2/history/files"
USER_PATH = "/api/v2/history/users"

HEADERS = {API_KEY_NAME: API_KEY}


def _load_admin_credentials() -> tuple[str, str]:
    """Header name + value for the super-admin-only user-create endpoint.

    Order of precedence: explicit env vars > app settings (.env) > defaults.
    Importing ``settings`` lets the harness pick up the SAME super-admin key the
    local server loaded from .env, so ``ensure_user`` just works against it.
    """
    name = os.environ.get("SUPER_ADMIN_KEY_NAME")
    key = os.environ.get("SUPER_ADMIN_API_KEY")
    if name and key:
        return name, key
    try:
        from app.core.config import settings

        return (
            name or settings.SUPER_ADMIN_KEY_NAME,
            key or settings.SUPER_ADMIN_API_KEY,
        )
    except Exception:
        return (name or "x-super-admin-key", key or "super-admin")

# The smoking-gun value: str(KeyError('error')) == "'error'".
# A raw, *unwrapped* KeyError reaching app/utils/streaming.py yields exactly
# "'error'". If the same KeyError is raised inside the orchestration try-block
# it gets wrapped in ChatGenerationException (an HTTPException), whose str() is
# "500: 'error'". Match the signature as a substring so BOTH forms are flagged.
KEYERROR_SIGNATURE = "'error'"


def _matches_keyerror_signature(err_val: str) -> bool:
    # Catches "'error'" (raw) and "500: 'error'" (wrapped HTTPException detail).
    return KEYERROR_SIGNATURE in err_val


# --------------------------------------------------------------------------- #
# Result tracking
# --------------------------------------------------------------------------- #
@dataclass
class StreamResult:
    label: str
    ok: bool = False
    http_status: int | None = None
    event_types: list[str] = field(default_factory=list)
    error_event: dict[str, Any] | None = None
    is_keyerror_signature: bool = False
    transport_error: str | None = None
    duration_s: float = 0.0

    @property
    def got_error_event(self) -> bool:
        return self.error_event is not None

    def summary(self) -> str:
        if self.transport_error:
            return f"transport-error: {self.transport_error}"
        if self.is_keyerror_signature:
            return f"🎯 REPRODUCED KeyError signature -> {self.error_event!r}"
        if self.got_error_event:
            return f"⚠️  error event -> {self.error_event!r}"
        return f"ok ({len(self.event_types)} events, types={sorted(set(self.event_types))})"


# --------------------------------------------------------------------------- #
# Core: drive one streamed request and classify what came back
# --------------------------------------------------------------------------- #
async def stream_once(
    client: httpx.AsyncClient, payload: dict[str, Any], label: str
) -> StreamResult:
    res = StreamResult(label=label)
    started = time.monotonic()
    try:
        async with client.stream(
            "POST", STREAM_PATH, json=payload, headers=HEADERS
        ) as resp:
            res.http_status = resp.status_code
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                res.transport_error = f"HTTP {resp.status_code}: {body}"
                return res

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                # SSE frames are separated by a blank line
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data:
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type") if isinstance(event, dict) else None
                        res.event_types.append(str(etype))
                        if etype == "error":
                            res.error_event = event
                            err_val = str(event.get("error", ""))
                            if _matches_keyerror_signature(err_val):
                                res.is_keyerror_signature = True
            res.ok = res.error_event is None
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        res.transport_error = f"{type(exc).__name__}: {exc}"
    finally:
        res.duration_s = time.monotonic() - started
    return res


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #
async def ensure_user(client: httpx.AsyncClient) -> bool:
    """Create-or-get the repro test user so ``create_session`` stops 404ing.

    ``POST /api/v2/history/sessions`` requires the user to already exist, and the
    user-create endpoint is super-admin gated + idempotent. Returns True if the
    user exists (created or already present) after this call.
    """
    admin_name, admin_key = _load_admin_credentials()
    print(admin_name, admin_key)
    try:
        resp = await client.post(
            USER_PATH,
            json={
                "user_id": USER_ID,
                "username": "repro_stream_error",
                "first_name": "Repro",
            },
            headers={**HEADERS, admin_name: admin_key},
        )
        if resp.status_code in (200, 201):
            print(f"[user] ensured test user {USER_ID!r} (HTTP {resp.status_code})")
            return True
        print(
            f"[user] could not ensure user {USER_ID!r} (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
        print(
            "       Fix: export SUPER_ADMIN_API_KEY=<key from .env> "
            "(and SUPER_ADMIN_KEY_NAME if non-default), or set REPRO_USER_ID to an "
            "existing user id."
        )
        return False
    except httpx.HTTPError as exc:
        print(f"[user] ensure transport error: {exc}")
        return False


async def create_session(client: httpx.AsyncClient, title: str = "repro") -> str | None:
    try:
        resp = await client.post(
            SESSION_PATH,
            json={"user_id": USER_ID, "title": title},
            headers=HEADERS,
        )
        if resp.status_code in (200, 201):
            body = resp.json()
            return body.get("session_id") or body.get("_id")
        print(f"  [session] create failed HTTP {resp.status_code}: {resp.text[:200]}")
    except httpx.HTTPError as exc:
        print(f"  [session] create transport error: {exc}")
    return None


async def upload_file(
    client: httpx.AsyncClient, idx: int, content: bytes
) -> str | None:
    try:
        files = {"file": (f"repro_{idx}.txt", io.BytesIO(content), "text/plain")}
        resp = await client.post(
            FILE_PATH,
            data={"user_id": USER_ID},
            files=files,
            headers=HEADERS,
        )
        if resp.status_code == 200:
            body = resp.json()
            return body.get("file_id") or body.get("_id")
        print(f"  [upload {idx}] failed HTTP {resp.status_code}: {resp.text[:160]}")
    except httpx.HTTPError as exc:
        print(f"  [upload {idx}] transport error: {exc}")
    return None


def base_payload(session_id: str, query: str, **extra: Any) -> dict[str, Any]:
    payload = {"query": query, "user_id": USER_ID, "session_id": session_id}
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# Strategies — each returns a list[StreamResult]
# --------------------------------------------------------------------------- #
async def strat_baseline(client, args) -> list[StreamResult]:
    sid = await create_session(client, "baseline")
    if not sid:
        return []
    return [await stream_once(client, base_payload(sid, "Salom, qisqa javob ber."), "baseline")]


async def strat_sequential_many(client, args) -> list[StreamResult]:
    sid = await create_session(client, "sequential")
    if not sid:
        return []
    out = []
    for i in range(args.count):
        q = f"[{i}] O'zbekistonda mehnat huquqi haqida qisqa gapir."
        out.append(await stream_once(client, base_payload(sid, q), f"sequential[{i}]"))
    return out


async def strat_concurrent_same_session(client, args) -> list[StreamResult]:
    # Same session_id => same langgraph thread_id => checkpointer/state races.
    # This is the most promising trigger for internal KeyErrors.
    sid = await create_session(client, "concurrent-same")
    if not sid:
        return []
    tasks = [
        stream_once(client, base_payload(sid, f"[{i}] Soliq qonunchiligini tushuntir."), f"same-session[{i}]")
        for i in range(args.concurrency)
    ]
    return list(await asyncio.gather(*tasks))


async def strat_concurrent_many_sessions(client, args) -> list[StreamResult]:
    sids = await asyncio.gather(
        *(create_session(client, f"many-{i}") for i in range(args.concurrency))
    )
    tasks = [
        stream_once(client, base_payload(sid, f"[{i}] Nikoh shartnomasi haqida."), f"many-session[{i}]")
        for i, sid in enumerate(sids)
        if sid
    ]
    return list(await asyncio.gather(*tasks))


async def strat_burst(client, args) -> list[StreamResult]:
    # Rapid fire many short requests to push the upstream model into
    # rate-limit / error-chunk territory.
    sid = await create_session(client, "burst")
    if not sid:
        return []
    tasks = [
        stream_once(client, base_payload(sid, f"burst {i}: javob ber"), f"burst[{i}]")
        for i in range(args.count)
    ]
    return list(await asyncio.gather(*tasks))


async def strat_big_file_context(client, args) -> list[StreamResult]:
    sid = await create_session(client, "big-context")
    if not sid:
        return []
    out = []
    for mult in (0.2, 1, 4, 16):
        # ~ mult * 100k chars of inline context to blow past model limits.
        blob = ("O'zbekiston Respublikasi qonunchiligi. " * 2600)[: int(mult * 100_000)]
        payload = base_payload(
            sid, "Yuqoridagi hujjat nima haqida?", file_context=blob
        )
        out.append(await stream_once(client, payload, f"big_context[x{mult}]"))
    return out


async def strat_many_fake_file_ids(client, args) -> list[StreamResult]:
    sid = await create_session(client, "fake-files")
    if not sid:
        return []
    out = []
    for n in (1, 5, 25, 100):
        fake = [f"nonexistent-file-{i:04d}" for i in range(n)]
        payload = base_payload(
            sid, "Bu fayllar haqida xulosa ber.", file_ids=fake
        )
        out.append(await stream_once(client, payload, f"fake_file_ids[n={n}]"))
    return out


async def strat_real_file_uploads(client, args) -> list[StreamResult]:
    sid = await create_session(client, "real-files")
    if not sid:
        return []
    contents = [
        (f"Hujjat #{i}\n" + "Ushbu shartnoma bandlari. " * 50).encode("utf-8")
        for i in range(args.files)
    ]
    file_ids = await asyncio.gather(
        *(upload_file(client, i, c) for i, c in enumerate(contents))
    )
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        print("  [real_file_uploads] no files uploaded (entitlement gate / OCR?) — skipping")
        return []
    print(f"  [real_file_uploads] attached {len(file_ids)} files")
    payload = base_payload(
        sid, "Yuklangan barcha hujjatlarni tahlil qil.", file_ids=file_ids
    )
    return [await stream_once(client, payload, f"real_files[n={len(file_ids)}]")]


async def strat_weird_inputs(client, args) -> list[StreamResult]:
    sid = await create_session(client, "weird")
    if not sid:
        return []
    cases = {
        "empty": " ",
        "single_char": "?",
        "unicode_zalgo": "z̸̢̛͔a̴l̷g̶o̷ " * 20,
        "control_chars": "test\x00\x01\x02\x07 query",
        "rtl_mix": "مرحبا salom 你好 " * 30,
        "giant_one_line": "so'z " * 8000,
        "only_emoji": "🧪⚖️📄" * 50,
        "json_injection": '{"type":"error","error":"x"}',
    }
    out = []
    for name, q in cases.items():
        out.append(await stream_once(client, base_payload(sid, q), f"weird[{name}]"))
    return out


STRATEGIES = {
    "baseline": strat_baseline,
    "sequential_many": strat_sequential_many,
    "concurrent_same_session": strat_concurrent_same_session,
    "concurrent_many_sessions": strat_concurrent_many_sessions,
    "burst": strat_burst,
    "big_file_context": strat_big_file_context,
    "many_fake_file_ids": strat_many_fake_file_ids,
    "real_file_uploads": strat_real_file_uploads,
    "weird_inputs": strat_weird_inputs,
}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy",
        choices=[*STRATEGIES.keys(), "all"],
        default="all",
        help="Which strategy to run (default: all)",
    )
    parser.add_argument("--count", type=int, default=15, help="Messages per sequential/burst strategy")
    parser.add_argument("--concurrency", type=int, default=10, help="Parallel streams for concurrent strategies")
    parser.add_argument("--files", type=int, default=8, help="Files to upload for real_file_uploads")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-request timeout (s)")
    args = parser.parse_args()

    print(f"Target : {BASE_URL}{STREAM_PATH}")
    print(f"Auth   : {API_KEY_NAME}: ...{API_KEY[-6:]}")
    print(f"User   : {USER_ID}\n")

    chosen = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    all_results: list[StreamResult] = []

    timeout = httpx.Timeout(args.timeout, connect=10.0)
    limits = httpx.Limits(max_connections=max(20, args.concurrency * 2))
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout, limits=limits) as client:
        # Quick reachability check
        try:
            health = await client.get("/health")
            print(f"[health] {health.status_code} {health.text[:80]}\n")
        except httpx.HTTPError as exc:
            print(f"[health] unreachable: {exc}\n  Is the server running on {BASE_URL}?")
            return 2

        # Sessions require an existing user; provision the repro user up front.
        if not await ensure_user(client):
            print("\nAborting: test user is not available, so every session would 404.")
            return 2
        print()

        for name in chosen:
            print(f"=== strategy: {name} ===")
            try:
                results = await STRATEGIES[name](client, args)
            except Exception as exc:  # noqa: BLE001 - harness, never crash the run
                print(f"  strategy crashed: {type(exc).__name__}: {exc}")
                results = []
            for r in results:
                marker = "🎯" if r.is_keyerror_signature else ("⚠️ " if r.got_error_event else "  ")
                print(f"  {marker} {r.label:<28} {r.summary()}")
            all_results.extend(results)
            print()

    # ----------------------------------------------------------------- #
    # Final report
    # ----------------------------------------------------------------- #
    total = len(all_results)
    error_events = [r for r in all_results if r.got_error_event]
    signatures = [r for r in all_results if r.is_keyerror_signature]
    transport = [r for r in all_results if r.transport_error]

    print("=" * 70)
    print("SUMMARY")
    print(f"  total streams attempted : {total}")
    print(f"  transport / non-200     : {len(transport)}")
    print(f"  'type=error' events     : {len(error_events)}")
    print(f"  KeyError('error') hits  : {len(signatures)}")
    print("=" * 70)

    if signatures:
        print("\n🎯 REPRODUCED the exact production signature in:")
        for r in signatures:
            print(f"   - {r.label}: {r.error_event!r}")
    elif error_events:
        print("\n⚠️  Got error events (not the exact 'error' signature) in:")
        for r in error_events:
            print(f"   - {r.label}: {r.error_event!r}")
        print("\n   These are real backend stream failures worth investigating —")
        print("   check the server logs for the traceback behind each one.")
    else:
        print("\nNo stream error events triggered this run.")
        print("Try: higher --concurrency, more --count, or --strategy concurrent_same_session")

    print(
        "\nTip: the backend currently swallows the traceback in "
        "app/utils/streaming.py. Add `logger.error(..., exc_info=True)` to the "
        "except block to see exactly where KeyError('error') is raised."
    )
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
