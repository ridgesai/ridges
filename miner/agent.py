from __future__ import annotations

import json
import os
import subprocess
import textwrap
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
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

# Hybrid mode constants
MAX_EXPLORATION_STEPS = int(os.getenv("MAX_EXPLORATION_STEPS", "20"))
MAX_OBS_CHARS = 20_000
MAX_BYTES_READ = 25_000

# Use function chunks by default
USE_FUNCTION_CHUNKS = os.getenv("EMBED_WHOLE_FILES", "0") != "1"

# Zero vector for failed embeddings
ZERO_VEC = [0.0] * 768

# Available commands for exploration
EXPLORATION_COMMANDS = {
    "READ_FILE": "READ_FILE(path): Read a file up to 25KB. Takes ONLY ONE argument (file path). Usage: READ_FILE(\"path/to/file.py\")",
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
        url = f"{proxy_url}/agents/embedding"
        data = json.dumps({"text": text, "run_id": run_id}).encode('utf-8')

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
        "temperature": 0.0
    }

    if model:
        request_data["model"] = model

    # Add tools if provided
    if tools:
        request_data["tools"] = tools

    url = f"{proxy_url.rstrip('/')}/agents/inference"
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

def _extract_problem_keywords(problem_text: str) -> Dict[str, List[str]]:
    """Extract relevant keywords from natural language problem description."""
    features = {
        'domain_keywords': [],
        'component_names': [],
        'error_terms': [],
        'action_words': [],
        'technical_terms': [],
        'file_mentions': []
    }

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

def _compute_problem_code_similarity(problem_features: Dict[str, List[str]], code_features: Dict[str, List[str]], file_path: str) -> float:
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
    """Enhanced pre-filtering using problem-to-code similarity matching."""

    print(f"Starting improved problem-to-code filtering with {len(chunk_texts)} chunks...")

    # Extract features from problem text (natural language)
    problem_features = _extract_problem_keywords(problem_text)
    print(f"Problem features extracted: {sum(len(v) for v in problem_features.values())} total features")

    # 1. Problem-to-code feature matching
    print("Computing problem-to-code similarities...")
    feature_similarities = []
    for i, chunk in enumerate(code_chunks):
        code_features = _extract_code_features(chunk_texts[i], chunk.file)
        feature_sim = _compute_problem_code_similarity(problem_features, code_features, chunk.file)
        feature_similarities.append(feature_sim)

    # 2. Enhanced TF-IDF similarity
    print("Computing enhanced TF-IDF similarities...")
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

    feature_similarities = normalize_scores(feature_similarities)
    tfidf_similarities = normalize_scores(tfidf_similarities)
    path_bonuses = normalize_scores(path_bonuses)

    # Combine all similarities with weights
    print("Combining similarity scores...")
    combined_similarities = []
    weights = {
        'feature': 0.4,    # Highest weight for problem-to-code feature matching
        'tfidf': 0.45,     # Good for content similarity
        'path': 0.15       # Path-based bonus
    }

    for i in range(len(chunk_texts)):
        combined_score = (
            weights['feature'] * feature_similarities[i] +
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

    # Extract features from problem to guide exploration
    problem_features = _extract_problem_keywords(problem_text)

    # Generate intelligent exploration hints
    exploration_hints = []

    # Suggest searches based on extracted features
    if problem_features['domain_keywords']:
        domain_hints = ', '.join(problem_features['domain_keywords'][:3])
        exploration_hints.append(f"🎯 Domain: This appears to be a {domain_hints} related problem")

    if problem_features['file_mentions']:
        file_hints = ', '.join(problem_features['file_mentions'][:3])
        exploration_hints.append(f"📁 Files: Problem mentions these files: {file_hints}")

    if problem_features['component_names']:
        component_hints = ', '.join(problem_features['component_names'][:3])
        exploration_hints.append(f"🔧 Components: Look for these components: {component_hints}")

    if problem_features['error_terms']:
        error_hints = ', '.join(problem_features['error_terms'][:3])
        exploration_hints.append(f"❌ Errors: Problem involves: {error_hints}")

    hints_text = "\n".join(exploration_hints) if exploration_hints else "No specific hints extracted."

    # Initialize current working directory BEFORE using it
    current_working_directory = "."

    # Use structured system prompt
    command_docs = "\n".join(EXPLORATION_COMMANDS.values())
    system_prompt = EXPLORATION_SYSTEM_PROMPT.format(command_docs=command_docs)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"PROBLEM TO LOCATE:\n{problem_text}\n\nAI ANALYSIS HINTS:\n{hints_text}\n\n(Current dir: {current_working_directory}) $ Start your exploration to find where this problem exists in the codebase. Use the hints above to guide your search strategy."}
    ]

    step = 0
    exploration_history = []
    command_history = []  # Track commands to detect loops

    while step < MAX_EXPLORATION_STEPS:
        step += 1

        try:
            # Get next action from LLM using string commands
            response = inference(messages, proxy_url, run_id, model_name)
            text_response = response.get("text_response", "")

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
            finish_in_text = "FINISH(" in text_response.upper()

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
                            return {
                                "success": True,
                                "target_files": target_files,
                                "problem_location": problem_location,
                                "exploration_history": exploration_history
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

            # Add observation to conversation with current directory context
            messages.append({"role": "assistant", "content": text_response})
            messages.append({"role": "user", "content": f"OBSERVATION:\n{observation[:MAX_OBS_CHARS]}"})

            pwd_prompt = f"(Current dir: {current_working_directory}) $ "
            messages.append({"role": "user", "content": f"{pwd_prompt}Continue exploration..."})

        except Exception as e:
            return {
                "success": False,
                "error": f"Exploration failed at step {step}: {e}",
                "exploration_history": exploration_history
            }

    # If we reached max steps without FINISH
    return {
        "success": False,
        "error": f"Exploration exceeded {MAX_EXPLORATION_STEPS} steps without finding target files",
        "exploration_history": exploration_history
    }

def run_focused_oneshot(problem_text: str, target_files: List[str], *, proxy_url: str, model_name: str, run_id: str) -> str:
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

    # Create focused oneshot prompt with structured approach
    messages = [
        {"role": "system", "content": PATCH_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Problem to fix:\n{problem_text}"},
        {"role": "user", "content": f"Relevant file contents:\n\n{full_context}"}
    ]

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

def run_hybrid(problem_text: str, *, proxy_url: str, model_name: str, run_id: str) -> Dict[str, Any]:
    """
    Main hybrid approach: Exploration followed by focused oneshot.
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

    print(f"[agent] Exploration found target files: {target_files}")
    print(f"[agent] Problem location: {problem_location}")
    print("[agent] Phase 2: Focused patch generation...")

    # Phase 2: Focused oneshot
    try:
        patch_text = run_focused_oneshot(
            problem_text,
            target_files,
            proxy_url=proxy_url,
            model_name=model_name,
            run_id=run_id
        )

        print(f"[agent] Generated patch ({len(patch_text)} chars)")

        return {
            "success": True,
            "patch": patch_text,
            "target_files": target_files,
            "problem_location": problem_location,
            "exploration_history": exploration_result["exploration_history"]
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
    run_id = input_dict.get("run_id", "default")

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
