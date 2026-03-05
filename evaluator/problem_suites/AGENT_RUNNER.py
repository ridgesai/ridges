"""Agent Runner - Executes agent code inside the sandbox environment.

This script runs inside the Docker sandbox and is responsible for:
- Loading and executing agent-provided code
- Capturing exceptions and formatting results
- Writing structured output to output.json

Execution Flow:
1. Reads input data from /sandbox/input.json
2. Dynamically imports agent code from /sandbox/agent.py
3. Invokes agent_main() function with the input data
4. Captures return value or exceptions
5. Writes results to /sandbox/output.json

Expected Agent Interface:
    def agent_main(input_data: dict) -> str:
        # Agent entry point. Must return a string (e.g., patch).
        pass

Environment:
    - Runs inside the isolated Docker sandbox
    - Has access to SANDBOX_PROXY_URL for inference requests
    - Can access /sandbox/repo for problem files
    - Cannot access the internet directly (must use proxy)

Output Format:
    Success: {"success": true, "output": "<agent result>"}
    Failure: {"success": false, "error": "<message>", "traceback": "<trace>"}
"""

import sys
import json
import time
import traceback
import importlib.util


def main():
    """Main entry point for agent execution inside the sandbox."""
    print("[AGENT_RUNNER] Entered main()")

    # Brief delay to ensure container is fully initialized
    time.sleep(3)

    try:
        # Read problem statement and configuration from input.json
        # This is provided by the ProblemSuite that initialized the sandbox
        print("[AGENT_RUNNER] Reading input.json")
        with open("/sandbox/input.json", "r") as f:
            input_data = json.load(f)
        print("[AGENT_RUNNER] Read input.json")
        
        # Dynamically import the agent code from /sandbox/agent.py
        # This file is created by the ProblemSuite when initializing the sandbox
        print("[AGENT_RUNNER] Loading /sandbox/agent.py")
        spec = importlib.util.spec_from_file_location("agent", "/sandbox/agent.py")
        agent_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_module)
        print("[AGENT_RUNNER] Loaded /sandbox/agent.py")
        
        # Verify that the agent module defines the required agent_main() function
        # This is the standardized entry point that all agents must implement
        if hasattr(agent_module, "agent_main"):
            print("[AGENT_RUNNER] agent_main() function found in /sandbox/agent.py")
        else:
            print("[AGENT_RUNNER] agent_main() function not found in /sandbox/agent.py")
            raise Exception("agent_main() function not found in /sandbox/agent.py")
        
        # Execute the agent's main function with the problem input
        # input_data typically contains "problem_statement" and other context
        print("[AGENT_RUNNER] Entering agent's agent_main()")
        agent_main_return_value = agent_module.agent_main(input_data)
        print("[AGENT_RUNNER] Exited agent's agent_main()")

        # Validate return type - agents must return a string (typically a patch/diff)
        if not isinstance(agent_main_return_value, str):
            raise Exception("agent_main() function returned a non-string value")

        # Success: format the result for output.json
        output = {
            "success": True,
            "output": agent_main_return_value
        }

        # Write successful result to output.json for the SandboxManager to collect
        print("[AGENT_RUNNER] Writing output.json")
        with open("/sandbox/output.json", "w") as f:
            json.dump(output, f, indent=2)
        print("[AGENT_RUNNER] Wrote output.json")
        
    except Exception as e:
        # Capture any exception during agent execution
        # This includes agent code errors, import failures, timeouts, etc.
        print("[AGENT_RUNNER] Exception:")
        traceback.print_exc(file=sys.stdout)
        
        # Format the error for structured output
        output = {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        
        # Attempt to write error output even if we're in an exception handler
        # This ensures the SandboxManager can see what went wrong
        try:
            print("[AGENT_RUNNER] Writing output.json")
            with open("/sandbox/output.json", "w") as f:
                json.dump(output, f, indent=2)
            print("[AGENT_RUNNER] Wrote output.json")
        except:
            # If we can't even write output.json, log the failure
            print("[AGENT_RUNNER] Failed to write output.json")
            pass

    print("[AGENT_RUNNER] Exiting main()")



if __name__ == "__main__":
    main()