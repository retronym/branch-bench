import tempfile
from pathlib import Path

from branch_bench.config import load_config, TEMPLATE


def test_load_template():
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        f.write(TEMPLATE)
        path = Path(f.name)

    cfg = load_config(path)
    assert cfg.repo.path == "."
    assert cfg.repo.branch == "main"
    assert cfg.commands.test_cmd == ""
    assert cfg.commands.bench_cmd == ""
    assert cfg.commands.profile_cmd == ""
    assert cfg.diff.commands == {}
    assert cfg.output.dir == ".bench"


def test_load_custom():
    content = """
[repo]
path = "/tmp/myrepo"
branch = "perf-branch"

[commands]
test_cmd = "./mill tests.jvm.2_13_16.test"
bench_cmd = "./mill benchmark.runJmh MyBench -rff {out} -prof async:libPath=/path/libasyncProfiler.dylib;dir={out_dir} -wi 5 -i 5 -f1"
profile_cmd = "./mill benchmark.runJmh MyBench -bm ss -wi 20 -i 5000 -f1 -prof async:dir={out_dir}"

[output]
dir = "out"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        f.write(content)
        path = Path(f.name)

    cfg = load_config(path)
    assert cfg.repo.branch == "perf-branch"
    assert "mill" in cfg.commands.test_cmd
    assert "{out}" in cfg.commands.bench_cmd
    assert "{out_dir}" in cfg.commands.bench_cmd
    assert "-bm ss" in cfg.commands.profile_cmd
    assert cfg.output.dir == "out"
    assert cfg.diff.commands == {}


def test_load_diff_config():
    content = """
[repo]
branch = "main"

[commands]
bench_cmd = "./run.sh"

[diff]
svg = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"
jfr = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        f.write(content)
        path = Path(f.name)

    cfg = load_config(path)
    assert "svg" in cfg.diff.commands
    assert "jfr" in cfg.diff.commands
    assert "jfrconv" in cfg.diff.commands["svg"]
    assert "$OUT_DIR" in cfg.diff.commands["svg"]


def test_run_assets_dir_includes_source():
    from branch_bench.config import Config
    cfg = Config()
    p_bench = cfg.run_assets_dir(1, "abc12345", "my commit", 1, "bench")
    p_profile = cfg.run_assets_dir(1, "abc12345", "my commit", 1, "profile")
    assert "bench" in str(p_bench)
    assert "profile" in str(p_profile)
    assert p_bench != p_profile
    assert p_bench.name == "run-1"
    assert p_profile.name == "run-1"


def test_diff_assets_dir():
    from branch_bench.config import Config
    cfg = Config()
    d = cfg.diff_assets_dir(1, "abc12345", "def67890", "cpu-forward")
    assert "diffs" in str(d)
    assert "abc12345-def67890" in str(d)
    assert d.name == "cpu-forward"
