# Agent OH Changing - Test Patch Reverse Engineering Agent

This agent specializes in analyzing test patches via git diff and implementing the minimal changes needed to make **fail-to-pass tests** pass. It's designed to excel at understanding what test patches expect and delivering precise solutions.

## How It Works

The agent follows a systematic 8-step approach that leverages git diff to understand exactly what the test patch changed:

### 1. **Git Diff Analysis** 
- Runs `git diff HEAD~1 HEAD` to see exactly what the test patch changed
- Parses the diff to identify new tests vs. modified tests
- Extracts affected files and test function names
- Understands both complete new tests (like `foo()`) and subtle modifications (like `bar()`)

### 2. **Fail-to-Pass Test Identification**
- Runs tests on the modified files to identify which tests are currently failing
- Focuses specifically on the fail-to-pass tests (tests that need to pass for the solution to be correct)
- Distinguishes from pass-to-pass tests (tests that should continue working)

### 3. **Test Patch Requirements Analysis**
- Uses AI to analyze the git diff content to understand what functionality is expected
- Extracts specific requirements from new test functions and test modifications
- Identifies which source files likely need modification based on test expectations

### 4. **Detailed Error Analysis**
- Runs specific failing tests to get detailed error messages
- Captures precise failure information to understand what's missing
- Focuses only on the tests that were changed in the patch

### 5. **Precise Implementation Planning** 
- Creates a targeted plan based on the test patch analysis
- Identifies minimal changes needed to satisfy test requirements
- Prioritizes changes for maximum impact on fail-to-pass tests

### 6. **Focused Code Implementation**
- Implements only the changes needed to make fail-to-pass tests pass
- Preserves existing code structure to avoid breaking pass-to-pass tests
- Uses AI to generate precise, minimal modifications

### 7. **Fail-to-Pass Verification**
- Re-runs the specific fail-to-pass tests to verify they now pass
- Provides success rate metrics (passing tests / total fail-to-pass tests)
- Validates that the solution actually addresses the test requirements

### 8. **Clean Patch Generation**
- Generates a git diff with only the necessary changes
- Produces a properly formatted patch for submission

## Key Features

### 🎯 **Git Diff-Driven Approach**
- Analyzes the actual test patch via `git diff HEAD~1 HEAD`
- Understands both new tests and subtle test modifications
- Focuses specifically on fail-to-pass test requirements

### 🔍 **Smart Test Patch Analysis**  
- Distinguishes between new test functions and modified existing tests
- Parses diff content to extract test expectations
- Identifies specific functionality that needs implementation

### 🛠️ **Fail-to-Pass Focus**
- Targets only the tests that need to pass for a successful solution
- Avoids over-engineering that might break pass-to-pass tests
- Implements minimal viable changes

### ✅ **Targeted Verification**
- Validates specific fail-to-pass tests rather than running entire test suites
- Provides clear success metrics
- Ensures changes actually solve the problem

## Understanding Test Types

This agent is designed around the SWE-bench classification:

- **Fail-to-Pass Tests**: Tests that are failing before your solution and must pass after
  - These are the critical tests - your solution is judged on making these pass
  - Can be entirely new tests or modifications to existing tests
  - Example: New function `test_foo()` or modified assertion in `test_bar()`

- **Pass-to-Pass Tests**: Tests that pass before your solution and should continue to pass
  - These should not be broken by your implementation
  - Agent focuses on minimal changes to avoid breaking these

## Git Diff Analysis Examples

### New Test (Easy to Identify):
```diff
+def test_new_feature():
+    result = my_function(input_data)
+    assert result == expected_output
```
→ Agent sees entire function, understands requirements clearly

### Modified Test (Harder to Identify):
```diff
 def test_existing_feature():
     result = my_function(input_data)
-    assert result == old_expected
+    assert result == new_expected
```
→ Agent analyzes context to understand what behavior changed

## Configuration

```bash
# Model selection (default: claude-3-5-sonnet-20241022)
export DEFAULT_MODEL="your-preferred-model"

# Test timeout (default: 180 seconds)
export TEST_TIMEOUT_SECONDS=300

# Analysis limits for efficiency
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

The agent provides detailed logging of its git diff analysis:

```
[agent] Starting test patch analysis for problem: Fix validation logic...
[agent] Step 1: Analyzing test patch via git diff...
[agent] Found test patch of 1543 characters
[agent] Found 2 new tests, 1 modified tests
[agent] Step 2: Identifying fail-to-pass tests...
[agent] Identified 3 fail-to-pass tests: ['test_foo', 'test_bar', 'test_validation']
[agent] Step 3: Analyzing requirements from test patch...
[agent] Step 4: Getting detailed error messages...
[agent] Step 5: Creating implementation plan...
[agent] Plan targets 2 files for modification
[agent] Step 6: Implementing changes...
[agent] Step 7: Verifying fail-to-pass tests...
[agent] Verification: 3/3 tests passing (100.0%)
[agent] Step 8: Generating final patch...
```

## Why This Approach Is Superior

### **Precision Through Git Diff**
- Sees exactly what the test patch changed, not just discovering all tests
- Understands the specific intent behind test modifications
- Focuses effort on the actual requirements

### **Fail-to-Pass Focus**
- Targets only the tests that determine success
- Avoids wasting time on unrelated functionality
- Minimizes risk of breaking existing functionality

### **Context Awareness**
- Understands both new tests and subtle modifications
- Uses the actual diff content to guide implementation
- Leverages test patch as a specification document

## Troubleshooting

### Common Issues

1. **No git diff found**
   - Ensure the test patch was committed to git
   - Agent tries multiple git commands to find the patch
   - Check if you're in the right git repository

2. **Can't identify fail-to-pass tests**
   - Agent falls back to extracting test names from the patch
   - Runs tests on affected files to identify failures
   - May need manual verification of test results

3. **Implementation seems incomplete**
   - Agent focuses on minimal changes only
   - Check that all fail-to-pass tests are actually identified
   - May need multiple iterations for complex changes

### Performance Tips

1. **Model Selection**: Claude Sonnet excels at understanding git diffs and test intent
2. **Git History**: Ensure clean git state for accurate diff analysis
3. **Test Isolation**: Agent works best when test patches are focused and clear

## Strategy Behind the Design

### Why Git Diff Analysis Works

1. **Direct Requirements**: The test patch IS the specification - it shows exactly what needs to work
2. **Minimal Scope**: Only implement what the changed tests require
3. **Clear Success Criteria**: Fail-to-pass tests provide unambiguous validation
4. **Context Preservation**: Understand both new and modified test intent

### Comparison to Other Approaches

| Approach | Pros | Cons |
|----------|------|------|
| **Exploration-First** | Thorough understanding | Time-consuming, may miss test intent |
| **Test Discovery** | Finds all tests | Lacks focus on specific requirements |
| **Git Diff Analysis (This)** | Precise, targeted, efficient | Requires clean git state |

## Advanced Usage

### Custom Git Commands
You can modify the git diff commands in `analyze_test_patch()`:

```python
for cmd in [
    ["git", "diff", "HEAD~1", "HEAD"],          # Standard approach
    ["git", "show", "--format=", "HEAD"],       # Alternative format
    ["git", "diff", "HEAD~2", "HEAD"],          # Deeper history
]:
```

### Enhanced Verification
For complex test suites, you might want to:
- Increase verification timeout
- Add more sophisticated test parsing
- Implement iterative refinement

## Contributing

To improve this agent:

1. **Better diff parsing** - Handle more git diff formats and edge cases
2. **Enhanced test identification** - Improve detection of modified test functions
3. **Smarter error analysis** - Extract more precise requirements from test failures
4. **Iterative refinement** - Add loops for complex implementation scenarios

## License

This agent follows the same license as the Ridges project.