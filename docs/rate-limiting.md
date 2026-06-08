# Rate Limiting Implementation

This document describes the rate limiting feature implemented for the WakilAI API.

## Overview

A daily request limit of **5 requests per user per day** has been implemented to control API usage. This limit applies to all chat-related endpoints.

## How It Works

- Each user is identified by their `user_id`
- The system tracks requests on a daily basis (resets at midnight UTC)
- When a user reaches their daily limit, they receive a `429 Too Many Requests` error
- Rate limit data is stored in MongoDB in the `rate_limits` collection

## Affected Endpoints

The following endpoints enforce rate limiting:

- `POST /api/v1/chat/ask` - Main chat endpoint
- `POST /api/v1/chat/soliq` - Soliq assistant endpoint
- `POST /api/v1/chat/file` - File-based chat endpoint
- `POST /api/v1/chat/agent` - Agentic RAG endpoint
- `POST /api/v1/chat/agent/stream` - Streaming agentic RAG endpoint

## Error Response

When a user exceeds their daily limit, they will receive:

```json
{
  "status_code": 429,
  "detail": "Daily request limit exceeded. You have used 5/5 requests today. Please try again tomorrow."
}
```

## Admin Endpoints

Two admin endpoints are available to manage rate limits:

### Check User's Remaining Requests

```bash
GET /api/v1/admin/rate-limit/{user_id}
```

**Response:**
```json
{
  "user_id": "user123",
  "remaining_requests": 3,
  "daily_limit": 5
}
```

### Reset User's Rate Limit

```bash
POST /api/v1/admin/rate-limit/reset
Content-Type: application/json

{
  "user_id": "user123"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Rate limit reset successfully for user user123"
}
```

## Database Schema

Rate limits are stored in MongoDB with the following structure:

```javascript
{
  "_id": ObjectId("..."),
  "user_id": "user123",
  "date": "2025-12-29",  // YYYY-MM-DD format
  "count": 3,
  "created_at": ISODate("2025-12-29T08:00:00.000Z"),
  "updated_at": ISODate("2025-12-29T14:30:00.000Z")
}
```

## Configuration

The daily limit is configured in the `RateLimitService` class:

```python
services/rate_limit_service.py
class RateLimitService:
    DAILY_LIMIT = 5  # Change this value to adjust the limit
```

To modify the limit, change the `DAILY_LIMIT` constant and redeploy the application.

## Implementation Details

### Service Layer

- **File:** `src/services/rate_limit_service.py`
- **Class:** `RateLimitService`
- **Key Methods:**
  - `check_and_increment_limit(user_id)` - Checks and increments the user's request count
  - `get_remaining_requests(user_id)` - Returns remaining requests for the day
  - `reset_user_limit(user_id)` - Admin function to reset a user's limit

### Integration

Rate limiting is integrated into each chat endpoint in `src/api/chat.py`. The check is performed at the beginning of each request handler:

```python
# Check rate limit
is_allowed, current_count, limit = rate_limit_service.check_and_increment_limit(request.user_id)
if not is_allowed:
    raise HTTPException(
        status_code=429,
        detail=f"Daily request limit exceeded. You have used {current_count}/{limit} requests today. Please try again tomorrow."
    )
```

## Testing

To test the rate limiting:

1. Make 5 requests with the same `user_id`
2. The 6th request should return a 429 error
3. Use the admin reset endpoint to clear the limit
4. Verify the user can make requests again

## Future Enhancements

Possible improvements to consider:

- Different limits for different user tiers (free, premium, enterprise)
- Rate limiting by IP address as a fallback
- Configurable limits via environment variables
- Hourly or minute-based rate limiting in addition to daily
- Rate limit headers in responses (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`)
