# WakilAI API

A legal document retrieval and chat API system designed for Uzbek legal documents, powered by advanced language models and vector databases.

## 🚀 Overview

WakilAI API provides intelligent legal document search and question-answering capabilities. It combines vector database retrieval with large language models to deliver accurate, context-aware responses to legal queries in Uzbek.

## 📚 Tech Stack

### Backend Framework
- **FastAPI** - Modern, fast web framework for building APIs
- **Python 3.11** - Programming language
- **Uvicorn** - ASGI web server implementation

### AI & Machine Learning
- **LangChain** - Framework for LLM application development
- **OpenAI API** - GPT models for text generation
- **Novita AI** - Alternative LLM provider (Gemma models)
- **Qwen Embeddings** - Text embedding models
- **FastText** - Language detection

### Databases & Storage
- **MongoDB** - Document storage and full-text search
- **Milvus** - Vector database for similarity search
- **Pinecone** - Alternative vector database option

### Security & Authentication
- **API Key Authentication** - Secure API access
- **HTTP Basic Auth** - Documentation access protection
- **Cryptography** - Data encryption utilities

### Additional Tools
- **Pydantic** - Data validation and settings management
- **Loguru** - Advanced logging
- **CORS Middleware** - Cross-origin request handling
- **Docker** - Containerization

## 🛠️ API Endpoints

### Health Check
- `GET /` - API health status

### Chat Endpoints
- `POST /api/chat/ask` - Ask legal questions with RAG (Retrieval Augmented Generation)
- `GET /api/chat/model-info` - Get current model configuration

### Retrieval Endpoints
- `POST /api/retrieval/mongo` - Full-text search in MongoDB
- `POST /api/retrieval/mongo-metadata` - Metadata-based search in MongoDB
- `POST /api/retrieval/search-hybrid` - Hybrid vector search (dense + sparse)
- `POST /api/retrieval/search-dense` - Dense vector search
- `POST /api/retrieval/search-sparse` - Sparse vector search
- `POST /api/retrieval/search-specific` - Specific document search

### Chat History Endpoints
- `POST /api/history/conversations` - Create new conversation
- `GET /api/history/conversations` - List user conversations
- `GET /api/history/conversations/{chat_id}` - Get specific conversation
- `PUT /api/history/conversations/{chat_id}` - Add message to conversation
- `DELETE /api/history/conversations/{chat_id}` - Archive conversation
- `POST /api/history/feedback` - Submit feedback for responses
- `GET /api/history/feedback/{chat_id}` - Get conversation feedback

### Documentation (Protected)
- `GET /docs` - Swagger UI documentation
- `GET /redoc` - ReDoc documentation
- `GET /openapi.json` - OpenAPI schema

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
   DEBUG=false
   
   # Authentication
   API_KEY_NAME=x-api-key
   API_KEY=your-api-key-here
   DOCS_USER=admin
   DOCS_PASSWORD=secure-password
   
   # Database Configuration
   MONGODB_URI=mongodb://localhost:27017
   MONGODB_DB_NAME=wakilai
   COLLECTION_NAME=itemdocs
   
   # Vector Database (choose one)
   VECTOR_DB_TYPE=milvus  # or pinecone
   
   # Milvus Configuration
   MILVUS_URI=http://localhost:19530
   MILVUS_COLLECTION_NAME=lexuz
   
   # Or Pinecone Configuration
   PINECONE_API_KEY=your-pinecone-key
   PINECONE_INDEX_NAME=your-index
   
   # LLM Configuration
   LLM_PROVIDER=novita  # or local
   
   # Novita AI
   NOVITA_API_KEY=your-novita-key
   NOVITA_MODEL=google/gemma-3-27b-it
   
   # Or OpenAI
   OPENAI_API_KEY=your-openai-key
   GPT_COMPLETION_MODEL=gpt-4o
   
   # Embedding Configuration
   EMBEDDING_MODEL=qwen  # qwen, openai, novita_qwen, deepinfra
   NOVITA_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
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
    "chat_history": [],
    "stream": false
  }'
```

#### Example Response
```json
{
  "answer": "According to Uzbek family law, marriage is regulated by..."
}
```

## 📖 API Documentation

Once the server is running, access interactive documentation:

- **Swagger UI**: http://localhost:8080/docs
- **ReDoc**: http://localhost:8080/redoc

*Note: Documentation is protected with basic authentication using `DOCS_USER` and `DOCS_PASSWORD`.*

## 🔧 Configuration

### Model Configuration
- `LLM_PROVIDER`: Choose between `novita` or `local`
- `EMBEDDING_MODEL`: Select from `qwen`, `openai`, `novita_qwen`, `deepinfra`
- `VECTOR_DB_TYPE`: Choose between `milvus` or `pinecone`

### Performance Tuning
- `TOP_K`: Number of documents to retrieve (default: 10)
- `ALPHA`: Hybrid search weighting (default: 0.8)
- `TEMPERATURE`: LLM creativity level (default: 0.1)
- `STREAM`: Enable streaming responses (default: true)

## 🐳 Docker Support

The application includes a production-ready Dockerfile with:
- Multi-worker Uvicorn setup
- Optimized for streaming responses
- Health check endpoints
- Proper signal handling

## 🔒 Security Features

- **API Key Authentication** for all endpoints
- **Basic Authentication** for documentation
- **CORS Configuration** for web clients
- **Request Validation** with Pydantic
- **Secure Headers** and middleware

## 📝 Development

### Project Structure
```
app/
├── api/           # API route handlers
├── chains/        # LangChain configurations
├── core/          # Configuration and logging
├── db/            # Database handlers
├── llms/          # Language model providers
├── models/        # Pydantic models
├── retrieval/     # Document retrieval logic
├── services/      # Business logic
└── utils/         # Utility functions
```

### Adding New Features

1. **New API Endpoints**: Add routes in `app/api/`
2. **New Models**: Define Pydantic models in `app/models/`
3. **New Services**: Implement business logic in `app/services/`
4. **New LLM Providers**: Add providers in `app/llms/`

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is proprietary software. All rights reserved.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the API documentation at `/docs`

---

**Status**: 🚀 WakilAI API is running and ready to serve legal document queries!