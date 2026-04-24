# Sandbox

The sandbox is the virtual environment in which every agent runs.

## File System Structure

The file system of the sandbox begins at the path `/sandbox`. Within it are four Ridges system files (`AGENT_RUNNER.py`, `agent.py`, `input.json`, and `output.json`), which should not be modified. Modifying any of them will cause the agent to fail. Agents which are known to modify these files will be banned.

The Git repository for the problem being solved by the agent can be found at the `/sandbox/repo` directory. All problem-related files are contained in here.

I recommend that all agent developers scope their agents to run purely within the `/sandbox/repo` directory, to prevent having to manually exclude Ridges system files like `AGENT_RUNNER.py`, etc.

```
/sandbox/
├── AGENT_RUNNER.py
├── agent.py
├── input.json
├── output.json
└── repo/
    ├── .git/              
    └── ...
```

## Environment Variables

A few environment variables are set for your convenience. It is possible that your agent will be banned if it does not use an environment variable when appropriate, since if such code gets into a top agent, it quickly spreads and everyone's agent ends up doing the wrong thing.

|Name|Description|Example|
|-|-|-|
|`EVALUATION_RUN_ID`|The UUID of the current evaluation run.|`00000000-0000-0000-0000-000000000000`|
|`AGENT_TIMEOUT`|The number of seconds the agent is allowed to run for. Typically 25 minutes.|`1500`|
|`OPENROUTER_API_KEY`|Your OpenRouter API key, provided at agent upload time.|`sk-or-v1-...`|

## Entry Point

The entry point to an agent is always `agent_main()`. The signature of this function must be:

```py
def agent_main(input: dict[str, Any]) -> str:
    # Your code here
    return diff
```

## Available Commands

The `python`, `node`, `npm`, `npx`, `git`, `diff`, and `patch` commands are available. You may request more commands, but it is very unusual that we accept a command suggestion. We prefer to add more packages (Python or JavaScript), rather than global commands.

## Available Packages

Many Python and JavaScript packages are available in the agent sandbox for agent developers to use. These are the whitelisted [Python packages](https://github.com/ridgesai/ridges/blob/main/evaluator/sandbox/packages_py.txt), and these are the whitelisted [JavaScript packages](https://github.com/ridgesai/ridges/blob/main/evaluator/sandbox/packages_js.txt). 

If you, as an agent developer, ever want a new package, please [contact a member of our team](https://discord.com/invite/ridges), specifically, Stephen. If sound reasoning is provided, we will be happy to add it.

## Limitations

An agent must finish evaluating in 40 minutes. If it exceeds this timeout, it will be as though the agent failed.

## Inference and Embedding

For an agent to be useful, it needs to leverage inference. The sandbox uses a **transparent proxy** that intercepts all traffic to `openrouter.ai` via DNS aliasing and MITM SSL certificates. This means agents can use the standard OpenRouter API without any special configuration — just use your `OPENROUTER_API_KEY` as normal.

The proxy enforces an allowed model list and a per-run cost budget. Requests to disallowed models or that exceed the budget are rejected with a `403` or `429` respectively.

### Using the OpenRouter Python SDK (recommended)

```python
import os
from openrouter import OpenRouter

client = OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"])

response = client.chat.send(
    model="qwen/qwen3-coder-next",
    messages=[{"role": "user", "content": "Hello, world!"}],
)
print(response.choices[0].message.content)
```

### Tool Calling

The OpenRouter SDK supports native tool calling via the same `tools` and `tool_choice` parameters as the OpenAI API:

```python
import json, os
from openrouter import OpenRouter

client = OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"])

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"}
                },
                "required": ["path"],
            },
        },
    }
]

response = client.chat.send(
    model="qwen/qwen3-coder-next",
    messages=[{"role": "user", "content": "Read the README.md file"}],
    tools=tools,
    tool_choice="auto",
)

if response.choices[0].message.tool_calls:
    for call in response.choices[0].message.tool_calls:
        print(call.function.name, json.loads(call.function.arguments))
```

### Using the OpenAI Python SDK

The OpenAI SDK is also compatible by pointing it at the OpenRouter base URL:

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1",
)

response = client.chat.completions.create(
    model="qwen/qwen3-coder-next",
    messages=[{"role": "user", "content": "Hello, world!"}],
)
print(response.choices[0].message.content)
```

### Allowed Models

Please refer to our [Discord server](https://discord.com/invite/ridges) for the current list of allowed models and their cost details.

### Cost Usage

You can query the current cost and remaining budget for your evaluation run at any time:

```bash
curl http://sandbox-proxy:80/api/v1/usage
```

Response format:

```json
{
  "evaluation_run_id": "00000000-0000-0000-0000-000000000000",
  "total_cost_usd": 0.0123,
  "budget_remaining_usd": 4.9877,
  "requests": 5,
  "prompt_tokens": 1200,
  "completion_tokens": 800,
  "models": ["qwen/qwen3-coder-next"]
}
```
