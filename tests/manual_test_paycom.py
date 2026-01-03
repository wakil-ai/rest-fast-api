#!/usr/bin/env python3
"""
Manual test script for Paycom payment integration

This script demonstrates the order validation and transaction management logic.
It can be run directly without pytest.
"""
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + '/..'))

from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone


def test_order_validation():
    """Test order validation logic"""
    print("\n=== Testing Order Validation ===\n")
    
    # Import after setting up the path
    from app.services.payment_service import PaymentService
    from app.models.payment import OrderStatus
    
    with patch('app.services.payment_service.MongoHandler') as mock_mongo:
        # Setup mock
        mock_collection = Mock()
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        # Test 1: Valid order
        print("Test 1: Valid order with correct amount")
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PENDING
        }
        
        service = PaymentService()
        is_valid, error = service.validate_order('ORD-12345', 10000)
        
        print(f"  Result: {'✓ PASS' if is_valid and not error else '✗ FAIL'}")
        print(f"  Valid: {is_valid}, Error: {error}")
        
        # Test 2: Order not found
        print("\nTest 2: Order not found")
        mock_collection.find_one.return_value = None
        
        is_valid, error = service.validate_order('ORD-NONEXIST', 10000)
        
        print(f"  Result: {'✓ PASS' if not is_valid and 'not found' in error else '✗ FAIL'}")
        print(f"  Valid: {is_valid}, Error: {error}")
        
        # Test 3: Wrong amount
        print("\nTest 3: Order with wrong amount")
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PENDING
        }
        
        is_valid, error = service.validate_order('ORD-12345', 20000)
        
        print(f"  Result: {'✓ PASS' if not is_valid and 'mismatch' in error.lower() else '✗ FAIL'}")
        print(f"  Valid: {is_valid}, Error: {error}")
        
        # Test 4: Wrong status
        print("\nTest 4: Order with wrong status")
        mock_collection.find_one.return_value = {
            'order_id': 'ORD-12345',
            'user_id': 'user123',
            'amount': 10000,
            'status': OrderStatus.PAID
        }
        
        is_valid, error = service.validate_order('ORD-12345', 10000)
        
        print(f"  Result: {'✓ PASS' if not is_valid and 'pending' in error.lower() else '✗ FAIL'}")
        print(f"  Valid: {is_valid}, Error: {error}")


def test_order_creation():
    """Test order creation"""
    print("\n=== Testing Order Creation ===\n")
    
    from app.services.payment_service import PaymentService
    
    with patch('app.services.payment_service.MongoHandler') as mock_mongo:
        # Setup mock
        mock_collection = Mock()
        mock_collection.insert_one.return_value = Mock(inserted_id='test_id')
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        print("Test: Create new order")
        service = PaymentService()
        order_id = service.create_order(
            user_id='user123',
            amount=10000,
            credit_amount=100,
            description='Test order'
        )
        
        success = order_id is not None and order_id.startswith('ORD-')
        print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
        print(f"  Order ID: {order_id}")
        print(f"  insert_one called: {mock_collection.insert_one.called}")


def test_transaction_management():
    """Test transaction lifecycle"""
    print("\n=== Testing Transaction Management ===\n")
    
    from app.services.payment_service import PaymentService
    from app.models.payment import TransactionState
    
    with patch('app.services.payment_service.MongoHandler') as mock_mongo:
        # Setup mock
        mock_collection = Mock()
        mock_mongo.return_value.db.__getitem__.return_value = mock_collection
        
        service = PaymentService()
        
        # Test 1: Create transaction
        print("Test 1: Create transaction")
        mock_collection.find_one.return_value = None  # No existing transaction
        mock_collection.insert_one.return_value = Mock(inserted_id='test_id')
        
        success = service.create_transaction(
            transaction_id='TX-12345',
            order_id='ORD-12345',
            amount=10000,
            time_ms=1609459200000
        )
        
        print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
        print(f"  Created: {success}")
        
        # Test 2: Perform transaction
        print("\nTest 2: Perform transaction")
        mock_collection.find_one.return_value = {
            'transaction_id': 'TX-12345',
            'order_id': 'ORD-12345',
            'amount': 10000,
            'state': TransactionState.CREATED
        }
        mock_collection.update_one.return_value = Mock(modified_count=1)
        
        success = service.perform_transaction('TX-12345', 1609459200000)
        
        print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
        print(f"  Performed: {success}")
        
        # Test 3: Cancel transaction
        print("\nTest 3: Cancel transaction")
        mock_collection.find_one.return_value = {
            'transaction_id': 'TX-12345',
            'order_id': 'ORD-12345',
            'amount': 10000,
            'state': TransactionState.CREATED
        }
        mock_collection.update_one.return_value = Mock(modified_count=1)
        
        success = service.cancel_transaction('TX-12345', 1609459200000, 1)
        
        print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
        print(f"  Cancelled: {success}")


def test_paycom_api_validation():
    """Test Paycom API validation"""
    print("\n=== Testing Paycom API Validation ===\n")
    
    import asyncio
    from app.api.paycom import check_perform_transaction, PaycomException
    
    async def run_test():
        with patch('app.api.paycom.payment_service') as mock_service:
            # Test 1: Valid order
            print("Test 1: CheckPerformTransaction with valid order")
            mock_service.validate_order.return_value = (True, None)
            
            result = await check_perform_transaction(
                request_id=1,
                params={
                    'account': {'order_id': 'ORD-12345'},
                    'amount': 10000
                }
            )
            
            success = result.get('allow') is True
            print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
            print(f"  Allow: {result.get('allow')}")
            
            # Test 2: Invalid order
            print("\nTest 2: CheckPerformTransaction with invalid order")
            mock_service.validate_order.return_value = (False, 'Order not found')
            
            exception_raised = False
            try:
                await check_perform_transaction(
                    request_id=1,
                    params={
                        'account': {'order_id': 'ORD-INVALID'},
                        'amount': 10000
                    }
                )
            except PaycomException as e:
                exception_raised = True
                error_code = e.code
            
            success = exception_raised and error_code == -31050
            print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")
            print(f"  Exception raised: {exception_raised}")
            if exception_raised:
                print(f"  Error code: {error_code}")
    
    asyncio.run(run_test())


def main():
    """Run all tests"""
    print("=" * 60)
    print("Paycom Payment Integration - Manual Test Suite")
    print("=" * 60)
    
    try:
        test_order_validation()
        test_order_creation()
        test_transaction_management()
        test_paycom_api_validation()
        
        print("\n" + "=" * 60)
        print("All tests completed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
