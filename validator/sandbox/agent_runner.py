# NOTE: This Python script is not really part of the code that runs on the
#       validator. This file is actually mounted onto each sandbox Docker
#       container at /sandbox/agent_runner.py and is used to run the agent code submitted
#       by the agent developer.

import sys
import json
import traceback
import importlib.util
import subprocess

# Configure git to trust the mounted repository directory in the sandbox
# This prevents "dubious ownership" errors when the repo is mounted from host
try:
    subprocess.run(['git', 'config', '--global', '--add', 'safe.directory', '/sandbox/repo'], 
                   check=True, capture_output=True)
    print('[DOCKER] Configured git safe.directory for /sandbox/repo')
except Exception as e:
    print(f'[DOCKER] Warning: Failed to configure git safe.directory: {e}')

try:
    # Load the input
    with open('/sandbox/input.json') as f:
        input = json.load(f)
    print(f'[DOCKER] input: {input}')

    # Add the src/ directory to the Python path so relative imports work
    src_dir = '/sandbox/src'
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    # Load the AgentMain.py file provided by the agent developer
    spec = importlib.util.spec_from_file_location('AgentMain', f'{src_dir}/agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Run the agent_main function
    print('[DOCKER] before agent_main()')
    output = module.agent_main(input)
    print(f'[DOCKER] after agent_main()')
    
    print(f'[DOCKER] output: {output}')
    success = True
except Exception:
    print(f'[DOCKER] error: {traceback.format_exc()}')
    error = traceback.format_exc()
    success = False



# Write the output to the output file
with open('/sandbox/output.json', 'w') as f:
    if success:
        json.dump({'success': True, 'output': output}, f)
    else:
        json.dump({'success': False, 'error': error}, f)