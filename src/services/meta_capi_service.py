"""Meta Conversions API: server-side Purchase events for app installs.

Reporting must never affect payments: every public entry point swallows errors.
"""

import asyncio
import time
import uuid
from typing import Any

import httpx

from core.config import settings
from core.logger import logger

# User-document fields written by the apps' sign-in sync; wiped on account deletion.
AD_ATTRIBUTION_FIELDS = (
    "platform",
    "madid",
    "anon_id",
    "att",
    "os_version",
    "app_version",
    "app_build",
)

_EXTINFO_LENGTH = 16
_EXTINFO_VERSION = {"ios": "i2", "android": "a2"}
_MAX_ID_LENGTH = 128
_MAX_VERSION_LENGTH = 32
_ZERO_MADID = "00000000-0000-0000-0000-000000000000"
# Meta dedupes on event_id, so a retry after an ambiguous failure can't double count.
_RETRY_DELAYS = (1.0, 4.0)

_pending: set[asyncio.Task] = set()
_warned_disabled = False


def _reporting_enabled() -> bool:
    """False (with one warning per process) when the token or dataset id isn't configured."""
    global _warned_disabled
    if settings.META_CAPI_TOKEN and settings.META_DATASET_ID:
        return True
    if not _warned_disabled:
        _warned_disabled = True
        logger.warning(
            "[MetaCAPI] Purchase reporting is disabled: META_CAPI_TOKEN or META_DATASET_ID is not set"
        )
    return False


def _clean_text(value: Any, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= max_length else None


def _clean_madid(value: Any) -> str | None:
    """A valid ad ID, "" for "no ID", or None for garbage.

    "" and the all-zero ID both mean ATT was denied / ad tracking is off, and must
    overwrite a previously stored ID.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == _ZERO_MADID:
        return ""
    if len(value) != 36:
        return None
    try:
        uuid.UUID(value)
    except ValueError:
        return None
    return value


def _parse_att(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value.strip() in ("0", "1"):
        return int(value)
    return None


def normalize_ad_attribution(
    *,
    platform: Any,
    madid: Any,
    anon_id: Any,
    att: Any,
    os_version: Any = None,
    app_version: Any = None,
    app_build: Any = None,
) -> dict[str, Any] | None:
    """Return clean fields to store on the user, or None when the client sent none.

    Invalid values are dropped rather than rejected so login never fails on them.
    """
    if all(v is None for v in (platform, madid, anon_id, att, os_version, app_version, app_build)):
        return None

    fields: dict[str, Any] = {}
    platform_value = platform.strip().lower() if isinstance(platform, str) else ""
    if platform_value in _EXTINFO_VERSION:
        fields["platform"] = platform_value
    if (clean_madid := _clean_madid(madid)) is not None:
        fields["madid"] = clean_madid
    if (clean_anon := _clean_text(anon_id, _MAX_ID_LENGTH)) is not None:
        fields["anon_id"] = clean_anon
    if (clean_att := _parse_att(att)) is not None:
        fields["att"] = clean_att
    for key, value in (
        ("os_version", os_version),
        ("app_version", app_version),
        ("app_build", app_build),
    ):
        if (clean := _clean_text(value, _MAX_VERSION_LENGTH)) is not None:
            fields[key] = clean
    return fields or None


def build_extinfo(
    platform: str, *, app_version: str = "", app_build: str = "", os_version: str = ""
) -> list[str]:
    """Exactly 16 items in Meta's fixed order; the rest stay empty strings.

    0 version (i2/a2, required), 1 bundle id, 2 short version, 3 long version,
    4 OS version (required).
    """
    extinfo = [""] * _EXTINFO_LENGTH
    extinfo[0] = _EXTINFO_VERSION[platform]
    extinfo[1] = settings.META_APP_BUNDLE_ID
    extinfo[2] = app_version
    extinfo[3] = app_build
    extinfo[4] = os_version
    return extinfo


def _purchase_value(amount: float | None, currency: str | None) -> tuple[float, str] | None:
    """(value, currency) to report, or None if it can't be priced. Sent in the charged currency."""
    if not amount or amount <= 0:
        return None
    code = str(currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        return None
    value = round(float(amount), 2)
    return (value, code) if value > 0 else None


def build_purchase_event(
    *,
    user: dict,
    event_id: str,
    amount: float | None,
    currency: str = "UZS",
    event_time: int,
) -> dict | None:
    """Build the CAPI event, or None if it can't be attributed / priced."""
    platform = user.get("platform")
    if platform not in _EXTINFO_VERSION:
        return None
    madid = user.get("madid") or ""
    anon_id = user.get("anon_id") or ""
    if not madid and not anon_id:
        return None
    # Meta requires the OS version in extinfo; without it the event isn't usable.
    os_version = user.get("os_version") or ""
    if not os_version:
        return None
    priced = _purchase_value(amount, currency)
    if priced is None:
        return None
    value, value_currency = priced

    user_data = {}
    if madid:
        user_data["madid"] = madid
    if anon_id:
        user_data["anon_id"] = anon_id

    # Android has no ATT: always enabled.
    att = 1 if platform == "android" else int(user.get("att") or 0)
    return {
        "event_name": "Purchase",
        "event_time": int(event_time),
        "event_id": str(event_id),
        "action_source": "app",
        "user_data": user_data,
        "app_data": {
            "advertiser_tracking_enabled": att,
            "extinfo": build_extinfo(
                platform,
                app_version=user.get("app_version") or "",
                app_build=user.get("app_build") or "",
                os_version=os_version,
            ),
        },
        "custom_data": {"value": value, "currency": value_currency},
    }


def _describe(response: httpx.Response) -> str:
    """Status plus Meta's own ids/error fields, never the raw body."""
    status = response.status_code
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {status}"
    if not isinstance(data, dict):
        return f"HTTP {status}"
    error = data.get("error")
    if isinstance(error, dict):
        return (
            f"HTTP {status} code={error.get('code')} type={error.get('type')} "
            f"message={str(error.get('message'))[:200]} fbtrace_id={error.get('fbtrace_id')}"
        )
    return f"HTTP {status} events_received={data.get('events_received')} fbtrace_id={data.get('fbtrace_id')}"


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def _post_with_retry(url: str, body: dict[str, Any], event_id: str) -> httpx.Response | None:
    """POST, retrying transport errors, 429 and 5xx. None if every attempt failed to connect."""
    response = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        if attempt:
            await asyncio.sleep(_RETRY_DELAYS[attempt - 1])
        try:
            async with httpx.AsyncClient(timeout=settings.META_CAPI_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=body)
        except httpx.TransportError as exc:
            # type only: httpx exception text can embed request details.
            logger.warning(
                f"[MetaCAPI] Purchase {event_id} attempt {attempt + 1} failed: {type(exc).__name__}"
            )
            response = None
            continue
        if not _is_retryable(response.status_code):
            break
    return response


async def send_purchase(
    *,
    user: dict | None,
    event_id: str,
    amount: float | None,
    currency: str = "UZS",
    event_time: int | None = None,
) -> None:
    """Send one Purchase event. Never raises."""
    try:
        if not _reporting_enabled():
            return
        if not user:
            return
        event = build_purchase_event(
            user=user,
            event_id=event_id,
            amount=amount,
            currency=currency,
            event_time=event_time or int(time.time()),
        )
        if event is None:
            logger.info(f"[MetaCAPI] Skipping Purchase {event_id}: no attribution or price")
            return

        body: dict[str, Any] = {
            "data": [event],
            # Token in the body (not the URL) so httpx URL logging can't leak it.
            "access_token": settings.META_CAPI_TOKEN,
        }
        if settings.META_CAPI_TEST_EVENT_CODE:
            body["test_event_code"] = settings.META_CAPI_TEST_EVENT_CODE

        url = (
            f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}"
            f"/{settings.META_DATASET_ID}/events"
        )
        response = await _post_with_retry(url, body, event_id)
        if response is None:
            logger.error(f"[MetaCAPI] Purchase {event_id} not sent: could not reach Meta")
        elif response.is_success:
            logger.info(f"[MetaCAPI] Purchase {event_id} sent: {_describe(response)}")
        else:
            logger.error(f"[MetaCAPI] Purchase {event_id} rejected: {_describe(response)}")
    except Exception as exc:
        logger.error(f"[MetaCAPI] Purchase {event_id} failed: {type(exc).__name__}")


def spawn(coro) -> None:
    """Run a reporting coroutine in the background; never raises into the caller."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as exc:
        coro.close()
        logger.error(f"[MetaCAPI] Could not schedule report: {type(exc).__name__}")
