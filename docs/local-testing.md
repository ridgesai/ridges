# Local Testing
## Prerequisites 
- Preferably a unix-based operating system. This may work on windows, but we haven't tested it
- [Chutes](https://chutes.ai/) or [Targon](https://targon.com/) API keys
- Docker runtime, such as [Docker Desktop](https://www.docker.com/)
- [UV](https://docs.astral.sh/uv/) (Python package manager)

## Environment Setup
1. Clone the [ridgesai/ridges](https://github.com/ridgesai/ridges/) repo and `cd` into it
2. Create a virtual environment for python
  ```bash
  uv venv --python 3.13
  uv pip install .
  ```
3. Run the following to create a `.env` file with your Chutes key:
  ```bash
  cp inference_gateway/.env.example inference_gateway/.env
  ```
  Edit the `USE_DATABASE` to be False, and set the `USE_CHUTES` or `USE_TARGON`, and `CHUTES_API_KEY` or `TARGON_API_KEY` depending on which one you’re using
4. In a separate terminal, start the inference gateway:
  ```bash
  uv run -m inference_gateway.main
  ```

5. Find your local IP address to connect to the inference gateway (NOT public IP address)
  ```bash
  # On macOS, You will have a different network interface on linux
  ipconfig getifaddr en0
  ```

## Running an agent
You can either run a single problem, or multiple problems in parallel

To just run a single problem, you can run:
```bash
uv run -m test_agent --inference-url http://"${YOUR_LOCAL_IP}":1234 --agent-path "${PATH_TO_YOUR_AGENT}" test-problem django__django-11138
```

To run on multiple problems in parallel, edit `test_agent_problem_sets.json`. The format is:
```json
{
  "problem-set-name": [
    "problem1",
    "problem2",
    ...
  ]
}
```

For example:
```json
{
  "test-set-1": [
    "lock-js",
    "eliuds-eggs-js",
    "variable-length-quantity-js",
    "rail-fence-cipher-js",
    "high-scores-js",
    "polyglot_py",
    "bowling-py",
    "diamond-py",
    "leap-py",
    "diffie-hellman-py",
    "variable-length-quantity-py",
    "swebench_verified_easy",
    "sympy__sympy-23534",
    "django__django-14404",
    "pytest-dev__pytest-7982",
    "django__django-12304",
    "pytest-dev__pytest-5262"
  ]
}
```

Then all problems by running 
```bash
uv run -m test_agent --inference-url http://"${YOUR_LOCAL_IP}":1234 --agent-path "${PATH_TO_YOUR_AGENT}" test-problem-set test-set-1
```

To see other options you can pass into the test command, run:
```bash
uv run -m test_agent --help
```

## Test results
You can see logs and test results by looking at the files in the tree:
```
test_agent_results/
  └── <date>__<agent-filename>__<evaluation-id>/
      ├── <agent-filename>           # Copy of your agent code
      └── <problem-name>__<evaluation-run-id>/
          ├── evaluation_run.json    # Metadata (status, timestamps, test results)
          ├── agent_logs.txt         # Complete agent execution logs
          └── eval_logs.txt          # Test execution logs
```

