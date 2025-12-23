# Use lightweight python base
FROM python:3.10-slim

# Set workdir
WORKDIR /app

# Copy only requirements first for better caching
COPY requirements.txt .

# Install system dependencies needed for cryptography, database drivers, and Azure Speech SDK
RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    pkg-config \
    libasound2-dev \
    alsa-utils \
    libc6 \
    libc6-dev \
    libssl-dev \
    libasound2 \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python packages with protobuf pure Python implementation
RUN PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python pip install --upgrade pip && \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python pip install --no-cache-dir --upgrade setuptools wheel && \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port
EXPOSE 8080

# Add environment variables for debugging
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

# Start server with streaming-optimized configuration (single worker to avoid protobuf multiprocessing issues)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio", "--http", "httptools", "--timeout-keep-alive", "30"]
