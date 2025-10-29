# WakilAI API

A comprehensive legal document retrieval and conversational AI system designed for Uzbek legal documents, powered by advanced language models, vector databases, and real-time speech processing capabilities.

## 🚀 Overview

WakilAI API provides intelligent legal document search, question-answering, and conversational capabilities with persistent memory. It combines vector database retrieval with large language models to deliver accurate, context-aware responses to legal queries in Uzbek, with support for real-time speech-to-text processing, user management, and conversation history.

## 📚 Tech Stack

### Backend Framework
- **FastAPI** - Modern, fast web framework for building APIs
- **Python 3.11** - Programming language
- **Uvicorn** - ASGI web server with multi-worker support
- **WebSockets** - Real-time bidirectional communication

### AI & Machine Learning
- **LangChain** - Framework for LLM application development
- **LangGraph** - Advanced workflow orchestration
- **OpenAI API** - GPT models for text generation
- **Novita AI** - Alternative LLM provider (Gemma models)
- **Local VLLM** - Self-hosted language models
- **Qwen Embeddings** - Advanced text embedding models
- **FastText** - Language detection and processing
- **Mem0AI** - Persistent memory management

### Speech Processing
- **Google Cloud Speech-to-Text** - Enterprise speech recognition
- **Azure Cognitive Services Speech** - Alternative STT provider
- **PyDub** - Audio processing and manipulation
- **Real-time WebSocket STT** - Live speech transcription

### Databases & Storage
- **MongoDB** - Document storage, full-text search, and user management
- **Milvus** - High-performance vector database for similarity search
- **Pinecone** - Cloud vector database alternative
- **TikToken** - Token counting and text analysis

### Authentication & Security
- **API Key Authentication** - Secure API access
- **HTTP Basic Auth** - Documentation access protection
- **Telegram Bot Integration** - Social authentication
- **Cryptography** - Advanced data encryption utilities
- **CORS Middleware** - Cross-origin request handling

### Additional Tools
- **Pydantic** - Data validation and settings management
- **Loguru** - Advanced structured logging
- **Docker** - Production containerization
- **UzTransliterator** - Uzbek text processing

## 🛠️ API Endpoints

### Health Check
- `GET /` - API health status and system information

### Chat Endpoints
- `POST /api/chat/ask` - Ask legal questions with RAG (Retrieval Augmented Generation)
- `GET /api/chat/model-info` - Get current model configuration and capabilities

### Retrieval & Search Endpoints
- `POST /api/retrieval/mongo` - Full-text search in MongoDB
- `POST /api/retrieval/mongo-metadata` - Metadata-based search in MongoDB
- `POST /api/retrieval/search-hybrid` - Hybrid vector search (dense + sparse)
- `POST /api/retrieval/search-dense` - Dense vector search only
- `POST /api/retrieval/search-sparse` - Sparse vector search only
- `POST /api/retrieval/search-specific` - Specific document search

### User Management & History
- `POST /api/chat_history/create/user/` - Create new user profile
- `POST /api/chat_history/create/session/` - Create new chat session
- `GET /api/chat_history/sessions/{user_id}` - List user's chat sessions
- `POST /api/chat_history/add/message/` - Add message to session
- `GET /api/chat_history/messages/{user_id}/{session_id}` - Get session messages
- `POST /api/chat_history/submit/feedback/` - Submit response feedback
- `GET /api/chat_history/feedback/{user_id}/{session_id}/{message_id}` - Get feedback details

### Memory Management
- `GET /api/memory/user/{user_id}/` - Get all user memories
- `POST /api/memory/save/` - Save interaction to persistent memory
- `PUT /api/memory/update/{memory_id}` - Update specific memory
- `DELETE /api/memory/user/{user_id}/` - Delete all user memories
- `DELETE /api/memory/{memory_id}/` - Delete specific memory

### Speech-to-Text Services
- `POST /api/speech_to_text/transcribe` - Upload audio file for transcription
- `WS /api/ws/stt` - Real-time WebSocket speech-to-text streaming

### Utility Services
- `POST /api/count/tokens` - Count tokens in text using TikToken

### Authentication
- `GET /login` - Telegram bot authentication endpoint

### Documentation (Protected)
- `GET /docs` - Swagger UI interactive documentation
- `GET /redoc` - ReDoc documentation
- `GET /openapi.json` - OpenAPI schema specification

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Docker (optional)
- MongoDB instance
- Vector database (Milvus or Pinecone)

### Environment Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd api.wakilai
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   ```

3. **Configure environment variables** (`.env` file):
   ```env
   # App Configuration
   APP_NAME=WakilAI Chatbot
   VERSION=5.0.0
   DEBUG=false
   
   # Authentication & Security
   API_KEY_NAME=x-api-key
   API_KEY=your-api-key-here
   DOCS_USER=admin
   DOCS_PASSWORD=secure-password
   
   # Database Configuration
   MONGODB_URI=mongodb://localhost:27017
   MONGODB_DB_NAME=wakilai
   COLLECTION_NAME=itemdocs
   USERS_COLLECTION=users
   SESSIONS_COLLECTION=sessions
   MESSAGES_COLLECTION=messages
   FEEDBACK_COLLECTION=feedbacks
   
   # Vector Database (choose one)
   VECTOR_DB_TYPE=milvus  # or pinecone
   
   # Milvus Configuration
   MILVUS_URI=http://localhost:19530
   MILVUS_MAIN_NAME=lexuz
   MILVUS_USER=optional-username
   MILVUS_PASSWORD=optional-password
   
   # Or Pinecone Configuration
   PINECONE_API_KEY=your-pinecone-key
   PINECONE_INDEX_NAME=your-index
   NAMESPACE_NAME=lexuz
   
   # Memory Management
   MEM0_API_KEY=your-mem0-api-key
   
   # LLM Configuration
   LLM_PROVIDER=novita  # novita, local
   
   # Novita AI
   NOVITA_API_KEY=your-novita-key
   NOVITA_MODEL=google/gemma-3-27b-it
   NOVITA_EMBEDDING_BASE_URL=https://api.novita.ai/openai
   NOVITA_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
   
   # OpenAI Configuration
   OPENAI_API_KEY=your-openai-key
   GPT_COMPLETION_MODEL=gpt-4o
   OPENAI_EMBEDDING_MODEL=text-embedding-ada-002
   
   # Local VLLM Configuration
   LOCAL_VLLM_BASE_URL=http://localhost:8000
   LOCAL_VLLM_MODEL=gpt-oss-120b
   LOCAL_VLLM_API_KEY=sk-no-key-required
   
   # DeepInfra Configuration
   DEEPINFRA_API_KEY=your-deepinfra-key
   DEEPINFRA_EMBEDDING_BASE_URL=https://api.deepinfra.com/v1/openai
   DEEPINFRA_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B
   
   # Embedding Configuration
   EMBEDDING_MODEL=qwen  # qwen, openai, novita_qwen, deepinfra
   
   # Qwen VLLM Embedding (if using local)
   QWEN_EMBEDDING_URL=http://localhost:8001
   QWEN_EMBEDDING_MODEL=qwen-embedding
   
   # Speech-to-Text Configuration
   SPEECH_TO_TEXT_PROVIDER=azure  # google, azure
   
   # Azure Speech Services
   AZURE_SPEECH_KEY=your-azure-speech-key
   AZURE_SPEECH_REGION=your-azure-region
   
   # Telegram Authentication
   TELEGRAM_BOT_TOKEN=your-telegram-bot-token
   TELEGRAM_BOT_LOGIN=your-bot-username
   TELEGRAM_SESSION_TIMEOUT=259200  # 3 days in seconds
   
   # Performance & Behavior Settings
   STREAM=true
   TOP_K=10
   ALPHA=0.8
   TEMPERATURE=0.1
   CHAT_HISTORY_LIMIT=5
   OUTPUT_MAX_TOKENS=8192
   ```

### Installation Methods

#### Method 1: Local Development

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the application**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```

#### Method 2: Docker

1. **Build and run with Docker**
   ```bash
   docker build -t wakilai-api .
   docker run -p 8080:8080 --env-file .env wakilai-api
   ```

### API Usage

#### Authentication
All API endpoints (except health check and docs) require an API key:

```bash
curl -H "x-api-key: your-api-key" http://localhost:8080/api/chat/ask
```

#### Example Chat Request
```bash
curl -X POST "http://localhost:8080/api/chat/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "query": "What are the marriage laws in Uzbekistan?",
    "user_id": "user123",
    "session_id": "session456", 
    "stream": false
  }'
```

#### Example WebSocket Speech-to-Text
```javascript
const ws = new WebSocket('ws://localhost:8080/api/ws/stt?api_key=your-api-key');

// Start transcription
ws.send(JSON.stringify({
  "event": "start",
  "provider": "azure", 
  "language": "uz-UZ",
  "hints": ["huquq", "qonun", "sud"]
}));

// Send audio data (PCM16 mono 16kHz)
ws.send(audioBuffer);

// Stop transcription
ws.send(JSON.stringify({"event": "stop"}));
```

#### Example Memory Interaction
```bash
curl -X POST "http://localhost:8080/api/memory/save/" \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "user_id": "user123",
    "query": "Marriage age requirements",
    "response": "In Uzbekistan, minimum marriage age is 18 years",
    "metadata": {"topic": "family_law"}
  }'
```

#### Example Response
```json
{
  "answer": "According to Uzbek family law, marriage is regulated by...",
  "sources": [
    {
      "content": "Marriage regulations in Uzbekistan...",
      "metadata": {"law": "Family Code", "article": "12"}
    }
  ],
  "session_id": "session456"
}
```

## 📖 API Documentation

Once the server is running, access interactive documentation:

- **Swagger UI**: http://localhost:8080/docs
- **ReDoc**: http://localhost:8080/redoc

*Note: Documentation is protected with basic authentication using `DOCS_USER` and `DOCS_PASSWORD`.*

## 🔧 Configuration

### Model Configuration
- `LLM_PROVIDER`: Choose between `novita` or `local` for language models
- `EMBEDDING_MODEL`: Select from `qwen`, `openai`, `novita_qwen`, `deepinfra`
- `VECTOR_DB_TYPE`: Choose between `milvus` or `pinecone`
- `SPEECH_TO_TEXT_PROVIDER`: Choose between `google` or `azure`

### Performance Tuning
- `TOP_K`: Number of documents to retrieve (default: 10)
- `ALPHA`: Hybrid search weighting (default: 0.8) 
- `TEMPERATURE`: LLM creativity level (default: 0.1)
- `STREAM`: Enable streaming responses (default: true)
- `CHAT_HISTORY_LIMIT`: Maximum conversation history (default: 5)
- `OUTPUT_MAX_TOKENS`: Maximum tokens in response (default: 8192)

### Memory Management
- `MEM0_API_KEY`: API key for persistent memory service
- `TELEGRAM_SESSION_TIMEOUT`: Session expiration time (default: 3 days)

### Audio Processing
- **Supported Formats**: WAV, MP3, M4A, FLAC, PCM16
- **Real-time Requirements**: PCM16 mono 16kHz for WebSocket STT
- **Language Support**: Uzbek (uz-UZ), English (en-US), Russian (ru-RU)

## 🐳 Docker Support

The application includes a production-ready Dockerfile with:
- **Multi-worker Uvicorn setup** with 4 workers for high concurrency
- **Optimized for streaming responses** with asyncio loop and httptools
- **Audio processing support** with ffmpeg and ALSA libraries  
- **Health check endpoints** for container orchestration
- **Proper signal handling** for graceful shutdowns
- **System dependencies** for cryptography, database drivers, and Azure Speech SDK
- **Lightweight base** using Python 3.11-slim for reduced image size

## 🔒 Security Features

- **API Key Authentication** for all protected endpoints
- **WebSocket Authentication** via query parameters
- **Basic Authentication** for documentation access
- **Telegram Bot Integration** for social authentication
- **CORS Configuration** for secure web client access
- **Request Validation** with Pydantic models
- **Cryptographic Security** for data encryption
- **Session Management** with configurable timeouts
- **Rate Limiting** and secure headers middleware

## 📝 Development

### Project Structure
```
app/
├── api/               # API route handlers
│   ├── auth.py        # Telegram authentication
│   ├── chat.py        # Chat and Q&A endpoints
│   ├── chat_history.py # User management and sessions
│   ├── count.py       # Token counting utilities
│   ├── memory.py      # Persistent memory management
│   ├── retrieval.py   # Search and retrieval
│   ├── speech_to_text.py # Audio transcription
│   └── ws_stt.py      # WebSocket speech-to-text
├── chains/            # LangChain configurations
├── core/              # Configuration and logging
├── db/                # Database handlers (MongoDB, Milvus, Pinecone)
├── llms/              # Language model providers
├── models/            # Pydantic models and schemas
├── retrieval/         # Document retrieval and embedding logic
├── services/          # Business logic and service layers
├── utils/             # Utility functions and helpers
└── assets/            # Static assets (FastText models, etc.)
```

### Adding New Features

1. **New API Endpoints**: Add routes in `app/api/` with proper authentication
2. **New Models**: Define Pydantic models in `app/models/` for request/response schemas
3. **New Services**: Implement business logic in `app/services/` following existing patterns
4. **New LLM Providers**: Add providers in `app/llms/` with base class inheritance
5. **New STT Providers**: Extend speech services in `app/services/streaming_speech_to_text.py`
6. **New Database Handlers**: Add handlers in `app/db/` with consistent interfaces

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is proprietary software. All rights reserved.

## 🚀 Recent Updates & Features

### Version 5.0.0 - Major Feature Release
- **�️ Real-time Speech-to-Text**: WebSocket-based live audio transcription
- **🧠 Persistent Memory**: Long-term conversation memory with Mem0AI
- **👥 User Management**: Complete user session and conversation tracking
- **🔐 Telegram Authentication**: Social login integration
- **📊 Advanced Analytics**: Token counting and usage tracking
- **🔍 Enhanced Search**: Improved hybrid and metadata search capabilities
- **🎯 Multi-language Support**: Uzbek, English, and Russian STT
- **⚡ Performance Optimization**: Multi-worker deployment with streaming

### Latest Capabilities
- **Multi-modal Interaction**: Text, voice, and real-time audio processing
- **Context Awareness**: Persistent memory across sessions
- **Scalable Architecture**: Microservices-based design with Docker support
- **Enterprise Features**: Advanced authentication, logging, and monitoring

## 🆘 Support & Resources

### Documentation & Help
- **Interactive API Docs**: http://localhost:8080/docs (Swagger UI)
- **Alternative Docs**: http://localhost:8080/redoc (ReDoc)
- **Health Check**: http://localhost:8080/ (API status)

### Getting Help
- Create an issue in the repository for bugs or feature requests
- Contact the development team for enterprise support
- Check the API documentation for endpoint details and examples
- Review configuration examples for setup guidance

### Monitoring & Debugging
- Check application logs for detailed error information
- Use `/api/chat/model-info` endpoint for system configuration details
- Monitor WebSocket connections for real-time service status
- Verify database connections and API key configurations

---

**Status**: 🚀 **WakilAI API v5.0.0** - Production-ready legal AI assistant with advanced conversational capabilities!