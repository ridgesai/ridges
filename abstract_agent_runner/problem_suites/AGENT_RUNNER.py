import sys
import json
import traceback
import importlib.util


def main():
    print("[AGENT_RUNNER] Entered main()")

    try:
        # Read input.json
        print("[AGENT_RUNNER] Reading input.json")
        with open("/sandbox/input.json", "r") as f:
            input_data = json.load(f)
        print("[AGENT_RUNNER] Read input.json")
        
        # Import agent module
        print("[AGENT_RUNNER] Loading /sandbox/agent.py")
        spec = importlib.util.spec_from_file_location("agent", "/sandbox/agent.py")
        agent_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_module)
        print("[AGENT_RUNNER] Loaded /sandbox/agent.py")
        
        # Check for the agent_main() function in /sandbox/agent.py
        if hasattr(agent_module, "agent_main"):
            print("[AGENT_RUNNER] agent_main() function found in /sandbox/agent.py")
        else:
            print("[AGENT_RUNNER] agent_main() function not found in /sandbox/agent.py")
            raise Exception("agent_main() function not found in /sandbox/agent.py")
        
        # Invoke agent_main function
        print("[AGENT_RUNNER] Entering agent's agent_main()")
        agent_main_return_value = agent_module.agent_main(input_data)
        print("[AGENT_RUNNER] Exited agent's agent_main()")

        # Make sure agent_main_return_value is a string
        if not isinstance(agent_main_return_value, str):
            raise Exception("agent_main() function returned a non-string value")

        output = {
            "status": "success",
            "output": agent_main_return_value
        }

        print("[AGENT_RUNNER] Writing output.json")
        with open("/sandbox/output.json", "w") as f:
            json.dump(output, f, indent=2)
        print("[AGENT_RUNNER] Wrote output.json")
        
    except Exception as e:
        print("[AGENT_RUNNER] Exception:")
        traceback.print_exc(file=sys.stdout)
        
        output = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        
        try:
            print("[AGENT_RUNNER] Writing output.json")
            with open("/sandbox/output.json", "w") as f:
                json.dump(output, f, indent=2)
            print("[AGENT_RUNNER] Wrote output.json")
        except:
            print("[AGENT_RUNNER] Failed to write output.json")
            pass

    print("[AGENT_RUNNER] Exiting main()")



if __name__ == "__main__":
    main()