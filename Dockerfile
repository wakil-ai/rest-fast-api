# Use lightweight python base
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy only requirements first for better caching
COPY requirements.txt .

# # Install system dependencies needed for cryptography and database drivers
RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port
EXPOSE 8080

# Add environment variables for debugging
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Start server with streaming-optimized configuration
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio", "--http", "httptools", "--timeout-keep-alive", "30", "--workers", "4"]
