# Miner Development Guide

We recommend testing out running your agents locally before trying to compete in production - the subnet is a winner takes all system, and so if you cannot compete you risk being deregistered. You can fully simulate what score you'll get in production, end to end, by running the full platform API and validator locally, and submitting your agent. 

This guide explains how to run Ridges locally. To get a better understanding of the incentive mechanism, read the [getting started documentation](/im-v3).

## Requirements

To run Ridges locally, all you need is a laptop. Because you are spinning up agent sandboxes, we recommend at least 32gb of RAM and 512GB of SSD to be on the safe side. 

As a miner, you can interact with Ridges entirely through the CLI. The flow is pretty simple - 

1. Edit your agent to improve its performance solving SWE problems, as measured by [SWE-Bench](https://www.swebench.com/) (for now 👀)
    - We recommend looking at what top agents are doing on our [dashboard](https://www.ridges.ai/dashboard). You can integrate ideas, but pure copying is not allowed 
2. Test your agent by running the Ridges CLI. This makes it easy to see how your agent scores.
3. Once you are ready, you can also use the CLI to submit an agent

This guide explains how to use the CLI both for evaluations and for submissions of your agent.

## Setup Guide

Previously, to run a miner you needed to be run a Bittensor subtensor, validator, platform, and API proxy system, as well as setup and S3 bucket, Chutes account, Postgres db, and multiple testing wallets. 

This is all gone now, all you need is a Chutes account - you can sign up [here](https://chutes.ai/). You should be able to grab an API key that looks like `cpk_some_long_.api_key`.

Once you have this, clone the [Ridges Github Repo](https://github.com/ridgesai/ridges/), run the following to create a `.env` file with your Chutes key:

```bash
cp inference_gateway/.env.example inference_gateway/.env
```

Next, go into `inference_gateway/.env` and paste your Chutes key into the PROXY_CHUTES_API_KEY field. That's all the setup needed on your end.

## Testing Your Agent

We give you the top agent at the time you cloned the repo at `miner/top-agent.py`, as well as a starting agent at `miner/agent.py`. Once you make edits, to test it, simply specify the problem name and your agent file:

```bash
./ridges.py test-agent affine-cipher miner/agent.py
```

The system will automatically:
- Search all available problem suites to find your problem
- Start the inference gateway on your local IP address (e.g., `http://10.0.0.154:8000`)
- Set up Docker sandboxes for isolated agent execution
- Clean up all resources when the test completes

### Test Agent Command Structure

The `test-agent` command requires two arguments and supports several optional flags:

**Required Arguments:**
- `problem_name`: Specific problem to test (e.g., `affine-cipher`, `django__django-12308`)
- `agent_file`: Path to your agent Python file

**Optional Flags:**
| Flag | Description | Default |
| --- | --- | --- |
| `--verbose` | Enable verbose output for debugging | False |
| `--timeout` | Timeout in seconds for sandbox execution | 10 |
| `--log-docker-to-stdout` | Print Docker container logs in real-time | False |
| `--include-solution` | Expose solution to agent at `/sandbox/solution.diff` | False |
| `--cleanup` | Clean up containers after test | True |
| `--start-proxy` | Automatically start proxy if needed | True |
| `--gateway-url` | Override default gateway URL | None |

### Common Usage Examples

**Basic test with a Polyglot problem:**
```bash
./ridges.py test-agent affine-cipher miner/agent.py
```

**Test with SWE-bench problem:**
```bash
./ridges.py test-agent django__django-12308 miner/agent.py
```

**Test with verbose output and solution access:**
```bash
./ridges.py test-agent affine-cipher miner/agent.py --verbose --include-solution
```

**Test with custom timeout and Docker logs:**
```bash
./ridges.py test-agent beer-song miner/agent.py --timeout 300 --log-docker-to-stdout
```

**Popular Polyglot problems to test with:**
```bash
./ridges.py test-agent affine-cipher miner/agent.py
./ridges.py test-agent beer-song miner/agent.py
./ridges.py test-agent bowling miner/agent.py
./ridges.py test-agent connect miner/agent.py
```

**Popular SWE-bench problems to test with:**
```bash
./ridges.py test-agent django__django-11138 miner/agent.py
./ridges.py test-agent django__django-11400 miner/agent.py
./ridges.py test-agent django__django-12325 miner/agent.py
``` 

## Submitting your agent 

During submission you submit your code, version number, and file, along with a signature from your hotkey. We recommend using the Ridges CLI,  which handles all of this for you.

By default, the CLI gets the agent file from `miner/agent.py`.

All you have to run is: 

```bash
./ridges.py upload
```

## Agent structure
Agents are a single python file, that have to adhere to two key specifications:

1. The file must contain an entry file called `agent_main`, with the following structure:
    ```python 
        def agent_main(input_dict: Dict[str, Any]):
            """
            Entry point for your agent. This is the function the validator calls when running your code.

            Parameters 
            ----------
            input_dict : dict
                Must contain at least a key ``problem_statement`` with the task
                description.  An optional ``run_id`` can be present (passed through to
                the proxy for bookkeeping).
            
            Returns
            -------
            Your agent must return a Dict with a key "patch" that has a value of a valid git diff with your final agent changes.
            """
        # Your logic for how the agent should generate the final solution and format it as a diff

        return {
            "patch": """
                diff --git file_a.py
            """
        }
    ```
2. You can only use built in Python libraries + a list of allowed external libs. If you would support for another library, message us on Discord and we will review it. You can see the supported external libraries [here](https://github.com/ridgesai/ridges/blob/im_v3/api/src/utils/config.py)

### Agent access to tools and context

Your agent will be injected into a sandbox with the repo mounted under the `/repo` path. You can see a full agent example [here](https://github.com/ridgesai/ridges/blob/im_v3/miner/agent.py).

Further, the libraries you have access to are preinstalled and can be imported right away, no install commands etc needed.

The problem statement is directly passed into the agent_main function, and you also receive variables letting your agent know how long it has to solve the problem before the sandbox times out plus an inference/embedding query URL as environment variables:
```python
proxy_url = os.getenv("AI_PROXY_URL", DEFAULT_PROXY_URL)
timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))
run_id = os.getenv("RUN_ID")  # Unique identifier for this agent run
```

What your agent does inside the sandbox is *up to you*, however all external requests (to APIs, DBs etc) will fail. This is what the `proxy_url` is for; you receive access to two external endpoints, hosted by Ridges:

1. Inference endpoint, which proxies to Chutes. You can specify whatever model you'd like to use, and output is unstructured and up to your agent. Access this at `f"{proxy_url}/api/inference"`.
2. Embedding endpoint, also proxying to Chutes. Again model is up to you, and the endpoint is at `f"{proxy_url}/api/embedding"`.

**Important**: When making API requests, always include the `run_id` in your payload:
```python
payload = {
    "run_id": os.getenv("RUN_ID"),
    "model": "moonshotai/Kimi-K2-Instruct",
    "temperature": 0.0,
    "messages": [{"role": "user", "content": "Your prompt here"}]
}
```

### Limits and timeouts 

Currently, the sandbox times out after two minutes and inference, embeddings are capped at a total cost of $2 each (this cost is paid for by Ridges on production and testnet, but for local testing you'll need your own Chutes key). These will likely change as we roll out to mainnet and get better information on actual usage requirements

Happy mining!
