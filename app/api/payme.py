"""Payme payment API endpoints"""

import base64
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import secrets
from app.models.payme import (
    PaymeRequest,
    PaymeResponse,
    PaymeError as PaymeErrorModel,
    PaymeMethod,
    PaymePaymentLink,
    PaymePaymentLinkResponse
)
from app.services.payme_service import PaymeService, PaymeError
from app.core.config import settings
from app.core.logger import logger

router = APIRouter()
payme_service = PaymeService()


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify API key for payment link creation"""
    if x_api_key is None:
        raise HTTPException(
            status_code=401,
            detail="API Key required"
        )
    
    if not secrets.compare_digest(x_api_key, settings.API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )
    
    return True


def verify_payme_authorization(authorization: Optional[str], request_id: int) -> bool:
    """Verify Payme authorization header"""
    if not authorization:
        raise PaymeError(
            request_id=request_id,
            code=-32504,
            message="Insufficient privilege to perform this method"
        )
    
    try:
        # Extract credentials from "Basic base64string"
        auth_type, credentials = authorization.split(" ", 1)
        if auth_type.lower() != "basic":
            raise ValueError("Not Basic auth")
        
        # Decode base64 credentials
        decoded = base64.b64decode(credentials).decode("utf-8")
        # Should be in format "Paycom:merchant_key"
        username, password = decoded.split(":", 1)
        
        if username != "Paycom":
            raise ValueError("Invalid username")
        
        # Compare with configured merchant key
        if not secrets.compare_digest(password, settings.PAYME_MERCHANT_KEY):
            raise ValueError("Invalid merchant key")
        
        return True
        
    except Exception as e:
        logger.error(f"Authorization verification failed: {e}")
        raise PaymeError(
            request_id=request_id,
            code=-32504,
            message="Insufficient privilege to perform this method"
        )


@router.post("/payme", include_in_schema=True)
@router.post("/payme/", include_in_schema=False)
async def payme_merchant_api(
    request: Request,
    payme_request: PaymeRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Payme Merchant API endpoint
    
    This endpoint handles all Payme payment callbacks including:
    - CheckPerformTransaction
    - CreateTransaction
    - PerformTransaction
    - CancelTransaction
    - CheckTransaction
    - GetStatement
    """
    try:
        # Verify Payme authorization
        verify_payme_authorization(authorization, payme_request.id)
        
        logger.info(f"Payme {payme_request.method} - request_id={payme_request.id}")
        
        # Route to appropriate handler based on method
        if payme_request.method == PaymeMethod.CHECK_PERFORM_TRANSACTION:
            result = payme_service.check_perform_transaction(payme_request.params, payme_request.id)
            
        elif payme_request.method == PaymeMethod.CREATE_TRANSACTION:
            result = payme_service.create_transaction(payme_request.params, payme_request.id)
            
        elif payme_request.method == PaymeMethod.PERFORM_TRANSACTION:
            result = payme_service.perform_transaction(payme_request.params, payme_request.id)
            
        elif payme_request.method == PaymeMethod.CANCEL_TRANSACTION:
            result = payme_service.cancel_transaction(payme_request.params, payme_request.id)
            
        elif payme_request.method == PaymeMethod.CHECK_TRANSACTION:
            result = payme_service.check_transaction(payme_request.params, payme_request.id)
            
        elif payme_request.method == PaymeMethod.GET_STATEMENT:
            result = payme_service.get_statement(payme_request.params, payme_request.id)
            
        else:
            raise PaymeError(
                request_id=payme_request.id,
                code=-32601,
                message="Method not found"
            )
        
        # Return successful response
        if isinstance(result, dict):
            response_data = {"result": result, "id": payme_request.id}
        else:
            response_data = {"result": result.model_dump() if hasattr(result, 'model_dump') else result, "id": payme_request.id}
        
        logger.info(f"Payme response: {response_data}")
        return JSONResponse(content=response_data)
        
    except PaymeError as e:
        # Return Payme error format
        error_response = {
            "error": {
                "code": e.code,
                "message": e.message,
                "data": e.data
            },
            "id": e.request_id or payme_request.id
        }

        if e.code == -31003 and payme_request.method == "CheckTransaction":
            logger.info(f"Payme: CheckTransaction returned 'not found' (normal - transaction will be created next)")
        else:
            logger.error(f"Payme error: {error_response}")
        return JSONResponse(
            content=error_response,
            status_code=200  # Payme expects 200 even for errors
        )
        
    except Exception as e:
        # Unexpected error
        logger.exception(f"Unexpected error in Payme API: {e}")
        error_response = {
            "error": {
                "code": -32400,
                "message": {"uz": "Ichki xatolik", "ru": "Внутренняя ошибка", "en": "Internal error"},
                "data": str(e)
            },
            "id": payme_request.id
        }
        return JSONResponse(
            content=error_response,
            status_code=200
        )


@router.post("/payme/create-link", response_model=PaymePaymentLinkResponse)
async def create_payment_link(
    payment_data: PaymePaymentLink,
    _: bool = Depends(verify_api_key)
):
    """
    Create a Payme payment link
    
    This endpoint generates a payment URL that can be sent to users.
    Requires API key authentication via x-api-key header.
    """
    try:
        logger.info(f"Creating payment link for user_id={payment_data.user_id}, amount={payment_data.amount}")
        
        # Generate payment URL (returns string)
        payment_url = payme_service.generate_payment_link(payment_data)
        
        # Create response object
        response = PaymePaymentLinkResponse(
            payment_url=payment_url,
            order_id=payment_data.order_id
        )
        
        logger.info(f"Payment link created: {payment_url}")
        return response
        
    except Exception as e:
        logger.exception(f"Error creating payment link: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create payment link: {str(e)}"
        )
