"""Click payment API endpoints (SHOP-API prepare/complete + init)"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.dependencies import get_click_service
from app.core.logger import logger
from app.models.click import ClickInitRequest, ClickInitResponse
from app.security import verify_api_key

router = APIRouter(prefix="/transaction", tags=["Click"])
click_service = get_click_service()


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
async def click_prepare(request: Request):
    payload = await _parse_click_payload(request)
    result = await click_service.prepare(payload)
    return JSONResponse(status_code=200, content=result)


@router.post("/click/complete")
async def click_complete(request: Request):
    payload = await _parse_click_payload(request)
    result = await click_service.complete(payload)
    return JSONResponse(status_code=200, content=result)
