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
    # {out_dir} is substituted with a temp dir for profiler output (SVGs, JFRs, …)
    bench_cmd: str = ""
    # Fixed-workload profiling — use JMH SingleShotTime (-bm ss) with a fixed iteration
    # count so both sides of a diff do identical amounts of work, making flamegraph
    # percentage comparisons meaningful.  Secondary metrics are NOT collected from this
    # command; use bench_cmd for those.
    profile_cmd: str = ""


@dataclass
class DiffConfig:
    # Maps file extension → shell command.  branch-bench calls the command once per
    # matched artifact pair with the following environment variables set:
    #   LEFT_FILE, LEFT_SHA, LEFT_COMMIT_MSG, LEFT_BRANCH
    #   RIGHT_FILE, RIGHT_SHA, RIGHT_COMMIT_MSG, RIGHT_BRANCH
    #   OUT_DIR  — write 0-N output files here (any name / extension)
    # Exit 0 = success; non-zero = failure (logged, pair skipped, run continues).
    commands: dict[str, str] = field(default_factory=dict)


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
    diff: DiffConfig = field(default_factory=DiffConfig)

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

    def run_assets_dir(
        self,
        epoch: int,
        short_sha: str,
        message: str,
        run_number: int,
        source: str = "bench",
    ) -> Path:
        """Return the directory for a single run's artifacts.

        Assets are stored under ``<source>/run-N/`` so bench and profile
        artifacts never collide and their origin is unambiguous on disk.
        """
        return (
            self.epoch_dir(epoch)
            / "assets"
            / commit_slug(short_sha, message)
            / source
            / f"run-{run_number}"
        )

    def diff_assets_dir(
        self,
        epoch: int,
        left_sha8: str,
        right_sha8: str,
        artifact_stem: str,
    ) -> Path:
        """Directory for diff tool output for one (left, right, artifact) triple."""
        return (
            self.epoch_dir(epoch)
            / "assets"
            / "diffs"
            / f"{left_sha8}-{right_sha8}"
            / artifact_stem
        )


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
    # [diff] keys are arbitrary extension strings, not fixed field names.
    diff = DiffConfig(commands=data.get("diff", {}))
    return Config(repo=repo, commands=commands, output=output, diff=diff)


TEMPLATE = """\
[repo]
path = "."
branch = "main"

[commands]
# Shell command run from repo root; exit code determines pass/fail.
test_cmd = ""

# Primary benchmarking command.  Two substitutions are available:
#   {out}     — temp file path for JMH JSON results        (-rff {out})
#   {out_dir} — temp directory path for profiler output    (dir={out_dir})
# Any files written into {out_dir} are collected as profiler artifacts.
# Primary metrics, secondary metrics (-prof gc), and incidental artifacts
# are all captured from this command.
# Example (Mill + async-profiler):
#   bench_cmd = "./mill foo.jmh.run -- -rff {out} -prof async:libPath=/path/to/libasyncProfiler.dylib;dir={out_dir} -wi 5 -i 5 -f1"
bench_cmd = ""

# Fixed-workload profiling for meaningful flamegraph diffs.
# Use JMH SingleShotTime (-bm ss) with a fixed iteration count so that both
# sides of a diff perform exactly the same number of operations — making
# percentage-based flamegraph comparisons structurally valid.
# Secondary metrics are NOT collected from this command.
# Example:
#   profile_cmd = "./mill foo.jmh.run -- -bm ss -wi 20 -i 5000 -f1 -prof async:libPath=/path/to/libasyncProfiler.dylib;dir={out_dir};event=cpu;output=flamegraph"
profile_cmd = ""

# [diff]
# Maps file extension to a shell command for diffing a pair of artifacts.
# branch-bench calls it with env vars:
#   LEFT_FILE, LEFT_SHA, LEFT_COMMIT_MSG, LEFT_BRANCH
#   RIGHT_FILE, RIGHT_SHA, RIGHT_COMMIT_MSG, RIGHT_BRANCH
#   OUT_DIR  — write any number of output files here
# Exit 0 = success; non-zero = skipped (logged).
# Example (jfrconv):
#   svg = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"
#   jfr = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"

[output]
dir = ".bench"
"""
