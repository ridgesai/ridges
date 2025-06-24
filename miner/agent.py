#!/usr/bin/env python3
"""One-file autonomous coding agent ("base-miner").

The validator mounts a repository and a problem statement inside a sandbox and
executes this file.  The agent talks to a local *AI-proxy* over a UNIX domain
socket using the OpenAI function-calling protocol.  It exposes a minimal but
sufficient set of tools so the language-model can inspect and modify the
repository and finally indicate it is done.

Tools exposed to the LM
-----------------------
LS(dir=".")
FIND(pattern, dir=".")
READ_FILE(path, max_bytes=4000)
DIFF(path1, path2)
WRITE_FILE(path, content)
APPLY_PATCH(patch)
FINISH()

Program exit codes
------------------
0  – model called FINISH (validator will run its own tests)
1  – uncaught exception, timeout or max-steps exceeded

Configuration (CLI flags *or* environment variables)
----------------------------------------------------
flag / env-var       default
------------------   -----------------------------
--socket  AI_PROXY_SOCK   /tmp/sandbox_proxy.sock
--problem PROBLEM_FILE    ./PROBLEM.md
--repo    REPO_ROOT       .
--timeout AGENT_TIMEOUT   600  (seconds)
--model   AGENT_MODEL     deepseek-ai/DeepSeek-V3-0324

A `trajectory.jsonl` file with the full conversation is written for debugging.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import textwrap
import time
import traceback
import random
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Environment tweaks ---------------------------------------------------------
# ---------------------------------------------------------------------------
# The `sentence_transformers` / `transformers` stack attempts to import the
# TensorFlow / Keras backend by default.  In the constrained sandbox image we
# don't ship those heavy dependencies, and recent `keras==3` is incompatible
# with `transformers`, causing an immediate import failure.  Setting the
# following environment variable instructs `transformers` to entirely skip any
# TensorFlow components so that the pure PyTorch code paths are used instead.
# IMPORTANT: This must be done *before* importing anything from transformers or
# sentence_transformers.

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

# Some recent versions of `transformers` still unconditionally import the legacy
# compatibility package `tf_keras` (a thin shim around Keras 2.x).  Rather than
# pulling in heavy TensorFlow dependencies or pinning older libraries, we
# provide an empty stub so that the import succeeds without side-effects.

import types as _types
import sys as _sys
if "tf_keras" not in _sys.modules:
    _sys.modules["tf_keras"] = _types.ModuleType("tf_keras")

# Some helper classes referenced by `transformers.integrations` are only
# defined when TensorFlow support is available.  We create lightweight stubs
# so that static imports succeed even though the functionality is absent.

try:
    import importlib as _importlib

    _tfm = _importlib.import_module("transformers")  # ensure it's loaded

    if not hasattr(_tfm, "TFPreTrainedModel"):
        class _DummyTFPreTrainedModel:  # noqa: N801 (class name style)
            pass

        _tfm.TFPreTrainedModel = _DummyTFPreTrainedModel
except Exception:
    # If transformers itself isn't importable for some reason we'll find out
    # later when sentence_transformers attempts; nothing to do here.
    pass

# ---------------------------------------------------------------------------
# Defaults via environment variables ----------------------------------------
# ---------------------------------------------------------------------------
DEFAULT_PROXY_URL = os.getenv("AI_PROXY_URL", "http://sandbox_proxy")
DEFAULT_PROBLEM = os.getenv("PROBLEM_FILE", "./PROBLEM.md")
DEFAULT_REPO = os.getenv("REPO_ROOT", ".")
DEFAULT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))
DEFAULT_MODEL = os.getenv("AGENT_MODEL", "deepseek-ai/DeepSeek-V3-0324")

# Other constants
MAX_STEPS = 50
MAX_OBS_CHARS = 20_000
MAX_BYTES_READ = 4_000

# ---------------------------------------------------------------------------
# Prompt templates (adapted from SWE-agent/config/default.yaml) --------------
# ---------------------------------------------------------------------------

### ----------------------------------------------------------------------
# Comprehensive prompt (SWE-agent default + coding_challenge) -------------
### ----------------------------------------------------------------------

# --- system prompt (long form) ---
_RAW_SYSTEM_PROMPT = textwrap.dedent(
    """
    SETTING: You are an autonomous programmer, and you're working directly in the command line with a special interface.

    The special interface consists of a file editor that shows you 100 lines of a file at a time.
    In addition to typical bash commands, you can also use the following commands to help you navigate and edit files.

    COMMANDS:
    {command_docs}

    Please note that THE EDIT COMMAND REQUIRES PROPER INDENTATION.
    If you'd like to add the line '        print(x)' you must fully write that out, with all those spaces before the code! Indentation is important and code that is not indented correctly will fail and require fixing before it can be run.

    RESPONSE FORMAT:
    Your shell prompt is formatted as follows:
    (Open file: <path>) <cwd> $

    You need to format your output using two fields; discussion and command.
    Your output should always include _one_ discussion and _one_ command field EXACTLY as in the following example:
    DISCUSSION
    First I'll start by using ls to see what files are in the current directory. Then maybe we can look at some relevant files to see what they look like.
    ```
    ls -a
    ```

    You should only include a *SINGLE* command in the command section and then wait for a response from the shell before continuing with more discussion and commands.
    If you'd like to issue two commands at once, PLEASE DO NOT DO THAT! Instead submit the first command, wait for feedback, then issue the second.

    You're free to use any other bash commands you want (e.g. find, grep, cat, ls, cd) in addition to the special commands listed above.
    However, the environment does NOT support interactive session commands (e.g. python, vim), so please do not invoke them.
    """
)

# --- instance prompt (long form) ---
DEFAULT_INSTANCE_TEMPLATE = textwrap.dedent(
    """
    <uploaded_files>
    {working_dir}
    </uploaded_files>

    We're currently attempting to solve the following problem:
    ISSUE:
    {problem_statement}

    INSTRUCTIONS:
    You are going to solve this issue on your own. Your terminal session has started and you're in the repository's root directory. You can use any bash commands or the special interface to help you. Edit all the files you need to and run any checks or tests that you want.
    Remember, YOU CAN ONLY ENTER ONE COMMAND AT A TIME. You should always wait for feedback after every command.
    When you're satisfied with all of the changes you've made, you can submit your changes to the code base by calling the FINISH tool.
    Note however that you cannot use any interactive session commands (e.g. python, vim) in this environment, but you can write scripts and run them with `python <script_name>.py`.

    NOTE ABOUT EDITING FILES: Indentation really matters! When editing a file, make sure to insert appropriate indentation before each line!

    IMPORTANT TIPS:
    1. Always test your code thoroughly before signalling FINISH, and if any tests fail, fix the code before continuing.
    2. If you run a command and it doesn't work, try running a different command or inspecting the error.
    3. Use commands like FIND or READ_FILE to inspect code instead of blindly scrolling.
    4. Always pay attention to the current working directory.
    5. When editing files, check that changes look correct; if not, issue another command to fix them.
    """
).strip()

# When the last tool produced no output we can use this as a gentle reminder
DEFAULT_NO_OUTPUT_MSG = (
    "Your command ran successfully and did not produce any output."
)

# ---------------------------------------------------------------------------
# Tool implementations -------------------------------------------------------
# ---------------------------------------------------------------------------

def _ls(dir: str = ".") -> str:
    path = Path(dir)
    if not path.is_dir():
        return f"Directory not found: {dir}"
    entries = [f"{p.name}/" if p.is_dir() else p.name for p in sorted(path.iterdir())]
    return "\n".join(entries) or "<empty>"


def _find(pattern: str, dir: str = ".") -> str:
    try:
        cmd = [
            "bash",
            "-lc",
            f"grep -R -n --line-number --binary-files=text --color=never {shlex.quote(pattern)} {shlex.quote(dir)} || true",
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out or "<no matches>"
    except Exception as e:
        return f"find error: {e}"


def _read_file(path: str, max_bytes: int = MAX_BYTES_READ) -> str:
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"
    try:
        data = p.read_bytes()[:max_bytes]
        return data.decode(errors="replace")
    except Exception as e:
        return f"read error: {e}"


def _diff(path1: str, path2: str) -> str:
    cmd = ["diff", "-u", "--", path1, path2]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out or "<no differences>"
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:  # files differ -> diff in stdout
            return e.output
        return f"diff error: {e.output}"


def _write_file(path: str, content: str) -> str:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"{path} written. bytes={len(content.encode())}"
    except Exception as e:
        return f"write error: {e}"


def _apply_patch(patch: str) -> str:
    """Apply unified diff using system `patch`. Returns result summary."""
    try:
        with NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(patch)
            tmp_path = tmp.name

        def _run_patch(p_level: str):
            return subprocess.run(
                ["patch", p_level, "--forward", "--reject-file=-", "-i", tmp_path],
                text=True,
                capture_output=True,
                timeout=60,
            )

        # Try multiple patch strip levels then fallback to git apply
        out = ""
        proc = None
        for level in ("-p1", "-p0", "-p2", "-p3"):
            proc = _run_patch(level)
            out += f"\n--- attempt {level} ---\n" + proc.stdout + proc.stderr
            if proc.returncode in (0, 1):
                break  # applied or with rejects

        # Fallback to git apply if available and previous attempts failed
        if proc.returncode not in (0, 1):
            git_proc = subprocess.run(
                ["git", "apply", "--unsafe-paths", tmp_path],
                text=True,
                capture_output=True,
                timeout=60,
            )
            out += "\n--- git apply fallback ---\n" + git_proc.stdout + git_proc.stderr
            if git_proc.returncode == 0:
                proc = git_proc
         
        if proc.returncode == 0:
            return "Patch applied successfully.\n" + out
        elif proc.returncode == 1:
            return "Patch applied with warnings/rejects.\n" + out
        else:
            return f"patch failed (code {proc.returncode}).\n" + out
    except Exception:
        return "patch execution error:\n" + traceback.format_exc()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _finish() -> str:
    return "<FINISHED>"


TOOLS = {
    "LS": _ls,
    "FIND": _find,
    "READ_FILE": _read_file,
    "DIFF": _diff,
    "WRITE_FILE": _write_file,
    "APPLY_PATCH": _apply_patch,
    "FINISH": _finish,
}

# Build command documentation once TOOLS is defined
_COMMAND_DOCS = "\n".join(
    f"- {name.lower()}: {fn.__doc__.splitlines()[0] if fn.__doc__ else ''}" for name, fn in TOOLS.items()
)

# Format the raw system prompt with command docs
DEFAULT_SYSTEM_PROMPT = _RAW_SYSTEM_PROMPT.format(command_docs=_COMMAND_DOCS)

# ---------------------------------------------------------------------------
# One-shot (MiniLM retrieval) mode -------------------------------------------
# ---------------------------------------------------------------------------

ONESHOT_SYSTEM_PROMPT = (
    "You are an autonomous programmer. The user will provide a bug report or "
    "feature request (the \"problem\") plus a compact summary of the most "
    "relevant repository files.  Your job is to return ONE *valid* unified "
    "diff patch that fixes the problem.\n\n"
    "STRICT FORMAT RULES\n"
    "1. Return *only* the diff – no prose, no Markdown back-ticks.\n"
    "2. The diff must start with 'diff --git a/<path> b/<path>' followed by "
    "   the standard \"--- a/<path>\" and \"+++ b/<path>\" headers.\n"
    "3. Use -u style context hunks that begin with lines like @@ -N,M +N,M @@.\n"
    "4. Every changed file needs its own header block as in rule 2.\n"
    "5. End the patch with a trailing newline.\n\n"
    "Be exact: if the diff is syntactically malformed or wrapped in extra "
    "text the automated patch tool will fail."
)

# Small helper – language tag for triple-backticks (very rough heuristic)
def _lang_tag(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
        ".go": "go",
    }.get(ext, "")


def _collect_repo_texts(root: str = ".", *, max_file_bytes: int = 100_000) -> Dict[str, str]:
    """Return {rel_path: text} for every reasonably-sized text file under *root*."""
    texts: Dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        if ".git" in dirpath.split(os.sep):
            continue  # skip git internals
        for fn in files:
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > max_file_bytes:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    texts[os.path.relpath(path, root)] = fh.read()
            except Exception:
                continue  # binary or unreadable
    return texts


def run_oneshot(
    problem_text: str,
    *,
    proxy_url: str,
    model_name: str,
    run_id: str,
    top_k: int = 10,
) -> str:
    """Build repository summary and send a single LLM call.

    We *prefer* to embed texts with `sentence_transformers` (MiniLM).  If that
    model cannot be loaded – e.g. no internet to download weights – we fall
    back to a plain TF-IDF vectoriser from scikit-learn so the agent still
    operates fully offline.
    """

    import numpy as np  # local import to avoid cost when not used

    repo_texts = _collect_repo_texts()
    if not repo_texts:
        raise RuntimeError("repository appears empty – nothing to embed")

    file_paths = list(repo_texts.keys())
    file_bodies = list(repo_texts.values())

    # ------------------------------------------------------------
    # 1) Try fast MiniLM embeddings (preferred) ------------------
    # ------------------------------------------------------------
    use_fallback = False
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        try:
            encoder = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            # Most likely offline – fall back.
            use_fallback = True
    except Exception:
        use_fallback = True

    if not use_fallback:
        file_vecs = encoder.encode(file_bodies, normalize_embeddings=True)
        query_vec = encoder.encode([problem_text], normalize_embeddings=True)[0]

        sims = file_vecs @ query_vec  # cosine similarity
    else:
        # --------------------------------------------------------
        # 2) Offline fallback: TF-IDF ----------------------------
        # --------------------------------------------------------
        from sklearn.feature_extraction.text import TfidfVectorizer

        corpus = [problem_text] + file_bodies
        vectorizer = TfidfVectorizer(stop_words="english", max_features=50_000)
        mat = vectorizer.fit_transform(corpus).astype(np.float32)

        query_vec = mat[0].toarray()[0]
        file_mat = mat[1:]

        # Cosine similarity: dot(u, v)/(||u||*||v||). Since TF-IDF vectors are
        # already L2-normalised by sklearn (norm="l2" default), we can use dot.
        sims = (file_mat @ query_vec)  # shape (n_files,)

    top_idx = np.argsort(-sims)[: top_k]

    summary_parts: list[str] = []
    for idx in top_idx:
        path = file_paths[idx]
        body = file_bodies[idx][:2000]  # truncate per file
        tag = _lang_tag(path)
        summary_parts.append(f"### {path}\n```{tag}\n{body}\n```")

    repo_summary = "\n\n".join(summary_parts)

    # --------------------------------------------------------------------
    # Build initial conversation messages.
    # --------------------------------------------------------------------

    messages = [
        {"role": "system", "content": ONESHOT_SYSTEM_PROMPT},
        {"role": "user", "content": problem_text},
        {"role": "user", "content": "Repository summary (top files):\n\n" + repo_summary},
    ]

    # The proxy now streams requests fully, so the hard 8-KiB ceiling is gone.
    # We keep a *very* generous soft cap (512 KiB) just to avoid accidentally
    # flooding the proxy with multi-MB payloads when a problem statement is
    # huge.  If the request still exceeds this after all summary files are
    # dropped we just send it – the proxy will handle it.

    SOFT_CAP_BYTES = 512 * 1024  # 512 KiB

    ATTEMPTS = 3

    for attempt in range(ATTEMPTS):
        # Ensure request fits (reuse shrinking logic)
        while True:
            request_data = {
                "run_id": run_id,
                "input_text": problem_text + "\n\nRepository summary (top files):\n\n" + repo_summary,
                "return_text": True,
                "return_code": True,
                "model": model_name,
            }
            request_bytes = json.dumps(request_data, ensure_ascii=False).encode('utf-8')
            if len(request_bytes) <= SOFT_CAP_BYTES:
                break
            if summary_parts:
                summary_parts.pop()
                repo_summary = "\n\n".join(summary_parts)
                request_data["input_text"] = problem_text + "\n\nRepository summary (top files):\n\n" + repo_summary
            else:
                break

        try:
            proxy_resp = _send_json_request(proxy_url, "/agents/inference", request_data)
        except Exception as e:
            print(f"[agent] Request failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt == ATTEMPTS - 1:
                raise RuntimeError(f"All {ATTEMPTS} attempts failed: {e}")
            continue

        text_resp = (proxy_resp.get("text_response") or "").lstrip()
        code_resp = (proxy_resp.get("code_response") or "").lstrip()

        patch_text = None
        for cand in (text_resp, code_resp):
            if cand and (cand.startswith("diff") or cand.startswith("--- ")):
                patch_text = cand
                break

        if patch_text is None:
            print(f"[agent] No valid patch found in response. text_response: {text_resp[:200]}...", file=sys.stderr)
            print(f"[agent] code_response: {code_resp[:200]}...", file=sys.stderr)
            print(f"[agent] Full proxy response: {proxy_resp}", file=sys.stderr)
            raise Exception(f"No valid patch in response. Response: {proxy_resp}")

        ok, dry_out = _dry_run_patch(patch_text)
        if ok:
            result = _apply_patch(patch_text)
            with open("trajectory.jsonl", "w", encoding="utf-8") as tf:
                tf.write(json.dumps({"assistant_patch": patch_text}) + "\n")
                tf.write(json.dumps({"apply_result": result}) + "\n")
            print(result)
            return patch_text

        # Patch failed – append feedback and ask for correction.
        messages.append({"role": "assistant", "content": patch_text})
        messages.append({"role": "user", "content": "Patch failed to apply. Patch output was:\n" + dry_out + "\nPlease reply with a corrected unified diff only."})

    # All attempts exhausted
    raise RuntimeError("Patch could not be applied after iterative corrections.")

# ---------------------------------------------------------------------------
# Proxy communication --------------------------------------------------------
# ---------------------------------------------------------------------------

import urllib.request as _urlreq
import urllib.error as _urlerr


def _send_json_request(base_url: str, endpoint: str, data: dict, max_retries: int = 5, base_delay: float = 0.1) -> dict:
    """Send JSON request to the proxy over HTTP with exponential back-off."""

    base_url = base_url.rstrip("/")
    url = f"{base_url}{endpoint}"

    request_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    print(f"[agent] Sending request to {url} with {len(request_bytes)} bytes", file=sys.stderr)

    for attempt in range(max_retries + 1):
        try:
            req = _urlreq.Request(url, data=request_bytes, method="POST")
            req.add_header("Content-Type", "application/json")
            print(f"[agent] HTTP POST to {url} (attempt {attempt + 1})", file=sys.stderr)

            with _urlreq.urlopen(req, timeout=30) as resp:
                response_body = resp.read()
                print(f"[agent] HTTP status: {resp.status}, headers: {dict(resp.headers)}", file=sys.stderr)
                response = json.loads(response_body.decode("utf-8"))
                print(f"[agent] Received HTTP response ({len(response_body)} bytes): {response}", file=sys.stderr)
                return response

        except _urlerr.URLError as e:
            print(f"[agent] URLError: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise RuntimeError(f"Failed to reach proxy after {max_retries + 1} attempts: {e}")

            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
            print(f"[agent] Proxy unreachable (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay:.2f}s", file=sys.stderr)
            time.sleep(delay)
        except Exception as e:
            print(f"[agent] Unexpected error: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise RuntimeError(f"Unexpected error after {max_retries + 1} attempts: {e}")
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
            print(f"[agent] Unexpected error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay:.2f}s", file=sys.stderr)
            time.sleep(delay)

    # Should never reach here
    raise RuntimeError("Exhausted retries while contacting proxy")

# ---------------------------------------------------------------------------
# Helper utilities -----------------------------------------------------------
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = MAX_OBS_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"

# ---------------------------------------------------------------------------
# Main agent logic -----------------------------------------------------------
# ---------------------------------------------------------------------------

def run_agent(problem_text: str, *, proxy_url: str, timeout: int, model_name: str, run_id: str) -> None:
    working_dir = os.getcwd()

    system_prompt = DEFAULT_SYSTEM_PROMPT
    instance_prompt = DEFAULT_INSTANCE_TEMPLATE.format(
        working_dir=working_dir, problem_statement=problem_text
    )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": instance_prompt},
    ]

    traj_file = Path("trajectory.jsonl").open("w", encoding="utf-8")
    start_time = time.time()

    for step in range(1, MAX_STEPS + 1):
        if time.time() - start_time > timeout:
            print("[agent] global timeout reached", file=sys.stderr)
            sys.exit(1)

        proxy_resp = _send_json_request(proxy_url, "/agents/inference", {
            "run_id": run_id,
            "input_text": instance_prompt,
            "return_text": True,
            "return_code": True,
            "model": model_name,
        })

        text_resp = (proxy_resp.get("text_response") or "").strip()
        code_resp = (proxy_resp.get("code_response") or "").strip()

        # Try diff first – model may send patch in code_response
        if code_resp and (code_resp.lstrip().startswith("diff") or code_resp.lstrip().startswith("--- ")):
            assistant_msg = {"role": "assistant", "content": None,
                             "tool_call": {"name": "APPLY_PATCH", "arguments": {"patch": code_resp}}}
            call = assistant_msg["tool_call"]
            messages.append(assistant_msg)
            thought_text = text_resp  # may be empty
        else:
            # Parse JSON tool call from text_response (preferred) or code_response fallback

            def _extract_call(raw: str) -> Dict[str, Any] | None:
                try:
                    obj = json.loads(raw)
                except Exception:
                    return None

                # Shape 1: {"name": ..., "arguments": {...}}
                if isinstance(obj, dict) and "name" in obj:
                    return {"name": obj["name"], "arguments": obj.get("arguments", {})}

                # Shape 2: {"tool_calls": [ {"function": {"name":..., "arguments":"{...}"}} ]}
                if isinstance(obj, dict) and "tool_calls" in obj:
                    try:
                        fc = obj["tool_calls"][0]["function"]
                        args_str = fc.get("arguments", "{}")
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        return {"name": fc["name"], "arguments": args}
                    except Exception:
                        return None

                return None

            call = _extract_call(text_resp) or _extract_call(code_resp)

            thought_text = text_resp if call else text_resp or code_resp

            if call:
                messages.append({"role": "assistant", "content": None, "tool_call": call})
            else:
                messages.append({"role": "assistant", "content": thought_text})
                traj_file.write(json.dumps({"assistant": {"content": thought_text}}) + "\n")
                traj_file.flush()
                continue  # nothing to execute this turn

        name = call["name"]
        args = call.get("arguments", {}) or {}

        # Execute tool
        try:
            result = TOOLS[name](**args) if args else TOOLS[name]()
        except Exception:
            result = "Tool execution error:\n" + traceback.format_exc()

        result_trunc = _truncate(result)

        # Append messages to history
        messages.append({"role": "assistant", "content": thought_text})
        messages.append({"role": "assistant", "tool_call": call})
        messages.append({"role": "tool", "name": name, "content": result_trunc})

        traj_file.write(json.dumps({"assistant": {"thought": thought_text, "action": call}}) + "\n")
        traj_file.write(json.dumps({"tool_result": {"name": name, "content": result_trunc}}) + "\n")
        traj_file.flush()

        if name == "FINISH":
            print("[agent] FINISH called – exiting.")
            traj_file.close()
            sys.exit(0)

    print("[agent] max steps exceeded", file=sys.stderr)
    traj_file.close()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Compatibility wrapper for validator import style ---------------------------
# ---------------------------------------------------------------------------

def agent_main(input_dict: Dict[str, Any]):
    """Entry-point expected by the validator legacy interface.

    Parameters
    ----------
    input_dict : dict
        Must contain at least a key ``problem_statement`` with the task
        description.  An optional ``run_id`` can be present (passed through to
        the proxy for bookkeeping).
    """

    problem_text = input_dict.get("problem_statement")
    if not problem_text:
        raise ValueError("input_dict must contain 'problem_statement'.")

    # Environment overrides (the validator sets these); fall back to CLI defaults.
    proxy_url = os.getenv("AI_PROXY_URL", DEFAULT_PROXY_URL)
    timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))
    model_name = os.getenv("AGENT_MODEL", DEFAULT_MODEL)

    # Force one-shot MiniLM retrieval mode for every run.
    run_id = input_dict.get("run_id", "")
    if not run_id:
        raise ValueError("input_dict must contain 'run_id' for API calls.")
    
    patch_text = run_oneshot(
        problem_text,
        proxy_url=proxy_url,
        model_name=model_name,
        run_id=run_id,
    )

    return {"patch": patch_text}

# Ensure both import-style and CLI execution work

if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except Exception:
        payload = {}
    agent_main(payload)

# ---------------------------------------------------------------------------
# Function specs (for iterative chat loop) ----------------------------------
# ---------------------------------------------------------------------------

FUNCTION_SPECS: List[Dict[str, Any]] = [
    {"name": "LS", "description": "List directory contents.", "parameters": {"type": "object", "properties": {"dir": {"type": "string", "description": "Directory path", "default": "."}}}},
    {
        "name": "FIND",
        "description": "Recursively search files for a regex pattern and return matching lines.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "dir": {"type": "string", "default": "."}},
            "required": ["pattern"],
        },
    },
    {
        "name": "READ_FILE",
        "description": "Read up to max_bytes bytes from a file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer", "default": MAX_BYTES_READ}},
            "required": ["path"],
        },
    },
    {
        "name": "DIFF",
        "description": "Return unified diff between two files.",
        "parameters": {
            "type": "object",
            "properties": {"path1": {"type": "string"}, "path2": {"type": "string"}},
            "required": ["path1", "path2"],
        },
    },
    {
        "name": "WRITE_FILE",
        "description": "Write content to a file (overwrites if exists).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "APPLY_PATCH",
        "description": "Apply a unified diff patch to the repository root using the patch command.",
        "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"]},
    },
    {"name": "FINISH", "description": "Signal that all tasks are complete.", "parameters": {"type": "object", "properties": {}}},
]

# Dry-run version – returns (applies_cleanly: bool, output: str)
def _dry_run_patch(patch: str) -> tuple[bool, str]:
    try:
        with NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(patch)
            tmp_path = tmp.name

        def _run(p_level: str):
            return subprocess.run(
                ["patch", "--dry-run", p_level, "--forward", "--reject-file=-", "-i", tmp_path],
                text=True,
                capture_output=True,
                timeout=60,
            )

        out = ""
        ok = False
        for level in ("-p1", "-p0", "-p2", "-p3"):
            proc = _run(level)
            out += f"\n--- dry-run {level} ---\n" + proc.stdout + proc.stderr
            if proc.returncode in (0, 1):
                ok = True
                break

        # Fallback to git apply --check
        if not ok:
            git_proc = subprocess.run(["git", "apply", "--check", tmp_path], text=True, capture_output=True)
            out += "\n--- git apply --check ---\n" + git_proc.stdout + git_proc.stderr
            ok = git_proc.returncode == 0
        return ok, out
    except Exception as e:
        return False, "dry-run error: " + str(e)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass