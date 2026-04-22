import tempfile
from pathlib import Path

from branch_bench.storage import Store, BenchmarkResult, TestResult


def make_store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Store(Path(f.name))


def test_save_and_query_commit():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "initial commit", "dev@example.com", 1700000000, "main", parent_sha="root")
    store.set_epoch_head(store.current_epoch(), "abc123")
    commits = store.all_commits()
    assert len(commits) == 1
    assert commits[0]["sha"] == "abc123"
    assert commits[0]["parent_sha"] == "root"


def test_topological_order():
    store = make_store()
    # A -> B -> C
    store.save_commit("C", "C", "msg C", "a@b.com", 1002, "main", parent_sha="B")
    store.save_commit("A", "A", "msg A", "a@b.com", 1000, "main", parent_sha="root")
    store.save_commit("B", "B", "msg B", "a@b.com", 1001, "main", parent_sha="A")
    
    store.set_epoch_head(1, "C")
    commits = store.all_commits()
    assert [c["sha"] for c in commits] == ["A", "B", "C"]


def test_create_run():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id = store.create_run("abc123", bench_cmd="./mill foo.jmh.run", test_cmd="./mill foo.test")
    assert isinstance(run_id, int)
    assert not store.has_runs("abc123")
    store.save_benchmark_results(run_id, [BenchmarkResult("bench.M.run", "avgt", 1.0, None, "ns/op")])
    assert store.has_runs("abc123")
    assert not store.has_runs("unknown")

    runs = store.runs_for_commit("abc123")
    assert len(runs) == 1
    assert runs[0]["bench_cmd"] == "./mill foo.jmh.run"
    assert runs[0]["test_cmd"] == "./mill foo.test"
    assert runs[0]["source"] == "bench"


def test_run_source_column():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    bench_id = store.create_run("abc123", bench_cmd="./bench.sh", test_cmd=None, source="bench")
    profile_id = store.create_run("abc123", bench_cmd="./profile.sh", test_cmd=None, source="profile")

    runs = store.runs_for_commit("abc123")
    sources = {r["source"] for r in runs}
    assert sources == {"bench", "profile"}

    # run_number_for_id counts within the same source
    assert store.run_number_for_id(bench_id) == 1
    assert store.run_number_for_id(profile_id) == 1

    # A second profile run gets number 2
    profile_id2 = store.create_run("abc123", bench_cmd="./profile.sh", test_cmd=None, source="profile")
    assert store.run_number_for_id(profile_id2) == 2


def test_all_epochs_from_settings():
    store = make_store()
    store.set_epoch_head(5, "sha1")
    store.set_epoch_head(10, "sha2")
    epochs = store.all_epochs()
    assert 5 in epochs
    assert 10 in epochs


def test_backfill_epoch_heads():
    store = make_store()
    # Mock some commits with positions but no head in settings
    store.save_commit("A", "A", "msg A", "auth", 1000, "main", position=0, parent_sha="root")
    store.save_commit("B", "B", "msg B", "auth", 1001, "main", position=1, parent_sha="A")
    # Both are in current epoch (1)
    
    # Add a run for B so it can be inferred as the head
    store.create_run("B", bench_cmd=None, test_cmd=None)
    
    heads = store.backfill_epoch_heads()
    assert heads == 1
    
    # Check if head is B (highest position)
    commits = store.all_commits()
    assert [c["sha"] for c in commits] == ["A", "B"]


def test_backfill_parents(monkeypatch):
    import subprocess
    from dataclasses import dataclass
    
    @dataclass
    class MockRes:
        returncode: int
        stdout: str

    def mock_run(args, **kwargs):
        # args[4] is the SHA
        if args[4] == "B":
            return MockRes(0, "A")
        return MockRes(0, "")

    monkeypatch.setattr(subprocess, "run", mock_run)
    
    store = make_store()
    store.save_commit("A", "A", "msg A", "auth", 1000, "main", parent_sha=None)
    store.save_commit("B", "B", "msg B", "auth", 1001, "main", parent_sha=None)
    
    count = store.backfill_parents(Path("."))
    assert count >= 1 # At least B -> A should be found
    
    # Verify B's parent is now A in DB
    row = store._conn.execute("SELECT parent_sha FROM commits WHERE sha='B'").fetchone()
    assert row[0] == "A"


def test_has_profile_runs():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    assert not store.has_profile_runs("abc123")

    profile_id = store.create_run("abc123", bench_cmd="./profile.sh", test_cmd=None, source="profile")
    # No artifacts yet — has_profile_runs checks for profiles rows
    assert not store.has_profile_runs("abc123")

    store.save_profile(profile_id, "cpu-forward", "epoch-1/assets/abc12345/profile/run-1/cpu.svg")
    assert store.has_profile_runs("abc123")


def test_best_profiles_for_commit_prefers_profile_source():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")

    bench_id = store.create_run("abc123", bench_cmd="./bench.sh", test_cmd=None, source="bench")
    store.save_profile(bench_id, "cpu-forward", "bench/cpu.svg")

    profile_id = store.create_run("abc123", bench_cmd="./profile.sh", test_cmd=None, source="profile")
    store.save_profile(profile_id, "cpu-forward", "profile/cpu.svg")

    profiles = store.best_profiles_for_commit("abc123")
    assert len(profiles) == 1
    assert profiles[0]["file_path"] == "profile/cpu.svg"


def test_best_profiles_falls_back_to_bench():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")

    bench_id = store.create_run("abc123", bench_cmd="./bench.sh", test_cmd=None, source="bench")
    store.save_profile(bench_id, "cpu-forward", "bench/cpu.svg")

    profiles = store.best_profiles_for_commit("abc123")
    assert profiles[0]["file_path"] == "bench/cpu.svg"


def test_save_and_query_diff():
    store = make_store()
    store.save_commit("aaa", "aaa"[:8], "left", "a@b.com", 1700000000, "main")
    store.save_commit("bbb", "bbb"[:8], "right", "a@b.com", 1700000001, "main")

    epoch = store.current_epoch()
    store.save_diff(epoch, "aaa", "bbb", "previous", "svg", "epoch-1/assets/diffs/aaa-bbb/cpu/diff.html")
    store.save_diff(epoch, "aaa", "bbb", "previous", "svg", "epoch-1/assets/diffs/aaa-bbb/cpu/reverse-diff.html")

    diffs = store.diffs_for_right_sha("bbb")
    assert len(diffs) == 2
    assert all(d["diff_vs"] == "previous" for d in diffs)
    assert all(d["source_ext"] == "svg" for d in diffs)
    names = {d["diff_path"].split("/")[-1] for d in diffs}
    assert names == {"diff.html", "reverse-diff.html"}


def test_diff_exists():
    store = make_store()
    epoch = store.current_epoch()
    assert not store.diff_exists(epoch, "aaa", "bbb", "previous", "svg")

    store.save_diff(epoch, "aaa", "bbb", "previous", "svg", "some/path.html")
    assert store.diff_exists(epoch, "aaa", "bbb", "previous", "svg")
    assert not store.diff_exists(epoch, "aaa", "bbb", "branch-base", "svg")


def test_diffs_grouped_by_diff_vs():
    store = make_store()
    epoch = store.current_epoch()
    store.save_diff(epoch, "base", "bbb", "branch-base", "svg", "epoch-1/diffs/base-bbb/cpu/diff.html")
    store.save_diff(epoch, "aaa", "bbb", "previous", "svg", "epoch-1/diffs/aaa-bbb/cpu/diff.html")

    diffs = store.diffs_for_right_sha("bbb")
    assert len(diffs) == 2
    vs_set = {d["diff_vs"] for d in diffs}
    assert vs_set == {"branch-base", "previous"}


def test_epoch_resets_has_runs():
    store = make_store()
    store.save_commit("abc123", "abc123"[:8], "msg", "a@b.com", 1700000000, "main")
    run_id = store.create_run("abc123", bench_cmd=None, test_cmd=None)
    store.save_benchmark_results(run_id, [BenchmarkResult("bench.M.run", "avgt", 1.0, None, "ns/op")])
    assert store.has_runs("abc123")

    n = store.new_epoch()
    assert n == 2
    assert not store.has_runs("abc123")

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


def test_clone_run_copies_source():
    store = make_store()
    store.save_commit("aaa", "aaa"[:8], "left", "a@b.com", 1700000000, "main")
    store.save_commit("bbb", "bbb"[:8], "right", "a@b.com", 1700000001, "main")

    profile_id = store.create_run("aaa", bench_cmd="./p.sh", test_cmd=None, source="profile")
    store.save_profile(profile_id, "cpu-forward", "aaa/cpu.svg")

    new_id = store.clone_run(profile_id, "bbb", reused_from_sha="aaa"[:8])
    runs = store.runs_for_commit("bbb")
    assert runs[0]["source"] == "profile"
    profiles = store.profiles_for(new_id)
    assert profiles[0]["file_path"] == "aaa/cpu.svg"
