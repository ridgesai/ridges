# agent.py
#  heads up for aaron or jalal                                               
#  This whole repo/sandbox starts here.                                     
#                                                                                 
#  The validator spins up a Docker container that mounts three things:          
#        /sandbox/Main.py   – tiny runner script (already provided)               
#        /sandbox/src       – folder that contains **this** agent.py              
#        /sandbox/repo      – the target open-source repo we need to patch    
#        correct me if im wrong, but feel free to change that    
#                                                                                 
#  Main.py imports this file and calls `agent_main(challenge_dict)`             
#                                                                                 
#  Inside we (1) chat with an LLM via a local HTTP proxy,                       
#            (2) give it some tiny built-in tools (ls/find/read/diff) to explore  
#            (3) loop until the LLM coughs up a unified diff.                     
#                                                                                 
#  Whatever diff we return becomes our "patch" and will be tested by Swe-bench.

from __future__ import annotations

import difflib
import json
import logging
import os
import pathlib
import re
import time
import socket
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote, urlencode

# ─────────────────────────────── Paths & constants ───────────────────────────
REPO_ROOT = pathlib.Path(os.getenv("REPO_ROOT", "/sandbox/repo")).resolve()
IO_DIR    = pathlib.Path(os.getenv("IO_DIR",    "/sandbox_io")).resolve()

# Unix socket path for secure host access
SOCKET_PATH = "/tmp/sandbox_proxy.sock"

MAX_TOOL_OUTPUT_CHARS = 900_000   # trim gigantic outputs, 900KB is insane, I just increased it from 100KB for testing purposes
TOOL_LOOP_CAP         = 80        # hard step cap
REQUEST_TIMEOUT       = 60        # seconds per proxy round‑trip

_verbose = os.getenv("VERBOSE", "0") not in {"0", "false", "False", ""}
logging.basicConfig(level=logging.DEBUG if _verbose else logging.INFO,
                    format="[%(levelname)s] %(message)s")

# ─────────────────────────────── helper utils ────────────────────────────────

def _safe_join(base: pathlib.Path, *paths: str | os.PathLike[str]) -> pathlib.Path:
    # Little helper to make sure the LLM doesn't wander outside the repo tree.
    # If it tries to read '/etc/passwd' we bail with 'access denied'.
    cand = (base.joinpath(*paths)).resolve()
    if not str(cand).startswith(str(base)):
        raise ValueError("access denied: path escapes repository root")
    return cand


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2 :]
    return f"{head}\n[…truncated…]\n{tail}"

# Heuristic mapping from free-text to tool calls. This lets the agent act on
# phrases like "I'll use the 'ls' tool" even when the model fails to emit the
# strict JSON wrapper.

_TOOL_PATTERN = re.compile(r"\b(ls|find|read|diff)\b", re.I)

def _nl_to_tool(text: str) -> tuple[str, Dict[str, Any]] | None:
    txt = unquote(text).strip().lower()

    m = _TOOL_PATTERN.search(txt)
    if not m:
        return None

    name = m.group(1)

    if name == "ls":
        # Try to capture path after 'ls' or 'in'
        pm = re.search(r"ls (?:the )?(?:dir(?:ectory)? )?(?:in )?([\w./\-]+)", txt)
        path = pm.group(1) if pm else "."
        return "ls", {"path": path}

    if name == "find":
        patm = re.search(r"find (?:for )?[\'\"]([^\'\"]+)[\'\"]", txt)
        pattern = patm.group(1) if patm else ".*"
        pathm = re.search(r"in ([\w./\-]+)", txt)
        pth = pathm.group(1) if pathm else "."
        return "find", {"pattern": pattern, "path": pth}

    if name == "read":
        rm = re.search(r"read ([\w./\-]+)(?: lines? (\d+)(?:-(\d+))?)?", txt)
        if rm:
            path = rm.group(1)
            start = int(rm.group(2) or 1)
            end = int(rm.group(3) or (start + 399))
            return "read", {"path": path, "start": start, "end": end}

    # diff likely requires explicit JSON, skip heuristic
    return None

# ─────────────────────────────── tool functions ──────────────────────────────

def ls(path: str = ".") -> str:
    try:
        base = _safe_join(REPO_ROOT, path)
    except ValueError as exc:
        return str(exc)
    files = (str(p.relative_to(REPO_ROOT)) for p in base.rglob("*") if p.is_file())
    return _truncate("\n".join(files)) or "[no files]"


def find(pattern: str, path: str = ".") -> str:
    try:
        base = _safe_join(REPO_ROOT, path)
    except ValueError as exc:
        return str(exc)
    try:
        regex = re.compile(pattern, re.I)
    except re.error as err:
        return f"[invalid regex: {err}]"
    matches: List[str] = []
    for file in base.rglob("*"):
        if not file.is_file():
            continue
        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = file.relative_to(REPO_ROOT)
                matches.append(f"{rel}:{i}: {line.strip()}")
    return _truncate("\n".join(matches) or "[no matches]")


def read(path: str, start: int = 1, end: int = 400) -> str:
    try:
        file = _safe_join(REPO_ROOT, path)
    except ValueError as exc:
        return str(exc)
    try:
        lines = file.read_text(errors="ignore").splitlines()
    except Exception as err:
        return f"[error reading file: {err}]"
    start, end = max(1, int(start)), max(int(start), int(end))
    snippet = lines[start - 1 : end]
    numbered = [f"{i+start:>5d}| {l}" for i, l in enumerate(snippet)]
    return _truncate("\n".join(numbered) or "[empty]")


def diff(old: str, new: str) -> str:
    delta = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                 fromfile="old", tofile="new", lineterm="")
    return _truncate("\n".join(delta) or "[no diff]")

TOOLS = {"ls": ls, "find": find, "read": read, "diff": diff}

# ↑ quick python call-backs the LLM can invoke by returning a JSON blob like
#   {"tool": "ls", "args": {"path": "astropy/io"}}
#   The heuristics underneath also pick up plain English like "I'll use ls".

# Empty list now; when proxy supports OpenAI tools we can push schema here.
FN_SPEC: List[Dict[str, Any]] = []

# ────────────────────────────── Unix socket client ────────────────────────────

def _socket_request(path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Make HTTP POST request over Unix socket"""
    json_body = json.dumps(data)
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(json_body)}\r\n"
        f"\r\n"
        f"{json_body}"
    )
    
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET_PATH)
        sock.send(request.encode())
        
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                # Continue reading until we have the full body
                headers_end = response.find(b"\r\n\r\n")
                headers_part = response[:headers_end].decode()
                
                # Check if we have Content-Length header
                content_length = 0
                for line in headers_part.split('\r\n'):
                    if line.lower().startswith('content-length:'):
                        content_length = int(line.split(':')[1].strip())
                        break
                
                body_start = headers_end + 4
                body_received = len(response) - body_start
                
                # Read remaining body if needed
                while body_received < content_length:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    body_received = len(response) - body_start
                break
        
        # Parse response
        response_str = response.decode()
        if "\r\n\r\n" in response_str:
            headers, body = response_str.split("\r\n\r\n", 1)
            return json.loads(body) if body else {}
        return {}
    finally:
        sock.close()

def _call_proxy(messages: List[Dict[str, Any]], run_id: str,
                retries: int = 3) -> Dict[str, Any]:
    # All communication with the LLM funnelled through this one function.         
    # It hits the Unix socket proxy and returns the raw JSON/text the model produced.
    payload = {"messages": messages, "tools": FN_SPEC}
    data = {
        "run_id": run_id,
        "return_text": "true",
        "return_code": "true",
        "input_text": json.dumps(payload),
    }
    
    for attempt in range(retries):
        try:
            response = _socket_request("/agents/inference", data)
            diff = response.get("code_response", "")
            if diff:
                return {"content": diff}

            txt = response.get("text_response", "")
            try:
                return json.loads(txt)
            except Exception:
                return {"content": txt, "code": response.get("code_response", "")}
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"proxy call failed: {exc}") from exc
            time.sleep(1 + attempt * 2)

# ────────────────────── clarifying‑question auto‑handler ─────────────────────
QUESTION_RE  = re.compile(r"\?\s*$")
FOLLOWUP_RE  = re.compile(r"would you like|do you want|can you provide", re.I)

def _looks_like_question(text: str) -> bool:
    t = text.strip().lower()
    return bool(QUESTION_RE.search(t) or FOLLOWUP_RE.search(t))

# ─────────────────────────── main solve loop ─────────────────────────────────

def _solve(prompt: Dict[str, Any]) -> tuple[str, str]:
    # Core autopilot loop:                                                         
    #  send conversation so far to the model                                     
    #  if reply contains a tool-call, run it and feed output back                
    #  else assume it's the final diff and bail                                  
    # We also auto-answer clarifying questions because the sandbox has no user.
    # Can change logic for this but idrk how to do it
    run_id = prompt.get("run_id", None)
    problem_statement = prompt.get("problem_statement", None)
    require_tool = os.getenv("FORCE_TOOL_CALL", "0") not in {"0", "false", "False", ""}

    # ------------------------- system prompt -------------------------
    system_prompt = (
        """
        You are a fully-autonomous code-analysis agent running in an **offline sandbox**.
        The repository root is /sandbox/repo (use **relative paths** such as
        'astropy/io/registry/compat.py').

        You may call four JSON tools — ls, find, read, diff — by replying
        **only** with a JSON blob like {"tool":"ls","args":{...}}.

        After you have gathered enough context you must reply with **one final
        unified diff** that can be applied with `git apply` or `patch -p1`
        **without any additional text**.

        Unified-diff requirements:
          • Start each file section with `--- a/<path>` and `+++ b/<path>` lines.
          • Use one or more hunk headers of the form `@@ -oldStart,oldCount +newStart,newCount @@`.
          • Use only lines that begin with a space (context), `+` (add), or `-` (delete).
          • Do NOT wrap the diff in markdown fences, do NOT add commentary before or after, and do NOT add blank lines outside the diff.

        Example format (do **not** repeat this text, only mirror the structure):
        diff --git a/foo.py b/foo.py
        --- a/foo.py
        +++ b/foo.py
        @@ -10,3 +10,4 @@ def bar():
             print("old")
        -    return 1
        +    return 42
        @@ -24,0 +25,3 @@
        +# new helper
        +def helper():
        +    pass

        You are a fully-autonomous code-analysis agent. Repo is at /sandbox/repo.
        No external internet. Available JSON tools: ls, find, read, diff.
        If unsure, assume and proceed.
        """
        .strip()
    )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": problem_statement},
    ]

    tool_used = False
    for step in range(TOOL_LOOP_CAP):
        logging.info("proxy round %d", step + 1)
        reply = _call_proxy(messages, run_id)

        raw_txt = reply.get("content", "").strip()
        # ───── detect explicit JSON tool call ─────
        if raw_txt.startswith("diff"):
            return raw_txt, ""

        if raw_txt.startswith("{"):
            try:
                obj = json.loads(raw_txt)
                if isinstance(obj, dict) and "tool" in obj:
                    name, args = obj.get("tool"), obj.get("args", {})
                    result = TOOLS.get(name, lambda **_: f"[unknown tool: {name}]")(**args)
                    messages.append({"role": "tool", "content": result})
                    tool_used = True
                    continue
            except Exception:
                pass  # fall through

        # ───── heuristic natural-language detection ─────
        hint = _nl_to_tool(raw_txt)
        if hint:
            tname, targs = hint
            logging.info("heuristic tool match → %s(%s)", tname, targs)
            try:
                result = TOOLS[tname](**targs)
            except Exception as err:
                result = f"[tool execution error: {err}]"
            messages.append({"role": "tool", "content": result})
            tool_used = True
            continue

        # ───── automatic answer to clarifying question ─────
        if _looks_like_question(raw_txt) and not tool_used:
            logging.info("LLM asked a question; auto‑replying with 'No additional info'.")
            messages.extend([
                {"role": "assistant", "content": raw_txt},
                {"role": "user",      "content": "No additional information is available. Proceed."},
            ])
            continue

        # ───── final answer ─────
        if not tool_used and require_tool:
            # If we've already asked once and model still didn't comply, accept its answer.
            if messages and messages[-1].get("role") == "user" and "Please inspect" in messages[-1].get("content", ""):
                logging.warning("LLM failed to use tool after nudge; accepting best effort answer.")
                return raw_txt, reply.get("code", "")

            logging.warning("LLM ended without using any tool; nudging once.")
            messages.append({"role": "assistant", "content": raw_txt})
            messages.append({"role": "user", "content": "Please inspect the codebase with at least one tool."})
            continue
        return raw_txt, reply.get("code", "")

    return "[no answer]", ""

# ---------------------------------------------------------------------------
# Sandbox entry-point required by the validator
# ---------------------------------------------------------------------------
def agent_main(input: dict):
    patch, _ = _solve(input)
    return {"patch": patch}

if __name__ == "__main__":
    agent_main()
