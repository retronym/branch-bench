import tempfile
from pathlib import Path

from branch_bench.storage import Store, BenchmarkResult, TestResult


def make_store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Store(Path(f.name))


def test_save_and_query_commit():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "initial commit", "dev@example.com", 1700000000, "main")
    commits = store.all_commits()
    assert len(commits) == 1
    assert commits[0]["sha"] == "abc123"


def test_create_run():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id = store.create_run("abc123", bench_cmd="./mill foo.jmh.run", test_cmd="./mill foo.test")
    assert isinstance(run_id, int)
    assert store.has_runs("abc123")
    assert not store.has_runs("unknown")

    runs = store.runs_for_commit("abc123")
    assert len(runs) == 1
    assert runs[0]["bench_cmd"] == "./mill foo.jmh.run"
    assert runs[0]["test_cmd"] == "./mill foo.test"


def test_epoch_resets_has_runs():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    store.create_run("abc123", bench_cmd=None, test_cmd=None)
    assert store.has_runs("abc123")

    n = store.new_epoch()
    assert n == 2
    assert not store.has_runs("abc123")  # new epoch — no runs yet

    # Old runs still accessible via direct run id (data preserved)
    runs_epoch1 = store._conn.execute(
        "SELECT id FROM runs WHERE commit_sha='abc123' AND epoch=1"
    ).fetchall()
    assert len(runs_epoch1) == 1


def test_multiple_runs_per_commit():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id_1 = store.create_run("abc123", bench_cmd="cmd v1", test_cmd=None)
    run_id_2 = store.create_run("abc123", bench_cmd="cmd v2", test_cmd=None)

    results = [BenchmarkResult("com.Bench.run", "thrpt", 1000.0, 10.0, "ops/s")]
    store.save_benchmark_results(run_id_1, results)
    store.save_benchmark_results(run_id_2, [BenchmarkResult("com.Bench.run", "thrpt", 1100.0, 11.0, "ops/s")])

    runs = store.runs_for_commit("abc123")
    assert len(runs) == 2
    assert store.benchmark_results_for(run_id_1)[0]["score"] == 1000.0
    assert store.benchmark_results_for(run_id_2)[0]["score"] == 1100.0


def test_save_test_run():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id = store.create_run("abc123", bench_cmd=None, test_cmd=None)
    store.save_test_run(run_id, TestResult(passed=True, duration_seconds=1.5, output="ok"))
    result = store.test_run_for(run_id)
    assert result is not None
    assert result["passed"] == 1


def test_benchmark_names():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id = store.create_run("abc123", bench_cmd=None, test_cmd=None)
    store.save_benchmark_results(run_id, [
        BenchmarkResult("com.Bench.run", "thrpt", 1000.0, 10.0, "ops/s"),
        BenchmarkResult("com.Bench.run2", "avgt", 5.0, None, "us/op"),
    ])
    names = store.all_benchmark_names()
    assert "com.Bench.run" in names
    assert "com.Bench.run2" in names
