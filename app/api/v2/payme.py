"""Payme payment API endpoints"""

import base64
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.dependencies import get_mongo_handler, get_transaction_service
from app.core.logger import logger
from app.models.payme import (
    PaymeError,
    PaymeInitRequest,
    PaymeInitResponse,
    PaymeMethod,
    PaymentLinkRequest,
    PaymentLinkResponse,
    SubscriptionCatalogResponse,
    SubscriptionPlan,
    TransactionError,
    UserSubscriptionResponse,
)
from app.security import verify_api_key

router = APIRouter(prefix="/transaction", tags=["Payme"])
transaction_service = get_transaction_service()


async def _maybe_log_payme_rpc(
    *,
    request_body: dict,
    response_body: dict,
    auth_ok: bool,
    client_ip: str | None,
) -> None:
    if not getattr(settings, "PAYME_DEBUG_LOGS", False):
        return

    try:
        mongo = get_mongo_handler()
        await mongo.insert_one(
            settings.PAYME_RPC_LOGS_COLLECTION,
            {
                "ts_ms": int(__import__("time").time() * 1000),
                "client_ip": client_ip,
                "auth_ok": auth_ok,
                "method": request_body.get("method"),
                "id": request_body.get("id"),
                "request": request_body,
                "response": response_body,
            },
        )
    except Exception:
        return


def _rpc_success(request_id: int | str | None, result: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
        "error": None,
    }


def _rpc_error(request_id: int | str | None, error: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": None,
        "error": error,
    }


def verify_payme_authorization(authorization: str | None) -> bool:
    """Verify Payme authorization header"""
    if not authorization:
        return False

    try:
        # Extract credentials from "Basic base64string"
        auth_type, credentials = authorization.split(" ", 1)
        if auth_type.lower() != "basic":
            return False

        # Decode base64 credentials
        decoded = base64.b64decode(credentials).decode("utf-8")
        # Should be in format "Paycom:merchant_key"
        username, password = decoded.split(":", 1)

        if username != "Paycom":
            return False

        # Compare with configured merchant key
        if not secrets.compare_digest(password, settings.PAYME_MERCHANT_KEY):
            return False

        return True

    except Exception:
        return False


@router.post("/payme/")
async def payme(request: Request):
    request_id = None
    body: dict = {}
    try:
        body = await request.json()
        method = body.get("method")
        params = body.get("params")
        request_id = body.get("id")
        client_ip = request.client.host if request.client else None

        logger.info(
            f"Received Payme request: method={method}, id={request_id}, params={params}"
        )

        # Verify authorization header
        authorization = request.headers.get("Authorization")
        auth_ok = verify_payme_authorization(authorization)
        if not auth_ok:
            error = PaymeError.InvalidAuthorization
            resp = _rpc_error(
                request_id,
                {"code": error["code"], "message": error["message"]},
            )
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=False,
                client_ip=client_ip,
            )
            return JSONResponse(status_code=200, content=resp)

        if method == PaymeMethod.CheckPerformTransaction:
            result = await transaction_service.check_perform_transaction(
                params, request_id
            )
            resp = _rpc_success(request_id, result or {"allow": True})
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.CheckTransaction:
            result = await transaction_service.check_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.CreateTransaction:
            result = await transaction_service.create_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.PerformTransaction:
            result = await transaction_service.perform_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.CancelTransaction:
            result = await transaction_service.cancel_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.GetStatement:
            result = await transaction_service.get_statement(params)
            resp = _rpc_success(request_id, {"transactions": result})
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        if method == PaymeMethod.SetFiscalData:
            result = await transaction_service.set_fiscal_data(params, request_id)
            resp = _rpc_success(request_id, result)
            await _maybe_log_payme_rpc(
                request_body=body,
                response_body=resp,
                auth_ok=True,
                client_ip=client_ip,
            )
            return resp

        resp = _rpc_error(request_id, {"code": -32601, "message": "Method not found"})
        await _maybe_log_payme_rpc(
            request_body=body,
            response_body=resp,
            auth_ok=True,
            client_ip=client_ip,
        )
        return JSONResponse(status_code=200, content=resp)

    except TransactionError as err:
        client_ip = request.client.host if request.client else None
        resp = _rpc_error(err.request_id, err.to_payme_response())
        await _maybe_log_payme_rpc(
            request_body=body,
            response_body=resp,
            auth_ok=True,
            client_ip=client_ip,
        )
        return JSONResponse(
            status_code=200,  # Payme requires 200 even for errors
            content=resp,
        )

    except Exception:
        client_ip = request.client.host if request.client else None
        resp = _rpc_error(request_id, {"code": -32603, "message": "Internal error"})
        await _maybe_log_payme_rpc(
            request_body=body,
            response_body=resp,
            auth_ok=True,
            client_ip=client_ip,
        )
        return JSONResponse(
            status_code=200,
            content=resp,
        )


@router.post("/payme/callback", response_model=PaymentLinkResponse)
async def create_payment_link(
    request: PaymentLinkRequest,
    _auth: bool = Depends(verify_api_key),
):
    """
    Create a Payme payment link

    This endpoint generates a payment URL that can be sent to users.
    Requires API key authentication via x-api-key header.
    """
    try:
        payment_link = await transaction_service.create_payment_link(
            amount=request.amount,
            user_id=request.user_id,
            callback_url=request.callback_url,
            order_id=request.order_id,
        )

        logger.info(f"Created payment link for user {request.user_id}: {payment_link}")

        return PaymentLinkResponse(link=payment_link)
    except Exception as e:
        logger.exception(f"Error creating payment link: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to create payment link: {str(e)}"
        )


@router.post("/payme/init", response_model=PaymeInitResponse)
async def init_payme_payment(
    request: PaymeInitRequest,
    _auth: bool = Depends(verify_api_key),
):
    """Create a local invoice and return a Payme checkout link.

    This is the single endpoint our client apps should call to start a payment.
    Payme will later call /transaction/payme/ (Merchant API) to create/perform the transaction.
    """

    try:
        result = await transaction_service.init_payment(
            amount_sum=request.amount,
            user_id=request.user_id,
            callback_url=request.callback_url,
            order_id=request.order_id,
            subscription_tier=request.subscription_tier,
            subscription_period=request.subscription_period,
        )

        logger.info(
            f"Initialized Payme payment for user {request.user_id}, order_id={result['order_id']}"
        )

        return PaymeInitResponse(order_id=result["order_id"], link=result["link"])
    except ValueError as e:
        # Client-side error (invalid amount, invalid subscription, etc.)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error initializing Payme payment: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to init Payme payment: {str(e)}"
        )


@router.get("/payme/subscriptions/catalog", response_model=SubscriptionCatalogResponse)
async def get_subscription_catalog(_auth: bool = Depends(verify_api_key)):
    plans = transaction_service.get_subscription_catalog()
    return SubscriptionCatalogResponse(plans=[SubscriptionPlan(**p) for p in plans])


@router.get(
    "/payme/subscriptions/{user_id}",
    response_model=UserSubscriptionResponse,
)
async def get_user_subscription(user_id: str, _auth: bool = Depends(verify_api_key)):
    data = await transaction_service.get_user_subscription(user_id)
    return UserSubscriptionResponse(**data)
