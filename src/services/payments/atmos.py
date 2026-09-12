import secrets
import time

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from core.config import settings
from core.logger import logger
from models.payment import AtmosError, AtmosServiceError
from services.payments.atmos_client import AtmosClient
from services.payments.base import BasePaymentService
from utils.text_cleaning import _md5_hex

# How long before a subscription's end_ms a mandate becomes "due" for renewal.
# Not specified in the spec beyond "shortly before end_ms" — kept as a plain
# constant rather than a setting since nothing outside this module reads it and
# a config knob nobody has asked to tune yet is unused surface area.
_RENEWAL_LOOKAHEAD_MS = 24 * 60 * 60 * 1000


class AtmosService(BasePaymentService):
    """ATMOS bind -> charge -> renew integration.

    Unlike Payme/Click/Uzum (which call *us*), ATMOS's charge sequence
    (merchant/pay/create -> pre-apply -> apply) is direct API calls *we* make —
    there is no payment-page redirect for a charge, only for the one-time card
    bind. See docs.atmos.uz/en/ and the spec at
    docs/b2c/specs/atmos-payment-integration.md for the full design and the
    doc anchors behind every field name used here.

    Bind-callback attribution gap — read before touching bind/callback code:
    ATMOS's card-bind callback body is documented as exactly
    ``{"api_key": ..., "card_id": ...}`` ("Card binding by the owner") — no
    request_id, account, or payment_id is echoed back, and the companion
    "Получение деталей привязанной карты" (``GET /mps/pay/card/{id}``) lookup
    doesn't surface them either (its response has ``store_id`` but nothing
    request-specific). There is therefore no documented way to tell which
    pending bind a given callback belongs to. Until ATMOS confirms a
    per-request correlation field (open question in the spec), this service
    serializes bind attempts through a single merchant-wide lock
    (``_acquire_bind_lock``) so at most one bind is ever awaiting a callback at
    a time, and attributes an incoming callback to whichever bind currently
    holds that lock. This is a deliberate interim mitigation, not a guess
    presented as confirmed protocol behavior.
    """

    provider = "atmos"

    def __init__(self):
        super().__init__()
        self.client = AtmosClient()
        self.mandates_collection = settings.ATMOS_MANDATES_COLLECTION
        self.bind_lock_collection = settings.ATMOS_BIND_LOCK_COLLECTION
        self.transactions_collection = settings.ATMOS_TRANSACTIONS_COLLECTION
        self._atmos_indexes_ready = False

    async def _ensure_atmos_indexes(self) -> None:
        if self._atmos_indexes_ready:
            return
        try:
            mandates = self.db_handler.db[self.mandates_collection]
            await mandates.create_index([("request_id", 1)], unique=True)
            await mandates.create_index([("user_id", 1), ("status", 1)])
            invoices = self.db_handler.db[self.invoices_collection]
            await invoices.create_index(
                [("provider", 1), ("atmos_account", 1)],
                unique=True,
                partialFilterExpression={"atmos_account": {"$type": "string"}},
            )
            self._atmos_indexes_ready = True
        except Exception as exc:
            logger.warning(f"[Atmos] Failed to ensure indexes: {exc}")

    # BasePaymentService.build_payment_link is intentionally left un-overridden:
    # ATMOS's user-facing entry points (bind, charge) don't fit the single
    # "create an invoice, return a redirect link" contract init_payment assumes
    # — bind has no amount, and charge has no redirect at all. See start_bind /
    # charge_mandate below instead.

    def _build_invoice_document(
        self,
        *,
        order_id: str,
        user_id: str,
        amount_sum: int,
        callback_url: str,
        purpose: str,
        quote: dict | None,
        now_ms: int,
    ) -> dict:
        document = super()._build_invoice_document(
            order_id=order_id,
            user_id=user_id,
            amount_sum=amount_sum,
            callback_url=callback_url,
            purpose=purpose,
            quote=quote,
            now_ms=now_ms,
        )
        # amount_tiyin: the base implementation omits it (only Payme's override
        # adds it); ATMOS's own API is entirely tiyin-denominated.
        document["amount_tiyin"] = amount_sum * 100
        return document

    @staticmethod
    def _generate_numeric_account(now_ms: int) -> str:
        """A digit-only string ATMOS's merchant/pay/* endpoints require for
        `account` (docs: "Поле account принимает в себя цифровое значение").
        Monotonic-ish (ms timestamp) plus a random 3-digit suffix; the unique
        index on (provider, atmos_account) plus one retry in charge_mandate
        covers the residual same-millisecond collision risk.
        """
        return f"{now_ms}{secrets.randbelow(1000):03d}"

    # ------------------------------------------------------------------ bind

    async def _acquire_bind_lock(self, request_id: str, now_ms: int) -> None:
        """Raises AtmosError(409) if another bind is currently in flight.

        Standard "lock document" upsert pattern: the filter only matches an
        expired-or-absent lock, so a held lock makes the upsert's implicit
        insert collide on `_id` and raise DuplicateKeyError — that collision
        *is* "someone else holds the lock", not an error to propagate.
        """
        lock_collection = self.db_handler.db[self.bind_lock_collection]
        ttl_ms = settings.ATMOS_BIND_LOCK_TTL_SECONDS * 1000
        try:
            await lock_collection.find_one_and_update(
                {"_id": "singleton", "expires_at_ms": {"$lte": now_ms}},
                {
                    "$set": {
                        "request_id": request_id,
                        "expires_at_ms": now_ms + ttl_ms,
                        "acquired_at_ms": now_ms,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            raise AtmosError(
                "Another card binding is already in progress; please try again "
                "in a few minutes.",
                status_code=409,
            ) from None

    async def _release_bind_lock(self, request_id: str) -> None:
        await self.db_handler.db[self.bind_lock_collection].delete_one(
            {"_id": "singleton", "request_id": request_id}
        )

    async def start_bind(self, *, user_id: str, success_url: str) -> dict:
        await self._ensure_atmos_indexes()
        user = await self.get_user_by_id(user_id)
        if not user:
            raise AtmosError("User not found", status_code=404)

        now_ms = int(time.time() * 1000)
        request_id = secrets.token_hex(8)
        await self._acquire_bind_lock(request_id, now_ms)

        try:
            response = await self.client.create_card_bind(
                request_id=request_id, account=request_id, success_url=success_url
            )
        except Exception:
            await self._release_bind_lock(request_id)
            raise

        await self.db_handler.insert_one(
            self.mandates_collection,
            {
                "request_id": request_id,
                "user_id": user_id,
                "status": "pending_bind",
                "atmos_payment_id": response.get("payment_id"),
                "atmos_card_id": None,
                "created_at_ms": now_ms,
                "updated_at_ms": now_ms,
            },
        )
        return {"request_id": request_id, "url": response["url"]}

    async def handle_bind_callback(self, payload: dict) -> dict:
        api_key = payload.get("api_key")
        card_id = payload.get("card_id")

        if not api_key or not secrets.compare_digest(
            str(api_key), str(settings.ATMOS_CALLBACK_API_KEY or "")
        ):
            logger.warning("[Atmos] Bind callback with invalid or missing api_key")
            return {"status": 0, "message": "Invalid api_key"}
        if card_id is None:
            return {"status": 0, "message": "Missing card_id"}

        now_ms = int(time.time() * 1000)
        lock = await self.db_handler.find_one(
            self.bind_lock_collection, {"_id": "singleton"}
        )
        if not lock:
            logger.error(
                f"[Atmos] Bind callback for card_id={card_id} arrived with no "
                "pending bind lock held — cannot attribute to a user."
            )
            return {"status": 0, "message": "No pending bind"}

        mandate = await self.db_handler.find_one(
            self.mandates_collection,
            {"request_id": lock["request_id"], "status": "pending_bind"},
        )
        if not mandate:
            logger.error(
                f"[Atmos] Bind lock held for request_id={lock['request_id']} but "
                f"no matching pending mandate — cannot attribute card_id={card_id}."
            )
            return {"status": 0, "message": "No pending mandate"}

        user_id = mandate["user_id"]

        # At most one active mandate per user — a new bind supersedes the old one.
        await self.db_handler.update_one(
            self.mandates_collection,
            {"user_id": user_id, "status": "active"},
            {
                "status": "revoked",
                "revoked_at_ms": now_ms,
                "revoked_reason": "superseded_by_new_bind",
            },
        )
        await self.db_handler.update_one(
            self.mandates_collection,
            {"request_id": mandate["request_id"]},
            {
                "status": "active",
                "atmos_card_id": int(card_id),
                "bound_at_ms": now_ms,
                "updated_at_ms": now_ms,
            },
        )
        await self._release_bind_lock(lock["request_id"])

        logger.info(
            f"[Atmos] Bound card_id={card_id} to user={user_id} "
            f"(request_id={mandate['request_id']})"
        )
        return {"status": 1, "message": "Успешно"}

    async def get_active_mandate(self, user_id: str) -> dict | None:
        return await self.db_handler.find_one(
            self.mandates_collection, {"user_id": user_id, "status": "active"}
        )

    async def unbind(self, user_id: str) -> bool:
        """Revoke the user's active mandate. See spec open question: the docs
        describe `/partner/remove-card` in the context of the raw-PAN bind API,
        not this hosted-bind flow — whether it applies here is unconfirmed, so
        this only marks our own mandate revoked (stops future renewals) rather
        than also calling an ATMOS unbind endpoint that may not be the right one.
        """
        now_ms = int(time.time() * 1000)
        result = await self.db_handler.update_one(
            self.mandates_collection,
            {"user_id": user_id, "status": "active"},
            {"status": "revoked", "revoked_at_ms": now_ms, "revoked_reason": "user_requested"},
        )
        return bool(result)

    async def get_diagnostic(self, user_id: str) -> dict:
        """For the admin panel: current mandate + most recent charge/renewal
        attempt. Read-only, no side effects — mirrors admin_subscription.py's
        diagnostic-view convention (stored state, not a live ATMOS call)."""
        mandate_cursor = (
            self.db_handler.db[self.mandates_collection]
            .find({"user_id": user_id})
            .sort("created_at_ms", -1)
            .limit(1)
        )
        mandates = await mandate_cursor.to_list(length=1)
        mandate = mandates[0] if mandates else None

        invoice_cursor = (
            self.db_handler.db[self.invoices_collection]
            .find({"provider": self.provider, "user_id": user_id})
            .sort("created_at", -1)
            .limit(5)
        )
        recent_invoices = await invoice_cursor.to_list(length=5)

        return {
            "user_id": user_id,
            "mandate_status": (mandate or {}).get("status", "none"),
            "atmos_card_id": (mandate or {}).get("atmos_card_id"),
            "bound_at_ms": (mandate or {}).get("bound_at_ms"),
            "revoked_at_ms": (mandate or {}).get("revoked_at_ms"),
            "recent_charges": [
                {
                    "order_id": inv.get("order_id"),
                    "status": inv.get("status"),
                    "is_renewal": inv.get("is_renewal", False),
                    "amount_sum": inv.get("amount_sum"),
                    "created_at_ms": inv.get("created_at"),
                    "atmos_error": inv.get("atmos_error"),
                }
                for inv in recent_invoices
            ],
        }

    # ---------------------------------------------------------------- charge

    async def charge_mandate(
        self,
        *,
        user_id: str,
        subscription_tier: str,
        subscription_period: str,
        is_renewal: bool,
    ) -> dict:
        """create -> pre-apply -> apply against the user's bound card_token.
        The single code path for both the initial post-bind charge and every
        renewal — see the module docstring and spec "Recurring" section."""
        await self.ensure_invoice_indexes()
        await self._ensure_atmos_indexes()

        mandate = await self.get_active_mandate(user_id)
        if not mandate or not mandate.get("atmos_card_id"):
            raise AtmosError("No active ATMOS mandate for this user", status_code=404)

        quote = self._get_subscription_quote(subscription_tier, subscription_period)
        now_ms = int(time.time() * 1000)

        if not is_renewal:
            # Renewals run *while* the current period is still active by design
            # (see spec) — the eligibility guard only applies to the
            # user-initiated first charge.
            await self.validate_subscription_eligibility(
                user_id=user_id, quote=quote, now_ms=now_ms
            )

        order_id = secrets.token_hex(8)
        atmos_account = self._generate_numeric_account(now_ms)
        amount_sum = int(quote["amount_sum"])

        invoice_doc = self._build_invoice_document(
            order_id=order_id,
            user_id=user_id,
            amount_sum=amount_sum,
            callback_url="",
            purpose=self._resolve_purpose(quote),
            quote=quote,
            now_ms=now_ms,
        )
        invoice_doc.update(
            {
                "atmos_account": atmos_account,
                "atmos_card_id": mandate["atmos_card_id"],
                "is_renewal": is_renewal,
            }
        )
        await self.db_handler.insert_one(self.invoices_collection, invoice_doc)

        def _fail(reason: str) -> dict:
            return {"order_id": order_id, "status": "failed", "ofd_url": None, "detail": reason}

        try:
            create_resp = await self.client.create_transaction(
                account=atmos_account, amount_tiyin=amount_sum * 100
            )
            transaction_id = create_resp["transaction_id"]
            await self.db_handler.update_one(
                self.invoices_collection,
                {"provider": self.provider, "order_id": order_id},
                {"atmos_transaction_id": transaction_id, "updated_at": now_ms},
            )

            await self.client.pre_apply(
                transaction_id=transaction_id, card_token=mandate["atmos_card_id"]
            )
            apply_resp = await self.client.apply(transaction_id=transaction_id)
        except AtmosServiceError as exc:
            logger.warning(f"[Atmos] Charge failed for user={user_id} order={order_id}: {exc}")
            await self.db_handler.update_one(
                self.invoices_collection,
                {"provider": self.provider, "order_id": order_id},
                {"status": "failed", "updated_at": now_ms, "atmos_error": str(exc)},
            )
            return _fail(str(exc))

        store_tx = apply_resp.get("store_transaction") or {}
        if not store_tx.get("confirmed"):
            await self.db_handler.update_one(
                self.invoices_collection,
                {"provider": self.provider, "order_id": order_id},
                {"status": "failed", "updated_at": now_ms},
            )
            return _fail("not confirmed")

        ofd_url = apply_resp.get("ofd_url")
        await self.db_handler.update_one(
            self.invoices_collection,
            {"provider": self.provider, "order_id": order_id},
            {
                "status": "paid",
                "updated_at": now_ms,
                "ofd_url": ofd_url,
                "ofd_url_commission": apply_resp.get("ofd_url_commission"),
            },
        )

        if is_renewal:
            await self.subscription_storage.upsert_subscription(
                user_id=user_id,
                quote=quote,
                order_id=order_id,
                transaction_id=str(transaction_id),
                now_ms=now_ms,
                provider=self.provider,
            )
            await self.db_handler.update_one(
                self.invoices_collection,
                {"provider": self.provider, "order_id": order_id},
                {"subscription_applied": True, "updated_at": now_ms},
            )
        else:
            await self._finalize_subscription_invoice(
                order_id=order_id, transaction_id=str(transaction_id), now_ms=now_ms
            )

        return {"order_id": order_id, "status": "paid", "ofd_url": ofd_url}

    # ------------------------------------------------------------- callback

    def _verify_payment_sign(
        self, *, store_id, transaction_id, invoice, amount, sign
    ) -> bool:
        api_key = settings.ATMOS_CALLBACK_API_KEY or ""
        # sign = md5(store_id + transaction_id + invoice + amount + api_key), no
        # separators, over the raw received string values (docs.atmos.uz Callback
        # API) — do not re-serialize amount before hashing.
        raw = f"{store_id}{transaction_id}{invoice}{amount}{api_key}"
        expected = _md5_hex(raw)
        return secrets.compare_digest(expected, str(sign or ""))

    async def handle_payment_callback(self, payload: dict) -> dict:
        """Pure authorization gate. Per "Provider semantics" in the spec, ATMOS
        asks *before* debiting — this handler only confirms the invoice is
        legitimate and the amount matches; it never grants anything. The actual
        grant happens in charge_mandate() once apply() confirms the debit,
        since that call (which we ourselves make) is what's in flight while
        this callback fires.
        """
        store_id = payload.get("store_id")
        transaction_id = payload.get("transaction_id")
        invoice = payload.get("invoice")
        amount = payload.get("amount")
        sign = payload.get("sign")

        if not self._verify_payment_sign(
            store_id=store_id, transaction_id=transaction_id, invoice=invoice,
            amount=amount, sign=sign,
        ):
            logger.warning(f"[Atmos] Payment callback sign mismatch for invoice={invoice}")
            return {"status": 0, "message": "Invalid sign"}

        invoice_doc = await self.db_handler.find_one(
            self.invoices_collection,
            {"provider": self.provider, "atmos_account": str(invoice)},
        )
        if not invoice_doc:
            return {"status": 0, "message": "Invoice not found"}

        expected_tiyin = int(invoice_doc.get("amount_tiyin") or 0)
        try:
            amount_int = int(amount)
        except (TypeError, ValueError):
            return {"status": 0, "message": "Invalid amount"}
        if amount_int != expected_tiyin:
            return {"status": 0, "message": "Amount mismatch"}

        return {"status": 1, "message": "Успешно"}

    # -------------------------------------------------------------- renewal

    async def _claim_renewal(self, user_id: str, period_start_ms: int) -> bool:
        """Atomic claim per (user_id, period_start_ms) — mirrors
        AppStoreService._claim_transaction (find_one_and_update +
        $setOnInsert + ReturnDocument.BEFORE) rather than the racy
        read-then-write _finalize_subscription_invoice uses. Returns True if
        this call is the first (and therefore owns the renewal)."""
        collection = self.db_handler.db[self.transactions_collection]
        doc_id = f"renewal:{user_id}:{period_start_ms}"
        existing = await collection.find_one_and_update(
            {"_id": doc_id},
            {
                "$setOnInsert": {
                    "_id": doc_id,
                    "user_id": user_id,
                    "period_start_ms": period_start_ms,
                    "claimed_at_ms": int(time.time() * 1000),
                }
            },
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        return existing is None

    async def renew_due(self, *, user_id: str | None = None) -> list[dict]:
        """Charge every active mandate whose subscription is within
        _RENEWAL_LOOKAHEAD_MS of end_ms (or immediately, when ``user_id`` names
        one mandate — the admin dashboard's manual "run renewal now"). Called by
        the external scheduler (no user_id) or the admin action (one user_id) —
        same code path either way, per the spec.
        """
        now_ms = int(time.time() * 1000)
        query: dict = {"status": "active"}
        if user_id:
            query["user_id"] = user_id

        mandates = await self.db_handler.db[self.mandates_collection].find(query).to_list(
            length=1000
        )

        results: list[dict] = []
        for mandate in mandates:
            uid = mandate["user_id"]
            subscription = await self.subscription_storage.get_subscription(uid)
            if not isinstance(subscription, dict) or subscription.get("provider") != self.provider:
                # No ATMOS-funded subscription to renew for this mandate (e.g. a
                # bound card that was never charged, or last funded by another
                # provider) — nothing to do.
                continue

            end_ms = int(subscription.get("end_ms") or 0)
            if not user_id and end_ms > now_ms + _RENEWAL_LOOKAHEAD_MS:
                results.append(
                    {"user_id": uid, "outcome": "skipped_not_due", "order_id": None}
                )
                continue

            if not await self._claim_renewal(uid, end_ms):
                results.append(
                    {
                        "user_id": uid,
                        "outcome": "skipped_not_due",
                        "order_id": None,
                        "detail": "already claimed for this period",
                    }
                )
                continue

            try:
                charge_result = await self.charge_mandate(
                    user_id=uid,
                    subscription_tier=subscription["tier"],
                    subscription_period=subscription["period"],
                    is_renewal=True,
                )
                outcome = "charged" if charge_result["status"] == "paid" else "failed"
                results.append(
                    {
                        "user_id": uid,
                        "order_id": charge_result["order_id"],
                        "outcome": outcome,
                        "detail": charge_result.get("detail"),
                    }
                )
            except Exception as exc:
                logger.exception(f"[Atmos] Renewal failed for user={uid}: {exc}")
                results.append(
                    {"user_id": uid, "outcome": "failed", "order_id": None, "detail": str(exc)}
                )

        return results
