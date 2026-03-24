"""Payme payment API endpoints"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.dependencies import get_transaction_service, get_click_service
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
from app.models.click import ClickInitRequest, ClickInitResponse
from app.services import ClickService
from app.services import TransactionService
from app.security import verify_api_key, verify_payme_authorization

router = APIRouter(prefix="/transaction", tags=["Payme"])

# MARK: Paycom
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

@router.post("/payme/")
async def payme(
    request: Request,
    transaction_service: TransactionService = Depends(get_transaction_service),
):
    request_id = None
    body: dict = {}
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
        auth_ok = verify_payme_authorization(authorization)
        if not auth_ok:
            error = PaymeError.InvalidAuthorization
            resp = _rpc_error(
                request_id,
                {"code": error["code"], "message": error["message"]},
            )
            return JSONResponse(status_code=200, content=resp)

        if method == PaymeMethod.CheckPerformTransaction:
            result = await transaction_service.check_perform_transaction(
                params, request_id
            )
            resp = _rpc_success(request_id, result or {"allow": True})
            return resp

        if method == PaymeMethod.CheckTransaction:
            result = await transaction_service.check_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            return resp

        if method == PaymeMethod.CreateTransaction:
            result = await transaction_service.create_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            return resp

        if method == PaymeMethod.PerformTransaction:
            result = await transaction_service.perform_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            return resp

        if method == PaymeMethod.CancelTransaction:
            result = await transaction_service.cancel_transaction(params, request_id)
            resp = _rpc_success(request_id, result)
            return resp

        if method == PaymeMethod.GetStatement:
            result = await transaction_service.get_statement(params)
            resp = _rpc_success(request_id, {"transactions": result})
            return resp

        if method == PaymeMethod.SetFiscalData:
            result = await transaction_service.set_fiscal_data(params, request_id)
            resp = _rpc_success(request_id, result)
            return resp

        resp = _rpc_error(request_id, {"code": -32601, "message": "Method not found"})
        return JSONResponse(status_code=200, content=resp)

    except TransactionError as err:
        resp = _rpc_error(err.request_id, err.to_payme_response())
        return JSONResponse(
            status_code=200,  # Payme requires 200 even for errors
            content=resp,
        )

    except Exception:
        resp = _rpc_error(request_id, {"code": -32603, "message": "Internal error"})
        return JSONResponse(
            status_code=200,
            content=resp,
        )


@router.post("/payme/callback", response_model=PaymentLinkResponse)
async def create_payment_link(
    request: PaymentLinkRequest,
    _auth: bool = Depends(verify_api_key),
    transaction_service: TransactionService = Depends(get_transaction_service),
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
    transaction_service: TransactionService = Depends(get_transaction_service),
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

# MARK: Click
async def _parse_click_payload(request: Request) -> dict:
    # Click usually sends application/x-www-form-urlencoded
    try:
        form = await request.form()
        if form:
            return dict(form)
    except Exception:
        pass

    try:
        body = await request.json()
        if isinstance(body, dict):
            return body
    except Exception:
        pass

    return {}

@router.post("/click/init", response_model=ClickInitResponse)
async def init_click_payment(
    request: ClickInitRequest,
    _auth: bool = Depends(verify_api_key),
    click_service: ClickService = Depends(get_click_service),
):
    try:
        result = await click_service.init_payment(
            amount_sum=request.amount,
            user_id=request.user_id,
            callback_url=request.callback_url,
            order_id=request.order_id,
            subscription_tier=request.subscription_tier,
            subscription_period=request.subscription_period,
        )
        return ClickInitResponse(order_id=result["order_id"], link=result["link"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error initializing Click payment: {e}")
        raise HTTPException(status_code=500, detail="Failed to init Click payment")


@router.post("/click/prepare")
async def click_prepare(
    request: Request,
    click_service: ClickService = Depends(get_click_service),
):
    payload = await _parse_click_payload(request)
    result = await click_service.prepare(payload)
    return JSONResponse(status_code=200, content=result)


@router.post("/click/complete")
async def click_complete(
    request: Request,
    click_service: ClickService = Depends(get_click_service),
):
    payload = await _parse_click_payload(request)
    result = await click_service.complete(payload)
    return JSONResponse(status_code=200, content=result)


# MARK: Subscriptions
@router.get("/payme/subscriptions/catalog", response_model=SubscriptionCatalogResponse)
async def get_subscription_catalog(
    _auth: bool = Depends(verify_api_key),
    transaction_service: TransactionService = Depends(get_transaction_service),
):
    plans = transaction_service.get_subscription_catalog()
    return SubscriptionCatalogResponse(plans=[SubscriptionPlan(**p) for p in plans])


@router.get(
    "/payme/subscriptions/{user_id}",
    response_model=UserSubscriptionResponse,
)
async def get_user_subscription(
    user_id: str,
    _auth: bool = Depends(verify_api_key),
    transaction_service: TransactionService = Depends(get_transaction_service),
):
    data = await transaction_service.get_user_subscription(user_id)
    return UserSubscriptionResponse(**data)
