# Agent OH Changing - Test-Driven Iterative Agent

This agent follows your ideal approach: it analyzes git diff to identify test changes, uses exploration tools to understand the codebase, makes targeted changes with WRITE_FILE, and iterates until all fail-to-pass tests pass.

## How It Works

The agent follows a systematic iterative approach that mirrors how a real developer would solve test failures:

### 1. **Git Diff Analysis** 
- Runs `git diff HEAD~1 HEAD` to see exactly what test changes were made
- Identifies NEW tests (completely new test functions) vs MODIFIED tests
- Extracts test names and file locations from the diff
- Understands the context of each test change

### 2. **Test Execution & Failure Identification**
- Runs each identified test specifically to see which ones are failing
- Captures detailed error messages for each failing test
- These become the "fail-to-pass" tests that must be fixed
- Focuses only on tests that were actually changed in the patch

### 3. **Codebase Exploration** 
- Uses exploration tools to understand what needs to be fixed:
  - **SMART_SEARCH()**: Finds relevant Python files in the codebase
  - **GREP(pattern, path)**: Searches for specific patterns in code
  - **READ_FILE(path)**: Reads specific files to understand implementation
  - **FIND(pattern)**: Finds files by name pattern
  - **LS(dir)**: Lists directory contents
- Systematically explores to understand missing or broken functionality

### 4. **Analysis & Planning**
- Analyzes exploration results and test failures to create a fix plan
- Identifies specific files that need modification
- Plans what changes are needed in each file
- Creates a targeted implementation strategy

### 5. **Targeted Implementation**
- Uses **WRITE_FILE** to make specific changes to identified files
- Generates complete modified file content using AI
- Preserves existing code structure and style
- Makes minimal necessary changes to fix test failures
- Tracks all changes made for debugging

### 6. **Test Verification**
- Re-runs the fail-to-pass tests to verify they now pass
- Provides clear success/failure feedback
- Continues iteration if tests still fail

### 7. **Iteration Until Success**
- If tests still fail, goes back to exploration with new context
- Uses different exploration strategies or looks at different files
- Continues iterating up to a maximum number of attempts
- Each iteration builds on previous knowledge

### 8. **Final Patch Generation**
- Generates a clean git diff with all changes made
- Includes only the actual code modifications needed
- Produces a properly formatted patch for submission

## Key Features

### 🔄 **True Iterative Approach**
- Acts like a real developer debugging test failures
- Explores, implements, tests, and refines in a loop
- Each iteration builds on the previous one's learnings

### 🔍 **Smart Exploration Tools**  
- Uses the same powerful tools as successful agents (diddler, agent_sus)
- SMART_SEARCH for finding relevant files
- GREP for targeted code searches
- READ_FILE for understanding implementations

### 🎯 **Test-Driven Focus**
- Only works on tests that were actually changed in the git patch
- Runs specific tests rather than entire test suites
- Clear success criteria: make fail-to-pass tests pass

### 🛠️ **Real Code Changes**
- Uses WRITE_FILE to make actual file modifications
- Tracks all changes made during the process
- Generates patches based on real git diffs

### ✅ **Robust Verification**
- Re-runs tests after each change to verify progress
- Provides clear feedback on what's working and what isn't
- Stops iterating when all tests pass

## Example Execution

```
[agent] Starting test-driven iterative approach for: Fix validation logic...
[agent] Step 1: Analyzing git diff to identify test changes...
[agent] Found 3 test changes:
  - NEW: test_validation_rules in tests/test_engine.py
  - MODIFIED: test_autoescape in tests/test_engine.py
  - NEW: test_error_handling in tests/test_engine.py

[agent] Step 2: Running tests to identify current failures...
[agent] FAILING: test_validation_rules - AttributeError: Engine has no autoescape
[agent] FAILING: test_autoescape - AssertionError: Expected False, got True
[agent] Found 2 failing tests

[agent] === ITERATION 1/8 ===
[agent] Step 3: Exploring codebase...
[agent] Exploration: SMART_SEARCH() -> Found relevant Python files:
./django/template/engine.py
./django/template/base.py
...

[agent] Step 4: Analyzing and planning fixes...
[agent] Plan to modify 1 files: ['django/template/engine.py']

[agent] Step 5: Implementing fixes...
[agent] Modifying django/template/engine.py: Add autoescape parameter to Engine constructor
[agent] Wrote 2543 characters to django/template/engine.py

[agent] Step 6: Verifying tests pass...
[agent] ✅ All tests now pass!
[agent] 🎉 SUCCESS! All tests pass after 1 iterations

[agent] Step 7: Generating final patch...
[agent] Generated patch of 1247 characters
[agent] Made 1 changes across 1 iterations
```

## Why This Approach Works

### **Real Developer Workflow**
- Mirrors how humans actually debug failing tests
- Explore → Understand → Fix → Verify → Repeat
- Uses the same tools that successful agents use

### **Focused and Efficient**
- Only works on tests that were actually changed
- Doesn't waste time on unrelated code or tests
- Clear success criteria and verification

### **Tool-Powered Exploration**
- Leverages proven exploration tools from successful agents
- SMART_SEARCH finds relevant files quickly
- GREP and READ_FILE provide deep code understanding

### **Iterative Refinement**
- If first attempt doesn't work, tries different approaches
- Each iteration learns from previous failures
- Robust against complex multi-file problems

## Configuration

```bash
# Model selection (default: claude-3-5-sonnet-20241022)
export DEFAULT_MODEL="your-preferred-model"

# Maximum iterations (default: 8)
MAX_ITERATIONS=10

# Test timeout (default: 180 seconds)
export TEST_TIMEOUT_SECONDS=300
```

## Usage

### Testing Locally
```bash
# Test with the new iterative agent
./ridges.py test-agent --agent-file miner/agent_OH_changing.py --verbose

# Test on specific problem sets
./ridges.py test-agent --agent-file miner/agent_OH_changing.py --problem-set medium --num-problems 3
```

### Understanding Output

The agent provides detailed logging of each iteration:

```
[agent] === ITERATION 2/8 ===
[agent] Step 3: Exploring codebase...
[agent] Exploration: GREP("autoescape", ".") -> Found 15 matches in 3 files
[agent] Step 4: Analyzing and planning fixes...
[agent] Plan to modify 2 files: ['engine.py', 'context.py']
[agent] Step 5: Implementing fixes...
[agent] Modifying engine.py: Add autoescape support to constructor
[agent] Wrote 3245 characters to engine.py
[agent] Step 6: Verifying tests pass...
[agent] ❌ 1 tests still failing
[agent] Tests still failing, continuing to iteration 3
```

## Troubleshooting

### Common Issues

1. **No test changes found in git diff**
   - Ensure test patch was committed to git
   - Check that you're in the right repository
   - Verify test files have 'test' in their names

2. **Tests already pass**
   - The test patch might already be implemented
   - Check if this is expected behavior
   - Verify you're running the right tests

3. **Max iterations reached**
   - Increase MAX_ITERATIONS for complex problems
   - Check if the exploration is finding relevant files
   - Review the error messages for clues

### Performance Tips

1. **Model Selection**: Claude Sonnet excels at code understanding and generation
2. **Iteration Limits**: Start with 8 iterations, increase for complex problems
3. **Exploration Strategy**: Agent automatically uses SMART_SEARCH → GREP → READ_FILE

## Advanced Features

### **Change Tracking**
- Tracks every file modification made during iterations
- Provides detailed logs of what was changed and why
- Useful for debugging and understanding the agent's approach

### **Smart Test Detection**
- Handles both completely new tests and subtle modifications
- Extracts test context from git diff to understand requirements
- Focuses only on tests that were actually changed

### **Robust Error Handling**
- Continues working even if some exploration commands fail
- Falls back to alternative strategies if initial approach doesn't work
- Provides detailed error information for debugging

## Comparison to Previous Approaches

| Approach | Pros | Cons |
|----------|------|------|
| **Pure Analysis** | Fast, systematic | No actual code changes |
| **One-shot Generation** | Simple | Misses test nuances |
| **Test-Driven Iterative (This)** | Acts like real developer, uses proven tools | Takes more iterations |

## Contributing

To improve this agent:

1. **Better exploration strategies** - Add more sophisticated tool usage patterns
2. **Enhanced change detection** - Improve git diff parsing for edge cases
3. **Smarter iteration logic** - Add learning between iterations
4. **Tool integration** - Add more exploration tools as they become available

This agent represents the ideal approach you described: it identifies failing tests, uses real tools to explore and understand the codebase, makes targeted changes, and iterates until success!