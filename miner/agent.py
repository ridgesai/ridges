from __future__ import annotations

import json
import os
import subprocess
import textwrap
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, NamedTuple
import urllib.request as _urlreq
import urllib.error as _urlerr
import ast
import re
import math

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

# ---------------------------------------------------------------------------
# Defaults via environment variables ----------------------------------------
# ---------------------------------------------------------------------------
DEFAULT_PROXY_URL = os.getenv("AI_PROXY_URL", "http://sandbox_proxy")
DEFAULT_MODEL = "moonshotai/Kimi-K2-Instruct"

# Hybrid mode constants
MAX_EXPLORATION_STEPS = int(os.getenv("MAX_EXPLORATION_STEPS", "55"))
MAX_OBS_CHARS = 20_000
MAX_BYTES_READ = 65_000

# Iterative refinement constants
MAX_REFINEMENT_ITERATIONS = int(os.getenv("MAX_REFINEMENT_ITERATIONS", "4"))
TEST_TIMEOUT_SECONDS = int(os.getenv("TEST_TIMEOUT_SECONDS", "300"))

# Test analysis constants
TEST_DISCOVERY_TIMEOUT = int(os.getenv("TEST_DISCOVERY_TIMEOUT", "120"))
REPRODUCTION_TEST_TIMEOUT = int(os.getenv("REPRODUCTION_TEST_TIMEOUT", "120"))

# Use function chunks by default
USE_FUNCTION_CHUNKS = os.getenv("EMBED_WHOLE_FILES", "0") != "1"

# Zero vector for failed embeddings
ZERO_VEC = [0.0] * 768

# Available commands for exploration
EXPLORATION_COMMANDS = {
    "READ_FILE": "READ_FILE(path): Read a file up to 15KB. Takes ONLY ONE argument (file path). Usage: READ_FILE(\"path/to/file.py\")",
    "FIND": "FIND(pattern): Find files by name pattern. Usage: FIND(\"*.py\") or FIND -name \"*.py\" -type f",
    "LS": "LS(dir): List directory contents. Usage: LS(\".\") or LS -la or LS -R for recursive",
    "CD": "CD(dir): Change current directory. Usage: CD(\"src/\") or CD(\"..\") to go up",
    "GREP": "GREP(pattern, path): Search for pattern in files. Usage: GREP(\"def function_name\", \"src/\") or GREP -A 5 -B 2 \"pattern\" \"file\"",
    "SMART_SEARCH": "SMART_SEARCH(): Use AI to find most relevant files based on problem. Provides intelligent suggestions. Usage: SMART_SEARCH()",
    "RUN_TESTS": "RUN_TESTS(test_file): Run specific test file to understand expected behavior. Usage: RUN_TESTS(\"test_engine.py\") or RUN_TESTS -v -x test.py",
    "FINISH": "FINISH(result): End exploration with findings. Usage: FINISH({\"target_files\": [...], \"problem_location\": \"description\"})"
}

# Structured system prompt for exploration
EXPLORATION_SYSTEM_PROMPT = textwrap.dedent(
    """
    You are a methodical code exploration specialist, an AI assistant that efficiently locates bugs in codebases.

    <ROLE>
    Your primary role is to identify the specific location where a bug exists in the codebase. You should be thorough, methodical, and prioritize accuracy over speed.
    * Do NOT try to fix the problem - your job is to FIND where it needs to be fixed
    * When you locate the problematic file(s), use FINISH to report your findings
    </ROLE>

    <EFFICIENCY>
    * Each exploration step is expensive. Wherever possible, use strategic approaches:
      - Use GREP to search for relevant keywords before reading entire files
      - Use SMART_SEARCH to find the most relevant files based on the problem description
      - Combine related searches when possible
    * When exploring the codebase, use efficient patterns like grep with specific search terms
    </EFFICIENCY>

    <EXPLORATION_STRATEGY>
    * Start broad, then narrow down:
      1. Use SMART_SEARCH to identify candidate files
      2. Use GREP to search for specific error messages, function names, or patterns
      3. Use READ_FILE to examine the most promising files
      4. Use targeted searches to understand the context
    * Look for:
      - Error messages mentioned in the problem description
      - Function/class names referenced in stack traces
      - File patterns that match the problem domain
    </EXPLORATION_STRATEGY>

    <PROBLEM_SOLVING_WORKFLOW>
    1. ANALYSIS: Understand the problem description and extract key search terms
    2. BROAD_SEARCH: Use SMART_SEARCH and GREP to identify candidate locations
    3. FOCUSED_EXPLORATION: Read promising files and understand the context
    4. VERIFICATION: Confirm you've found the correct location of the issue
    5. CONCLUSION: Use FINISH to report the target files and problem location
    </PROBLEM_SOLVING_WORKFLOW>

    <TROUBLESHOOTING>
    * If you can't find the issue immediately:
      1. Extract different keywords from the problem description
      2. Search for related concepts (e.g., if looking for "validation", try "check", "verify", "validate")
      3. Look in test files for clues about expected behavior
      4. Consider different file extensions or directory structures
    * If you're getting truncated files, use GREP to find specific patterns within them
    </TROUBLESHOOTING>

    <AVAILABLE_COMMANDS>
    {command_docs}
    </AVAILABLE_COMMANDS>

    <RESPONSE_FORMAT>
    Your output should include _one_ discussion and _one_ command field EXACTLY as in this example:
    DISCUSSION
    I'll search for the Engine class and render_to_string method mentioned in the error.
    ```
    GREP("class Engine", ".")
    ```

    Use only ONE command per response and wait for results before continuing.
    </RESPONSE_FORMAT>
    """
)

# Structured system prompt for patch generation
PATCH_GENERATION_SYSTEM_PROMPT = textwrap.dedent(
    """
    You are a methodical patch generation specialist, an AI that creates precise fixes for code issues.

    <ROLE>
    Your primary role is to generate a single, correct unified diff patch that fixes the reported bug. You should be thorough, precise, and prioritize correctness over speed.
    * Generate EXACTLY ONE unified diff patch that solves the problem
    * If you have questions, solve them using your best judgment - do not ask the user
    </ROLE>

    <CODE_QUALITY>
    * Make minimal changes needed to solve the problem - avoid unnecessary modifications
    * Preserve existing code style, formatting, and conventions
    * Ensure your changes don't break existing functionality
    * Focus on the root cause rather than symptoms
    </CODE_QUALITY>

    <PROBLEM_SOLVING_APPROACH>
    1. ANALYSIS: Understand the bug report and exploration findings thoroughly
    2. ROOT_CAUSE: Identify the core issue that needs to be fixed
    3. SOLUTION_DESIGN: Plan the minimal change needed to resolve the problem
    4. IMPLEMENTATION: Generate the precise diff with correct syntax and formatting
    5. VERIFICATION: Ensure the patch addresses the problem completely
    </PROBLEM_SOLVING_APPROACH>

    <PATCH_QUALITY>
    * Use proper unified diff format with correct headers and line numbers
    * Include sufficient context lines (typically 3) around changes
    * Preserve exact indentation and whitespace from the original file
    * Make sure line numbers and file paths are accurate
    * Test your logic mentally - will this change solve the reported issue?
    </PATCH_QUALITY>

    <CRITICAL_OUTPUT_RULES>
    1. Your response must be EXACTLY the diff - absolutely nothing else
    2. NO explanatory text before, after, or mixed with the diff
    3. NO markdown formatting, NO backticks, NO code blocks
    4. NO "Looking at the problem..." or "The solution is..." text
    5. Start immediately with "diff --git a/..."
    6. End immediately after the last diff line

    WRONG EXAMPLES:
    ❌ "Looking at the bug report... diff --git..."
    ❌ "```diff\ndiff --git..."
    ❌ "The solution is:\n\ndiff --git..."

    CORRECT EXAMPLE:
    ✅ diff --git a/path/to/file.py b/path/to/file.py
    index abc123..def456 100644
    --- a/path/to/file.py
    +++ b/path/to/file.py
    @@ -10,7 +10,7 @@ def function():
         context_line
    -    old_line
    +    new_line
         context_line

    RESPONSE FORMAT ENFORCEMENT:
    - If you include ANY text other than the diff, the patch will be rejected
    - The diff must be syntactically perfect with correct line numbers
    - Preserve exact indentation from the original file
    - Use standard unified diff format with proper headers
    </CRITICAL_OUTPUT_RULES>
    """
).strip()

# Structured system prompt for patch refinement
PATCH_REFINEMENT_SYSTEM_PROMPT = textwrap.dedent(
    """
    You are a methodical patch refinement specialist, an AI that improves code fixes based on test failures.

    <ROLE>
    Your role is to analyze why a patch failed testing and generate an improved version. You have:
    * The original problem description
    * A patch that was applied but failed tests
    * Detailed test failure information
    * The goal to create a refined patch that passes tests
    </ROLE>

    <REFINEMENT_APPROACH>
    1. ANALYZE: Study the test failures to understand what went wrong
    2. DIAGNOSE: Identify the root cause of the failure
    3. STRATEGIZE: Plan a different or improved approach
    4. IMPLEMENT: Generate a refined patch with correct syntax
    5. VALIDATE: Ensure the refined approach addresses the test failures
    </REFINEMENT_APPROACH>

    <FAILURE_ANALYSIS>
    * Compilation errors: Fix syntax, imports, or API usage issues
    * Test failures: Address logic errors or incorrect assumptions
    * Runtime errors: Handle edge cases or missing error handling
    * Integration issues: Consider cross-module dependencies
    </FAILURE_ANALYSIS>

    <CRITICAL_OUTPUT_RULES>
    1. Your response must be EXACTLY the refined diff - absolutely nothing else
    2. NO explanatory text before, after, or mixed with the diff
    3. Start immediately with "diff --git a/..."
    4. Learn from the previous failure and try a different approach
    5. Use proper unified diff format with correct line numbers
    </CRITICAL_OUTPUT_RULES>
    """
).strip()

def _ls(dir: str = ".") -> str:
    """List files and directories in the given directory."""
    try:
        result = subprocess.run(["ls", "-la", dir], capture_output=True, text=True, check=False, timeout=10)
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        return f"Error running ls: {e}"

def _find(pattern: str, dir: str = ".") -> str:
    """Find files matching the given pattern in the directory tree."""
    try:
        result = subprocess.run(["find", dir, "-name", pattern], capture_output=True, text=True, check=False, timeout=30)
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        return f"Error running find: {e}"

def _read_file(path: str, max_bytes: int = MAX_BYTES_READ) -> str:
    """Read a file, truncating if it's too large."""
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(max_bytes)
            if len(content) == max_bytes:
                content += "\n... [FILE TRUNCATED] ..."
            return content
    except Exception as e:
        return f"Error reading file: {e}"



def extract_diff_from_response(response_text: str) -> str:
    """Extract diff from mixed text response as fallback when model includes explanations."""
    if not response_text:
        return None

    # Primary: Look for complete diff pattern with proper structure
    diff_match = re.search(r'(diff --git.*?)(?=\n\n\w+|\n\n[A-Z]|\Z)', response_text, re.DOTALL)
    if diff_match:
        extracted = diff_match.group(1).strip()
        if extracted.count('diff --git') >= 1 and ('@@' in extracted or '---' in extracted):
            return extracted

    # Secondary: Look for diff start and extract until end of diff content
    lines = response_text.split('\n')
    diff_lines = []
    in_diff = False

    for i, line in enumerate(lines):
        if line.startswith('diff --git'):
            in_diff = True
            diff_lines = [line]
        elif in_diff:
            # Valid diff line patterns
            if (line.startswith(('---', '+++', '@@', '+', '-', ' ')) or
                line.strip() == '' or
                line.startswith('index ') or
                line.startswith('new file mode') or
                line.startswith('deleted file mode')):
                diff_lines.append(line)
            else:
                # Check if this looks like explanatory text after diff
                if (line and not line.startswith(('diff --git', '---', '+++', '@@', '+', '-', ' ')) and
                    any(word in line.lower() for word in ['explanation', 'this', 'the', 'fix', 'change', 'solution'])):
                    # End of diff - explanatory text found
                    break
                elif line.strip():
                    # Non-empty line that doesn't match diff format - might be end
                    # Only add if it looks like a valid context line (starts with space)
                    if line.startswith(' '):
                        diff_lines.append(line)
                    else:
                        # Probably end of diff
                        break
                else:
                    diff_lines.append(line)

    if diff_lines and diff_lines[0].startswith('diff --git'):
        # Ensure proper diff termination
        result = '\n'.join(diff_lines)
        # Add final newline if not present
        if not result.endswith('\n'):
            result += '\n'
        return result

    return None


def _validate_patch_structure(patch: str) -> tuple[bool, str]:
    """Validate that a patch has proper unified diff structure."""
    if not patch or not patch.strip():
        return False, "Empty patch"

    lines = patch.strip().split('\n')
    if not lines[0].startswith('diff --git'):
        return False, "Patch must start with 'diff --git'"

    # Check for required sections
    has_index = any(line.startswith('index ') for line in lines)
    has_minus_file = any(line.startswith('--- ') for line in lines)
    has_plus_file = any(line.startswith('+++ ') for line in lines)
    has_hunk = any(line.startswith('@@') for line in lines)

    if not has_minus_file:
        return False, "Missing '---' file marker"
    if not has_plus_file:
        return False, "Missing '+++' file marker"
    if not has_hunk:
        return False, "Missing '@@' hunk marker"

    # Check that hunks have proper structure
    in_hunk = False
    for line in lines:
        if line.startswith('@@'):
            in_hunk = True
        elif in_hunk and line and not line.startswith((' ', '+', '-', '\\')):
            # Invalid line in hunk
            return False, f"Invalid line in hunk: {line[:50]}..."

    return True, "Valid patch structure"

def _sanitize_patch(patch: str) -> str:
    """Sanitize and validate a patch before applying it."""
    if not patch or not patch.strip():
        return ""

    lines = patch.strip().split('\n')
    sanitized_lines = []

    for line in lines:
        # Only remove lines that are clearly malicious commands (not diff content)
        # Be very conservative - only remove obvious shell commands
        if (line.strip().startswith(('rm ', 'sudo ', 'rm\t')) or
            ('>' in line and not line.startswith(('---', '+++', '@@', '+', '-', ' '))) or
            (';' in line and not line.startswith(('---', '+++', '@@', '+', '-', ' '))) or
            ('&' in line and not line.startswith(('---', '+++', '@@', '+', '-', ' ')))):
            print(f"[agent] Filtering potentially dangerous line: {line[:50]}...")
            continue

        # Fix malformed git index lines with fake hashes
        if line.startswith('index ') and '..' in line:
            # Check if this looks like a fake hash (too short, non-hex, etc.)
            hash_part = line[6:].split()[0]  # Get the hash part after "index "
            if '..' in hash_part:
                old_hash, new_hash = hash_part.split('..', 1)
                # Remove file mode if present (e.g., "100644")
                new_hash = new_hash.split()[0]

                # Check if hashes look fake (too short, contain non-hex chars, obvious placeholders)
                fake_patterns = ['abc123', '123456', 'def456', '987654']
                is_fake = (len(old_hash) < 7 or len(new_hash) < 7 or
                          not all(c in '0123456789abcdef' for c in old_hash.lower()) or
                          not all(c in '0123456789abcdef' for c in new_hash.lower()) or
                          any(pattern in hash_part.lower() for pattern in fake_patterns))

                if is_fake:
                    print(f"[agent] Removing fake git index line: {line[:50]}...")
                    continue

        sanitized_lines.append(line)

    result = '\n'.join(sanitized_lines)

    # Ensure patch ends with newline for proper format
    if result and not result.endswith('\n'):
        result += '\n'

    return result

def _apply_patch(patch: str) -> str:
    """Apply a patch to the repository."""
    sanitized_patch = _sanitize_patch(patch)

    if not sanitized_patch.strip():
        return "Error: Empty or invalid patch"

    # Try different patch levels
    for p_level in ['0', '1']:
        with NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as tmp:
            tmp.write(sanitized_patch)
            tmp.flush()

            try:
                result = subprocess.run(
                    ["patch", f"-p{p_level}", "--dry-run"],
                    stdin=open(tmp.name),
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    # Apply for real
                    result = subprocess.run(
                        ["patch", f"-p{p_level}"],
                        stdin=open(tmp.name),
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    os.unlink(tmp.name)
                    return f"Patch applied successfully with -p{p_level}:\n{result.stdout}"

            except Exception as e:
                os.unlink(tmp.name)
                return f"Error applying patch: {e}"
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

    return "Failed to apply patch with any patch level"

# Missing critical function - dry run patch validation
def _dry_run_patch(patch: str) -> tuple[bool, str]:
    """Dry-run version – returns (applies_cleanly: bool, output: str)"""
    # Sanitize before dry-run.
    patch = _sanitize_patch(patch)
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



def _remote_embed(text: str, proxy_url: str, run_id: str) -> List[float]:
    """Get embeddings from the proxy service."""
    try:
        url = f"{proxy_url}/api/embedding"
        data = json.dumps({"input": text, "run_id": run_id}).encode('utf-8')

        req = _urlreq.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')

        with _urlreq.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get("embedding", ZERO_VEC)

    except Exception as e:
        print(f"[agent] embedding error: {e}")
        return ZERO_VEC

def _cosine(u: List[float], v: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not u or not v:
        return 0.0

    dot_product = sum(a * b for a, b in zip(u, v))
    norm_u = math.sqrt(sum(a * a for a in u))
    norm_v = math.sqrt(sum(a * a for a in v))

    if norm_u == 0 or norm_v == 0:
        return 0.0

    return dot_product / (norm_u * norm_v)

def inference(messages: List[Dict[str, Any]], proxy_url: str, run_id: str, model: str = None, tools: List[Dict[str, Any]] = None) -> dict:
    """Send messages to the LLM via proxy and get response."""
    request_data = {
        "run_id": run_id,
        "messages": messages,
        "temperature": 0.0,
        "model": model or DEFAULT_MODEL
    }

    url = f"{proxy_url.rstrip('/')}/api/inference"
    request_bytes = json.dumps(request_data, ensure_ascii=False).encode('utf-8')

    try:
        req = _urlreq.Request(url, data=request_bytes, method="POST")
        req.add_header("Content-Type", "application/json")

        with _urlreq.urlopen(req, timeout=300) as resp:
            response_body = resp.read()
            response_json = json.loads(response_body.decode("utf-8"))

            # The proxy may return a plain string instead of a JSON object
            if isinstance(response_json, str):
                return {"text_response": response_json, "code_response": ""}

            return response_json

    except Exception as e:
        raise RuntimeError(f"Inference failed: {e}")

# --- Advanced Feature Extraction Functions (from legitimate parts of agent_3-miner-5) ---

# UNUSED: Domain pattern extraction (removed to prevent overfitting/spoon-feeding)
# This function was creating bias by pre-categorizing problems instead of letting LLM discover organically
# def _extract_problem_keywords(problem_text: str) -> Dict[str, List[str]]:
#     """Extract relevant keywords from natural language problem description."""
#     features = {
#         'domain_keywords': [],
#         'component_names': [],
#         'error_terms': [],
#         'action_words': [],
#         'technical_terms': [],
#         'file_mentions': []
#     }

    # Domain-specific frameworks and libraries
    DOMAIN_PATTERNS = {
        'django': r'\b(django|model|queryset|migration|admin|form|view|template|url|settings|ORM)\b',
        'flask': r'\b(flask|route|blueprint|request|response|session|app|render_template)\b',
        'sklearn': r'\b(sklearn|scikit.learn|fit|transform|predict|estimator|classifier|regressor|pipeline)\b',
        'numpy': r'\b(numpy|np|array|ndarray|dtype|shape|axis|broadcast)\b',
        'pandas': r'\b(pandas|pd|dataframe|series|index|column|groupby|merge)\b',
        'matplotlib': r'\b(matplotlib|plt|plot|figure|axis|subplot|legend|xlabel|ylabel)\b',
        'sympy': r'\b(sympy|symbol|equation|solve|diff|integrate|matrix|polynomial)\b',
        'pytest': r'\b(pytest|test|fixture|mock|assert|parametrize|unittest)\b',
        'astropy': r'\b(astropy|fits|table|unit|quantity|coordinate|time|wcs)\b',
        'sphinx': r'\b(sphinx|doc|directive|rst|autodoc|toctree)\b'
    }

    # Error and problem indicators
    ERROR_PATTERNS = [
        r'\b(error|exception|fail|failure|bug|issue|problem|broken|crash|traceback)\b',
        r'\b(incorrect|wrong|invalid|unexpected|missing|not.working)\b',
        r'\b(raise|throw|assert|warning|debug)\b'
    ]

    # Action words that indicate what needs to be done
    ACTION_PATTERNS = [
        r'\b(fix|repair|solve|resolve|handle|support|implement|add|remove|update|modify)\b',
        r'\b(improve|enhance|optimize|refactor|validate|check|ensure)\b'
    ]

    # Technical terms and components
    TECH_PATTERNS = [
        r'\b(function|method|class|module|package|import|library)\b',
        r'\b(parameter|argument|variable|attribute|property|field)\b',
        r'\b(database|query|sql|json|xml|api|endpoint|request|response)\b',
        r'\b(configuration|settings|options|preferences|default)\b'
    ]

    # File and path mentions
    FILE_PATTERNS = [
        r'\b([a-zA-Z_][a-zA-Z0-9_]*\.py)\b',  # Python files
        r'\b([a-zA-Z_][a-zA-Z0-9_/]*\.[a-zA-Z]{2,4})\b',  # General files
        r'\b([a-zA-Z_][a-zA-Z0-9_]*)/([a-zA-Z_][a-zA-Z0-9_]*)\b'  # Path-like structures
    ]

    problem_lower = problem_text.lower()

    # Extract domain keywords
    for domain, pattern in DOMAIN_PATTERNS.items():
        matches = re.findall(pattern, problem_lower, re.IGNORECASE)
        if matches:
            features['domain_keywords'].extend([domain] + matches)

    # Extract error terms
    for pattern in ERROR_PATTERNS:
        features['error_terms'].extend(re.findall(pattern, problem_lower, re.IGNORECASE))

    # Extract action words
    for pattern in ACTION_PATTERNS:
        features['action_words'].extend(re.findall(pattern, problem_lower, re.IGNORECASE))

    # Extract technical terms
    for pattern in TECH_PATTERNS:
        features['technical_terms'].extend(re.findall(pattern, problem_lower, re.IGNORECASE))

    # Extract file mentions
    for pattern in FILE_PATTERNS:
        matches = re.findall(pattern, problem_text, re.IGNORECASE)  # Case sensitive for files
        features['file_mentions'].extend([m if isinstance(m, str) else '/'.join(m) for m in matches])

    # Extract component names (CamelCase or specific patterns)
    component_patterns = [
        r'\b([A-Z][a-zA-Z0-9]*(?:[A-Z][a-zA-Z0-9]*)*)\b',  # CamelCase
        r'\b([a-z]+_[a-z_]+)\b',  # snake_case
        r'\b([A-Z][A-Z_]+)\b'  # CONSTANTS
    ]
    for pattern in component_patterns:
        features['component_names'].extend(re.findall(pattern, problem_text))

    return features

def _extract_code_features(text: str, file_path: str = "") -> Dict[str, List[str]]:
    """Extract code-specific features for better similarity matching."""
    features = {
        'functions': [],
        'classes': [],
        'imports': [],
        'identifiers': [],
        'error_keywords': [],
        'docstrings': []
    }

    # Error and exception related keywords in code
    ERROR_KEYWORDS = [
        'error', 'exception', 'fail', 'bug', 'issue', 'problem', 'fix', 'broken', 'crash',
        'traceback', 'stacktrace', 'assert', 'raise', 'throw', 'catch', 'try', 'except',
        'errno', 'stderr', 'warning', 'debug', 'test', 'unittest', 'pytest'
    ]

    # Extract file extension for language-specific processing
    file_ext = os.path.splitext(file_path)[1].lower()

    # Python-specific AST extraction
    if file_ext == '.py':
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    features['functions'].append(node.name)
                elif isinstance(node, ast.ClassDef):
                    features['classes'].append(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        features['imports'].append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        features['imports'].append(node.module)
                    for alias in node.names:
                        features['imports'].append(alias.name)
        except:
            pass  # Fall back to regex if AST parsing fails

    # Regex-based extraction for all languages
    text_lower = text.lower()

    # Extract function/method definitions
    func_patterns = [
        r'\bdef\s+(\w+)',  # Python
        r'\bfunction\s+(\w+)',  # JavaScript
        r'\b(\w+)\s*\([^)]*\)\s*{',  # C/Java/JavaScript functions
        r'\bfunc\s+(\w+)',  # Go
    ]
    for pattern in func_patterns:
        features['functions'].extend(re.findall(pattern, text, re.IGNORECASE))

    # Extract class definitions
    class_patterns = [
        r'\bclass\s+(\w+)',  # Python/Java/C++
        r'\binterface\s+(\w+)',  # Java/TypeScript
        r'\bstruct\s+(\w+)',  # C/Go
    ]
    for pattern in class_patterns:
        features['classes'].extend(re.findall(pattern, text, re.IGNORECASE))

    # Extract meaningful identifiers
    identifier_pattern = r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b'
    all_identifiers = re.findall(identifier_pattern, text)
    # Filter out common words and keep meaningful identifiers
    meaningful_identifiers = [
        word for word in all_identifiers
        if len(word) > 2 and not word.lower() in ['the', 'and', 'for', 'with', 'this', 'that', 'from', 'import']
    ]
    features['identifiers'] = meaningful_identifiers[:50]  # Limit to avoid noise

    # Extract error-related keywords
    for keyword in ERROR_KEYWORDS:
        if keyword in text_lower:
            features['error_keywords'].append(keyword)

    # Extract docstrings and comments
    docstring_patterns = [
        r'"""([^"]+)"""',  # Python docstrings
        r"'''([^']+)'''",  # Python docstrings
        r'/\*\*([^*]+)\*/',  # Javadoc
        r'#\s*(.+)$',  # Comments
    ]
    for pattern in docstring_patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.DOTALL)
        features['docstrings'].extend(matches[:5])  # Limit to avoid noise

    return features

# UNUSED: Domain-based similarity (removed to prevent overfitting)
# def _compute_problem_code_similarity(problem_features: Dict[str, List[str]], code_features: Dict[str, List[str]], file_path: str) -> float:
    """Compute similarity between problem description and code chunk."""
    score = 0.0

    # Weight different types of matches
    weights = {
        'domain_match': 3.0,      # High weight for domain/framework matches
        'error_match': 2.5,       # High weight for error-related matches
        'component_match': 2.0,   # Component name matches
        'file_match': 2.5,        # File name matches
        'technical_match': 1.5,   # Technical term matches
        'general_match': 1.0      # General identifier matches
    }

    # 1. Domain keyword matching
    problem_domains = set(problem_features.get('domain_keywords', []))
    code_all_text = ' '.join(
        code_features.get('functions', []) +
        code_features.get('classes', []) +
        code_features.get('imports', []) +
        code_features.get('identifiers', [])
    ).lower()

    for domain in problem_domains:
        if domain in code_all_text or domain in file_path.lower():
            score += weights['domain_match']

    # 2. Error keyword matching
    problem_errors = set(problem_features.get('error_terms', []))
    code_errors = set(code_features.get('error_keywords', []))
    error_overlap = len(problem_errors & code_errors)
    if error_overlap > 0:
        score += weights['error_match'] * error_overlap

    # 3. Component name matching
    problem_components = set(problem_features.get('component_names', []))
    code_components = set(
        code_features.get('functions', []) +
        code_features.get('classes', [])
    )
    component_overlap = len(problem_components & code_components)
    if component_overlap > 0:
        score += weights['component_match'] * component_overlap

    # 4. File name matching
    problem_files = set(problem_features.get('file_mentions', []))
    file_name = os.path.basename(file_path).lower()
    for mentioned_file in problem_files:
        if mentioned_file.lower() in file_path.lower() or file_name in mentioned_file.lower():
            score += weights['file_match']

    # 5. Technical term matching
    problem_tech = set(problem_features.get('technical_terms', []))
    code_identifiers = set(code_features.get('identifiers', []))
    tech_overlap = len(problem_tech & code_identifiers)
    if tech_overlap > 0:
        score += weights['technical_match'] * min(tech_overlap, 3)  # Cap to avoid noise

    # 6. General identifier overlap (with lower weight)
    problem_all = set()
    for feature_list in problem_features.values():
        problem_all.update([f.lower() for f in feature_list])

    code_all = set()
    for feature_list in code_features.values():
        code_all.update([f.lower() for f in feature_list])

    general_overlap = len(problem_all & code_all)
    if general_overlap > 0:
        score += weights['general_match'] * min(general_overlap, 5)  # Cap to avoid noise

    return score

def _compute_enhanced_tfidf_similarity(problem_text: str, chunk_texts: List[str]) -> List[float]:
    """Compute TF-IDF similarity with better preprocessing for problem-to-code matching."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        def preprocess_text(text):
            # Handle code-specific patterns
            # Split camelCase and snake_case
            text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
            text = text.replace('_', ' ')
            # Remove common punctuation but keep important ones
            text = re.sub(r'[^\w\s.-]', ' ', text)
            return text.lower()

        # More targeted stop words for problem-to-code matching
        stop_words = [
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
            'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does',
            'did', 'will', 'would', 'could', 'should', 'this', 'that', 'these', 'those', 'can'
        ]

        vectorizer = TfidfVectorizer(
            preprocessor=preprocess_text,
            stop_words=stop_words,
            max_features=25000,
            ngram_range=(1, 2),  # Include bigrams
            max_df=0.85,
            min_df=1
        )

        all_texts = [problem_text] + chunk_texts
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        query_vec = tfidf_matrix[0]
        chunk_matrix = tfidf_matrix[1:]

        # Compute cosine similarity
        similarities = []
        for i in range(chunk_matrix.shape[0]):
            chunk_vec = chunk_matrix[i]
            similarity = (query_vec * chunk_vec.T).toarray()[0, 0]
            similarities.append(similarity)

        return similarities
    except Exception as e:
        print(f"Enhanced TF-IDF computation failed: {e}")
        return [0.0] * len(chunk_texts)

def _improved_problem_code_filter(problem_text: str, code_chunks: List[Chunk], chunk_texts: List[str], pre_filter_top: int) -> tuple[List[str], List[Chunk]]:
    """Organic problem-to-code similarity matching using only TF-IDF without domain bias."""

    print(f"Starting organic problem-to-code filtering with {len(chunk_texts)} chunks...")

    # Use only TF-IDF similarity - let the model discover patterns organically
    print("Computing TF-IDF similarities...")
    tfidf_similarities = _compute_enhanced_tfidf_similarity(problem_text, chunk_texts)

    # 3. Path-based similarity bonus
    print("Computing path-based similarities...")
    path_bonuses = []
    problem_lower = problem_text.lower()
    for chunk in code_chunks:
        bonus = 0.0
        file_parts = chunk.file.lower().split('/')
        base_name = os.path.basename(chunk.file).lower()

        # Check if filename or path components mentioned in problem
        for part in file_parts + [base_name, base_name.split('.')[0]]:
            if part in problem_lower and len(part) > 2:
                bonus += 0.3

        path_bonuses.append(bonus)

    # Normalize similarities
    def normalize_scores(scores):
        if not scores:
            return scores
        min_score = min(scores)
        max_score = max(scores)
        if max_score > min_score:
            return [(x - min_score) / (max_score - min_score) for x in scores]
        else:
            return [0.0] * len(scores)

    tfidf_similarities = normalize_scores(tfidf_similarities)
    path_bonuses = normalize_scores(path_bonuses)

    # Combine similarities with organic weighting (no domain bias)
    print("Combining similarity scores...")
    combined_similarities = []
    weights = {
        'tfidf': 0.8,      # Primary weight for content similarity
        'path': 0.2        # Minor bonus for path mentions
    }

    for i in range(len(chunk_texts)):
        combined_score = (
            weights['tfidf'] * tfidf_similarities[i] +
            weights['path'] * path_bonuses[i]
        )
        combined_similarities.append(combined_score)

    # Get top indices
    top_indices = sorted(range(len(combined_similarities)), key=lambda i: -combined_similarities[i])[:pre_filter_top]

    # Return filtered chunks
    filtered_chunk_texts = [chunk_texts[i] for i in top_indices]
    filtered_code_chunks = [code_chunks[i] for i in top_indices]

    return filtered_chunk_texts, filtered_code_chunks

# --- Hybrid Mode Functions ---

def run_exploration(problem_text: str, *, proxy_url: str, model_name: str, run_id: str) -> Dict[str, Any]:
    """
    Phase 1: Use tools to explore and locate the problem.
    Enhanced with smart feature extraction to guide exploration.
    Returns information about target files to focus on.
    """

    # Initialize conversation memory to store rich exploration context
    memory = ConversationMemory()

        # Store just the raw problem text in memory - let LLM discover patterns organically
    memory.add_problem_analysis({
        "problem_text": problem_text[:500],  # Store raw problem for reference
        "exploration_approach": "organic_discovery"  # Mark as discovery-based exploration
    })

    # Initialize current working directory BEFORE using it
    current_working_directory = "."

    # Use structured system prompt
    command_docs = "\n".join(EXPLORATION_COMMANDS.values())
    system_prompt = EXPLORATION_SYSTEM_PROMPT.format(command_docs=command_docs)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"PROBLEM TO LOCATE:\n{problem_text}\n\n(Current dir: {current_working_directory}) $ Start your exploration to find where this problem exists in the codebase. Analyze the problem statement carefully and develop your own search strategy."}
    ]

    step = 0
    exploration_history = []
    command_history = []  # Track commands to detect loops

    while step < MAX_EXPLORATION_STEPS:
        step += 1

        try:
            # Get next action from LLM using string commands
            response = inference(messages, proxy_url, run_id, model_name)

            # Handle API failures gracefully
            if response is None:
                print(f"[agent] API returned None response at step {step}")
                raise Exception("API returned None response")

            text_response = response.get("text_response", "")

            # Handle error responses from API
            if "error" in response and not text_response:
                error_msg = response.get("error", "Unknown API error")
                print(f"[agent] API error at step {step}: {error_msg}")
                raise Exception(f"API error: {error_msg}")

            if not text_response or not text_response.strip():
                print(f"[agent] Empty response from API at step {step}")
                raise Exception("Empty response from API")

                        # Parse the response to extract discussion and command
            if "```" in text_response:
                parts = text_response.split("```")
                discussion = parts[0].strip()
                command = parts[1].strip() if len(parts) > 1 else ""
            else:
                discussion = text_response.strip()
                command = ""

            # Clean up command - remove any extra whitespace and newlines
            command = command.strip()

            # Remove language identifiers from code blocks (e.g. "python\nFINISH(...)")
            if '\n' in command and len(command.split('\n')) > 1:
                lines = command.split('\n')
                # If first line looks like a language identifier, skip it
                if lines[0].strip().lower() in ['python', 'bash', 'shell', 'json', ''] and len(lines) > 1:
                    command = '\n'.join(lines[1:]).strip()

            # Debug logging for command parsing
            print(f"[agent] Raw command: {repr(command[:100])}...")
            print(f"[agent] Command starts with: {repr(command[:20])}")

            # Extract just the first line if command spans multiple lines (for loop detection)
            command_for_loop_check = command.split('\n')[0].strip() if command else ""

            exploration_history.append(f"STEP {step}:\nDISCUSSION: {discussion}\nCOMMAND: {command}")

            # Improved loop detection - only check actual commands, not analysis text
            is_valid_command = any(command_for_loop_check.startswith(cmd) for cmd in [
                "READ_FILE", "FIND", "LS", "CD", "GREP", "SMART_SEARCH", "RUN_TESTS", "FINISH"
            ])

            # Also detect repeated failed commands (even if slightly different)
            command_base = command_for_loop_check.split('(')[0] if '(' in command_for_loop_check else command_for_loop_check
            recent_command_bases = [cmd.split('(')[0] if '(' in cmd else cmd for cmd in command_history[-3:]]
            repeated_base_command = command_base in recent_command_bases and len(command_history) >= 3

            if (is_valid_command and command_for_loop_check in command_history[-3:]) or repeated_base_command:  # Same command in last 3 attempts
                observation = f"Detected command loop with '{command_for_loop_check}'. Trying different approach..."
                print(f"[agent] Loop detected: {command_for_loop_check}")

                # Generic suggestions based on command type (not specific files/frameworks)
                if "READ_FILE" in command_for_loop_check:
                    observation += "\n\nSuggestion: File may be too large or truncated. Try GREP to find specific patterns or use SMART_SEARCH to identify target files."
                elif "GREP" in command_for_loop_check:
                    observation += "\n\nSuggestion: If you've found relevant files/patterns, consider using SMART_SEARCH for broader context or FINISH if you've identified the target location."
                else:
                    observation += "\n\nSuggestion: Try a different exploration strategy or use FINISH if you've identified the problem location."

                # Skip command execution and provide feedback
                messages.append({"role": "assistant", "content": text_response})
                messages.append({"role": "user", "content": f"OBSERVATION:\n{observation[:MAX_OBS_CHARS]}"})
                pwd_prompt = f"(Current dir: {current_working_directory}) $ "
                messages.append({"role": "user", "content": f"{pwd_prompt}Continue exploration..."})
                continue  # Skip to next iteration
            else:
                # Only track valid commands in history
                if is_valid_command:
                    command_history.append(command_for_loop_check)
                    # Keep only last 5 commands to avoid memory bloat
                    if len(command_history) > 5:
                        command_history.pop(0)

            # Handle FINISH command - improved detection
            command_upper = command.upper().strip()
            # Also check if FINISH appears anywhere in the text (fallback)
            # Check for both FINISH( and standalone FINISH patterns
            finish_in_text = ("FINISH(" in text_response.upper() or
                             re.search(r'\bFINISH\b', text_response.upper()))

            if command_upper.startswith("FINISH") or finish_in_text:
                print(f"[agent] FINISH command detected: {repr(command)}")
                try:
                    # Multiple strategies to extract JSON
                    finish_data = None

                    # Use full text if FINISH was found there but not in command
                    text_to_parse = command if command_upper.startswith("FINISH") else text_response
                    print(f"[agent] Parsing from: {'command' if text_to_parse == command else 'full text'}")

                    # Strategy 1: Extract from parentheses
                    if "(" in text_to_parse and ")" in text_to_parse:
                        start_paren = text_to_parse.find("(")
                        end_paren = text_to_parse.rfind(")")
                        json_str = text_to_parse[start_paren+1:end_paren].strip()
                        print(f"[agent] Extracted JSON from parens: {repr(json_str[:100])}...")
                        try:
                            finish_data = json.loads(json_str)
                        except json.JSONDecodeError as e:
                            print(f"[agent] JSON parsing failed: {e}")

                    # Strategy 2: Look for JSON-like structure in the command
                    if finish_data is None and "{" in text_to_parse and "}" in text_to_parse:
                        start_brace = text_to_parse.find("{")
                        end_brace = text_to_parse.rfind("}")
                        json_str = text_to_parse[start_brace:end_brace+1].strip()
                        print(f"[agent] Extracted JSON from braces: {repr(json_str[:100])}...")
                        try:
                            finish_data = json.loads(json_str)
                        except json.JSONDecodeError as e:
                            print(f"[agent] JSON parsing failed: {e}")

                    # Strategy 3: Try to extract from multi-line text
                    if finish_data is None:
                        # Look for target_files and problem_location in text
                        target_match = re.search(r'"target_files":\s*\[(.*?)\]', text_to_parse, re.DOTALL)
                        location_match = re.search(r'"problem_location":\s*"(.*?)"', text_to_parse, re.DOTALL)

                        if target_match and location_match:
                            target_files_str = target_match.group(1)
                            # Extract file names from the list
                            file_matches = re.findall(r'"([^"]+)"', target_files_str)
                            finish_data = {
                                "target_files": file_matches,
                                "problem_location": location_match.group(1)
                            }
                            print(f"[agent] Extracted via regex: {finish_data}")

                    if finish_data:
                        target_files = finish_data.get("target_files", [])
                        problem_location = finish_data.get("problem_location", "")

                        print(f"[agent] FINISH command executed successfully")
                        print(f"[agent] Target files: {target_files}")
                        print(f"[agent] Problem location: {problem_location}")

                        if not target_files:
                            observation = "Error: FINISH must specify target_files"
                        else:
                            # Store final insights in memory
                            memory.add_pattern("solution_location", problem_location, f"Found in files: {target_files}")
                            for file_path in target_files:
                                memory.add_file_analysis(file_path, {"identified_as_target": True, "reason": problem_location})

                            return {
                                "success": True,
                                "target_files": target_files,
                                "problem_location": problem_location,
                                "exploration_history": exploration_history,
                                "memory": memory
                            }
                    else:
                        observation = "Error: Could not extract valid JSON from FINISH command"

                except Exception as e:
                    print(f"[agent] FINISH command error: {e}")
                    observation = f"Error parsing FINISH command: {e}"

            # Handle other commands using string parsing
            elif command.startswith("READ_FILE"):
                try:
                    # Extract content within parentheses
                    paren_content = command[command.find("(")+1:command.rfind(")")]

                    # Check if there are multiple arguments (commas) - this is invalid
                    if ',' in paren_content:
                        observation = f"Error: READ_FILE only accepts one argument (file path). Got: {paren_content}"
                    else:
                        path = paren_content.strip('\'"')
                        if not path:
                            observation = "Error: READ_FILE requires a file path argument"
                        else:
                            if not os.path.isabs(path):
                                path = os.path.join(current_working_directory, path)
                            observation = _read_file(path)
                except Exception as e:
                    observation = f"Error parsing READ_FILE command: {e}. Correct usage: READ_FILE(\"path/to/file.py\")"

            elif command.startswith("FIND"):
                # Enhanced FIND parsing to handle flags like -name, -type, -maxdepth
                try:
                    find_cmd = ["find", current_working_directory]

                    if "(" in command and ")" in command:
                        # FIND(pattern) format
                        pattern = command[command.find("(")+1:command.rfind(")")]
                        pattern = pattern.strip('\'"')
                        find_cmd.extend(["-name", pattern])
                    elif " -" in command:
                        # FIND -name "*.py" -type f format
                        parts = command.split()
                        i = 1  # Skip "FIND"
                        while i < len(parts):
                            part = parts[i]
                            if part == "-name" and i + 1 < len(parts):
                                find_cmd.extend(["-name", parts[i + 1].strip('\'"')])
                                i += 2
                            elif part == "-type" and i + 1 < len(parts):
                                find_cmd.extend(["-type", parts[i + 1]])
                                i += 2
                            elif part == "-maxdepth" and i + 1 < len(parts):
                                find_cmd.extend(["-maxdepth", parts[i + 1]])
                                i += 2
                            else:
                                i += 1
                    else:
                        # Default: find all files
                        find_cmd.extend(["-type", "f"])

                    result = subprocess.run(find_cmd, capture_output=True, text=True, check=False, timeout=30)
                    observation = result.stdout if result.returncode == 0 else result.stderr
                except Exception as e:
                    observation = f"Error running find: {e}"

            elif command.startswith("LS"):
                # Enhanced LS parsing to handle flags like -la, -R
                try:
                    dir_path = current_working_directory
                    ls_cmd = ["ls"]

                    if "(" in command:
                        # LS(dir) format
                        dir_path = command[command.find("(")+1:command.rfind(")")]
                        dir_path = dir_path.strip('\'"') if dir_path else current_working_directory
                    elif " -" in command:
                        # LS -la format or LS -R dir format
                        parts = command.split()
                        for i, part in enumerate(parts):
                            if part.startswith("-"):
                                # Add flags
                                if "l" in part: ls_cmd.append("-l")
                                if "a" in part: ls_cmd.append("-a")
                                if "h" in part: ls_cmd.append("-h")
                                if "R" in part: ls_cmd.append("-R")
                            elif i > 0 and not part.startswith("-"):
                                # This is the directory
                                dir_path = part.strip('\'"')
                                break

                    if not os.path.isabs(dir_path):
                        dir_path = os.path.join(current_working_directory, dir_path)

                    ls_cmd.append(dir_path)
                    result = subprocess.run(ls_cmd, capture_output=True, text=True, check=False, timeout=10)
                    observation = result.stdout if result.returncode == 0 else result.stderr
                except Exception as e:
                    observation = f"Error running ls: {e}"

            elif command.startswith("CD"):
                new_dir = command[command.find("(")+1:command.rfind(")")]
                new_dir = new_dir.strip('\'"')
                try:
                    if new_dir == "..":
                        new_path = os.path.dirname(current_working_directory) or "."
                    elif new_dir == ".":
                        new_path = current_working_directory
                    elif os.path.isabs(new_dir):
                        new_path = new_dir
                    else:
                        new_path = os.path.join(current_working_directory, new_dir)

                    new_path = os.path.normpath(new_path)
                    if os.path.isdir(new_path):
                        current_working_directory = new_path
                        observation = f"Changed directory to: {current_working_directory}"
                    else:
                        observation = f"Directory not found: {new_path}"
                except Exception as e:
                    observation = f"Error changing directory: {e}"

            elif command.startswith("GREP"):
                # Enhanced GREP parsing to handle flags like -A, -B, -n
                # Supports: GREP("pattern", "path") and GREP -A 10 -B 2 "pattern" "path"
                try:
                    if command.count('"') >= 2:
                        # Check for flags before the first quote
                        first_quote = command.find('"')
                        before_quotes = command[:first_quote]

                        # Extract all quoted strings
                        quote_parts = []
                        in_quote = False
                        current_quote = ""
                        for char in command[first_quote:]:
                            if char == '"':
                                if in_quote:
                                    quote_parts.append(current_quote)
                                    current_quote = ""
                                    in_quote = False
                                else:
                                    in_quote = True
                            elif in_quote:
                                current_quote += char

                        if len(quote_parts) >= 2:
                            pattern = quote_parts[0]
                            path = quote_parts[1]

                            # Build grep command with flags
                            grep_cmd = ["grep"]

                            # Parse flags from before_quotes
                            if "-A" in before_quotes:
                                match = re.search(r'-A\s+(\d+)', before_quotes)
                                if match:
                                    grep_cmd.extend(["-A", match.group(1)])

                            if "-B" in before_quotes:
                                match = re.search(r'-B\s+(\d+)', before_quotes)
                                if match:
                                    grep_cmd.extend(["-B", match.group(1)])

                            if "-n" in before_quotes:
                                grep_cmd.append("-n")

                            # Add recursive flag by default
                            if "-r" not in before_quotes:
                                grep_cmd.append("-r")

                            # Add pattern and path
                            grep_cmd.extend([pattern, path])

                            # Make path absolute if needed
                            if not os.path.isabs(path):
                                path = os.path.join(current_working_directory, path)
                                grep_cmd[-1] = path

                            result = subprocess.run(
                                grep_cmd, capture_output=True, text=True, check=False, timeout=30
                            )
                            observation = result.stdout if result.returncode == 0 else f"No matches found for '{pattern}'"
                        else:
                            observation = "Error: GREP requires pattern and path arguments"
                    else:
                        # Fallback to simple parsing for GREP(pattern, path)
                        args = command[command.find("(")+1:command.rfind(")")]
                        args_parts = [arg.strip().strip('\'"') for arg in args.split(",")]
                        if len(args_parts) >= 2:
                            pattern, path = args_parts[0], args_parts[1]
                            if not os.path.isabs(path):
                                path = os.path.join(current_working_directory, path)
                            result = subprocess.run(
                                ["grep", "-r", pattern, path],
                                capture_output=True, text=True, check=False, timeout=30
                            )
                            observation = result.stdout if result.returncode == 0 else f"No matches found for '{pattern}'"
                        else:
                            observation = "Error: GREP requires pattern and path arguments"

                except Exception as e:
                    observation = f"Error running grep: {e}"

            elif command.startswith("SMART_SEARCH"):
                try:
                    print("[agent] Performing smart search...")
                    if USE_FUNCTION_CHUNKS:
                        code_chunks = _collect_code_chunks()
                        chunk_texts = [c.text for c in code_chunks]
                    else:
                        repo_texts = _collect_repo_texts()
                        code_chunks = [Chunk(file=fp, start_line=1, end_line=text.count("\n") + 1, text=text) for fp, text in repo_texts.items()]
                        chunk_texts = [c.text for c in code_chunks]

                    if len(chunk_texts) > 20:
                        filtered_chunk_texts, filtered_code_chunks = _improved_problem_code_filter(
                            problem_text, code_chunks, chunk_texts, 10
                        )

                        results = []
                        target_files = []
                        for chunk in filtered_code_chunks:
                            results.append(f"📁 {chunk.file} (lines {chunk.start_line}-{chunk.end_line})")
                            # Extract unique file paths for potential FINISH
                            if chunk.file not in target_files:
                                target_files.append(chunk.file)

                        observation = f"SMART_SEARCH found {len(results)} most relevant files:\n" + "\n".join(results[:10])

                        # Intelligent suggestion based on results quality
                        if len(filtered_code_chunks) > 0:
                            top_file = filtered_code_chunks[0].file

                            # Check if we have high-confidence results
                            if len(target_files) <= 3:  # Few, focused results
                                observation += f"\n\n💡 INTELLIGENT SUGGESTION: The top result '{top_file}' appears highly relevant to your problem."
                                observation += f"\n   Consider using: FINISH({{\"target_files\": {target_files[:3]}, \"problem_location\": \"Found via SMART_SEARCH\"}})"
                            else:
                                observation += f"\n\n💡 SUGGESTION: Multiple relevant files found. Focus on the top results or use GREP to narrow down to specific patterns."

                    else:
                        observation = f"Repository has {len(chunk_texts)} files. Use FIND or LS to explore them manually."

                except Exception as e:
                    observation = f"Smart search failed: {e}"

            elif command.startswith("RUN_TESTS"):
                # Enhanced RUN_TESTS parsing to handle flags like -v, -x
                try:
                    test_cmd = ["python", "-m", "pytest"]
                    test_file = ""

                    if "(" in command and ")" in command:
                        # RUN_TESTS(test_file) format
                        test_file = command[command.find("(")+1:command.rfind(")")]
                        test_file = test_file.strip('\'"')
                    elif " -" in command:
                        # RUN_TESTS -v test.py format
                        parts = command.split()
                        for i, part in enumerate(parts):
                            if part.startswith("-"):
                                if "v" in part: test_cmd.append("-v")
                                if "x" in part: test_cmd.append("-x")
                                if "s" in part: test_cmd.append("-s")  # Don't capture output
                            elif i > 0 and not part.startswith("-") and part != "RUN_TESTS":
                                test_file = part.strip('\'"')

                    if test_file:
                        test_cmd.append(test_file)
                    else:
                        # Default: add -v flag for verbose output
                        test_cmd.append("-v")

                    result = subprocess.run(
                        test_cmd, capture_output=True, text=True, check=False, timeout=60
                    )
                    observation = f"Test output:\n{result.stdout}\n{result.stderr}"
                except Exception as e:
                    observation = f"Error running tests: {e}"

            else:
                print(f"[agent] Unrecognized command: {repr(command)}")
                print(f"[agent] Command length: {len(command)}")
                print(f"[agent] First 50 chars: {repr(command[:50])}")
                observation = f"Unknown command: {command[:100]}..."

            # Extract insights from observation and store in memory
            insight = _extract_insight_from_observation(command, observation)
            memory.add_exploration_step(step, command, observation, insight)

            # Store specific insights based on command type
            if command.startswith("READ_FILE") and "Error" not in observation:
                file_path = command[command.find("(")+1:command.rfind(")")].strip('\'"')
                if len(observation) > 100:  # Meaningful content
                    memory.add_code_insight(file_path, "file_content", f"Contains {len(observation)} chars of code")
            elif command.startswith("GREP") and observation.count('\n') > 0:
                lines_found = observation.count('\n')
                memory.add_pattern("grep_results", f"Found {lines_found} matches", observation[:200])
            elif command.startswith("SMART_SEARCH") and "found" in observation.lower():
                memory.add_pattern("smart_search", "Found relevant files", observation[:300])

            # Add observation to conversation with current directory context
            messages.append({"role": "assistant", "content": text_response})
            messages.append({"role": "user", "content": f"OBSERVATION:\n{observation[:MAX_OBS_CHARS]}"})

            pwd_prompt = f"(Current dir: {current_working_directory}) $ "
            messages.append({"role": "user", "content": f"{pwd_prompt}Continue exploration..."})

        except Exception as e:
            return {
                "success": False,
                "error": f"Exploration failed at step {step}: {e}",
                "exploration_history": exploration_history,
                "memory": memory
            }

    # If we reached max steps without FINISH
    return {
        "success": False,
        "error": f"Exploration exceeded {MAX_EXPLORATION_STEPS} steps without finding target files",
        "exploration_history": exploration_history,
        "memory": memory
    }

def run_focused_oneshot(problem_text: str, target_files: List[str], memory: ConversationMemory = None, behavior_analysis: Dict = None, *, proxy_url: str, model_name: str, run_id: str) -> str:
    """
    Phase 2: Focused oneshot patch generation.

    Given specific target files from exploration, generate a precise patch.
    Uses the working agents' robust patch validation approach.
    """

    print(f"[agent] Reading {len(target_files)} target files for focused patch generation...")

    # Read the target files completely
    file_contents = []
    for file_path in target_files:
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Limit file size to prevent token overflow
                    if len(content) > 50000:  # 50KB limit
                        content = content[:50000] + "\n... (truncated)"
                    file_contents.append(f"=== {file_path} ===\n{content}")
                    print(f"[agent] Read {file_path} ({len(content)} chars)")
            except Exception as e:
                print(f"[agent] Failed to read {file_path}: {e}")
                file_contents.append(f"=== {file_path} ===\nError reading file: {e}")
        else:
            print(f"[agent] File not found: {file_path}")
            file_contents.append(f"=== {file_path} ===\nFile not found")

    full_context = "\n\n".join(file_contents)

    # Create focused oneshot prompt with structured approach and memory context
    messages = [
        {"role": "system", "content": PATCH_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Problem to fix:\n{problem_text}"},
        {"role": "user", "content": f"Relevant file contents:\n\n{full_context}"}
    ]

    # Add rich context from exploration memory if available
    if memory:
        context_summary = memory.get_context_summary()
        if context_summary.strip():
            messages.append({"role": "user", "content": f"Exploration Context from Investigation:\n\n{context_summary}"})
            print(f"[agent] Added exploration context ({len(context_summary)} chars)")

    # Add behavior analysis from test-first understanding if available
    if behavior_analysis:
        behavior_context = f"""Test-First Behavior Analysis:

CURRENT BEHAVIOR: {behavior_analysis.get('current_behavior', 'Unknown')}
EXPECTED BEHAVIOR: {behavior_analysis.get('expected_behavior', 'Unknown')}
BEHAVIOR GAP: {behavior_analysis.get('behavior_gap', 'Unknown')}

TEST EVIDENCE: {behavior_analysis.get('test_evidence', 'No test evidence')}

This analysis shows what the code currently does vs. what it should do. Use this to generate a targeted fix that addresses the specific behavior gap."""

        messages.append({"role": "user", "content": behavior_context})
        print(f"[agent] Added behavior analysis context ({len(behavior_context)} chars)")

    print(f"[agent] Generating patch with focused context ({len(full_context)} chars)")

    # Try up to 3 attempts with validation and correction
    ATTEMPTS = 3
    for attempt in range(ATTEMPTS):
        try:
            response = inference(messages, proxy_url, run_id, model_name)
            text_response = response.get("text_response", "")
            code_response = response.get("code_response", "")

            print(f"[agent] Attempt {attempt + 1}: Got response with text ({len(text_response)} chars) and code ({len(code_response)} chars)")

            # Extract patch from response using layered approach
            patch_text = None

            # Primary: Try clean responses first (code_response then text_response)
            for cand in (code_response, text_response):
                if cand and cand.strip().startswith("diff --git"):
                    patch_text = cand.strip()
                    print(f"[agent] Found clean diff in response")
                    break

            # Secondary: Try extracting from markdown blocks
            if patch_text is None:
                for cand in (text_response, code_response):
                    if cand and "```diff" in cand:
                        patch_start = cand.find("```diff") + 7
                        patch_end = cand.find("```", patch_start)
                        if patch_end != -1:
                            patch_text = cand[patch_start:patch_end].strip()
                            print(f"[agent] Extracted diff from markdown block")
                            break
                    elif cand and "```" in cand:
                        patch_start = cand.find("```") + 3
                        patch_end = cand.find("```", patch_start)
                        if patch_end != -1:
                            extracted = cand[patch_start:patch_end].strip()
                            if extracted.startswith(("diff", "---")):
                                patch_text = extracted
                                print(f"[agent] Extracted diff from generic markdown block")
                                break

            # Fallback: Use enhanced extraction for mixed responses (Kimi's case)
            if patch_text is None:
                for cand in (text_response, code_response):
                    if cand:
                        extracted = extract_diff_from_response(cand)
                        if extracted:
                            patch_text = extracted
                            print(f"[agent] Extracted diff using fallback parser (likely mixed response)")
                            break

            if patch_text is None:
                print(f"[agent] No valid patch found in response. text_response: {text_response[:300]}...")
                print(f"[agent] code_response: {code_response[:300]}...")
                raise Exception(f"No valid patch in response. Response: {response}")

            print(f"[agent] Extracted patch ({len(patch_text)} chars)")

            # Validate patch structure before sanitization
            valid, error_msg = _validate_patch_structure(patch_text)
            if not valid:
                raise Exception(f"Invalid patch structure: {error_msg}")

            # Count diff sections to ensure completeness
            diff_sections = patch_text.count('diff --git')
            hunk_sections = patch_text.count('@@')
            print(f"[agent] Patch validation: {diff_sections} file(s), {hunk_sections} hunk(s)")

            # Sanitize the patch
            original_length = len(patch_text)
            patch_text = _sanitize_patch(patch_text)
            if len(patch_text) != original_length:
                print(f"[agent] Patch sanitization changed length: {original_length} -> {len(patch_text)}")

            # Final format check
            if not patch_text.strip():
                raise Exception("Patch became empty after sanitization")

            # Re-validate after sanitization
            valid, error_msg = _validate_patch_structure(patch_text)
            if not valid:
                raise Exception(f"Patch invalid after sanitization: {error_msg}")

            print(f"[agent] Testing patch with dry run...")

            # Test the patch with dry run
            ok, dry_out = _dry_run_patch(patch_text)
            if ok:
                print(f"[agent] Patch validation successful!")
                return patch_text

            # Patch failed - add feedback for correction
            print(f"[agent] Patch failed validation (attempt {attempt + 1})")
            print(f"[agent] Dry run output: {dry_out[:500]}...")

            # Debug: Show patch content for troubleshooting
            print(f"[agent] Failed patch content (first 300 chars):")
            print(repr(patch_text[:300]))
            print(f"[agent] Failed patch content (last 100 chars):")
            print(repr(patch_text[-100:]))

            messages.append({"role": "assistant", "content": patch_text})
            messages.append({"role": "user", "content": f"Patch failed to apply. Patch output was:\n{dry_out}\nPlease reply with a corrected unified diff only."})

        except Exception as e:
            print(f"[agent] Request failed (attempt {attempt + 1}): {e}")
            if attempt == ATTEMPTS - 1:
                raise RuntimeError(f"All {ATTEMPTS} attempts failed: {e}")
            continue

    # All attempts exhausted
    raise RuntimeError("Patch could not be applied after iterative corrections.")

# ============================================================================
# ITERATIVE REFINEMENT FUNCTIONS
# ============================================================================

def _discover_relevant_tests(target_files: List[str], problem_text: str) -> List[str]:
    """Find tests that are likely relevant to the problem"""
    print(f"[refinement] Discovering tests for {len(target_files)} target files...")

    test_files = []

    # Strategy 1: Tests in same directory or parallel test directory
    for target_file in target_files:
        target_dir = os.path.dirname(target_file)

        # Look for test files in same directory
        if os.path.exists(target_dir):
            for file in os.listdir(target_dir):
                if file.startswith('test_') and file.endswith('.py'):
                    test_files.append(os.path.join(target_dir, file))

        # Look for tests/ directory structure
        test_dirs = [
            os.path.join(target_dir, 'tests'),
            os.path.join('tests', target_dir),
            'tests',
            os.path.join(target_dir, '..', 'tests'),
        ]

        for test_dir in test_dirs:
            if os.path.exists(test_dir):
                for root, dirs, files in os.walk(test_dir):
                    for file in files:
                        if (file.startswith('test_') or file.endswith('_test.py')) and file.endswith('.py'):
                            test_files.append(os.path.join(root, file))

    # Strategy 2: Look for specific patterns mentioned in problem
    problem_lower = problem_text.lower()
    if 'session' in problem_lower and 'django' in problem_lower:
        django_session_tests = [
            'tests/sessions_tests/tests.py',
            'tests/sessions_tests/',
            'django/contrib/sessions/tests.py'
        ]
        for test_path in django_session_tests:
            if os.path.exists(test_path):
                test_files.append(test_path)

    # Remove duplicates and return
    test_files = list(set(test_files))
    print(f"[refinement] Found {len(test_files)} potential test files")
    return test_files

def _test_patch(patch: str, target_files: List[str], problem_text: str) -> Dict[str, Any]:
    """Apply patch and run tests to validate the fix"""
    print(f"[refinement] Testing patch ({len(patch)} chars)...")

    result = {
        "patch_applied": False,
        "compilation_success": False,
        "test_results": {
            "passed": 0,
            "failed": 0,
            "errors": [],
            "output": ""
        },
        "success": False,
        "tests_found": False
    }

    # First, try to apply the patch with dry run
    print(f"[refinement] Applying patch with dry run...")
    patch_ok, patch_output = _dry_run_patch(patch)

    if not patch_ok:
        print(f"[refinement] Patch failed to apply: {patch_output[:500]}")
        result["test_results"]["errors"].append(f"Patch application failed: {patch_output}")
        return result

    result["patch_applied"] = True

    # Create a backup of current state before applying patch
    print(f"[refinement] Creating backup before applying patch...")
    backup_files = {}

    try:
        # Apply the patch for real
        with NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as tmp:
            tmp.write(patch)
            tmp_path = tmp.name

        apply_result = subprocess.run(
            ["patch", "-p1", "-i", tmp_path],
            capture_output=True,
            text=True,
            timeout=60
        )

        os.unlink(tmp_path)

        if apply_result.returncode != 0:
            result["test_results"]["errors"].append(f"Patch application failed: {apply_result.stderr}")
            return result

        print(f"[refinement] Patch applied successfully")

        # Try to compile/validate the changes
        print(f"[refinement] Checking compilation...")
        compilation_errors = []

        for target_file in target_files:
            if target_file.endswith('.py') and os.path.exists(target_file):
                try:
                    with open(target_file, 'r') as f:
                        code = f.read()
                    compile(code, target_file, 'exec')
                except SyntaxError as e:
                    compilation_errors.append(f"Syntax error in {target_file}: {e}")
                except Exception as e:
                    compilation_errors.append(f"Compilation error in {target_file}: {e}")

        if compilation_errors:
            result["test_results"]["errors"].extend(compilation_errors)
            return result

        result["compilation_success"] = True

        # Discover and run relevant tests
        test_files = _discover_relevant_tests(target_files, problem_text)

        if not test_files:
            print(f"[refinement] No test files found, considering patch successful")
            result["success"] = True
            return result

        result["tests_found"] = True

        # Run tests
        print(f"[refinement] Running {len(test_files)} test files...")

        test_commands = []

        # Try different test runners based on project structure
        if any('django' in f.lower() for f in target_files):
            # Django project
            test_commands = [
                "python manage.py test --verbosity=2",
                "python -m pytest tests/ -v",
                "python runtests.py --verbosity=2"
            ]
        else:
            # Generic Python project
            test_commands = [
                "python -m pytest tests/ -v",
                "python -m unittest discover -s tests -v",
                "python -m pytest . -v"
            ]

        test_passed = False
        test_output = ""

        for cmd in test_commands:
            try:
                print(f"[refinement] Trying test command: {cmd}")
                test_result = subprocess.run(
                    cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=TEST_TIMEOUT_SECONDS,
                    cwd='.'
                )

                test_output = test_result.stdout + test_result.stderr

                if test_result.returncode == 0:
                    print(f"[refinement] Tests passed with command: {cmd}")
                    result["test_results"]["passed"] = test_output.count("PASSED") + test_output.count("ok")
                    result["test_results"]["output"] = test_output
                    result["success"] = True
                    test_passed = True
                    break
                else:
                    print(f"[refinement] Tests failed with command: {cmd}")
                    result["test_results"]["failed"] += 1
                    result["test_results"]["errors"].append(f"Test command failed: {cmd}\nOutput: {test_output[:1000]}")

            except subprocess.TimeoutExpired:
                print(f"[refinement] Test command timed out: {cmd}")
                result["test_results"]["errors"].append(f"Test timeout: {cmd}")
            except Exception as e:
                print(f"[refinement] Test command error: {cmd} - {e}")
                result["test_results"]["errors"].append(f"Test error: {cmd} - {e}")

        if not test_passed and test_output:
            result["test_results"]["output"] = test_output

    except Exception as e:
        print(f"[refinement] Exception during testing: {e}")
        result["test_results"]["errors"].append(f"Testing exception: {e}")

    finally:
        # Rollback the patch to restore original state
        print(f"[refinement] Rolling back patch...")
        try:
            subprocess.run(
                ["git", "checkout", "."],
                capture_output=True,
                text=True,
                timeout=30
            )
        except Exception as e:
            print(f"[refinement] Warning: Failed to rollback changes: {e}")

    return result

def _analyze_test_failures(test_results: Dict, patch: str, problem_text: str) -> Dict[str, Any]:
    """Analyze why tests failed and what needs to be fixed"""
    print(f"[refinement] Analyzing test failures...")

    errors = test_results.get("test_results", {}).get("errors", [])
    output = test_results.get("test_results", {}).get("output", "")

    analysis = {
        "failure_type": "unknown",
        "root_cause": "",
        "specific_errors": [],
        "suggested_fixes": []
    }

    # Check for compilation errors
    if not test_results.get("compilation_success", False):
        analysis["failure_type"] = "compilation_error"
        syntax_errors = [e for e in errors if "syntax" in e.lower() or "compilation" in e.lower()]
        if syntax_errors:
            analysis["root_cause"] = "Syntax or compilation errors in the patch"
            analysis["specific_errors"] = syntax_errors
            analysis["suggested_fixes"] = [
                "Fix syntax errors in the patch",
                "Check for missing imports or undefined variables",
                "Verify proper indentation and structure"
            ]
        return analysis

    # Check for patch application failures
    if not test_results.get("patch_applied", False):
        analysis["failure_type"] = "patch_application_error"
        analysis["root_cause"] = "Patch could not be applied to the codebase"
        analysis["specific_errors"] = [e for e in errors if "patch" in e.lower()]
        analysis["suggested_fixes"] = [
            "Check line numbers and context in the patch",
            "Verify file paths are correct",
            "Ensure patch format is valid"
        ]
        return analysis

    # Analyze test failures
    if errors or "failed" in output.lower() or "error" in output.lower():
        analysis["failure_type"] = "test_failure"

        # Look for specific error patterns
        common_patterns = {
            "attributeerror": "Method or attribute doesn't exist",
            "typeerror": "Incorrect argument types or API usage",
            "importerror": "Import or module issues",
            "assertionerror": "Logic error - test expectations not met",
            "keyerror": "Missing dictionary key or configuration",
            "valueerror": "Invalid parameter values"
        }

        for pattern, description in common_patterns.items():
            if pattern in output.lower() or any(pattern in e.lower() for e in errors):
                analysis["root_cause"] = description
                break

        if not analysis["root_cause"]:
            analysis["root_cause"] = "Tests are failing, likely due to incorrect logic in the patch"

        analysis["specific_errors"] = errors
        analysis["suggested_fixes"] = [
            "Review the patch logic against test expectations",
            "Check if the patch addresses the root cause of the problem",
            "Consider alternative implementation approach",
            "Verify all edge cases are handled"
        ]

    return analysis

def _generate_refinement_prompt(
    original_problem: str,
    current_patch: str,
    test_failures: Dict,
    iteration: int
) -> str:
    """Create focused prompt for patch refinement based on test failures"""

    failure_analysis = _analyze_test_failures(test_failures, current_patch, original_problem)

    prompt = f"""Problem to fix:
{original_problem}

Previous patch attempt (iteration {iteration-1}) failed:
{current_patch}

Test Results:
- Patch Applied: {test_failures.get('patch_applied', False)}
- Compilation Success: {test_failures.get('compilation_success', False)}
- Tests Found: {test_failures.get('tests_found', False)}
- Success: {test_failures.get('success', False)}

Failure Analysis:
- Type: {failure_analysis['failure_type']}
- Root Cause: {failure_analysis['root_cause']}
- Specific Errors: {failure_analysis['specific_errors']}

Test Output:
{test_failures.get('test_results', {}).get('output', 'No test output')[:2000]}

Errors:
{'; '.join(test_failures.get('test_results', {}).get('errors', []))}

Based on this analysis, please generate a refined patch that addresses these issues. Learn from the previous failure and try a different approach."""

    return prompt

def _is_refinement_worthwhile(test_result: Dict) -> bool:
    """Decide if refinement is likely to help"""

    # Don't refine if we can't run any validation
    if not test_result.get("patch_applied", False):
        patch_errors = test_result.get("test_results", {}).get("errors", [])
        # Only refine patch application errors if they look fixable
        if any("syntax" in e.lower() or "line" in e.lower() for e in patch_errors):
            return True
        return False

    # DO refine for these recoverable issues:
    if test_result.get("compilation_success", False):
        return True  # Logic errors we can fix

    errors = test_result.get("test_results", {}).get("errors", [])
    if any("syntax" in str(e).lower() for e in errors):
        return True  # Syntax errors we can fix

    # If no tests found, but patch applied and compiles, consider it worthwhile
    if not test_result.get("tests_found", False) and test_result.get("compilation_success", False):
        return False  # Can't validate further

    return True  # Default: try refinement

def _should_continue_refinement(test_result: Dict, iteration: int) -> bool:
    """Decide if another iteration is worthwhile"""

    # Stop if we're at max iterations
    if iteration >= MAX_REFINEMENT_ITERATIONS:
        return False

    # Stop if patch can't be applied and we've tried once
    if not test_result.get("patch_applied", False) and iteration > 1:
        return False

    # Continue if there's clear progress potential
    if test_result.get("compilation_success", False):
        return True

    return True

def run_iterative_refinement(
    initial_patch: str,
    target_files: List[str],
    problem_text: str,
    memory: ConversationMemory,
    *,
    proxy_url: str,
    model_name: str,
    run_id: str
) -> str:
    """
    Phase 3: Iterative refinement with test-driven validation

    Flow:
    1. Apply initial patch
    2. Run relevant tests
    3. Analyze results
    4. If tests pass → return patch
    5. If tests fail → generate refinement prompt
    6. Generate improved patch
    7. Repeat up to 3 times
    """
    print(f"[refinement] Starting iterative refinement with {len(target_files)} target files")

    current_patch = initial_patch

    for iteration in range(1, MAX_REFINEMENT_ITERATIONS + 1):
        print(f"[refinement] Testing iteration {iteration}/{MAX_REFINEMENT_ITERATIONS}")

        test_result = _test_patch(current_patch, target_files, problem_text)

        # SUCCESS: Return immediately
        if test_result["success"]:
            print(f"[refinement] Success on iteration {iteration}! Tests pass.")
            return current_patch

        # FIRST ITERATION: Decide if refinement is worthwhile
        if iteration == 1 and not _is_refinement_worthwhile(test_result):
            print(f"[refinement] Refinement not worthwhile, returning initial patch")
            return current_patch

        # LAST ITERATION: Return best attempt
        if iteration == MAX_REFINEMENT_ITERATIONS:
            print(f"[refinement] Max iterations reached, returning best attempt")
            return current_patch

        # CONTINUE: Generate refinement for next iteration
        if _should_continue_refinement(test_result, iteration):
            print(f"[refinement] Generating refinement for iteration {iteration + 1}")

            try:
                # Generate refinement prompt
                refinement_prompt = _generate_refinement_prompt(
                    problem_text, current_patch, test_result, iteration
                )

                # Create messages for refinement request
                messages = [
                    {"role": "system", "content": PATCH_REFINEMENT_SYSTEM_PROMPT},
                    {"role": "user", "content": refinement_prompt}
                ]

                # Add memory context if available
                if memory:
                    context_summary = memory.get_context_summary()
                    if context_summary.strip():
                        messages.append({"role": "user", "content": f"Additional Context from Exploration:\n\n{context_summary}"})

                # Generate refined patch
                response = inference(messages, proxy_url, run_id, model_name)
                text_response = response.get("text_response", "")
                code_response = response.get("code_response", "")

                # Extract refined patch using same logic as focused oneshot
                refined_patch = None
                for cand in (code_response, text_response):
                    if cand and cand.strip().startswith("diff --git"):
                        refined_patch = cand.strip()
                        break

                if refined_patch is None:
                    for cand in (text_response, code_response):
                        if cand:
                            extracted = extract_diff_from_response(cand)
                            if extracted:
                                refined_patch = extracted
                                break

                if refined_patch:
                    # Validate and sanitize refined patch
                    valid, error_msg = _validate_patch_structure(refined_patch)
                    if valid:
                        refined_patch = _sanitize_patch(refined_patch)
                        if refined_patch.strip():
                            print(f"[refinement] Generated refined patch ({len(refined_patch)} chars)")
                            current_patch = refined_patch
                        else:
                            print(f"[refinement] Refined patch became empty after sanitization")
                            break
                    else:
                        print(f"[refinement] Refined patch invalid: {error_msg}")
                        break
                else:
                    print(f"[refinement] Failed to extract refined patch from response")
                    break

            except Exception as e:
                print(f"[refinement] Failed to generate refinement: {e}")
                break
        else:
            print(f"[refinement] Stopping early at iteration {iteration}")
            break

    return current_patch

# ============================================================================
# TEST ANALYSIS FUNCTIONS
# ============================================================================

def _discover_relevant_tests_for_analysis(target_files: List[str], problem_text: str) -> List[str]:
    """Discover tests that are most likely to reveal the current bug behavior"""
    print(f"[test-analysis] Discovering tests for {len(target_files)} target files...")

    test_files = []

    # Strategy 1: Tests in same directory or parallel test directory
    for target_file in target_files:
        target_dir = os.path.dirname(target_file)
        target_name = os.path.basename(target_file).replace('.py', '')

        # Look for test files in same directory
        test_patterns = [
            f"test_{target_name}.py",
            f"{target_name}_test.py",
            f"tests.py"
        ]

        for pattern in test_patterns:
            test_path = os.path.join(target_dir, pattern)
            if os.path.exists(test_path):
                test_files.append(test_path)

        # Look for tests directory
        possible_test_dirs = [
            os.path.join(target_dir, "tests"),
            os.path.join(target_dir, "test"),
            os.path.join(os.path.dirname(target_dir), "tests"),
            "./tests"
        ]

        for test_dir in possible_test_dirs:
            if os.path.exists(test_dir) and os.path.isdir(test_dir):
                for root, dirs, files in os.walk(test_dir):
                    for file in files:
                        if (file.startswith('test_') or file.endswith('_test.py')) and file.endswith('.py'):
                            test_path = os.path.join(root, file)
                            # Check if test file mentions our target
                            if _test_file_relevant(test_path, target_name, target_file):
                                test_files.append(test_path)

    # Remove duplicates and limit to most relevant
    test_files = list(set(test_files))[:10]  # Limit to avoid excessive testing

    print(f"[test-analysis] Found {len(test_files)} relevant test files")
    return test_files

def _test_file_relevant(test_file: str, target_name: str, target_file: str) -> bool:
    """Check if a test file is likely relevant to our target"""
    try:
        with open(test_file, 'r') as f:
            content = f.read()
            # Check for imports or references to target
            return (target_name in content or
                   os.path.basename(target_file) in content or
                   any(part in content for part in target_file.split('/') if len(part) > 3))
    except:
        return False

def _run_current_tests(test_files: List[str], target_files: List[str]) -> Dict[str, Any]:
    """Run existing tests to understand current behavior"""
    print(f"[test-analysis] Running {len(test_files)} test files to analyze current behavior...")

    results = {
        "test_results": [],
        "overall_status": "unknown",
        "failure_patterns": [],
        "current_behavior": ""
    }

    if not test_files:
        print("[test-analysis] No test files found, skipping test execution")
        return results

    # Try to run tests using common patterns
    for test_file in test_files[:5]:  # Limit to avoid long execution
        print(f"[test-analysis] Running test file: {test_file}")

        # Try different test runners
        test_commands = [
            ["python", "-m", "pytest", test_file, "-v", "--tb=short"],
            ["python", "-m", "unittest", test_file.replace('/', '.').replace('.py', ''), "-v"],
            ["python", test_file]
        ]

        for cmd in test_commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=TEST_DISCOVERY_TIMEOUT,
                    cwd="."
                )

                test_result = {
                    "file": test_file,
                    "command": " ".join(cmd),
                    "returncode": result.returncode,
                    "stdout": result.stdout[:1000],  # Limit output
                    "stderr": result.stderr[:1000]
                }

                results["test_results"].append(test_result)

                # Analyze test output for behavior patterns
                if result.returncode != 0:
                    failure_info = _extract_failure_info(result.stdout + result.stderr)
                    if failure_info:
                        results["failure_patterns"].append(failure_info)

                # If test ran successfully, don't try other runners
                if result.returncode in [0, 1]:  # 0=pass, 1=test failures (but runner worked)
                    break

            except subprocess.TimeoutExpired:
                print(f"[test-analysis] Test {test_file} timed out with command: {' '.join(cmd)}")
                continue
            except Exception as e:
                print(f"[test-analysis] Failed to run {test_file}: {e}")
                continue

    # Determine overall status
    if results["test_results"]:
        failed_tests = [r for r in results["test_results"] if r["returncode"] != 0]
        if failed_tests:
            results["overall_status"] = "has_failures"
            results["current_behavior"] = f"Found {len(failed_tests)} failing test(s)"
        else:
            results["overall_status"] = "passing"
            results["current_behavior"] = "All tests currently pass"

    print(f"[test-analysis] Test analysis complete: {results['overall_status']}")
    return results

def _extract_failure_info(test_output: str) -> str:
    """Extract meaningful failure information from test output"""
    failure_indicators = [
        "AssertionError", "ValueError", "TypeError", "AttributeError",
        "FAILED", "ERROR", "Exception", "Traceback"
    ]

    lines = test_output.split('\n')
    failure_info = []

    for i, line in enumerate(lines):
        if any(indicator in line for indicator in failure_indicators):
            # Capture this line and a few context lines
            context_start = max(0, i-2)
            context_end = min(len(lines), i+3)
            context = lines[context_start:context_end]
            failure_info.extend(context)

    return '\n'.join(failure_info[:10])  # Limit output

def _create_behavior_analysis(problem_text: str, target_files: List[str], test_results: Dict) -> Dict[str, Any]:
    """Analyze current vs expected behavior based on problem description and test results"""
    print("[test-analysis] Creating behavior analysis...")

    analysis = {
        "problem_summary": problem_text[:300],
        "target_files": target_files,
        "current_behavior": "Unknown",
        "expected_behavior": "Unknown",
        "behavior_gap": "Unknown",
        "test_evidence": test_results.get("current_behavior", "No test evidence"),
        "failure_patterns": test_results.get("failure_patterns", [])
    }

    # Extract expected behavior from problem description
    expected_indicators = [
        "should", "expected", "ought to", "supposed to",
        "correct behavior", "desired", "intended"
    ]

    problem_lines = problem_text.split('\n')
    expected_behaviors = []

    for line in problem_lines:
        if any(indicator in line.lower() for indicator in expected_indicators):
            expected_behaviors.append(line.strip())

    if expected_behaviors:
        analysis["expected_behavior"] = '; '.join(expected_behaviors[:3])

    # Determine current behavior from test results
    if test_results["overall_status"] == "has_failures":
        analysis["current_behavior"] = f"Tests failing: {test_results['current_behavior']}"
        if test_results["failure_patterns"]:
            analysis["current_behavior"] += f" | Errors: {test_results['failure_patterns'][0][:100]}"
    elif test_results["overall_status"] == "passing":
        analysis["current_behavior"] = "Tests pass but issue reported in problem statement"
    else:
        analysis["current_behavior"] = "Unable to determine from tests - need to reproduce issue"

    # Identify behavior gap
    if "fail" in analysis["current_behavior"].lower() and analysis["expected_behavior"] != "Unknown":
        analysis["behavior_gap"] = f"Current: {analysis['current_behavior'][:100]} | Expected: {analysis['expected_behavior'][:100]}"
    else:
        analysis["behavior_gap"] = "Gap needs further investigation through reproduction"

    print(f"[test-analysis] Behavior analysis complete")
    return analysis

def run_test_analysis(problem_text: str, target_files: List[str]) -> Dict[str, Any]:
    """Phase 2: Test-First Understanding - Analyze current behavior before generating patches"""
    print("[agent] Phase 2: Test-First Understanding...")

    try:
        # Step 1: Discover relevant tests
        test_files = _discover_relevant_tests_for_analysis(target_files, problem_text)

        # Step 2: Run tests to understand current behavior
        test_results = _run_current_tests(test_files, target_files)

        # Step 3: Create behavior analysis
        behavior_analysis = _create_behavior_analysis(problem_text, target_files, test_results)

        # Step 4: Package results for patch generation
        analysis_result = {
            "success": True,
            "test_files_found": len(test_files),
            "test_results": test_results,
            "behavior_analysis": behavior_analysis,
            "summary": f"Found {len(test_files)} test files, status: {test_results['overall_status']}"
        }

        print(f"[test-analysis] Analysis complete: {analysis_result['summary']}")
        return analysis_result

    except Exception as e:
        print(f"[test-analysis] Test analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "behavior_analysis": {
                "current_behavior": "Test analysis failed",
                "expected_behavior": "Unknown due to analysis failure",
                "behavior_gap": f"Analysis error: {str(e)}"
            }
        }

def run_hybrid(problem_text: str, *, proxy_url: str, model_name: str, run_id: str) -> Dict[str, Any]:
    """
    Main hybrid approach: Four-phase pipeline for robust patch generation.

    Phase 1: Exploration - Locate the problem in the codebase
    Phase 2: Test-First Understanding - Analyze current behavior via tests before fixing
    Phase 3: Initial Patch Generation - Generate focused patch based on exploration and test analysis
    Phase 4: Iterative Refinement - Test and refine patch up to 3 times for correctness
    """

    print("[agent] Starting hybrid approach...")
    print("[agent] Phase 1: Exploration to locate problem...")

    # Phase 1: Exploration
    exploration_result = run_exploration(
        problem_text,
        proxy_url=proxy_url,
        model_name=model_name,
        run_id=run_id
    )

    print(f"[agent] Exploration result: {exploration_result['success']}")
    if not exploration_result["success"]:
        print(f"[agent] Exploration failed: {exploration_result['error']}")
        return {
            "success": False,
            "error": exploration_result["error"],
            "exploration_history": exploration_result.get("exploration_history", [])
        }

    target_files = exploration_result["target_files"]
    problem_location = exploration_result["problem_location"]
    memory = exploration_result.get("memory")  # Extract conversation memory

    print(f"[agent] Exploration found target files: {target_files}")
    print(f"[agent] Problem location: {problem_location}")
    if memory:
        print(f"[agent] Retrieved conversation memory with {len(memory.exploration_steps)} exploration steps")

    # Phase 2: Test-First Understanding
    test_analysis_result = run_test_analysis(problem_text, target_files)
    print(f"[agent] Test analysis result: {test_analysis_result['success']}")
    if test_analysis_result["success"]:
        print(f"[agent] {test_analysis_result['summary']}")
        behavior_analysis = test_analysis_result["behavior_analysis"]
        print(f"[agent] Behavior gap: {behavior_analysis['behavior_gap'][:100]}")
    else:
        print(f"[agent] Test analysis failed: {test_analysis_result.get('error', 'Unknown error')}")
        # Continue with limited information
        behavior_analysis = test_analysis_result["behavior_analysis"]

    print("[agent] Phase 3: Focused patch generation...")

    # Phase 3: Focused oneshot
    try:
        initial_patch = run_focused_oneshot(
            problem_text,
            target_files,
            memory,  # Pass memory to patch generation
            behavior_analysis,  # Pass test analysis to inform patch generation
            proxy_url=proxy_url,
            model_name=model_name,
            run_id=run_id
        )

        print(f"[agent] Generated initial patch ({len(initial_patch)} chars)")
        print("[agent] Phase 4: Iterative refinement with test validation...")

        # Phase 4: Iterative refinement
        try:
            final_patch = run_iterative_refinement(
                initial_patch,
                target_files,
                problem_text,
                memory,
                proxy_url=proxy_url,
                model_name=model_name,
                run_id=run_id
            )

            print(f"[agent] Refinement completed, final patch ({len(final_patch)} chars)")

            return {
                "success": True,
                "patch": final_patch,
                "target_files": target_files,
                "problem_location": problem_location,
                "exploration_history": exploration_result["exploration_history"]
            }

        except Exception as e:
            print(f"[agent] Iterative refinement failed: {e}")
            print(f"[agent] Returning initial patch as fallback")

            return {
                "success": True,
                "patch": initial_patch,
                "target_files": target_files,
                "problem_location": problem_location,
                "exploration_history": exploration_result["exploration_history"],
                "refinement_error": str(e)
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Focused oneshot failed: {e}",
            "target_files": target_files,
            "problem_location": problem_location,
            "exploration_history": exploration_result["exploration_history"]
        }

class Chunk(NamedTuple):
    file: str
    start_line: int
    end_line: int
    text: str

class ConversationMemory:
    """Stores rich context and insights from exploration to inform patch generation."""

    def __init__(self):
        self.problem_analysis = {}
        self.code_insights = {}
        self.exploration_steps = []
        self.patterns_found = []
        self.failed_attempts = []
        self.file_analysis = {}

    def add_problem_analysis(self, analysis: Dict[str, Any]):
        """Store initial problem breakdown and analysis."""
        self.problem_analysis.update(analysis)

    def add_exploration_step(self, step: int, command: str, observation: str, insight: str = ""):
        """Record an exploration step with its results and insights."""
        self.exploration_steps.append({
            "step": step,
            "command": command,
            "observation": observation[:1000],  # Truncate very long observations
            "insight": insight,
            "timestamp": step
        })

    def add_code_insight(self, file_path: str, insight_type: str, details: str):
        """Store insights about specific code files or patterns."""
        if file_path not in self.code_insights:
            self.code_insights[file_path] = []
        self.code_insights[file_path].append({
            "type": insight_type,
            "details": details
        })

    def add_pattern(self, pattern_type: str, description: str, evidence: str = ""):
        """Record patterns found during exploration."""
        self.patterns_found.append({
            "type": pattern_type,
            "description": description,
            "evidence": evidence[:500]  # Truncate evidence
        })

    def add_file_analysis(self, file_path: str, analysis: Dict[str, Any]):
        """Store detailed analysis of a specific file."""
        self.file_analysis[file_path] = analysis

    def get_context_summary(self) -> str:
        """Generate a rich context summary for patch generation."""
        summary_parts = []

        # Problem analysis
        if self.problem_analysis:
            summary_parts.append("PROBLEM ANALYSIS:")
            for key, value in self.problem_analysis.items():
                summary_parts.append(f"- {key}: {value}")
            summary_parts.append("")

        # Key insights from exploration
        if self.exploration_steps:
            summary_parts.append("EXPLORATION INSIGHTS:")
            for step in self.exploration_steps[-5:]:  # Last 5 steps
                if step.get("insight"):
                    summary_parts.append(f"- Step {step['step']}: {step['insight']}")
            summary_parts.append("")

        # Code patterns found
        if self.patterns_found:
            summary_parts.append("PATTERNS IDENTIFIED:")
            for pattern in self.patterns_found:
                summary_parts.append(f"- {pattern['type']}: {pattern['description']}")
            summary_parts.append("")

        # File-specific insights
        if self.code_insights:
            summary_parts.append("CODE INSIGHTS:")
            for file_path, insights in self.code_insights.items():
                summary_parts.append(f"- {file_path}:")
                for insight in insights:
                    summary_parts.append(f"  * {insight['type']}: {insight['details']}")
            summary_parts.append("")

        return "\n".join(summary_parts)

    def get_detailed_exploration_log(self) -> str:
        """Get detailed log of exploration steps for debugging."""
        log_parts = []
        for step in self.exploration_steps:
            log_parts.append(f"Step {step['step']}: {step['command']}")
            if step['observation']:
                log_parts.append(f"  Result: {step['observation'][:200]}...")
            if step['insight']:
                log_parts.append(f"  Insight: {step['insight']}")
            log_parts.append("")
        return "\n".join(log_parts)

def _extract_insight_from_observation(command: str, observation: str) -> str:
    """Extract meaningful insights from command observations for memory storage."""
    if not observation or "Error" in observation or not command.strip():
        return ""

    # Safe command type extraction - handle empty commands
    if '(' in command:
        command_type = command.split('(')[0]
    else:
        command_parts = command.split()
        command_type = command_parts[0] if command_parts else ""

    if not command_type:
        return ""
    observation_lower = observation.lower()

    insights = []

    # READ_FILE insights
    if command_type == "READ_FILE":
        if "def " in observation:
            func_count = observation.count("def ")
            insights.append(f"Contains {func_count} function definitions")
        if "class " in observation:
            class_count = observation.count("class ")
            insights.append(f"Contains {class_count} class definitions")
        if "import " in observation:
            insights.append("Contains import statements")
        if any(error_word in observation_lower for error_word in ["error", "exception", "traceback", "bug"]):
            insights.append("Contains error-related code")

    # GREP insights
    elif command_type == "GREP":
        if observation.strip():
            line_count = observation.count('\n') + 1 if observation.strip() else 0
            if line_count > 0:
                insights.append(f"Found {line_count} matching lines")
                # Extract file patterns
                files = set()
                for line in observation.split('\n'):
                    if ':' in line:
                        file_part = line.split(':')[0]
                        if '/' in file_part:
                            files.add(file_part)
                if files:
                    insights.append(f"Matches in {len(files)} files")

    # SMART_SEARCH insights
    elif command_type == "SMART_SEARCH":
        if "relevant" in observation_lower:
            insights.append("Found relevant files via AI search")
        if "suggestion" in observation_lower:
            insights.append("Provided intelligent suggestions")

    # LS insights
    elif command_type == "LS":
        line_count = observation.count('\n')
        if line_count > 5:
            insights.append(f"Directory contains many files ({line_count} entries)")
        if ".py" in observation:
            py_count = observation.count(".py")
            insights.append(f"Found {py_count} Python files")

    # FIND insights
    elif command_type == "FIND":
        if observation.strip():
            file_count = observation.count('\n') + 1 if observation.strip() else 0
            insights.append(f"Found {file_count} matching files")

    return "; ".join(insights) if insights else ""

def _guess_tokens(text: str) -> int:
    """Rough estimate of token count."""
    return len(text) // 4

def _lang_tag(path: str) -> str:
    """Get language tag for syntax highlighting."""
    ext = Path(path).suffix.lower()
    return {
        '.py': 'python',
        '.js': 'javascript',
        '.ts': 'typescript',
        '.java': 'java',
        '.cpp': 'cpp',
        '.c': 'c',
        '.rb': 'ruby',
        '.go': 'go',
        '.rs': 'rust',
        '.php': 'php',
        '.sh': 'bash',
    }.get(ext, 'text')

def _collect_repo_texts(root: str = ".") -> Dict[str, str]:
    """Collect entire files as single chunks."""
    texts = {}
    skip_patterns = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.venv', 'venv'}

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and cache directories
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in skip_patterns]

        for filename in filenames:
            if filename.startswith('.'):
                continue

            filepath = os.path.join(dirpath, filename)
            try:
                # Only process text files
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if len(content.strip()) > 0:  # Skip empty files
                        relative_path = os.path.relpath(filepath, root)
                        texts[relative_path] = content
            except (UnicodeDecodeError, PermissionError):
                # Skip binary files and permission errors
                continue

    return texts

def _collect_code_chunks(root: str = ".") -> List[Chunk]:
    """Collect code chunks using function/class level granularity."""
    chunks = []
    skip_patterns = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.venv', 'venv'}

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and cache directories
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in skip_patterns]

        for filename in filenames:
            if not filename.endswith('.py'):  # Focus on Python files for now
                continue
            if filename.startswith('.'):
                continue

            filepath = os.path.join(dirpath, filename)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                relative_path = os.path.relpath(filepath, root)

                # Try to parse into functions/classes
                try:
                    tree = ast.parse(content)
                    lines = content.split('\n')

                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                            start_line = node.lineno
                            end_line = node.end_lineno or start_line + 10

                            # Extract the function/class text
                            chunk_lines = lines[start_line-1:end_line]
                            chunk_text = '\n'.join(chunk_lines)

                            if len(chunk_text.strip()) > 50:  # Skip very small chunks
                                chunks.append(Chunk(
                                    file=relative_path,
                                    start_line=start_line,
                                    end_line=end_line,
                                    text=chunk_text
                                ))

                except SyntaxError:
                    # If AST parsing fails, just use the whole file
                    chunks.append(Chunk(
                        file=relative_path,
                        start_line=1,
                        end_line=len(content.split('\n')),
                        text=content
                    ))

            except (UnicodeDecodeError, PermissionError):
                continue

    return chunks

def agent_main(input_dict: Dict[str, Any]):
    """
    Main entry point for the hybrid agent.

    Uses hybrid approach by default, but can fall back to traditional modes
    via environment variables.
    """

    problem_text = input_dict.get("problem_statement", "")
    proxy_url = input_dict.get("proxy_url", DEFAULT_PROXY_URL)
    model_name = input_dict.get("model_name", DEFAULT_MODEL)
    run_id = input_dict.get("run_id") or os.getenv("RUN_ID") or str(uuid.uuid4())

    mode = os.getenv("AGENT_MODE", "HYBRID").upper()

    if mode == "HYBRID":
        # Our new hybrid approach
        result = run_hybrid(
            problem_text,
            proxy_url=proxy_url,
            model_name=model_name,
            run_id=run_id
        )

        if result["success"]:
            patch = result["patch"]
            print(f"[agent] Returning patch of length: {len(patch)}")
            print(f"[agent] Patch preview: {patch[:100]}...")
            return {"patch": patch}
        else:
            # If hybrid fails, could fall back to traditional oneshot
            print(f"[agent] Hybrid approach failed: {result['error']}")
            print("[agent] Falling back to traditional oneshot...")
            # TODO: Add fallback to original oneshot if needed
            return {"error": result["error"]}

    else:
        # Traditional modes would go here if needed
        return {"error": f"Mode {mode} not implemented in hybrid agent"}

if __name__ == "__main__":
    # Simple test harness
    test_input = {
        "problem_statement": "Fix the Django username validator to properly handle Unicode characters",
        "proxy_url": DEFAULT_PROXY_URL,
        "model_name": DEFAULT_MODEL,
        "run_id": "test"
    }

    result = agent_main(test_input)
    print("Result:", result)