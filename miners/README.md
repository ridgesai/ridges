# Ridges Miner CLI

```
                           ·        ·        ·
                 .   . · ´  ` · .         . · ´ ` · .   .
                  . ·                 ·                ` .
             . ·´                                          `· .

          /\
         /**\
        /****\   /\
       /      \ /**\
      /  /\    /    \        /\    /\  /\      /\            /\/\/\  /\
     /  /  \  /      \      /  \/\/  \/  \  /\/  \/\  /\  /\/ / /  \/  \
    /  /    \/ /\     \    /    \ \  /    \/ /   /  \/  \/  \  /    \   \
   /  /      \/  \/\   \  /      \    /   /    \
__/__/_______/___/__\___\__________________________________________________

```

Run your miner locally before you ship it to the subnet.

`miners/` is the CLI + Python toolkit for testing `agent.py`, wiring inference providers, and running Harbor tasks with the same miner-facing contract used by Ridges.

---

## Install

```bash
pip install -e ".[miner]"
```

```bash
uv sync --extra miner
```

Run these from the repo root.

---

## Quickstart

### 1. Setup your workspace

```bash
ridges miner setup
```

Writes your local miner config and prepares a workspace for runs, cache, and provider env.

### 2. Configure inference

Fill the generated file:

```bash
<workspace>/.env.miner
```

Start from the checked-in template:

```bash
miners/env.miner.example
```

Supported providers:
- OpenRouter
- Targon
- Chutes

### 3. Run a task locally

```bash
ridges miner run-local
```

Pick a dataset, choose a problem, and run your local `agent.py` end-to-end.

---

## CLI

### `ridges miner setup`

Create or update your miner config and provider selection.

```bash
ridges miner setup
```

### `ridges miner run-local`

Run one Harbor task locally against your miner.

```bash
ridges miner run-local
```

Scripted mode:

```bash
ridges miner run-local \
  --task-path /path/to/task-or-task.tar.gz \
  --agent-path /path/to/agent.py \
  --provider openrouter \
  --non-interactive
```

### `ridges miner cleanup`

Prune cached extracted task archives from local runs.

```bash
ridges miner cleanup
```

Preview first:

```bash
ridges miner cleanup --dry-run
```

---

## Configuration

The miner CLI reads provider settings from:

1. your current shell environment
2. `<workspace>/.env.miner`

The workspace file is the easiest path for most miners.

### `.env.miner`

```bash
# OpenRouter
RIDGES_OPENROUTER_API_KEY=
RIDGES_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Targon
RIDGES_TARGON_API_KEY=
RIDGES_TARGON_BASE_URL=

# Chutes
RIDGES_CHUTES_API_KEY=
RIDGES_CHUTES_INFERENCE_BASE_URL=
RIDGES_CHUTES_EMBEDDING_BASE_URL=
```

If no provider is configured yet, `ridges miner setup` / `ridges miner run-local` will guide you and create the file for you.

---

## Python API

The CLI is the main path, but you can script local runs too.

```python
from miners import LocalInferenceClient, LocalInferenceConfig, run_local_task
```

Use `run_local_task(...)` to launch a local Harbor run from Python.
Inside a local-testing `agent.py`, use `LocalInferenceClient.from_env()` and return the generated diff from `agent_main(input) -> str`.

---

## What Matters In This Folder

```text
miners/
├── cli/                  # CLI entrypoints and command flows
├── env.miner.example     # provider env template
├── inference_client.py   # local provider-backed inference helper
└── local_harbor.py       # Python API for local task runs
```

---

## Notes

- `ridges miner run-local` is for fast local iteration, not validator-equivalent execution.
- Your local agent still uses the normal Ridges miner contract: `agent_main(input) -> str`.
- For deeper runtime details, see `docs/harbor_local_testing.md` and `docs/sandbox.md`.

---

<details>
<summary>Advanced: custom sandbox proxy endpoint</summary>

If you need to point local runs at a sandbox-proxy-compatible endpoint instead of OpenRouter / Targon / Chutes:

- set `RIDGES_CUSTOM_SANDBOX_PROXY_URL` in `<workspace>/.env.miner`
- use provider `custom`
- support `POST /api/inference`
- support `POST /api/embedding`

</details>
