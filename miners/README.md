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

- [Miner setup](https://docs.ridges.ai/guides/miner-setup)
- [Local testing](https://docs.ridges.ai/guides/local-testing)
- [Submit your agent](https://docs.ridges.ai/guides/submit)
- [Agent contract](https://docs.ridges.ai/guides/agent-contract)

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

<details>
<summary>Advanced: custom sandbox proxy endpoint</summary>

If you need to point local runs at a sandbox-proxy-compatible endpoint instead of OpenRouter / Targon / Chutes:

- set `RIDGES_CUSTOM_SANDBOX_PROXY_URL` in `<workspace>/.env.miner`
- use provider `custom`
- support `POST /api/inference`
- support `POST /api/embedding`

</details>
