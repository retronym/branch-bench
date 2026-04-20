from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from . import git, commands
from .config import Config
from .git import Commit
from .report import generate, generate_index
from .storage import Store


def _format_eta(secs: float) -> str:
    s = int(secs)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


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


def _try_relative(p: Path) -> Path:
    """Return p relative to cwd, resolving symlinks on both sides first (e.g. /tmp → /private/tmp on macOS)."""
    if not p.is_absolute():
        return p
    try:
        return p.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return p


def _infer_event(fg: Path) -> str:
    if fg.suffix.lower() == ".jfr":
        return "jfr"
    name = fg.stem.lower()
    for base in ("alloc", "wall", "lock", "cpu"):
        if base in name:
            if "collapsed" in name:
                return f"{base}-collapsed"
            if "reverse" in name:
                return f"{base}-reverse"
            if "forward" in name:
                return f"{base}-forward"
            return base
    return "cpu"


def _make_tee(log: Callable[[str], None]) -> Callable[[str], None]:
    """Return a callable that forwards raw subprocess output lines to *log*."""
    def tee(line: str) -> None:
        # Strip the trailing newline — log() adds its own.
        log(line.rstrip("\n"))
    return tee


def run_commit(
    commit: Commit,
    cfg: Config,
    store: Store,
    repo_path: Path,
    run_tests: bool,
    run_benchmarks: bool,
    log: Callable[[str], None],
    verbose: int = 0,
) -> bool:
    """Run tests and benchmarks for one commit. Returns True if both succeeded.

    *verbose* controls live streaming of subprocess output:
      0 — silent (buffered, shown only on failure via the stored output)
      1 — stream bench command output as it runs
      2 — stream both test and bench command output
    """
    log(f"  Checking out {commit.short_sha}: {commit.message[:60]}")
    git.checkout(repo_path, commit.sha)

    run_id = store.create_run(
        commit_sha=commit.sha,
        bench_cmd=cfg.commands.bench_cmd or None,
        test_cmd=cfg.commands.test_cmd or None,
    )

    if run_tests and cfg.commands.test_cmd:
        log(f"  $ {cfg.commands.test_cmd}")
        test_tee = _make_tee(log) if verbose >= 2 else None
        result = commands.run_test(cfg.commands.test_cmd, repo_path, tee=test_tee)
        store.save_test_run(run_id, result)
        status = "PASS" if result.passed else "FAIL"
        log(f"  Tests: {status} ({result.duration_seconds:.1f}s)")
        if not result.passed:
            log("  [!] Tests failed — skipping benchmarks for this commit")
            return False

    if run_benchmarks and cfg.commands.bench_cmd:
        resolved_cmd = cfg.commands.bench_cmd.replace("{out}", "<jmh-results.json>").replace("{out_dir}", "<profiles-dir>")
        log(f"  $ {resolved_cmd}")
        bench_tee = _make_tee(log) if verbose >= 1 else None
        epoch = store.current_epoch()
        run_number = store.run_number_for_id(run_id)
        run_dir = cfg.run_assets_dir(epoch, commit.short_sha, commit.message, run_number)
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            bench_results, artifacts, bench_output, saved_json = commands.run_bench(
                cfg.commands.bench_cmd,
                repo_path,
                jmh_save_dir=run_dir,
                jmh_save_name="jmh-results",
                tee=bench_tee,
            )
            store.save_bench_output(run_id, bench_output)
            if saved_json:
                rel = _try_relative(saved_json)
                store.save_jmh_json_path(run_id, str(rel))
            store.save_benchmark_results(run_id, bench_results)
            log(f"  Benchmarks: {len(bench_results)} result(s)")

            for artifact in artifacts:
                dest = run_dir / artifact.name
                shutil.move(str(artifact), dest)
                event = _infer_event(dest)
                rel = _try_relative(dest)
                store.save_profile(run_id, event, str(rel))
                log(f"  Profile: {dest.name}")

            return len(bench_results) > 0
        except RuntimeError as e:
            raw = e.args[1] if len(e.args) > 1 else ""
            store.save_bench_output(run_id, raw)
            log(f"  [!] Benchmark error: {e.args[0]}")
            return False

    return True


def _resolve_ref(repo_path: Path, ref: str, flag: str, log: Callable[[str], None]) -> str | None:
    """Resolve *ref* to a full SHA, printing a clear error via *log* on failure."""
    sha = git.rev_parse(repo_path, ref)
    if sha is None:
        log(f"[!] {flag} {ref!r}: not a valid git ref — aborting.")
    return sha


def _expand_refs(
    repo_path: Path,
    refs: tuple[str, ...],
    flag: str,
    log: Callable[[str], None],
) -> list[str] | None:
    """Expand a mix of git refs and range specs to a deduplicated list of full SHAs.

    Range specs (containing '..') are expanded via ``git log``; individual refs
    are resolved with ``git rev-parse``.  Returns None if any ref fails to resolve
    so callers can abort cleanly.
    """
    shas: list[str] = []
    seen: set[str] = set()
    ok = True
    for ref in refs:
        if ".." in ref:
            expanded = git.expand_range(repo_path, ref)
            if not expanded:
                log(f"[!] {flag} {ref!r}: range yielded no commits — skipping.")
            for sha in expanded:
                if sha not in seen:
                    seen.add(sha)
                    shas.append(sha)
        else:
            sha = git.rev_parse(repo_path, ref)
            if sha is None:
                log(f"[!] {flag} {ref!r}: not a valid git ref — skipping.")
                ok = False
                continue
            if sha not in seen:
                seen.add(sha)
                shas.append(sha)
    return shas if ok else None


def run_branch(
    cfg: Config,
    store: Store,
    *,
    max_commits: int | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    target_refs: tuple[str, ...] = (),
    strategy: str = "bisect",
    run_tests: bool = True,
    run_benchmarks: bool = True,
    skip_existing: bool = True,
    live_report: bool = True,
    verbose: int = 0,
    log: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> None:
    repo_path = Path(cfg.repo.path).resolve()
    cfg.base_dir().mkdir(parents=True, exist_ok=True)
    github_url = git.github_remote_url(repo_path)

    if git.is_dirty(repo_path):
        log("[!] Working tree is dirty — stash or commit changes before running.")
        return

    original_ref = git.current_ref(repo_path)
    log(f"Current ref: {original_ref}")

    # ── Resolve all user-supplied refs to full SHAs now, before any git checkout ──
    from_sha: str | None = None
    if from_ref:
        from_sha = _resolve_ref(repo_path, from_ref, "--from", log)
        if from_sha is None:
            return
        log(f"  --from resolved: {from_ref!r} → {from_sha[:8]}")

    to_sha: str | None = None
    if to_ref:
        to_sha = _resolve_ref(repo_path, to_ref, "--to", log)
        if to_sha is None:
            return
        log(f"  --to   resolved: {to_ref!r} → {to_sha[:8]}")

    resolved_targets: list[str] = []
    if target_refs:
        result = _expand_refs(repo_path, target_refs, "--sha", log)
        if result is None:
            return
        resolved_targets = result
        for orig, sha in zip(target_refs, resolved_targets[:len(target_refs)]):
            if orig != sha and not sha.startswith(orig):
                log(f"  --sha  resolved: {orig!r} → {sha[:8]}")

    merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
    if merge_base:
        log(f"Merge base with main/master: {merge_base[:8]} — limiting to branch-only commits")
    all_commits = git.list_commits(repo_path, cfg.repo.branch, max_count=max_commits, exclude_before=merge_base)

    def _find(sha: str, lst: list) -> int | None:
        for i, c in enumerate(lst):
            if c.sha == sha or c.sha.startswith(sha) or c.short_sha.startswith(sha):
                return i
        return None

    # Determine the run range (from/to slicing) — separate from the full list
    run_range = all_commits
    if from_sha:
        idx = _find(from_sha, run_range)
        if idx is None:
            log(f"[!] --from {from_ref!r} (→ {from_sha[:8]}) not found on branch — aborting.")
            return
        run_range = run_range[idx:]
    if to_sha:
        idx = _find(to_sha, run_range)
        if idx is None:
            log(f"[!] --to {to_ref!r} (→ {to_sha[:8]}) not found on branch — aborting.")
            return
        run_range = run_range[: idx + 1]

    if not run_range:
        log("No commits found.")
        return

    log(f"Found {len(run_range)} commit(s) on branch '{cfg.repo.branch}'")

    # Persist ALL commits with absolute positions so the report order is always correct.
    # Using all_commits (not the sliced run_range) prevents --from from corrupting positions.
    for i, commit in enumerate(all_commits):
        store.save_commit(
            sha=commit.sha,
            short_sha=commit.short_sha,
            message=commit.message,
            author=commit.author,
            timestamp=commit.timestamp,
            branch=cfg.repo.branch,
            position=i,
            tree_sha=commit.tree_sha,
        )

    # Retire commits left over from a previous branch incarnation (e.g. after rebase).
    # Only safe when we have a complete, unfiltered view of the branch.
    if not from_sha and not to_sha and max_commits is None:
        current_shas = {c.sha for c in all_commits}
        retired = store.retire_stale_commits(current_shas)
        if retired:
            log(f"  Retired {retired} stale commit(s) no longer on branch")

    backfilled = store.backfill_by_tree_sha()
    if backfilled:
        log(f"  Backfilled {backfilled} commit(s) via tree-SHA reuse")

    # --sha: restrict the run loop to specific commits (saves/retires still use the full list)
    run_commits = run_range
    if resolved_targets:
        seen_shas: set[str] = set()
        run_commits = []
        for sha in resolved_targets:
            idx = _find(sha, all_commits)
            if idx is None:
                log(f"[!] --sha resolved to {sha[:8]} but that commit is not on branch — skipping.")
            elif all_commits[idx].sha not in seen_shas:
                seen_shas.add(all_commits[idx].sha)
                run_commits.append(all_commits[idx])
        if not run_commits:
            log("No matching commits to run.")
            return

    epoch = store.current_epoch()
    report_path = cfg.report_path(epoch)
    if live_report:
        generate(store, report_path, github_url=github_url)
        generate_index(cfg)
        log(f"Report: {report_path.resolve()}")
        log("(refresh after each commit completes)\n")

    indices = bisect_order(len(run_commits)) if strategy == "bisect" else list(range(len(run_commits)))

    runs_done: int = 0
    run_time_total: float = 0.0

    def _eta_str(pos: int) -> str:
        if runs_done <= 0:
            return ""
        avg = run_time_total / runs_done
        remaining = len(indices) - pos
        return f" — ETA ~{_format_eta(avg * remaining)}"

    try:
        first_run = True  # tracks the first commit we actually execute (not skip)
        for pos, idx in enumerate(indices):
            commit = run_commits[idx]

            if skip_existing and store.has_runs(commit.sha, run_benchmarks=run_benchmarks, run_tests=run_tests):
                log(f"  Skipping {commit.short_sha} (already has runs — use --all to re-run)")
                # Note: existing runs shadow tree-SHA matches from newer re-benches on rebased
                # counterparts. If you rebased, re-benched, then reverted, this commit will show
                # its pre-rebase results. Use --sha <sha> --all to add a fresh run, then switch
                # to Aggregate mode in the report, or run `branch-bench epoch` for a clean slate.
                continue

            if skip_existing and commit.tree_sha:
                source = store.find_run_by_tree_sha(
                    commit.tree_sha, exclude_sha=commit.sha,
                    require_bench=run_benchmarks,
                    require_test=run_tests,
                )
                if source:
                    log(f"[{pos+1}/{len(indices)}] {commit.short_sha} — tree identical to {source['short_sha']}, reusing results{_eta_str(pos)}")
                    store.clone_run(source["run_id"], commit.sha, reused_from_sha=source["short_sha"])
                    if live_report:
                        generate(store, report_path, github_url=github_url)
                        generate_index(cfg)
                    if first_run:
                        first_run = False
                    continue

            log(f"[{pos+1}/{len(indices)}] {commit.short_sha}{_eta_str(pos)}")
            if live_report:
                generate(store, report_path, github_url=github_url, next_sha=commit.sha)
                generate_index(cfg)
            _run_start = time.monotonic()
            ok = run_commit(
                commit=commit,
                cfg=cfg,
                store=store,
                repo_path=repo_path,
                run_tests=run_tests,
                run_benchmarks=run_benchmarks,
                log=log,
                verbose=verbose,
            )
            run_time_total += time.monotonic() - _run_start
            runs_done += 1
            if live_report:
                generate(store, report_path, github_url=github_url)
                generate_index(cfg)
                log("  Report updated\n")

            if first_run:
                first_run = False
                if not ok:
                    log(f"[!] Commit 0 ({commit.short_sha}) failed tests or benchmarks — aborting.")
                    log("    Fix the baseline or use --no-test / --no-bench to skip checks.")
                    return
    except KeyboardInterrupt:
        log("\n[!] Interrupted.")
    finally:
        log(f"Restoring {original_ref}...")
        git.restore(repo_path, original_ref)
