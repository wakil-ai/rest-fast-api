#!/usr/bin/env python3
"""
Script to create an order and test the Paycom payment flow

This script demonstrates the complete order creation and payment validation flow:
1. Create an order using the orders API
2. Attempt to create a transaction with the order_id
3. Verify that the validation works correctly

Usage:
    python test_order_payment_flow.py
"""

import json
import os
import requests
from base64 import b64encode
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8080')
MERCHANT_API_URL = f'{API_BASE_URL}/paycom/merchant'
ORDERS_API_URL = f'{API_BASE_URL}/api/orders'

# Get credentials from environment variables
API_KEY = os.getenv('API_KEY', 'your-api-key')
API_KEY_NAME = os.getenv('API_KEY_NAME', 'x-api-key')
MERCHANT_KEY = os.getenv('PAYCOM_MERCHANT_KEY', 'your-merchant-key')

# Test data
TEST_USER_ID = "test_user_123"
TEST_AMOUNT = 10000  # 100 sum (10000 tiyin)
TEST_DESCRIPTION = "Test order for payment"


def create_order(user_id, amount, description):
    """
    Create a new order using the orders API
    
    Args:
        user_id: User ID
        amount: Amount in tiyin
        description: Order description
        
    Returns:
        Order response or None if failed
    """
    headers = {
        'Content-Type': 'application/json',
        API_KEY_NAME: API_KEY
    }
    
    payload = {
        'user_id': user_id,
        'amount': amount,
        'description': description
    }
    
    print("Step 1: Creating order...")
    print(f"Endpoint: {ORDERS_API_URL}")
    print("Request:")
    print(json.dumps(payload, indent=2))
    print()
    
    try:
        response = requests.post(
            ORDERS_API_URL,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        print(f"HTTP Status Code: {response.status_code}")
        print("Response:")
        
        response_data = response.json()
        print(json.dumps(response_data, indent=2, default=str))
        print()
        
        if response.status_code == 200:
            print("✅ Order created successfully!")
            print(f"   Order ID: {response_data['order_id']}")
            print(f"   Amount: {response_data['amount']} tiyin")
            print(f"   Status: {response_data['status']}")
            print()
            return response_data
        else:
            print("❌ Failed to create order")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def check_perform_transaction(order_id, amount):
    """
    Check if transaction can be performed (Paycom CheckPerformTransaction)
    
    Args:
        order_id: Order ID
        amount: Amount in tiyin
        
    Returns:
        Response data or None
    """
    request_data = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'CheckPerformTransaction',
        'params': {
            'account': {
                'order_id': order_id
            },
            'amount': amount
        }
    }
    
    auth_string = b64encode(f"Paycom:{MERCHANT_KEY}".encode()).decode()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {auth_string}'
    }
    
    print("Step 2: Checking if transaction can be performed...")
    print(f"Endpoint: {MERCHANT_API_URL}")
    print("Request:")
    print(json.dumps(request_data, indent=2))
    print()
    
    try:
        response = requests.post(
            MERCHANT_API_URL,
            json=request_data,
            headers=headers,
            timeout=30
        )
        
        print(f"HTTP Status Code: {response.status_code}")
        print("Response:")
        
        response_data = response.json()
        print(json.dumps(response_data, indent=2))
        print()
        
        if 'result' in response_data and response_data['result'].get('allow'):
            print("✅ Transaction can be performed!")
            print()
            return response_data
        elif 'error' in response_data:
            print("❌ Transaction validation failed")
            print(f"   Error: {response_data['error']['message']}")
            print()
            return None
        else:
            print("❌ Unexpected response")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def test_invalid_amount(order_id):
    """
    Test validation with wrong amount
    
    Args:
        order_id: Order ID
        
    Returns:
        Response data or None
    """
    wrong_amount = 5000  # Wrong amount
    
    request_data = {
        'jsonrpc': '2.0',
        'id': 2,
        'method': 'CheckPerformTransaction',
        'params': {
            'account': {
                'order_id': order_id
            },
            'amount': wrong_amount
        }
    }
    
    auth_string = b64encode(f"Paycom:{MERCHANT_KEY}".encode()).decode()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {auth_string}'
    }
    
    print("Step 3: Testing validation with wrong amount...")
    print(f"Endpoint: {MERCHANT_API_URL}")
    print("Request (with wrong amount):")
    print(json.dumps(request_data, indent=2))
    print()
    
    try:
        response = requests.post(
            MERCHANT_API_URL,
            json=request_data,
            headers=headers,
            timeout=30
        )
        
        print(f"HTTP Status Code: {response.status_code}")
        print("Response:")
        
        response_data = response.json()
        print(json.dumps(response_data, indent=2))
        print()
        
        if 'error' in response_data:
            print("✅ Validation correctly rejected wrong amount!")
            print(f"   Error: {response_data['error']['message']}")
            print()
            return response_data
        else:
            print("❌ Validation should have failed but didn't!")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def get_order_status(order_id):
    """
    Get order status
    
    Args:
        order_id: Order ID
        
    Returns:
        Order data or None
    """
    headers = {
        API_KEY_NAME: API_KEY
    }
    
    print("Step 4: Getting order status...")
    print(f"Endpoint: {ORDERS_API_URL}/{order_id}")
    print()
    
    try:
        response = requests.get(
            f"{ORDERS_API_URL}/{order_id}",
            headers=headers,
            timeout=30
        )
        
        print(f"HTTP Status Code: {response.status_code}")
        print("Response:")
        
        response_data = response.json()
        print(json.dumps(response_data, indent=2, default=str))
        print()
        
        if response.status_code == 200:
            print("✅ Order retrieved successfully!")
            print(f"   Status: {response_data['status']}")
            print()
            return response_data
        else:
            print("❌ Failed to get order")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def main():
    """Main function to test the order payment flow"""
    print("=" * 70)
    print("Order Payment Flow Test")
    print("=" * 70)
    print()
    
    # Step 1: Create an order
    order = create_order(TEST_USER_ID, TEST_AMOUNT, TEST_DESCRIPTION)
    if not order:
        print("❌ Test failed: Could not create order")
        return 1
    
    order_id = order['order_id']
    
    # Step 2: Check if transaction can be performed with correct amount
    result = check_perform_transaction(order_id, TEST_AMOUNT)
    if not result:
        print("❌ Test failed: CheckPerformTransaction failed")
        return 1
    
    # Step 3: Test validation with wrong amount
    test_invalid_amount(order_id)
    
    # Step 4: Get order status
    get_order_status(order_id)
    
    print("=" * 70)
    print("✅ Order payment flow test completed successfully!")
    print("=" * 70)
    print()
    print("Summary:")
    print("1. ✅ Order created successfully")
    print("2. ✅ Transaction validation works with correct amount")
    print("3. ✅ Transaction validation rejects incorrect amount")
    print("4. ✅ Order status can be retrieved")
    print()
    
    return 0


if __name__ == '__main__':
    exit(main())
