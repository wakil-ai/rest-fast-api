# Data Flow Diagram - WakilAI REST API

This document provides comprehensive information about data flows throughout the WakilAI system, explaining where different types of information are stored, processed, and retrieved.

## Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Data Storage Locations](#data-storage-locations)
3. [Major Data Flows](#major-data-flows)
4. [External System Integration](#external-system-integration)
5. [Data Persistence Patterns](#data-persistence-patterns)

---

## System Architecture Overview

The WakilAI system consists of multiple interconnected components that work together to provide legal AI assistance and document processing services.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         API Layer                                    │
│  (FastAPI - /app/api/v1, /app/api/v2)                              │
│  - Authentication endpoints                                         │
│  - Chat/conversation endpoints                                      │
│  - File management endpoints                                        │
│  - Admin/settings endpoints                                         │
└────────────────┬────────────────────────────────────────────────────┘
                 │
     ┌───────────┼───────────┬──────────────┬──────────────┐
     │           │           │              │              │
     ▼           ▼           ▼              ▼              ▼
┌─────────┐ ┌────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐
│ Auth    │ │ Chat   │ │File      │ │Payment     │ │Rate Limiting │
│Service  │ │Service │ │Management│ │Service     │ │Service       │
│         │ │        │ │Service   │ │            │ │              │
└────┬────┘ └───┬────┘ └────┬─────┘ └─────┬──────┘ └──────┬───────┘
     │          │           │            │               │
     │          │           │            │               │
     ▼          ▼           ▼            ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Data Persistence Layer                         │
├──────────┬──────────┬──────────┬──────────┬──────────┬──────────┤
│ MongoDB  │ Milvus   │ Neo4j    │ Redis    │ GCS      │ Pinecone │
│ (Primary)│(Vector   │(Graph    │(Session  │(Files)   │(Vector   │
│ (Users,  │ DB for   │Database) │Checkpt)  │          │ DB Alt)  │
│ Sessions,│ Doc      │          │          │          │          │
│ Messages)│ Vectors) │          │          │          │          │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

---

## Data Storage Locations

### 1. MongoDB Collections

MongoDB serves as the primary relational data store for the system.

| Collection | Purpose | Key Fields | Indexed Fields |
|-----------|---------|-----------|-----------------|
| **users** | User accounts and profiles | user_id, email, phone, external_id | external_id (unique sparse) |
| **sessions** | API session tracking | session_id, user_id, created_at | user_id, created_at |
| **messages** | Chat conversations | message_id, chat_id, user_id, content, role | chat_id, user_id, created_at |
| **files** | Uploaded file metadata | file_id, user_id, filename, gcs_path, vector_ids | user_id, file_id |
| **creditusage** | Daily credit tracking | user_id, date, credits_used | user_id + date (compound) |
| **promos** | Promotional code definitions | promo_code, discount_percent, daily_limit | promo_code |
| **user_promos** | User-promo associations | user_id, promo_id, activated_at | user_id |
| **subscriptions** | Subscription records | subscription_id, user_id, plan_id | user_id |
| **transactions** | Payment transactions | transaction_id, user_id, amount, status | user_id, status |
| **projects** | User projects/workspaces | project_id, user_id, name | user_id |
| **telegram_chats** | Telegram integration | chat_id, user_id, telegram_id | user_id, telegram_id |
| **referral_sources** | Referral tracking | source_id, user_id, source_type | user_id, source_type |
| **fingerprints** | Device fingerprints | fingerprint_id, user_id, fingerprint_hash | user_id, fingerprint_hash |
| **token_counts** | Token usage tracking | user_id, model, count, timestamp | user_id, timestamp |

**Storage Location**: MongoDB Atlas or self-hosted MongoDB instance  
**Access Pattern**: Synchronous via MongoDB driver in Python  
**Data Lifespan**: Long-term persistent storage

### 2. Vector Databases

Vector embeddings for document retrieval are stored in specialized vector databases.

#### Milvus (Primary Vector DB)
```
Collections:
├── lexuz              - Lex Uz legal database documents
├── soliq              - SOLIQ tax and business registry
├── project_files      - User-uploaded documents
├── mamuriy_sud        - Civil court cases
├── shartnoma          - Contract database
├── economic_court     - Economic court cases
├── civil_court        - Civil court cases
└── criminal_court     - Criminal court cases
```

**Storage Location**: Milvus server (vector indexing)  
**Indexing**: HNSW (Hierarchical Navigable Small World) for similarity search  
**Access Pattern**: Vector similarity queries via MilvusService  
**Data Lifespan**: Long-term, frequently accessed

#### Pinecone (Fallback/Alternative)
- Alternative vector database configured as backup
- Same collection structure as Milvus
- Used when Milvus is unavailable

### 3. Neo4j Graph Database

Stores relationship graphs for criminal cases and complex legal relationships.

```
Nodes:
├── Case
├── Person
├── Organization
├── Evidence
└── Location

Relationships:
├── INVOLVED_IN
├── PROSECUTED_BY
├── REPRESENTED_BY
├── MENTIONS
└── RELATED_TO

Indexes:
├── Full-text indexes on case descriptions
└── Vector indexes for semantic search on case content
```

**Storage Location**: Neo4j database instance  
**Access Pattern**: Cypher queries via Neo4j driver  
**Data Lifespan**: Long-term persistent

### 4. Redis

Serves as distributed cache and session state backend.

```
Keys Structure:
├── checkpoint:*                    - LangGraph conversation checkpoints
├── rate_limit:user_id:timestamp   - Rate limiting counters
├── cache:*                         - Temporary cache entries
└── session:session_id             - Session state data
```

**Storage Location**: Redis server (in-memory with persistence)  
**Access Pattern**: Asynchronous via AsyncRedisSaver (LangGraph integration)  
**Data Lifespan**: Transient with optional TTL (typically hours to days)  
**Purpose**: Chat session state, conversation checkpoints, rate limit tracking

### 5. Google Cloud Storage (GCS)

Stores uploaded files and documents.

```
Bucket Structure:
├── /users/{user_id}/
│   └── {file_id}/
│       ├── original_file
│       ├── processed_version
│       └── metadata.json
├── /temp/
│   └── processing_cache/
└── /backups/
    └── archives/
```

**Storage Location**: Google Cloud Storage bucket (configured via GCS_BUCKET_NAME)  
**Access Pattern**: Asynchronous via StorageService  
**File Size**: Supports up to file_max_size_mb configuration  
**Data Lifespan**: Long-term, versioned storage

### 6. LLM Context Memory (Mem0AI)

Persistent user memory and context for enhanced conversations.

**Storage Location**: Mem0AI service (external)  
**Data Type**: Structured user memories, preferences, conversation context  
**Access Pattern**: REST API calls via MemoryService  
**Data Lifespan**: User-managed, can be updated/deleted

---

## Major Data Flows

### 1. User Authentication Flow

```
User Request
    │
    ▼
┌─────────────────────────────────────┐
│ /api/v2/auth/login or              │
│ /api/v2/auth/telegram-auth         │
│ /api/v2/auth/dt-auth               │
└────────┬────────────────────────────┘
         │
         ▼
    ┌────────────────────┐
    │ Identify Provider  │
    └────┬───────────────┘
         │
    ┌────┴────────────────────────────────┐
    │                                     │
    ▼                                     ▼
┌──────────────┐              ┌──────────────────┐
│ Google OAuth │              │ Telegram/DT Auth │
└──────┬───────┘              └────────┬─────────┘
       │                               │
       ▼                               ▼
┌──────────────────────────────────────────────┐
│ Check user in MongoDB (users collection)     │
└────┬─────────────────────────────────────────┘
     │
     ├─ New user? → Create user record
     │
     └─ Existing? → Update last_login
             │
             ▼
    ┌─────────────────────────────┐
    │ Create/Update Session       │
    │ (sessions collection)        │
    └────┬────────────────────────┘
         │
         ▼
    ┌─────────────────────────────┐
    │ Generate JWT Token          │
    │ Store in Redis session      │
    └────┬────────────────────────┘
         │
         ▼
    ┌─────────────────────────────┐
    │ Return auth response        │
    │ (token, user_id, metadata)  │
    └─────────────────────────────┘
```

**Data Saved**: 
- User record in MongoDB (users collection)
- Session metadata in MongoDB (sessions collection)  
- JWT token in Redis (session cache)
- External ID mapping stored (external_id unique sparse index)

---

### 2. Chat Request Processing Flow

```
User Message Request
    │
    ▼
┌────────────────────────────────┐
│ /api/v1/chat or                │
│ /api/v2/chat/invoke            │
│ /api/v2/chat/stream-invoke     │
└────┬───────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Validate Authentication      │
│ Check JWT in Redis           │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────────┐
│ Check Rate Limiting              │
│ (creditusage collection)         │
│ Verify daily credit allowance    │
└────┬─────────────────────────────┘
     │
     ├─ Over limit? → Return error
     │
     └─ Under limit?
             │
             ▼
    ┌──────────────────────────────┐
    │ LangGraph Orchestration       │
    │ Create/retrieve conversation │
    │ checkpoint from Redis         │
    └────┬─────────────────────────┘
         │
         ▼
    ┌──────────────────────────────┐
    │ Intent Classification Agent   │
    │ Determine question type       │
    │ (tax, court, contract, etc.)  │
    └────┬─────────────────────────┘
         │
         ├─ Tax/Business Query?
         │       │
         │       ▼
         │  ┌────────────────────────┐
         │  │ Milvus Vector Search   │
         │  │ Collection: soliq      │
         │  └────────────────────────┘
         │
         ├─ Criminal Case?
         │       │
         │       ▼
         │  ┌────────────────────────┐
         │  │ Neo4j Graph Search     │
         │  │ + Vector similarity    │
         │  └────────────────────────┘
         │
         ├─ General Knowledge?
         │       │
         │       ▼
         │  ┌────────────────────────┐
         │  │ Web Search (Tavily)    │
         │  │ Fallback search        │
         │  └────────────────────────┘
         │
         └─ Document Analysis?
                 │
                 ▼
          ┌────────────────────────┐
          │ Milvus Vector Search   │
          │ Collection:            │
          │ project_files          │
          └────────────────────────┘
                 │
                 ▼
         ┌─────────────────────────────────┐
         │ LLM Processing                  │
         │ (OpenAI, Gemini, or local VLLM)│
         │ Generate response with context  │
         └────┬────────────────────────────┘
              │
              ▼
         ┌─────────────────────────────────┐
         │ Store Message in MongoDB        │
         │ (messages collection)           │
         │ - user_id, chat_id, content     │
         │ - role (user/assistant)         │
         │ - timestamp                     │
         └────┬────────────────────────────┘
              │
              ▼
         ┌─────────────────────────────────┐
         │ Update Credit Usage             │
         │ (creditusage collection)        │
         │ Deduct credits based on model   │
         │ and processing complexity       │
         └────┬────────────────────────────┘
              │
              ▼
         ┌─────────────────────────────────┐
         │ Save Checkpoint to Redis        │
         │ (for conversation continuity)   │
         └────┬────────────────────────────┘
              │
              ▼
         ┌─────────────────────────────────┐
         │ Update Mem0AI User Memory       │
         │ (if applicable)                 │
         └────┬────────────────────────────┘
              │
              ▼
         ┌─────────────────────────────────┐
         │ Return Response to User         │
         │ (streaming or direct)           │
         └─────────────────────────────────┘
```

**Data Saved**:
- Message in MongoDB (messages collection)
- Credit usage tracked in MongoDB (creditusage collection)
- Conversation checkpoint in Redis (AsyncRedisSaver)
- User memory updated in Mem0AI (external)
- Token count logged (token_counts collection)

---

### 3. File Upload and Indexing Flow

```
User Uploads File
    │
    ▼
┌──────────────────────────────┐
│ /api/v2/files/upload         │
│ Multipart form-data          │
└────┬───────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Validate Authentication      │
│ Check user permissions       │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Store File in GCS            │
│ Path: users/{user_id}/{fid}  │
│ Preserve original filename   │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Extract Text from File       │
│ (OCR if image via Datalab)   │
│ (PDF parsing if document)    │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Generate Embeddings          │
│ Split into chunks with       │
│ overlap strategy             │
│ Create vectors via           │
│ embedding model (Qwen/Ada)   │
└────┬─────────────────────────┘
     │
     ├─────────────────────────────┐
     │                             │
     ▼                             ▼
┌─────────────────┐      ┌──────────────────┐
│ Index in Milvus │      │ Index in Pinecone│
│ Collection:     │      │ (if configured)  │
│ project_files   │      │                  │
└────┬────────────┘      └──────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Store File Metadata in       │
│ MongoDB (files collection)   │
│ - file_id, user_id          │
│ - filename, file_type       │
│ - gcs_path                  │
│ - vector_ids (references)   │
│ - created_at, updated_at    │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Return File Metadata         │
│ (file_id, status, indexing) │
└──────────────────────────────┘
```

**Data Saved**:
- File stored in Google Cloud Storage (GCS_BUCKET_NAME)
- File metadata in MongoDB (files collection)
- Vector embeddings in Milvus (project_files collection)
- Optional: Pinecone (if configured as backup)
- Processing state tracked in file record

---

### 4. Payment and Subscription Processing Flow

```
User Initiates Payment
    │
    ▼
┌──────────────────────────────┐
│ /api/v2/payments/subscribe   │
│ /api/v2/payments/process     │
└────┬───────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Create Payment Intent        │
│ with amount, plan_id, etc.   │
└────┬─────────────────────────┘
     │
     ├─ Payme Payment?
     │       │
     │       ▼
     │  ┌──────────────────┐
     │  │ Payme API Call   │
     │  └──────────────────┘
     │
     └─ Click Payment?
             │
             ▼
         ┌──────────────────┐
         │ Click API Call   │
         └──────────────────┘
              │
              ▼
    ┌──────────────────────────────┐
    │ Receive Webhook Callback     │
    │ Payment provider → API       │
    └────┬─────────────────────────┘
         │
         ▼
    ┌──────────────────────────────┐
    │ Verify Payment Signature     │
    │ Validate transaction         │
    └────┬─────────────────────────┘
         │
         ├─ Payment Approved?
         │       │
         │       ▼
         │  ┌──────────────────────────┐
         │  │ Create Transaction       │
         │  │ (transactions collection)│
         │  │ Status: confirmed        │
         │  └─────┬────────────────────┘
         │        │
         │        ▼
         │  ┌──────────────────────────┐
         │  │ Create/Update            │
         │  │ Subscription             │
         │  │ (subscriptions coll.)    │
         │  │ Set plan, expiry_date    │
         │  └─────┬────────────────────┘
         │        │
         │        ▼
         │  ┌──────────────────────────┐
         │  │ Update User Credits      │
         │  │ Add subscription credits │
         │  │ Reset daily limit        │
         │  └─────┬────────────────────┘
         │        │
         │        ▼
         │  ┌──────────────────────────┐
         │  │ Sync to Bitrix24         │
         │  │ Webhook: new customer    │
         │  └──────────────────────────┘
         │
         └─ Payment Failed?
                 │
                 ▼
         ┌──────────────────────────────┐
         │ Store Failed Transaction     │
         │ (transactions collection)    │
         │ Status: failed               │
         │ Notify user                  │
         └──────────────────────────────┘
```

**Data Saved**:
- Transaction record in MongoDB (transactions collection)
- Subscription record in MongoDB (subscriptions collection)
- User credit allocation updated in MongoDB (users collection)
- Webhook event logged for audit trail
- Bitrix24 CRM notified (external sync)

---

### 5. Rate Limiting and Credit System Flow

```
API Request Received
    │
    ▼
┌──────────────────────────────┐
│ Extract user_id from JWT     │
│ Verify token in Redis        │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Check Rate Limit             │
│ (creditusage collection)     │
│ Query: user_id + today date  │
└────┬─────────────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Calculate Available Credits  │
│ - Subscription plan limit    │
│ - Promo code adjustments     │
│ - Daily reset time           │
└────┬─────────────────────────┘
     │
     ├─ Credits Available?
     │       │
     │       └─ No → Return 429 (Too Many Requests)
     │
     └─ Yes
             │
             ▼
    ┌──────────────────────────────┐
    │ Process Request              │
    │ Deduct estimated credits     │
    │ (may vary by model)          │
    └────┬─────────────────────────┘
         │
         ▼
    ┌──────────────────────────────┐
    │ After Processing:            │
    │ Calculate Actual Cost        │
    │ (tokens used, complexity)    │
    └────┬─────────────────────────┘
         │
         ▼
    ┌──────────────────────────────┐
    │ Update Credit Usage          │
    │ (creditusage collection)     │
    │ - user_id, date, cost        │
    └────┬─────────────────────────┘
         │
         ▼
    ┌──────────────────────────────┐
    │ Log Token Count              │
    │ (token_counts collection)    │
    │ - model, token_count, date   │
    └──────────────────────────────┘
```

**Credit Costs**:
- Main assistant: 10 credits per request
- Tax/Soliq specialist: 20 credits per request
- Court specialist: 25 credits per request
- Contract analyzer: 25 credits per request

**Data Saved**:
- Credit usage in MongoDB (creditusage collection)
- Token counts in MongoDB (token_counts collection)
- Rate limit state in Redis (rate_limit:user_id:timestamp)

**Indexes**:
- creditusage: user_id + date (compound index) for daily lookups

---

## External System Integration

### 1. LLM Providers

```
Query Routing:
├── OpenAI (GPT-5.2, GPT-4.1-mini)
│   └── Primary provider for most queries
├── Gemini
│   └── Alternative provider
├── Novita AI
│   └── Fallback provider
└── Local VLLM
    └── For on-premise deployments
```

**Data Flow**: 
- Prompt + context sent to LLM
- Response received and formatted
- Token count tracked locally

### 2. Embedding Models

```
Vector Generation:
├── Qwen (Primary)
├── OpenAI ada
├── Novita Qwen
├── DeepInfra
└── SiliconFlow
```

**Data Flow**:
- Document text chunked
- Chunks sent to embedding model
- Vectors stored in Milvus/Pinecone

### 3. Speech-to-Text Integration

```
Audio Processing:
├── Google Cloud Speech-to-Text
└── Azure Cognitive Services

Data Flow:
Audio File → Speech API → Text Transcription → Store in messages → Process as text query
```

### 4. Payment Providers

```
Payment Processing:
├── Payme
│   ├── Webhook: /api/v2/payments/payme-webhook
│   └── Status: confirmed/cancelled/failed
│
└── Click
    ├── Webhook: /api/v2/payments/click-webhook
    └── Status: confirmed/cancelled/failed

Data Stored:
- Transaction ID, amount, status in MongoDB
- Provider reference ID for reconciliation
```

### 5. Telegram Integration

```
Telegram Bot:
├── OAuth authentication via Telegram ID
├── Chat storage in telegram_chats collection
├── Message relay between Telegram and API
└── User context maintained across platforms

Data Stored:
- chat_id, telegram_id in telegram_chats collection
- Reference to main user record
```

### 6. DT (OneID) Integration

```
DT Authentication:
├── Business/Individual verification
├── OAuth flow via DT endpoints
└── External ID mapping in users collection

Data Stored:
- external_id (DT ID) - unique sparse index
- dt_metadata in user record
```

### 7. Bitrix24 CRM Webhook

```
Bitrix24 Sync:
├── New subscription → Create/Update contact
├── Payment received → Log activity
└── User activity → Track interactions

Data Flow:
└─ API → Bitrix24 webhook endpoint
   └─ CRM automatically updated
```

### 8. Mem0AI Memory Service

```
User Memory Persistence:
├── Store conversation context
├── Track user preferences
├── Maintain learning state
└── Enhance response personalization

Data Flow:
API → Mem0AI service → Memory stored and retrieved as needed
```

### 9. Datalab API (OCR)

```
Document Processing:
├── Image/document upload
├── Text extraction via Datalab
├── Store extracted text
└── Generate embeddings from text

Data Flow:
File → Datalab API → Text extraction → Embeddings → Vector DB
```

### 10. Tavily Web Search

```
Fallback Search:
├── Query when knowledge base insufficient
├── Search internet for current information
├── Augment response with web results

Data Flow:
Unknown query → Tavily API → Web results → Include in context → LLM response
```

---

## Data Persistence Patterns

### Session State Management

```
Conversation Continuity:
User → Request → LangGraph Orchestration
                    │
                    ├─ Check Redis for conversation checkpoint
                    │  (AsyncRedisSaver)
                    │
                    ├─ Load previous context
                    │
                    ├─ Process new message
                    │
                    └─ Save new checkpoint to Redis
```

**Redis Key Structure**:
```
checkpoint:{conversation_id}:{turn_number}
```

**TTL**: Configured per installation, typically 24-72 hours

### Async Processing Pattern

```
Long-running Operations:
Request → Queue in background → Worker processes
    │
    ├─ Generate embeddings
    ├─ OCR document
    ├─ Process large file
    │
    └─ Update status in MongoDB when complete
```

### Data Archival Pattern

```
Old Data Management:
├── Messages older than configured period → Archive in GCS
├── Expired sessions → Clean from Redis
└── Inactive users → Archive to backup storage
```

---

## Summary: Where Data is Saved

| Data Type | Primary Storage | Secondary | Purpose |
|-----------|-----------------|-----------|---------|
| User profiles | MongoDB (users) | Redis cache | Account management, authentication |
| Chat messages | MongoDB (messages) | Redis (checkpoint) | Conversation history, context |
| User files | GCS bucket | MongoDB metadata | Document storage, retrieval |
| File embeddings | Milvus/Pinecone | - | Semantic search, retrieval |
| Graph relationships | Neo4j | - | Case relationship mapping |
| Session state | Redis | MongoDB | Conversation continuity |
| Credit tracking | MongoDB (creditusage) | Redis (current) | Rate limiting, billing |
| Transactions | MongoDB (transactions) | Payment provider | Payment history, audit |
| User memory | Mem0AI | - | Persistent context, personalization |
| Promo codes | MongoDB (promos) | Redis cache | Discount management |
| Rate limits | Redis | MongoDB (logs) | Request throttling |
| Token counts | MongoDB (token_counts) | - | Usage analytics, billing |
| Subscriptions | MongoDB (subscriptions) | - | Plan management, expiry |

---

## Related Documentation

- [Database Architecture](./database-architecture.md) - Detailed MongoDB schemas and relationships
- [README](../README.md) - Technology stack overview
- [API Documentation](../API.md) - Endpoint specifications and request/response formats

