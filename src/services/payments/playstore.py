import asyncio
import time

from dateutil import parser as date_parser
from pymongo import ReturnDocument

from core.config import settings
from core.logger import logger
from models.payment import GooglePlayError
from services.payments.base import BasePaymentService

# SubscriptionPurchaseV2.subscriptionState values that must NOT grant/extend access.
# Every other state (ACTIVE, CANCELED-but-not-expired, IN_GRACE_PERIOD, ON_HOLD, ...)
# is left to the same expiry-based `active` computation every other provider uses
# (see BasePaymentService.get_user_subscription) — Play's own state machine only
# gates the cases that expiry alone can't express (never-completed or explicitly
# canceled-before-completion purchases).
_NO_GRANT_STATES = {
    "SUBSCRIPTION_STATE_PENDING",
    "SUBSCRIPTION_STATE_EXPIRED",
    "SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED",
}


def _parse_rfc3339_ms(value: str | None) -> int | None:
    """Google API timestamps are RFC3339 (e.g. "2026-09-05T10:15:30.123456Z"),
    not the epoch-ms StoreKit already gives Apple's payloads — python's stdlib
    `datetime.fromisoformat` can't reliably parse these on Python 3.10 (nanosecond
    fractional precision, trailing "Z"), so this uses `dateutil` like
    `promo_code_service.py` already does for the same reason."""
    if not value:
        return None
    return int(date_parser.isoparse(value).timestamp() * 1000)


class GooglePlayService(BasePaymentService):
    provider = "playstore"

    def __init__(self):
        super().__init__()
        self.transactions_collection = settings.PLAYSTORE_TRANSACTIONS_COLLECTION
        self._client = None
        self._playstore_indexes_ready = False

    # MARK: androidpublisher client

    def _get_client(self):
        """Lazily builds (and caches) the androidpublisher client. Verification is
        a live authenticated API call — unlike Apple's offline JWS verification,
        Google Play has no signed-payload equivalent for purchases."""
        if self._client is not None:
            return self._client

        if not settings.PLAYSTORE_SERVICE_ACCOUNT_FILE:
            raise GooglePlayError(
                "PLAYSTORE_SERVICE_ACCOUNT_FILE is not configured; cannot verify "
                "Google Play purchases.",
                status_code=500,
            )

        # Imported lazily so a deployment without Play Billing configured never
        # pays the import cost / needs the dependency importable at module load.
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            settings.PLAYSTORE_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        # cache_discovery=False: the bundled discovery doc is used directly, no
        # need for googleapiclient's on-disk discovery cache in a container.
        self._client = build(
            "androidpublisher", "v3", credentials=credentials, cache_discovery=False
        )
        return self._client

    # MARK: package name / product mapping

    def _package_matches(self, candidate: str) -> bool:
        return candidate == settings.PLAYSTORE_PACKAGE_NAME

    def _map_product(self, product_id: str) -> tuple[str, str]:
        """``wakil.android.sub.<tier>.<period>`` -> (tier, period).

        Same shape as AppStoreService._map_product, validated against the shared
        subscription catalog. Daily passes are not Play products and are always
        rejected (a 1-day auto-renewing subscription isn't a good Play fit).
        """
        parts = product_id.rsplit(".sub.", 1)[-1].split(".")
        if len(parts) != 2:
            raise GooglePlayError(
                f"Unrecognized product id: {product_id}", status_code=422
            )
        tier, period = parts
        if period == "daily":
            raise GooglePlayError(
                f"Unsupported Play product id: {product_id}", status_code=422
            )
        try:
            self._get_subscription_quote(tier, period)
        except ValueError:
            raise GooglePlayError(
                f"Unsupported Play product id: {product_id}", status_code=422
            ) from None
        return tier, period

    # MARK: idempotency store

    async def _ensure_playstore_indexes(self) -> None:
        if self._playstore_indexes_ready:
            return
        try:
            collection = self.db_handler.db[self.transactions_collection]
            await collection.create_index([("purchase_token", 1)], unique=True)
            self._playstore_indexes_ready = True
        except Exception as exc:
            logger.warning(f"[PlayStore] Failed to ensure indexes: {exc}")

    async def _claim_purchase(self, document: dict) -> dict | None:
        """Atomically claim a purchase token. Returns the pre-existing record if
        already claimed, or None if we are the first to insert it (we own the
        grant). Play's idempotency key is purchaseToken, not a stable per-subscription
        id — it changes on renewal, so each billing cycle's charge is claimed
        separately, same as Apple's per-charge transactionId."""
        collection = self.db_handler.db[self.transactions_collection]
        return await collection.find_one_and_update(
            {"purchase_token": document["purchase_token"]},
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )

    async def _mark_granted(self, purchase_token: str, now_ms: int) -> None:
        await self.db_handler.update_one(
            self.transactions_collection,
            {"purchase_token": purchase_token},
            {"granted": True, "granted_at_ms": now_ms},
        )

    # MARK: Play Developer API call

    async def _fetch_subscription_purchase(self, purchase_token: str) -> dict:
        client = self._get_client()
        request = (
            client.purchases()
            .subscriptionsv2()
            .get(packageName=settings.PLAYSTORE_PACKAGE_NAME, token=purchase_token)
        )
        try:
            # googleapiclient is synchronous/blocking — keep it off the event loop,
            # same as AppStoreService does for the JWS verifier's blocking calls.
            return await asyncio.to_thread(request.execute)
        except Exception as exc:
            logger.warning(f"[PlayStore] subscriptionsv2.get failed: {exc}")
            raise GooglePlayError(
                "Purchase could not be verified", status_code=422
            ) from exc

    def _line_item_for_product(self, purchase: dict, product_id: str) -> dict | None:
        for item in purchase.get("lineItems") or []:
            if item.get("productId") == product_id:
                return item
        return None

    # MARK: shared grant

    async def _grant_from_purchase(
        self,
        purchase: dict,
        *,
        purchase_token: str,
        product_id: str,
        tier: str,
        period: str,
        user_id: str,
        now_ms: int,
    ) -> dict:
        """Grant (once) from a verified SubscriptionPurchaseV2. Idempotent per
        purchaseToken. Returns a summary dict with tier/period/expires/granted."""
        subscription_state = purchase.get("subscriptionState")
        line_item = self._line_item_for_product(purchase, product_id)
        expires_ms = _parse_rfc3339_ms(
            line_item.get("expiryTime") if line_item else None
        )

        summary = {
            "tier": tier,
            "period": period,
            "expires_ms": expires_ms,
        }

        base_claim = {
            "purchase_token": purchase_token,
            "user_id": user_id,
            "product_id": product_id,
            "tier": tier,
            "period": period,
            "subscription_state": subscription_state,
            "expires_date_ms": expires_ms,
            "granted": False,
            "source": "verify",
            "created_at_ms": now_ms,
        }

        # A purchase that never completed, or was explicitly canceled before it
        # did, must not grant — matches Apple's revoked-transaction skip.
        if subscription_state in _NO_GRANT_STATES:
            await self._claim_purchase({**base_claim, "note": subscription_state})
            return {**summary, "granted": False}

        # No line item for this product id, or no expiry at all — nothing to grant.
        if expires_ms is None:
            await self._claim_purchase({**base_claim, "note": "missing_expiry"})
            return {**summary, "granted": False}

        # Already-expired purchase — almost always a stale replay. Claim it (so a
        # retry doesn't keep re-checking) but don't grant a fresh period, same
        # reasoning as AppStoreService's expired-transaction skip.
        if expires_ms <= now_ms:
            await self._claim_purchase({**base_claim, "note": "expired"})
            logger.info(
                f"[PlayStore] Skipping expired purchase {purchase_token} "
                f"(expires_ms={expires_ms} <= now_ms={now_ms}) for user {user_id}"
            )
            return {**summary, "granted": False}

        # Claim the purchase token. If someone already claimed it, don't re-grant.
        existing = await self._claim_purchase(base_claim)
        if existing is not None:
            return {**summary, "granted": bool(existing.get("granted"))}

        # We own the grant. If applying credits fails, remove our (granted=False)
        # claim so a retry can re-drive the grant instead of being permanently
        # skipped.
        quote = self._get_subscription_quote(tier, period)
        try:
            await self.subscription_storage.upsert_subscription(
                user_id=user_id,
                quote=quote,
                order_id=purchase_token,
                transaction_id=purchase_token,
                now_ms=now_ms,
                provider=self.provider,
            )
        except Exception:
            await self.db_handler.db[self.transactions_collection].delete_one(
                {"purchase_token": purchase_token, "granted": False}
            )
            raise
        await self._mark_granted(purchase_token, now_ms)
        logger.info(
            f"[PlayStore] Granted {tier}/{period} to user {user_id} "
            f"(token={purchase_token})"
        )
        return {**summary, "granted": True}

    # MARK: public — verify (called by the Android app)

    async def verify_purchase(
        self,
        *,
        user_id: str,
        package_name: str,
        product_id: str,
        purchase_token: str,
    ) -> dict:
        """``verify_purchase`` called by the Android app right after a purchase
        (POST /transaction/playstore/verify). Verifies via a live call to
        `purchases.subscriptionsv2.get` (Play has no offline signature to check),
        maps the product to a (tier, period), and grants the subscription through
        the same ``SubscriptionStorage.upsert_subscription`` path Payme/Click/App
        Store use.
        """
        await self._ensure_playstore_indexes()

        if not self._package_matches(package_name):
            raise GooglePlayError("Package name mismatch", status_code=422)

        user = await self.get_user_by_id(user_id)
        if not user:
            raise GooglePlayError("User not found", status_code=404)

        tier, period = self._map_product(product_id)
        purchase = await self._fetch_subscription_purchase(purchase_token)

        # The purchase token is verified against our package by Play itself
        # (subscriptionsv2.get above), but `product_id` is a client-supplied
        # request field with no such verification — without this check, a real
        # token for a cheap subscription plus a forged product_id in the request
        # body could grant a pricier tier. Only trust a product_id Play's own
        # response actually lists as sold under this token.
        if self._line_item_for_product(purchase, product_id) is None:
            raise GooglePlayError(
                f"Purchase token does not contain product id: {product_id}",
                status_code=422,
            )

        now_ms = int(time.time() * 1000)
        result = await self._grant_from_purchase(
            purchase,
            purchase_token=purchase_token,
            product_id=product_id,
            tier=tier,
            period=period,
            user_id=user_id,
            now_ms=now_ms,
        )

        # Acknowledge right away for any purchase that's actually granted —
        # unacknowledged purchases auto-refund after 3 days, and there's no
        # client-visible harm in acknowledging one we didn't grant *this* call
        # (e.g. a replayed verify of an already-granted purchase): Play's
        # acknowledge is itself idempotent. `result["granted"]` is true both for
        # a fresh grant and a prior one (see _grant_from_purchase's replay
        # branch), and false for every no-grant path (pending/expired/missing
        # line item) — those must NOT be acknowledged, since expires_ms can be
        # 0 (not None) on the expired path and would otherwise slip through.
        if (
            result.get("granted")
            and purchase.get("acknowledgementState") != "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
        ):
            try:
                await self.acknowledge_purchase(
                    product_id=product_id, purchase_token=purchase_token
                )
            except GooglePlayError as exc:
                # Non-fatal: the grant already succeeded above. Worst case Google
                # auto-refunds this specific purchase after 3 days if every retry
                # (the app re-verifies on next queryPurchasesAsync) also fails to
                # acknowledge — logged so it's visible, not raised to the caller.
                logger.warning(
                    f"[PlayStore] Acknowledge failed after grant for "
                    f"token={purchase_token}: {exc.detail}"
                )

        subscription = await self.get_user_subscription(user_id)
        status = "active" if subscription.get("active") else "inactive"

        return {
            "status": status,
            "tier": result.get("tier"),
            "period": result.get("period"),
            "expires_ms": result.get("expires_ms"),
            "daily_credits": subscription.get("combined_daily_credits")
            or subscription.get("daily_credits")
            or 0,
        }

    # MARK: public — acknowledge (called by the Android app, or a future RTDN handler)

    async def acknowledge_purchase(self, *, product_id: str, purchase_token: str) -> None:
        """Acknowledges a purchase server-side (`purchases.subscriptions.acknowledge`).
        Unacknowledged purchases are auto-refunded by Google after 3 days — this has
        no App Store equivalent (`transaction.finish()` there is free and client-side).
        Safe to call even if already acknowledged; Play just returns success again.
        """
        client = self._get_client()
        request = (
            client.purchases()
            .subscriptions()
            .acknowledge(
                packageName=settings.PLAYSTORE_PACKAGE_NAME,
                subscriptionId=product_id,
                token=purchase_token,
                body={},
            )
        )
        try:
            await asyncio.to_thread(request.execute)
        except Exception as exc:
            logger.warning(f"[PlayStore] acknowledge failed: {exc}")
            raise GooglePlayError(
                "Purchase could not be acknowledged", status_code=422
            ) from exc
