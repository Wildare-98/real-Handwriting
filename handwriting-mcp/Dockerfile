# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

# Install system deps for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Bundle default font
COPY handwriting_mcp/fonts /app/handwriting_mcp/fonts

EXPOSE 8080

# Default: SSE/HTTP transport for deployment
CMD ["python", "-m", "handwriting_mcp.server:main_sse"]

# For local stdio use:
# CMD ["python", "-m", "handwriting_mcp.server"]
