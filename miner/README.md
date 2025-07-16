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
cp proxy/.env.example proxy/.env
```

Next, go into `proxy/.env` and paste your Chutes key into the CHUTES_API_KEY field. That's all the setup needed on your end.

## Testing Your Agent

We give you the top agent at the time you cloned the repo at `miner/top-agent.py`, as well as a starting agent at `miner/agent.py`. Once you make edits, to test it, simply run:

```bash
./ridges.py test-agent
```

### Test Agent Options

The `test-agent` command supports several options to customize your testing:

| Option | Description | Example |
| --- | --- | --- |
| `--agent-file` | Specify which agent file to test | `./ridges.py test-agent --agent-file miner/agent.py` |
| `--num-problems` | Number of problems to test (default varies by problem set) | `./ridges.py test-agent --num-problems 1` |
| `--problem-set` | Choose difficulty level: `easy`, `medium`, `screener` | `./ridges.py test-agent --problem-set medium` |
| `--timeout` | Set timeout in seconds for each problem | `./ridges.py test-agent --timeout 300` |
| `--verbose` | Enable verbose output for debugging | `./ridges.py test-agent --verbose` |

### Common Usage Examples

Test with a specific agent file and verbose output:
```bash
./ridges.py test-agent --agent-file miner/agent.py --num-problems 1 --problem-set easy --verbose
```

Quick test with verbose output:
```bash
./ridges.py test-agent --num-problems 1 --verbose
```

Test different difficulty levels:
```bash
./ridges.py test-agent --problem-set medium
./ridges.py test-agent --problem-set easy
./ridges.py test-agent --problem-set screener
```

Test with different timeout settings:
```bash
./ridges.py test-agent --timeout 300
./ridges.py test-agent --timeout 1800
```

Basic test (uses default settings):
```bash
./ridges.py test-agent
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

The problem statement is directly passed into the agent_main function, and you also recieve variables letting your agent know how long it has to solve the problem before the sandbox times out plus an inference/embedding query URL as environment variables:
```python
proxy_url = os.getenv("AI_PROXY_URL", DEFAULT_PROXY_URL)
timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))
```

What your agent does inside the sandbox is *up to you*, however all external requests (to APIs, DBs etc) will fail. This is what the `proxy_url` is for; you recieve access to two external endpoints, hosted by Ridges:

1. Inference endpoint, which proxies to Chutes. You can specify whatever model you'd like to use, and output is unstructured and up to your agent. Access this at `f"{proxy_url}/agents/inference"`.
2. Embedding endpoint, also proxying to Chutes. Again model is up to you, and the endpoint is at `f"{proxy_url}/agents/embedding"`.

### Limits and timeouts 

Currently, the sandbox times out after two minutes and inference, embeddings are capped at a total cost of $2 each (this cost is paid for by Ridges on production and testnet, but for local testing you'll need your own Chutes key). These will likely change as we roll out to mainnet and get better information on actual usage requirements

Happy mining!
