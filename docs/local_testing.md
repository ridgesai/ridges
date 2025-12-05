# Local Testing

## Prerequisites 
- You must use Linux or macOS. We do not support or test on Windows.
- [Chutes](https://chutes.ai/) or [Targon](https://targon.com/) API keys.
- A Docker installation, such as [Docker Desktop](https://www.docker.com/).
- [UV](https://docs.astral.sh/uv/) (a Python package manager).



## Setup

1. Clone the repository.
```bash
git clone https://github.com/ridgesai/ridges/
cd ridges
```

2. Create a virtual environment.
```bash
uv venv
source .venv/bin/activate
```

3. Setup your inference gateway `.env` file. Read the notes in [this file](https://github.com/ridgesai/ridges/blob/main/inference_gateway/.env.example), you'll have to set some variables appropriately (the API keys).
```bash
cp inference_gateway/.env.example inference_gateway/.env
```

4. Start the inference gateway. Don't close this terminal, it must be running while you test your agents.
```bash
uv run -m inference_gateway.main
```

5. Find your local IP address. The method to do this varies by system. Your local IP address is something like `10.0.0.123` or `192.168.0.123`, *not* `0.0.0.0` or `127.0.0.1`.



## Running an Agent

You can either run a single problem, or multiple problems in parallel.

To run a single problem, you can run:
```bash
uv run -m test_agent --inference-url http://"YOUR LOCAL IP":1234 --agent-path "YOUR AGENT PATH" test-problem django__django-11138
```

Remember, your local IP is *not* `0.0.0.0` or `127.0.0.1`!

To run on multiple problems in parallel, add a problem set to `test_agent_problem_sets.json`. For example:
```json
{
  "my-test-set-1": [
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

Then run: 
```bash
uv run -m test_agent --inference-url http://"YOUR LOCAL IP":1234 --agent-path "YOUR AGENT PATH" test-problem-set my-test-set-1
```

To see other options you can pass into the test command, run:
```bash
uv run -m test_agent --help
```



## Test Results
You can see logs and test results by looking at the files in the tree:
```
test_agent_results/
  └── <date>__<agent-name>.py__<evaluation-id>/
      ├── <agent-name>.py
      └── <problem-name>__<evaluation-run-id>/
          ├── evaluation_run.json
          ├── agent_logs.txt
          └── eval_logs.txt
```