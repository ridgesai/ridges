# Tool Reference

## `analyze_code_patterns`

**Description:**
```
Analyze code patterns and provide insights
Arguments:
    file_path: Path to the file to analyze
    pattern_type: Type of pattern analysis ("general", "performance", "security", "maintainability")
Output:
    Code pattern analysis with recommendations
```

**Present in:** `test3_86.py`, `yes_82.py`

## `analyze_dependencies`

**Description:**
```
Analyze dependencies of a file to understand impact of changes
Arguments:
    file_path: Path to the file to analyze
Output:
    List of dependencies and dependent files
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `analyze_git_history`

**Description:**
```
Analyze git history for a file to understand previous changes
Arguments:
    file_path: Path to the file to analyze
    commit_range: Commit range to analyze (default: last 5 commits)
Output:
    Git history analysis with commit messages and changes
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `analyze_git_operations`

**Description:**
```
Analyze a file for git-related operations and patterns
Arguments:
    file_path: Path to the file to analyze
Output:
    Analysis of git-related operations found in the file
```

**Present in:** `test3_86.py`, `yes_82.py`

## `analyze_test_coverage`

**Description (varies by file):**
- **`codex_80.py`**:
  ```
Analyze test coverage for proposed test functions
Arguments:
    test_func_names: List of test function names with file paths
Output:
    Coverage analysis report showing which code paths are tested
```
- **`cyan_76.py`**:
  ```
Analyze test coverage for proposed test functions
Arguments:
    test_func_names: List of test function names with file paths
Output:
    Coverage analysis report showing which code paths are tested
```
- **`test3_86.py`**:
  ```
Analyze test coverage for proposed test functions using manual AST analysis
Arguments:
    test_func_names: List of test function names with file paths
Output:
    Coverage analysis report showing which code paths are tested
```
- **`yes_82.py`**:
  ```
Analyze test coverage for proposed test functions
Arguments:
    test_func_names: List of test function names with file paths
Output:
    Coverage analysis report showing which code paths are tested
```
**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `apply_code_edit`

**Description:**
```
Performs targeted text replacement within source files. If there are any syntax errors in the code, it rejects the edit with an error message. Please note use you can only use this tool after you have approval from user on your proposed solution.
Arguments:
file_path: target file for modification
search: exact text pattern to locate and replace
replace: new text content to substitute
    
Output:
    operation status - success confirmation or detailed error with guidance
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `check_git_workflow_issues`

**Description:**
```
Check for common git workflow issues in the codebase
Arguments:
    None
Output:
    Analysis of potential git workflow issues and recommendations
```

**Present in:** `test3_86.py`, `yes_82.py`

## `clear_cache`

**Description:**
```
Clear cached data to free up memory
Arguments:
    cache_type: Type of cache to clear ("all", "tool_cache", "performance_cache", "smart_cache")
Output:
    Confirmation message with cache clearing results
```

**Present in:** `test3_86.py`, `yes_82.py`

## `compare_solutions`

**Description:**
```
Compare two proposed solutions for pros/cons
Arguments:
    solution1: First solution to compare
    solution2: Second solution to compare
Output:
    Comparison analysis of the two solutions
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `compile_repo`

**Description:**
```
Byte-compile all Python files to catch syntax errors quickly.
Arguments:
    None
Output:
    "OK" on success or error details on failure.
```

**Present in:** `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `create_new_file`

**Description:**
```
Generates new file with specified content at target location. Do not use this tool to create test or files to reproduce the error unless user has specifically asked you to create test files as part of problem statement.
Arguments:
    file_path: destination path for new file
    content: text content for file creation
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `detect_code_smells`

**Description:**
```
Detect code smells and anti-patterns in a file
Arguments:
    file_path: Path to the file to analyze
Output:
    List of code smells with line numbers and suggestions
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `enhanced_problem_analysis`

**Description:**
```
Enhanced problem analysis combining self-consistency and intelligent search
Arguments:
    problem_statement: The problem to analyze comprehensively
Output:
    Combined analysis with consensus and search results for maximum accuracy
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `execute_intelligent_search`

**Description:**
```
Execute intelligent search algorithm for +15% accuracy improvement
Arguments:
    problem_statement: The problem to search for with multiple strategies
    fusion_method: Search result fusion method (weighted, consensus, simple)
Output:
    Comprehensive search results with fused findings and recommendations
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `execute_self_consistency_analysis`

**Description:**
```
Execute self-consistency algorithm for +25% accuracy improvement
Arguments:
    problem_statement: The problem to analyze with multiple reasoning paths
    context: Optional context information for the problem
Output:
    Consensus analysis with recommended approach and confidence scores
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `filter_test_func_names`

**Description:**
```
Filter the list of test functions to keep the test functions that is specifically designed to test the scenario mentioned in the problem statement.
Arguments:
    reason_for_filtering: The reason for filtering the list of test function names.
    filtered_test_func_names: The filtered list of test function names with file path (e.g. ["test_file_path.py - test_func_name", "test_file_path.py - test_func_name"])
Output:
    Confirmation that test functions were filtered
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `finish`

**Description:**
```
Signals completion of the current workflow execution
Arguments:
    run_repo_tests_passed: Whether the tests passed or not.
    run_repo_test_depdency_error: Whether the tests failed due to missing dependencies or not.
    investigation_summary: Please provide a detailed summary of the findings from your investigation and detailed solution to the problem.Use the following format:
        Problem: <problem_statement>
        Investigation: <investigation_summary>
        Solution: <your solution>
Output:
    Confirmation that the workflow is finished
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `get_approval_for_solution`

**Description:**
```
This tool is used to get approval for your proposed solution. You need to propose at least 2 meaningfully different and elegant solutions to the problem.
While all the solutions proposed need to be accurate, the following are guidelines for selecting the best solution:
1. Expected output should be closest to the most relevant test case.
Arguments:
    solutions: list of solutions proposed by you. Each solution should be very detailed and explain why it is better than the other solutions.
    selected_solution: Index of the solution you think is the best.
    reason_for_selection: Reason for selecting the solution over other solutions.
    
Output:
    approval: approved/not approved. If approved, you can go ahead and implement the solution.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `get_cache_stats`

**Description:**
```
Get detailed cache statistics and usage information
Arguments:
    None
Output:
    Comprehensive cache statistics including hit rates and memory usage
```

**Present in:** `test3_86.py`, `yes_82.py`

## `get_code_quality_metrics`

**Description:**
```
Calculate code quality metrics for a file
Arguments:
    file_path: Path to the file to analyze
Output:
    Code quality metrics including cyclomatic complexity, maintainability index, etc.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `get_enhanced_test_coverage`

**Description:**
```
Get enhanced test coverage analysis with additional metrics
Arguments:
    test_func_names: List of test function names with file paths
Output:
    Enhanced coverage report with quality metrics, risk assessment, and improvement suggestions
```

**Present in:** `test3_86.py`

## `get_file_content`

**Description:**
```
Retrieves file contents with optional filtering based on search term and line numbers
Arguments:
    file_path: filesystem path to target file. This file must be python file.
    search_start_line: optional start line number to begin extraction (1-indexed)
    search_end_line: optional end line number to end extraction (1-indexed)
    search_term: optional text pattern to filter matching lines
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `get_function_body`

**Description:**
```
Extract the body/source code of a specific function from a file.
Args:
    file_path: Path to the Python file
    function_name: Name of the function to extract
Returns:
    The full source code of the function
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `get_git_branches`

**Description:**
```
Get all git branches in the repository
Arguments:
    None
Output:
    List of all branches with current branch marked
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `get_git_diff`

**Description:**
```
Get git diff for staged/unstaged changes
Arguments:
    file_path: Optional specific file to get diff for
Output:
    Git diff showing changes in the repository
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `get_git_log`

**Description:**
```
Get recent git commit history
Arguments:
    num_commits: Number of recent commits to show (default: 10)
Output:
    Recent commit history with commit hashes, authors, dates, and messages
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `get_git_status`

**Description:**
```
Get the current git status of the repository
Arguments:
    None
Output:
    Current git status including branch, staged/unstaged changes, and untracked files
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `get_performance_metrics`

**Description:**
```
Get performance metrics from parallel operations
Arguments:
    None
Output:
    Performance summary and metrics
```

**Present in:** `test3_86.py`, `yes_82.py`

## `get_smart_performance_analysis`

**Description:**
```
Get intelligent performance analysis with recommendations
Arguments:
    None
Output:
    Detailed performance analysis with optimization suggestions
```

**Present in:** `test3_86.py`, `yes_82.py`

## `get_system_health`

**Description:**
```
Get comprehensive system health status
Arguments:
    None
Output:
    System health report including resource usage and performance metrics
```

**Present in:** `test3_86.py`, `yes_82.py`

## `grep_replace_once`

**Description:**
```
Regex-based single replacement with safety checks.
Arguments:
    file_path: file to edit (py or text).
    pattern: regex to find (must match exactly one region).
    replacement: replacement text (supports backrefs).
    flags: optional re flags: "I" (IGNORECASE), "M" (MULTILINE), "S" (DOTALL).
Output:
    "ok" or a descriptive error message.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `list_python_files`

**Description:**
```
List Python files in the repo (tracked and untracked).
Arguments:
    None
Output:
    Newline-separated list of paths.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `llm_complete`

**Description:**
```
Call the underlying LLM to reason or draft content. Does NOT browse the web.
Arguments:
    prompt: user-facing instruction or content to transform.
    system: optional system primer to steer style/role.
    temperature: decoding temperature (0.0–1.0 typical).
    max_tokens: response length hint (best-effort).
Output:
    Raw model text response.
```

**Present in:** `test3_86.py`, `yes_82.py`

## `parallel_codebase_analysis`

**Description:**
```
Perform comprehensive codebase analysis using parallel execution
Arguments:
    file_paths: List of files to analyze
    search_terms: List of terms to search for
Output:
    Comprehensive analysis results from parallel execution
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `parallel_file_operations`

**Description:**
```
Perform multiple file operations in parallel
Arguments:
    file_paths: List of files to operate on
    operations: List of operations to perform (read, analyze, search)
Output:
    Results of parallel file operations
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `parallel_test_discovery`

**Description:**
```
Discover test functions using parallel search strategies
Arguments:
    problem_statement: The problem to find tests for
Output:
    List of relevant test functions found through parallel search
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `propose_solutions`

**Description:**
```
Propose multiple solutions to a problem with analysis
Arguments:
    problem_statement: The problem to solve
    context: Optional context information
Output:
    Multiple proposed solutions with analysis
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `run_code`

**Description:**
```
Runs any python code. You can use this tool directly to run any test code or bug reproduction code.
Saves the code at the given file_path and then runs it. Do not use this tool to create test or files to reproduce the error unless user has specifically asked you to create test files as part of problem statement.

Arguments:
    content: text code to write in file
    file_path: path of the file to save the code in. This file should always be in the current working directory.

Output:
    Returns the stdout/stderr from the executed file.
    Returns error message if there are any third party dependencies.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `run_repo_tests`

**Description:**
```
Run repository tests to validate edits.
Arguments:
    timeout_secs: cap execution time.
Output:
    Combined stdout/stderr (last 200 lines if long).
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `search_git_related_code`

**Description:**
```
Search for git-related code patterns in the codebase
Arguments:
    search_terms: List of git-related terms to search for (e.g., ["git", "commit", "merge", "branch"])
Output:
    Locations where git-related code patterns were found
```

**Present in:** `test3_86.py`, `yes_82.py`

## `search_in_all_files_content`

**Description:**
```
Performs text pattern matching across all files in the codebase
Arguments:
    search_term: text pattern to locate (e.g., "def test_function", "*SomeClass*")
Output:
    locations where pattern was found with file paths and line numbers
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `search_in_specified_file_v2`

**Description:**
```
Locates text patterns within a specific file
Arguments:
    file_path: target file for pattern matching. This file must be python file.
    search_term: text pattern to find (e.g., "def test_function", "*SomeClass*")
Output:
    matching locations with line numbers, or error description
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `search_recurive_in_all_files_in_directory`

**Description:**
```
Locates text patterns recursively within all files in a specific directory
Arguments:
    directory_path: target directory for pattern matching
    search_term: text pattern to find (e.g., "def test_function", "*SomeClass*")
Output:
    matching locations with line numbers, or error description
```

**Present in:** `codex_80.py`, `test3_86.py`, `yes_82.py`

## `search_recursive_in_all_files_in_directory`

**Description:**
```
Locates text patterns recursively within all files in a specific directory
Arguments:
    directory_path: target directory for pattern matching
    search_term: text pattern to find (e.g., "def test_function", "*SomeClass*")
Output:
    matching locations with line numbers, or error description
```

**Present in:** `cyan_76.py`

## `sort_test_func_names`

**Description:**
```
Sorts the list of test function names by their relevance to the issue mentioned in the problem statement in descending order.
Arguments:
    reason_for_sorting: The reason for sorting the test function names.
    sorted_test_func_names: The sorted list of test function names with file path (e.g. ["test_file_path.py - test_func_name", "test_file_path.py - test_func_name"])
Output:
    Confirmation that test function names were sorted
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `start_over`

**Description:**
```
This will revert any changes made to the codebase and let's you start over. Only use this tool when you have concluded that current changes you made to the codebase are not relevant and you want to start again with new approach.
Arguments:
    problem_with_old_approach: What you tried and what was the key issues you faced with this approach.
    new_apprach_to_try: What is the new approach you want to try and how it will fix the issues you faced earlier.
Output:
    Confirmation that the codebase has been reverted
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `structured_llm`

**Description:**
```
Ask LLM to return strictly valid JSON and parse it.
Arguments:
    instruction: what structure you want (e.g., {"files":[], "edits":[]}).
    schema_hint: optional schema/example JSON to nudge formatting.
Output:
    A valid JSON string if parsing succeeds; otherwise an error string.
```

**Present in:** `test3_86.py`, `yes_82.py`

## `test_git_operation`

**Description:**
```
Test a specific git operation to verify it works correctly
Arguments:
    git_command: The git command to test (e.g., "git status", "git log --oneline")
    expected_output: Optional expected output pattern to verify
Output:
    Result of the git operation and whether it matches expectations
```

**Present in:** `test3_86.py`, `yes_82.py`

## `test_patch_find_finish`

**Description:**
```
Signals completion of the test patch find workflow execution
Arguments:
    test_func_names: The list of test function names with file path (e.g. ["test_file_path.py - test_func_name", "test_file_path.py - test_func_name"])
    **REMEMBER:** each name format should be "test_file_path.py - test_func_name". DON'T add any other texts like comments and line numbers.
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

## `validate_git_solution`

**Description:**
```
Validate that a git-related fix is working correctly
Arguments:
    file_path: Path to the file containing the git operation fix
    git_operation: Description of the git operation being tested
Output:
    Validation results and recommendations for the git solution
```

**Present in:** `test3_86.py`, `yes_82.py`

## `validate_solution`

**Description:**
```
Validate a proposed solution against all test functions
Arguments:
    file_path: Path to the file with the proposed solution
    test_func_names: List of test functions to validate against
Output:
    Validation results showing which tests pass/fail
```

**Present in:** `codex_80.py`, `cyan_76.py`, `test3_86.py`, `yes_82.py`

