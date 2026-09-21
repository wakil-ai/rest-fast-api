import base64
import time

import httpx

from core.config import settings
from core.logger import logger
from models.payment import AtmosServiceError

# https://docs.atmos.uz/en/ — single-page Slate doc, no per-section URLs. Every
# request/response shape below was checked against the live RU page's section
# headings (search for the Russian heading quoted in each method's docstring;
# the English mirror at docs.atmos.uz/en/ has the same structure translated).
# Do not add or rename a field here without checking that page directly — see
# CLAUDE.md's vendor-doc-citation rule and this file's git history for why.


# "OK" for merchant/pay/* and partner/*, 0 for checkout/* and mps/*.
_SUCCESS_CODES = frozenset({"OK", "0"})


class AtmosClient:
    """Thin async HTTP client for apigw.atmos.uz.

    Per-call `async with httpx.AsyncClient(...)`, matching the existing convention
    for outbound provider calls (llm_service_client.py, bitrix24_service.py) —
    ATMOS is the first *payment* provider we call out to rather than one that
    calls us, so there is no prior client to copy inside services/payments/.

    ATMOS signals a business-level failure inside a 200 response body (a
    `result.code`/`status.code` other than "OK"), not via HTTP status — every
    call here checks that envelope explicitly rather than trusting
    `raise_for_status()` alone. Example: `merchant/pay/get` on a closed
    transaction returns HTTP 200 with `{"result": {"code": "STPIMS-ERR-092", ...}}`.
    """

    def __init__(self):
        self._base = settings.ATMOS_APIGW_BASE.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # MARK: auth — "Авторизация в API" / "Получение токена"

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        """Bearer token, cached and refreshed off the response's own `expires_in`.

        Docs prose says the token is valid for 3600s but the sample response
        returns `"expires_in": 2525` — never hardcode the TTL.

        Auth is `Authorization: Basic base64(consumer_key:consumer_secret)` with
        `grant_type=client_credentials` as a query param + form body — not a JSON
        body with client_id/client_secret (that would be a plausible-looking
        guess, not what the docs show).
        """
        if not force_refresh and self._token and time.monotonic() < self._token_expires_at:
            return self._token

        if not settings.ATMOS_CONSUMER_KEY or not settings.ATMOS_CONSUMER_SECRET:
            raise AtmosServiceError("ATMOS_CONSUMER_KEY/SECRET are not configured")

        basic = base64.b64encode(
            f"{settings.ATMOS_CONSUMER_KEY}:{settings.ATMOS_CONSUMER_SECRET}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=settings.ATMOS_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self._base}/token",
                params={"grant_type": "client_credentials"},
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}"},
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AtmosServiceError(
                f"ATMOS token request failed: {response.status_code} {response.text[:500]}"
            ) from exc

        data = response.json()
        token = data.get("access_token")
        if not token:
            raise AtmosServiceError("ATMOS token response missing access_token")

        expires_in = int(data.get("expires_in") or 0) or 3600
        # Refresh a little early so an in-flight multi-call sequence (create ->
        # pre-apply -> apply) never straddles expiry.
        self._token = token
        self._token_expires_at = time.monotonic() + max(30, expires_in - 30)
        return token

    @staticmethod
    def _check_envelope(data: dict, *, path: str) -> dict:
        """Raise if the embedded result/status envelope is not OK; ATMOS returns
        these as HTTP 200 regardless of the underlying business outcome."""
        envelope = data.get("result") or data.get("status") or {}
        code = envelope.get("code")
        # Two envelope dialects: merchant/pay/* and partner/* use
        # {"code": "OK", "description": ...}; checkout/card-bind/* and mps/*
        # use {"code": 0, "message": "Success", "trace_id": ...} (verified
        # against ATMOS DEV on 2026-09-17: card-bind/create returned code 0).
        if code is not None and str(code) not in _SUCCESS_CODES:
            locale = envelope.get("locale") or {}
            detail = (
                envelope.get("description")
                or envelope.get("message")
                or locale.get("en")
                or locale.get("ru")
                or ""
            )
            raise AtmosServiceError(
                f"ATMOS {path} returned {code}: {detail}",
                atmos_code=str(code),
            )
        return data

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, retry_on_401: bool = True
    ) -> dict:
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=settings.ATMOS_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method,
                f"{self._base}{path}",
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code == 401 and retry_on_401:
            await self._get_token(force_refresh=True)
            return await self._request(method, path, json=json, retry_on_401=False)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                f"[Atmos] {method} {path} failed: {response.status_code} {response.text[:500]}"
            )
            raise AtmosServiceError(
                f"ATMOS {path} failed: {response.status_code}"
            ) from exc

        try:
            data = response.json()
        except Exception as exc:
            raise AtmosServiceError(f"ATMOS {path} returned a non-JSON response") from exc

        return self._check_envelope(data, path=path)

    # MARK: card binding — "Привязка карты владельцем" ("Card binding by the owner")
    # docs.atmos.uz/en/ under "Visa/Mastercard acquiring" -> "Card binding (IPS)".
    # Request: {request_id, store_id, account, success_url}; both request_id and
    # account are documented free-form here (example "DFSDF") — the numeric
    # constraint is specific to merchant/pay/* below, not this endpoint.

    async def create_card_bind(
        self, *, request_id: str, account: str, success_url: str
    ) -> dict:
        """Returns {"store_id", "payment_id", "token", "url", "status": {"code": 0, "message": "Success", ...}}."""
        return await self._request(
            "POST",
            "/checkout/card-bind/create",
            json={
                "request_id": request_id,
                "store_id": settings.ATMOS_STORE_ID,
                "account": account,
                "success_url": success_url,
            },
        )

    # MARK: charge sequence — "Создание транзакции" / "Пред-подтверждение
    # транзакции" / "Подтверждение транзакции" / "Получение информации о
    # транзакции" (merchant/pay/create, pre-apply, apply, get).
    # Amounts throughout are in TIYIN (1 sum = 100 tiyin). `account` is a numeric
    # STRING (docs: "Поле account принимает в себя цифровое значение") — pass a
    # digit-only string, not a JSON integer (the docs' own example sends
    # `"account": "12345"`, quoted).

    async def create_transaction(self, *, account: str, amount_tiyin: int) -> dict:
        """Returns {"result", "transaction_id", "store_transaction": {...}}."""
        return await self._request(
            "POST",
            "/merchant/pay/create",
            json={
                "amount": amount_tiyin,
                "account": account,
                "store_id": settings.ATMOS_STORE_ID,
            },
        )

    async def pre_apply(self, *, transaction_id: int, card_token: int) -> dict:
        """Token-charge branch: `card_token`, no `card_number`/`expiry` — the
        bound-mandate path, not the raw-PAN/SMS branch (out of scope; see spec
        Non-goals). Docs: "Данный запрос не может иметь одновременно поля
        card_token и card_number с expiry" — the two branches are mutually
        exclusive, so this method only ever sends card_token.

        Returns {"transaction_id", "result"}.
        """
        return await self._request(
            "POST",
            "/merchant/pay/pre-apply",
            json={
                "transaction_id": transaction_id,
                "card_token": card_token,
                "store_id": settings.ATMOS_STORE_ID,
            },
        )

    async def apply(self, *, transaction_id: int) -> dict:
        """ATMOS documents the fixed integer 111111 as the `otp` value for a
        token-based (pre-bound) charge — there is no real OTP prompt in this
        branch. Returns {"result", "store_transaction": {..., "confirmed": bool,
        "card_id": <token>, ...}, "ofd_url", "ofd_url_commission"}.
        """
        return await self._request(
            "POST",
            "/merchant/pay/apply",
            json={
                "transaction_id": transaction_id,
                "otp": 111111,
                "store_id": settings.ATMOS_STORE_ID,
            },
        )

    async def get_transaction(self, *, transaction_id: int) -> dict:
        """Returns {"result", "store_transaction": {...}}."""
        return await self._request(
            "POST",
            "/merchant/pay/get",
            json={"transaction_id": transaction_id, "store_id": settings.ATMOS_STORE_ID},
        )

    async def reverse(self, *, transaction_id: int) -> dict:
        """Out of scope for v1 (see spec Non-goals — no refund flow yet); kept
        for the reconciliation/"Later" path. Field shape follows the same
        {transaction_id, store_id} convention as get/pre-apply but has not been
        checked against the docs as closely as the v1 methods above — verify
        before relying on it.
        """
        return await self._request(
            "POST",
            "/merchant/pay/reverse",
            json={"transaction_id": transaction_id, "store_id": settings.ATMOS_STORE_ID},
        )
