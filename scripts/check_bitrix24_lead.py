#!/usr/bin/env python3
"""
Create a test Bitrix24 lead using the same payload builder as the app.

Usage:
    .venv/bin/python scripts/check_bitrix24_lead.py
"""

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import settings
from app.services.bitrix24_service import Bitrix24Service


SEND_TO_BITRIX = True
FETCH_CREATED_LEAD = True
WEBHOOK_URL_OVERRIDE = None

FIRST_NAMES = ["Wakil", "Test"]
LAST_NAMES = ["Lead", "User"]


def random_uz_phone() -> str:
    operator_code = random.choice(["90", "99"])
    subscriber_number = random.randint(1000000, 9999999)
    return f"+998{operator_code}{subscriber_number}"


def build_test_user() -> dict:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    random_id = random.randint(1000, 9999)
    user_id = f"bitrix-check-{now}-{random_id}"
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)

    return {
        "_id": user_id,
        "user_id": user_id,
        "username": f"bitrix-check-{random_id}",
        "first_name": first_name,
        "last_name": last_name,
        "phone_number": random_uz_phone(),
        "web_client": "wakilai",
    }


async def post_bitrix_method(
    service: Bitrix24Service,
    method: str,
    payload: dict,
) -> dict:
    async with httpx.AsyncClient(timeout=settings.BITRIX24_TIMEOUT_SECONDS) as client:
        response = await client.post(service._method_url(method), json=payload)

    result = {
        "status_code": response.status_code,
        "ok": 200 <= response.status_code < 300,
    }

    try:
        result["body"] = response.json()
    except ValueError:
        result["body"] = response.text

    return result


def print_json(title: str, payload: dict) -> None:
    print(f"\n{title}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def main_async() -> int:
    service = Bitrix24Service(webhook_url=WEBHOOK_URL_OVERRIDE)

    user = build_test_user()
    fields = service.build_lead_fields(user)
    payload = {"fields": fields}
    lead_add_url = (
        service._method_url("crm.lead.add")
        if service.is_configured
        else "BITRIX24_WEBHOOK_URL is not configured"
    )

    print(f"Bitrix method URL: {lead_add_url}")
    print_json("Lead payload:", payload)

    if not SEND_TO_BITRIX:
        print("\nSEND_TO_BITRIX is False. No request sent.")
        return 0

    if not service.is_configured:
        print("\nBITRIX24_WEBHOOK_URL is not configured.")
        print("Add BITRIX24_WEBHOOK_URL to .env.")
        return 2

    result = await post_bitrix_method(service, "crm.lead.add", payload)
    print_json("Bitrix crm.lead.add response:", result)

    body = result.get("body")
    if not result["ok"] or not isinstance(body, dict) or body.get("error"):
        return 1

    lead_id = body.get("result")
    if not lead_id:
        print("\nBitrix response was OK, but no lead ID was returned.")
        return 1

    print(f"\nLead created successfully. Bitrix lead ID: {lead_id}")

    if FETCH_CREATED_LEAD:
        fetch_result = await post_bitrix_method(
            service,
            "crm.lead.get",
            {"id": lead_id},
        )
        print_json("Bitrix crm.lead.get response:", fetch_result)
        fetch_body = fetch_result.get("body")
        if (
            not fetch_result["ok"]
            or not isinstance(fetch_body, dict)
            or fetch_body.get("error")
        ):
            return 1

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
