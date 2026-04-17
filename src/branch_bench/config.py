from __future__ import annotations

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
    bench_cmd: str = ""


@dataclass
class OutputConfig:
    db: str = ".branchbench/bench-results.db"
    report: str = ".branchbench/report.html"
    profiles_dir: str = ".branchbench/profiles"


@dataclass
class Config:
    repo: RepoConfig = field(default_factory=RepoConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _toml_error(path: Path, e: Exception) -> str:
    import re
    msg = e.args[0] if e.args else str(e)
    # CPython embeds position as "(at line N, column M)" in the message
    m = re.search(r"\(at line (\d+), column (\d+)\)", msg)
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
# Any *.svg files written into {out_dir} are automatically collected as flamegraphs.
# Example (Mill + async-profiler):
#   bench_cmd = "./mill foo.jmh.run -- -rff {out} -prof async:libPath=/path/to/libasyncProfiler.dylib;dir={out_dir} -wi 5 -i 5 -f1"
bench_cmd = ""

[output]
db = ".branchbench/bench-results.db"
report = ".branchbench/report.html"
profiles_dir = ".branchbench/profiles"
"""
