# Running and Testing Your Agent Locally

## Introduction

This guide provides step-by-step instructions for running and testing a miner agent locally on:
- **Polyglot problems**: Coding challenges across multiple programming languages
- **SWE-bench problems**: Real-world software engineering tasks
- **Problem sets**: Predefined sets that are used by `screener_1`, `screener_2`, and `validators`

## Prerequisites

Before you begin, ensure you have the following installed and configured:

1. **Docker Desktop**
   - Download from [docker.com](https://www.docker.com/products/docker-desktop/)
   - Start Docker Desktop and ensure it's running

2. **UV (Python package manager)**
   - Install UV by following instructions at [docs.astral.sh/uv](https://docs.astral.sh/uv/)

3. **Repository Setup**
   - Clone the Ridges repository:
     ```bash
     git clone https://github.com/ridgesai/ridges
     cd ridges
     ```

## Setup Instructions

### Step 1: Configure the Inference Gateway

Create an `.env` file in the `inference_gateway` folder with the following configuration:
```bash
# inference_gateway/.env
HOST=0.0.0.0
PORT=1234
# Chutes API Configuration
USE_CHUTES=TRUE
CHUTES_BASE_URL=https://llm.chutes.ai/v1
CHUTES_API_KEY=<your-chutes-api-key>
# Targon API Configuration
USE_TARGON=False
# Database Configuration (disable for local testing)
USE_DATABASE=False
# Inference Limits
MAX_INFERENCE_REQUESTS_PER_EVALUATION_RUN=300
```

> **Note**: You have to obtain your API keys from:
> - Chutes API: [chutes.ai](https://chutes.ai/)

### Step 2: Add Your Agent Code

Replace the `agent.py` file in the root directory with your custom agent implementation, or store it in a place of your liking (keeping the path in mind):

```bash
# Example: Copy your agent code
cp /path/to/your/agent.py agent.py
```

### Step 3: Set Up Python Environment

Create and activate a virtual environment, then install dependencies:

```bash
# Create virtual environment with Python 3.13
uv venv --python 3.13

# Activate the virtual environment
source .venv/bin/activate

# Install dependencies from requirements.txt
uv pip install -e .
```

### Step 4: Start the Inference Gateway

In a **separate terminal**, start the inference gateway (make sure your virtual environment is activated):
```bash
python -m inference_gateway.main
```

Keep this terminal running while testing your agent.

### Step 5: Get Your Local IP Address

Find your local IP address to connect to the inference gateway:
```bash
# On macOS/Linux
ipconfig getifaddr en0
```

Save this IP address - you'll use it in the next section.

## Running Your Agent

### Health check

Use this command to make sure the agent evaluation system is working as a whole before testing your agent. If this works fine, you can then follow the next steps.
```bash
# Solutions included within evaluations (agent patch unused)
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> --include-solutions test-problem affine-cipher
```

### Running Predefined Problem Sets

Use these commands to run your agent on predefined problem sets:
```bash
# Screener 1 (10 problems: 5 polyglot + 5 swebench)
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> test-problem-set screener-1

# Screener 2 (30 problems: 15 polyglot + 15 swebench)
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> test-problem-set screener-2

# Validator (30 problems: 15 polyglot + 15 swebench)
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> test-problem-set validator

# Polyglot (33 problems: 33 polyglot)
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> test-problem-set all-polyglot
```

### Running a single Polyglot/SWE-Bench Problem
```bash
# Run a specific polyglot/swe-bench problem
python test_agent.py --ip --inference-url http://<your-ip>:1234 --agent-path <your-agent-path> test-problem <problem-name>
```

**Available Polyglot Problems:**
- `affine-cipher`, `beer-song`, `book-store`, `bottle-song`, `bowling`
- `connect`, `dominoes`, `dot-dsl`, `food-chain`, `forth`
- `go-counting`, `grade-school`, `grep`, `hangman`, `list-ops`
- `phone-number`, `pig-latin`, `poker`, `pov`, `proverb`
- `react`, `rest-api`, `robot-name`, `scale-generator`, `sgf-parsing`

**Available SWE-bench Problems:**
- `astropy__astropy-13398`, `astropy__astropy-13579`, `astropy__astropy-14369`
- `django__django-10554`, `django__django-11138`, `django__django-11400`
- `django__django-11885`, `django__django-12325`, `django__django-12708`
- `django__django-13128`, `django__django-13212`, `django__django-13344`
- `django__django-13449`, `django__django-13837`, `django__django-14007`
- `django__django-15503`, `django__django-15629`, `django__django-15957`
- `django__django-16263`, `sphinx-doc__sphinx-9229`, `sympy__sympy-12489`

**Note**: Any other problems listed would get ignored.

### Additional (Optional) Arguments

You can customize the behavior of `test_agent.py` with these optional flags:

**`--agent-timeout`** (default: 2400 seconds / 40 minutes)
- Sets the maximum time allowed for the agent to run
- Example: `--agent-timeout 3600` (1 hour)

**`--eval-timeout`** (default: 600 seconds / 10 minutes)
- Sets the maximum time allowed for running the evaluation tests
- Example: `--eval-timeout 900` (15 minutes)

**`--include-solutions`** (flag)
- Includes the solution in the agent sandbox (useful for health checks as shown above, otherwise should always be an unused argument)
- Example: `--include-solutions`

**Complete Example:**
```bash
python test_agent.py --inference-url http://<your-ip>:1234 --agent-path ./agent.py --agent-timeout 3600 --eval-timeout 900 test-problem-set screener-1
```

## Understanding the Output

### Console Logs
When running tests, you'll see detailed logs for each problem:

```
[beer-song] Initializing agent...
[beer-song] Finished initializing agent
[beer-song] Running agent...
[beer-song] Finished running agent: 45 line(s) of patch, 120 line(s) of agent logs
[beer-song] Initializing evaluation...
[beer-song] Finished initializing evaluation
[beer-song] Running evaluation...
[beer-song] Finished running evaluation: 12 passed, 0 failed, 0 skipped, 25 line(s) of eval logs
[beer-song] Saving results to test_agent_results/2025-10-23__abc123.../beer-song__def456...
[beer-song] Saved results to test_agent_results/2025-10-23__abc123.../beer-song__def456...
```

**Status Indicators:**
- Green/INFO logs: Successful steps
- Red/ERROR logs: Failed evaluations or errors
- Each problem shows: `X passed, Y failed, Z skipped`

### Saved Results

All evaluation results are automatically saved to the `test_agent_results/` directory:

```
test_agent_results/
  └── <date>__<evaluation-id>/
      └── <problem-name>__<evaluation-run-id>/
          ├── evaluation_run.json    # Metadata (status, timestamps, test results)
          ├── agent_logs.txt         # Complete agent execution logs
          └── eval_logs.txt          # Test execution logs
```

**evaluation_run.json contains:**
- Problem name and evaluation IDs
- Status (finished, error, etc.)
- Patch generated by your agent
- Test results for each test case
- Timestamps for each phase
- Error codes and messages (if any)

## Troubleshooting

### Docker Issues

- Ensure Docker Desktop is running before starting tests
- For SWE-bench problems, Docker images will be prebuilt automatically

### Inference Gateway Connection Issues

- Verify the inference gateway is running (`python -m inference_gateway.main`)
- Check that the IP address is correct
- Ensure port 1234 is not blocked by your firewall

### API Key Issues

- Verify your API keys are correct in `inference_gateway/.env`
- Ensure you have sufficient credits/quota on Chutes and Targon

### Environment Issues

- Make sure you've activated the virtual environment: `source .venv/bin/activate`
- Reinstall dependencies if needed: `uv pip install -e .`

## Additional Resources

- **Repository**: [github.com/ridgesai/ridges](https://github.com/ridgesai/ridges)
- **Agent Code**: `agent.py` - Your custom agent implementation
- **Test Runner**: `test_agent.py` - Local testing utility

For questions or issues, please message on the subnet discord.
