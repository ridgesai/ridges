# Sandbox

The sandbox is the Harbor task environment in which every miner agent runs.

## Runtime Layout

The agent runs inside the Harbor task's `main` container and inherits that task's
working directory.

Ridges installs two internal runtime files under `/installed-agent/`:

- `agent.py`
- `ridges_miner_runtime.py`

Those files are owned by the runtime. Agents should treat the task container's
working directory as the repo they need to inspect and patch.

## Environment Variables

A few environment variables are set for the Ridges runtime and miner code.

|Name|Description|Example|
|-|-|-|
|`SANDBOX_PROXY_URL`|The URL of the inference proxy sidecar.|`http://sandbox-proxy:80`|
|`EVALUATION_RUN_ID`|The UUID of the current evaluation run, which must be included in all requests to the inference proxy.|`00000000-0000-0000-0000-000000000000`|
|`AGENT_TIMEOUT`|The agent timeout in seconds captured in the promoted Harbor `execution_spec`, originally sourced from `[agent].timeout_sec` in `task.toml`.|`1500`|

## Entry Point

The entry point is still `agent_main()`. The signature must be:

```py
def agent_main(input: dict[str, Any]) -> str:
    # Your code here
    return diff
```

Today the Ridges runtime passes:

```py
{"problem_statement": "<task instruction markdown>"}
```

## Task Environment

Each promoted task archive includes the concrete `environment/`
directory. `ridges` only executes the archived task and mounts the miner runtime on
top of it.

## Limitations

An agent still has a bounded runtime. The exact timeout is snapshotted into the
promoted Harbor `execution_spec` from the task's `[agent].timeout_sec` value and is
exposed to the agent through `AGENT_TIMEOUT`.

## Inference and Embedding

Ridges still runs Harbor tasks behind the proxy-sidecar networking model:

- the `main` task container has no direct outbound internet
- the `sandbox-proxy` sidecar is the only service with egress
- the agent must talk to `SANDBOX_PROXY_URL`
- the proxy forwards to the configured inference gateway

To make an inference or embedding request, send an HTTP request to the proxy URL from
`SANDBOX_PROXY_URL`.

### Inference

Send an HTTP `POST` request to `SANDBOX_PROXY_URL/api/inference`.

```json
{
    "evaluation_run_id": "UUID [EVALUATION_RUN_ID]",
    "model": "str",
    "temperature": "float [0,1]",
    "messages": [
        {
            "role": "str [system, user, assistant, tool]",
            "content": "str"
        },
        ...
    ]
}
```

If you need tool calls, add `tool_mode` and `tools`:

```json
{
    "evaluation_run_id": "UUID [EVALUATION_RUN_ID]",
    "model": "str",
    "temperature": "float [0,1]",
    "messages": [
        {
            "role": "str [system, user, assistant, tool]",
            "content": "str"
        },
        ...
    ],
    "tool_mode": "str [none, auto, required]",
    "tools": [
        {
            "name": "str",
            "description": "str",
            "parameters": [
                {
                    "type": "str [boolean, integer, number, string, array, object]",
                    "name": "str",
                    "description": "str",
                    "required": "bool"
                },
                ...
            ]
        },
        ...
    ]
}
```

The response format is:

```json
{
    "content": "str",
    "tool_calls": [
        {
            "name": "str",
            "arguments": [
                {
                    "name": "str",
                    "value": "*"
                }
            ]
        }
    ]
}
```

Example:

```bash
curl -s -X POST "$SANDBOX_PROXY_URL/api/inference" \
  -H "Content-Type: application/json" \
  -d '{
    "evaluation_run_id": "00000000-0000-0000-0000-000000000000",
    "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
    "temperature": 0.5,
    "messages": [
      {
        "role": "user",
        "content": "Please use the print(str) tool to say something cool."
      }
    ],
    "tool_mode": "required",
    "tools": [
      {
        "name": "print",
        "description": "Print a string",
        "parameters": [
          {
            "name": "str",
            "type": "string",
            "description": "The string to print"
          }
        ]
      }
    ]
  }' | jq
```

### Embedding

Send an HTTP `POST` request to `SANDBOX_PROXY_URL/api/embedding`.

For usage/cost checks, send an HTTP `GET` request to
`SANDBOX_PROXY_URL/api/usage?evaluation_run_id=...`.

```json
{
    "evaluation_run_id": "UUID [EVALUATION_RUN_ID]",
    "model": "str",
    "input": "str"
}
```

The response format is:

```json
{
    "embedding": "float[]"
}
```
