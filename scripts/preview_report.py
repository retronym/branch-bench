#!/usr/bin/env python3
"""Generate a sample report with dummy data for UI preview/development.

Usage:
    python scripts/preview_report.py [--in-progress <short_sha>]

Writes to preview/report.html and prints the path.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from branch_bench.storage import Store, BenchmarkResult, TestResult, SecondaryMetric
from branch_bench.report import generate

BENCHMARKS = [
    "com.example.MyBench.parseJson",
    "com.example.MyBench.serializeJson",
    "com.example.OtherBench.dbQuery",
    "com.example.OtherBench.dbInsert",
]

COMMITS = [
    ("aabbccdd1111", "aabbccd", "Optimise JSON parser hot path", "Alice <alice@example.com>"),
    ("bbccddee2222", "bbccdde", "Add streaming serialiser", "Bob <bob@example.com>"),
    ("ccddeeff3333", "ccddeef", "Refactor DB connection pool", "Alice <alice@example.com>"),
    ("ddeeff001111", "ddeeff0", "Switch to prepared statements", "Carol <carol@example.com>"),
    ("eeff00112222", "eeff001", "Batch insert optimisation", "Bob <bob@example.com>"),
    ("ff0011223333", "ff00112", "Remove redundant allocations", "Carol <carol@example.com>"),
    ("001122334444", "0011223", "Upgrade jackson to 2.17", "Alice <alice@example.com>"),
    ("112233445555", "1122334", "Inline hot loop in query path", "Bob <bob@example.com>"),
]

BASELINES = {
    "com.example.MyBench.parseJson":    1_250.0,
    "com.example.MyBench.serializeJson": 980.0,
    "com.example.OtherBench.dbQuery":    420.0,
    "com.example.OtherBench.dbInsert":   310.0,
}


def _seed_score(bench: str, commit_idx: int, rng: random.Random) -> float:
    base = BASELINES[bench]
    # Slight upward trend with noise
    trend = 1.0 + commit_idx * 0.012
    noise = rng.gauss(1.0, 0.025)
    return base * trend * noise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-progress", metavar="SHA", default=None,
                        help="Short SHA to mark as in-progress (pulsing indicator)")
    args = parser.parse_args()

    out_dir = ROOT / "preview"
    out_dir.mkdir(exist_ok=True)
    db_path = out_dir / "preview.db"
    db_path.unlink(missing_ok=True)

    store = Store(db_path)

    rng = random.Random(42)
    base_ts = int(time.time()) - len(COMMITS) * 3600

    for i, (sha, short_sha, message, author) in enumerate(COMMITS):
        ts = base_ts + i * 3600
        store.save_commit(sha, short_sha, message, author, ts, "feature/my-branch", position=i)

        # Last commit can be left pending (no run) to demo the pending state
        if i == len(COMMITS) - 1 and args.in_progress is None:
            # Leave it pending to demo that state naturally
            # (unless we're demoing in-progress, in which case we skip nothing)
            continue

        run_id = store.create_run(sha,
                                  bench_cmd="mvn -pl bench jmh:run -rf JSON",
                                  test_cmd="mvn test")

        passed = rng.random() > 0.15
        store.save_test_run(run_id, TestResult(
            passed=passed,
            tests_run=rng.randint(80, 200),
            tests_failed=0 if passed else rng.randint(1, 3),
            duration_seconds=rng.uniform(8.0, 25.0),
            output="" if passed else "FAILED: com.example.SomeTest#testEdgeCase\nassertionError: expected 42 but was 41\n",
        ))

        raw_data = [_seed_score(b, i, rng) * rng.gauss(1.0, 0.01) for b in BENCHMARKS for _ in range(5)]

        results = [
            BenchmarkResult(
                benchmark=bench,
                mode="thrpt",
                score=_seed_score(bench, i, rng),
                score_error=abs(rng.gauss(0, BASELINES[bench] * 0.015)),
                unit="ops/s",
                raw_data=[_seed_score(bench, i, rng) * rng.gauss(1.0, 0.01) for _ in range(5)],
                secondary_metrics=[
                    SecondaryMetric(
                        metric="·gc.alloc.rate.norm",
                        score=rng.uniform(400, 1200),
                        score_error=rng.uniform(5, 30),
                        unit="B/op",
                    ),
                ],
            )
            for bench in BENCHMARKS
        ]
        store.save_benchmark_results(run_id, results)

    next_sha: str | None = None
    if args.in_progress:
        # Find the matching full SHA
        for sha, short_sha, *_ in COMMITS:
            if short_sha == args.in_progress or sha.startswith(args.in_progress):
                next_sha = sha
                break

    report_path = out_dir / "index.html"
    generate(store, report_path,
             github_url="https://github.com/example/myrepo",
             next_sha=next_sha)

    print(report_path.resolve())


if __name__ == "__main__":
    main()
