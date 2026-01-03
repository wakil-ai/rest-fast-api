# tests/test_paycom_order_validation.py

import pytest
from unittest.mock import Mock, patch
from app.services.order_service import OrderService
from app.models.order import Order, OrderCreate
from datetime import datetime


@pytest.fixture
def mock_mongo_handler():
    """Mock MongoDB handler"""
    with patch('app.services.order_service.MongoHandler') as mock:
        handler = Mock()
        handler.db = {
            'orders': Mock()
        }
        mock.return_value = handler
        yield handler


def test_validate_order_success(mock_mongo_handler):
    """Test successful order validation"""
    # Setup
    order_service = OrderService()
    test_order = Order(
        order_id="abc123",
        user_id="user1",
        amount=10000,
        status="pending",
        created_at=datetime.utcnow()
    )
    
    # Mock get_order to return test order
    mock_mongo_handler.db['orders'].find_one.return_value = {
        'order_id': 'abc123',
        'user_id': 'user1',
        'amount': 10000,
        'status': 'pending',
        'created_at': datetime.utcnow()
    }
    
    # Test
    is_valid, error_message = order_service.validate_order("abc123", 10000)
    
    # Assert
    assert is_valid is True
    assert error_message is None


def test_validate_order_not_found(mock_mongo_handler):
    """Test validation when order doesn't exist"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].find_one.return_value = None
    
    # Test
    is_valid, error_message = order_service.validate_order("nonexistent", 10000)
    
    # Assert
    assert is_valid is False
    assert "not found" in error_message


def test_validate_order_amount_mismatch(mock_mongo_handler):
    """Test validation when amount doesn't match"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].find_one.return_value = {
        'order_id': 'abc123',
        'user_id': 'user1',
        'amount': 10000,
        'status': 'pending',
        'created_at': datetime.utcnow()
    }
    
    # Test with wrong amount
    is_valid, error_message = order_service.validate_order("abc123", 5000)
    
    # Assert
    assert is_valid is False
    assert "Amount mismatch" in error_message


def test_validate_order_already_paid(mock_mongo_handler):
    """Test validation when order is already paid"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].find_one.return_value = {
        'order_id': 'abc123',
        'user_id': 'user1',
        'amount': 10000,
        'status': 'paid',
        'created_at': datetime.utcnow(),
        'paid_at': datetime.utcnow()
    }
    
    # Test
    is_valid, error_message = order_service.validate_order("abc123", 10000)
    
    # Assert
    assert is_valid is False
    assert "already paid" in error_message


def test_validate_order_cancelled(mock_mongo_handler):
    """Test validation when order is cancelled"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].find_one.return_value = {
        'order_id': 'abc123',
        'user_id': 'user1',
        'amount': 10000,
        'status': 'cancelled',
        'created_at': datetime.utcnow()
    }
    
    # Test
    is_valid, error_message = order_service.validate_order("abc123", 10000)
    
    # Assert
    assert is_valid is False
    assert "cancelled" in error_message


def test_create_order(mock_mongo_handler):
    """Test order creation"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].insert_one.return_value = Mock(inserted_id="mock_id")
    
    order_data = OrderCreate(
        user_id="user1",
        amount=10000,
        description="Test order"
    )
    
    # Test
    order = order_service.create_order(order_data)
    
    # Assert
    assert order is not None
    assert order.user_id == "user1"
    assert order.amount == 10000
    assert order.status == "pending"
    assert len(order.order_id) == 12  # Should be 12-character hex string


def test_update_order_status(mock_mongo_handler):
    """Test updating order status"""
    # Setup
    order_service = OrderService()
    mock_result = Mock()
    mock_result.modified_count = 1
    mock_mongo_handler.db['orders'].update_one.return_value = mock_result
    
    # Test
    success = order_service.update_order_status("abc123", "paid", paid_at=datetime.utcnow())
    
    # Assert
    assert success is True


def test_get_order(mock_mongo_handler):
    """Test getting order by ID"""
    # Setup
    order_service = OrderService()
    mock_mongo_handler.db['orders'].find_one.return_value = {
        'order_id': 'abc123',
        'user_id': 'user1',
        'amount': 10000,
        'status': 'pending',
        'created_at': datetime.utcnow()
    }
    
    # Test
    order = order_service.get_order("abc123")
    
    # Assert
    assert order is not None
    assert order.order_id == "abc123"
    assert order.amount == 10000
