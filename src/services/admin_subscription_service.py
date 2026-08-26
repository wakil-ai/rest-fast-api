"""Manual subscription changes made by an operator, not by a payment.

The one thing to understand before editing this file: ``credits_remaining`` on the
subscription document does not decide what a user can spend. ``RateLimitService``
computes the effective balance as

    total_credits - max(creditusage_used_over_window, total_credits - credits_remaining)

so ``total_credits`` is a hard ceiling and the ``creditusage`` ledger is the real
debit record. Writing ``credits_remaining`` alone grants nothing. Every mutation
here therefore ends in :meth:`AdminSubscriptionService.reconcile_pool_entitlement`,
which sets ``total_credits`` from the ledger so both branches of that ``max()``
agree and the effective balance lands exactly where the operator asked.
"""

import time
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from core.error_codes import ErrorCode
from core.exceptions import AdminActionError
from core.logger import logger
from core.subscription_tiers import is_daily_pass_quote
from models.payment import SubscriptionEligibilityError
from services.admin_audit_service import (
    ACTION_ADJUST_CREDITS,
    ACTION_EXTEND,
    ACTION_GRANT,
    ACTION_REVOKE,
    RESULT_SUCCESS,
)

ADMIN_PROVIDER = "admin"

# Fields whose BSON type the diagnostic checks. A String here reads back fine over
# HTTP but never matches the Mongo-side `{"$gt": now_ms}` predicates.
_INT_TYPED_FIELDS = ("start_ms", "end_ms", "total_credits", "credits_remaining")


class AdminSubscriptionService:
    def __init__(
        self,
        *,
        subscription_storage,
        rate_limit_service,
        transaction_service,
        audit_service,
    ):
        self.subscription_storage = subscription_storage
        self.rate_limit_service = rate_limit_service
        self.transaction_service = transaction_service
        self.audit_service = audit_service

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _as_int(value: Any) -> int:
        """Coerce a possibly String-typed stored field. 0 when unusable."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _lot_object_id(daily_lot_id: str):
        try:
            return ObjectId(daily_lot_id)
        except (InvalidId, TypeError):
            # Lots created outside Mongo's default id generation may use a plain
            # string _id; fall back rather than rejecting a valid lot.
            return daily_lot_id

    @staticmethod
    def _jsonable(document: dict | None) -> dict | None:
        """Stringify `_id` so a raw document survives JSON serialization."""
        if document is None:
            return None
        cleaned = dict(document)
        if "_id" in cleaned:
            cleaned["_id"] = str(cleaned["_id"])
        return cleaned

    async def _computed_view(self, user_id: str) -> dict | None:
        """What the client actually sees. Never raises into a mutation path."""
        try:
            return await self.transaction_service.get_user_subscription(user_id)
        except Exception as exc:
            logger.warning(
                f"[AdminSubscription] Could not compute subscription view for {user_id}: {exc}"
            )
            return None

    async def _computed_remaining(self, user_id: str) -> int:
        sub = await self.subscription_storage.get_raw_subscription(user_id)
        if not isinstance(sub, dict):
            return 0
        return await self.rate_limit_service._get_pool_credits_remaining(user_id, sub)

    async def snapshot(self, user_id: str) -> dict:
        raw = await self.subscription_storage.get_raw_subscription(user_id)
        lots = await self.subscription_storage.get_all_daily_lots(user_id)
        return {
            "raw_subscription": self._jsonable(raw),
            "computed": await self._computed_view(user_id),
            "daily_lot_count": len(lots),
        }

    # ------------------------------------------------------------------
    # The reconciliation step every mutation ends with
    # ------------------------------------------------------------------

    async def reconcile_pool_entitlement(
        self,
        user_id: str,
        desired_remaining: int,
        *,
        now_ms: int,
        expected_updated_at_ms: int | None = None,
    ) -> dict:
        """Make the effective balance equal ``desired_remaining``.

        Sets ``total_credits = creditusage_used + desired_remaining`` over the
        document's *current* window, so the ledger and the stored balance agree.
        """
        sub = await self.subscription_storage.get_raw_subscription(user_id)
        if not isinstance(sub, dict):
            raise AdminActionError(ErrorCode.SUBSCRIPTION_NOT_FOUND, user_id=user_id)

        start_ms = self._as_int(sub.get("start_ms"))
        end_ms = self._as_int(sub.get("end_ms"))
        usage = await self.rate_limit_service._get_subscription_period_credits_used(
            user_id, start_ms, end_ms
        )

        desired = max(0, int(desired_remaining))
        updated = await self.subscription_storage.admin_set_pool_fields(
            user_id,
            updates={
                "total_credits": usage + desired,
                "credits_remaining": desired,
            },
            now_ms=now_ms,
            expected_updated_at_ms=expected_updated_at_ms,
        )
        if updated is None:
            raise AdminActionError(
                ErrorCode.ADMIN_SUBSCRIPTION_CONFLICT, user_id=user_id
            )
        return updated

    async def _verify_effective_balance(
        self, user_id: str, desired_remaining: int, *, now_ms: int
    ) -> None:
        """Confirm the write landed, retrying once against a mid-flight spend."""
        actual = await self._computed_remaining(user_id)
        if actual == desired_remaining:
            return

        await self.reconcile_pool_entitlement(
            user_id, desired_remaining, now_ms=now_ms
        )
        actual = await self._computed_remaining(user_id)
        if actual != desired_remaining:
            raise AdminActionError(
                ErrorCode.ADMIN_RECONCILE_FAILED,
                desired_remaining=desired_remaining,
                actual_remaining=actual,
            )

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------

    def _warnings_for(self, sub: dict | None, lots: list[dict], user: dict | None) -> list[dict]:
        warnings: list[dict] = []

        if isinstance(user, dict) and isinstance(user.get("subscription"), dict):
            warnings.append(
                {
                    "code": "LEGACY_SUBSCRIPTION_ON_USER_DOC",
                    "field": "users.subscription",
                    "message": (
                        "A legacy subscription is embedded on the user document. It "
                        "shadows the subscriptions collection when no row exists there."
                    ),
                }
            )

        if isinstance(sub, dict):
            for field in _INT_TYPED_FIELDS:
                if field not in sub:
                    if field == "credits_remaining":
                        warnings.append(
                            {
                                "code": "CREDITS_REMAINING_MISSING",
                                "field": field,
                                "message": (
                                    "credits_remaining is absent; it is backfilled from "
                                    "total_credits on read."
                                ),
                            }
                        )
                    continue
                if not isinstance(sub[field], int) or isinstance(sub[field], bool):
                    warnings.append(
                        {
                            "code": f"{field.upper()}_NOT_INT",
                            "field": field,
                            "message": (
                                f"{field} is stored as {type(sub[field]).__name__}, not int. "
                                "Mongo-side range predicates will not match it."
                            ),
                        }
                    )

            start_ms = self._as_int(sub.get("start_ms"))
            end_ms = self._as_int(sub.get("end_ms"))
            if end_ms and start_ms and end_ms <= start_ms:
                warnings.append(
                    {
                        "code": "WINDOW_INVERTED",
                        "field": "end_ms",
                        "message": "end_ms is not after start_ms.",
                    }
                )
            if start_ms > self._now_ms():
                warnings.append(
                    {
                        "code": "WINDOW_STARTS_IN_FUTURE",
                        "field": "start_ms",
                        "message": (
                            "The window starts in the future, so creditusage in it "
                            "reads as zero until then."
                        ),
                    }
                )
            if self._as_int(sub.get("total_credits")) < self._as_int(
                sub.get("credits_remaining")
            ):
                warnings.append(
                    {
                        "code": "TOTAL_LESS_THAN_REMAINING",
                        "field": "total_credits",
                        "message": (
                            "total_credits is below credits_remaining; the ceiling wins, "
                            "so the extra balance is unspendable."
                        ),
                    }
                )

        now_ms = self._now_ms()
        active_lots = [lot for lot in lots if self._as_int(lot.get("end_ms")) > now_ms]
        if len(active_lots) > 1:
            warnings.append(
                {
                    "code": "MULTIPLE_ACTIVE_DAILY_LOTS",
                    "field": "daily_subscriptions",
                    "message": (
                        f"{len(active_lots)} daily lots are active; they stack, so name "
                        "the lot explicitly when adjusting one."
                    ),
                }
            )

        return warnings

    async def diagnostic(self, user_id: str) -> dict:
        user = await self.subscription_storage._get_user_document(user_id)
        raw_sub = await self.subscription_storage.get_raw_subscription(user_id)
        lots = await self.subscription_storage.get_all_daily_lots(user_id)
        computed = await self._computed_view(user_id)

        start_ms = self._as_int(raw_sub.get("start_ms")) if raw_sub else 0
        end_ms = self._as_int(raw_sub.get("end_ms")) if raw_sub else 0
        usage = await self.rate_limit_service.get_period_credit_usage_breakdown(
            user_id, start_ms, end_ms
        )

        total = self._as_int(raw_sub.get("total_credits")) if raw_sub else 0
        stored_remaining = self._as_int(raw_sub.get("credits_remaining")) if raw_sub else 0
        creditusage_used = int(usage.get("total_used_in_window") or 0)
        legacy_used = max(0, total - stored_remaining)
        effective_used = max(creditusage_used, legacy_used)
        computed_remaining = max(0, total - effective_used)

        reconciliation = {
            "stored_total_credits": total,
            "stored_credits_remaining": stored_remaining,
            "creditusage_used": creditusage_used,
            "legacy_used": legacy_used,
            "effective_used": effective_used,
            "computed_remaining": computed_remaining,
            "expected_total_credits_for_stored_remaining": creditusage_used
            + stored_remaining,
            "drift": computed_remaining - stored_remaining,
        }

        warnings = self._warnings_for(raw_sub, lots, user)
        if reconciliation["drift"] != 0:
            warnings.append(
                {
                    "code": "ENTITLEMENT_DRIFT",
                    "field": "credits_remaining",
                    "message": (
                        f"Stored balance is {stored_remaining} but the user can actually "
                        f"spend {computed_remaining}."
                    ),
                }
            )

        bson_types = (
            {key: type(value).__name__ for key, value in raw_sub.items()}
            if isinstance(raw_sub, dict)
            else {}
        )

        return {
            "user_id": user_id,
            "user": {
                "exists": user is not None,
                "id_field": None
                if user is None
                else ("_id" if user.get("_id") == user_id else "user_id"),
                "username": (user or {}).get("username"),
                "first_name": (user or {}).get("first_name"),
                "last_name": (user or {}).get("last_name"),
                "phone_number": (user or {}).get("phone_number"),
                "web_client": (user or {}).get("web_client"),
                "is_blocked": (user or {}).get("is_blocked"),
                "created_at": (user or {}).get("created_at"),
            },
            "raw": {
                "subscription": self._jsonable(raw_sub),
                "subscription_bson_types": bson_types,
                "legacy_user_subscription": (user or {}).get("subscription"),
                "daily_lots": [self._jsonable(lot) for lot in lots],
            },
            "computed": computed,
            "credit_usage": usage,
            "reconciliation": reconciliation,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def grant(self, user_id: str, request, *, operator: str) -> dict:
        now_ms = self._now_ms()

        try:
            quote = self.transaction_service._get_subscription_quote(
                request.tier, request.period
            )
        except (ValueError, KeyError):
            raise AdminActionError(
                ErrorCode.INVALID_SUBSCRIPTION_PLAN,
                tier=request.tier,
                period=request.period,
            ) from None

        if request.credits_override is not None:
            quote = {**quote, "total_credits": int(request.credits_override)}

        overridden_code: str | None = None
        try:
            await self.transaction_service.validate_subscription_eligibility(
                user_id=user_id, quote=quote, now_ms=now_ms
            )
        except SubscriptionEligibilityError as exc:
            if not request.override_eligibility:
                raise
            overridden_code = getattr(exc, "code", None)
            logger.info(
                f"[AdminSubscription] {operator} overrode {overridden_code} "
                f"granting {request.tier}/{request.period} to {user_id}"
            )

        order_id = f"admin-{request.external_reference or ''}-{now_ms}".replace("--", "-")

        if is_daily_pass_quote(quote):
            await self.subscription_storage.upsert_subscription(
                user_id=user_id,
                quote=quote,
                order_id=order_id,
                transaction_id=None,
                now_ms=now_ms,
                provider=ADMIN_PROVIDER,
            )
            return {"overridden_eligibility_code": overridden_code}

        # Pool tier. `upsert_subscription` stacks behind an active window and writes
        # total_credits from the new purchase alone, so decide the intended balance
        # here and let reconciliation put it on the document.
        purchased = int(quote.get("total_credits") or 0)
        if request.start_mode == "now":
            existing = await self.subscription_storage.get_raw_subscription(user_id)
            if isinstance(existing, dict):
                await self.subscription_storage.admin_set_pool_fields(
                    user_id, updates={"end_ms": now_ms - 1}, now_ms=now_ms
                )
            desired_remaining = purchased
        else:
            desired_remaining = await self._computed_remaining(user_id) + purchased

        await self.subscription_storage.upsert_subscription(
            user_id=user_id,
            quote=quote,
            order_id=order_id,
            transaction_id=None,
            now_ms=now_ms,
            provider=ADMIN_PROVIDER,
        )
        await self.reconcile_pool_entitlement(
            user_id, desired_remaining, now_ms=now_ms
        )
        await self._verify_effective_balance(
            user_id, desired_remaining, now_ms=now_ms
        )
        return {"overridden_eligibility_code": overridden_code}

    async def extend(self, user_id: str, request, *, operator: str) -> dict:
        now_ms = self._now_ms()

        if request.target == "daily_lot":
            lot_id = self._lot_object_id(request.daily_lot_id)
            lots = await self.subscription_storage.get_all_daily_lots(user_id)
            lot = next((item for item in lots if str(item.get("_id")) == str(request.daily_lot_id)), None)
            if lot is None:
                raise AdminActionError(
                    ErrorCode.DAILY_LOT_NOT_FOUND, daily_lot_id=request.daily_lot_id
                )
            new_end = (
                int(request.new_end_ms)
                if request.new_end_ms is not None
                else max(now_ms, self._as_int(lot.get("end_ms")))
                + request.extend_days * 86_400_000
            )
            updated = await self.subscription_storage.admin_set_daily_lot_fields(
                lot_id, user_id, updates={"end_ms": new_end}, now_ms=now_ms
            )
            if updated is None:
                raise AdminActionError(
                    ErrorCode.DAILY_LOT_NOT_FOUND, daily_lot_id=request.daily_lot_id
                )
            return {"new_end_ms": new_end}

        sub = await self.subscription_storage.get_raw_subscription(user_id)
        if not isinstance(sub, dict):
            raise AdminActionError(ErrorCode.SUBSCRIPTION_NOT_FOUND, user_id=user_id)

        remaining_before = await self._computed_remaining(user_id)
        current_end = self._as_int(sub.get("end_ms"))
        new_end = (
            int(request.new_end_ms)
            if request.new_end_ms is not None
            else max(now_ms, current_end) + request.extend_days * 86_400_000
        )

        updated = await self.subscription_storage.admin_set_pool_fields(
            user_id,
            updates={"end_ms": new_end},
            now_ms=now_ms,
            expected_updated_at_ms=sub.get("updated_at_ms"),
        )
        if updated is None:
            raise AdminActionError(
                ErrorCode.ADMIN_SUBSCRIPTION_CONFLICT, user_id=user_id
            )

        if request.preserve_remaining:
            await self.reconcile_pool_entitlement(
                user_id, remaining_before, now_ms=now_ms
            )
            await self._verify_effective_balance(
                user_id, remaining_before, now_ms=now_ms
            )
        return {"new_end_ms": new_end}

    async def adjust_credits(self, user_id: str, request, *, operator: str) -> dict:
        now_ms = self._now_ms()

        if request.target == "daily_lot":
            lot_id = self._lot_object_id(request.daily_lot_id)
            lots = await self.subscription_storage.get_all_daily_lots(user_id)
            lot = next((item for item in lots if str(item.get("_id")) == str(request.daily_lot_id)), None)
            if lot is None:
                raise AdminActionError(
                    ErrorCode.DAILY_LOT_NOT_FOUND, daily_lot_id=request.daily_lot_id
                )
            current = self._as_int(
                lot.get("credits_remaining")
                if "credits_remaining" in lot
                else lot.get("daily_credits")
            )
            desired = (
                request.credits
                if request.mode == "set"
                else max(0, current + request.credits)
            )
            # Daily lots carry no creditusage coupling: the stored balance IS the
            # balance (see SubscriptionStorage._daily_lot_remaining).
            await self.subscription_storage.admin_set_daily_lot_fields(
                lot_id, user_id, updates={"credits_remaining": desired}, now_ms=now_ms
            )
            return {"desired_remaining": desired}

        sub = await self.subscription_storage.get_raw_subscription(user_id)
        if not isinstance(sub, dict):
            raise AdminActionError(ErrorCode.SUBSCRIPTION_NOT_FOUND, user_id=user_id)
        if self._as_int(sub.get("end_ms")) <= now_ms:
            raise AdminActionError(
                ErrorCode.SUBSCRIPTION_NOT_ACTIVE,
                user_id=user_id,
                end_ms=self._as_int(sub.get("end_ms")),
            )

        current = await self._computed_remaining(user_id)
        desired = (
            request.credits
            if request.mode == "set"
            else max(0, current + request.credits)
        )

        await self.reconcile_pool_entitlement(
            user_id,
            desired,
            now_ms=now_ms,
            expected_updated_at_ms=sub.get("updated_at_ms"),
        )
        await self._verify_effective_balance(user_id, desired, now_ms=now_ms)
        return {"previous_remaining": current, "desired_remaining": desired}

    async def revoke(self, user_id: str, request, *, operator: str) -> dict:
        now_ms = self._now_ms()
        result: dict[str, Any] = {}

        if request.target in ("pool", "all"):
            sub = await self.subscription_storage.get_raw_subscription(user_id)
            if isinstance(sub, dict):
                updates: dict[str, Any] = {
                    "end_ms": now_ms - 1,
                    "revoked_at_ms": now_ms,
                    "revoked_by": operator,
                    "revoke_reason": request.reason,
                }
                if request.zero_credits:
                    updates["credits_remaining"] = 0
                    updates["total_credits"] = 0
                await self.subscription_storage.admin_set_pool_fields(
                    user_id, updates=updates, now_ms=now_ms
                )
                result["pool_revoked"] = True
            elif request.target == "pool":
                raise AdminActionError(
                    ErrorCode.SUBSCRIPTION_NOT_FOUND, user_id=user_id
                )

        if request.target in ("daily_lot", "all"):
            lot_ids = (
                [self._lot_object_id(request.daily_lot_id)]
                if request.target == "daily_lot"
                else None
            )
            revoked = await self.subscription_storage.admin_revoke_daily_lots(
                user_id,
                now_ms=now_ms,
                lot_ids=lot_ids,
                zero_credits=request.zero_credits,
                operator=operator,
                reason=request.reason,
            )
            result["daily_lots_revoked"] = revoked
            if request.target == "daily_lot" and revoked == 0:
                raise AdminActionError(
                    ErrorCode.DAILY_LOT_NOT_FOUND, daily_lot_id=request.daily_lot_id
                )

        return result

    # ------------------------------------------------------------------
    # Audited execution wrapper
    # ------------------------------------------------------------------

    ACTIONS = {
        "grant": ACTION_GRANT,
        "extend": ACTION_EXTEND,
        "adjust_credits": ACTION_ADJUST_CREDITS,
        "revoke": ACTION_REVOKE,
    }

    async def execute(
        self,
        action: str,
        user_id: str,
        request,
        *,
        operator: str,
        request_id: str,
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Claim the request id, snapshot, mutate, reconcile, then finalize the audit.

        The claim happens before any write, so a replayed request id is rejected
        rather than applied twice. A failed attempt is still finalized — an audit
        gap on a money-equivalent action is worse than a burnt request id.
        """
        handler = getattr(self, action)
        audit_action = self.ACTIONS[action]

        event_id = await self.audit_service.claim(
            request_id=request_id,
            action=audit_action,
            target_user_id=user_id,
            operator=operator,
            reason=request.reason,
            params=request.model_dump(mode="json"),
            source_ip=source_ip,
            user_agent=user_agent,
        )

        before = await self.snapshot(user_id)
        try:
            detail = await handler(user_id, request, operator=operator)
        except AdminActionError as exc:
            await self.audit_service.finalize(
                event_id,
                result="error",
                before=before,
                error_code=getattr(exc.code, "value", str(exc.code)),
            )
            raise
        except SubscriptionEligibilityError as exc:
            await self.audit_service.finalize(
                event_id, result="error", before=before, error_code=exc.code
            )
            raise
        except Exception as exc:
            await self.audit_service.finalize(
                event_id, result="error", before=before, error_code="INTERNAL_ERROR"
            )
            logger.exception(f"[AdminSubscription] {action} failed for {user_id}: {exc}")
            raise

        after = await self.snapshot(user_id)
        await self.audit_service.finalize(
            event_id, result=RESULT_SUCCESS, before=before, after=after
        )

        diagnostic = await self.diagnostic(user_id)
        return {
            "action": audit_action,
            "user_id": user_id,
            "audit_id": event_id,
            "request_id": request_id,
            "operator": operator,
            "applied_at_ms": self._now_ms(),
            "before": before,
            "after": after,
            "delta": {
                "credits_remaining": (after.get("computed") or {}).get(
                    "credits_remaining"
                ),
                "end_ms": (after.get("computed") or {}).get("end_ms"),
                **(detail or {}),
            },
            "warnings": diagnostic["warnings"],
        }
