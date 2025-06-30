# Validator

The validator is a core component of the subnet system that evaluates code-solving agents by running them in sandboxed environments against standardized benchmarks. It connects to the Ridges platform via WebSocket to receive evaluation tasks and report results.

## Overview

The validator operates as a distributed system component that:
- Connects to the Ridges platform via WebSocket for task coordination
- Manages Docker-based sandbox environments for secure code execution
- Evaluates agents using the SWE-bench dataset of real-world software engineering problems
- Tracks performance metrics and weights for the subnet consensus mechanism
- Maintains a local database of evaluation runs and results

## Core Components

### Main Entry Point
- **`main.py`** - Application entry point that initializes the database and starts the WebSocket connection

### Configuration
- **`config.py`** - Central configuration file containing:
  - Network settings (NETUID, subtensor configuration)
  - Validator credentials (hotkey, wallet)
  - Evaluation parameters (timeouts, intervals)
  - SWE-bench problem instance lists (easy/medium difficulty)
  - API endpoints and logging configuration

### Database Layer (`db/`)
- **`schema.py`** - SQLAlchemy models for tracking evaluations, agent versions, and results

### Sandbox System (`sandbox/`)
- **`manager.py`** - Core sandbox management system using Docker containers
- **`validator.py`** - Validation logic for sandbox environments
- **`clone_repo.py`** - Git repository cloning utilities for test environments
- **`Main.py`** - Main execution script for sandbox operations
- **`Dockerfile`** - Container definition for sandbox environments
- **`proxy/`** - HTTP proxy configuration for sandbox networking
  - **`Dockerfile`** - Proxy container definition
  - **`default.conf.template`** - Nginx configuration template

### WebSocket Communication (`socket/`)
- **`websocket_app.py`** - Main WebSocket client managing connection to Ridges platform
- **`handle_message.py`** - Message routing and processing logic
- **`handle_evaluation.py`** - Evaluation request processing
- **`handle_evaluation_available.py`** - Availability status management

### Task Processing (`tasks/`)
- **`run_evaluation.py`** - Core evaluation orchestration logic
- **`set_weights.py`** - Subnet weight setting functionality  
- **`weights.py`** - Weight calculation and management utilities

### Utilities (`utils/`)
- **`http_client.py`** - HTTP client utilities for API communication
- **`node_utils.py`** - Blockchain node interaction utilities
- **`weight_utils.py`** - Weight calculation helper functions
- **`temp_files.py`** - Temporary file management
- **`get_validator_version_info.py`** - Version tracking utilities

### Setup
- **`setup.py`** - Package installation configuration
- **`dependencies.py`** - Dependency injection setup

## System Architecture

The validator follows an event-driven architecture:

1. **WebSocket Connection**: Maintains persistent connection to Ridges platform
2. **Task Reception**: Receives evaluation requests with agent specifications
3. **Sandbox Creation**: Spins up isolated Docker containers for each evaluation
4. **Code Execution**: Runs agent code against SWE-bench problem instances
5. **Result Collection**: Gathers outputs, metrics, and performance data
6. **Weight Updates**: Calculates and submits subnet weights based on performance
7. **State Persistence**: Stores evaluation history in local SQLite database

## Key Features

- **Secure Isolation**: Docker-based sandboxes prevent malicious code execution
- **Standardized Benchmarks**: Uses SWE-bench for consistent agent evaluation
- **Real-time Communication**: WebSocket integration for immediate task coordination
- **Performance Tracking**: Comprehensive metrics collection and weight calculation
- **Fault Tolerance**: Robust error handling and recovery mechanisms
- **Scalable Design**: Modular architecture supporting multiple concurrent evaluations

## Dependencies

The validator integrates with several external systems:
- **Docker**: Container runtime for sandbox isolation
- **SWE-bench**: Software engineering benchmark dataset
- **SQLAlchemy**: Database ORM for local state management
- **WebSockets**: Real-time communication with Ridges platform
- **Fiber**: Blockchain integration for subnet participation

## Usage

The validator is designed to run as a long-lived service, automatically handling evaluation requests and maintaining subnet participation through continuous operation.