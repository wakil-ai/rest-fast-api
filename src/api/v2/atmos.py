"""ATMOS payment gateway API endpoints (bind -> charge -> renew).

See docs/b2c/specs/atmos-payment-integration.md for the design and the ATMOS
doc anchors behind every field/endpoint used here.
"""

import ipaddress

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from core.config import settings
from core.error_codes import ErrorCode
from core.exceptions import ChatException
from core.dependencies import get_atmos_service
from core.logger import logger
from models.payment import (
    AtmosBindCancelResponse,
    AtmosBindRequest,
    AtmosBindResponse,
    AtmosChargeRequest,
    AtmosChargeResponse,
    AtmosDiagnosticResponse,
    AtmosMandateResponse,
    AtmosRenewDueRequest,
    AtmosRenewDueResponse,
    AtmosRenewDueResult,
    AtmosServiceError,
    SubscriptionEligibilityError,
)
from security import verify_api_key, verify_super_admin_key, verify_user_or_service_auth
from services.payments.atmos import AtmosService

router = APIRouter(prefix="/transaction/atmos", tags=["Atmos"])
admin_router = APIRouter(
    prefix="/admin/subscriptions/atmos",
    tags=["Admin Subscriptions"],
    dependencies=[Depends(verify_super_admin_key)],
)


def _gateway_unavailable(*, outcome_unknown: bool) -> ChatException:
    return ChatException(
        detail="ATMOS is not responding. Please try again shortly.",
        status_code=502,
        code=ErrorCode.ATMOS_GATEWAY_UNAVAILABLE,
        params={"outcome_unknown": outcome_unknown},
    )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _ip_allowed(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        network = ipaddress.ip_network(settings.ATMOS_CALLBACK_IP_ALLOWLIST, strict=False)
        return ipaddress.ip_address(ip) in network
    except ValueError:
        logger.error(
            f"[Atmos] Could not parse allowlist/IP for callback check: "
            f"allowlist={settings.ATMOS_CALLBACK_IP_ALLOWLIST!r} ip={ip!r}"
        )
        return False


@router.post("/bind", response_model=AtmosBindResponse)
async def start_atmos_bind(
    request: AtmosBindRequest,
    _auth: bool = Depends(verify_user_or_service_auth),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """Starts a hosted card bind. Returns the checkout.atmos.uz/bind URL to
    redirect the user to — no PAN or OTP form is built on our side (Path A)."""
    try:
        result = await atmos_service.start_bind(
            user_id=request.user_id, success_url=request.success_url
        )
        return AtmosBindResponse(**result)
    except HTTPException:
        raise
    except (httpx.HTTPError, AtmosServiceError) as e:
        # Nothing was bound and nothing charged: safe to tell the user to retry.
        logger.warning(f"[Atmos] Bind start failed at ATMOS: {e!r}")
        raise _gateway_unavailable(outcome_unknown=False) from e
    except Exception as e:
        logger.exception(f"Error starting ATMOS bind: {e}")
        raise HTTPException(status_code=500, detail="Failed to start card binding")


@router.post("/bind/callback")
async def atmos_bind_callback(
    request: Request,
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """ATMOS's bind-completion callback: {"api_key": ..., "card_id": ...}.
    Authenticated by api_key (checked inside the service), not by JWT."""
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Body is not a JSON object")
    except Exception:
        return JSONResponse(status_code=200, content={"status": 0, "message": "Invalid JSON"})

    result = await atmos_service.handle_bind_callback(payload)
    return JSONResponse(status_code=200, content=result)


@router.post("/callback")
async def atmos_payment_callback(
    request: Request,
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """ATMOS's payment Callback API. IP-allowlisted before anything else — the
    one deliberate non-200 response in this route (see spec acceptance
    criteria): everything else always answers 200 with status 0/1, since ATMOS
    treats any non-200 as capture failure and retries."""
    client_ip = _client_ip(request)
    if not _ip_allowed(client_ip):
        logger.warning(f"[Atmos] Payment callback rejected from out-of-range IP {client_ip}")
        raise HTTPException(status_code=403, detail="IP not allowed")

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Body is not a JSON object")
    except Exception:
        return JSONResponse(status_code=200, content={"status": 0, "message": "Invalid JSON"})

    result = await atmos_service.handle_payment_callback(payload)
    return JSONResponse(status_code=200, content=result)


@router.post("/charge", response_model=AtmosChargeResponse)
async def charge_atmos_mandate(
    request: AtmosChargeRequest,
    _auth: bool = Depends(verify_user_or_service_auth),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """Charges the caller's bound card for a plan. Used for the first charge
    right after a successful bind; the renewal runner reuses the same
    AtmosService.charge_mandate() method directly rather than this HTTP route."""
    try:
        result = await atmos_service.charge_mandate(
            user_id=request.user_id,
            subscription_tier=request.subscription_tier,
            subscription_period=request.subscription_period,
            is_renewal=False,
        )
        return AtmosChargeResponse(**result)
    except SubscriptionEligibilityError as e:
        raise HTTPException(status_code=409, detail=e.to_detail())
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        # The request may have reached ATMOS: the debit outcome is unknown until
        # reconciliation, so the client must not present this as a failure.
        logger.error(f"[Atmos] Charge transport error for user={request.user_id}: {e!r}")
        raise _gateway_unavailable(outcome_unknown=True) from e
    except Exception as e:
        logger.exception(f"Error charging ATMOS mandate: {e}")
        raise HTTPException(status_code=500, detail="Failed to charge card")


@router.get("/cards/{user_id}", response_model=AtmosMandateResponse)
async def get_atmos_mandate(
    user_id: str,
    _auth: bool = Depends(verify_user_or_service_auth),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    mandate = await atmos_service.get_active_mandate(user_id)
    if not mandate:
        pending = await atmos_service.get_pending_bind(user_id)
        if pending:
            return AtmosMandateResponse(
                status="pending_bind",
                pending_expires_at_ms=pending.get("lock_expires_at_ms"),
            )
        return AtmosMandateResponse(status="none")
    return AtmosMandateResponse(
        status=mandate.get("status", "none"),
        bound_at_ms=mandate.get("bound_at_ms"),
        atmos_card_id=mandate.get("atmos_card_id"),
    )


@router.post("/cards/{user_id}/bind/cancel", response_model=AtmosBindCancelResponse)
async def cancel_atmos_bind(
    user_id: str,
    _auth: bool = Depends(verify_user_or_service_auth),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """Abandon this user's unfinished card bind and free the bind slot."""
    return AtmosBindCancelResponse(cancelled=await atmos_service.cancel_bind(user_id))


@router.post("/cards/{user_id}/unbind")
async def unbind_atmos_mandate(
    user_id: str,
    _auth: bool = Depends(verify_user_or_service_auth),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    revoked = await atmos_service.unbind(user_id)
    return {"revoked": revoked}


@router.post("/renew-due", response_model=AtmosRenewDueResponse)
async def run_atmos_renewals(
    request: AtmosRenewDueRequest,
    _auth: bool = Depends(verify_api_key),
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """Charges every mandate due for renewal, or one named user. The external
    scheduler calls this with no user_id; the admin dashboard's manual "run
    renewal now" action calls it with one — same code path either way."""
    results = await atmos_service.renew_due(user_id=request.user_id)
    return AtmosRenewDueResponse(results=[AtmosRenewDueResult(**r) for r in results])


# MARK: Admin
@admin_router.get("/{user_id}", response_model=AtmosDiagnosticResponse)
async def get_atmos_diagnostic(
    user_id: str,
    atmos_service: AtmosService = Depends(get_atmos_service),
):
    """Mandate status + recent charge/renewal attempts for the admin panel.
    Read-only, no side effects — mirrors admin_subscription.py's diagnostic
    convention."""
    data = await atmos_service.get_diagnostic(user_id)
    return AtmosDiagnosticResponse(**data)
