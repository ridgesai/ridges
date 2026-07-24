"""Run one agent against a whole Harbor dataset (e.g. all of SWE-bench) locally.

Run as a module:

    uv run python -m miners.batch_eval --agent ./agent.py --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from pathlib import Path

from execution.artifacts import result_from_summary
from execution.errors import EvaluationRunException
from miners import run_local_task
from miners.cli.provider_env import resolve_inference_config
from miners.cli.registry import HarborRegistryAdapter
from ridges_harbor.shared import DEFAULT_RESULTS_DIR

RESULTS_FILENAME = "batch_results.jsonl"

STATUS_SCORED = "scored"  # ran and the verifier produced a reward
STATUS_FAILED = "failed"  # ran, but the verifier returned a failure
STATUS_ERROR = "error"  # download/Harbor crashed before scoring


def _parse_shard(value: str, total: int) -> list[int]:
    """Return the indices [0, total) belonging to shard ``i/N`` (i is 1-based)."""
    try:
        shard_text, count_text = value.split("/", 1)
        shard, shard_count = int(shard_text), int(count_text)

    except ValueError as exception:
        raise argparse.ArgumentTypeError(f"--shard must look like 'i/N', got {value!r}") from exception

    if not (1 <= shard <= shard_count):
        raise argparse.ArgumentTypeError(f"--shard i/N requires 1 <= i <= N, got {value!r}")

    return [index for index in range(total) if index % shard_count == shard - 1]


def _load_done_ids(results_path: Path) -> set[str]:
    """Read previously recorded task ids so a resumed run skips them."""
    if not results_path.exists():
        return set()

    done: set[str] = set()
    for line in results_path.read_text().splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            done.add(json.loads(line)["task"])

        except (json.JSONDecodeError, KeyError):
            continue
    return done


async def _run_one(
    adapter: HarborRegistryAdapter,
    *,
    dataset: str,
    problem_id: str,
    agent_path: Path,
    inference,
    results_dir: Path,
    cache_dir: Path,
) -> dict:
    """Download, run, and score a single problem; return one JSONL record."""

    record: dict = {"task": problem_id}
    try:
        task_path = await asyncio.to_thread(adapter.download_problem, dataset, problem_id, dest=cache_dir)
        summary = await run_local_task(
            task_path,
            agent_path=agent_path,
            inference=inference,
            results_dir=results_dir,
        )

    except Exception as exception:
        return record | {"status": STATUS_ERROR, "error": f"{type(exception).__name__}: {exception}"}

    try:
        result = result_from_summary(summary)
    except EvaluationRunException as exception:
        return record | {
            "status": STATUS_FAILED,
            "error_code": exception.error_code.name,
            "error": exception.error_message,
        }

    passed = sum(1 for test in result.test_results if test.status == "pass")
    failed = sum(1 for test in result.test_results if test.status == "fail")
    skipped = sum(1 for test in result.test_results if test.status == "skip")

    return record | {
        "status": STATUS_SCORED,
        "reward": result.verifier_reward,
        "tests": {"total": len(result.test_results), "passed": passed, "failed": failed, "skipped": skipped},
        "trial_dir": str(summary.trial_dir),
    }


async def _prune_docker(until: str) -> None:
    """Best-effort Docker prune to bound disk during long runs.

    Harbor leaves a tagged image PER task (~GBs each) plus BuildKit cache.
    `docker system prune -af` reclaims unused images and build cache
    """
    command = ["docker", "system", "prune", "-af", "--filter", f"until={until}"]
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await process.communicate()
    except Exception as exception:
        print(f"  prune: skipped ({type(exception).__name__}: {exception})", flush=True)
        return

    lines = stdout.decode(errors="replace").strip().splitlines()
    reclaimed = next((line for line in reversed(lines) if "reclaimed" in line.lower()), "done")
    print(f"  prune (until={until}): {reclaimed}", flush=True)


async def _run_all(
    adapter: HarborRegistryAdapter,
    *,
    dataset: str,
    problem_ids: list[str],
    agent_path: Path,
    inference,
    results_dir: Path,
    cache_dir: Path,
    concurrency: int,
    results_path: Path,
    prune_every: int,
    prune_until: str,
) -> None:
    """Run all selected problems with bounded concurrency, streaming results to disk."""
    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    total = len(problem_ids)
    completed = 0

    # Graceful shutdown:
    stop_event = asyncio.Event()

    def _request_stop(signame: str) -> None:
        if not stop_event.is_set():
            print(f"\n{signame} received — draining in-flight tasks, not starting new ones.", flush=True)
            stop_event.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, signame), _request_stop, signame)
        except (NotImplementedError, AttributeError):
            pass

    async def worker(problem_id: str) -> dict | None:
        nonlocal completed
        if stop_event.is_set():
            return None
        async with semaphore:
            if stop_event.is_set():
                return None
            started = time.time()
            record = await _run_one(
                adapter,
                dataset=dataset,
                problem_id=problem_id,
                agent_path=agent_path,
                inference=inference,
                results_dir=results_dir,
                cache_dir=cache_dir,
            )
            record["elapsed_sec"] = round(time.time() - started, 1)

        async with write_lock:
            with results_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
            completed += 1
            reward = record.get("reward")
            detail = f"reward={reward}" if reward is not None else record.get("error_code") or record.get("error", "")
            print(f"[{completed}/{total}] {record['status']:>6}  {problem_id}  {detail}", flush=True)
            should_prune = prune_every > 0 and completed % prune_every == 0

        # Prune outside the write lock so it doesn't block other workers' result writes.
        if should_prune:
            await _prune_docker(prune_until)

        return record

    results = await asyncio.gather(*(worker(problem_id) for problem_id in problem_ids))
    completed_results = [record for record in results if record is not None]
    if stop_event.is_set():
        print(
            f"\nStopped early: {len(completed_results)} done, {len(results) - len(completed_results)} not started (resume will continue).",
            flush=True,
        )
    _print_summary(completed_results)


def _print_summary(results: list[dict]) -> None:
    scored = [record for record in results if record["status"] == STATUS_SCORED]
    failed = [record for record in results if record["status"] == STATUS_FAILED]
    errored = [record for record in results if record["status"] == STATUS_ERROR]
    rewards = [record["reward"] for record in scored if isinstance(record.get("reward"), (int, float))]
    resolved = sum(1 for reward in rewards if reward >= 1.0)

    print("\n" + "=" * 60)
    print(f"  total run:   {len(results)}")
    print(f"  scored:      {len(scored)}  (resolved reward>=1.0: {resolved})")
    print(f"  failed:      {len(failed)}  (ran, verifier returned a failure)")
    print(f"  errored:     {len(errored)}  (download/harbor crash)")
    if rewards:
        print(f"  mean reward: {sum(rewards) / len(rewards):.4f}  (over scored tasks)")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", required=True, type=Path, help="Path to the agent.py to evaluate")
    parser.add_argument("--dataset", default="swebench-verified@1.0", help="Harbor dataset id (default: %(default)s)")
    parser.add_argument("--provider", default="openrouter", help="Inference provider (default: %(default)s)")
    parser.add_argument("--workspace", default=Path.cwd(), type=Path, help="Dir holding .env.miner (default: cwd)")
    parser.add_argument(
        "--concurrency", default=4, type=int, help="Concurrent tasks / containers (default: %(default)s)"
    )
    parser.add_argument("--shard", help="Run only shard i/N of the dataset (1-based), e.g. 1/4")
    parser.add_argument("--limit", type=int, help="Cap number of tasks (after shard) for smoke tests")
    parser.add_argument(
        "--results-dir",
        default=DEFAULT_RESULTS_DIR,
        type=Path,
        help="Where Harbor job dirs + batch_results.jsonl land (default: %(default)s)",
    )
    parser.add_argument("--no-resume", action="store_true", help="Re-run tasks already in batch_results.jsonl")
    parser.add_argument(
        "--prune-every",
        type=int,
        default=5,
        help="Run `docker system prune` after every N completed tasks to bound disk; 0 disables (default: %(default)s)",
    )
    parser.add_argument(
        "--prune-until",
        default="10m",
        help="Keep images/build cache newer than this; older unused is evicted (docker --filter until=) (default: %(default)s)",
    )
    args = parser.parse_args()

    agent_path = args.agent.expanduser().resolve()
    if not agent_path.is_file():
        parser.error(f"--agent is not a file: {agent_path}")

    results_dir = args.results_dir.expanduser().resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / RESULTS_FILENAME
    cache_dir = results_dir / "_batch_downloads"

    inference = resolve_inference_config(args.provider, args.workspace.expanduser().resolve())
    adapter = HarborRegistryAdapter.build()
    problem_ids = [problem.id for problem in adapter.list_problems(args.dataset)]
    print(f"Dataset {args.dataset}: {len(problem_ids)} problems")

    if args.shard:
        shard_indices = set(_parse_shard(args.shard, len(problem_ids)))
        problem_ids = [problem_id for index, problem_id in enumerate(problem_ids) if index in shard_indices]
        print(f"Shard {args.shard}: {len(problem_ids)} problems")

    if args.limit is not None:
        problem_ids = problem_ids[: args.limit]
        print(f"Limited to {len(problem_ids)} problems")

    if not args.no_resume:
        done_ids = _load_done_ids(results_path)
        original_count = len(problem_ids)
        problem_ids = [problem_id for problem_id in problem_ids if problem_id not in done_ids]
        if original_count != len(problem_ids):
            print(f"Resuming: skipped {original_count - len(problem_ids)} already in {results_path.name}")

    if not problem_ids:
        print("Nothing to run.")
        return

    prune_note = f"prune every {args.prune_every} (until={args.prune_until})" if args.prune_every > 0 else "prune off"
    print(f"Running {len(problem_ids)} tasks, concurrency={args.concurrency}, {prune_note}, agent={agent_path}\n")
    asyncio.run(
        _run_all(
            adapter,
            dataset=args.dataset,
            problem_ids=problem_ids,
            agent_path=agent_path,
            inference=inference,
            results_dir=results_dir,
            cache_dir=cache_dir,
            concurrency=args.concurrency,
            results_path=results_path,
            prune_every=args.prune_every,
            prune_until=args.prune_until,
        )
    )


if __name__ == "__main__":
    main()
