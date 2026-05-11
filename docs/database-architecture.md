# WakilAI Database Architecture

## Overview
WakilAI uses MongoDB as the primary database for storing user data, chat history, promo codes, and rate limiting information. This document provides a comprehensive overview of all collections, their schemas, relationships, and how the system works.

---

## Collections Structure

### 1. **users** Collection
Stores user authentication and profile information.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Unique user identifier (from Telegram or Google)
  "telegram_id": String,          // Telegram user ID (if authenticated via Telegram)
  "google_id": String,            // Google user ID (if authenticated via Google)
  "email": String,                // User email
  "name": String,                 // User full name
  "username": String,             // Username
  "created_at": DateTime,         // Account creation timestamp
  "last_login": DateTime,         // Last login timestamp
  "auth_provider": String         // "telegram" or "google"
}
```

**Indexes:**
- `user_id` (unique)
- `telegram_id` (unique, sparse)
- `google_id` (unique, sparse)

---

### 2. **sessions** Collection
Manages user authentication sessions.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "session_id": String,           // Unique session identifier
  "session_token": String,        // Authentication token
  "created_at": DateTime,         // Session creation time
  "expires_at": DateTime,         // Session expiration time (3 days default)
  "is_active": Boolean,           // Whether session is active
  "ip_address": String,           // User's IP address
  "user_agent": String            // Browser/client information
}
```

**Indexes:**
- `session_id` (unique)
- `session_token` (unique)
- `user_id`
- `expires_at`

**Lifecycle:**
- Sessions expire after 3 days (configurable via `TELEGRAM_SESSION_TIMEOUT`)
- Expired sessions are automatically invalidated

---

### 3. **messages** Collection
Stores all chat messages (questions and answers).

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "session_id": String,           // Chat session identifier
  "message_id": String,           // Unique message identifier
  "role": String,                 // "user" or "assistant"
  "content": String,              // Message text
  "assistant_type": String,       // e.g. "main", "tax", "court"
  "model": String,                // LLM model used (e.g., "gpt-oss-120b")
  "timestamp": DateTime,          // Message timestamp
  "metadata": {
    "retrieved_contents": Array,  // Retrieved documents (if applicable)
    "processing_time": Number,    // Response generation time in seconds
    "token_count": Number         // Tokens used
  }
}
```

**Indexes:**
- `user_id`
- `session_id`
- `message_id` (unique)
- `timestamp`

---

### 4. **files** Collection
Stores uploaded files and their OCR results.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "file_id": String,              // Unique file identifier
  "filename": String,             // Original filename
  "file_url": String,             // Google Cloud Storage URL
  "file_type": String,            // MIME type (e.g., "image/png", "application/pdf")
  "file_size": Number,            // File size in bytes
  "ocr_result": String,           // Extracted text from OCR
  "uploaded_at": DateTime,        // Upload timestamp
  "status": String                // "processing", "completed", "failed"
}
```

**Indexes:**
- `user_id`
- `file_id` (unique)
- `uploaded_at`

---

### 5. **feedbacks** Collection
Stores user feedback on assistant responses.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "session_id": String,           // Reference to chat session
  "message_id": String,           // Reference to messages.message_id
  "feedback_type": String,        // "positive", "negative", or "neutral"
  "rating": Number,               // 1-5 star rating
  "comment": String,              // User's feedback comment
  "timestamp": DateTime,          // Feedback submission time
  "metadata": {
    "assistant_type": String,     // Which assistant was used
    "model": String               // Which model generated the response
  }
}
```

**Indexes:**
- `user_id`
- `message_id`
- `timestamp`

---

### 6. **rate_limits** Collection
Tracks daily credit usage for users **without promo codes**.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "date": String,                 // Date in "YYYY-MM-DD" format
  "credits_used": Number,         // Credits consumed today
  "created_at": DateTime,         // First request timestamp
  "updated_at": DateTime          // Last request timestamp
}
```

**Indexes:**
- `user_id` + `date` (compound, unique)
- `date` (for cleanup of old records)

**How it works:**
- **Resets daily**: Each day gets a new document
- **Default limit**: 100 credits per day (configurable via `DAILY_CREDITS_LIMIT`)
- **Credit costs**: Per assistant, see `CREDIT_COST_*` in `app/core/config.py` (e.g. main, tax, court).

**Example calculation:**
- User with 100 daily credits can make any mix of requests whose per-request costs sum to at most 100 for that day.

---

### 7. **promo_codes** Collection
Defines promo codes with their properties.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "code": String,                 // Unique promo code (e.g., "PREMIUM2026")
  "is_active": Boolean,           // Whether code can be used
  "expiration_date": DateTime,    // Expiration date (null = forever)
  "credit_amount": Number,        // Daily credits granted (null = unlimited)
  "created_at": DateTime,         // Code creation time
  "created_by": String,           // Admin who created it
  "description": String,          // Optional description
  "updated_at": DateTime          // Last modification time
}
```

**Indexes:**
- `code` (unique)
- `is_active`

**Promo Code Types:**

| expiration_date | credit_amount | Meaning |
|-----------------|---------------|---------|
| `null` | `null` | Unlimited credits forever |
| `2026-12-31` | `null` | Unlimited credits until Dec 31, 2026 |
| `null` | `500` | 500 daily credits forever |
| `2026-06-30` | `200` | 200 daily credits until June 30, 2026 |

---

### 8. **user_promo_codes** Collection
Links users to their assigned promo codes.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id (unique)
  "promo_code": String,           // Reference to promo_codes.code
  "assigned_at": DateTime,        // When code was assigned
  "total_credits_granted": Number,// Daily credits from promo (null = unlimited)
  "total_credits_used": Number    // Total credits used (for tracking)
}
```

**Indexes:**
- `user_id` (unique) - **One user = One promo code**
- `promo_code`

**Important Notes:**
- **One user can only have ONE promo code at a time**
- Assigning a new promo code replaces the old one
- Promo code credits override default daily limits
- If promo code expires, user falls back to default limits

---

## System Architecture & Data Flow

### Authentication Flow

```
1. User logs in via Telegram/Google
   ↓
2. System creates/updates record in `users` collection
   ↓
3. System generates session token and stores in `sessions` collection
   ↓
4. Session token returned to client
   ↓
5. Client includes token in subsequent requests
```

### Chat Request Flow

```
1. User sends question
   ↓
2. System validates session token (checks `sessions`)
   ↓
3. Rate Limit Check:
   │
   ├─→ Check if user has promo code (`user_promo_codes`)
   │   │
   │   ├─→ Has promo code?
   │   │   ├─→ Check expiration_date (from `promo_codes`)
   │   │   ├─→ Expired? → Use default rate limits
   │   │   └─→ Valid? → Use promo credit_amount
   │   │       ├─→ credit_amount = null → Unlimited
   │   │       └─→ credit_amount = N → N daily credits
   │   │
   │   └─→ No promo code?
   │       └─→ Check `rate_limits` for today
   │           ├─→ credits_used < 100 → Allow
   │           └─→ credits_used >= 100 → Block (429 error)
   │
4. Deduct credits based on assistant type (see `AssistantConfig` / `settings.ASSISTANTS`):
   ├─→ Main (umumiy)
   ├─→ Tax (soliq)
   └─→ Court and other specialists
   ↓
5. Process request (retrieve documents, generate answer)
   ↓
6. Save question to `messages` (role: "user")
   ↓
7. Save answer to `messages` (role: "assistant")
   ↓
8. Return response to client
```

### File Upload Flow

```
1. User uploads file
   ↓
2. System uploads to Google Cloud Storage
   ↓
3. System performs OCR extraction
   ↓
4. File metadata + OCR result saved to `files` collection
   ↓
5. User can query file using file_id
   ↓
6. System uses OCR result as context for answering
```

### Promo Code Assignment Flow

```
1. Admin creates promo code in `promo_codes`
   ↓
2. Admin assigns code to user
   ↓
3. System validates:
   ├─→ Promo code exists?
   ├─→ Promo code active?
   └─→ Valid? → Proceed
   ↓
4. System checks `user_promo_codes` for existing assignment
   ├─→ Exists? → Update to new promo code
   └─→ Not exists? → Create new assignment
   ↓
5. User now uses promo code credits instead of default limits
```

### Rate Limiting Logic

**For users WITHOUT promo codes:**
```javascript
// Check today's usage in rate_limits collection
const today = "2026-01-01";
const limit = await db.rate_limits.findOne({ user_id, date: today });

if (!limit) {
  // First request today - allow and create record
  credits_remaining = 100 - credit_cost;
} else {
  // Check remaining credits
  credits_remaining = 100 - limit.credits_used;
  
  if (credits_remaining < credit_cost) {
    // Block request - insufficient credits
    throw new Error("Insufficient credits");
  }
  
  // Deduct credits
  await db.rate_limits.updateOne(
    { user_id, date: today },
    { $inc: { credits_used: credit_cost } }
  );
}
```

**For users WITH promo codes:**
```javascript
// Get user's promo code assignment
const assignment = await db.user_promo_codes.findOne({ user_id });
const promo = await db.promo_codes.findOne({ code: assignment.promo_code });

// Check expiration
if (promo.expiration_date && now > promo.expiration_date) {
  // Expired - use default rate limits
  // ... (same as above)
}

// Check credit amount
if (promo.credit_amount === null) {
  // Unlimited credits - allow all requests
  return { allowed: true, remaining: -1 };
}

// Use promo code daily limit instead of default 100
const today = "2026-01-01";
const limit = await db.rate_limits.findOne({ user_id, date: today });
const daily_limit = promo.credit_amount; // e.g., 500

if (!limit) {
  credits_remaining = daily_limit - credit_cost;
} else {
  credits_remaining = daily_limit - limit.credits_used;
  
  if (credits_remaining < credit_cost) {
    throw new Error("Insufficient credits");
  }
  
  await db.rate_limits.updateOne(
    { user_id, date: today },
    { $inc: { credits_used: credit_cost } }
  );
}
```

---

## Credit System Details

### Default Credits (No Promo Code)
- **Daily Limit**: 100 credits
- **Resets**: Every day at midnight UTC
- **Tracked in**: `rate_limits` collection

### Promo Code Credits
- **Daily Limit**: Defined by `promo_codes.credit_amount`
  - `null` = Unlimited
  - Number = Custom daily limit (e.g., 500 credits/day)
- **Expiration**: Defined by `promo_codes.expiration_date`
  - `null` = Forever
  - DateTime = Expires at specified date
- **Tracked in**: `rate_limits` collection (same as default, but different limit)

### Credit Costs by Assistant Type
Costs are defined in `settings.ASSISTANTS` (see `app/core/config.py`). Typical entries include main (umumiy), tax (soliq), court, and contract analyzer.

**Example:** With main at 10 credits per turn, a user with 100 daily credits can make up to 10 main-only requests if no other assistants are used.

---

## Relationships Diagram

```
users (1) ←──→ (0..1) user_promo_codes ←──→ (1) promo_codes
  │
  ├──→ (0..*) sessions
  │
  ├──→ (0..*) messages
  │
  ├──→ (0..*) files
  │
  ├──→ (0..*) feedbacks
  │
  └──→ (0..*) rate_limits
```

**Cardinality:**
- One user can have **zero or one** promo code
- One user can have **many** sessions
- One user can have **many** messages
- One user can have **many** files
- One user can have **many** feedbacks
- One user can have **many** rate limit records (one per day)

---

## Configuration Settings

All configurable via environment variables in `.env`:

### Credit System
```bash
DAILY_CREDITS_LIMIT=100              # Default daily credits for users
CREDIT_COST_MAIN_ASSISTANT=10        # Credits for main assistant
CREDIT_COST_SOLIQ_ASSISTANT=15       # Credits for soliq assistant
```

### Database
```bash
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=wakilai
USERS_COLLECTION=users
SESSIONS_COLLECTION=sessions
MESSAGES_COLLECTION=messages
FEEDBACK_COLLECTION=feedbacks
FILES_COLLECTION=files
```

### Session Management
```bash
TELEGRAM_SESSION_TIMEOUT=259200      # 3 days in seconds
```

---

## API Endpoints Summary

### Rate Limits
- `GET /api/admin/rate-limit/{user_id}` - Get user's remaining credits
- `POST /api/admin/rate-limit/reset` - Reset user's daily credits (admin)

### Promo Codes
- `POST /api/admin/promo-codes` - Create promo code
- `GET /api/admin/promo-codes` - List all promo codes
- `GET /api/admin/promo-codes/{code}` - Get specific promo code
- `PATCH /api/admin/promo-codes/{code}/activate` - Activate promo code
- `PATCH /api/admin/promo-codes/{code}/deactivate` - Deactivate promo code
- `DELETE /api/admin/promo-codes/{code}` - Delete promo code

### User Promo Code Assignment
- `POST /api/admin/promo-codes/assign` - Assign promo code to user
- `DELETE /api/admin/promo-codes/assign/{user_id}` - Remove user's promo code
- `GET /api/admin/promo-codes/users` - List all users with promo codes
- `GET /api/admin/promo-codes/users/{user_id}` - Get user's promo code

### Chat
- `POST /api/chat/ask` - Ask question to main or soliq assistant
- `POST /api/chat/ask-file` - Ask question about uploaded file
- `POST /api/chat/agent` - Agentic RAG (if exposed on your deployment)
- `POST /api/chat/agent/stream` - Streaming two-stage RAG (same pipeline as main; billed as main)

---

## Data Retention & Cleanup

### Automatic Cleanup (Recommended)
- **rate_limits**: Delete records older than 30 days
- **sessions**: Delete expired sessions (expires_at < now)
- **messages**: Archive messages older than 1 year

### Manual Cleanup
MongoDB TTL indexes can be used for automatic cleanup:

```javascript
// Auto-delete rate_limits after 30 days
db.rate_limits.createIndex(
  { "created_at": 1 }, 
  { expireAfterSeconds: 2592000 } // 30 days
)

// Auto-delete expired sessions
db.sessions.createIndex(
  { "expires_at": 1 }, 
  { expireAfterSeconds: 0 }
)
```

---

## Security Considerations

1. **User Authentication**: All requests require valid session token
2. **Rate Limiting**: Prevents abuse via credit system
3. **Promo Code Validation**: Checks active status and expiration
4. **Session Expiration**: Auto-expires after 3 days
5. **Admin Endpoints**: Should be protected with admin authentication (not implemented yet)

---

## Future Enhancements

1. **Payment Integration**: Link promo codes to payment system
2. **Usage Analytics**: Track usage patterns per user/assistant
3. **Credit Top-ups**: Allow users to purchase additional credits
4. **Tiered Plans**: Different credit limits for different subscription tiers
5. **Referral System**: Bonus credits for referrals
6. **Credit History**: Detailed log of all credit transactions

---

**Last Updated**: January 1, 2026
**Version**: 5.0.0
