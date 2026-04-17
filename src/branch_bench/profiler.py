from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

from .commands import parse_jmh_json
from .storage import BenchmarkResult


def build_prof_arg(lib: str, event: str, svg_path: str) -> str:
    """Build a JMH -prof async: argument string."""
    return f"async:libPath={lib};event={event};output=flamegraph;dir={Path(svg_path).parent}"


def run_bench_with_profile(
    bench_cmd: str,
    cwd: Path,
    lib: str,
    event: str,
    svg_path: str,
) -> list[BenchmarkResult]:
    """Run bench_cmd with JMH async profiler for one event, return benchmark results."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name

    prof_arg = build_prof_arg(lib, event, svg_path)
    cmd = bench_cmd.replace("{out}", out_path) + f" -prof '{prof_arg}'"

    result = subprocess.run(shlex.split(cmd), cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Profiled benchmark failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )

    # async-profiler writes <benchmark>-<event>-flamegraph.svg into the dir
    svg_dir = Path(svg_path).parent
    svgs = list(svg_dir.glob(f"*{event}*flamegraph*.svg"))
    if svgs:
        # Move the first found svg to the desired path
        svgs[0].rename(svg_path)

    return parse_jmh_json(Path(out_path))
