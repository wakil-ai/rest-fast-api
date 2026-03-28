#!/usr/bin/env python3

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_response_body(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def make_request(method: str, url: str, headers: dict[str, str], payload=None) -> dict:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = Request(url=url, data=body, headers=headers, method=method)

    try:
        with urlopen(request) as response:
            raw = response.read().decode("utf-8")
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "response": parse_response_body(raw),
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        return {
            "ok": False,
            "status_code": exc.code,
            "response": parse_response_body(raw),
        }
    except URLError as exc:
        return {
            "ok": False,
            "status_code": None,
            "response": {"error": str(exc)},
        }


def append_step(report: dict, *, api: str, method: str, payload, result: dict) -> None:
    report["steps"].append(
        {
            "api": api,
            "method": method,
            "payload": payload,
            "response": result.get("response"),
            "status_code": result.get("status_code"),
            "ok": result.get("ok", False),
        }
    )


def require_ok(step_name: str, result: dict) -> None:
    if result.get("ok"):
        return
    raise RuntimeError(
        f"{step_name} failed with status {result.get('status_code')}: {result.get('response')}"
    )


def extract_message_id(response) -> str | None:
    if isinstance(response, dict):
        message_id = response.get("message_id")
        return message_id if isinstance(message_id, str) else None

    if isinstance(response, str):
        for line in response.splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line.removeprefix("data: "))
            except json.JSONDecodeError:
                continue
            if event.get("type") == "metadata":
                message_id = event.get("message_id")
                return message_id if isinstance(message_id, str) else None

    return None


def wait_for_messages(
    base_url: str,
    session_id: str,
    dt_key_header: str,
    dt_key_value: str,
    attempts: int = 10,
    delay_seconds: float = 0.5,
) -> dict:
    headers = {dt_key_header: dt_key_value}
    last_result = {"ok": False, "status_code": None, "response": []}

    for _ in range(attempts):
        last_result = make_request(
            "GET",
            f"{base_url}/history/messages/{session_id}",
            headers,
        )
        if last_result.get("ok") and isinstance(last_result.get("response"), list):
            if last_result["response"]:
                return last_result
        time.sleep(delay_seconds)

    return last_result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check DT integration APIs and save api/payload/response report.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080/api/v2",
        help="API base URL, default: http://localhost:8080/api/v2",
    )
    parser.add_argument(
        "--output",
        default="dt_api_check_report.json",
        help="Path to save the JSON report",
    )
    parser.add_argument(
        "--dt-key-header",
        default="x-dt-team-api-key",
        help="DT API key header name",
    )
    parser.add_argument(
        "--dt-key-value",
        required=True,
        help="DT API key value",
    )
    parser.add_argument(
        "--chat-key-header",
        default="admin",
        help="Chat API key header name",
    )
    parser.add_argument(
        "--chat-key-value",
        required=True,
        help="Chat API key value",
    )
    parser.add_argument(
        "--query",
        default="How to make marriage?",
        help="Query to send to /chat/ask",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    external_user_id = f"dt-user-{run_id}"

    dt_headers = {
        "Content-Type": "application/json",
        args.dt_key_header: args.dt_key_value,
    }
    chat_headers = {
        "Content-Type": "application/json",
        args.chat_key_header: args.chat_key_value,
    }

    report = {
        "generated_at": now_iso(),
        "base_url": base_url,
        "steps": [],
    }

    try:
        create_user_payload = {
            "user_id": external_user_id,
            "phone_number": "+998901234567",
            "first_name": "DT",
            "last_name": "Tester",
            "username": f"dt.tester.{run_id}",
        }
        create_user_result = make_request(
            "POST",
            f"{base_url}/auth/dt",
            dt_headers,
            create_user_payload,
        )
        append_step(
            report,
            api="/auth/dt",
            method="POST",
            payload=create_user_payload,
            result=create_user_result,
        )
        require_ok("Create user", create_user_result)

        internal_user_id = create_user_result["response"]["user_id"]

        get_user_result = make_request(
            "GET",
            f"{base_url}/history/users/{internal_user_id}",
            {args.dt_key_header: args.dt_key_value},
        )
        append_step(
            report,
            api=f"/history/users/{internal_user_id}",
            method="GET",
            payload=None,
            result=get_user_result,
        )
        require_ok("Get user", get_user_result)

        list_sessions_result = make_request(
            "GET",
            f"{base_url}/history/sessions/{internal_user_id}",
            {args.dt_key_header: args.dt_key_value},
        )
        append_step(
            report,
            api=f"/history/sessions/{internal_user_id}",
            method="GET",
            payload=None,
            result=list_sessions_result,
        )
        require_ok("List sessions", list_sessions_result)

        create_session_payload = {
            "user_id": internal_user_id,
            "title": "New Chat",
            "tags": [],
        }
        create_session_result = make_request(
            "POST",
            f"{base_url}/history/sessions",
            dt_headers,
            create_session_payload,
        )
        append_step(
            report,
            api="/history/sessions",
            method="POST",
            payload=create_session_payload,
            result=create_session_result,
        )
        require_ok("Create session", create_session_result)

        session_id = create_session_result["response"]["_id"]

        ask_chat_payload = {
            "user_id": internal_user_id,
            "session_id": session_id,
            "query": args.query,
            "stream": False,
            "assistant": "main",
        }
        ask_chat_result = make_request(
            "POST",
            f"{base_url}/chat/ask",
            chat_headers,
            ask_chat_payload,
        )
        append_step(
            report,
            api="/chat/ask",
            method="POST",
            payload=ask_chat_payload,
            result=ask_chat_result,
        )
        require_ok("Ask chat", ask_chat_result)

        message_id = extract_message_id(ask_chat_result.get("response"))

        list_messages_result = wait_for_messages(
            base_url=base_url,
            session_id=session_id,
            dt_key_header=args.dt_key_header,
            dt_key_value=args.dt_key_value,
        )
        append_step(
            report,
            api=f"/history/messages/{session_id}",
            method="GET",
            payload=None,
            result=list_messages_result,
        )
        require_ok("List messages", list_messages_result)

        report["summary"] = {
            "external_user_id": external_user_id,
            "internal_user_id": internal_user_id,
            "session_id": session_id,
            "message_id": message_id,
        }

    except Exception as exc:
        report["error"] = str(exc)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")

    print(f"Saved report to {output_path}")
    if report.get("error"):
        print(report["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
