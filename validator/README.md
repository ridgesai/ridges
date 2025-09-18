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
  - SWE-bench problems to test miner code on (easy/medium difficulty)
  - API endpoints and logging configuration

### Sandbox System (`sandbox/`)
- **`manager.py`** - Core sandbox management system using Docker containers
- **`clone_repo.py`** - Git repository cloning utilities for test environments
- **`agent_runner.py`** - Main execution script for sandbox operations
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

## System Architecture

The validator follows an event-driven architecture:

1. **WebSocket Connection**: Maintains persistent connection to Ridges platform
2. **Task Reception**: Receives evaluation requests with agent specifications
3. **Sandbox Creation**: Spins up isolated Docker containers for each evaluation
4. **Code Execution**: Runs agent code against SWE-bench problem instances
5. **Result Collection**: Gathers outputs, metrics, and performance data
6. **Weight Updates**: Calculates and submits subnet weights based on performance
7. **State Persistence**: Stores evaluation history in local SQLite database

## Dependencies

The validator integrates with several external systems:
- **Docker**: Container runtime for sandbox isolation
- **SWE-bench**: Software engineering benchmark dataset
- **WebSockets**: Real-time communication with Ridges platform
- **Fiber**: Blockchain integration for subnet participation, built by Rayon Labs

## Usage

The validator is designed to run as a long-lived service, automatically handling evaluation requests and maintaining subnet participation through continuous operation.
It's important that it's constantly kept up-to-date, since if you're running an old version your weights could drift out of sync and affect your vtrust.

- [ ] Install `docker` for your platform. Run `docker run hello-world` to make sure your permissions are set correctly and the daemon is running
- [ ] Install `npm` so you can get `pm2` (`sudo apt install npm` on debian-based distros)
- [ ] `sudo npm i -g pm2` so you can manage background processes
- [ ] Install `uv` for python management. (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [ ] Run `uv venv` to create a virtual environmeent
- [ ] `source .venv/bin/activate`
- [ ] `sudo apt install -y libpq-dev python3-dev build-essential` to install dependencies for our PostgreSQL driver
- [ ] `uv pip install -e .`
- [ ] Create an environment file to specify your settings (`cp validator/.env.example validator/.env`)
- [ ] Choose the network, subnet, etc. in `validator/.env`
- [ ] `./ridges.py validator run` to create the validator process in `pm2` and another updater process that will keep it up-to-date
- [ ] `./ridges.py validator logs` to see the log output and make sure everything is running well
