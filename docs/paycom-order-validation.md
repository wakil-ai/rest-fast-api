# Order Validation Implementation for Paycom API

## Overview

This implementation addresses the TODO comment in `app/api/paycom.py` to validate that orders exist and amounts are correct before processing Paycom payment transactions.

## Components

### 1. Order Model (`app/models/order.py`)

Defines the data structures for orders:
- `Order`: Main order model with fields for order_id, user_id, amount, status, timestamps
- `OrderCreate`: Request model for creating new orders
- `OrderResponse`: Response model for API endpoints

### 2. Order Service (`app/services/order_service.py`)

Business logic for order management:
- `create_order()`: Creates new orders with unique 12-character IDs
- `get_order()`: Retrieves orders by ID from MongoDB
- `update_order_status()`: Updates order status (pending, paid, cancelled)
- `validate_order()`: Core validation function that checks:
  - Order exists in database
  - Amount matches exactly
  - Order is not already paid
  - Order is not cancelled

### 3. Orders API (`app/api/orders.py`)

RESTful endpoints for order management:
- `POST /api/orders`: Create a new order
- `GET /api/orders/{order_id}`: Retrieve order details

### 4. Updated Paycom Integration (`app/api/paycom.py`)

Modified Paycom merchant API handlers:
- `check_perform_transaction()`: Now validates order before allowing transaction
- `create_transaction()`: Now validates order during transaction creation
- `perform_transaction()`: Now marks order as paid upon completion

## Database Schema

Orders are stored in MongoDB with the following structure:

```json
{
  "order_id": "abc123def456",
  "user_id": "user123",
  "amount": 10000,
  "description": "Order description",
  "status": "pending",
  "created_at": "2026-01-03T23:00:00.000Z",
  "paid_at": null
}
```

**Status Values:**
- `pending`: Order created, awaiting payment
- `paid`: Payment completed successfully
- `cancelled`: Order cancelled

## Usage

### Creating an Order

```bash
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "user_id": "user123",
    "amount": 10000,
    "description": "Test order"
  }'
```

Response:
```json
{
  "order_id": "abc123def456",
  "user_id": "user123",
  "amount": 10000,
  "description": "Test order",
  "status": "pending",
  "created_at": "2026-01-03T23:00:00.000Z",
  "paid_at": null
}
```

### Paycom Transaction Flow

1. **CheckPerformTransaction**: Paycom calls this to verify the order can be paid
   - Validates order exists
   - Validates amount matches
   - Returns `{"allow": true}` or error

2. **CreateTransaction**: Paycom creates a transaction
   - Re-validates order
   - Creates transaction record (currently mocked)

3. **PerformTransaction**: Paycom completes the transaction
   - Marks order as paid
   - Updates paid_at timestamp

## Testing

### Unit Tests

Run the test suite:
```bash
pytest tests/test_paycom_order_validation.py -v
```

Tests cover:
- Successful order validation
- Order not found errors
- Amount mismatch errors
- Already paid order rejection
- Cancelled order rejection
- Order CRUD operations

### Integration Testing

Use the provided test script:
```bash
python test_order_payment_flow.py
```

This script:
1. Creates a test order
2. Tests CheckPerformTransaction with correct amount
3. Tests validation with incorrect amount
4. Retrieves order status

**Note:** Update the environment variables in `.env` before running:
- `API_KEY`: Your API key
- `API_KEY_NAME`: API key header name (default: x-api-key)
- `PAYCOM_MERCHANT_KEY`: Paycom merchant key

## Validation Rules

The `validate_order()` function enforces these rules:

1. **Order Existence**: Order must exist in database
2. **Exact Amount Match**: Transaction amount must exactly match order amount (in tiyin)
3. **Not Already Paid**: Cannot process payment for already paid orders
4. **Not Cancelled**: Cannot process payment for cancelled orders

## Error Handling

Validation errors are returned as Paycom JSON-RPC errors:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -31050,
    "message": "Amount mismatch: expected 10000, got 5000",
    "data": null
  }
}
```

Error codes used:
- `-31050`: Invalid account (order validation failed)
- `-31003`: Transaction not found
- `-32504`: Insufficient privilege

## Security Considerations

1. **Authentication**: Orders API requires API key authentication
2. **Paycom Auth**: Paycom endpoints use Basic Auth with merchant key
3. **Input Validation**: All amounts are validated as integers
4. **Order Status**: Prevents double payment through status checks
5. **No SQL Injection**: Uses MongoDB native operations, not string queries

## Future Enhancements

1. **Transaction Storage**: Currently transactions are mocked; implement full transaction model
2. **Webhooks**: Add webhooks for order status changes
3. **Audit Log**: Track all order status changes
4. **Refund Support**: Add order refund functionality
5. **Expiration**: Add order expiration after certain time period

## Dependencies

- `pymongo`: MongoDB client
- `pydantic`: Data validation
- `FastAPI`: Web framework
- `loguru`: Logging

## Configuration

Add to `.env`:
```env
# MongoDB (required)
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=wakilai

# Paycom (required)
PAYCOM_MERCHANT_KEY=your-merchant-key

# API Authentication (required)
API_KEY=your-api-key
API_KEY_NAME=x-api-key
```

## Troubleshooting

### "Database connection not available"
- Ensure MongoDB is running
- Check MONGODB_URI in .env

### "Order not found"
- Verify order was created successfully
- Check order_id is correct string format

### "Amount mismatch"
- Ensure amount is in tiyin (coins), not sum
- 1 sum = 100 tiyin
- Example: 100 sum = 10000 tiyin

### "Order is already paid"
- This is expected behavior
- Order can only be paid once
- Create a new order for new payment
