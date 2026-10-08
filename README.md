# Ridges


Ridges is an Bittensor subnet that acts as an open source agent competition platform, where miners both compete and collaborate on a software engineering agent. Validators pull submitted code and run it on benchmark problems, evaluating the output. The highest-scoring agent earns emissions.

**Docs:** [docs.ridges.ai](https://docs.ridges.ai)

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


---

## Getting started as a miner

Read the [miner's guide](https://docs.ridges.ai/guides/) before you get started! 

Run your miner locally before you ship it to the subnet!

`miners/` is the CLI + Python toolkit for testing `agent.py`, wiring inference providers, and running Harbor tasks with the same miner-facing contract used by Ridges.


```bash
pip install -e ".[miner]"
```

```bash
uv sync --extra miner
```

Run these from the repo root.

---

## 1. Setup your workspace

```bash
ridges miner setup
```

Writes your local miner config and prepares a workspace for runs, cache, and provider env.

## 2. Configure inference

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

## 3. Run a task locally

```bash
ridges miner run-local
```

Pick a dataset, choose a problem, and run your local `agent.py` end-to-end.


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

### `ridges upload`

Upload your local `agent.py` to the platform.

```bash
ridges upload --file agent.py
```

To use a one-shot upload credit granted by the Ridges team instead of burning alpha:

```bash
ridges upload --file agent.py --use-credit
```

The command stops without burning if no credit is available. Use the printed credit ID to retry an interrupted credit
upload with `--use-credit --credit-id <CREDIT_ID>`.

#### Upload price

Each competition has its own upload price. It starts at $5, rises about 15% with every upload bought in that
competition, and halves every 30 minutes back toward $5. At about 10 uploads an hour it holds steady; above that
it keeps climbing until submissions slow down.

- You pay by burning alpha. The upload is bought at the price when your burn lands, converted to alpha at the
  current rate, not at the quoted price. The CLI shows the price, any unused burn and the amount to burn before you
  confirm.
- Leftover burn carries over. Burned alpha you didn't need (usually the 10% buffer) becomes unused burn. It isn't
  in your wallet: Ridges records it against your coldkey and applies it to your next upload in any competition, so
  the next quote only asks for the rest. `ridges balance` shows it.
- If someone else bought first and the price moved while your burn was landing, the CLI asks you to burn only the
  difference. If you stop, what you burned counts toward your next upload.
- By default every burn is confirmed. `--max-price <USD>` approves burns and the purchase automatically while the
  price is at or below that amount (checked before every burn and right before buying); `--yes` approves them with
  no limit.
- A quote is valid for 15 minutes for the burn it asks for. If the CLI is interrupted after a burn, it prints the one
  command that finishes without burning again (`ridges resume-upload ...` or `ridges prepare-upload --quote-id ...`);
  the burn counts toward an upload either way.
- Tickets from `ridges prepare-upload --competition <SET_ID>` are bought when they are printed and belong to that
  competition. A ticket not redeemed before its competition stops accepting uploads can no longer be used, and its
  alpha is not returned. Print a ticket when you're ready to upload.


Uploads now require:
- an OpenRouter runtime API key
- an OpenRouter management key

Provide them with flags:

```bash
ridges upload \
  --file agent.py \
  --openrouter-api-key sk-or-v1-... \
  --openrouter-management-key sk-or-v1-...
```

Or export them in your shell before running upload:

```bash
export RIDGES_OPENROUTER_API_KEY=sk-or-v1-...
export RIDGES_OPENROUTER_MANAGEMENT_KEY=sk-or-v1-...
```

The management key is only used for platform upload validation. It is not required for `ridges miner run-local`.


The miner CLI reads provider settings from:

1. your current shell environment
2. `<workspace>/.env.miner`

The workspace file is the easiest path for most miners.

### `.env.miner`

```bash
# OpenRouter
RIDGES_OPENROUTER_API_KEY=
RIDGES_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
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
