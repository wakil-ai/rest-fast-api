# Paycom Payment Integration - Testing Guide

## Overview
This document describes how to test the Paycom payment integration implementation.

## Prerequisites
- MongoDB running and accessible
- Python 3.11+ with all dependencies from requirements.txt installed
- Paycom merchant credentials configured in .env file

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables in `.env`:
```bash
PAYCOM_MERCHANT_ID=your_merchant_id
PAYCOM_MERCHANT_KEY=your_merchant_key
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=wakilai
```

3. Start the API server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## Manual Testing

### 1. Create an Order

```bash
curl -X POST "http://localhost:8080/paycom/create-order" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "amount": 10000,
    "credit_amount": 100,
    "description": "Purchase 100 credits"
  }'
```

Expected response:
```json
{
  "order_id": "ORD-1234567890-a1b2c3d4",
  "user_id": "user123",
  "amount": 10000,
  "credit_amount": 100,
  "status": "pending",
  "description": "Purchase 100 credits",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### 2. Test Order Validation (CheckPerformTransaction)

```bash
# Prepare credentials
MERCHANT_KEY="your_merchant_key"
AUTH=$(echo -n "Paycom:$MERCHANT_KEY" | base64)

# Test with valid order
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "CheckPerformTransaction",
    "params": {
      "amount": 10000,
      "account": {
        "order_id": "ORD-1234567890-a1b2c3d4"
      }
    }
  }'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "allow": true
  }
}
```

### 3. Test with Invalid Order ID

```bash
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "CheckPerformTransaction",
    "params": {
      "amount": 10000,
      "account": {
        "order_id": "INVALID-ORDER"
      }
    }
  }'
```

Expected error response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -31050,
    "message": "Order INVALID-ORDER not found",
    "data": null
  }
}
```

### 4. Test with Wrong Amount

```bash
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "CheckPerformTransaction",
    "params": {
      "amount": 20000,
      "account": {
        "order_id": "ORD-1234567890-a1b2c3d4"
      }
    }
  }'
```

Expected error response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -31050,
    "message": "Amount mismatch for order ORD-1234567890-a1b2c3d4. Expected: 10000, Got: 20000",
    "data": null
  }
}
```

### 5. Create Transaction

```bash
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "CreateTransaction",
    "params": {
      "id": "5f9c3d4e1c9d440000a1b2c3",
      "time": 1609459200000,
      "amount": 10000,
      "account": {
        "order_id": "ORD-1234567890-a1b2c3d4"
      }
    }
  }'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "create_time": 1609459200000,
    "transaction": "ORD-1234567890-a1b2c3d4",
    "state": 1,
    "receivers": null
  }
}
```

### 6. Perform Transaction

```bash
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "PerformTransaction",
    "params": {
      "id": "5f9c3d4e1c9d440000a1b2c3"
    }
  }'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "transaction": "5f9c3d4e1c9d440000a1b2c3",
    "perform_time": 1609459201000,
    "state": 2
  }
}
```

### 7. Check Transaction

```bash
curl -X POST "http://localhost:8080/paycom/merchant" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "CheckTransaction",
    "params": {
      "id": "5f9c3d4e1c9d440000a1b2c3"
    }
  }'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "create_time": 1609459200000,
    "perform_time": 1609459201000,
    "cancel_time": null,
    "transaction": "ORD-1234567890-a1b2c3d4",
    "state": 2,
    "reason": null
  }
}
```

## Automated Tests

Run the test suite:
```bash
pytest tests/test_paycom_payment.py -v
```

Or run the manual test script:
```bash
python3 tests/manual_test_paycom.py
```

## Database Verification

Check MongoDB for created records:

```javascript
// Connect to MongoDB
use wakilai

// View orders
db.orders.find().pretty()

// View transactions
db.paycom_transactions.find().pretty()

// Check order status after payment
db.orders.find({order_id: "ORD-1234567890-a1b2c3d4"})
```

## Using the create_transaction.py Script

The repository includes a script to test transaction creation:

1. Update the configuration in `create_transaction.py`:
```python
MERCHANT_API_URL = 'http://localhost:8080/paycom/merchant'
MERCHANT_KEY = 'your_merchant_key'  # From .env PAYCOM_MERCHANT_KEY
ORDER_ID = 12345  # Your test order ID
AMOUNT = 10000  # Amount in tiyin
```

2. Run the script:
```bash
python3 create_transaction.py
```

## Validation Test Scenarios

1. ✅ Valid order with correct amount → allow=true
2. ✅ Non-existent order → Error -31050 "Order not found"
3. ✅ Wrong amount → Error -31050 "Amount mismatch"
4. ✅ Already paid order → Error -31050 "not in pending status"
5. ✅ Cancelled order → Error -31050 "not in pending status"
6. ✅ Transaction creation → state=1 (created)
7. ✅ Transaction completion → state=2 (completed), order status → paid
8. ✅ Transaction cancellation → state=-1 (cancelled), order status → cancelled

## Error Codes

- `-32700`: Invalid JSON-RPC object
- `-32601`: Method not found
- `-31050`: Invalid account (order not found, wrong amount, wrong status)
- `-31008`: Could not perform operation
- `-31003`: Transaction not found
- `-32504`: Insufficient privilege (authentication failed)

## Next Steps

After successful testing, the following enhancements can be added:

1. Implement credit addition in `perform_transaction()`
2. Implement credit deduction in `cancel_transaction()` for refunds
3. Add order expiration handling
4. Add webhook notifications
5. Create admin dashboard endpoints
