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
    Remove unused dictionaries from a git patch.

    Args:
        patch (str): The git patch as a string

    Returns:
        str: The cleaned patch with unused dictionaries removed
    """
    # Extract code from patch
    code, pre_patch_lines, post_header_lines = extract_code_from_patch(patch)

    # Remove unused dictionaries
    cleaned_code = remove_unused_dicts(code)

    # Create new patch with cleaned code
    return create_patch(patch, cleaned_code, pre_patch_lines, post_header_lines)


def remove_comments(patch_content: str) -> str:
    """
    Process a Git patch string to remove comments from added lines, keeping the '+' intact.
    Preserves:
    - Comments within string literals (single, double, or triple-quoted)
    - Shebang lines
    - Docstrings
    
    :param patch_content: The content of a Git patch as a string.
    :return: The cleaned patch content as a string.
    """
    cleaned_lines = []
    
    for line in patch_content.splitlines():
        # Don't process lines that aren't additions
        if not line.startswith('+'):
            cleaned_lines.append(line)
            continue
            
        # Preserve shebang lines
        if line.startswith('+#!'):
            cleaned_lines.append(line)
            continue
            
        # Skip pure comment lines (but not docstrings)
        if line.lstrip().startswith('# '):
            continue
            
        # For lines with potential inline comments or strings
        content = line[1:]  # Remove the '+' temporarily
        result = []
        i = 0
        length = len(content)
        in_single_quote = False
        in_double_quote = False
        in_triple_single = False
        in_triple_double = False
        
        while i < length:
            char = content[i]
            
            # Handle triple quotes
            if i + 2 < length:
                triple = content[i:i+3]
                if triple == '"""' and not in_single_quote:
                    in_triple_double = not in_triple_double
                    result.append(triple)
                    i += 3
                    continue
                elif triple == "'''" and not in_double_quote:
                    in_triple_single = not in_triple_single
                    result.append(triple)
                    i += 3
                    continue
            
            # Handle escape sequences
            if char == '\\' and i + 1 < length:
                result.append(char + content[i + 1])
                i += 2
                continue
                
            # Handle string boundaries
            if char == '"' and not in_single_quote and not in_triple_single and not in_triple_double:
                in_double_quote = not in_double_quote
                result.append(char)
            elif char == "'" and not in_double_quote and not in_triple_single and not in_triple_double:
                in_single_quote = not in_single_quote
                result.append(char)
            # Handle comments - only if we're not in any type of string
            elif char == '#' and not any([in_single_quote, in_double_quote, in_triple_single, in_triple_double]):
                break
            else:
                result.append(char)
            
            i += 1
            
        # Reconstruct the line
        cleaned_line = '+' + ''.join(result).rstrip()
        if cleaned_line != '+':  # Don't add empty lines
            cleaned_lines.append(cleaned_line)
            
    return '\n'.join(cleaned_lines)


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
