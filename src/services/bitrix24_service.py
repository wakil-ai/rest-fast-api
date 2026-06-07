from urllib.parse import urljoin

import httpx

from app.core.config import settings
from app.core.logger import logger


class Bitrix24Service:
    """Create Bitrix24 CRM leads for newly added users."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = (webhook_url or settings.BITRIX24_WEBHOOK_URL or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    def _method_url(self, method: str) -> str:
        normalized_url = self.webhook_url.rstrip("/")
        base_url = normalized_url + "/"
        if normalized_url.endswith(method) or normalized_url.endswith(
            f"{method}.json"
        ):
            return self.webhook_url
        return urljoin(base_url, f"{method}.json")

    @staticmethod
    def _display_name(user: dict) -> str:
        first_name = (user.get("first_name") or "").strip()
        last_name = (user.get("last_name") or "").strip()
        username = (user.get("username") or "").strip()
        full_name = " ".join(part for part in [first_name, last_name] if part)
        return full_name or username or str(
            user.get("user_id") or user.get("_id") or ""
        )

    def build_lead_fields(self, user: dict) -> dict:
        phone_number = (user.get("phone_number") or "").strip()
        fields = {
            "TITLE": settings.BITRIX24_LEAD_TITLE,
            "NAME": (user.get("first_name") or user.get("username") or "").strip()
            or self._display_name(user),
            "LAST_NAME": (user.get("last_name") or "").strip(),
            "SOURCE_DESCRIPTION": settings.BITRIX24_LEAD_SOURCE_DESCRIPTION,
            "COMMENTS": (
                "Created automatically from WakilAI user registration.\n"
                f"User ID: {user.get('user_id') or user.get('_id')}\n"
                f"Web client: {user.get('web_client') or '-'}"
            ),
        }

        if phone_number:
            fields["PHONE"] = [{"VALUE": phone_number, "VALUE_TYPE": "WORK"}]

        if settings.BITRIX24_LEAD_SOURCE_ID:
            fields["SOURCE_ID"] = settings.BITRIX24_LEAD_SOURCE_ID

        if settings.BITRIX24_LEAD_ASSIGNED_BY_ID is not None:
            fields["ASSIGNED_BY_ID"] = settings.BITRIX24_LEAD_ASSIGNED_BY_ID

        return {
            key: value for key, value in fields.items() if value not in (None, "")
        }

    async def create_lead_for_user(self, user: dict) -> int | str | None:
        if not self.is_configured:
            logger.info(
                "[Bitrix24] BITRIX24_WEBHOOK_URL is not configured; "
                "skipping lead creation"
            )
            return None

        phone_number = (user.get("phone_number") or "").strip()
        if not phone_number:
            logger.info(
                f"[Bitrix24] User {user.get('user_id') or user.get('_id')} "
                "has no phone number; skipping lead creation"
            )
            return None

        payload = {"fields": self.build_lead_fields(user)}

        try:
            async with httpx.AsyncClient(
                timeout=settings.BITRIX24_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    self._method_url("crm.lead.add"), json=payload
                )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error(
                f"[Bitrix24] Failed to create lead for user "
                f"{user.get('user_id') or user.get('_id')}: {exc}",
                exc_info=True,
            )
            return None

        if data.get("error"):
            user_id = user.get("user_id") or user.get("_id")
            logger.error(
                f"[Bitrix24] Lead creation failed for user {user_id}: "
                f"{data.get('error')} {data.get('error_description')}"
            )
            return None

        lead_id = data.get("result")
        user_id = user.get("user_id") or user.get("_id")
        logger.info(
            f"[Bitrix24] Created lead {lead_id} for user {user_id}"
        )
        return lead_id
