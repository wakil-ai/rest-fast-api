import asyncio
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace

from pymongo import ReturnDocument

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
)

from core.config import settings
from core.logger import logger
from models.payment import AppStoreError
from services.payments.base import BasePaymentService

# Notification types that (re)grant access.
_GRANT_NOTIFICATIONS = {
    "SUBSCRIBED",
    "DID_RENEW",
    "OFFER_REDEEMED",
    "RESUBSCRIBE",
}
# Notification types that revoke access immediately.
_REVOKE_NOTIFICATIONS = {
    "EXPIRED",
    "GRACE_PERIOD_EXPIRED",
    "REFUND",
    "REVOKE",
}


class AppStoreService(BasePaymentService):
    provider = "appstore"

    def __init__(self):
        super().__init__()
        self.transactions_collection = settings.APPSTORE_TRANSACTIONS_COLLECTION
        self._root_certificates: list[bytes] | None = None
        self._verifier_cache: dict[str, SignedDataVerifier] = {}
        self._appstore_indexes_ready = False

    # MARK: certificates / verifier

    def _load_root_certificates(self) -> list[bytes]:
        if self._root_certificates is not None:
            return self._root_certificates

        if settings.APPSTORE_ROOT_CERTS_DIR:
            certs_dir = Path(settings.APPSTORE_ROOT_CERTS_DIR)
        else:
            # src/services/payments/appstore.py -> src/resources/certs/apple
            certs_dir = (
                Path(__file__).resolve().parents[2]
                / "resources"
                / "certs"
                / "apple"
            )

        certs: list[bytes] = []
        if certs_dir.is_dir():
            for pattern in ("*.cer", "*.der"):
                for cert_file in sorted(certs_dir.glob(pattern)):
                    certs.append(cert_file.read_bytes())

        if not certs:
            logger.warning(
                f"[AppStore] No Apple root certs found in {certs_dir}; "
                "JWS verification will fail for Production/Sandbox."
            )

        self._root_certificates = certs
        return certs

    def _environment_from_str(self, value: str) -> Environment:
        try:
            return Environment(value)
        except ValueError:
            raise AppStoreError(
                f"Unknown environment: {value}", status_code=422
            ) from None

    def _get_verifier(self, environment: Environment) -> SignedDataVerifier:
        cached = self._verifier_cache.get(environment.value)
        if cached is not None:
            return cached

        app_apple_id = settings.APPSTORE_APP_APPLE_ID
        if environment == Environment.PRODUCTION and not app_apple_id:
            raise AppStoreError(
                "APPSTORE_APP_APPLE_ID is not configured; cannot verify production "
                "App Store transactions.",
                status_code=500,
            )

        verifier = SignedDataVerifier(
            self._load_root_certificates(),
            settings.APPSTORE_ENABLE_ONLINE_CHECKS,
            environment,
            settings.APPSTORE_BUNDLE_ID,
            app_apple_id or None,
        )
        self._verifier_cache[environment.value] = verifier
        return verifier

    # MARK: bundle id

    def _bundle_matches(self, candidate: str | None) -> bool:
        """True if ``candidate`` is a bundle id this deployment trusts.

        In DEBUG the Xcode Debug build's bundle id (``APPSTORE_DEBUG_BUNDLE_ID``,
        defaulting to ``<APPSTORE_BUNDLE_ID>-debug``) is trusted too so local
        StoreKit testing works. Production (DEBUG off) requires an exact match.
        """
        expected = settings.APPSTORE_BUNDLE_ID
        if not candidate or not expected:
            return True
        allowed = {expected}
        if settings.DEBUG:
            allowed.add(settings.APPSTORE_DEBUG_BUNDLE_ID or f"{expected}-debug")
        return candidate in allowed

    # MARK: product mapping

    def _map_product(self, product_id: str) -> tuple[str, str]:
        """``ai.humblebee.wakil.ios.sub.<tier>.<period>`` -> (tier, period).

        Validated against the shared subscription catalog so App Store products
        track exactly what Payme/Click sell. Daily passes are not StoreKit
        products and are always rejected.
        """
        parts = product_id.rsplit(".sub.", 1)[-1].split(".")
        if len(parts) != 2:
            raise AppStoreError(
                f"Unrecognized product id: {product_id}", status_code=422
            )
        tier, period = parts
        if period == "daily":
            raise AppStoreError(
                f"Unsupported App Store product id: {product_id}", status_code=422
            )
        try:
            self._get_subscription_quote(tier, period)
        except ValueError:
            raise AppStoreError(
                f"Unsupported App Store product id: {product_id}", status_code=422
            ) from None
        return tier, period

    # MARK: decode

    async def _decode_transaction(
        self, jws: str, environment: Environment
    ) -> SimpleNamespace:
        # Xcode / local StoreKit test transactions are signed by a local test cert
        # that does not chain to Apple's roots, so real verification is impossible.
        # Accept them only in DEBUG, decoding the payload without verifying.
        if environment in (Environment.XCODE, Environment.LOCAL_TESTING):
            if not settings.DEBUG:
                raise AppStoreError(
                    "Xcode/LocalTesting StoreKit transactions are only accepted "
                    "when DEBUG is enabled.",
                    status_code=422,
                )
            return self._decode_unverified(jws)

        verifier = self._get_verifier(environment)
        try:
            # The library's verification is synchronous (X.509 chain + ECDSA, plus
            # blocking OCSP calls when online checks are on) — keep it off the
            # event loop.
            return await asyncio.to_thread(
                verifier.verify_and_decode_signed_transaction, jws
            )
        except VerificationException as exc:
            logger.warning(f"[AppStore] JWS verification failed: {exc}")
            raise AppStoreError(
                "Transaction could not be verified", status_code=422
            ) from exc

    @staticmethod
    def _decode_jws_payload(jws: str) -> dict:
        """Decode a JWS payload segment WITHOUT verifying its signature."""
        try:
            payload_segment = jws.split(".")[1]
            padding = "=" * (-len(payload_segment) % 4)
            return json.loads(base64.urlsafe_b64decode(payload_segment + padding))
        except Exception as exc:
            raise AppStoreError("Malformed JWS payload", status_code=422) from exc

    def _decode_unverified(self, jws: str) -> SimpleNamespace:
        """DEBUG-only: decode a JWS payload WITHOUT verifying its signature.

        Only reached for Xcode/LocalTesting environments (never Production/Sandbox),
        and only when ``settings.DEBUG`` is true.
        """
        return SimpleNamespace(**self._decode_jws_payload(jws))

    # MARK: idempotency store

    async def _ensure_appstore_indexes(self) -> None:
        if self._appstore_indexes_ready:
            return
        try:
            collection = self.db_handler.db[self.transactions_collection]
            await collection.create_index([("transaction_id", 1)], unique=True)
            await collection.create_index([("original_transaction_id", 1)])
            self._appstore_indexes_ready = True
        except Exception as exc:
            logger.warning(f"[AppStore] Failed to ensure indexes: {exc}")

    async def _claim_transaction(self, document: dict) -> dict | None:
        """Atomically claim a transaction id. Returns the pre-existing record if it
        was already claimed, or None if we are the first to insert it (i.e. we own
        the grant)."""
        collection = self.db_handler.db[self.transactions_collection]
        return await collection.find_one_and_update(
            {"transaction_id": document["transaction_id"]},
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )

    async def _mark_granted(self, transaction_id: str, now_ms: int) -> None:
        await self.db_handler.update_one(
            self.transactions_collection,
            {"transaction_id": transaction_id},
            {"granted": True, "granted_at_ms": now_ms},
        )

    # MARK: shared grant

    async def _grant_from_payload(
        self,
        payload: SimpleNamespace,
        *,
        user_id: str,
        environment_str: str,
        source: str,
        now_ms: int,
    ) -> dict:
        """Grant (once) from a decoded transaction payload. Idempotent per
        transactionId; pins ownership per originalTransactionId. Returns a summary
        dict with tier/period/expires/granted."""
        transaction_id = getattr(payload, "transactionId", None)
        original_transaction_id = getattr(payload, "originalTransactionId", None)
        product_id = getattr(payload, "productId", None)
        payload_bundle = getattr(payload, "bundleId", None)
        expires_ms = getattr(payload, "expiresDate", None)
        purchase_ms = getattr(payload, "purchaseDate", None)
        revocation_ms = getattr(payload, "revocationDate", None)
        token = getattr(payload, "appAccountToken", None)

        if not transaction_id or not product_id:
            raise AppStoreError(
                "Transaction payload missing required fields", status_code=422
            )

        if not self._bundle_matches(payload_bundle):
            raise AppStoreError("Bundle id mismatch", status_code=422)

        tier, period = self._map_product(product_id)

        # Common fields of every result this method returns; each exit adds its
        # own granted/revoked/expired flags.
        summary = {
            "tier": tier,
            "period": period,
            "original_transaction_id": original_transaction_id,
            "expires_ms": expires_ms,
        }

        # Shared record for this transaction; all skip/grant paths reuse it.
        base_claim = {
            "transaction_id": transaction_id,
            "original_transaction_id": original_transaction_id,
            "user_id": user_id,
            "app_account_token": token,
            "product_id": product_id,
            "tier": tier,
            "period": period,
            "environment": environment_str,
            "purchase_date_ms": purchase_ms,
            "expires_date_ms": expires_ms,
            "granted": False,
            "source": source,
            "created_at_ms": now_ms,
        }

        # Ownership: an originalTransactionId already bound to a different user is a
        # hard conflict (transferred device, shared Apple ID being abused, etc.).
        if original_transaction_id:
            conflict = await self.db_handler.find_one(
                self.transactions_collection,
                {
                    "original_transaction_id": original_transaction_id,
                    "user_id": {"$ne": user_id},
                },
            )
            if conflict:
                raise AppStoreError(
                    "This subscription is already associated with a different "
                    "account.",
                    status_code=409,
                )

        # A revoked/refunded transaction must not grant.
        if revocation_ms:
            await self._claim_transaction(
                {**base_claim, "revocation_date_ms": revocation_ms}
            )
            return {**summary, "granted": False, "revoked": True}

        # Already-expired transaction — almost always a stale one redelivered by the
        # StoreKit transaction listener (e.g. old sandbox purchases from earlier tests).
        # Acknowledge it (record + 200) so StoreKit stops redelivering, but do NOT grant
        # a fresh period; otherwise every replayed historical transaction stacks another
        # month/year of credits onto the user. A current purchase always has expiresDate
        # in the future, so this only skips genuinely-lapsed transactions.
        if expires_ms is not None and expires_ms <= now_ms:
            await self._claim_transaction({**base_claim, "note": "expired"})
            logger.info(
                f"[AppStore] Skipping expired transaction {transaction_id} "
                f"(expired_ms={expires_ms} <= now_ms={now_ms}) for user {user_id}"
            )
            return {**summary, "granted": False, "expired": True}

        # Claim the transaction id. If someone already claimed it, don't re-grant.
        existing = await self._claim_transaction(base_claim)
        if existing is not None:
            if not existing.get("granted"):
                # Expected under concurrency: StoreKit delivers the same transaction
                # via BOTH the purchase() result and the Transaction.updates listener,
                # so two verify calls race. The loser skips here; the winner grants.
                # This is normal, not an error.
                logger.info(
                    f"[AppStore] Transaction {transaction_id} already in flight "
                    "(concurrent duplicate verify); skipping to avoid double-grant."
                )
            return {
                **summary,
                "granted": bool(existing.get("granted")),
                "already_processed": True,
            }

        # We own the grant. If applying credits fails, remove our (granted=False)
        # claim so a StoreKit retry — the transaction stays unfinished on the device
        # until we ack — can re-drive the grant instead of being permanently skipped.
        quote = self._get_subscription_quote(tier, period)
        try:
            await self.subscription_storage.upsert_subscription(
                user_id=user_id,
                quote=quote,
                order_id=transaction_id,
                transaction_id=transaction_id,
                now_ms=now_ms,
                provider=self.provider,
                verified_end_ms=expires_ms,
                full_price_standard_to_pro_upgrade=True,
            )
        except Exception:
            # Only removes our own uncommitted claim; a claim another request has
            # already granted (granted=True) is left untouched.
            await self.db_handler.db[self.transactions_collection].delete_one(
                {"transaction_id": transaction_id, "granted": False}
            )
            raise
        await self._mark_granted(transaction_id, now_ms)
        logger.info(
            f"[AppStore] Granted {tier}/{period} to user {user_id} "
            f"(tx={transaction_id}, source={source})"
        )
        return {**summary, "granted": True}

    async def _revoke_subscription(self, user_id: str, now_ms: int) -> None:
        """Expire the user's App Store subscription in place (refund/revoke/expiry).

        Only touches subscriptions granted by this provider so a web (Payme/Click)
        subscription is never clobbered by an Apple notification.
        """
        await self.db_handler.update_one(
            settings.SUBSCRIPTIONS_COLLECTION,
            {"user_id": user_id, "provider": self.provider},
            {
                "end_ms": now_ms,
                "credits_remaining": 0,
                "updated_at_ms": now_ms,
                "revoked_at_ms": now_ms,
            },
        )

    # MARK: public — verify (called by the iOS app)

    async def verify_transaction(
        self,
        *,
        user_id: str,
        jws: str,
        environment: str,
        bundle_id: str,
    ) -> dict:
        """
        ``verify_transaction`` called by the iOS app right after a purchase/restore
        (POST /transaction/appstore/verify). Verifies the signed transaction, maps the
        product to a (tier, period), and grants the subscription through the same
        ``SubscriptionStorage.upsert_subscription`` path Payme/Click use.
        """
        await self._ensure_appstore_indexes()

        if not self._bundle_matches(bundle_id):
            raise AppStoreError("Bundle id mismatch", status_code=422)

        user = await self.get_user_by_id(user_id)
        if not user:
            raise AppStoreError("User not found", status_code=404)

        payload = await self._decode_transaction(
            jws, self._environment_from_str(environment)
        )
        now_ms = int(time.time() * 1000)
        result = await self._grant_from_payload(
            payload,
            user_id=user_id,
            environment_str=environment,
            source="verify",
            now_ms=now_ms,
        )

        subscription = await self.get_user_subscription(user_id)
        if result.get("revoked"):
            status = "revoked"
        elif subscription.get("active"):
            status = "active"
        else:
            status = "inactive"

        return {
            "status": status,
            "tier": result.get("tier"),
            "period": result.get("period"),
            "original_transaction_id": result.get("original_transaction_id"),
            "expires_ms": result.get("expires_ms"),
            "daily_credits": subscription.get("combined_daily_credits")
            or subscription.get("daily_credits")
            or 0,
        }

    # MARK: public — notifications (called by Apple)

    async def _verify_notification(self, signed_payload: str):
        """Apple signs notifications with the cert chain of the environment they
        came from. The environment is inside the signed payload — peek at it
        without verifying to pick the right verifier (a forged claim just makes
        the real verification below fail), then verify for real."""
        body = self._decode_jws_payload(signed_payload)
        env_name = (body.get("data") or {}).get("environment") or (
            body.get("summary") or {}
        ).get("environment")
        if not env_name:
            raise AppStoreError(
                "Notification payload missing environment", status_code=422
            )

        verifier = self._get_verifier(self._environment_from_str(env_name))
        try:
            return await asyncio.to_thread(
                verifier.verify_and_decode_notification, signed_payload
            )
        except VerificationException as exc:
            logger.warning(f"[AppStore] Notification verification failed: {exc}")
            raise AppStoreError(
                "Notification could not be verified", status_code=422
            ) from exc

    async def handle_notification(self, signed_payload: str) -> dict:
        """handle_notification called by Apple's App Store Server Notifications V2
        (POST /transaction/appstore/notifications). Keeps entitlement in sync on
        renewal / expiry / refund without the app polling."""

        await self._ensure_appstore_indexes()

        notification = await self._verify_notification(signed_payload)
        notification_type = getattr(notification, "rawNotificationType", None) or str(
            getattr(notification, "notificationType", "") or ""
        )
        data = getattr(notification, "data", None)
        signed_transaction = getattr(data, "signedTransactionInfo", None) if data else None
        environment = getattr(data, "environment", None) if data else None
        environment_str = (
            environment.value if isinstance(environment, Environment) else str(environment)
        )

        if not signed_transaction:
            # TEST notifications and a few others carry no transaction — just ack.
            logger.info(f"[AppStore] Notification {notification_type} with no transaction; acked")
            return {"handled": False, "type": notification_type}

        # Decode the inner transaction with a verifier for the notification's env.
        env_enum = (
            environment
            if isinstance(environment, Environment)
            else self._environment_from_str(environment_str)
        )
        payload = await self._decode_transaction(signed_transaction, env_enum)

        original_transaction_id = getattr(payload, "originalTransactionId", None)
        transaction_id = getattr(payload, "transactionId", None)

        # Attribute the notification to a user via a prior verify record.
        record = None
        if original_transaction_id:
            record = await self.db_handler.find_one(
                self.transactions_collection,
                {"original_transaction_id": original_transaction_id},
            )
        if record is None:
            logger.warning(
                f"[AppStore] Notification {notification_type} for unknown "
                f"originalTransactionId={original_transaction_id} "
                f"(tx={transaction_id}); acked without action."
            )
            return {"handled": False, "type": notification_type, "reason": "unknown_user"}

        user_id = record["user_id"]
        now_ms = int(time.time() * 1000)

        if notification_type in _GRANT_NOTIFICATIONS:
            result = await self._grant_from_payload(
                payload,
                user_id=user_id,
                environment_str=environment_str,
                source=f"notification:{notification_type}",
                now_ms=now_ms,
            )
            return {"handled": True, "type": notification_type, "granted": result.get("granted")}

        if notification_type in _REVOKE_NOTIFICATIONS:
            await self._revoke_subscription(user_id, now_ms)
            logger.info(
                f"[AppStore] Revoked subscription for user {user_id} "
                f"due to {notification_type} (tx={transaction_id})"
            )
            return {"handled": True, "type": notification_type, "revoked": True}

        # DID_CHANGE_RENEWAL_STATUS, DID_FAIL_TO_RENEW, PRICE_INCREASE, etc. —
        # informational; current access stays until an EXPIRED/REVOKE arrives.
        logger.info(
            f"[AppStore] Notification {notification_type} for user {user_id} acked (no state change)"
        )
        return {"handled": False, "type": notification_type}
