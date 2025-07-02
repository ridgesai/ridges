# Ridges AI - Development Setup Guide

## Prerequisites

### Local Subtensor Network Setup
Before proceeding with the Ridges AI setup, ensure you have a local Subtensor network running on your machine. Follow the official setup instructions:
- [Subtensor Local Network Setup Guide](https://github.com/opentensor/bittensor-subnet-template/blob/main/docs/running_on_staging.md)

**Critical Requirements:**
- Create separate wallets for miner and validator operations
- Ensure both wallets are properly configured and secured

### Network Registration and Funding
Complete the following steps to register and fund your wallets on the local subnet:

```bash
# Fund validator wallet
btcli wallet faucet --wallet.name validator --subtensor.chain_endpoint ws://127.0.0.1:9945

# Register validator on subnet
btcli subnet register --wallet.name validator --subtensor.chain_endpoint ws://127.0.0.1:9945

# Add stake to validator (required for validation operations)
btcli stake add --wallet.name validator --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945

# Fund miner wallet
btcli wallet faucet --wallet.name miner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945

# Register miner on subnet
btcli subnet register --wallet.name miner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945
```

## Development Environment Setup

### Repository Initialization
```bash
# Clone the repository
git clone https://github.com/ridgesai/ridges.git
cd ridges

# Initialize Python virtual environment
uv venv --python 3.11
source .venv/bin/activate

# Install project dependencies
uv pip install -e .
```

### Local Network Configuration
If running against a local Subtensor instance, set the following environment variable:
```bash
export SUBTENSOR_ADDRESS=ws://127.0.0.1:9945
```

## Network Connectivity Setup

### Miner IP Registration
Register your miner's IP address on the blockchain using Fiber to enable validator discovery:

**Important:** Ensure your miner is registered on the Subtensor network before executing this command.

```bash
fiber-post-ip \
  --netuid 1 \
  --subtensor.chain_endpoint ws://127.0.0.1:9945 \
  --external_ip 0.0.0.1 \
  --external_port 7999 \
  --wallet.name miner \
  --wallet.hotkey default
```

**Configuration Notes:**
- The specified port will be used for miner communication
- For multiple miner instances, use unique ports for each
- If connectivity issues occur with `0.0.0.1`, use your local IP address:
  ```bash
  # macOS: Get local IP address
  ipconfig getifaddr en0
  ```
  Then restart the Subtensor network, re-register both miner and validator, and use the local IP address.

## Platform Infrastructure Setup

### AWS Configuration
The platform requires the following AWS resources:

1. **AWS Account & CLI Authentication**
   - Valid AWS account with appropriate permissions
   - AWS CLI configured and authenticated (with `aws configure`)

2. **Database Setup**
   - Create PostgreSQL RDS instance
   - Apply schema from `api/src/db/postgres_schema.sql`

3. **Storage Setup**
   - Create S3 bucket for file storage

4. **API Integration**
   - Obtain API key from [Chutes AI Platform](https://chutes.ai/app/api)

### Platform Deployment
```bash
# Navigate to API directory
cd api

# Configure environment variables
cp api/.env.example api/.env
# Edit api/.env with your configuration values

# Start the platform services
uvicorn api.src.main:app
```

## Validator Environment Setup

### Docker Configuration
Build sandbox execution environment and proxy

```bash
docker build -t sandbox-runner validator/sandbox
docker build -t sandbox-nginx-proxy validator/sandbox/proxy/
```

### Running the validator
```bash
uv run validator/main.py
```

## Miner Agent Development

### Agent Upload Process
1. Access the platform API documentation: http://localhost:8000/docs
2. Use the `/upload/agent` endpoint to deploy your agent

### Agent Requirements
Your agent file must meet the following criteria for successful deployment:

- **File Structure**: Single Python file named `agent.py`
- **Code Quality**: Valid Python syntax and logic
- **Entry Point**: Contains `if __name__ == '__main__'` block
- **Dependencies**: Only imports from:
  - Python standard library
  - Approved libraries specified in `api/src/utils/config.py`

**Note**: Files that do not meet these requirements will be rejected during the upload process.
