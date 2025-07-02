# Ridges API Platform

The Ridges API Platform is the central coordinating service of the subnet system that manages agent submissions, evaluation orchestration, and real-time communication between validators and the frontend interface. It serves as the primary data hub and task coordinator for the distributed evaluation network.

## Overview

The platform operates as a high-availability service that:
- Accepts and validates code-solving agent submissions from miners
- Coordinates evaluation tasks across connected validator nodes
- Manages secure file storage and retrieval for agent code
- Provides real-time WebSocket communication for live updates
- Tracks performance metrics, rankings, and subnet weights
- Maintains comprehensive data analytics and historical records
- Integrates with blockchain infrastructure for subnet consensus

## Core Components

### Main Entry Point
- **`main.py`** - FastAPI application entry point that initializes:
  - REST API endpoints with CORS configuration
  - WebSocket server for real-time communication
  - Background weight monitoring service
  - Database connection management

### REST API Endpoints (`endpoints/`)
- **`upload.py`** - Agent submission and validation system:
  - Python code validation and security checks
  - Rate limiting and conflict resolution
  - S3 storage integration for agent files
  - Blockchain verification for miner authentication
- **`retrieval.py`** - Data access and analytics endpoints:
  - Agent rankings and performance metrics
  - Evaluation history and execution results
  - Statistics aggregation and reporting
  - File download and code access
- **`agents.py`** - Agent execution services:
  - Embedding generation for code analysis
  - AI inference capabilities during evaluation
  - Execution context validation

### Database Layer (`db/`)
- **`operations.py`** - Core database operations with dual architecture:
  - Modern SQLAlchemy methods for maintainability
  - Raw SQL fallbacks for complex queries
  - Connection pooling and transaction management
- **`sqlalchemy_models.py`** - Object-relational mapping definitions:
  - Agent and version tracking models
  - Evaluation and execution result schemas
  - Weight history and analytics models
- **`sqlalchemy_manager.py`** - SQLAlchemy session and connection management
- **`postgres_schema.sql`** - Database schema definitions and migrations
- **`s3.py`** - AWS S3 integration for secure file storage

### WebSocket Communication (`socket/`)
- **`websocket_manager.py`** - Central WebSocket hub managing:
  - Validator connection lifecycle
  - Real-time evaluation task distribution
  - Live status updates and notifications
  - Connection health monitoring
- **`server_helpers.py`** - WebSocket message processing utilities:
  - Evaluation creation and assignment logic
  - Task state management
  - Validator version tracking

### Utilities (`utils/`)
- **`config.py`** - System configuration including:
  - Approved Python packages for agent code
  - Rate limiting and security parameters
  - AI model pricing and resource limits
- **`auth.py`** - Request authentication and validation
- **`weights.py`** - Subnet weight monitoring and blockchain integration
- **`subtensor.py`** - Blockchain interaction utilities
- **`chutes.py`** - AI service integration for agent evaluation
- **`models.py`** - Pydantic data models for API contracts
- **`logging_utils.py`** - Centralized logging configuration
- **`nodes.py`** - Subnet node management utilities

## System Architecture

The platform follows a microservices-oriented architecture:

1. **Agent Ingestion**: Validates and stores submitted agent code securely
2. **Task Coordination**: Distributes evaluation tasks to available validators
3. **Real-time Communication**: Maintains WebSocket connections for instant updates
4. **Data Management**: Handles complex queries and analytics on evaluation data
5. **File Storage**: Manages secure cloud storage for agent artifacts
6. **Blockchain Integration**: Tracks subnet weights and validator performance
7. **API Gateway**: Provides comprehensive REST API for frontend integration

## Data Flow

### Agent Submission Flow
1. Miner submits Python agent code via `/upload` endpoint
2. Platform validates code syntax, imports, and security constraints
3. Agent stored in S3 with unique version identifier
4. Database updated with agent metadata and version tracking
5. Evaluation tasks created for all connected validators
6. WebSocket notifications sent to validators about new work

### Evaluation Coordination Flow
1. Validators connect via WebSocket and register capabilities
2. Platform assigns evaluation tasks based on validator availability
3. Real-time status updates track evaluation progress
4. Results collected and stored with comprehensive metrics
5. Agent rankings updated based on performance data
6. Weight calculations performed for subnet consensus

## Security Features

- **Code Validation**: AST parsing to ensure safe Python execution
- **Import Restrictions**: Whitelist of approved packages and modules
- **Rate Limiting**: Prevents spam and resource exhaustion
- **Authentication**: Cryptographic signature verification
- **Sandboxing**: Secure execution environments for agent code
- **Access Control**: Blockchain-based permission system

## Performance Monitoring

The platform includes comprehensive observability:
- **Real-time Metrics**: Active connections, task throughput, response times
- **Historical Analytics**: Performance trends, agent rankings, validator health
- **Resource Monitoring**: Database performance, S3 usage, compute metrics
- **Alert System**: Automated notifications for system anomalies

## Dependencies

The platform integrates with several external systems:
- **PostgreSQL**: Primary database for structured data storage
- **AWS S3**: Secure cloud storage for agent code and artifacts
- **FastAPI**: High-performance web framework for REST APIs
- **WebSockets**: Real-time bidirectional communication
- **Fiber**: Blockchain integration for subnet participation
- **Docker**: Containerization for deployment and scaling

## Configuration

Environment variables control key platform behaviors:
- Database connection parameters
- S3 bucket configuration
- Authentication settings
- Rate limiting parameters
- Blockchain network settings

## Usage

The platform is designed to run as a production service with high availability requirements. It automatically handles:
- Connection pooling and load balancing
- Graceful degradation under high load
- Automatic failover and recovery
- Continuous health monitoring

### Setup Instructions

- [ ] Install PostgreSQL database server (`sudo apt install libpq-dev` on debian)
- [ ] Configure AWS S3 bucket for file storage (`aws configure`)
- [ ] Install `uv` for Python dependency management (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [ ] Install `npm` so you can get `pm2` (`sudo apt install npm` on debian-based distros)
- [ ] `sudo npm i -g pm2` so you can manage background processes
- [ ] Create environment file with database and AWS credentials (`cp api/.env.example api/.env`) and choose your valies

- [ ] Run `uv venv` to create a virtual environment  
- [ ] `source .venv/bin/activate`
- [ ] `uv pip install -e .` to install dependencies
- [ ] `./ridges.py platform run` to run the platform using pm2

