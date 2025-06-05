import os
import ast



def validate_sandbox_dir(dir_path: str) -> None:
    """
    Checks if the given directory is in the appropriate format for a sandbox.
    Returns None if the directory is valid, otherwise raises a ValueError.
    """

    # First, check if the directory exists.
    if not os.path.isdir(dir_path):
        raise ValueError(f'Failed to find {dir_path}')

    # Then, check if the AgentMain.py file exists
    agent_main_path = os.path.join(dir_path, 'AgentMain.py')
    if not os.path.isfile(agent_main_path):
        raise ValueError(f'Failed to find AgentMain.py')

    # Then, parse the AgentMain.py file
    try:
        with open(agent_main_path, 'r') as f:
            tree = ast.parse(f.read())
    except Exception as e:
        raise ValueError(f'Failed to parse AgentMain.py: {str(e)}')

    # Finally, look for top-level agent_main function
    found_agent_main = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'agent_main':
            args = node.args
            if len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs) == 1 and not args.vararg and not args.kwarg:
                found_agent_main = True
                break

    if not found_agent_main:
        raise ValueError('Failed to find agent_main()')