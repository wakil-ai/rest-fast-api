# app/api/paycom.py
"""
Paycom Merchant API Implementation

This module implements the Paycom Merchant API endpoints for payment processing.
It handles JSON-RPC 2.0 requests for transaction management.
"""

from fastapi import APIRouter, Request, HTTPException, Header, status
from typing import Optional, Dict, Any
import base64
import time
from datetime import datetime

from app.core.logger import logger
from app.core.config import settings

router = APIRouter(tags=["Paycom"])


class PaycomException(Exception):
    """Paycom exception for error handling"""
    
    ERROR_INVALID_JSON_RPC_OBJECT = -32700
    ERROR_METHOD_NOT_FOUND = -32601
    ERROR_INVALID_ACCOUNT = -31050
    ERROR_COULD_NOT_PERFORM = -31008
    ERROR_TRANSACTION_NOT_FOUND = -31003
    ERROR_INSUFFICIENT_PRIVILEGE = -32504
    
    def __init__(self, request_id: int, message: str, code: int, data: Optional[str] = None):
        self.request_id = request_id
        self.message = message
        self.code = code
        self.data = data
        super().__init__(self.message)


def authorize_request(authorization: Optional[str], merchant_key: str) -> bool:
    """
    Verify Basic Authentication header
    
    Args:
        authorization: Authorization header value
        merchant_key: Expected merchant key
        
    Returns:
        bool: True if authorized
        
    Raises:
        PaycomException: If authorization fails
    """
    if not authorization:
        raise PaycomException(
            None,
            'Insufficient privilege to perform this method.',
            PaycomException.ERROR_INSUFFICIENT_PRIVILEGE
        )
    
    # Parse Basic Auth header
    try:
        auth_type, auth_string = authorization.split(' ', 1)
        if auth_type.lower() != 'basic':
            raise ValueError('Not Basic auth')
            
        decoded = base64.b64decode(auth_string).decode('utf-8')
        login, key = decoded.split(':', 1)
        
        if login != 'Paycom' or key != merchant_key:
            raise ValueError('Invalid credentials')
            
        return True
        
    except Exception:
        raise PaycomException(
            None,
            'Insufficient privilege to perform this method.',
            PaycomException.ERROR_INSUFFICIENT_PRIVILEGE
        )


@router.post("/paycom/merchant")
async def paycom_merchant_api(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    Paycom Merchant API endpoint
    
    Handles JSON-RPC 2.0 requests for payment processing:
    - CheckPerformTransaction
    - CreateTransaction
    - PerformTransaction
    - CancelTransaction
    - CheckTransaction
    - GetStatement
    """
    try:
        # Parse JSON-RPC request
        payload = await request.json()
        
        request_id = payload.get('id')
        method = payload.get('method')
        params = payload.get('params', {})
        
        logger.info(f"Paycom request: method={method}, params={params}")
        
        # Authorize request
        merchant_key = getattr(settings, 'PAYCOM_MERCHANT_KEY', 'test_key')
        authorize_request(authorization, merchant_key)
        
        # Route to appropriate handler
        if method == 'CheckPerformTransaction':
            result = await check_perform_transaction(request_id, params)
        elif method == 'CreateTransaction':
            result = await create_transaction(request_id, params)
        elif method == 'PerformTransaction':
            result = await perform_transaction(request_id, params)
        elif method == 'CancelTransaction':
            result = await cancel_transaction(request_id, params)
        elif method == 'CheckTransaction':
            result = await check_transaction(request_id, params)
        elif method == 'GetStatement':
            result = await get_statement(request_id, params)
        else:
            return {
                'jsonrpc': '2.0',
                'id': request_id,
                'error': {
                    'code': PaycomException.ERROR_METHOD_NOT_FOUND,
                    'message': 'Method not found.',
                    'data': method
                }
            }
        
        # Return successful response
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': result
        }
        
    except PaycomException as e:
        return {
            'jsonrpc': '2.0',
            'id': e.request_id,
            'error': {
                'code': e.code,
                'message': e.message,
                'data': e.data
            }
        }
    except Exception as e:
        logger.error(f"Paycom API error: {str(e)}")
        return {
            'jsonrpc': '2.0',
            'id': payload.get('id') if 'payload' in locals() else None,
            'error': {
                'code': PaycomException.ERROR_INVALID_JSON_RPC_OBJECT,
                'message': 'Invalid JSON-RPC object.',
                'data': str(e)
            }
        }


async def check_perform_transaction(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check if transaction can be performed
    
    Args:
        request_id: Request ID
        params: Request parameters containing account and amount
        
    Returns:
        Dict with allow status
    """
    account = params.get('account', {})
    amount = params.get('amount')
    
    # TODO: Validate order exists and amount is correct
    order_id = account.get('order_id')
    
    if not order_id:
        raise PaycomException(
            request_id,
            'Invalid account.',
            PaycomException.ERROR_INVALID_ACCOUNT
        )
    
    logger.info(f"CheckPerformTransaction: order_id={order_id}, amount={amount}")
    
    # TODO: Add your business logic here to validate the order
    # For now, just return allow=True
    
    return {'allow': True}


async def create_transaction(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new transaction
    
    Args:
        request_id: Request ID
        params: Request parameters
        
    Returns:
        Dict with transaction details
    """
    transaction_id = params.get('id')
    time_ms = params.get('time')
    amount = params.get('amount')
    account = params.get('account', {})
    
    order_id = account.get('order_id')
    
    if not order_id:
        raise PaycomException(
            request_id,
            'Invalid account.',
            PaycomException.ERROR_INVALID_ACCOUNT
        )
    
    logger.info(f"CreateTransaction: id={transaction_id}, order_id={order_id}, amount={amount}")
    
    # TODO: Store transaction in database
    # For now, return a mock response
    
    current_time_ms = int(time.time() * 1000)
    
    return {
        'create_time': current_time_ms,
        'transaction': str(order_id),  # Use order_id as transaction ID for now
        'state': 1,  # STATE_CREATED
        'receivers': None
    }


async def perform_transaction(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Perform (complete) a transaction
    
    Args:
        request_id: Request ID
        params: Request parameters
        
    Returns:
        Dict with transaction status
    """
    transaction_id = params.get('id')
    
    logger.info(f"PerformTransaction: id={transaction_id}")
    
    # TODO: Update transaction state to completed
    
    current_time_ms = int(time.time() * 1000)
    
    return {
        'transaction': transaction_id,
        'perform_time': current_time_ms,
        'state': 2  # STATE_COMPLETED
    }


async def cancel_transaction(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cancel a transaction
    
    Args:
        request_id: Request ID
        params: Request parameters
        
    Returns:
        Dict with cancellation status
    """
    transaction_id = params.get('id')
    reason = params.get('reason')
    
    logger.info(f"CancelTransaction: id={transaction_id}, reason={reason}")
    
    # TODO: Cancel transaction in database
    
    current_time_ms = int(time.time() * 1000)
    
    return {
        'transaction': transaction_id,
        'cancel_time': current_time_ms,
        'state': -1  # STATE_CANCELLED
    }


async def check_transaction(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check transaction status
    
    Args:
        request_id: Request ID
        params: Request parameters
        
    Returns:
        Dict with transaction details
    """
    transaction_id = params.get('id')
    
    logger.info(f"CheckTransaction: id={transaction_id}")
    
    # TODO: Query transaction from database
    # For now, return transaction not found
    
    raise PaycomException(
        request_id,
        'Transaction not found.',
        PaycomException.ERROR_TRANSACTION_NOT_FOUND
    )


async def get_statement(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get statement of transactions
    
    Args:
        request_id: Request ID
        params: Request parameters with from/to timestamps
        
    Returns:
        Dict with list of transactions
    """
    from_time = params.get('from')
    to_time = params.get('to')
    
    logger.info(f"GetStatement: from={from_time}, to={to_time}")
    
    # TODO: Query transactions from database
    
    return {
        'transactions': []
    }
