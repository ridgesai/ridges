#!/usr/bin/env python3
"""One-file autonomous coding agent ("base-miner").

The validator mounts a repository and a problem statement inside a sandbox and
executes this file.  It talks to a local *AI-proxy* over HTTP using the
OpenAI function-calling protocol.  It exposes a minimal but sufficient set of
tools so the language-model can inspect and modify the repository and finally
indicate it is done.

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
--proxy   AI_PROXY_URL   http://sandbox_proxy
--problem PROBLEM_FILE    ./PROBLEM.md
--repo    REPO_ROOT       .
--timeout AGENT_TIMEOUT   600  (seconds)
--model   AGENT_MODEL     deepseek-ai/DeepSeek-V3-0324

A `trajectory.jsonl` file with the full conversation is written for debugging.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
import time
import traceback
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, NamedTuple
import urllib.request as _urlreq
import urllib.error as _urlerr
from dataclasses import dataclass
import ast
import re
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

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
MAX_EMBED_TOKENS = 512000
MAX_EMBED_CHARS = MAX_EMBED_TOKENS * 4  # reserve but we'll split by tokens

# New env flag – set EMBED_WHOLE_FILES=1 to revert to legacy behaviour.
USE_FUNCTION_CHUNKS = os.getenv("EMBED_WHOLE_FILES", "0") != "1"

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
# One-shot mode system prompt ----------------------------------------------
# ---------------------------------------------------------------------------

ONESHOT_SYSTEM_PROMPT = (
    "You are an autonomous programmer. The user will provide a bug report or "
    "feature request (the \"problem\") plus a compact summary of the most "
    "relevant repository files.  Your job is to return ONE *valid* unified "
    "diff patch that fixes the problem. If you have any questions, do not ask the user. "
    "Instead, solve it to the best of your ability with the knowledge you have.\n\n"
    "STRICT FORMAT RULES\n"
    "1. Return *only* the diff – no prose, no Markdown back-ticks.\n"
    "2. The diff must start with 'diff --git a/<path> b/<path>' followed by "
    "   the standard \"--- a/<path>\" and \"+++ b/<path>\" headers.\n"
    "3. Use -u style context hunks that begin with lines like @@ -N,M +N,M @@.\n"
    "4. Every changed file needs its own header block as in rule 2.\n"
    "5. End the patch with a trailing newline.\n\n"
    "Be exact: if the diff is syntactically malformed or wrapped in extra "
    "text the automated patch tool will fail.\n\n"
    "OUTPUT RULES (VERY IMPORTANT)\n"
    "• You MUST end your reply with a *raw* JSON object – nothing else – that has exactly two keys: 'text_response' and 'code_response'.\n"
    "• Do NOT surround the JSON with Markdown back-ticks or any other fencing.\n"
    "• 'text_response' can hold arbitrary explanatory text (may be empty).\n"
    "• 'code_response' must hold the unified diff from rules 1-5 *verbatim*.\n"
    "Example: {\"text_response\": \"<explanation>\", \"code_response\": \"diff --git a/foo.py b/foo.py\n...\"}."
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
# Embedding cache and helper -------------------------------------------------
# ---------------------------------------------------------------------------

_EMBED_CACHE: Dict[str, List[float]] = {}
ZERO_VEC: List[float] = [0.0] * 1024  # embedding for empty input


def _remote_embed(text: str, proxy_url: str) -> List[float]:
    """Return embedding vector for *text* via the proxy /agents/embedding endpoint.

    Caches results in-memory to avoid duplicate HTTP calls.
    """
    # Short-circuit empty or whitespace-only inputs.
    if not text.strip():
        return _EMBED_CACHE.setdefault("", [0.0] * 1024)

    # Retry–shrink loop to satisfy 512-token limit
    attempt_text = text
    for _ in range(2):  # original + 1 retry after halving
        tokens = attempt_text.split()
        if len(tokens) > MAX_EMBED_TOKENS:
            attempt_text = " ".join(tokens[:MAX_EMBED_TOKENS])

        url = f"{proxy_url.rstrip('/')}/agents/embedding"
        req = _urlreq.Request(
            url,
            data=json.dumps({"input": attempt_text}, ensure_ascii=False).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with _urlreq.urlopen(req, timeout=300) as resp:
                data_raw = resp.read()
                data = json.loads(data_raw.decode())

                if isinstance(data, list):
                    vec = data[0] if (len(data) == 1 and isinstance(data[0], list)) else data
                    _EMBED_CACHE[text] = vec
                    return vec
                if isinstance(data, dict) and "embedding" in data:
                    vec = data["embedding"]
                    _EMBED_CACHE[text] = vec
                    return vec

                # If we received a validation error about tokens, halve and retry
                if isinstance(data, dict) and data.get("error_type") == "Validation":
                    attempt_text = " ".join(tokens[: len(tokens) // 2])
                    continue

                raise ValueError(f"unexpected embedding response: {data!r}")
        except Exception as exc:
            if "less than 512 tokens" in str(exc):
                attempt_text = " ".join(tokens[: len(tokens) // 2])
                continue
            raise RuntimeError(f"embedding request failed: {exc}")

    # If all retries failed, return zero vector to keep pipeline alive
    _EMBED_CACHE[text] = ZERO_VEC
    return ZERO_VEC


def _cosine(u: List[float], v: List[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    s = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu == 0 or nv == 0:
        return 0.0
    return s / (nu * nv)

# ---------------------------------------------------------------------------
# One-shot retrieval using remote embeddings --------------------------------
# ---------------------------------------------------------------------------

def run_oneshot(
    problem_text: str,
    *,
    proxy_url: str,
    model_name: str,
    run_id: str,
    top_k: int = 30,
) -> str:
    """Build repository summary and send a single LLM call.

    Embeddings are fetched from the internal `/agents/embedding` proxy endpoint, so no model weights or internet access are required inside the sandbox.
    """

    if USE_FUNCTION_CHUNKS:
        code_chunks = _collect_code_chunks()
        if not code_chunks:
            raise RuntimeError("repository appears empty – nothing to embed")
        chunk_texts = [c.text for c in code_chunks]
    else:
        repo_texts = _collect_repo_texts()
        if not repo_texts:
            raise RuntimeError("repository appears empty – nothing to embed")
        code_chunks = [Chunk(file=fp, start_line=1, end_line=text.count("\n") + 1, text=text) for fp, text in repo_texts.items()]
        chunk_texts = [c.text for c in code_chunks]

    # --------------------------------------------------------------------
    # Cheap TF-IDF pre-filter to limit expensive embedding calls ----------
    # --------------------------------------------------------------------

    PRE_FILTER_TOP = int(os.getenv("PREFILTER_TOP", "400"))

    if len(chunk_texts) > PRE_FILTER_TOP:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

            tfidf_vec = TfidfVectorizer(stop_words="english", max_features=20_000)
            mat = tfidf_vec.fit_transform([problem_text] + chunk_texts).astype("float32")

            query_vec = mat[0]
            chunk_mat = mat[1:]
            sims_quick = (chunk_mat @ query_vec.T).toarray().ravel()  # cosine; TF-IDF rows are L2-normed

            top_idx_quick = sims_quick.argsort()[-PRE_FILTER_TOP:][::-1]

            chunk_texts = [chunk_texts[i] for i in top_idx_quick]
            code_chunks = [code_chunks[i] for i in top_idx_quick]
        except Exception as _e:
            # If sklearn unavailable, skip pre-filter gracefully.
            pass

    # --------------------------------------------------------------------
    # Obtain embeddings via proxy endpoint --------------------------------
    # --------------------------------------------------------------------

    query_vec = _remote_embed(problem_text, proxy_url)

    # Parallel embedding of chunks to avoid long serial wait times.
    chunk_vecs: List[List[float]] = [ZERO_VEC] * len(chunk_texts)
    MAX_WORKERS = int(os.getenv("EMBED_CONCURRENCY", "8"))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fut_to_idx = {pool.submit(_remote_embed, txt, proxy_url): idx for idx, txt in enumerate(chunk_texts)}

        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            try:
                chunk_vecs[idx] = fut.result()
            except Exception as exc:
                # Log and keep zero vector; retrieval will simply rank it low.
                print(f"[agent] embedding error (chunk {idx}): {exc}")
                chunk_vecs[idx] = ZERO_VEC

    sims = [ _cosine(vec, query_vec) for vec in chunk_vecs ]

    # --------------------------------------------------------------------
    # Light path-based bonus if filename is mentioned in the problem text --
    # --------------------------------------------------------------------
    prob_lower = problem_text.lower()
    for idx, ch in enumerate(code_chunks):
        base = os.path.basename(ch.file).lower()
        if base in prob_lower or base.split(".")[0] in prob_lower:
            sims[idx] += 0.2

    sorted_idx = sorted(range(len(sims)), key=lambda i: -sims[i])

    TARGET_TOKENS = 12_000
    token_budget = int(TARGET_TOKENS * 0.85)
    token_total = 0
    top_idx: list[int] = []
    for idx in sorted_idx:
        tok = _guess_tokens(chunk_texts[idx])
        if token_total + tok > token_budget:
            break
        token_total += tok
        top_idx.append(idx)

    # Fallback to at most top_k if budget yields too many
    if len(top_idx) > top_k:
        top_idx = top_idx[:top_k]

    summary_parts: list[str] = []
    for idx in top_idx:
        ch = code_chunks[idx]
        body = ch.text[:2000]
        tag = _lang_tag(ch.file)
        header = f"### {ch.file}:L{ch.start_line}-{ch.end_line}"
        summary_parts.append(f"{header}\n```{tag}\n{body}\n```")

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

    ATTEMPTS = 8

    for attempt in range(ATTEMPTS):
        messages = [
            {"role": "system", "content": ONESHOT_SYSTEM_PROMPT},
            {"role": "user", "content": problem_text},
            {"role": "user", "content": "Repository summary (top files):\n\n" + repo_summary},
        ]

        try:
            proxy_resp = inference(messages, proxy_url, run_id, model_name)
        except Exception as e:
            print(f"[agent] Request failed (attempt {attempt + 1}): {e}")
            if attempt == ATTEMPTS - 1:
                raise RuntimeError(f"All {ATTEMPTS} attempts failed: {e}")
            continue
        
        # proxy_resp is already a dict from inference(); no need to decode again.
        if isinstance(proxy_resp, str):
            # In case inference ever returns plain text, fail fast for inspection.
            raise Exception(proxy_resp)

        text_resp = (proxy_resp.get("text_response") or "").lstrip()
        code_resp = (proxy_resp.get("code_response") or "").lstrip()

        patch_text = None
        for cand in (text_resp, code_resp):
            if cand and (cand.startswith("diff") or cand.startswith("--- ")):
                patch_text = cand
                break

        if patch_text is None:
            print(f"[agent] No valid patch found in response. text_response: {text_resp[:200]}...")
            print(f"[agent] code_response: {code_resp[:200]}...")
            print(f"[agent] Full proxy response: {proxy_resp}")
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

def inference(messages: List[Dict[str, Any]], proxy_url: str, run_id: str, model: str = None) -> dict:
    """Send inference request to the proxy and return the response."""
    # Build request data
    request_data = {
        "run_id": run_id,
        "messages": messages,
    }
    
    if model:
        request_data["model"] = model

    # Send HTTP request
    url = f"{proxy_url.rstrip('/')}/agents/inference"
    request_bytes = json.dumps(request_data, ensure_ascii=False).encode('utf-8')
    
    print(f"[agent] Making inference request to {url}")
    print(f"[agent] Request data: {request_data}")
    
    try:
        req = _urlreq.Request(url, data=request_bytes, method="POST")
        req.add_header("Content-Type", "application/json")
        
        with _urlreq.urlopen(req, timeout=300) as resp:
            response_body = resp.read()
            print(f"[agent] HTTP {resp.status} from {url} ({len(response_body)} bytes)")
            
            response_json = json.loads(response_body.decode("utf-8"))
            print(f"[agent] Response: {response_json}")

            # The proxy may return a plain string instead of a JSON object with
            # separate text / code fields.  In that case we wrap it in the
            # expected shape so downstream logic can stay unchanged.
            if isinstance(response_json, str):
                raw_text: str = response_json.lstrip()

                # Attempt to separate a unified diff from the explanatory text.
                diff_start = None
                if raw_text.startswith("diff") or raw_text.startswith("--- "):
                    diff_start = 0
                else:
                    # Look for the first occurrence of a diff header inside the text.
                    for marker in ("\ndiff --git", "\n--- "):
                        idx = raw_text.find(marker)
                        if idx != -1:
                            diff_start = idx + 1  # skip the leading newline
                            break

                code_resp = ""
                text_resp = raw_text
                if diff_start is not None:
                    code_resp = raw_text[diff_start:].lstrip()
                    text_resp = raw_text[:diff_start].strip()

                response_json = {"text_response": text_resp, "code_response": code_resp}

            return response_json
            
    except Exception as e:
        print(f"[agent] Inference request failed: {e}")
        raise RuntimeError(f"Inference request failed: {e}")

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

def run_agent(problem_text: str, *, proxy_url: str, timeout: int, model_name: str, run_id: str) -> str:
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
            print("[agent] global timeout reached")
            return "Global timeout reached"

        proxy_resp = inference(messages, proxy_url, run_id, model_name)

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
            return "FINISH called"

    print("[agent] max steps exceeded")
    traj_file.close()
    return "Max steps exceeded"


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

    # If the validator mounted the target repository at /sandbox/repo, switch
    # to that directory so that all relative file paths and patch operations
    # resolve correctly.  If the directory is absent we keep the current CWD
    # (useful for local testing).
    repo_root = Path("/sandbox/repo")
    if repo_root.exists() and repo_root.is_dir():
        os.chdir(repo_root)

    # Always use one-shot retrieval with proxy-hosted embeddings.
    patch_text = run_oneshot(
        problem_text,
        proxy_url=proxy_url,
        model_name=model_name,
        run_id=input_dict.get("run_id", ""),
    )

    return {"patch": patch_text}

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

# ---------------------------------------------------------------------------
# Misc helper functions ------------------------------------------------------
# ---------------------------------------------------------------------------

def _lang_tag(path: str) -> str:
    """Return a language tag for markdown fencing based on file extension."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
        ".go": "go",
    }.get(ext, "")


def _collect_repo_texts(root: str = ".") -> Dict[str, str]:
    """Return {rel_path: text} for every text file under *root* (best-effort)."""
    texts: Dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        if ".git" in dirpath.split(os.sep):
            continue
        for fn in files:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    texts[os.path.relpath(path, root)] = fh.read()
            except Exception:
                # binary or unreadable
                continue
    return texts

# ---------------------------------------------------------------------------
# Chunk representation and collector ---------------------------------------
# ---------------------------------------------------------------------------

from typing import NamedTuple


class Chunk(NamedTuple):
    file: str
    start_line: int
    end_line: int
    text: str


def _guess_tokens(text: str) -> int:
    """Rough heuristic: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)


def _collect_code_chunks(root: str = ".") -> List[Chunk]:
    """Walk the repository and split source files into function/class chunks.

    • Python: real AST split.
    • Other langs: regex heuristic on `function` / `class` keywords.
    • Files without matches fall back to one chunk (whole file).
    """
    chunks: List[Chunk] = []
    root_path = Path(root)

    for path in root_path.rglob("*"):
        if path.is_dir() or ".git" in path.parts:
            continue
        rel_path = str(path.relative_to(root_path))

        # Skip the agent itself (both the original and sandbox copy) to avoid polluting retrieval.
        if rel_path == "miner/agent.py" or rel_path == "src/agent.py" or rel_path.endswith("/miner/agent.py"):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue  # binary/unreadable

        if rel_path.endswith(".py"):
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and hasattr(node, "end_lineno"):
                        start, end = node.lineno, node.end_lineno
                        lines = text.splitlines()[start - 1 : end]
                        # break very long bodies into 400-line windows
                        MAX_LINES = 400
                        for i in range(0, len(lines), MAX_LINES):
                            sub_lines = lines[i : i + MAX_LINES]
                            if not sub_lines:
                                continue
                            sub_text = "\n".join(sub_lines)
                            # further split on tokens <= 512
                            offset = 0
                            for piece in _token_windows(sub_text):
                                piece_lines = piece.count("\n")
                                sub_start = start + i + sub_text[:offset].count("\n")
                                sub_end = sub_start + piece_lines
                                offset += len(piece) + 1  # approximate advance
                                chunks.append(Chunk(rel_path, sub_start, sub_end, piece))
            except Exception:
                for piece in _token_windows(text):
                    end_line = piece.count("\n") + 1
                    chunks.append(Chunk(rel_path, 1, end_line, piece))
        else:
            pattern = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\b|^\s*class\b", re.MULTILINE)
            indices = [m.start() for m in pattern.finditer(text)]
            if not indices:
                chunks.append(Chunk(rel_path, 1, text.count("\n") + 1, text))
            else:
                indices.append(len(text))
                for j, start_char in enumerate(indices[:-1]):
                    end_char = indices[j + 1]
                    chunk_text = text[start_char:end_char]
                    start_line = text[:start_char].count("\n") + 1
                    end_line = start_line + chunk_text.count("\n")
                    chunks.append(Chunk(rel_path, start_line, end_line, chunk_text))

                    if not chunk_text.strip():
                        continue  # skip empty slices

                    # enforce embedding size limit by token windows
                    offset_chars = 0
                    for piece in _token_windows(chunk_text):
                        off_start_line = start_line + chunk_text[:offset_chars].count("\n")
                        off_end_line = off_start_line + piece.count("\n")
                        chunks.append(Chunk(rel_path, off_start_line, off_end_line, piece))
                        offset_chars += len(piece) + 1

    # Final safety: truncate if some text >512 tokens slipped through.
    tokens = text.split()
    if len(tokens) > MAX_EMBED_TOKENS:
        text = " ".join(tokens[:MAX_EMBED_TOKENS])

    return chunks

# Helper to split long texts by token count
def _token_windows(text: str, max_tokens: int = MAX_EMBED_TOKENS) -> List[str]:
    words = text.split()
    windows: List[str] = []
    for i in range(0, len(words), max_tokens):
        seg = " ".join(words[i : i + max_tokens])
        if seg.strip():
            windows.append(seg)
    return windows or [""]