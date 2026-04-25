# ridges_harbor

Adapter that lets Ridges miners run inside Harbor.

## What is Harbor?

Harbor is a sandboxed task-execution framework. Give it a task directory and an
"agent" class, and it runs the agent against the task inside a container, then
calls a verifier to score the result.

## What is this package?

Ridges miners are Python files that expose `agent_main(input) -> patch`.
While Harbor expects a richer `BaseInstalledAgent` subclass. 
Having miners switch to this class at this stage does not make sense since the core contract is different from general harbor agents. 

This package is the thin adapter that bridges the two: upload the miner, invoke it inside the container,
collect the patch, check it with `git apply`, let Harbor's verifier score it,
then translate Harbor's result back into Ridges'.

## The flow

```
host                                         container
─────                                        ─────────
runner.py
  builds a Harbor job

agents.py.install()   ── upload ─────►      /installed-agent/
                                               agent.py
                                               ridges_miner_runtime.py
                                               _stdlib_contract.py

agents.py.run()       ── exec ───────►      python3 ridges_miner_runtime.py
                                               calls agent_main(instruction)
                                               writes patch.diff
                                               (or ridges_runtime.json on failure)

                      ── exec ───────►      git apply patch.diff

                                            Harbor's verifier runs
                                               writes reward

execution/artifacts.py  ◄── read ────       trial_dir/
  emits ExecutionResult
```

## File map

- `runner.py` — host-side entrypoint; builds a one-task Harbor job and runs it
- `agents.py` — Harbor `BaseInstalledAgent`; uploads files, runs the runtime, applies the patch
- `ridges_miner_runtime.py` — runs inside the container; loads the miner and calls `agent_main`
- `_stdlib_contract.py` — stdlib-only constants (filenames + phase names) shared host-and-container
- `runtime_contract.py` — host-side Pydantic schema + custom exceptions for miner failures
- `docker_runtime.py` — Docker-compose-specific glue (container labels, verifier egress hook)
- `digest.py` — stable SHA256 of a task directory

## Few points

1. **`ridges_miner_runtime.py` is stdlib-only.** It runs inside the container
   before any third-party deps are guaranteed to exist, so it may only import
   stdlib modules plus its sibling `_stdlib_contract.py`.

2. **`_stdlib_contract.py`'s filename is part of the contract.** The runtime
   script imports it as `from _stdlib_contract import ...` after it's uploaded
   as a sibling.

3. **`ridges_runtime.json` is the wire format.** The container writes it on
   failure; `execution/failure_classifier.py` reads it on the host to decide
   whether the failure is an agent crash or validator-infra noise.
