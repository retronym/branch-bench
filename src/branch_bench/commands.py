from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from .storage import BenchmarkResult, SecondaryMetric, TestResult


def _run_cmd(
    cmd: str,
    cwd: Path,
    tee: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run *cmd* in *cwd*, return (returncode, combined_output).

    When *tee* is provided the command's stdout and stderr are merged and each
    line is passed to *tee* as it arrives (useful for live progress display).
    The same text is always accumulated and returned as the second value.
    """
    if tee is None:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    # Streaming mode: merge stderr into stdout, tee each line in real time.
    proc = subprocess.Popen(
        cmd, shell=True, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        tee(line)
        lines.append(line)
    proc.wait()
    return proc.returncode, "".join(lines)


def run_test(
    cmd: str,
    cwd: Path,
    tee: Callable[[str], None] | None = None,
) -> TestResult:
    start = time.monotonic()
    returncode, output = _run_cmd(cmd, cwd, tee=tee)
    duration = time.monotonic() - start
    return TestResult(
        passed=returncode == 0,
        duration_seconds=duration,
        output=output,
    )


def run_bench(
    bench_cmd: str,
    cwd: Path,
    jmh_save_dir: Path | None = None,
    jmh_save_name: str = "results",
    tee: Callable[[str], None] | None = None,
) -> tuple[list[BenchmarkResult], list[Path], str, Path | None]:
    """Run bench_cmd, return (benchmark_results, artifact_paths, raw_output, saved_json_path).

    Substitutions available in bench_cmd:
      {out}     — path to a temp file where JMH should write JSON results (-rff {out})
      {out_dir} — path to a temp directory for profiler output (dir={out_dir})
    All files found in {out_dir} are returned as artifact paths.
    If jmh_save_dir is given the JSON is copied there for posterity.
    When *tee* is given, stdout+stderr are streamed to it line-by-line in real time.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = Path(f.name)

    with tempfile.TemporaryDirectory() as out_dir:
        out_dir_path = Path(out_dir)

        cmd = bench_cmd.replace("{out}", str(out_path)).replace("{out_dir}", str(out_dir_path))
        returncode, raw_output = _run_cmd(cmd, cwd, tee=tee)

        if returncode != 0:
            raise RuntimeError(
                f"Benchmark command failed (exit {returncode}):\n{raw_output}",
                raw_output,
            )

        saved_json: Path | None = None
        if jmh_save_dir is not None:
            jmh_save_dir.mkdir(parents=True, exist_ok=True)
            saved_json = jmh_save_dir / f"{jmh_save_name}.json"
            shutil.copy2(out_path, saved_json)

        bench_results = parse_jmh_json(out_path)

        artifacts = sorted(p for p in out_dir_path.rglob("*") if p.is_file())
        kept: list[Path] = []
        for artifact in artifacts:
            dest = out_path.parent / artifact.name
            shutil.copy2(artifact, dest)
            kept.append(dest)

    return bench_results, kept, raw_output, saved_json


def parse_jmh_json(path: Path) -> list[BenchmarkResult]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        snippet = _json_snippet(raw, e)
        raise RuntimeError(
            f"Failed to parse JMH JSON output ({path.name}):\n  {e.msg} (line {e.lineno}, col {e.colno})\n\n{snippet}\n\nFull file: {path}",
            "",
        ) from e

    results = []
    for entry in data:
        metric = entry.get("primaryMetric", {})
        params = entry.get("params") or None
        nested = metric.get("rawData") or []
        flat_raw = [v for fork in nested for v in fork] or None

        secondary: list[SecondaryMetric] = []
        for name, sm in (entry.get("secondaryMetrics") or {}).items():
            # Skip non-numeric metrics (e.g. -prof stack produces text stack traces
            # with score "NaN" and string rawData).
            try:
                score_val = float(sm.get("score", 0) or 0)
            except (TypeError, ValueError):
                continue
            if math.isnan(score_val):
                continue

            sm_nested = sm.get("rawData") or []
            sm_raw_flat = [v for fork in sm_nested for v in fork]
            # Only keep raw data when it is numeric (stack profiler sends strings).
            sm_raw: list | None = None
            if sm_raw_flat:
                try:
                    sm_raw = [float(v) for v in sm_raw_flat]
                except (TypeError, ValueError):
                    sm_raw = None

            secondary.append(SecondaryMetric(
                metric=name,
                score=score_val,
                score_error=float(sm["scoreError"]) if sm.get("scoreError") not in (None, "NaN") else None,
                unit=sm.get("scoreUnit", ""),
                raw_data=sm_raw,
            ))

        results.append(
            BenchmarkResult(
                benchmark=entry["benchmark"],
                mode=entry.get("mode", ""),
                score=float(metric.get("score", 0)),
                score_error=float(metric["scoreError"]) if metric.get("scoreError") not in (None, "NaN") else None,
                unit=metric.get("scoreUnit", ""),
                params=params,
                raw_data=flat_raw,
                secondary_metrics=secondary or None,
            )
        )
    return results


def _json_snippet(src: str, e: json.JSONDecodeError) -> str:
    lines = src.splitlines()
    lineno = e.lineno  # 1-based
    col = e.colno      # 1-based
    # Show up to 2 lines of context before the error line
    start = max(0, lineno - 3)
    pad = len(str(lineno))
    out_lines = []
    for i, line in enumerate(lines[start:lineno], start=start + 1):
        prefix = f"  {i:{pad}} | "
        out_lines.append(f"{prefix}{line[:200]}")  # cap long lines
    out_lines.append(f"  {'':{pad}} | {' ' * (col - 1)}^")
    return "\n".join(out_lines)
