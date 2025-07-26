# Agent OH Changing - Test Patch Reverse Engineering Agent

This agent specializes in reverse engineering test patches and implementing the minimal changes needed to make tests pass. It's designed to excel at understanding what test patches expect and delivering precise solutions.

## How It Works

The agent follows a systematic 7-step approach:

### 1. **Test Discovery** 
- Automatically discovers all test files in the repository
- Uses common patterns like `test*.py`, `*test*.py`, `tests/` directories
- Handles various test file structures and naming conventions

### 2. **Test Execution & Failure Analysis**
- Runs tests using pytest, unittest, or direct execution
- Captures error messages and failure patterns
- Identifies specific test failures and their root causes

### 3. **Test Content Analysis**
- Uses AI to analyze test file content
- Extracts what functions/methods are being tested
- Understands expected behavior from test assertions
- Identifies which files likely need modification

### 4. **Implementation Planning** 
- Creates a precise plan based on test failures
- Determines minimal changes needed
- Identifies target files and specific functions to implement
- Prioritizes changes for maximum impact

### 5. **Code Implementation**
- Implements only the minimal changes needed to pass tests
- Preserves existing code structure
- Uses AI to generate precise, focused modifications
- Limits scope to avoid over-engineering

### 6. **Solution Verification**
- Re-runs tests to verify the solution works
- Checks for improvement in test results
- Validates that changes actually fix the issues

### 7. **Patch Generation**
- Generates a clean git diff with all changes
- Produces a properly formatted patch for submission

## Key Features

### 🎯 **Test-Driven Approach**
- Focuses entirely on what tests expect
- Reverse engineers requirements from test cases
- Implements minimal viable solutions

### 🔍 **Smart Test Analysis**  
- Handles multiple test frameworks (pytest, unittest)
- Parses error messages intelligently
- Understands test assertion patterns

### 🛠️ **Precise Implementation**
- Makes only necessary changes
- Preserves existing code architecture
- Uses AI for accurate code generation

### ✅ **Built-in Verification**
- Validates solutions by re-running tests
- Ensures changes actually work
- Provides feedback on solution quality

## Configuration

The agent can be configured via environment variables:

```bash
# Model selection (default: claude-3-5-sonnet-20241022)
export DEFAULT_MODEL="your-preferred-model"

# Test timeout (default: 180 seconds)
export TEST_TIMEOUT_SECONDS=300

# Maximum files to analyze (built-in limits for efficiency)
MAX_EXPLORATION_STEPS=20
MAX_TEST_ANALYSIS_STEPS=15
```

## Usage

### Testing Locally
```bash
# Test with the new agent
./ridges.py test-agent --agent-file miner/agent_OH_changing.py --verbose

# Test on specific problem sets
./ridges.py test-agent --agent-file miner/agent_OH_changing.py --problem-set medium --num-problems 3
```

### Understanding Output

The agent provides detailed logging:

```
[agent] Starting test-focused analysis for problem: Fix validation logic...
[agent] Step 1: Discovering test files...
[agent] Step 2: Running tests to identify failures...
[agent] Found 3 failing tests
[agent] Step 3: Analyzing test content...
[agent] Step 4: Creating implementation plan...
[agent] Plan targets 2 files for modification
[agent] Step 5: Implementing changes...
[agent] Step 6: Verifying solution...
[agent] Solution verification: PASSED
[agent] Step 7: Generating final patch...
```

## Troubleshooting

### Common Issues

1. **No test files found**
   - Check if tests are in non-standard locations
   - Agent looks for `test*.py`, `tests/`, etc.
   - Manual test discovery patterns can be extended

2. **Test execution fails**
   - Ensure dependencies are installed
   - Check Python path and module imports
   - Agent tries multiple test runners automatically

3. **Implementation seems incomplete**
   - Agent focuses on minimal changes only
   - May need multiple iterations for complex issues
   - Designed to pass tests, not create perfect code

### Performance Tips

1. **Model Selection**: Claude Sonnet works best for code analysis
2. **Timeout Adjustment**: Increase for complex test suites
3. **Verbose Mode**: Use `--verbose` to see detailed execution logs

## Strategy Behind the Design

### Why This Approach Works

1. **Test patches reveal intent**: By analyzing what tests expect, we can understand the exact requirements
2. **Minimal changes reduce risk**: Small, focused changes are less likely to break existing functionality  
3. **Iterative verification**: Testing after each change ensures we're on the right track
4. **AI-powered analysis**: LLMs excel at understanding test patterns and generating precise code

### Comparison to Other Approaches

| Approach | Pros | Cons |
|----------|------|------|
| **Exploration-First** | Thorough understanding | Time-consuming, may over-engineer |
| **Oneshot Generation** | Fast | May miss test nuances |
| **Test-Focused (This)** | Precise, efficient | Requires good test coverage |

## Advanced Usage

### Custom Test Patterns
You can modify the `discover_test_files()` function to handle custom test patterns:

```python
test_patterns = [
    "test*.py",
    "*test*.py", 
    "tests/*.py",
    "your_custom_pattern/*.py"  # Add custom patterns
]
```

### Extended Analysis
For complex repositories, you might want to:
- Increase the number of test files analyzed
- Extend the LLM context window
- Add more sophisticated error pattern matching

### Integration with Existing Workflows
The agent is designed to work with:
- Standard git workflows
- CI/CD pipelines
- Multiple test frameworks
- Various Python project structures

## Contributing

To improve this agent:

1. **Enhance test discovery** - Add support for more test patterns
2. **Improve error parsing** - Better extraction of requirements from errors  
3. **Extend verification** - More sophisticated solution validation
4. **Add test framework support** - Support for additional testing tools

## License

This agent follows the same license as the Ridges project.