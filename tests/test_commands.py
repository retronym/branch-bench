import json
import os
import tempfile
from pathlib import Path

import pytest

from branch_bench.commands import parse_jmh_json, run_profile, run_diff_tool
from branch_bench.storage import BenchmarkResult

JMH_SAMPLE = [
    {
        "benchmark": "com.example.MyBench.measure",
        "mode": "thrpt",
        "params": None,
        "primaryMetric": {
            "score": 1234.56,
            "scoreError": 12.3,
            "scoreUnit": "ops/s",
        },
    },
    {
        "benchmark": "com.example.MyBench.measure",
        "mode": "thrpt",
        "params": {"size": "100"},
        "primaryMetric": {
            "score": 999.0,
            "scoreError": "NaN",
            "scoreUnit": "ops/s",
        },
    },
]


def test_parse_jmh_json():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(JMH_SAMPLE, f)
        path = Path(f.name)

    results = parse_jmh_json(path)
    assert len(results) == 2

    r0 = results[0]
    assert r0.benchmark == "com.example.MyBench.measure"
    assert r0.mode == "thrpt"
    assert r0.score == 1234.56
    assert r0.score_error == 12.3
    assert r0.unit == "ops/s"
    assert r0.params is None

    r1 = results[1]
    assert r1.score_error is None  # NaN -> None
    assert r1.params == {"size": "100"}


def test_run_profile_collects_artifacts():
    with tempfile.TemporaryDirectory() as cwd:
        cwd_path = Path(cwd)
        cmd = 'sh -c \'touch "$1/flamegraph.svg" "$1/data.jfr"\' -- {out_dir}'
        artifacts, output = run_profile(cmd, cwd_path)
        names = {p.name for p in artifacts}
        assert "flamegraph.svg" in names
        assert "data.jfr" in names


def test_run_profile_no_artifacts():
    with tempfile.TemporaryDirectory() as cwd:
        artifacts, output = run_profile("true", Path(cwd))
        assert artifacts == []


def test_run_profile_raises_on_failure():
    with tempfile.TemporaryDirectory() as cwd:
        with pytest.raises(RuntimeError, match="Profile command failed"):
            run_profile("false", Path(cwd))


def test_run_diff_tool_env_vars_and_output():
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as out_d:
        cwd_path = Path(cwd)
        out_dir = Path(out_d)
        left = cwd_path / "left.svg"
        right = cwd_path / "right.svg"
        left.write_text("L")
        right.write_text("R")

        cmd = (
            'sh -c \'echo "$LEFT_SHA $RIGHT_SHA" > "$OUT_DIR/diff.html" && '
            'echo "$LEFT_BRANCH $RIGHT_BRANCH" > "$OUT_DIR/reverse-diff.html"\''
        )
        files, output = run_diff_tool(
            cmd,
            left_file=left, left_sha="aaa", left_commit_msg="left msg", left_branch="main",
            right_file=right, right_sha="bbb", right_commit_msg="right msg", right_branch="feat",
            out_dir=out_dir, cwd=cwd_path,
        )
        names = {p.name for p in files}
        assert names == {"diff.html", "reverse-diff.html"}
        assert (out_dir / "diff.html").read_text().strip() == "aaa bbb"
        assert (out_dir / "reverse-diff.html").read_text().strip() == "main feat"


def test_run_diff_tool_zero_output():
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as out_d:
        files, _ = run_diff_tool(
            "true",
            left_file=Path(cwd) / "l.svg", left_sha="aaa", left_commit_msg="", left_branch="main",
            right_file=Path(cwd) / "r.svg", right_sha="bbb", right_commit_msg="", right_branch="feat",
            out_dir=Path(out_d), cwd=Path(cwd),
        )
        assert files == []


def test_run_diff_tool_raises_on_failure():
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as out_d:
        with pytest.raises(RuntimeError, match="Diff command failed"):
            run_diff_tool(
                "false",
                left_file=Path(cwd) / "l.svg", left_sha="aaa", left_commit_msg="", left_branch="main",
                right_file=Path(cwd) / "r.svg", right_sha="bbb", right_commit_msg="", right_branch="feat",
                out_dir=Path(out_d), cwd=Path(cwd),
            )


def test_run_diff_tool_passes_absolute_paths_when_cwd_differs(tmp_path, monkeypatch):
    # Mirrors `run --diff` with a worktree: paths are relative to the process cwd
    # (repo root) but the tool runs inside the commit's checkout.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "left.jfr").write_text("L")
    (tmp_path / "right.jfr").write_text("R")
    checkout = tmp_path / ".bench" / "worktrees" / "abc"
    checkout.mkdir(parents=True)

    files, _ = run_diff_tool(
        'cat "$LEFT_FILE" "$RIGHT_FILE" > "$OUT_DIR/diff.txt"',
        left_file=Path("left.jfr"),
        left_sha="a" * 40,
        left_commit_msg="left",
        left_branch="main",
        right_file=Path("right.jfr"),
        right_sha="b" * 40,
        right_commit_msg="right",
        right_branch="main",
        out_dir=Path(".bench/epoch-1/assets/diffs/a-b/cpu"),
        cwd=checkout,
    )
    assert [f.name for f in files] == ["diff.txt"]
    assert files[0].read_text() == "LR"
    assert files[0].resolve() == (tmp_path / ".bench/epoch-1/assets/diffs/a-b/cpu/diff.txt").resolve()
