# NOTE: This Python script is not really part of the code that runs on the
#       validator. This file is actually mounted onto each sandbox Docker
#       container at /sandbox/agent_runner.py and is used to run the agent code submitted
#       by the agent developer.

import sys
import json
import traceback
import importlib.util
import os

try:
    # Load the input
    print('[DOCKER] Loading input.json...')
    with open('/sandbox/input.json') as f:
        input = json.load(f)
    print(f'[DOCKER] input loaded: {input}')

    # Add the src/ directory to the Python path so relative imports work
    src_dir = '/sandbox/src'
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    print(f'[DOCKER] Added {src_dir} to Python path')

    # Check if agent.py exists
    agent_file = f'{src_dir}/agent.py'
    if not os.path.exists(agent_file):
        raise FileNotFoundError(f"agent.py not found at {agent_file}")
    print(f'[DOCKER] Found agent.py at {agent_file}')

    # Load the agent.py file provided by the agent developer
    print('[DOCKER] Loading agent module...')
    spec = importlib.util.spec_from_file_location('AgentMain', agent_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print('[DOCKER] Agent module loaded successfully')

    # Check if agent_main function exists
    if not hasattr(module, 'agent_main'):
        raise AttributeError("agent.py must contain an 'agent_main' function")
    print('[DOCKER] Found agent_main function')

    # Run the agent_main function
    print('[DOCKER] Calling agent_main()...')
    output = module.agent_main(input)
    print(f'[DOCKER] agent_main() completed successfully')
    
    print(f'[DOCKER] output: {output}')
    success = True
except Exception as e:
    print(f'[DOCKER] Error occurred: {e}')
    print(f'[DOCKER] Full traceback: {traceback.format_exc()}')
    error = traceback.format_exc()
    success = False



# Write the output to the output file
with open('/sandbox/output.json', 'w') as f:
    if success:
        json.dump({'success': True, 'output': output}, f)
    else:
        json.dump({'success': False, 'error': error}, f)