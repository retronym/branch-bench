from __future__ import annotations

import json
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


class _RunningLog:
    """Writes tool output to a JS file the report can load via <script src>."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._f = path.open("w", encoding="utf-8")
        self._f.write("window.__runLog=[];\n")
        self._f.flush()

    def append(self, msg: str) -> None:
        entry = json.dumps({"ts": time.time(), "msg": msg})
        self._f.write(f"window.__runLog.push({entry});\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def _make_tee(log: Callable[[str], None]) -> Callable[[str], None]:
    """Return a callable that forwards raw subprocess output lines to *log*."""
    def tee(line: str) -> None:
        # Strip the trailing newline — log() adds its own.
        log(line.rstrip("\n"))
    return tee


def _collect_artifacts(
    artifacts: list[Path],
    run_dir: Path,
    run_id: int,
    store: Store,
    log: Callable[[str], None],
) -> None:
    """Move artifact files into run_dir, infer event type, and persist to store."""
    for artifact in artifacts:
        dest = run_dir / artifact.name
        shutil.move(str(artifact), dest)
        event = _infer_event(dest)
        rel = _try_relative(dest)
        store.save_profile(run_id, event, str(rel))
        log(f"  Profile: {dest.name}")


def run_commit(
    commit: Commit,
    cfg: Config,
    store: Store,
    repo_path: Path,
    run_tests: bool,
    run_benchmarks: bool,
    run_profile_cmd: bool,
    log: Callable[[str], None],
    verbose: int = 0,
) -> bool:
    """Run tests, benchmarks, and/or profiling for one commit.

    Returns True if required steps succeeded (or were skipped).

    *run_profile_cmd* — when True and ``cfg.commands.profile_cmd`` is set,
    a separate profile run (source='profile') is executed after bench.
    Secondary metrics are NOT collected from the profile run.
    """
    log(f"  Checking out {commit.short_sha}: {commit.message[:60]}")
    git.checkout(repo_path, commit.sha)

    epoch = store.current_epoch()

    # ── Test ──────────────────────────────────────────────────────────────────
    bench_run_id = store.create_run(
        commit_sha=commit.sha,
        bench_cmd=cfg.commands.bench_cmd or None,
        test_cmd=cfg.commands.test_cmd or None,
        source="bench",
    )

    if run_tests and cfg.commands.test_cmd:
        log(f"  $ {cfg.commands.test_cmd}")
        test_tee = _make_tee(log) if verbose >= 2 else None
        result = commands.run_test(cfg.commands.test_cmd, repo_path, tee=test_tee)
        store.save_test_run(bench_run_id, result)
        status = "PASS" if result.passed else "FAIL"
        log(f"  Tests: {status} ({result.duration_seconds:.1f}s)")
        if not result.passed:
            log("  [!] Tests failed — skipping benchmarks for this commit")
            return False

    # ── Bench ─────────────────────────────────────────────────────────────────
    if run_benchmarks and cfg.commands.bench_cmd:
        resolved_cmd = cfg.commands.bench_cmd.replace("{out}", "<jmh-results.json>").replace("{out_dir}", "<profiles-dir>")
        log(f"  $ {resolved_cmd}")
        bench_tee = _make_tee(log) if verbose >= 1 else None
        run_number = store.run_number_for_id(bench_run_id)
        run_dir = cfg.run_assets_dir(epoch, commit.short_sha, commit.message, run_number, "bench")
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            bench_results, artifacts, bench_output, saved_json = commands.run_bench(
                cfg.commands.bench_cmd,
                repo_path,
                jmh_save_dir=run_dir,
                jmh_save_name="jmh-results",
                tee=bench_tee,
            )
            store.save_bench_output(bench_run_id, bench_output)
            if saved_json:
                rel = _try_relative(saved_json)
                store.save_jmh_json_path(bench_run_id, str(rel))
            store.save_benchmark_results(bench_run_id, bench_results)
            log(f"  Benchmarks: {len(bench_results)} result(s)")
            _collect_artifacts(artifacts, run_dir, bench_run_id, store, log)

            if not bench_results:
                return False
        except RuntimeError as e:
            raw = e.args[1] if len(e.args) > 1 else ""
            store.save_bench_output(bench_run_id, raw)
            log(f"  [!] Benchmark error: {e.args[0]}")
            return False

    # ── Profile ───────────────────────────────────────────────────────────────
    if run_profile_cmd and cfg.commands.profile_cmd:
        _run_profile_for_commit(commit, cfg, store, repo_path, epoch, log, verbose)

    return True


def _run_profile_for_commit(
    commit: Commit,
    cfg: Config,
    store: Store,
    repo_path: Path,
    epoch: int,
    log: Callable[[str], None],
    verbose: int = 0,
) -> None:
    """Execute profile_cmd for a commit and store the resulting artifacts."""
    resolved_cmd = cfg.commands.profile_cmd.replace("{out}", "<profile.json>").replace("{out_dir}", "<profiles-dir>")
    log(f"  [profile] $ {resolved_cmd}")
    profile_tee = _make_tee(log) if verbose >= 1 else None

    profile_run_id = store.create_run(
        commit_sha=commit.sha,
        bench_cmd=cfg.commands.profile_cmd or None,
        test_cmd=None,
        source="profile",
    )
    run_number = store.run_number_for_id(profile_run_id)
    run_dir = cfg.run_assets_dir(epoch, commit.short_sha, commit.message, run_number, "profile")
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        artifacts, raw_output = commands.run_profile(
            cfg.commands.profile_cmd,
            repo_path,
            tee=profile_tee,
        )
        store.save_bench_output(profile_run_id, raw_output)
        _collect_artifacts(artifacts, run_dir, profile_run_id, store, log)
        log(f"  [profile] {len(artifacts)} artifact(s)")
    except RuntimeError as e:
        raw = e.args[1] if len(e.args) > 1 else ""
        store.save_bench_output(profile_run_id, raw)
        log(f"  [!] Profile error: {e.args[0]}")


def diff_pair(
    left_sha: str,
    left_short_sha: str,
    left_message: str,
    right_commit: Commit,
    diff_vs: str,
    cfg: Config,
    store: Store,
    repo_path: Path,
    epoch: int,
    branch: str,
    log: Callable[[str], None],
    force: bool = False,
) -> None:
    """Run configured diff tools between two commits' best available profiles.

    *diff_vs* is stored verbatim ('previous' or 'branch-base') to label the
    relationship in the UI.
    """
    if not cfg.diff.commands:
        return

    left_profiles = store.best_profiles_for_commit(left_sha)
    right_profiles = store.best_profiles_for_commit(right_commit.sha)

    if not left_profiles or not right_profiles:
        log(
            f"  [diff] No profiles for {left_short_sha[:8]}…{right_commit.short_sha} — skipping"
        )
        return

    # Index right profiles by file extension
    right_by_ext: dict[str, list[dict]] = {}
    for p in right_profiles:
        ext = Path(p["file_path"]).suffix.lstrip(".")
        right_by_ext.setdefault(ext, []).append(p)

    ran = 0
    for left_p in left_profiles:
        left_file = Path(left_p["file_path"])
        ext = left_file.suffix.lstrip(".")
        diff_cmd = cfg.diff.commands.get(ext)
        if not diff_cmd or ext not in right_by_ext:
            continue

        for right_p in right_by_ext[ext]:
            right_file = Path(right_p["file_path"])

            # Skip if already computed (idempotent re-runs), unless forced
            if not force and store.diff_exists(epoch, left_sha, right_commit.sha, diff_vs, ext):
                log(f"  [diff] {left_short_sha[:8]}…{right_commit.short_sha} ({ext}) — cached")
                continue
            if force:
                store.delete_diffs_for_pair(epoch, left_sha, right_commit.sha, diff_vs, ext)

            artifact_stem = left_file.stem
            out_dir = cfg.diff_assets_dir(
                epoch, left_short_sha[:8], right_commit.short_sha, artifact_stem
            )

            try:
                output_files, _ = commands.run_diff_tool(
                    diff_cmd,
                    left_file=left_file.resolve(),
                    left_sha=left_sha,
                    left_commit_msg=left_message,
                    left_branch=branch,
                    right_file=right_file.resolve(),
                    right_sha=right_commit.sha,
                    right_commit_msg=right_commit.message,
                    right_branch=branch,
                    out_dir=out_dir,
                    cwd=repo_path,
                )
                for f in output_files:
                    rel = _try_relative(f)
                    store.save_diff(
                        epoch=epoch,
                        left_sha=left_sha,
                        right_sha=right_commit.sha,
                        diff_vs=diff_vs,
                        source_ext=ext,
                        diff_path=str(rel),
                    )
                    log(f"  [diff] → {f.name}")
                ran += 1
            except RuntimeError as e:
                log(f"  [!] Diff failed ({left_short_sha[:8]}…{right_commit.short_sha}): {e.args[0]}")

    if ran == 0 and left_profiles and right_profiles:
        log(f"  [diff] No matching extensions for {left_short_sha[:8]}…{right_commit.short_sha}")


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
    """Expand a mix of git refs and range specs to a deduplicated list of full SHAs."""
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


def _resolve_diff_vs(
    diff_vs: tuple[str, ...],
    repo_path: Path,
    log: Callable[[str], None],
) -> tuple[str, ...]:
    """Resolve non-'previous' entries in *diff_vs* to full SHAs.

    Unresolvable refs are logged and dropped so the rest of the run continues.
    """
    resolved: list[str] = []
    for v in diff_vs:
        if v == "previous":
            resolved.append(v)
        else:
            sha = git.rev_parse(repo_path, v)
            if sha is None:
                log(f"  [!] --diff-vs {v!r}: not a valid git ref — skipping")
            else:
                resolved.append(sha)
    return tuple(resolved)


def _ensure_merge_base_profiled(
    merge_base: str,
    cfg: Config,
    store: Store,
    repo_path: Path,
    epoch: int,
    log: Callable[[str], None],
    verbose: int = 0,
) -> None:
    """Check out merge-base and run profile_cmd if not already profiled."""
    if store.has_profile_runs(merge_base):
        log(f"  Merge-base {merge_base[:8]} already profiled — reusing")
        return

    log(f"  Profiling merge-base {merge_base[:8]}…")
    git.checkout(repo_path, merge_base)

    # Use a synthetic Commit-like object (merge-base may not be in commits table)
    short = merge_base[:8]
    synthetic = Commit(
        sha=merge_base,
        short_sha=short,
        author="",
        timestamp=0,
        message="(merge-base)",
    )
    _run_profile_for_commit(synthetic, cfg, store, repo_path, epoch, log, verbose)


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
    run_profile_cmd: bool = True,
    run_diff: bool = False,
    diff_vs: tuple[str, ...] = ("previous",),
    skip_existing: bool = True,
    live_report: bool = True,
    verbose: int = 0,
    log: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> None:
    """Walk commits on a branch, run tests/benchmarks/profiling, store results.

    *run_profile_cmd* — also run profile_cmd (when configured) for each commit.
    *run_diff* — run configured diff tools after each commit (linear strategy only;
                 silently skipped for bisect).
    *diff_vs* — tuple of targets: 'previous' and/or full SHAs to diff against.
    """
    repo_path = Path(cfg.repo.path).resolve()
    cfg.base_dir().mkdir(parents=True, exist_ok=True)
    github_url = git.github_remote_url(repo_path, cfg.repo.branch)

    if git.is_dirty(repo_path):
        log("[!] Working tree is dirty — stash or commit changes before running.")
        return

    original_ref = git.current_ref(repo_path)
    log(f"Current ref: {original_ref}")

    # ── Resolve all user-supplied refs to full SHAs before any checkout ──────
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

    epoch = store.current_epoch()
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
            parent_sha=commit.parent_sha,
        )

    if all_commits:
        store.set_epoch_head(epoch, all_commits[-1].sha)
        # We also store the base commit's parent as the epoch base to know where to stop
        # when traversing backwards (though stopping when parent_sha is not in DB also works).
        store.set_epoch_base(epoch, all_commits[0].parent_sha or "")

    backfilled = store.backfill_by_tree_sha()
    if backfilled:
        log(f"  Backfilled {backfilled} commit(s) via tree-SHA reuse")

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

    report_path = cfg.report_path(epoch)
    running_log: _RunningLog | None = None
    if live_report:
        generate(store, report_path, github_url=github_url)
        generate_index(cfg)
        running_log = _RunningLog(report_path.parent / "running.js")
        _orig_log = log
        def log(msg: str) -> None:  # noqa: F811
            _orig_log(msg)
            running_log.append(msg)  # type: ignore[union-attr]
        log(f"Report: {report_path.resolve()}")
        log("(refresh after each commit completes)\n")

    # Resolve any SHA-based diff targets to full SHAs upfront
    resolved_diff_vs = _resolve_diff_vs(diff_vs, repo_path, log) if run_diff else diff_vs

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
        first_run = True
        for pos, idx in enumerate(indices):
            commit = run_commits[idx]

            if skip_existing and store.has_runs(commit.sha, run_benchmarks=run_benchmarks, run_tests=run_tests):
                log(f"  Skipping {commit.short_sha} (already has runs — use --all to re-run)")
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
                run_profile_cmd=run_profile_cmd,
                log=log,
                verbose=verbose,
            )
            run_time_total += time.monotonic() - _run_start
            runs_done += 1

            # Inline diffs after each commit (linear strategy only)
            if run_diff and strategy == "linear" and ok:
                _run_inline_diffs(
                    commit=commit,
                    idx=idx,
                    run_commits=run_commits,
                    diff_vs=resolved_diff_vs,
                    cfg=cfg,
                    store=store,
                    repo_path=repo_path,
                    epoch=epoch,
                    log=log,
                )

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
        if running_log:
            running_log.close()
            running_log._path.unlink(missing_ok=True)


def _run_inline_diffs(
    commit: Commit,
    idx: int,
    run_commits: list[Commit],
    diff_vs: tuple[str, ...],
    cfg: Config,
    store: Store,
    repo_path: Path,
    epoch: int,
    log: Callable[[str], None],
) -> None:
    """Run diffs after a commit in a linear walk.

    *diff_vs* is a tuple of targets:
      'previous'  — diff vs the preceding commit in the walk
      <full SHA>  — diff vs that fixed commit (resolved upstream)
    """
    diff_vs_set = set(diff_vs)

    if "previous" in diff_vs_set and idx > 0:
        prev = run_commits[idx - 1]
        diff_pair(
            left_sha=prev.sha,
            left_short_sha=prev.short_sha,
            left_message=prev.message,
            right_commit=commit,
            diff_vs="previous",
            cfg=cfg,
            store=store,
            repo_path=repo_path,
            epoch=epoch,
            branch=cfg.repo.branch,
            log=log,
        )

    for sha_base in diff_vs_set - {"previous"}:
        info = store.commit_info(sha_base)
        left_sha = info["sha"] if info else sha_base
        left_short = info["short_sha"] if info else sha_base[:8]
        left_message = info["message"] if info else "(unknown)"
        diff_pair(
            left_sha=left_sha,
            left_short_sha=left_short,
            left_message=left_message,
            right_commit=commit,
            diff_vs=sha_base[:8],
            cfg=cfg,
            store=store,
            repo_path=repo_path,
            epoch=epoch,
            branch=cfg.repo.branch,
            log=log,
        )


def profile_branch(
    cfg: Config,
    store: Store,
    *,
    max_commits: int | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    target_refs: tuple[str, ...] = (),
    run_diff: bool = False,
    diff_vs: tuple[str, ...] = ("previous",),
    skip_existing: bool = True,
    live_report: bool = True,
    verbose: int = 0,
    log: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> None:
    """Walk branch commits running only profile_cmd — no bench, no tests."""
    if not cfg.commands.profile_cmd:
        log("[!] profile_cmd is not set in [commands] — nothing to do.")
        return

    repo_path = Path(cfg.repo.path).resolve()
    cfg.base_dir().mkdir(parents=True, exist_ok=True)
    github_url = git.github_remote_url(repo_path, cfg.repo.branch)

    if git.is_dirty(repo_path):
        log("[!] Working tree is dirty — stash or commit changes before running.")
        return

    original_ref = git.current_ref(repo_path)
    log(f"Current ref: {original_ref}")

    from_sha: str | None = None
    if from_ref:
        from_sha = _resolve_ref(repo_path, from_ref, "--from", log)
        if from_sha is None:
            return

    to_sha: str | None = None
    if to_ref:
        to_sha = _resolve_ref(repo_path, to_ref, "--to", log)
        if to_sha is None:
            return

    resolved_targets: list[str] = []
    if target_refs:
        result = _expand_refs(repo_path, target_refs, "--sha", log)
        if result is None:
            return
        resolved_targets = result

    merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
    if merge_base:
        log(f"Merge base: {merge_base[:8]}")
    all_commits = git.list_commits(repo_path, cfg.repo.branch, max_count=max_commits, exclude_before=merge_base)

    def _find(sha: str, lst: list) -> int | None:
        for i, c in enumerate(lst):
            if c.sha == sha or c.sha.startswith(sha) or c.short_sha.startswith(sha):
                return i
        return None

    run_range = all_commits
    if from_sha:
        idx = _find(from_sha, run_range)
        if idx is None:
            log(f"[!] --from not found on branch — aborting.")
            return
        run_range = run_range[idx:]
    if to_sha:
        idx = _find(to_sha, run_range)
        if idx is None:
            log(f"[!] --to not found on branch — aborting.")
            return
        run_range = run_range[: idx + 1]

    if not run_range:
        log("No commits found.")
        return

    # Persist commits so they show in the report
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
            parent_sha=commit.parent_sha,
        )

    epoch = store.current_epoch()
    if all_commits:
        store.set_epoch_head(epoch, all_commits[-1].sha)
        store.set_epoch_base(epoch, all_commits[0].parent_sha or "")
    if resolved_targets:
        seen_shas: set[str] = set()
        run_commits = []
        for sha in resolved_targets:
            idx = _find(sha, all_commits)
            if idx is not None and all_commits[idx].sha not in seen_shas:
                seen_shas.add(all_commits[idx].sha)
                run_commits.append(all_commits[idx])

    log(f"Found {len(run_commits)} commit(s) to profile")

    report_path = cfg.report_path(epoch)

    # Resolve any SHA-based diff targets upfront
    resolved_diff_vs = _resolve_diff_vs(diff_vs, repo_path, log) if run_diff else diff_vs

    try:
        for list_idx, commit in enumerate(run_commits):
            if skip_existing and store.has_profile_runs(commit.sha):
                log(f"  Skipping {commit.short_sha} (already profiled — use --all to re-run)")
                continue

            log(f"[{list_idx+1}/{len(run_commits)}] {commit.short_sha} {commit.message[:50]}")
            git.checkout(repo_path, commit.sha)
            _run_profile_for_commit(commit, cfg, store, repo_path, epoch, log, verbose)

            if run_diff:
                _run_inline_diffs(
                    commit=commit,
                    idx=list_idx,
                    run_commits=run_commits,
                    diff_vs=resolved_diff_vs,
                    cfg=cfg,
                    store=store,
                    repo_path=repo_path,
                    epoch=epoch,
                    log=log,
                )

        if live_report:
            generate(store, report_path, github_url=github_url)
            generate_index(cfg)
            log(f"Report: {report_path.resolve()}")
    except KeyboardInterrupt:
        log("\n[!] Interrupted.")
    finally:
        log(f"Restoring {original_ref}...")
        git.restore(repo_path, original_ref)


def diff_range(
    cfg: Config,
    store: Store,
    commits: list[Commit],
    diff_vs: tuple[str, ...],
    repo_path: Path,
    epoch: int,
    log: Callable[[str], None],
) -> None:
    """Run AOT diffs over an ordered list of commits.

    *diff_vs* is a tuple of targets:
      'previous'  — each commit vs its predecessor in *commits*
      <full SHA>  — each commit vs that fixed commit
    """
    if not cfg.diff.commands:
        log("[!] No [diff] commands configured — nothing to diff.")
        return

    resolved = _resolve_diff_vs(diff_vs, repo_path, log)
    diff_vs_set = set(resolved)

    for i, commit in enumerate(commits):
        if "previous" in diff_vs_set and i > 0:
            prev = commits[i - 1]
            log(f"[{i}/{len(commits)-1}] Diffing {prev.short_sha}…{commit.short_sha} (vs previous)")
            diff_pair(
                left_sha=prev.sha,
                left_short_sha=prev.short_sha,
                left_message=prev.message,
                right_commit=commit,
                diff_vs="previous",
                cfg=cfg,
                store=store,
                repo_path=repo_path,
                epoch=epoch,
                branch=cfg.repo.branch,
                log=log,
            )

        for sha_base in diff_vs_set - {"previous"}:
            info = store.commit_info(sha_base)
            left_sha = info["sha"] if info else sha_base
            left_short = info["short_sha"] if info else sha_base[:8]
            left_message = info["message"] if info else "(unknown)"
            log(f"[{i+1}/{len(commits)}] Diffing {left_short}…{commit.short_sha} (vs {left_short})")
            diff_pair(
                left_sha=left_sha,
                left_short_sha=left_short,
                left_message=left_message,
                right_commit=commit,
                diff_vs=sha_base[:8],
                cfg=cfg,
                store=store,
                repo_path=repo_path,
                epoch=epoch,
                branch=cfg.repo.branch,
                log=log,
            )
