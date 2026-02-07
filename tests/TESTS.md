# WakilAI API Test Suite

This comprehensive test suite provides unit tests for all critical services in the WakilAI API, ensuring reliability and health monitoring.

## 🧪 Test Coverage

### Services Tested

1. **Embedding Service** (`test_embedding.py`)
   - SiliconFlow API connection
   - Query and document embedding computation
   - Batch processing
   - Error handling and connection failures
   - Singleton pattern verification

2. **Vector Database - Milvus** (`test_milvus.py`)
   - Connection and collection management
   - Dense and sparse vector searches
   - Hybrid search functionality
   - Data upsertion and partition handling
   - Result parsing and error handling

3. **LLM Service - OpenAI** (`test_openai.py`)
   - API connection and authentication
   - Chat completion (streaming and non-streaming)
   - Model-specific parameter handling
   - Error handling and response validation
   - Token management and limits

4. **Database - MongoDB** (`test_mongodb.py`)
   - Connection and database operations
   - CRUD operations (Create, Read, Update, Delete)
   - User and project history workflows
   - Singleton pattern verification
   - Connection failure handling

5. **Storage - Google Cloud Storage** (`test_gcp_storage.py`)
   - File upload and download
   - Signed URL generation
   - File archiving and deletion
   - Metadata operations
   - Complete file workflows

6. **Health Check Endpoint** (`test_health.py`)
   - Service status aggregation
   - Response time monitoring
   - Error handling and degraded status
   - Authentication-free access

## 🏥 Health Endpoint

The API includes a comprehensive health check endpoint at `/api/health` that monitors all services:

```bash
curl http://localhost:8000/api/health
```

**Response Format:**
```json
{
  "status": "ok|degraded",
  "timestamp": "2024-01-01T12:00:00.000Z",
  "services": {
    "embedding": {
      "status": "ok",
      "response_time": "0.150s",
      "embedding_dim": 1536
    },
    "milvus": {
      "status": "ok", 
      "response_time": "0.050s",
      "collections": ["main", "soliq", "projects"]
    },
    "openai": {
      "status": "ok",
      "response_time": "0.300s",
      "model": "gpt-4",
      "response_length": 42
    },
    "mongodb": {
      "status": "ok",
      "response_time": "0.025s",
      "database": "test_wakilai",
      "collections_count": 5
    },
    "gcp_storage": {
      "status": "ok",
      "response_time": "0.080s",
      "bucket_name": "wakilai-storage"
    }
  }
}
```

## 🚀 Quick Start

### Prerequisites
```bash
# Install dependencies
pip install -r requirements.txt

# Ensure pytest is installed
pip install pytest pytest-asyncio pytest-mock pytest-cov
```

### Running Tests

#### 1. Run All Tests
```bash
# Use the comprehensive test runner
./run_tests.sh

# Or directly with pytest
pytest tests/ -v --cov=app --cov-report=html
```

#### 2. Run Specific Service Tests
```bash
# Embedding service tests
pytest tests/ -v -m embedding

# Milvus tests
pytest tests/ -v -m milvus

# OpenAI tests
pytest tests/ -v -m openai

# MongoDB tests
pytest tests/ -v -m mongodb

# GCP Storage tests
pytest tests/ -v -m gcp_storage

# Health endpoint tests
pytest tests/test_health.py -v
```

#### 3. Run by Test Type
```bash
# Only unit tests
pytest tests/ -v -m unit

# Only integration tests (requires real services)
RUN_INTEGRATION_TESTS=1 pytest tests/ -v -m integration

# Skip slow tests
pytest tests/ -v -m "not slow"
```

## 📊 Test Structure

### Unit Tests (`@pytest.mark.unit`)
- Fast, isolated tests using mocks
- No external dependencies required
- Test logic and error handling

### Integration Tests (`@pytest.mark.integration`)
- Tests with real external services
- Require actual API credentials and running services
- Marked with `@pytest.mark.slow`
- Only run with `RUN_INTEGRATION_TESTS=1`

### Markers
- `@pytest.mark.embedding` - Embedding service tests
- `@pytest.mark.milvus` - Milvus database tests  
- `@pytest.mark.openai` - OpenAI LLM tests
- `@pytest.mark.mongodb` - MongoDB tests
- `@pytest.mark.gcp_storage` - GCP Storage tests
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slow/integration tests

## 🛠️ Test Configuration

### Environment Variables
```bash
# Required for tests
export OPENAI_API_KEY="your-openai-key" # pragma: allowlist secret
export SILICONFLOW_API_KEY="your-siliconflow-key" # pragma: allowlist secret
export MONGODB_URI="mongodb://localhost:27017/test_wakilai"
export MILVUS_URI="http://localhost:19530"
export GCS_PROJECT_ID="your-gcp-project"
export GCS_BUCKET_NAME="your-test-bucket"

# Optional for integration tests
export RUN_INTEGRATION_TESTS=1
```

### Test Database Setup
For MongoDB tests, ensure MongoDB is running:
```bash
# Using Docker
docker run -d -p 27017:27017 mongo:latest

# Or local MongoDB
mongod --dbpath /path/to/data
```

### Test Milvus Setup
For Milvus tests, ensure Milvus is running:
```bash
# Using Docker
docker run -d -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest
```

## 📈 Coverage Reports

### Generate Coverage Report
```bash
# HTML report
pytest tests/ --cov=app --cov-report=html

# Terminal report
pytest tests/ --cov=app --cov-report=term

# Fail if coverage below threshold
pytest tests/ --cov=app --cov-fail-under=80
```

### Coverage Reports Location
- HTML Report: `htmlcov/index.html`
- Terminal Report: Shown in console
- XML Report: `coverage.xml` (for CI/CD)

## 🐛 Debugging Tests

### Verbose Output
```bash
pytest tests/ -v -s --tb=long
```

### Stop on First Failure
```bash
pytest tests/ -x
```

### Run Specific Test
```bash
pytest tests/test_embedding.py::TestSiliconFlowEmbedding::test_embed_doc_success -v
```

### Debug with pdb
```bash
pytest --pdb tests/
```

## 🔄 Continuous Integration

### GitHub Actions Example
```yaml
name: Test Suite
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      - name: Run unit tests
        run: |
          pytest tests/ -v -m unit --cov=app --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v1
```

## 📝 Writing New Tests

### Test Template
```python
import pytest
from unittest.mock import Mock, patch

@pytest.mark.unit
class TestNewService:
    """Test new service implementation."""

    @pytest.fixture
    def service_instance(self):
        """Create service instance for testing."""
        with patch('path.to.external.Dependency'):
            from app.services.new_service import NewService
            return NewService()

    def test_basic_functionality(self, service_instance):
        """Test basic service functionality."""
        result = service_instance.basic_method("test_input")
        assert result == "expected_output"

    def test_error_handling(self, service_instance):
        """Test error handling."""
        with pytest.raises(Exception):
            service_instance.method_that_fails()
```

### Best Practices
1. **Use Descriptive Names**: Test methods should clearly describe what they test
2. **Follow AAA Pattern**: Arrange, Act, Assert
3. **Mock External Dependencies**: Don't rely on real external services in unit tests
4. **Test Both Success and Failure**: Ensure error paths are covered
5. **Use Fixtures**: Reduce code duplication with pytest fixtures
6. **Mark Tests Appropriately**: Use markers for categorization

## 🔧 Troubleshooting

### Common Issues

#### 1. Test Dependencies Missing
```bash
# Solution: Install missing packages
pip install pytest pytest-asyncio pytest-mock pytest-cov
```

#### 2. Environment Variables Missing
```bash
# Solution: Set up test environment
cp .env.example .env.test
# Edit .env.test with test values
export $(cat .env.test | xargs)
```

#### 3. External Services Not Running
```bash
# Solution: Start required services
docker-compose up -d mongodb milvus redis
```

#### 4. Permission Issues
```bash
# Solution: Make test runner executable
chmod +x run_tests.sh
```

## 📞 Support

For test-related issues:
1. Check this README for common solutions
2. Review test logs for specific error messages
3. Ensure all environment variables are set
4. Verify external services are running for integration tests

## 🎉 Next Steps

1. **Add More Integration Tests**: Expand real-service testing
2. **Performance Tests**: Add load testing for endpoints
3. **Contract Tests**: Add API contract testing
4. **Visual Testing**: Add frontend component tests
5. **Security Tests**: Add security-focused tests

---

**Happy Testing! 🧪✨**
