# Miner Development Guide

This document explains the **common infrastructure** that every "miner" implementation shares in the Ridges ecosystem.  It does **not** describe the internal design of the reference miner in this repo – feel free to ignore / replace that code entirely.  Instead, the sections below outline the fixed contracts (HTTP endpoints, sandbox layout, expected outputs) that *all* miners rely on.

---

## 1  High-level workflow

A miner is a program that

1. receives a **problem description** (a failing SWE-bench issue),
2. analyses the repository under test,
3. produces **one unified diff** that is expected to fix the issue, and
4. terminates.

Everything runs inside a throw-away **sandbox** directory that contains a full checkout of the target repository.

---

## 2  Sandbox environment

• **Current working directory** – always the *root* of the cloned repo.  You can safely assume relative paths like `./setup.py` work.

• **No internet** – the miner cannot call external APIs directly.  Instead it interacts with the local **proxy service** (see next section) which handles LLM traffic.

• **Resource limits** – long-running shell commands will be killed by the orchestrator after a timeout.  Keep external processes short.

---

## 3  Proxy endpoints

All model-related calls go through an internal HTTP service running at `$PROXY_URL`.  Two endpoints are important:

### POST `/agents/embedding`

Request body (JSON):
```
{
  "run_id": "<uuid string>",   // required – unique per miner run
  "input":  "<arbitrary text>"
}
```
Response body:
```
[0.0123, -0.98, …]   // list[float] – fixed-length embedding vector
```
Guidelines:

* Call this once per *text chunk* you want to embed (file, function, sliding window, etc.).
* The service is stateless – caching on the miner side is recommended.

### POST `/agents/inference`

Request body:
```
{
  "run_id":  "<same uuid as above>",
  "messages": [ {"role": "system", "content": "…"}, … ],
  "model":       "<optional model name>",
  "temperature": 0.7                    // optional
}
```
Response body: **raw text** directly streamed back from the LLM.  No JSON wrapper.  Your miner is responsible for parsing this text (e.g. extracting the unified diff).

Notes:
* `messages` follows the OpenAI chat format.  You can include arbitrary roles (`system`, `user`, `assistant`).
* Keep messages concise – the proxy enforces a context-length limit (~64 k tokens at time of writing).
* Always send the same `run_id` to group embedding and inference calls that belong to one miner execution.

---

## 4  Producing the patch

A valid answer is **one unified diff** that starts with
```
diff --git a/<file> b/<file>
```
and ends with a trailing newline.  No Markdown fences, no JSON wrappers, no explanations unless the validator explicitly allows them.  If no change is necessary, output exactly `<>` (or whatever sentinel your orchestrator expects).

### Tips

* Run `patch -p1 --dry-run` inside the sandbox before exiting to ensure the diff applies cleanly.
* Strip markdown fences (```), HTML, or other non-diff lines – the validator will reject malformed patches.

---

## 6  Reference implementation

The file `miner/agent.py` in this repo is **one** way to implement a miner (≈1300 LOC, supports retries, sanitisation, embedding pre-filter, etc.).  Feel free to copy ideas or ignore it entirely – only the contracts above are fixed.

Happy mining!