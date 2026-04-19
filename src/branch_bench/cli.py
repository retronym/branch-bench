from __future__ import annotations

import shutil
import webbrowser
from pathlib import Path

import click

from .config import load_config, commit_slug, TEMPLATE
from .runner import run_branch
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
@click.option("--from-sha", default=None, help="Start from this commit SHA")
@click.option("--to-sha", default=None, help="Stop at this commit SHA")
@click.option("--sha", "target_shas", multiple=True, metavar="SHA", help="Run only this commit (repeatable; prefix match)")
@click.option(
    "--strategy",
    type=click.Choice(["bisect", "linear"]),
    default="bisect",
    show_default=True,
    help="bisect: endpoints+midpoints first for quick shape; linear: oldest-to-newest",
)
@click.option("--no-test", is_flag=True, help="Skip correctness tests")
@click.option("--no-bench", is_flag=True, help="Skip benchmarks")
@click.option("--all/--skip-existing", "run_all", default=False, help="Re-run already-processed commits")
@click.option("--no-live-report", is_flag=True, default=False, help="Disable per-commit report updates")
@click.option("--report", "auto_report", is_flag=True, default=False, help="Generate report after run (always on with live report)")
@click.option("--epoch", "epoch_override", type=int, default=None, help="Run in a specific epoch (default: current)")
def run(
    config: str,
    commits: int | None,
    from_sha: str | None,
    to_sha: str | None,
    target_shas: tuple[str, ...],
    strategy: str,
    no_test: bool,
    no_bench: bool,
    run_all: bool,
    no_live_report: bool,
    auto_report: bool,
    epoch_override: int | None,
) -> None:
    """Walk commits on a branch, run tests and benchmarks, store results."""
    cfg = load_config(Path(config))
    store = Store(cfg.db_path(), epoch_override=epoch_override)

    try:
        run_branch(
            cfg=cfg,
            store=store,
            max_commits=commits,
            from_sha=from_sha,
            to_sha=to_sha,
            target_shas=target_shas,
            strategy=strategy,
            run_tests=not no_test,
            run_benchmarks=not no_bench,
            skip_existing=not run_all,
            live_report=not no_live_report,
            log=lambda s: click.echo(s),
        )
    finally:
        store.close()

    if auto_report:
        _do_report(cfg, epoch_override)


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
@click.option("--epoch", "epoch_override", type=int, default=None, help="Generate report for a specific epoch")
def report(config: str, epoch_override: int | None) -> None:
    """Generate an HTML report from stored results."""
    cfg = load_config(Path(config))
    _do_report(cfg, epoch_override)


def _do_report(cfg, epoch_override: int | None = None) -> None:
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
        epoch = store.current_epoch()
        out = cfg.report_path(epoch)
        generate(store, out)
        generate_index(cfg)
    finally:
        store.close()
    click.echo(f"Report written to {out}")


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
    """Migrate profiles and JMH JSONs to the new epoch/slug/run layout.

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

        # Group by (epoch, commit_sha) to compute per-commit run numbers
        # all_runs_with_metadata is ordered by epoch, commit_sha, run_at ASC
        run_number: dict[tuple[int, str], int] = {}
        copied = skipped = 0

        for run in runs:
            key = (run["epoch"], run["commit_sha"])
            run_number[key] = run_number.get(key, 0) + 1
            rnum = run_number[key]

            run_dir = cfg.run_assets_dir(
                run["epoch"], run["short_sha"], run["message"], rnum
            )
            run_dir.mkdir(parents=True, exist_ok=True)

            # Migrate JMH JSON
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
                src = Path(prof["file_path"])
                dest = run_dir / src.name
                if src.exists() and not dest.exists():
                    shutil.copy2(src, dest)
                    rel = dest.relative_to(Path.cwd()) if dest.is_absolute() else dest
                    store.update_profile_path(prof["id"], str(rel))
                    copied += 1
                elif dest.exists():
                    skipped += 1

        click.echo(f"  Copied {copied} file(s), skipped {skipped} already-migrated.")

        # Regenerate reports for all epochs
        for ep in store.all_epochs():
            ep_store = Store(cfg.db_path(), epoch_override=ep)
            try:
                out = cfg.report_path(ep)
                generate(ep_store, out)
                click.echo(f"  Report regenerated: {out}")
            finally:
                ep_store.close()

        generate_index(cfg)
        click.echo("Migration complete. Original files have not been deleted.")
    finally:
        store.close()
