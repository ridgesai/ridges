import ast
import re
from typing import Set, Dict, Tuple, List


def extract_code_from_patch(patch: str) -> Tuple[str, List[str], List[str]]:
    """Extract the actual code from a git patch, removing the patch metadata."""
    lines = patch.split("\n")
    code_lines = []
    pre_patch_lines = []
    post_header_lines = []
    in_patch = False
    after_header = False

    for line in lines:
        if line.startswith("+++"):
            in_patch = True
            continue
        if not in_patch:
            pre_patch_lines.append(line)
        elif line.startswith("@@"):
            after_header = True
            continue
        elif after_header and not line.startswith("+"):
            post_header_lines.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            code_lines.append(line[1:])

    return "\n".join(code_lines), pre_patch_lines, post_header_lines


def find_dict_usages(tree: ast.AST) -> Tuple[Set[str], Dict[str, ast.Assign]]:
    """Find all dictionary usages and assignments in the AST."""
    dict_assignments = {}
    dict_usages = set()

    class DictVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name):
            if isinstance(node.ctx, ast.Load):
                dict_usages.add(node.id)
            return self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Dict):
                        dict_assignments[target.id] = node
            return self.generic_visit(node)

    visitor = DictVisitor()
    visitor.visit(tree)
    return dict_usages, dict_assignments


def remove_unused_dicts(code: str) -> str:
    """Remove unused dictionary assignments from the code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # Return original code if parsing fails

    dict_usages, dict_assignments = find_dict_usages(tree)

    # Find unused dictionaries
    unused_dicts = set(dict_assignments.keys()) - dict_usages

    if not unused_dicts:
        return code

    # Create new AST without unused dict assignments
    class DictRemover(ast.NodeTransformer):
        def visit_Assign(self, node: ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in unused_dicts:
                    return None
            return node

    new_tree = DictRemover().visit(tree)
    return ast.unparse(new_tree)


def remove_unused_const_assignments(code: str) -> str:
    """Remove assignments of constant literals to names that are never used.

    We purposefully restrict removal to cases where the *value* is a
    constant (str, int, float, bool, None).  This avoids accidentally
    deleting helper variables that hold callables or computed values.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # Leave untouched if parsing fails.

    # Pass 1: collect usages (ast.Load) of names.
    used: set[str] = set()

    class UsageVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            self.generic_visit(node)

    UsageVisitor().visit(tree)

    # Pass 2: remove Assign nodes whose targets are *all* unused and whose
    # value is a Constant.
    class ConstAssignRemover(ast.NodeTransformer):
        def visit_Assign(self, node: ast.Assign):
            # If any target is not a Name (e.g., tuple unpacking) bail.
            target_ids = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if len(target_ids) != len(node.targets):
                return node

            if all(t not in used for t in target_ids) and isinstance(node.value, ast.Constant):
                return None  # drop the node
            return node

    new_tree = ConstAssignRemover().visit(tree)
    return ast.unparse(new_tree)


def create_patch(
    original_patch: str,
    cleaned_code: str,
    pre_patch_lines: List[str],
    post_header_lines: List[str],
) -> str:
    """Create a new patch file with the cleaned code."""
    # Get the file paths and hunk header from the original patch
    lines = original_patch.split("\n")
    old_path = None
    new_path = None
    hunk_header = None

    for line in lines:
        if line.startswith("---"):
            old_path = line[4:]
        elif line.startswith("+++"):
            new_path = line[4:]
        elif line.startswith("@@"):
            hunk_header = line
            break

    if not old_path or not new_path:
        return cleaned_code

    # Create new patch content
    patch_lines = []

    # Add pre-patch lines (excluding duplicates and empty lines)
    seen_paths = set()
    for line in pre_patch_lines:
        if line.startswith("---") or line.startswith("+++"):
            if line not in seen_paths:
                patch_lines.append(line)
                seen_paths.add(line)
        elif line.strip():  # Only add non-empty lines
            patch_lines.append(line)

    # Add file paths if not already present
    if f"--- {old_path}" not in seen_paths:
        patch_lines.append(f"--- {old_path}")
    if f"+++ {new_path}" not in seen_paths:
        patch_lines.append(f"+++ {new_path}")

    # Update hunk header with new line count
    if hunk_header:
        # Parse the hunk header
        # Format: @@ -start,count +start,count @@
        parts = hunk_header.split(" ")
        if len(parts) >= 3:
            old_info = parts[1]  # -start,count
            new_info = parts[2]  # +start,count

            # Extract numbers without signs
            old_start = old_info.split(",")[0].lstrip("-")
            old_count = old_info.split(",")[1]

            # Calculate new count
            new_count = len(cleaned_code.split("\n"))

            # Create new hunk header
            new_hunk_header = f"@@ -{old_start},{old_count} +{old_start},{new_count} @@"
            patch_lines.append(new_hunk_header)

    # Add post-header lines (excluding empty lines)
    for line in post_header_lines:
        if line.strip():  # Only add non-empty lines
            patch_lines.append(line)

    # Add the cleaned code with + prefix
    for line in cleaned_code.split("\n"):
        patch_lines.append("+" + line)

    return "\n".join(patch_lines)


def remove_unused(patch: str) -> str:
    """
    Remove unused variables from a git patch.

    Args:
        patch (str): The git patch as a string

    Returns:
        str: The cleaned patch with unused variables removed
    """
    # Extract code from patch
    code, pre_patch_lines, post_header_lines = extract_code_from_patch(patch)

    cleaned_code = remove_unused_dicts(code)
    cleaned_code = remove_unused_const_assignments(cleaned_code)

    # Create new patch with cleaned code
    return create_patch(patch, cleaned_code, pre_patch_lines, post_header_lines)


def _strip_inline_comment(code: str) -> str:
    """Return *code* with any trailing ``# …`` removed **unless** the hash sign
    occurs inside a string literal.

    This is a simple heuristic (handles single- and double-quoted strings with
    backslash escapes).  It is good enough to preserve colour strings like
    "#3341A" while still stripping genuine inline comments.
    """

    in_single = False  # inside '…'
    in_double = False  # inside "…"
    escaped = False

    for idx, ch in enumerate(code):
        if escaped:
            escaped = False
            continue

        if ch == "\\":
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            continue

        # comment starts when we hit unquoted '#'
        if ch == "#" and not in_single and not in_double:
            return code[:idx].rstrip()

    return code.rstrip()


def remove_comments(patch_content: str) -> str:
    """Remove comments from *added* lines of a patch while preserving ``+``.

    • Entire added lines that *start* with ``#`` (after the ``+`` and optional
      whitespace) are dropped.
    • Inline ``#`` comments are removed **only** if the hash is not inside a
      quoted string literal.
    """

    full_line_comment = re.compile(r"^\+\s*#")

    cleaned: list[str] = []
    for line in patch_content.splitlines():
        if not line.startswith("+"):
            cleaned.append(line)
            continue

        # Strip the leading '+' and keep the rest for analysis
        body = line[1:]

        if full_line_comment.match(line):
            # Skip whole-line comment entirely
            continue

        processed_body = _strip_inline_comment(body)
        cleaned.append("+" + processed_body)

    return "\n".join(cleaned)


def remove_docstrings(patch_content: str) -> str:
    """
    Process a Git patch string to remove added lines that introduce or modify docstrings,
    while keeping the '+' intact for other additions.

    :param patch_content: The content of a Git patch as a string.
    :return: The cleaned patch content as a string.
    """
    cleaned_lines = []
    in_docstring = False
    docstring_delim = None

    for line in patch_content.splitlines():
        if line.startswith("+"):  # Only process added lines
            stripped_line = line[1:].lstrip()  # Remove '+' for checking

            # If we are inside a docstring, check for closing delimiter
            if in_docstring and docstring_delim is not None:
                if docstring_delim in stripped_line:  # Closing delimiter found
                    in_docstring = False
                continue  # Skip all lines inside the docstring

            # Detect docstring start (including when a patch adds text to an existing docstring)
            if stripped_line.startswith(('"""', "'''")):
                docstring_delim = stripped_line[:3]  # Capture delimiter type
                if stripped_line.count(docstring_delim) >= 2:
                    continue  # Single-line docstring, skip this line
                in_docstring = True
                continue  # Start of multiline docstring, skip line

            cleaned_lines.append(line)  # Keep non-docstring lines
        else:
            # If the line is not an addition (`+`), keep it unchanged
            if line.lstrip().startswith(('"""', "'''")) and not in_docstring:
                in_docstring = True
                docstring_delim = line.lstrip()[:3]  # Track delimiter
            elif docstring_delim and docstring_delim in line:
                in_docstring = False  # Close docstring block

            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def remove_print_statements(patch_content: str) -> str:
    """Remove *added* print statement lines (e.g. ``+print(...)``) from a Git
    patch.

    The goal is to discard stray debug prints without rejecting the submission.
    We purposefully perform a shallow textual check rather than AST parsing for
    speed and to keep line-oriented diff structure intact.
    
    Parameters
    ----------
    patch_content : str
        The unified diff/patch text returned by the miner.

    Returns
    -------
    str
        The patch with any added print statement lines removed.
    """

    # Regex matches a *print* call after optional whitespace, e.g. "print(" or
    # "print (".
    print_line_regex = re.compile(r"^\s*print\s*\(")

    cleaned_lines: list[str] = []
    for line in patch_content.splitlines():
        if line.startswith("+"):  # Only consider newly-added lines
            if print_line_regex.match(line[1:]):
                # Skip this line entirely – do not include it in the output
                continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def has_unused_variables(patch: str) -> bool:
    """Return True if the git *patch* contains unused dicts or constants."""

    code, _, _ = extract_code_from_patch(patch)

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # If it doesn't parse we treat it as having problems but let
        # the syntax gate handle the reject; here we return False so
        # the caller relies on the syntax check instead.
        return False

    # --- unused dicts -------------------------------------------------
    dict_usages, dict_assignments = find_dict_usages(tree)
    if set(dict_assignments.keys()) - dict_usages:
        return True

    # --- unused constant assignments ---------------------------------
    used: set[str] = set()

    class UsageVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            self.generic_visit(node)

    UsageVisitor().visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target_ids = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if len(target_ids) != len(node.targets):
                continue
            if all(t not in used for t in target_ids) and isinstance(node.value, ast.Constant):
                return True

    return False


def has_unused_dicts(patch: str) -> bool:
    """Return True iff the *patch* introduces dictionary literals that are never used.

    This is a lighter-weight variant of :func:`has_unused_variables` that focuses
    solely on dictionary assignments.  It is useful for graders that wish to
    reject submissions containing such dead code rather than silently cleaning
    it up.
    """

    code, _, _ = extract_code_from_patch(patch)

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return True

    dict_usages, dict_assignments = find_dict_usages(tree)
    return bool(set(dict_assignments.keys()) - dict_usages)

def drop_header_noise(patch: str) -> str:
    """Remove free-form text inserted into the diff header section.

    Any line located after the `+++ path` marker but *before* the first
    `@@` hunk header that is not blank and does not begin with a comment
    character is stripped.  This is where most prompt-injection payloads
    are placed because the diff still parses.
    """

    cleaned_lines: list[str] = []
    in_header = False
    in_hunk = False

    for line in patch.splitlines():
        # -- global checks -----------------------------------------------------
        # malformed `index` lines
        if line.startswith("index ") and not re.match(r"^index [0-9a-fA-F]{7,40}\.{2}[0-9a-fA-F]{7,40}( \d{6})?$", line):
            # skip bogus index line (often prompt-injection)
            continue

        # malformed `similarity index` lines
        if line.startswith("similarity index ") and not re.match(r"^similarity index \d+%$", line):
            continue

        if line.startswith("+++ "):
            in_header = True
            cleaned_lines.append(line)
            continue

        if line.startswith("@@"):
            # entering a hunk; reset header flag, enable hunk flag
            in_header = False
            in_hunk = True
            cleaned_lines.append(line)  # keep the hunk header itself
            continue

        if in_header:
            # Only *one* line is legitimate here: the canonical `index` line.
            # Everything else (including comments) is stripped to eliminate
            # prompt-injection vectors.

            if line.startswith("index "):
                if not re.match(r"^index [0-9a-fA-F]{7,40}\.{2}[0-9a-fA-F]{7,40}( \d{6})?$", line):
                    # malformed index line → drop
                    continue
                # keep good index line
                cleaned_lines.append(line)
            # skip all other header lines (comment, blank, etc.)
            # Do *not* append to cleaned_lines – effectively dropping them.
            continue

        # Inside a hunk, drop any line that has *no* diff prefix (!= + or -)
        if in_hunk and line and not line.startswith(('+', '-', ' ')):
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)

def remove_logging_calls(patch_content: str) -> str:
    """Strip *added* calls to the top-level :pymod:`logging` functions.

    We match statements such as::

        +logging.info("help")
        +logging.warning(

    Only lines beginning with ``+`` are considered so we do not tamper with
    existing project code; the goal is to block prompt-injection payloads that
    hide in new ``logging`` calls.
    """

    log_regex = re.compile(r"^\s*logging\.[a-zA-Z_]+\(")

    cleaned: list[str] = []
    for line in patch_content.splitlines():
        if line.startswith("+") and log_regex.match(line[1:]):
            continue  # drop the injected logging line
        cleaned.append(line)

    return "\n".join(cleaned)

def strip_non_diff_preamble(patch: str) -> str:
    """Return *patch* starting from the first ``diff --git`` line.

    Any arbitrary text (including prompt-injection metadata) that precedes the
    real diff is discarded so it never reaches the LLM or `git apply`.
    """
    lines = patch.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("diff --git"):
            return "\n".join(lines[i:])
    # no diff marker – return as-is
    return patch