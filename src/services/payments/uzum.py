import time

from core.config import settings
from core.logger import logger
from models.payment import (
    SubscriptionEligibilityError,
    UzumError,
    UzumResponseStatus,
    UzumServiceError,
    UzumTransactionState,
)
from services.payments.base import BasePaymentService

# Acceptable planId formats: "<tier>_<period>" — e.g. "standard_monthly", "basic_daily".
_VALID_PERIODS = {"daily", "monthly", "yearly"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pick_param(params: dict, *keys: str) -> str | None:
    """Try multiple keys (camelCase + snake_case) and return the first non-empty string value."""
    if not isinstance(params, dict):
        return None
    for key in keys:
        value = params.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


class UzumService(BasePaymentService):
    """Uzum Bank Merchant API integration.

    Webhook-only protocol: Uzum POSTs to /check, /create, /confirm, /reverse, /status.
    We never call Uzum.

    Customer enters TWO fields in the Uzum Bank app — their wakil.ai user_id and a
    plan_id like "standard_monthly". Single serviceId, amount validated against our
    subscription catalog (the same one Payme / Click use).
    """

    provider = "uzum"

    def __init__(self):
        super().__init__()
        self.transactions_collection = settings.UZUM_TRANSACTIONS_COLLECTION
        self._tx_indexes_ready = False

    # ---------------------------------------------------------------- helpers

    async def _ensure_tx_indexes(self) -> None:
        if self._tx_indexes_ready:
            return
        collection = self.db_handler.db[self.transactions_collection]
        await collection.create_index([("transId", 1)], unique=True)
        await collection.create_index([("user_id", 1), ("status", 1)])
        self._tx_indexes_ready = True

    def _check_service_id(self, service_id) -> bool:
        configured = settings.UZUM_SERVICE_ID
        if configured is None:
            logger.error("[Uzum] UZUM_SERVICE_ID is not configured")
            return False
        try:
            return int(service_id) == int(configured)
        except (TypeError, ValueError):
            return False

    def _parse_plan_id(self, plan_id: str) -> tuple[str, str]:
        """Map planId like 'standard_monthly' → (tier, period)."""
        if not isinstance(plan_id, str) or "_" not in plan_id:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)
        # Split on the LAST underscore so a tier name with an underscore still works.
        tier, period = plan_id.rsplit("_", 1)
        tier = tier.strip().lower()
        period = period.strip().lower()
        if tier == "daily" and period == "daily":
            tier = "basic"
        if not tier or period not in _VALID_PERIODS:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)
        return tier, period

    def _extract_user_and_plan(self, params: dict) -> tuple[str, str]:
        user_id = _pick_param(params, "userId", "user_id", "account")
        plan_id = _pick_param(params, "planId", "plan_id", "tariff")
        if not user_id or not plan_id:
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)
        return user_id, plan_id

    async def _resolve_quote(self, plan_id: str) -> dict:
        tier, period = self._parse_plan_id(plan_id)
        try:
            return self._get_subscription_quote(tier, period)
        except ValueError as exc:
            logger.warning(f"[Uzum] Unknown plan {plan_id!r}: {exc}")
            raise UzumServiceError(
                UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND
            ) from exc

    def _data_block(self, user_id: str, plan_id: str) -> dict:
        # Per Uzum spec, each value must be wrapped in `{"value": "<string>"}`.
        return {
            "account": {"value": str(user_id)},
            "tariff": {"value": str(plan_id)},
        }

    @staticmethod
    def _err(error_code: str, /, **extra) -> dict:
        body = {"status": UzumResponseStatus.FAILED, "errorCode": error_code}
        body.update(extra)
        return body

    # ----------------------------------------------------------------- /check

    async def check(self, payload: dict) -> dict:
        service_id = payload.get("serviceId")
        params = payload.get("params") or {}
        now = _now_ms()

        if not self._check_service_id(service_id):
            raise UzumServiceError(UzumError.INVALID_SERVICE_ID)

        user_id, plan_id = self._extract_user_and_plan(params)
        quote = await self._resolve_quote(plan_id)

        user = await self.get_user_by_id(user_id)
        if not user:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        # Block buying a plan the user already has active (matches Payme / Click semantics).
        try:
            await self.validate_subscription_eligibility(
                user_id=user_id, quote=quote, now_ms=now
            )
        except SubscriptionEligibilityError as exc:
            logger.info(
                f"[Uzum] /check rejected for {user_id}/{plan_id}: {exc.code} — {exc.message}"
            )
            raise UzumServiceError(UzumError.PAYMENT_ALREADY_MADE) from exc

        # Uzum's catalog has no amount field — they display whatever we return here
        # to the customer for confirmation. Per Uzum spec, the amount in /check
        # response is in SUMS (string), unlike everywhere else in the protocol
        # where amount is in tiyin.
        data = self._data_block(user_id, plan_id)
        data["amount"] = {"value": str(int(quote["amount_sum"]))}

        return {
            "serviceId": int(service_id),
            "timestamp": now,
            "status": UzumResponseStatus.OK,
            "data": data,
        }

    # ---------------------------------------------------------------- /create

    async def create(self, payload: dict) -> dict:
        await self.ensure_invoice_indexes()
        await self._ensure_tx_indexes()

        service_id = payload.get("serviceId")
        trans_id = payload.get("transId")
        amount_tiyin = payload.get("amount")
        params = payload.get("params") or {}
        now = _now_ms()

        if not self._check_service_id(service_id):
            raise UzumServiceError(UzumError.INVALID_SERVICE_ID)

        if not trans_id or not isinstance(trans_id, str):
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)

        if not isinstance(amount_tiyin, int) or amount_tiyin <= 0:
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)

        user_id, plan_id = self._extract_user_and_plan(params)
        quote = await self._resolve_quote(plan_id)

        # Uzum sends amount in tiyin (1 sum = 100 tiyin); our catalog stores sum.
        if amount_tiyin % 100 != 0 or (amount_tiyin // 100) != int(quote["amount_sum"]):
            logger.warning(
                f"[Uzum] /create amount mismatch: got {amount_tiyin} tiyin for plan {plan_id} (expected {quote['amount_sum'] * 100})"
            )
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        user = await self.get_user_by_id(user_id)
        if not user:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        existing_tx = await self.db_handler.find_one(
            self.transactions_collection, {"transId": trans_id}
        )
        if existing_tx:
            # Idempotent replay: same trans_id, same user/plan/amount, still pending.
            if (
                existing_tx.get("status") == UzumTransactionState.CREATED
                and existing_tx.get("user_id") == user_id
                and existing_tx.get("plan_id") == plan_id
                and int(existing_tx.get("amount_tiyin") or 0) == int(amount_tiyin)
            ):
                return {
                    "serviceId": int(service_id),
                    "transId": trans_id,
                    "status": UzumResponseStatus.CREATED,
                    "transTime": int(
                        existing_tx.get("trans_time") or existing_tx.get("created_at") or now
                    ),
                    "amount": int(amount_tiyin),
                    "data": self._data_block(user_id, plan_id),
                }
            # transId reused with different details, or transaction already settled.
            raise UzumServiceError(UzumError.PAYMENT_ALREADY_MADE)

        # Eligibility check again — user may have completed a payment via another provider
        # between /check and /create.
        try:
            await self.validate_subscription_eligibility(
                user_id=user_id, quote=quote, now_ms=now
            )
        except SubscriptionEligibilityError as exc:
            logger.info(
                f"[Uzum] /create rejected for {user_id}/{plan_id}: {exc.code}"
            )
            raise UzumServiceError(UzumError.PAYMENT_ALREADY_MADE) from exc

        # Create the invoice row so _finalize_subscription_invoice picks it up on /confirm.
        await self.db_handler.insert_one(
            self.invoices_collection,
            self._build_invoice_document(
                order_id=trans_id,
                user_id=user_id,
                amount_sum=int(amount_tiyin // 100),
                callback_url="",  # Uzum has no callback URL concept.
                purpose=self._resolve_purpose(quote),
                quote=quote,
                now_ms=now,
            )
            | {"amount_tiyin": int(amount_tiyin), "uzum_trans_id": trans_id},
        )

        await self.db_handler.insert_one(
            self.transactions_collection,
            {
                "transId": trans_id,
                "service_id": int(service_id),
                "user_id": user_id,
                "plan_id": plan_id,
                "amount_tiyin": int(amount_tiyin),
                "amount_sum": int(amount_tiyin // 100),
                "status": UzumTransactionState.CREATED,
                "provider": self.provider,
                "trans_time": now,
                "confirm_time": None,
                "reverse_time": None,
                "params": params,
                "created_at": now,
                "updated_at": now,
            },
        )

        return {
            "serviceId": int(service_id),
            "transId": trans_id,
            "status": UzumResponseStatus.CREATED,
            "transTime": now,
            "amount": int(amount_tiyin),
            "data": self._data_block(user_id, plan_id),
        }

    # --------------------------------------------------------------- /confirm

    async def confirm(self, payload: dict) -> dict:
        service_id = payload.get("serviceId")
        trans_id = payload.get("transId")
        now = _now_ms()

        if not self._check_service_id(service_id):
            raise UzumServiceError(UzumError.INVALID_SERVICE_ID)

        if not trans_id:
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)

        tx = await self.db_handler.find_one(
            self.transactions_collection, {"transId": trans_id}
        )
        if not tx:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        confirm_meta = {
            "paymentSource": payload.get("paymentSource"),
            "tariff": payload.get("tariff"),
            "processingReferenceNumber": payload.get("processingReferenceNumber"),
            "phone": payload.get("phone"),
            "cardType": payload.get("cardType"),
        }

        if tx.get("status") == UzumTransactionState.REVERSED:
            raise UzumServiceError(UzumError.PAYMENT_CANCELLED)

        if tx.get("status") == UzumTransactionState.CONFIRMED:
            # Idempotent: Uzum retries /confirm up to 10x on 5xx/timeout.
            # Re-apply subscription in case the previous attempt died before granting it.
            try:
                await self._finalize_subscription_invoice(
                    order_id=trans_id,
                    transaction_id=trans_id,
                    now_ms=now,
                )
            except Exception as exc:
                logger.exception(
                    f"[Uzum] Re-apply subscription failed on idempotent confirm: {exc}"
                )
            return {
                "serviceId": int(service_id),
                "transId": trans_id,
                "status": UzumResponseStatus.CONFIRMED,
                "confirmTime": int(tx.get("confirm_time") or now),
                "amount": int(tx.get("amount_tiyin") or 0),
                "data": self._data_block(
                    tx.get("user_id") or "", tx.get("plan_id") or ""
                ),
            }

        if tx.get("status") != UzumTransactionState.CREATED:
            raise UzumServiceError(UzumError.PAYMENT_ALREADY_MADE)

        await self.db_handler.update_one(
            self.transactions_collection,
            {"transId": trans_id},
            {
                "status": UzumTransactionState.CONFIRMED,
                "confirm_time": now,
                "confirm_meta": confirm_meta,
                "updated_at": now,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": trans_id}, {"invoice_id": trans_id}],
            },
            {"status": "paid", "updated_at": now},
        )

        try:
            await self._finalize_subscription_invoice(
                order_id=trans_id,
                transaction_id=trans_id,
                now_ms=now,
            )
        except Exception as exc:
            logger.exception(f"[Uzum] Failed to apply subscription: {exc}")

        return {
            "serviceId": int(service_id),
            "transId": trans_id,
            "status": UzumResponseStatus.CONFIRMED,
            "confirmTime": now,
            "amount": int(tx.get("amount_tiyin") or 0),
            "data": self._data_block(
                tx.get("user_id") or "", tx.get("plan_id") or ""
            ),
        }

    # --------------------------------------------------------------- /reverse

    async def reverse(self, payload: dict) -> dict:
        service_id = payload.get("serviceId")
        trans_id = payload.get("transId")
        now = _now_ms()

        if not self._check_service_id(service_id):
            raise UzumServiceError(UzumError.INVALID_SERVICE_ID)

        if not trans_id:
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)

        tx = await self.db_handler.find_one(
            self.transactions_collection, {"transId": trans_id}
        )
        if not tx:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        if tx.get("status") == UzumTransactionState.REVERSED:
            return {
                "serviceId": int(service_id),
                "transId": trans_id,
                "status": UzumResponseStatus.REVERSED,
                "reverseTime": int(tx.get("reverse_time") or now),
                "amount": int(tx.get("amount_tiyin") or 0),
                "data": self._data_block(
                    tx.get("user_id") or "", tx.get("plan_id") or ""
                ),
            }

        was_confirmed = tx.get("status") == UzumTransactionState.CONFIRMED

        await self.db_handler.update_one(
            self.transactions_collection,
            {"transId": trans_id},
            {
                "status": UzumTransactionState.REVERSED,
                "reverse_time": now,
                "updated_at": now,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": trans_id}, {"invoice_id": trans_id}],
            },
            {"status": "canceled", "updated_at": now},
        )

        if was_confirmed:
            # Subscription was already granted; flag for manual review. We don't auto-revoke
            # because it could mid-flight invalidate paid usage. Matches existing Payme/Click behavior.
            logger.warning(
                f"[Uzum] Reverse received for already-confirmed transId={trans_id} "
                f"user={tx.get('user_id')} plan={tx.get('plan_id')} — subscription NOT auto-revoked"
            )

        return {
            "serviceId": int(service_id),
            "transId": trans_id,
            "status": UzumResponseStatus.REVERSED,
            "reverseTime": now,
            "amount": int(tx.get("amount_tiyin") or 0),
            "data": self._data_block(
                tx.get("user_id") or "", tx.get("plan_id") or ""
            ),
        }

    # ---------------------------------------------------------------- /status

    async def status(self, payload: dict) -> dict:
        service_id = payload.get("serviceId")
        trans_id = payload.get("transId")
        now = _now_ms()

        if not self._check_service_id(service_id):
            raise UzumServiceError(UzumError.INVALID_SERVICE_ID)

        if not trans_id:
            raise UzumServiceError(UzumError.MISSING_REQUIRED_PARAMETERS)

        tx = await self.db_handler.find_one(
            self.transactions_collection, {"transId": trans_id}
        )
        if not tx:
            raise UzumServiceError(UzumError.ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND)

        return {
            "serviceId": int(service_id),
            "transId": trans_id,
            "status": tx.get("status") or UzumTransactionState.CREATED,
            "transTime": int(tx.get("trans_time") or tx.get("created_at") or now),
            "confirmTime": tx.get("confirm_time"),
            "reverseTime": tx.get("reverse_time"),
            "amount": int(tx.get("amount_tiyin") or 0),
            "data": self._data_block(
                tx.get("user_id") or "", tx.get("plan_id") or ""
            ),
        }
