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
from app.services.payment_service import PaymentService
from app.models.payment import OrderCreate, OrderResponse

router = APIRouter(tags=["Paycom"])

# Initialize payment service
payment_service = PaymentService()


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


@router.post("/paycom/create-order", response_model=OrderResponse)
async def create_order(order: OrderCreate):
    """
    Create a new order for credit purchase
    
    Args:
        order: Order details including user_id, amount, and credit_amount
        
    Returns:
        OrderResponse: Created order details
    """
    try:
        order_id = payment_service.create_order(
            user_id=order.user_id,
            amount=order.amount,
            credit_amount=order.credit_amount,
            description=order.description
        )
        
        if not order_id:
            raise HTTPException(
                status_code=500,
                detail="Failed to create order"
            )
        
        # Get the created order
        order_doc = payment_service.get_order(order_id)
        
        if not order_doc:
            raise HTTPException(
                status_code=500,
                detail="Order created but could not be retrieved"
            )
        
        return OrderResponse(
            order_id=order_doc['order_id'],
            user_id=order_doc['user_id'],
            amount=order_doc['amount'],
            credit_amount=order_doc['credit_amount'],
            status=order_doc['status'],
            description=order_doc.get('description'),
            created_at=order_doc['created_at']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create order: {str(e)}"
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
    
    order_id = account.get('order_id')
    
    if not order_id:
        raise PaycomException(
            request_id,
            'Invalid account.',
            PaycomException.ERROR_INVALID_ACCOUNT
        )
    
    logger.info(f"CheckPerformTransaction: order_id={order_id}, amount={amount}")
    
    # Validate order exists and amount is correct
    is_valid, error_message = payment_service.validate_order(str(order_id), amount)
    
    if not is_valid:
        logger.warning(f"Order validation failed: {error_message}")
        raise PaycomException(
            request_id,
            error_message or 'Invalid account.',
            PaycomException.ERROR_INVALID_ACCOUNT
        )
    
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
    
    # Validate order before creating transaction
    is_valid, error_message = payment_service.validate_order(str(order_id), amount)
    
    if not is_valid:
        logger.warning(f"Order validation failed: {error_message}")
        raise PaycomException(
            request_id,
            error_message or 'Invalid account.',
            PaycomException.ERROR_INVALID_ACCOUNT
        )
    
    # Check if transaction already exists
    existing_transaction = payment_service.get_transaction(transaction_id)
    if existing_transaction:
        # Transaction already exists, return existing details
        logger.info(f"Transaction {transaction_id} already exists, returning existing details")
        return {
            'create_time': existing_transaction.get('create_time'),
            'transaction': existing_transaction.get('order_id'),
            'state': existing_transaction.get('state'),
            'receivers': None
        }
    
    # Create transaction in database
    success = payment_service.create_transaction(transaction_id, str(order_id), amount, time_ms)
    
    if not success:
        raise PaycomException(
            request_id,
            'Could not create transaction.',
            PaycomException.ERROR_COULD_NOT_PERFORM
        )
    
    current_time_ms = int(time.time() * 1000)
    
    return {
        'create_time': current_time_ms,
        'transaction': str(order_id),
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
    
    # Get transaction from database
    transaction = payment_service.get_transaction(transaction_id)
    
    if not transaction:
        raise PaycomException(
            request_id,
            'Transaction not found.',
            PaycomException.ERROR_TRANSACTION_NOT_FOUND
        )
    
    # Perform the transaction
    current_time_ms = int(time.time() * 1000)
    success = payment_service.perform_transaction(transaction_id, current_time_ms)
    
    if not success:
        raise PaycomException(
            request_id,
            'Could not perform transaction.',
            PaycomException.ERROR_COULD_NOT_PERFORM
        )
    
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
    
    # Get transaction from database
    transaction = payment_service.get_transaction(transaction_id)
    
    if not transaction:
        raise PaycomException(
            request_id,
            'Transaction not found.',
            PaycomException.ERROR_TRANSACTION_NOT_FOUND
        )
    
    # Cancel the transaction
    current_time_ms = int(time.time() * 1000)
    success = payment_service.cancel_transaction(transaction_id, current_time_ms, reason)
    
    if not success:
        raise PaycomException(
            request_id,
            'Could not cancel transaction.',
            PaycomException.ERROR_COULD_NOT_PERFORM
        )
    
    # Get updated transaction to determine state
    updated_transaction = payment_service.get_transaction(transaction_id)
    state = updated_transaction.get('state', -1)
    
    return {
        'transaction': transaction_id,
        'cancel_time': current_time_ms,
        'state': state
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
    
    # Get transaction from database
    transaction = payment_service.get_transaction(transaction_id)
    
    if not transaction:
        raise PaycomException(
            request_id,
            'Transaction not found.',
            PaycomException.ERROR_TRANSACTION_NOT_FOUND
        )
    
    return {
        'create_time': transaction.get('create_time'),
        'perform_time': transaction.get('perform_time'),
        'cancel_time': transaction.get('cancel_time'),
        'transaction': transaction.get('order_id'),
        'state': transaction.get('state'),
        'reason': transaction.get('reason')
    }


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
    
    # Get transactions from database
    transactions = payment_service.get_transactions_by_time_range(from_time, to_time)
    
    # Format transactions for response
    formatted_transactions = []
    for transaction in transactions:
        formatted_transactions.append({
            'id': transaction.get('transaction_id'),
            'time': transaction.get('create_time'),
            'amount': transaction.get('amount'),
            'account': {
                'order_id': transaction.get('order_id')
            },
            'create_time': transaction.get('create_time'),
            'perform_time': transaction.get('perform_time'),
            'cancel_time': transaction.get('cancel_time'),
            'transaction': transaction.get('order_id'),
            'state': transaction.get('state'),
            'reason': transaction.get('reason')
        })
    
    return {
        'transactions': formatted_transactions
    }
