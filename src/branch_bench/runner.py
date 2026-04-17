from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

from . import git, commands
from .config import Config
from .git import Commit
from .report import generate
from .storage import Store


def bisect_order(n: int) -> list[int]:
    """Return indices 0..n-1 in bisect order: endpoints first, then midpoints."""
    if n == 0:
        return []
    if n == 1:
        return [0]
    result: list[int] = []
    seen: set[int] = set()

    def add(i: int) -> None:
        if i not in seen:
            seen.add(i)
            result.append(i)

    add(0)
    add(n - 1)

    queue = [(0, n - 1)]
    while queue:
        lo, hi = queue.pop(0)
        mid = (lo + hi) // 2
        if mid == lo or mid == hi:
            continue
        add(mid)
        queue.append((lo, mid))
        queue.append((mid, hi))

    for i in range(n):
        add(i)

    return result


def _infer_event(fg: Path) -> str:
    name = fg.stem.lower()
    if "alloc" in name:
        return "alloc"
    if "wall" in name:
        return "wall"
    return "cpu"


def run_commit(
    commit: Commit,
    cfg: Config,
    store: Store,
    repo_path: Path,
    profiles_dir: Path,
    run_tests: bool,
    run_benchmarks: bool,
    log: Callable[[str], None],
) -> bool:
    """Run tests and benchmarks for one commit. Returns True if both succeeded."""
    log(f"  Checking out {commit.short_sha}: {commit.message[:60]}")
    git.checkout(repo_path, commit.sha)

    run_id = store.create_run(
        commit_sha=commit.sha,
        bench_cmd=cfg.commands.bench_cmd or None,
        test_cmd=cfg.commands.test_cmd or None,
    )

    if run_tests and cfg.commands.test_cmd:
        log(f"  $ {cfg.commands.test_cmd}")
        result = commands.run_test(cfg.commands.test_cmd, repo_path)
        store.save_test_run(run_id, result)
        status = "PASS" if result.passed else "FAIL"
        log(f"  Tests: {status} ({result.duration_seconds:.1f}s)")
        if not result.passed:
            log("  [!] Tests failed — skipping benchmarks for this commit")
            return False

    if run_benchmarks and cfg.commands.bench_cmd:
        resolved_cmd = cfg.commands.bench_cmd.replace("{out}", "<jmh-results.json>").replace("{out_dir}", "<profiles-dir>")
        log(f"  $ {resolved_cmd}")
        jmh_dir = profiles_dir.parent / "jmh"
        try:
            bench_results, svgs, bench_output, saved_json = commands.run_bench(
                cfg.commands.bench_cmd,
                repo_path,
                jmh_save_dir=jmh_dir,
                jmh_save_name=f"{commit.short_sha}-{run_id}",
            )
            store.save_bench_output(run_id, bench_output)
            if saved_json:
                rel = saved_json.relative_to(Path.cwd()) if saved_json.is_absolute() else saved_json
                store.save_jmh_json_path(run_id, str(rel))
            store.save_benchmark_results(run_id, bench_results)
            log(f"  Benchmarks: {len(bench_results)} result(s)")

            for svg in svgs:
                dest = profiles_dir / f"{commit.short_sha}-{svg.name}"
                shutil.move(str(svg), dest)
                event = _infer_event(dest)
                rel = dest.relative_to(Path.cwd()) if dest.is_absolute() else dest
                store.save_profile(run_id, event, str(rel))
                log(f"  Profile: {dest.name}")

            return len(bench_results) > 0
        except RuntimeError as e:
            raw = e.args[1] if len(e.args) > 1 else ""
            store.save_bench_output(run_id, raw)
            log(f"  [!] Benchmark error: {e.args[0]}")
            return False

    return True


def run_branch(
    cfg: Config,
    store: Store,
    *,
    max_commits: int | None = None,
    from_sha: str | None = None,
    to_sha: str | None = None,
    strategy: str = "bisect",
    run_tests: bool = True,
    run_benchmarks: bool = True,
    skip_existing: bool = True,
    live_report: bool = True,
    log: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> None:
    repo_path = Path(cfg.repo.path).resolve()
    profiles_dir = Path(cfg.output.profiles_dir)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    if git.is_dirty(repo_path):
        log("[!] Working tree is dirty — stash or commit changes before running.")
        return

    original_ref = git.current_ref(repo_path)
    log(f"Current ref: {original_ref}")

    merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
    if merge_base:
        log(f"Merge base with main/master: {merge_base[:8]} — limiting to branch-only commits")
    commits = git.list_commits(repo_path, cfg.repo.branch, max_count=max_commits, exclude_before=merge_base)

    if from_sha:
        shas = [c.sha for c in commits]
        if from_sha in shas:
            commits = commits[shas.index(from_sha):]
    if to_sha:
        shas = [c.sha for c in commits]
        if to_sha in shas:
            commits = commits[: shas.index(to_sha) + 1]

    if not commits:
        log("No commits found.")
        return

    log(f"Found {len(commits)} commit(s) on branch '{cfg.repo.branch}'")

    # Persist all commits up front so the placeholder report shows the full list
    for commit in commits:
        store.save_commit(
            sha=commit.sha,
            short_sha=commit.short_sha,
            message=commit.message,
            author=commit.author,
            timestamp=commit.timestamp,
            branch=cfg.repo.branch,
        )

    report_path = Path(cfg.output.report)
    if live_report:
        generate(store, report_path)
        log(f"Report: {report_path.resolve()}")
        log("(refresh after each commit completes)\n")

    indices = bisect_order(len(commits)) if strategy == "bisect" else list(range(len(commits)))

    try:
        first_run = True  # tracks the first commit we actually execute (not skip)
        for pos, idx in enumerate(indices):
            commit = commits[idx]

            if skip_existing and store.has_runs(commit.sha):
                log(f"  Skipping {commit.short_sha} (already has runs — use --all to re-run)")
                continue

            log(f"[{pos+1}/{len(indices)}] {commit.short_sha}")
            ok = run_commit(
                commit=commit,
                cfg=cfg,
                store=store,
                repo_path=repo_path,
                profiles_dir=profiles_dir,
                run_tests=run_tests,
                run_benchmarks=run_benchmarks,
                log=log,
            )
            if live_report:
                generate(store, report_path)
                log("  Report updated\n")

            if first_run:
                first_run = False
                if not ok:
                    log(f"[!] Commit 0 ({commit.short_sha}) failed tests or benchmarks — aborting.")
                    log("    Fix the baseline or use --no-test / --no-bench to skip checks.")
                    return
    finally:
        log(f"Restoring {original_ref}...")
        git.restore(repo_path, original_ref)
