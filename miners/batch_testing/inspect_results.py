"""Summarize a batch_eval run: SWE-bench score, failure buckets, per-repo breakdown.

    uv run python -m miners.inspect_results                 # summary
    uv run python -m miners.inspect_results --show-failures # + list every non-resolved task

Reads harbor_test_agent_results/batch_results.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from ridges_harbor.shared import DEFAULT_RESULTS_DIR

RESULTS_FILENAME = "batch_results.jsonl"

# Excludes infra failures (disk/validator/other) and degenerate tasks (0 tests ran).
MEASURED = {"resolved", "unresolved", "patch broke all", "agent threw"}

INDENT = "  "
SCORE_LABEL_WIDTH = 12


def _load_records(results_path: Path) -> tuple[list[dict], int]:
    """Stream-parse the JSONL, tolerating records glued together by an interrupted write."""
    decoder = json.JSONDecoder()
    data = results_path.read_text()
    records: list[dict] = []
    index = 0
    length = len(data)
    recovered_fragments = 0

    while index < length:
        if data[index].isspace():
            index += 1
            continue

        try:
            record, end = decoder.raw_decode(data, index)
            records.append(record)
            index = end

        except json.JSONDecodeError:
            next_object_start = data.find("{", index + 1)
            if next_object_start == -1:
                break
            recovered_fragments += 1
            index = next_object_start

    return records, recovered_fragments


def _bucket(record: dict) -> str:
    """Classify a record by root cause: agent verdict vs infra vs degenerate."""
    if record.get("status") == "scored":
        tests = record.get("tests", {})
        passed, failed = tests.get("passed", 0), tests.get("failed", 0)
        if (record.get("reward") or 0) >= 1:
            return "resolved"

        if passed == 0 and failed == 0:
            return "degenerate (0 tests)"

        if passed == 0:
            return "patch broke all"
        return "unresolved"

    error = record.get("error") or ""
    if "No space left" in error:
        return "infra: disk-full"

    if record.get("error_code") == "VALIDATOR_INTERNAL_ERROR":
        return "infra: validator-internal"

    if record.get("error_code") == "AGENT_EXCEPTION_RUNNING_AGENT":
        return "agent threw"
    return "infra: other"


def _repo(task: str) -> str:
    return task.split("__", 1)[0]


def _print_summary(
    results_path: Path,
    records: list[dict],
    bucket_counts: collections.Counter,
    recovered_fragments: int,
) -> None:
    print(f"=== {results_path} ===")
    print(
        f"records: {len(records)}"
        + (f"   (recovered {recovered_fragments} corrupt fragments)" if recovered_fragments else "")
    )
    print()

    for name, count in bucket_counts.most_common():
        print(f"{INDENT}{count:4d}  {name}")


def _print_scores(
    records: list[dict],
    bucket_counts: collections.Counter,
    scored: list[dict],
) -> None:
    resolved = bucket_counts["resolved"]
    measured = sum(count for name, count in bucket_counts.items() if name in MEASURED)

    print("\nscore:")
    print(
        f"{INDENT}{'leaderboard':<{SCORE_LABEL_WIDTH}}: "
        f"{resolved}/{len(records)} = {100 * resolved / max(1, len(records)):.1f}%   (resolved / all tasks)"
    )
    print(
        f"{INDENT}{'true':<{SCORE_LABEL_WIDTH}}: "
        f"{resolved}/{measured} = {100 * resolved / max(1, measured):.1f}%   (resolved / cleanly-measured)"
    )

    if scored:
        mean_reward = sum(r["reward"] for r in scored) / len(scored)
        print(f"{INDENT}{'mean reward':<{SCORE_LABEL_WIDTH}}: {mean_reward:.4f}")

    passed = sum(r.get("tests", {}).get("passed", 0) for r in scored)
    failed = sum(r.get("tests", {}).get("failed", 0) for r in scored)
    print(f"{INDENT}{'tests':<{SCORE_LABEL_WIDTH}}: {passed} passed, {failed} failed")


def _print_per_repo(records: list[dict]) -> None:
    resolved_by_repo: collections.Counter = collections.Counter()
    measured_by_repo: collections.Counter = collections.Counter()

    for record in records:
        if _bucket(record) in MEASURED:
            repo = _repo(record["task"])
            measured_by_repo[repo] += 1
            if _bucket(record) == "resolved":
                resolved_by_repo[repo] += 1

    print("\nper-repo (resolved / measured):")
    for repo in sorted(measured_by_repo, key=lambda r: (-measured_by_repo[r], r)):
        total = measured_by_repo[repo]
        print(f"{INDENT}{repo:16} {resolved_by_repo[repo]:3d}/{total:<3d} {100 * resolved_by_repo[repo] / total:5.1f}%")


def _print_failures(records: list[dict]) -> None:
    print("\nnon-resolved tasks:")
    for record in sorted((r for r in records if _bucket(r) != "resolved"), key=lambda r: r["task"]):
        print(f"{INDENT}{_bucket(record):26} {record['task']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--results-dir", default=DEFAULT_RESULTS_DIR, type=Path, help="Dir holding %s" % RESULTS_FILENAME
    )
    parser.add_argument("--show-failures", action="store_true", help="List every non-resolved task and its bucket")
    args = parser.parse_args()

    results_path = args.results_dir.expanduser().resolve() / RESULTS_FILENAME
    if not results_path.exists():
        parser.error(f"No results file at {results_path}")

    records, recovered_fragments = _load_records(results_path)
    bucket_counts = collections.Counter(_bucket(r) for r in records)
    scored = [r for r in records if r.get("status") == "scored"]

    _print_summary(results_path, records, bucket_counts, recovered_fragments)
    _print_scores(records, bucket_counts, scored)
    _print_per_repo(records)

    if args.show_failures:
        _print_failures(records)


if __name__ == "__main__":
    main()
