#!/usr/bin/env python3
"""
Script to create a transaction using Paycom Merchant API

This script sends a CreateTransaction request to the Paycom Merchant API endpoint.
It uses JSON-RPC 2.0 protocol with Basic Authentication.

Usage:
    python create_transaction.py
"""

import json
import time
import random
import secrets
import requests
from base64 import b64encode


# Configuration
# This calls YOUR FastAPI backend merchant API endpoint
MERCHANT_API_URL = 'http://localhost:8080/paycom/merchant'  # Your backend Paycom endpoint
MERCHANT_ID = '6954f0cbccaf6835002a70a6'  # Replace with your merchant ID
MERCHANT_KEY = 'ZYOH#UtxGqzGariuCM%sAAcJxp%7jjjbV5Ve'  # Replace with your merchant key (add to .env as PAYCOM_MERCHANT_KEY)

# Transaction parameters
ORDER_ID = 12345  # Replace with your order ID
AMOUNT = 10000  # Amount in coins (tiyin), e.g., 10000 tiyin = 100 sum
ACCOUNT = {
    'order_id': ORDER_ID
}  # Account parameters to identify the order


def generate_transaction_id():
    """
    Generate a unique transaction ID
    Format: 24 character hexadecimal string (similar to MongoDB ObjectId)
    
    Returns:
        str: Transaction ID
    """
    return secrets.token_hex(12)


def create_transaction(merchant_url, merchant_id, merchant_key, order_id, amount, account):
    """
    Send CreateTransaction request to Paycom Merchant API
    
    Args:
        merchant_url (str): Merchant API endpoint URL
        merchant_id (str): Merchant ID
        merchant_key (str): Merchant key for authentication
        order_id (int): Order ID
        amount (int): Amount in coins
        account (dict): Account parameters
        
    Returns:
        dict: Response from the API
    """
    # Generate unique transaction ID
    transaction_id = generate_transaction_id()
    
    # Current time in milliseconds
    current_time = int(time.time() * 1000)
    
    # Build JSON-RPC request payload
    request_data = {
        'jsonrpc': '2.0',
        'id': random.randint(1, 999999),  # Request ID
        'method': 'CreateTransaction',
        'params': {
            'id': transaction_id,
            'time': current_time,
            'amount': amount,
            'account': account
        }
    }
    
    # Prepare Basic Authentication header
    auth_string = b64encode(f"Paycom:{merchant_key}".encode()).decode()
    
    # Prepare headers
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {auth_string}',
        'X-Auth': merchant_id
    }
    
    # Display request information
    print("Sending CreateTransaction request...")
    print(f"Endpoint: {merchant_url}")
    print("Request:")
    print(json.dumps(request_data, indent=2))
    print()
    
    try:
        # Send POST request
        response = requests.post(
            merchant_url,
            json=request_data,
            headers=headers,
            timeout=30
            # For development/testing - disable SSL verification (remove in production)
            # verify=False
        )
        
        # Display response information
        print(f"HTTP Status Code: {response.status_code}")
        print("Response:")
        
        # Parse JSON response
        response_data = response.json()
        print(json.dumps(response_data, indent=2))
        print()
        
        # Check for errors in response
        if 'error' in response_data:
            print("Error occurred:")
            print(f"  Code: {response_data['error']['code']}")
            print(f"  Message: {response_data['error']['message']}")
            if 'data' in response_data['error']:
                print(f"  Data: {response_data['error']['data']}")
        elif 'result' in response_data:
            print("Transaction created successfully!")
            print(f"  Transaction ID: {response_data['result']['transaction']}")
            print(f"  Create Time: {response_data['result']['create_time']}")
            print(f"  State: {response_data['result']['state']}")
            
        return response_data
        
    except requests.exceptions.RequestException as e:
        print(f"Request Error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e}")
        print(f"Raw Response: {response.text}")
        return None


def main():
    """Main function"""
    response = create_transaction(
        merchant_url=MERCHANT_API_URL,
        merchant_id=MERCHANT_ID,
        merchant_key=MERCHANT_KEY,
        order_id=ORDER_ID,
        amount=AMOUNT,
        account=ACCOUNT
    )
    
    if response:
        return 0
    else:
        return 1


if __name__ == '__main__':
    exit(main())
