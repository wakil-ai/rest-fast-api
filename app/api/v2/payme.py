"""Payme payment API endpoints"""

import base64
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.dependencies import get_transaction_service
from app.core.logger import logger
from app.security import verify_api_key
from app.models.payme import (
    PaymeError,
    PaymeMethod,
    PaymeInitRequest,
    PaymeInitResponse,
    PaymentLinkRequest,
    PaymentLinkResponse,
    TransactionError,
)

router = APIRouter(prefix="/transaction", tags=["Payme"])
transaction_service = get_transaction_service()


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
    try:
        body = await request.json()
        method = body.get("method")
        params = body.get("params")
        request_id = body.get("id")

        logger.info(
            f"Received Payme request: method={method}, id={request_id}, params={params}"
        )

        # Verify authorization header
        authorization = request.headers.get("Authorization")
        if not verify_payme_authorization(authorization):
            error = PaymeError.InvalidAuthorization
            return JSONResponse(
                status_code=200,
                content={
                    "error": {"code": error["code"], "message": error["message"]},
                    "id": request_id,
                },
            )

        if method == PaymeMethod.CheckPerformTransaction:
            await transaction_service.check_perform_transaction(params, request_id)
            return {"result": {"allow": True}, "id": request_id}

        if method == PaymeMethod.CheckTransaction:
            result = await transaction_service.check_transaction(params, request_id)
            return {"result": result, "id": request_id}

        if method == PaymeMethod.CreateTransaction:
            result = await transaction_service.create_transaction(params, request_id)
            return {"result": result, "id": request_id}

        if method == PaymeMethod.PerformTransaction:
            result = await transaction_service.perform_transaction(params, request_id)
            return {"result": result, "id": request_id}

        if method == PaymeMethod.CancelTransaction:
            result = await transaction_service.cancel_transaction(params, request_id)
            return {"result": result, "id": request_id}

        if method == PaymeMethod.GetStatement:
            result = await transaction_service.get_statement(params)
            return {"result": {"transactions": result}, "id": request_id}

        if method == PaymeMethod.SetFiscalData:
            result = await transaction_service.set_fiscal_data(params, request_id)
            return {"result": result, "id": request_id}

        return JSONResponse(
            status_code=200,
            content={
                "error": {"code": -32601, "message": "Method not found"},
                "id": request_id,
            },
        )

    except TransactionError as err:
        return JSONResponse(
            status_code=200,  # Payme requires 200 even for errors
            content={"error": err.to_payme_response(), "id": err.request_id},
        )

    except Exception:
        return JSONResponse(
            status_code=200,
            content={
                "error": {"code": -32603, "message": "Internal error"},
                "id": request_id,
            },
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
