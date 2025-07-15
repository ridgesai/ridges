"""
Local evaluation runner for testing agents without database infrastructure.

This module orchestrates:
- Loading SWE-bench problems
- Running evaluations on multiple problems
- Collecting and summarizing results
"""

import asyncio
import time
from typing import Dict, Any, List
from pathlib import Path

from swebench.harness.run_evaluation import load_swebench_dataset
from validator.local_testing.problem_instances import EASY_INSTANCES, SCREENER_INSTANCES, TEST_SCREENER_INSTANCES
from validator.sandbox.schema import SwebenchProblem
from validator.local_testing.local_manager import LocalSandboxManager
from loggers.logging_utils import get_logger
from rich.console import Console
from rich.panel import Panel

logger = get_logger(__name__)
console = Console()

def _display_single_test_result(result: Dict[str, Any], problem_index: int, total_problems: int):
    """Display complete result for a single test"""
    
    instance_id = result['instance_id']
    status = result['status']
    duration = result.get('duration', 0)
    solved = result.get('solved', False)
    
    # Choose status icon and color
    if status == 'COMPLETED' and solved:
        status_icon = "✅"
        status_color = "green"
        status_text = "SOLVED"
    elif status == 'COMPLETED' and not solved:
        status_icon = "🔧"
        status_color = "yellow" 
        status_text = "PATCH GENERATED"
    elif status == 'TIMEOUT':
        status_icon = "⏰"
        status_color = "red"
        status_text = "TIMEOUT"
    else:
        status_icon = "❌"
        status_color = "red"
        status_text = "FAILED"
    
    # Create title
    title = f"{status_icon} Test {problem_index}/{total_problems}: {instance_id}"
    
    # Build content
    content_lines = []
    content_lines.append(f"[{status_color}]Status:[/{status_color}] {status_text}")
    content_lines.append(f"[cyan]Duration:[/cyan] {duration:.1f}s")
    
    if result.get('patch_generated'):
        patch_len = result.get('patch_length', 0)
        content_lines.append(f"[green]Patch:[/green] Generated ({patch_len} chars)")
    
    # Add logs from the test execution
    if result.get('logs'):
        content_lines.append("\n[dim]Test Execution Log:[/dim]")
        for log_line in result['logs']:
            content_lines.append(f"[dim]{log_line}[/dim]")
    
    # Add error details if present  
    if result.get('error') and status != 'COMPLETED':
        error_msg = result['error']
        # Truncate very long errors for display
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + "... (truncated)"
        content_lines.append(f"\n[red]Error:[/red]")
        content_lines.append(f"[dim]{error_msg}[/dim]")
    
    content = "\n".join(content_lines)
    
    # Display with panel
    console.print(Panel(content, title=title, border_style=status_color))

async def run_single_problem_evaluation(
    problem: Any,
    agent_file: str,
    timeout: int,
    manager: LocalSandboxManager,
    problem_index: int,
    total_problems: int
) -> Dict[str, Any]:
    """Run evaluation on a single problem with complete error handling"""
    
    # Initialize log buffer for this test
    log_buffer = []
    log_buffer.append(f"[{problem_index}/{total_problems}] Testing: {problem.instance_id}")
    
    problem_start = time.time()
    
    try:
        # Create sandbox with log buffer
        sandbox = await manager.create_sandbox(problem, Path(agent_file), log_buffer)
        
        # Run evaluation with timeout
        result = await asyncio.wait_for(
            run_single_evaluation(sandbox, problem),
            timeout=timeout
        )
        
        result['duration'] = time.time() - problem_start
        result['logs'] = log_buffer  # Include logs in result
        
        # Display complete result for this test
        _display_single_test_result(result, problem_index, total_problems)
        
        return result
        
    except asyncio.TimeoutError:
        log_buffer.append(f"❌ Evaluation timed out after {timeout}s")
        result = {
            'instance_id': problem.instance_id,
            'status': 'TIMEOUT',
            'solved': False,
            'error': f'Evaluation timed out after {timeout}s',
            'duration': timeout,
            'patch_generated': False,
            'patch_length': 0,
            'patch_content': '',
            'logs': log_buffer
        }
        _display_single_test_result(result, problem_index, total_problems)
        return result
        
    except Exception as e:
        log_buffer.append(f"❌ Exception during evaluation: {str(e)}")
        result = {
            'instance_id': problem.instance_id,
            'status': 'ERROR',
            'solved': False,
            'error': str(e),
            'duration': time.time() - problem_start,
            'patch_generated': False,
            'patch_length': 0,
            'patch_content': '',
            'logs': log_buffer
        }
        _display_single_test_result(result, problem_index, total_problems)
        return result

async def run_local_evaluations(
    agent_file: str,
    num_problems: int,
    timeout: int,
    problem_set: str,
    manager: LocalSandboxManager
) -> Dict[str, Any]:
    """Run local evaluations on selected problems in parallel"""
    
    # Load problems
    problems = load_local_problems(problem_set, num_problems)
    
    start_time = time.time()
    
    # Create tasks for all problems to run in parallel
    tasks = []
    for i, problem in enumerate(problems):
        task = asyncio.create_task(
            run_single_problem_evaluation(
                problem=problem,
                agent_file=agent_file,
                timeout=timeout,
                manager=manager,
                problem_index=i+1,
                total_problems=len(problems)
            )
        )
        tasks.append(task)
    
    # Run all evaluations in parallel
    console.print(f"🚀 Starting {len(problems)} evaluations in parallel...", style="cyan")
    console.print(f"📝 Individual results will be displayed as each test completes.\n", style="dim")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Handle any exceptions that were returned instead of results
    final_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            final_results.append({
                'instance_id': problems[i].instance_id,
                'status': 'ERROR',
                'solved': False,
                'error': f'Task failed with exception: {str(result)}',
                'duration': 0,
                'patch_generated': False,
                'patch_length': 0,
                'patch_content': ''
            })
        else:
            final_results.append(result)
    
    # Generate summary
    summary = generate_summary(final_results)
    
    return {
        'results': final_results,
        'summary': summary,
        'total_duration': time.time() - start_time
    }

async def run_single_evaluation(sandbox, problem: SwebenchProblem) -> Dict[str, Any]:
    """Run evaluation on a single problem"""
    
    try:
        # Run the sandbox (this does patch generation + evaluation)
        await sandbox.run()
        
        # Extract results
        evaluation_run = sandbox.evaluation_run
        
        return {
            'instance_id': problem.instance_id,
            'status': 'SOLVED' if evaluation_run.solved else 'FAILED',
            'solved': evaluation_run.solved or False,
            'error': evaluation_run.error,
            'patch_generated': bool(evaluation_run.response),
            'patch_length': len(evaluation_run.response) if evaluation_run.response else 0,
            'patch_content': evaluation_run.response or '',  # Add patch content
            'fail_to_pass': evaluation_run.fail_to_pass_success,
            'pass_to_pass': evaluation_run.pass_to_pass_success,
        }
        
    except Exception as e:
        return {
            'instance_id': problem.instance_id,
            'status': 'ERROR',
            'solved': False,
            'error': str(e),
            'patch_generated': False,
            'patch_length': 0,
            'patch_content': '',  # Add empty patch content for errors
        }

def load_local_problems(problem_set: str, num_problems: int) -> List[SwebenchProblem]:
    """Load problems for local testing"""
    
    # Select problem instances
    if problem_set == "screener":
        instances = TEST_SCREENER_INSTANCES  # Use smaller subset for local testing
    elif problem_set == "easy":
        instances = EASY_INSTANCES
    else:
        instances = TEST_SCREENER_INSTANCES  # Default fallback
    
    # Take only the requested number
    selected_instances = instances[:num_problems]
    
    # Load from SWE-bench dataset
    swebench_problems = load_swebench_dataset(
        "SWE-bench/SWE-bench_Verified", 
        "test", 
        selected_instances
    )
    
    # Convert to our problem format
    problems = []
    for instance in swebench_problems:
        problem = SwebenchProblem(
            instance_id=instance["instance_id"],
            problem_statement=instance["problem_statement"],
            repo=instance["repo"],
            base_commit=instance["base_commit"],
        )
        problems.append(problem)
    
    return problems

def generate_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate summary statistics"""
    
    total_count = len(results)
    solved_count = sum(1 for r in results if r['solved'])
    patches_generated = sum(1 for r in results if r.get('patch_generated', False))
    
    durations = [r['duration'] for r in results]
    avg_time = sum(durations) / len(durations) if durations else 0
    
    return {
        'total_count': total_count,
        'solved_count': solved_count,
        'success_rate': (solved_count / total_count * 100) if total_count > 0 else 0,
        'patches_generated': patches_generated,
        'avg_time': avg_time,
        'total_time': sum(durations)
    } 