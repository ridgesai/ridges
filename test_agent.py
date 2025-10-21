import uuid
import asyncio
import argparse
import random
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite
from evaluator.sandbox.sandbox_manager import SandboxManager

ALLOWED_POLYGLOT_PROBLEMS = [
    'affine-cipher', 'beer-song', 'book-store', 'bottle-song', 'bowling',
    'connect', 'dominoes', 'dot-dsl', 'food-chain', 'forth', 'go-counting',
    'grade-school', 'grep', 'hangman', 'list-ops', 'phone-number', 'pig-latin',
    'poker', 'pov', 'proverb', 'react', 'rest-api', 'robot-name',
    'scale-generator', 'sgf-parsing'
]

ALLOWED_SWEBENCH_PROBLEMS = [
    'astropy__astropy-13398', 'astropy__astropy-13579', 'astropy__astropy-14369',
    'django__django-10554', 'django__django-11138', 'django__django-11400',
    'django__django-11885', 'django__django-12325', 'django__django-12708',
    'django__django-13128', 'django__django-13212', 'django__django-13344',
    'django__django-13449', 'django__django-13837', 'django__django-14007',
    'django__django-15503', 'django__django-15629', 'django__django-15957',
    'django__django-16263', 'sphinx-doc__sphinx-9229', 'sympy__sympy-12489'
]

SCREENER_1_PROBLEMS = [
    'affine-cipher', 'beer-song', 'book-store', 'bottle-song', 'bowling',
    'astropy__astropy-13398', 'astropy__astropy-13579', 'astropy__astropy-14369',
    'django__django-10554', 'django__django-11138'
]

SCREENER_2_PROBLEMS = [
    'connect', 'dominoes', 'dot-dsl', 'food-chain', 'forth', 'go-counting',
    'grade-school', 'grep', 'hangman', 'list-ops', 'phone-number', 'pig-latin',
    'poker', 'sgf-parsing', 'pov', 'proverb', 'react', 'rest-api', 'robot-name',
    'scale-generator', 'django__django-11400', 'django__django-11885',
    'django__django-12325', 'django__django-12708', 'django__django-13128',
    'django__django-13212', 'django__django-13344', 'django__django-13449',
    'django__django-13837', 'django__django-14007'
]

VALIDATOR_PROBLEMS = [
    'affine-cipher', 'beer-song', 'book-store', 'grep', 'hangman', 'list-ops',
    'phone-number', 'pig-latin', 'poker', 'pov', 'proverb', 'react', 'rest-api',
    'robot-name', 'scale-generator', 'astropy__astropy-13398', 'astropy__astropy-13579',
    'astropy__astropy-14369', 'django__django-10554', 'django__django-11138',
    'django__django-11400', 'django__django-11885', 'django__django-12325',
    'django__django-12708', 'django__django-15503', 'django__django-15629',
    'django__django-15957', 'django__django-16263', 'sphinx-doc__sphinx-9229',
    'sympy__sympy-12489'
]

# example usage:
# python test_agent.py --ip <ip> --problem-set screener_1
# python test_agent.py --ip <ip> --problem-set screener_2
# python test_agent.py --ip <ip> --problem-set validator
# python test_agent.py --ip <ip> --polyglot --random 5
# python test_agent.py --ip <ip> --polyglot --problems problem1 problem2
# python test_agent.py --ip <ip> --swebench --random 3
# python test_agent.py --ip <ip> --swebench --problems problem1 problem2

# prereqs:
# docker desktop & inference_gateway running
# inference_gateway .env file
# agent.py file in current directory

sandbox_manager = None


async def run_agent_on_problem(suite, problem_name=None):
    """Run agent on a problem"""
    global sandbox_manager
    
    if problem_name is None:
        problem_names = list(suite.problems.keys())
        problem_name = problem_names[0]
    
    problem = suite.get_problem(problem_name)
    
    # generate run ID (useless)
    run_id = str(uuid.uuid4())
    

    with open("agent.py", "r") as f:
        agent_code = f.read()
    
    agent_sandbox = await asyncio.to_thread(
        suite.initialize_agent_sandbox,
        sandbox_manager, 
        problem, 
        run_id, 
        agent_code
    )
    
    diff, logs = await asyncio.to_thread(
        suite.run_agent_sandbox,
        sandbox_manager, 
        agent_sandbox, 
        timeout_seconds=2400
    )

    eval_sandbox = await asyncio.to_thread(
        suite.initialize_eval_sandbox,
        sandbox_manager,
        problem,
        run_id,
        diff
    )
    
    test_results, eval_logs = await asyncio.to_thread(
        suite.run_eval_sandbox,
        sandbox_manager, 
        eval_sandbox, 
        timeout_seconds=1200
    )
    
    # uncomment this to look at test results and eval logs
    # print(f"Test results: {test_results}")
    # print(f"Evaluation logs: {eval_logs}")
    
    return diff, test_results

async def run_single_problem(suite, problem_name):
    """Run a single problem using the existing run_agent_on_problem function"""
    try:
        print(f"Starting to run problem: {problem_name}")
        diff, test_results = await run_agent_on_problem(suite, problem_name)
        print(f"Successfully ran problem: {problem_name}")

        return {
            "problem_name": problem_name,
            "diff": diff,
            "test_results": test_results,
            "success": True
        }
        
    except Exception as e:
        print(f"Failed to run problem: {problem_name} with error: {e}")
        return {
            "problem_name": problem_name,
            "error": str(e),
            "success": False
        }

async def run_multiple_problems_concurrently(problem_set=None, problem_names=None, max_problems=None, problem_type=None):
    """Run multiple problems concurrently"""
    
    # if given problem_set
    if problem_set:
        if problem_set == "screener_1":
            problem_names = SCREENER_1_PROBLEMS
        elif problem_set == "screener_2":
            problem_names = SCREENER_2_PROBLEMS
        elif problem_set == "validator":
            problem_names = VALIDATOR_PROBLEMS
        else:
            print(f"Unknown problem set: {problem_set}")
            return []
        
        print(f"🚀🚀🚀🚀 Running {problem_set} problem set with {len(problem_names)} problems: {problem_names}\n")
    
    # if given problem_names
    elif problem_names:
        print(f"🚀🚀🚀🚀 Running {len(problem_names)} specific problems: {problem_names}\n")
    
    # if given max_problems and problem_type
    elif max_problems and problem_type:
        if problem_type == "polyglot":
            problem_names = random.sample(ALLOWED_POLYGLOT_PROBLEMS, max_problems)
        elif problem_type == "swebench":
            problem_names = random.sample(ALLOWED_SWEBENCH_PROBLEMS, max_problems)
        else:
            print(f"Invalid problem type: {problem_type}")
            return []
        
        print(f"🚀🚀🚀🚀 Running {len(problem_names)} random {problem_type} problems: {problem_names}\n")
    
    else:
        print("No valid parameters provided!")
        return []
    
    polyglot_problems = [p for p in problem_names if p in ALLOWED_POLYGLOT_PROBLEMS]
    swebench_problems = [p for p in problem_names if p in ALLOWED_SWEBENCH_PROBLEMS]
    
    if not polyglot_problems and not swebench_problems:
        print("No valid problems found!")
        return []
    
    # load required problem suites
    polyglot_suite = None
    swebench_suite = None
    
    if polyglot_problems:
        dataset_path = "evaluator/datasets/polyglot"
        polyglot_suite = PolyglotSuite(dataset_path)
    
    if swebench_problems:
        dataset_path = "evaluator/datasets/swebench_verified"
        swebench_suite = SWEBenchVerifiedSuite(dataset_path)
        
        print("🔨 Prebuilding Docker images for SWE-bench problems...")
        try:
            swebench_suite.prebuild_problem_images(swebench_problems)
            print("✅ Successfully prebuilt Docker images")
        except Exception as e:
            print(f"⚠️ Warning: Failed to prebuild some Docker images: {e}")
            print("Continuing anyway...")
    
    tasks = []
    for problem_name in problem_names:
        if problem_name in polyglot_problems:
            suite = polyglot_suite
        elif problem_name in swebench_problems:
            suite = swebench_suite
        else:
            continue  # skip invalid problems
            
        task = asyncio.create_task(
            run_single_problem(suite, problem_name)
        )
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # process results
    successful = [r for r in results if isinstance(r, dict) and r.get("success", False)]
    failed = [r for r in results if isinstance(r, dict) and not r.get("success", False)]
    exceptions = [r for r in results if isinstance(r, Exception)]
    
    print(f"\n📊 Results Summary:")
    print(f"  ✅ Successful: {len(successful)}")
    print(f"  ❌ Failed: {len(failed)}")
    print(f"  💥 Exceptions: {len(exceptions)}")
    
    for result in successful:
        num_passed = sum(1 for test in result["test_results"] if test.status.value == "pass")
        num_total = len(result["test_results"])
        print(f"  ✅ {result['problem_name']}: {num_passed}/{num_total} tests passed")
    
    for result in failed:
        print(f"  ❌ {result['problem_name']}: {result.get('error', 'Unknown error')}")
    
    for i, exc in enumerate(exceptions):
        print(f"  💥 Exception {i+1}: {exc}")
    
    return results

if __name__ == "__main__":
    # parse command line arguments
    parser = argparse.ArgumentParser(description="Run agent on polyglot or SWE-bench problems")
    
    parser.add_argument('--ip', type=str, required=True,
                       help='IP address of the inference gateway (e.g., 192.168.1.100 or localhost)')
    
    problem_type_group = parser.add_mutually_exclusive_group()
    problem_type_group.add_argument('--polyglot', action='store_true', help='run polyglot problems')
    problem_type_group.add_argument('--swebench', action='store_true', help='run SWE-bench problems')
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--problem-set', choices=['screener_1', 'screener_2', 'validator'], 
                       help='run a predefined problem set')
    group.add_argument('--random', type=int, help='pick N random problems')
    group.add_argument('--problems', nargs='+', help='specific problem names to run')
    
    args = parser.parse_args()

    # initialize sandbox manager
    inference_gateway_url = f"http://{args.ip}:1234"
    sandbox_manager = SandboxManager(inference_gateway_url)
    
    # problem set (screener_1, screener_2, validator)
    if args.problem_set:
        asyncio.run(run_multiple_problems_concurrently(problem_set=args.problem_set))
    
    # swebench (random or list of problem names)
    elif args.swebench:
        if args.random:
            asyncio.run(run_multiple_problems_concurrently(
                max_problems=min(args.random, len(ALLOWED_SWEBENCH_PROBLEMS)),
                problem_type="swebench"
            ))
        else:
            asyncio.run(run_multiple_problems_concurrently(
                problem_names=args.problems, 
                problem_type="swebench"
            ))
    
    # polyglot (random or list of problem names)
    elif args.polyglot:
        if args.random:
            asyncio.run(run_multiple_problems_concurrently(
                max_problems=min(args.random, len(ALLOWED_POLYGLOT_PROBLEMS)), 
                problem_type="polyglot"
            ))
        else:
            asyncio.run(run_multiple_problems_concurrently(
                problem_names=args.problems, 
                problem_type="polyglot"
            ))
    
    # neither polyglot nor swebench specified when using random/problems
    else:
        if args.random or args.problems:
            print("Error: Must specify either --polyglot or --swebench when using --random or --problems")
            exit(1)