"""
Tests for Paycom payment integration
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from app.services.payment_service import PaymentService
from app.models.payment import OrderStatus, TransactionState


class TestPaymentService:
    """Test cases for PaymentService"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.payment_service = PaymentService()
        
    @patch('app.services.payment_service.MongoHandler')
    def test_validate_order_success(self, mock_mongo):
        """Test successful order validation"""
        # Mock MongoDB response
        mock_collection = Mock()
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PENDING
        }
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        is_valid, error = service.validate_order('ORD-12345', 10000)
        
        assert is_valid is True
        assert error is None
    
    @patch('app.services.payment_service.MongoHandler')
    def test_validate_order_not_found(self, mock_mongo):
        """Test order validation when order doesn't exist"""
        # Mock MongoDB response
        mock_collection = Mock()
        mock_collection.find_one.return_value = None
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        is_valid, error = service.validate_order('ORD-NONEXIST', 10000)
        
        assert is_valid is False
        assert 'not found' in error
    
    @patch('app.services.payment_service.MongoHandler')
    def test_validate_order_wrong_amount(self, mock_mongo):
        """Test order validation with incorrect amount"""
        # Mock MongoDB response
        mock_collection = Mock()
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PENDING
        }
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        is_valid, error = service.validate_order('ORD-12345', 20000)
        
        assert is_valid is False
        assert 'Amount mismatch' in error
    
    @patch('app.services.payment_service.MongoHandler')
    def test_validate_order_wrong_status(self, mock_mongo):
        """Test order validation when order is not pending"""
        # Mock MongoDB response
        mock_collection = Mock()
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PAID
        }
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        is_valid, error = service.validate_order('ORD-12345', 10000)
        
        assert is_valid is False
        assert 'not in pending status' in error
    
    @patch('app.services.payment_service.MongoHandler')
    def test_create_order(self, mock_mongo):
        """Test order creation"""
        # Mock MongoDB
        mock_collection = Mock()
        mock_collection.insert_one.return_value = Mock(inserted_id='test_id')
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        order_id = service.create_order(
            user_id='user123',
            amount=10000,
            credit_amount=100,
            description='Test order'
        )
        
        assert order_id is not None
        assert order_id.startswith('ORD-')
        mock_collection.insert_one.assert_called_once()
    
    @patch('app.services.payment_service.MongoHandler')
    def test_create_transaction(self, mock_mongo):
        """Test transaction creation"""
        # Mock MongoDB
        mock_collection = Mock()
        mock_collection.find_one.return_value = None  # No existing transaction
        mock_collection.insert_one.return_value = Mock(inserted_id='test_id')
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        success = service.create_transaction(
            transaction_id='TX-12345',
            order_id='ORD-12345',
            amount=10000,
            time_ms=1609459200000
        )
        
        assert success is True
        mock_collection.insert_one.assert_called_once()
    
    @patch('app.services.payment_service.MongoHandler')
    def test_perform_transaction(self, mock_mongo):
        """Test transaction performance"""
        # Mock MongoDB
        mock_collection = Mock()
        mock_collection.find_one.return_value = {
            'transaction_id': 'TX-12345',
            'order_id': 'ORD-12345',
            'amount': 10000,
            'state': TransactionState.CREATED
        }
        mock_collection.update_one.return_value = Mock(modified_count=1)
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        success = service.perform_transaction('TX-12345', 1609459200000)
        
        assert success is True
    
    @patch('app.services.payment_service.MongoHandler')
    def test_cancel_transaction(self, mock_mongo):
        """Test transaction cancellation"""
        # Mock MongoDB
        mock_collection = Mock()
        mock_collection.find_one.return_value = {
            'transaction_id': 'TX-12345',
            'order_id': 'ORD-12345',
            'amount': 10000,
            'state': TransactionState.CREATED
        }
        mock_collection.update_one.return_value = Mock(modified_count=1)
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        success = service.cancel_transaction('TX-12345', 1609459200000, 1)
        
        assert success is True


class TestPaycomAPI:
    """Test cases for Paycom API endpoints"""
    
    @pytest.mark.asyncio
    async def test_check_perform_transaction_success(self):
        """Test CheckPerformTransaction with valid order"""
        from app.api.paycom import check_perform_transaction
        
        with patch('app.api.paycom.payment_service') as mock_service:
            mock_service.validate_order.return_value = (True, None)
            
            result = await check_perform_transaction(
                request_id=1,
                params={
                    'account': {'order_id': 'ORD-12345'},
                    'amount': 10000
                }
            )
            
            assert result['allow'] is True
    
    @pytest.mark.asyncio
    async def test_check_perform_transaction_invalid_order(self):
        """Test CheckPerformTransaction with invalid order"""
        from app.api.paycom import check_perform_transaction, PaycomException
        
        with patch('app.api.paycom.payment_service') as mock_service:
            mock_service.validate_order.return_value = (False, 'Order not found')
            
            with pytest.raises(PaycomException) as exc_info:
                await check_perform_transaction(
                    request_id=1,
                    params={
                        'account': {'order_id': 'ORD-INVALID'},
                        'amount': 10000
                    }
                )
            
            assert exc_info.value.code == -31050  # ERROR_INVALID_ACCOUNT
    
    @pytest.mark.asyncio
    async def test_create_transaction_success(self):
        """Test CreateTransaction endpoint"""
        from app.api.paycom import create_transaction
        
        with patch('app.api.paycom.payment_service') as mock_service:
            mock_service.validate_order.return_value = (True, None)
            mock_service.get_transaction.return_value = None
            mock_service.create_transaction.return_value = True
            
            result = await create_transaction(
                request_id=1,
                params={
                    'id': 'TX-12345',
                    'time': 1609459200000,
                    'amount': 10000,
                    'account': {'order_id': 'ORD-12345'}
                }
            )
            
            assert result['state'] == 1  # STATE_CREATED
            assert result['transaction'] == 'ORD-12345'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
