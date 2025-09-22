# Chutes Proxy Server

A minimal FastAPI server that proxies chutes inference and embedding endpoints with database validation for run_id and sandbox status.

## Overview

This proxy server provides:
- **Embedding endpoint**: `/api/embedding` - Proxies text embedding requests to chutes
- **Inference endpoint**: `/api/inference` - Proxies text generation requests to chutes  
- **Health endpoint**: `/health` - Health check

In production, all requests are validated against the database to ensure:
- The `run_id` exists in the `evaluation_runs` table
- The evaluation run has `status = "sandbox_created"`

If ENV=dev, these checks are omitted for local testing. Make sure you specify your Chutes API key.

## Quick Start

1. Install dependencies:
```bash
uv pip install -e .
```

2. Set environment variables:
```bash
export ENV=dev
export AWS_MASTER_USERNAME=your_db_username
export AWS_MASTER_PASSWORD=your_db_password
export AWS_RDS_PLATFORM_ENDPOINT=your_db_host
export AWS_RDS_PLATFORM_DB_NAME=your_db_name
export CHUTES_API_KEY=your_chutes_api_key
```

3. Run the server:
```bash
./ridges.py proxy run --no-auto-update
```

## Environment Variables

Required:
- `AWS_MASTER_USERNAME` - Database username
- `AWS_MASTER_PASSWORD` - Database password
- `AWS_RDS_PLATFORM_ENDPOINT` - Database host
- `AWS_RDS_PLATFORM_DB_NAME` - Database name
- `CHUTES_API_KEY` - Chutes API key

Optional:
- `PGPORT` - Database port (default: 5432)
- `SERVER_HOST` - Server host (default: 0.0.0.0)
- `SERVER_PORT` - Server port (default: 8000)
- `LOG_LEVEL` - Logging level (default: INFO)

## API Endpoints

### POST /api/embedding
Proxy endpoint for text embedding.

**Request:**
```json
{
  "input": "Text to embed",
  "run_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### POST /api/inference
Proxy endpoint for text generation.

**Request:**
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "model": "deepseek-ai/DeepSeek-V3-0324",
  "temperature": 0.7,
  "messages": [
    {
      "role": "user",
      "content": "Hello, how are you?"
    }
  ]
}
```

### GET /health
Health check endpoint.
