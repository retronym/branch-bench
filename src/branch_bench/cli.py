from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

import click

from .config import load_config, TEMPLATE
from .runner import run_branch
from .report import generate
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
def run(
    config: str,
    commits: int | None,
    from_sha: str | None,
    to_sha: str | None,
    strategy: str,
    no_test: bool,
    no_bench: bool,
    run_all: bool,
    no_live_report: bool,
    auto_report: bool,
) -> None:
    """Walk commits on a branch, run tests and benchmarks, store results."""
    cfg = load_config(Path(config))
    store = Store(Path(cfg.output.db))

    try:
        run_branch(
            cfg=cfg,
            store=store,
            max_commits=commits,
            from_sha=from_sha,
            to_sha=to_sha,
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
        _do_report(cfg)


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
def report(config: str) -> None:
    """Generate an HTML report from stored results."""
    cfg = load_config(Path(config))
    _do_report(cfg)


def _do_report(cfg) -> None:
    repo_path = Path(cfg.repo.path).resolve()
    store = Store(Path(cfg.output.db))
    out = Path(cfg.output.report)
    try:
        merge_base = git.find_merge_base(repo_path, cfg.repo.branch)
        commits = git.list_commits(repo_path, cfg.repo.branch, exclude_before=merge_base)
        retired = store.retire_stale_commits({c.sha for c in commits})
        if retired:
            click.echo(f"  Retired {retired} stale commit(s) no longer on branch")
        generate(store, out)
    finally:
        store.close()
    click.echo(f"Report written to {out}")


@main.command()
@click.option("--config", default=CONFIG_FILE, help="Path to bench.toml")
def show(config: str) -> None:
    """Open the HTML report in the default browser."""
    cfg = load_config(Path(config))
    p = Path(cfg.output.report)
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
    store = Store(Path(cfg.output.db))
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
    store = Store(Path(cfg.output.db))
    try:
        commits = store.all_commits()
        names = store.all_benchmark_names()
        click.echo(f"Commits processed : {len(commits)}")
        click.echo(f"Benchmark variants: {len(names)}")
        for name in names:
            click.echo(f"  {name}")
    finally:
        store.close()
