# Promo Code System Documentation

## Overview

The promo code system allows administrators to create promo codes that grant users unlimited API requests, bypassing the standard rate limit of 5 requests per day.

## Features

- **Create/Manage Promo Codes**: Admins can create, activate, deactivate, and delete promo codes
- **Assign to Users**: Promo codes can be assigned to specific users for unlimited access
- **Automatic Rate Limit Bypass**: Users with valid promo codes automatically bypass rate limits
- **Track Assignments**: View all users with promo codes and their assignment details

## API Endpoints

### Promo Code Management

#### 1. Create a Promo Code
```http
POST /admin/promo-codes
Content-Type: application/json

{
  "code": "UNLIMITED2024",
  "description": "VIP user unlimited access"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Promo code 'UNLIMITED2024' created successfully",
  "promo_code": {
    "code": "UNLIMITED2024",
    "is_active": true,
    "created_at": "2024-01-01T00:00:00Z",
    "created_by": "admin",
    "description": "VIP user unlimited access"
  }
}
```

#### 2. List All Promo Codes
```http
GET /admin/promo-codes?active_only=true
```

**Response:**
```json
{
  "promo_codes": [
    {
      "code": "UNLIMITED2024",
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z",
      "created_by": "admin",
      "description": "VIP user unlimited access"
    }
  ],
  "count": 1
}
```

#### 3. Get Specific Promo Code
```http
GET /admin/promo-codes/UNLIMITED2024
```

#### 4. Activate a Promo Code
```http
PATCH /admin/promo-codes/UNLIMITED2024/activate
```

#### 5. Deactivate a Promo Code
```http
PATCH /admin/promo-codes/UNLIMITED2024/deactivate
```

#### 6. Delete a Promo Code
```http
DELETE /admin/promo-codes/UNLIMITED2024
```

**Note:** Deleting a promo code also removes all user assignments.

### User Assignment

#### 1. Assign Promo Code to User
```http
POST /admin/promo-codes/assign
Content-Type: application/json

{
  "user_id": "user123",
  "promo_code": "UNLIMITED2024"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Promo code 'UNLIMITED2024' assigned to user user123",
  "user_id": "user123",
  "promo_code": "UNLIMITED2024",
  "has_unlimited_access": true
}
```

#### 2. Remove Promo Code from User
```http
DELETE /admin/promo-codes/assign/user123
```

#### 3. List All Users with Promo Codes
```http
GET /admin/promo-codes/users
```

**Response:**
```json
{
  "users": [
    {
      "user_id": "user123",
      "promo_code": "UNLIMITED2024",
      "assigned_at": "2024-01-01T12:00:00Z",
      "has_unlimited_access": true
    }
  ],
  "count": 1
}
```

#### 4. Get User's Promo Code
```http
GET /admin/promo-codes/users/user123
```

## Rate Limit Behavior

### Without Promo Code
- Users are limited to 5 requests per day
- Rate limit counter increments with each request
- Requests are blocked when limit is reached

### With Valid Promo Code
- Users have unlimited requests (no daily limit)
- Rate limit check returns `is_allowed=True, current_count=-1, limit=-1`
- `-1` indicates unlimited access
- No rate limit counter is incremented

## Database Collections

### `promo_codes`
Stores all promo codes:
```json
{
  "_id": ObjectId,
  "code": "UNLIMITED2024",
  "is_active": true,
  "created_at": ISODate,
  "created_by": "admin",
  "description": "VIP user unlimited access"
}
```

### `user_promo_codes`
Stores user-promo code assignments:
```json
{
  "_id": ObjectId,
  "user_id": "user123",
  "promo_code": "UNLIMITED2024",
  "assigned_at": ISODate,
  "has_unlimited_access": true
}
```

## Usage Flow

1. **Admin Creates Promo Code**
   ```bash
   POST /admin/promo-codes
   ```

2. **Admin Assigns to User**
   ```bash
   POST /admin/promo-codes/assign
   ```

3. **User Makes Request**
   - Rate limit service checks for valid promo code
   - If found and active, request is allowed without counting
   - User can make unlimited requests

4. **Admin Can Revoke Access**
   - Deactivate the promo code: affects all users with that code
   - Remove assignment from specific user: affects only that user

## Implementation Details

### Files Modified/Created

1. **`src/models/promo_code.py`** - Pydantic models for promo codes
2. **`src/services/promo_code_service.py`** - Service layer for promo code operations
3. **`src/services/rate_limit_service.py`** - Updated to check promo codes
4. **`src/api/admin.py`** - Admin endpoints for promo code management

### Key Methods

- `PromoCodeService.user_has_unlimited_access(user_id)` - Check if user has valid promo code
- `RateLimitService.check_and_increment_limit(user_id)` - Now checks promo codes first
- `RateLimitService.get_remaining_requests(user_id)` - Returns -1 for unlimited users

## Security Considerations

1. All promo code endpoints are under `/admin` prefix - ensure proper authentication
2. Promo codes are case-sensitive
3. Inactive promo codes don't grant unlimited access
4. Deleting a promo code revokes access for all assigned users
5. Admin actions should be logged and audited

## Testing Examples

### Test Unlimited Access
```python
# 1. Create promo code
promo_code = "TESTCODE123"

# 2. Assign to user
user_id = "test_user"

# 3. Make 10+ requests - all should succeed
# Normal users would be blocked after 5 requests
```

### Test Deactivation
```python
# 1. Assign active promo code to user
# 2. User can make unlimited requests
# 3. Deactivate promo code
# 4. User is now subject to normal rate limits
```

## Monitoring

Check user's access status:
```http
GET /admin/rate-limit/user123
```

Response for user with promo code:
```json
{
  "user_id": "user123",
  "remaining_requests": -1,  // -1 = unlimited
  "daily_limit": 5
}
```

Response for regular user:
```json
{
  "user_id": "user456",
  "remaining_requests": 3,
  "daily_limit": 5
}
```
