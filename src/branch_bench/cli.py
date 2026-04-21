from __future__ import annotations

import shutil
import webbrowser
from pathlib import Path

import click

from .config import load_config, commit_slug, TEMPLATE
from .runner import run_branch, profile_branch, diff_range
from .report import generate, generate_index
from .storage import Store
from . import git

CONFIG_FILE = "bench.toml"


def _find_config() -> Path:
    p = Path(CONFIG_FILE)
    if not p.exists():
        raise click.ClickException(
            f"{CONFIG_FILE} not found. Run `branch-bench init` to create one."
        )
    return p


def _onboard(config_path: Path) -> None:
    """Offer to write a starter config to .bench/bench.toml with setup instructions."""
    template_path = Path(".bench") / "bench.toml"
    click.echo(f"No {config_path} found in the current directory.")
    if not click.confirm(f"Create a starter config at {template_path}?", default=True):
        raise SystemExit(0)

    template_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not template_path.exists()
    if fresh:
        template_path.write_text(TEMPLATE, encoding="utf-8")
        click.echo(f"Written to {template_path}.")
    else:
        click.echo(f"{template_path} already exists — not overwritten.")

    click.echo(f"""
Next steps:

  1. Open {template_path}
  2. Set [repo] → branch  to your feature branch name
  3. Set [commands] → bench_cmd  to your JMH runner command
       Use {{out}} for the JMH results file and {{out_dir}} for profiler output
       Example: ./mill foo.jmh.run -- -rff {{out}} -wi 5 -i 5 -f 1
  4. Move it:  mv {template_path} {config_path}
  5. Run:      branch-bench run
""")


@click.group()
def main() -> None:
    """branch-bench: verify, benchmark, and profile a git branch commit-by-commit."""


@main.command()
def init() -> None:
    """Create a bench.toml config file in the current directory."""
    p = Path(CONFIG_FILE)
    if p.exists():
        raise click.ClickException(f"{CONFIG_FILE} already exists.")
    p.write_text(TEMPLATE, encoding="utf-8")
    click.echo(f"Created {CONFIG_FILE}. Edit it to configure your project.")


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--commits", "-n", type=int, default=None, help="Max commits to process")
@click.option("--from", "--from-sha", "from_ref", default=None, metavar="REF",
              help="Start from this commit (any git ref: SHA, HEAD~N, tag, branch)")
@click.option("--to", "--to-sha", "to_ref", default=None, metavar="REF",
              help="Stop at this commit (any git ref: SHA, HEAD~N, tag, branch)")
@click.option("--sha", "target_refs", multiple=True, metavar="REF|RANGE",
              help=(
                  "Run only this commit (repeatable). "
                  "Accepts any git ref (HEAD~2, v1.0, abc123) "
                  "or a range (HEAD~5..HEAD, v1.0..v2.0)."
              ))
@click.option(
    "--strategy",
    type=click.Choice(["bisect", "linear"]),
    default="bisect",
    show_default=True,
    help="bisect: endpoints+midpoints first for quick shape; linear: oldest-to-newest",
)
@click.option("--no-test", is_flag=True, help="Skip correctness tests")
@click.option("--no-bench", is_flag=True, help="Skip benchmarks")
@click.option("--no-profile", is_flag=True, help="Skip profile_cmd even if configured")
@click.option("--all/--skip-existing", "run_all", default=False, help="Re-run already-processed commits")
@click.option("--no-live-report", is_flag=True, default=False, help="Disable per-commit report updates")
@click.option("--report", "auto_report", is_flag=True, default=False, help="Generate report after run (always on with live report)")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Open the report in your browser when done (implies --report)")
@click.option("--epoch", "epoch_override", type=int, default=None, help="Run in a specific epoch (default: current)")
@click.option(
    "-v", "--verbose", count=True,
    help=(
        "Stream command output as it runs. "
        "-v streams the bench command; "
        "-vv also streams the test command."
    ),
)
@click.option(
    "--diff", "run_diff", is_flag=True, default=False,
    help="Run configured diff tools after each commit (requires --strategy linear and [diff] config).",
)
@click.option(
    "--diff-vs",
    "diff_vs",
    multiple=True,
    type=str,
    default=("previous",),
    show_default=True,
    help=(
        "What to diff each commit against. Repeatable. "
        "'previous' = the preceding commit in the linear walk; "
        "any git ref (SHA, tag, branch) = diff vs that fixed commit. "
        "Example: --diff-vs previous --diff-vs abc1234"
    ),
)
def run(
    config: str,
    commits: int | None,
    from_ref: str | None,
    to_ref: str | None,
    target_refs: tuple[str, ...],
    strategy: str,
    no_test: bool,
    no_bench: bool,
    no_profile: bool,
    run_all: bool,
    no_live_report: bool,
    auto_report: bool,
    open_browser: bool,
    epoch_override: int | None,
    verbose: int,
    run_diff: bool,
    diff_vs: tuple[str, ...],
) -> None:
    """Walk commits on a branch, run tests and benchmarks, store results."""
    config_path = Path(config)
    if not config_path.exists() and config == CONFIG_FILE:
        _onboard(config_path)
        raise SystemExit(1)
    cfg = load_config(config_path)
    store = Store(cfg.db_path(), epoch_override=epoch_override)

    if run_diff and strategy != "linear":
        click.echo("Note: --diff is only effective with --strategy linear; ignoring.")
        run_diff = False

    try:
        run_branch(
            cfg=cfg,
            store=store,
            max_commits=commits,
            from_ref=from_ref,
            to_ref=to_ref,
            target_refs=target_refs,
            strategy=strategy,
            run_tests=not no_test,
            run_benchmarks=not no_bench,
            run_profile_cmd=not no_profile,
            run_diff=run_diff,
            diff_vs=diff_vs,
            skip_existing=not run_all,
            live_report=not no_live_report,
            verbose=verbose,
            log=lambda s: click.echo(s),
        )
    finally:
        store.close()

    if auto_report or open_browser:
        _do_report(cfg, epoch_override, open_browser=open_browser)


@main.command(name="profile")
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--commits", "-n", type=int, default=None, help="Max commits to process")
@click.option("--from", "--from-sha", "from_ref", default=None, metavar="REF")
@click.option("--to", "--to-sha", "to_ref", default=None, metavar="REF")
@click.option("--sha", "target_refs", multiple=True, metavar="REF|RANGE")
@click.option("--all/--skip-existing", "run_all", default=False, help="Re-profile even if already done")
@click.option("--no-live-report", is_flag=True, default=False)
@click.option("--epoch", "epoch_override", type=int, default=None)
@click.option("-v", "--verbose", count=True)
@click.option(
    "--diff", "run_diff", is_flag=True, default=False,
    help="Run diff tools after each commit (requires [diff] config).",
)
@click.option(
    "--diff-vs",
    "diff_vs",
    multiple=True,
    type=str,
    default=("previous",),
    show_default=True,
    help="What to diff against: 'previous' or a git ref. Repeatable.",
)
def profile_cmd(
    config: str,
    commits: int | None,
    from_ref: str | None,
    to_ref: str | None,
    target_refs: tuple[str, ...],
    run_all: bool,
    no_live_report: bool,
    epoch_override: int | None,
    verbose: int,
    run_diff: bool,
    diff_vs: tuple[str, ...],
) -> None:
    """Run profile_cmd for each commit — fixed-workload profiling for meaningful diffs.

    Unlike bench, this command only runs profile_cmd and collects flamegraphs /
    JFR files.  No primary or secondary metrics are stored.  Use a fixed iteration
    count (-bm ss -i N) in profile_cmd so both sides of a diff do identical work.
    """
    cfg = load_config(Path(config))
    if not cfg.commands.profile_cmd:
        raise click.ClickException("profile_cmd is not set in [commands].")
    store = Store(cfg.db_path(), epoch_override=epoch_override)
    try:
        profile_branch(
            cfg=cfg,
            store=store,
            max_commits=commits,
            from_ref=from_ref,
            to_ref=to_ref,
            target_refs=target_refs,
            run_diff=run_diff,
            diff_vs=diff_vs,
            skip_existing=not run_all,
            live_report=not no_live_report,
            verbose=verbose,
            log=lambda s: click.echo(s),
        )
    finally:
        store.close()


@main.command(name="diff")
@click.argument("refs", nargs=-1, required=True)
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option(
    "--diff-vs",
    "diff_vs",
    multiple=True,
    type=str,
    default=("previous",),
    show_default=True,
    help=(
        "What to diff against. Repeatable. 'previous' = adjacent pairs; "
        "any git ref = each commit vs that fixed ref. Ignored for two-SHA form."
    ),
)
@click.option("--epoch", "epoch_override", type=int, default=None)
def diff_cmd(
    refs: tuple[str, ...],
    config: str,
    diff_vs: tuple[str, ...],
    epoch_override: int | None,
) -> None:
    """Run configured diff tools between commits.

    Two-SHA form — diff exactly this pair:

      branch-bench diff SHA1 SHA2

    Range form — diff adjacent pairs and/or vs branch-base:

      branch-bench diff SHA1..SHA2 [--diff-vs previous|branch-base|both]

    Artifacts are written to the epoch assets directory and recorded in the DB
    so they appear in the static report and are served by `branch-bench serve`.
    """
    cfg = load_config(Path(config))
    if not cfg.diff.commands:
        raise click.ClickException("No [diff] commands configured in bench.toml.")

    repo_path = Path(cfg.repo.path).resolve()
    store = Store(cfg.db_path(), epoch_override=epoch_override)
    log = click.echo

    try:
        epoch = store.current_epoch()
        merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
        all_commits = git.list_commits(repo_path, cfg.repo.branch, exclude_before=merge_base)

        def _find_commit(sha_prefix: str) -> "git.Commit | None":
            for c in all_commits:
                if c.sha == sha_prefix or c.sha.startswith(sha_prefix) or c.short_sha.startswith(sha_prefix):
                    return c
            # Also try rev_parse for refs not on branch (e.g. the merge-base itself)
            full = git.rev_parse(repo_path, sha_prefix)
            if full:
                for c in all_commits:
                    if c.sha == full:
                        return c
            return None

        # ── Two-SHA form ─────────────────────────────────────────────────────
        if len(refs) == 2:
            left_ref, right_ref = refs
            left_commit = _find_commit(left_ref)
            right_commit = _find_commit(right_ref)
            if not left_commit:
                raise click.ClickException(f"Commit not found on branch: {left_ref!r}")
            if not right_commit:
                raise click.ClickException(f"Commit not found on branch: {right_ref!r}")
            log(f"Diffing {left_commit.short_sha}…{right_commit.short_sha}")
            from .runner import diff_pair as _diff_pair
            _diff_pair(
                left_sha=left_commit.sha,
                left_short_sha=left_commit.short_sha,
                left_message=left_commit.message,
                right_commit=right_commit,
                diff_vs="on-demand",
                cfg=cfg,
                store=store,
                repo_path=repo_path,
                epoch=epoch,
                branch=cfg.repo.branch,
                log=log,
            )
            return

        # ── Range / single-ref form ───────────────────────────────────────────
        if len(refs) == 1:
            ref = refs[0]
            if ".." in ref:
                shas = git.expand_range(repo_path, ref)
                if not shas:
                    raise click.ClickException(f"Range {ref!r} yielded no commits.")
                # expand_range returns newest-first; reverse for chronological
                shas = list(reversed(shas))
                range_commits = [c for sha in shas for c in [_find_commit(sha)] if c]
                if not range_commits:
                    raise click.ClickException("None of the range commits found on branch.")
            else:
                # Single SHA: diff against previous and/or branch-base
                c = _find_commit(ref)
                if not c:
                    raise click.ClickException(f"Commit not found on branch: {ref!r}")
                range_commits = [c]
        else:
            raise click.ClickException(
                "Provide either two SHAs (SHA1 SHA2) or a range (SHA1..SHA2)."
            )

        diff_range(
            cfg=cfg,
            store=store,
            commits=range_commits,
            diff_vs=diff_vs,
            repo_path=repo_path,
            epoch=epoch,
            log=log,
        )
    finally:
        store.close()


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--epoch", "epoch_override", type=int, default=None, help="Generate report for a specific epoch")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Open the report in your browser when done")
def report(config: str, epoch_override: int | None, open_browser: bool) -> None:
    """Generate an HTML report from stored results."""
    cfg = load_config(Path(config))
    _do_report(cfg, epoch_override, open_browser=open_browser)


def _do_report(cfg, epoch_override: int | None = None, open_browser: bool = False) -> None:
    repo_path = Path(cfg.repo.path).resolve()
    store = Store(cfg.db_path(), epoch_override=epoch_override)
    try:
        merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
        commits = git.list_commits(repo_path, cfg.repo.branch, exclude_before=merge_base)
        store.refresh_positions([c.sha for c in commits])
        retired = store.retire_stale_commits({c.sha for c in commits})
        if retired:
            click.echo(f"  Retired {retired} stale commit(s) no longer on branch")
        backfilled = store.backfill_by_tree_sha()
        if backfilled:
            click.echo(f"  Backfilled {backfilled} commit(s) via tree-SHA reuse")
        github_url = git.github_remote_url(repo_path, cfg.repo.branch)
        epoch = store.current_epoch()
        out = cfg.report_path(epoch)
        generate(store, out, github_url=github_url)
        generate_index(cfg)
    finally:
        store.close()
    click.echo(f"Report written to {out}")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
def show(config: str) -> None:
    """Open the HTML report in the default browser."""
    cfg = load_config(Path(config))
    store = Store(cfg.db_path())
    try:
        epoch = store.current_epoch()
    finally:
        store.close()
    p = cfg.report_path(epoch)
    if not p.exists():
        raise click.ClickException(f"{p} not found. Run `branch-bench report` first.")
    webbrowser.open(p.resolve().as_uri())


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
def epoch(config: str) -> None:
    """Start a new testing epoch — treat all commits as untested.

    Historical run data is preserved; the new epoch simply starts fresh
    so branch-bench will re-run every commit on the next `run` invocation.
    """
    cfg = load_config(Path(config))
    store = Store(cfg.db_path())
    try:
        n = store.new_epoch()
        click.echo(f"Epoch {n} started. All commits will be re-run on the next `branch-bench run`.")
    finally:
        store.close()


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
def status(config: str) -> None:
    """Show a summary of what's been collected so far."""
    cfg = load_config(Path(config))
    store = Store(cfg.db_path())
    try:
        commits = store.all_commits()
        names = store.all_benchmark_names()
        click.echo(f"Commits processed : {len(commits)}")
        click.echo(f"Benchmark variants: {len(names)}")
        for name in names:
            click.echo(f"  {name}")
    finally:
        store.close()


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--from-db", "from_db", default=None, help="Old database file to migrate from (copied to new location)")
def migrate(config: str, from_db: str | None) -> None:
    """Migrate profiles and JMH JSONs to the new epoch/slug/source/run layout.

    Files are COPIED to their new locations; originals are left in place.
    After migration, reports are regenerated for all epochs.

    If migrating from an old layout, point --from-db at the old database:

      branch-bench migrate --from-db bench-results.db
    """
    cfg = load_config(Path(config))

    if from_db:
        src = Path(from_db)
        if not src.exists():
            raise click.ClickException(f"--from-db: file not found: {src}")
        dest = cfg.db_path()
        if dest.exists():
            raise click.ClickException(
                f"{dest} already exists — remove it first if you want to re-migrate."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        click.echo(f"  Copied {src} → {dest}")

    store = Store(cfg.db_path())
    try:
        runs = store.all_runs_with_metadata()
        if not runs:
            click.echo("No runs found in database.")
            return

        run_number: dict[tuple[int, str, str], int] = {}  # (epoch, commit_sha, source) → count
        copied = skipped = 0

        for run in runs:
            source = run.get("source") or "bench"
            key = (run["epoch"], run["commit_sha"], source)
            run_number[key] = run_number.get(key, 0) + 1
            rnum = run_number[key]

            run_dir = cfg.run_assets_dir(
                run["epoch"], run["short_sha"], run["message"], rnum, source
            )
            run_dir.mkdir(parents=True, exist_ok=True)

            # Migrate JMH JSON (bench runs only)
            old_jmh = run["jmh_json_path"]
            if old_jmh:
                src = Path(old_jmh)
                dest = run_dir / "jmh-results.json"
                if src.exists() and not dest.exists():
                    shutil.copy2(src, dest)
                    rel = dest.relative_to(Path.cwd()) if dest.is_absolute() else dest
                    store.update_jmh_json_path(run["id"], str(rel))
                    copied += 1
                elif dest.exists():
                    skipped += 1

            # Migrate profiles
            for prof in store.profiles_for_migration(run["id"]):
                src_path = Path(prof["file_path"])
                dest = run_dir / src_path.name
                if src_path.exists() and not dest.exists():
                    shutil.copy2(src_path, dest)
                    rel = dest.relative_to(Path.cwd()) if dest.is_absolute() else dest
                    store.update_profile_path(prof["id"], str(rel))
                    copied += 1
                elif dest.exists():
                    skipped += 1

        click.echo(f"  Copied {copied} file(s), skipped {skipped} already-migrated.")

        # Regenerate reports for all epochs
        github_url = git.github_remote_url(Path(cfg.repo.path).resolve(), cfg.repo.branch)
        for ep in store.all_epochs():
            ep_store = Store(cfg.db_path(), epoch_override=ep)
            try:
                out = cfg.report_path(ep)
                generate(ep_store, out, github_url=github_url)
                click.echo(f"  Report regenerated: {out}")
            finally:
                ep_store.close()

        generate_index(cfg)
        click.echo("Migration complete. Original files have not been deleted.")
    finally:
        store.close()


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--port", default=7823, show_default=True, help="Port to listen on")
@click.option("--epoch", "epoch_override", type=int, default=None, help="Serve a specific epoch")
def serve(config: str, port: int, epoch_override: int | None) -> None:
    """Serve the report with an HTTP API for on-demand flamegraph diffing.

    Unlike the static report, the server can run diff tools on request when
    you select two commits in the UI.  Pre-computed AOT diffs (from
    `branch-bench diff` or `--diff`) are always available statically.
    """
    cfg = load_config(Path(config))
    store = Store(cfg.db_path(), epoch_override=epoch_override)
    try:
        epoch = store.current_epoch()
    finally:
        store.close()

    epoch_dir = cfg.epoch_dir(epoch)
    report_html = cfg.report_path(epoch)

    if not report_html.exists():
        raise click.ClickException(
            f"{report_html} not found. Run `branch-bench report` first."
        )

    _run_server(cfg, epoch, epoch_dir, report_html, port)


def _run_server(cfg, epoch: int, epoch_dir: Path, report_html: Path, port: int) -> None:
    """Start a stdlib-based HTTP server with diff API."""
    import http.server
    import json
    import mimetypes
    import urllib.parse

    from .runner import diff_pair as _diff_pair
    from .storage import Store as _Store

    # Inject server-mode flag into the report HTML at serve time
    _report_source = report_html.read_text(encoding="utf-8")
    _report_injected = _report_source.replace(
        "window.__runLog=[];",
        f"window.__runLog=[];\nwindow.__serverMode=true;\nwindow.__serverEpoch={epoch};",
        1,
    )

    repo_path = Path(cfg.repo.path).resolve()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silence default access log

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.lstrip("/")

            if path in ("", "index.html"):
                self._send(200, "text/html; charset=utf-8", _report_injected.encode())
                return

            # Static assets relative to epoch_dir
            candidate = (epoch_dir / path).resolve()
            try:
                candidate.relative_to(epoch_dir.resolve())
            except ValueError:
                self._send(403, "text/plain", b"Forbidden")
                return

            if candidate.is_file():
                mime, _ = mimetypes.guess_type(str(candidate))
                self._send(200, mime or "application/octet-stream", candidate.read_bytes())
            else:
                self._send(404, "text/plain", b"Not found")

        def do_POST(self):
            if self.path != "/api/diff":
                self._send(404, "text/plain", b"Not found")
                return

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            left_sha = body.get("left_sha", "")
            right_sha = body.get("right_sha", "")

            if not left_sha or not right_sha:
                self._send(400, "application/json", json.dumps({"error": "left_sha and right_sha required"}).encode())
                return

            store = _Store(cfg.db_path(), epoch_override=epoch)
            try:
                # Find commits
                all_commits = git.list_commits(
                    repo_path, cfg.repo.branch,
                    exclude_before=git.find_merge_base(repo_path, cfg.repo.branch),
                )
                left_commit = next((c for c in all_commits if c.sha == left_sha or c.sha.startswith(left_sha)), None)
                right_commit = next((c for c in all_commits if c.sha == right_sha or c.sha.startswith(right_sha)), None)

                if not left_commit or not right_commit:
                    self._send(404, "application/json", json.dumps({"error": "commit not found"}).encode())
                    return

                messages: list[str] = []
                _diff_pair(
                    left_sha=left_commit.sha,
                    left_short_sha=left_commit.short_sha,
                    left_message=left_commit.message,
                    right_commit=right_commit,
                    diff_vs="on-demand",
                    cfg=cfg,
                    store=store,
                    repo_path=repo_path,
                    epoch=epoch,
                    branch=cfg.repo.branch,
                    log=messages.append,
                )

                raw_diffs = store.diffs_for_right_sha(right_sha)
                # Rebase diff_path to be epoch-dir-relative so the browser
                # can request them from this server (which serves under epoch_dir).
                epoch_dir_resolved = epoch_dir.resolve()
                rebased = []
                for d in raw_diffs:
                    try:
                        rel = Path(d["diff_path"]).resolve().relative_to(epoch_dir_resolved)
                        rebased.append({**d, "diff_path": str(rel)})
                    except ValueError:
                        rebased.append(d)
                resp = json.dumps({"diffs": rebased, "log": messages}).encode()
                self._send(200, "application/json", resp)
            finally:
                store.close()

    server = http.server.HTTPServer(("", port), Handler)
    click.echo(f"Serving epoch {epoch} at http://localhost:{port}/")
    click.echo("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
