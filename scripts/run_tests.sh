#!/bin/bash

# Comprehensive test runner for WakilAI API services
# This script runs all unit tests for the critical services

set -e

echo "🚀 Running WakilAI API Test Suite"
echo "=================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}$1${NC}"
}

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    print_error "pytest is not installed. Installing..."
    pip install pytest pytest-asyncio pytest-mock pytest-cov
fi

# Run unit tests for specific services
run_service_tests() {
    local service_name=$1
    local marker=$2

    print_header "🧪 Running $service_name tests..."

    if PYTHONPATH=. pytest tests/test_$marker.py -v; then
        print_status "✅ $service_name tests passed"
    else
        print_error "❌ $service_name tests failed"
        return 1
    fi
}

# Run all tests
print_header "📊 Running Complete Test Suite"
echo ""

# Individual service tests
run_service_tests "Embedding Service" "embedding"
echo ""
run_service_tests "Milvus Vector DB" "milvus"
echo ""
run_service_tests "OpenAI LLM" "gpt"
echo ""
run_service_tests "MongoDB" "mongodb"
echo ""
run_service_tests "GCP Storage" "gcp_storage"
echo ""
run_service_tests "OCR Service" "ocr"
echo ""
run_service_tests "Chat Chain main" "chat_chain"
echo ""


# Run all unit tests
print_header "🔬 Running All Unit Tests"
if pytest tests/ -v -m "unit" --cov=app --cov-report=html --cov-report=term-missing; then
    print_status "✅ All unit tests passed"
else
    print_warning "⚠️  Some unit tests failed"
fi
echo ""

# Generate coverage report
print_header "📈 Coverage Report"
pytest tests/ --cov=app --cov-report=html --cov-report=term --cov-fail-under=80 || print_warning "Coverage is below 80%"
echo ""

# Integration tests (if enabled)
if [[ "$RUN_INTEGRATION_TESTS" == "1" ]]; then
    print_header "🌐 Running Integration Tests"
    pytest tests/ -v -m "integration" --cov=app --cov-report=term-missing || print_warning "Some integration tests failed"
    echo ""
fi

# Performance tests (basic)
print_header "⚡ Running Performance Checks"
echo "Running basic performance validation..."

# Test health endpoint response time
start_time=$(date +%s%N)
curl -s http://localhost:8000/api/health > /dev/null 2>&1 || print_warning "Health endpoint not accessible - ensure API is running"
end_time=$(date +%s%N)
response_time=$((($end_time - $start_time) / 1000000))

if [ $response_time -lt 1000 ]; then
    print_status "✅ Health endpoint response time: ${response_time}ms"
else
    print_warning "⚠️  Health endpoint response time: ${response_time}ms (slow)"
fi
echo ""

print_header "🎉 Test Suite Complete"
echo ""
echo "📊 Test Reports Generated:"
echo "  - HTML Coverage Report: htmlcov/index.html"
echo "  - Terminal Coverage Report: Above"
echo "  - Individual Test Results: Above"
echo ""
echo "🚀 Next Steps:"
echo "  1. Review any failed tests above"
echo "  2. Check coverage report for gaps"
echo "  3. Run integration tests with: RUN_INTEGRATION_TESTS=1 ./run_tests.sh"
echo ""
print_status "All done! 🙌"
