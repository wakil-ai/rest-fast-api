# Infrastructure Overview

This document summarizes the core infrastructure components required to run the WakilAI API.
For step-by-step setup, see [deployment.md](deployment.md) and the root `compose.yml`.

## Core Services

| Component | Purpose | Notes |
| --- | --- | --- |
| FastAPI API | HTTP + WebSocket API service | Built from the `Dockerfile`, served by Uvicorn |
| Redis | Caching + LangGraph checkpoints | Provided in `compose.yml` (Redis Stack) |
| MongoDB | Primary data store | Users, sessions, messages, files, promos, subscriptions |
| Vector DB | Semantic search | Milvus (default) or Pinecone (cloud) |
| Object Storage | File uploads | Google Cloud Storage (GCS) |

## Local Docker Topology

`compose.yml` runs the API and Redis containers:

- **API service**
  - Image: `humblebeeai/wakil-ai-rest-api:latest`
  - Port mapping: `8080-8083:8080`
  - Healthcheck: `GET http://localhost:8080/health`
- **Redis Stack**
  - Image: `redis/redis-stack-server:7.4.0-v3`
  - Port: `6379`

MongoDB and Milvus are provisioned separately; see [deployment.md](deployment.md).

## Configuration

- Environment variables are defined in `.env` (see `.env.example` for defaults).
- `API_PREFIX` defaults to `/api/v2`.
- Vector DB selection is configured with `VECTOR_DB_TYPE` (`milvus` or `pinecone`).

## External Dependencies

The API integrates with external LLM and speech providers based on configuration:

- LLM providers: OpenAI, Novita, local vLLM, DeepInfra, SiliconFlow
- Speech-to-text: Google Cloud Speech or Azure Speech

Ensure credentials are supplied via `.env` before running the service.
