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
    assert cfg.output.db == ".branchbench/bench-results.db"


def test_load_custom():
    content = """
[repo]
path = "/tmp/myrepo"
branch = "perf-branch"

[commands]
test_cmd = "./mill tests.jvm.2_13_16.test"
bench_cmd = "./mill benchmark.runJmh MyBench -rff {out} -prof async:libPath=/path/libasyncProfiler.dylib;dir={out_dir} -wi 5 -i 5 -f1"

[output]
db = "results.db"
report = "out.html"
profiles_dir = "flame"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        f.write(content)
        path = Path(f.name)

    cfg = load_config(path)
    assert cfg.repo.branch == "perf-branch"
    assert "mill" in cfg.commands.test_cmd
    assert "{out}" in cfg.commands.bench_cmd
    assert "{out_dir}" in cfg.commands.bench_cmd
    assert cfg.output.profiles_dir == "flame"
