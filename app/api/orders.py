# app/api/orders.py

from fastapi import APIRouter, HTTPException
from app.models.order import OrderCreate, OrderResponse
from app.services.order_service import OrderService
from app.core.logger import logger

router = APIRouter(prefix="/orders", tags=["Orders"])

# Initialize order service
order_service = OrderService()


@router.post("/", response_model=OrderResponse, summary="Create a new order")
async def create_order(order_data: OrderCreate):
    """
    Create a new order for payment processing
    
    Parameters:
    - user_id: User ID who is creating the order
    - amount: Order amount in tiyin (coins), must be greater than 0
    - description: Optional order description
    
    Returns:
    - Created order with order_id and other details
    """
    try:
        order = order_service.create_order(order_data)
        
        if not order:
            raise HTTPException(
                status_code=500,
                detail="Failed to create order"
            )
        
        return OrderResponse(
            order_id=order.order_id,
            user_id=order.user_id,
            amount=order.amount,
            description=order.description,
            status=order.status,
            created_at=order.created_at,
            paid_at=order.paid_at
        )
        
    except Exception as e:
        logger.error(f"[OrdersAPI] Error creating order: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create order: {str(e)}"
        )


@router.get("/{order_id}", response_model=OrderResponse, summary="Get order by ID")
async def get_order(order_id: str):
    """
    Get order details by order ID
    
    Parameters:
    - order_id: Order identifier
    
    Returns:
    - Order details if found
    """
    try:
        order = order_service.get_order(order_id)
        
        if not order:
            raise HTTPException(
                status_code=404,
                detail=f"Order {order_id} not found"
            )
        
        return OrderResponse(
            order_id=order.order_id,
            user_id=order.user_id,
            amount=order.amount,
            description=order.description,
            status=order.status,
            created_at=order.created_at,
            paid_at=order.paid_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[OrdersAPI] Error getting order {order_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get order: {str(e)}"
        )
