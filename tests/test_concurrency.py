#!/usr/bin/env python3
"""
Concurrency test — sends 5 concurrent requests across all 4 assistants (20 total).
Measures per-request latency and total wall-clock time.
Saves detailed results to scripts/concurrency_results.csv.

Usage:
    python scripts/test_concurrency.py
    python scripts/test_concurrency.py --base-url http://your-server:8080
"""

import argparse
import asyncio
import csv
import os
import time
from datetime import datetime

import httpx

# ── Config ──────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "http://localhost:8080"
API_PREFIX = "/api/v2"
API_KEY_NAME = "X-API-HBAI-KEY"
API_KEY_VALUE = "h621xfDtSVkJvaw1D8WhYJGl2TAVnUKW"
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "concurrency_results.csv")

ASSISTANTS = ["main", "soliq", "mamuriy_sud", "shartnoma"]
REQUESTS_PER_ASSISTANT = 5

QUERIES = {
    "main": "Mehnat kodeksining 100-moddasi nimani tartibga soladi?",
    "soliq": "QQS stavkasi qancha va qachon to'lanadi?",
    "mamuriy_sud": "Ma'muriy sud ishlarini ko'rish tartibi qanday?",
    "shartnoma": "Xizmat ko'rsatish shartnomasining asosiy shartlari nimalardan iborat?",
}


def build_payload(assistant: str, index: int) -> dict:
    return {
        "user_id": f"concurrency_test_user_{index}",
        "query": QUERIES[assistant],
        "stream": False,
        "assistant": assistant,
    }


async def send_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    assistant: str,
    index: int,
) -> dict:
    payload = build_payload(assistant, index)
    label = f"[{assistant}#{index}]"

    start = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=120)
        elapsed = time.perf_counter() - start
        status = resp.status_code

        # Try to extract server-side processing time if header exists
        server_time = resp.headers.get("x-process-time", "")

        print(f"  ✅ {label:25s}  {status}  {elapsed:.2f}s")
        return {
            "assistant": assistant,
            "index": index,
            "status": status,
            "latency_s": round(elapsed, 4),
            "server_time_s": server_time,
            "ok": True,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"  ❌ {label:25s}  ERR   {elapsed:.2f}s  {e}")
        return {
            "assistant": assistant,
            "index": index,
            "status": 0,
            "latency_s": round(elapsed, 4),
            "server_time_s": "",
            "ok": False,
            "error": str(e),
        }


def save_results(results: list[dict], wall_time: float, base_url: str):
    """Append results to CSV file with a run timestamp."""
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.exists(RESULTS_FILE)

    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "run_timestamp",
                "base_url",
                "assistant",
                "request_index",
                "status",
                "latency_s",
                "server_time_s",
                "ok",
                "wall_time_s",
                "total_requests",
                "concurrency_gain",
            ])

        total = len(results)
        sum_latency = sum(r["latency_s"] for r in results)
        gain = round(sum_latency / wall_time, 2) if wall_time > 0 else 0

        for r in results:
            writer.writerow([
                run_ts,
                base_url,
                r["assistant"],
                r["index"],
                r["status"],
                r["latency_s"],
                r.get("server_time_s", ""),
                r["ok"],
                round(wall_time, 4),
                total,
                gain,
            ])

    print(f"  📄 Results saved to {RESULTS_FILE}")


async def main(base_url: str):
    url = f"{base_url}{API_PREFIX}/chat/ask"
    headers = {API_KEY_NAME: API_KEY_VALUE, "Content-Type": "application/json"}

    # Build task list: 5 requests × 4 assistants = 20 concurrent requests
    tasks = []
    for assistant in ASSISTANTS:
        for i in range(1, REQUESTS_PER_ASSISTANT + 1):
            tasks.append((assistant, i))

    total = len(tasks)
    print(f"\n🚀 Sending {total} concurrent requests ({REQUESTS_PER_ASSISTANT} × {len(ASSISTANTS)} assistants)")
    print(f"   Target: {url}")
    print(f"   Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    async with httpx.AsyncClient() as client:
        wall_start = time.perf_counter()

        results = await asyncio.gather(
            *[send_request(client, url, headers, a, i) for a, i in tasks]
        )

        wall_time = time.perf_counter() - wall_start

    # ── Summary ─────────────────────────────────────────────────────
    sum_latency = sum(r["latency_s"] for r in results)
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = total - ok_count

    print(f"\n{'=' * 60}")
    print(f"  Total requests:    {total}")
    print(f"  Successful:        {ok_count}")
    print(f"  Failed:            {fail_count}")
    print(f"  Wall-clock time:   {wall_time:.2f}s")
    print(f"  Sum of latencies:  {sum_latency:.2f}s")
    print(f"  Concurrency gain:  {sum_latency / wall_time:.1f}x")

    # Per-assistant breakdown
    print(f"\n  Per-assistant breakdown:")
    print(f"    {'assistant':15s}  {'avg':>6s}  {'min':>6s}  {'max':>6s}  {'p50':>6s}  {'ok':>3s}")
    print(f"    {'-'*50}")
    for assistant in ASSISTANTS:
        times = sorted([r["latency_s"] for r in results if r["assistant"] == assistant and r["ok"]])
        if times:
            avg = sum(times) / len(times)
            p50 = times[len(times) // 2]
            print(f"    {assistant:15s}  {avg:5.2f}s  {min(times):5.2f}s  {max(times):5.2f}s  {p50:5.2f}s  {len(times):>3d}")
    print(f"{'=' * 60}")

    # Save to CSV
    save_results(results, wall_time, base_url)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concurrency test for WakilAI API")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    args = parser.parse_args()

    asyncio.run(main(args.base_url))
