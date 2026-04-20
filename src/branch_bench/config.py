from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoConfig:
    path: str = "."
    branch: str = "main"


@dataclass
class CommandsConfig:
    test_cmd: str = ""
    # {out} is substituted with a temp path for JMH JSON output
    # {out_dir} is substituted with a temp dir for profiler output (SVGs)
    bench_cmd: str = ""


@dataclass
class OutputConfig:
    dir: str = ".bench"


def commit_slug(short_sha: str, message: str) -> str:
    """Return a filesystem-safe slug: '{short_sha}-{sanitized-message}'."""
    msg = re.sub(r"[^a-z0-9]+", "-", message.lower()).strip("-")[:40].rstrip("-")
    return f"{short_sha}-{msg}"


@dataclass
class Config:
    repo: RepoConfig = field(default_factory=RepoConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def base_dir(self) -> Path:
        return Path(self.output.dir)

    def db_path(self) -> Path:
        return self.base_dir() / "bench.db"

    def index_path(self) -> Path:
        return self.base_dir() / "index.html"

    def epoch_dir(self, epoch: int) -> Path:
        return self.base_dir() / f"epoch-{epoch}"

    def report_path(self, epoch: int) -> Path:
        return self.epoch_dir(epoch) / "index.html"

    def run_assets_dir(self, epoch: int, short_sha: str, message: str, run_number: int) -> Path:
        return self.epoch_dir(epoch) / "assets" / commit_slug(short_sha, message) / f"run-{run_number}"


def _toml_error(path: Path, e: Exception) -> str:
    import re as _re
    msg = e.args[0] if e.args else str(e)
    m = _re.search(r"\(at line (\d+), column (\d+)\)", msg)
    if m:
        lineno, col = int(m.group(1)), int(m.group(2))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            line = lines[lineno - 1] if lineno <= len(lines) else ""
            pad = len(str(lineno))
            bare_msg = msg[:m.start()].strip()
            return (
                f"Error: invalid TOML in {path} (line {lineno}, col {col}):\n"
                f"  {bare_msg}\n\n"
                f"  {lineno:{pad}} | {line}\n"
                f"  {'':{pad}} | {' ' * (col - 1)}^"
            )
        except OSError:
            pass
    return f"Error: invalid TOML in {path}:\n  {msg}"


def load_config(path: Path) -> Config:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Error: config file not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(_toml_error(path, e))

    repo = RepoConfig(**data.get("repo", {}))
    commands = CommandsConfig(**data.get("commands", {}))
    output = OutputConfig(**data.get("output", {}))
    return Config(repo=repo, commands=commands, output=output)


TEMPLATE = """\
[repo]
path = "."
branch = "main"

[commands]
# Shell command run from repo root; exit code determines pass/fail.
test_cmd = ""
# Two substitutions are available:
#   {out}     — temp file path for JMH JSON results        (-rff {out})
#   {out_dir} — temp directory path for profiler output    (dir={out_dir})
# Any *.svg / *.html files written into {out_dir} are collected as flamegraphs.
# Example (Mill + async-profiler):
#   bench_cmd = "./mill foo.jmh.run -- -rff {out} -prof async:libPath=/path/to/libasyncProfiler.dylib;dir={out_dir} -wi 5 -i 5 -f1"
bench_cmd = ""

[output]
dir = ".bench"
"""
