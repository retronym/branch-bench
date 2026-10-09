"""diff_pair caching and 'previous' resolution, against fake profile files."""
from __future__ import annotations

from pathlib import Path

from branch_bench.config import Config, DiffConfig, OutputConfig
from branch_bench.git import Commit
from branch_bench.runner import diff_pair
from branch_bench.storage import Store

CAT_DIFF = 'cat "$LEFT_FILE" "$RIGHT_FILE" > "$OUT_DIR/diff.txt"'


def _commit(sha: str, message: str = "msg") -> Commit:
    return Commit(sha=sha, short_sha=sha[:8], message=message, author="a@b.com", timestamp=0)


def _setup(tmp_path: Path, ext: str = "jfr") -> tuple[Config, Store]:
    cfg = Config(output=OutputConfig(dir=str(tmp_path / ".bench")), diff=DiffConfig(commands={ext: CAT_DIFF}))
    cfg.base_dir().mkdir(parents=True)
    store = Store(cfg.db_path())
    for i, sha in enumerate(("a" * 40, "b" * 40)):
        store.save_commit(sha, sha[:8], f"commit {i}", "a@b.com", 1700000000 + i, "main", position=i)
    return cfg, store


def _profile(store: Store, tmp_path: Path, sha: str, content: str, name: str = "cpu.jfr") -> None:
    run_id = store.create_run(sha, bench_cmd="profile", test_cmd=None, source="profile")
    f = tmp_path / f"{sha[:8]}-run{run_id}" / name
    f.parent.mkdir(parents=True)
    f.write_text(content)
    store.save_profile(run_id, "cpu", str(f))


def _diff(cfg: Config, store: Store, tmp_path: Path, log: list[str]) -> None:
    diff_pair(
        left_sha="a" * 40, left_short_sha="aaaaaaaa", left_message="commit 0",
        right_commit=_commit("b" * 40, "commit 1"), diff_vs="previous",
        cfg=cfg, store=store, repo_path=tmp_path, epoch=store.current_epoch(),
        branch="main", log=log.append,
    )


def _diff_contents(store: Store) -> list[str]:
    return [Path(d["diff_path"]).read_text() for d in store.diffs_for_pair("a" * 40, "b" * 40)]


def test_reprofile_invalidates_cached_diff(tmp_path):
    cfg, store = _setup(tmp_path)
    _profile(store, tmp_path, "a" * 40, "L1\n")
    _profile(store, tmp_path, "b" * 40, "R1\n")

    log: list[str] = []
    _diff(cfg, store, tmp_path, log)
    assert _diff_contents(store) == ["L1\nR1\n"]

    # Same profiles → cached, and not reported as an extension mismatch
    log.clear()
    _diff(cfg, store, tmp_path, log)
    assert any("cached" in m for m in log)
    assert not any("No matching extensions" in m for m in log)
    assert _diff_contents(store) == ["L1\nR1\n"]

    # Re-profile the right commit → fresh diff replaces the stale one
    _profile(store, tmp_path, "b" * 40, "R2\n")
    log.clear()
    _diff(cfg, store, tmp_path, log)
    assert not any("cached" in m for m in log)
    assert _diff_contents(store) == ["L1\nR2\n"]

    # Re-profile the left commit too
    _profile(store, tmp_path, "a" * 40, "L2\n")
    _diff(cfg, store, tmp_path, log)
    assert _diff_contents(store) == ["L2\nR2\n"]


def test_no_matching_extensions_logged_only_on_mismatch(tmp_path):
    cfg, store = _setup(tmp_path, ext="svg")
    _profile(store, tmp_path, "a" * 40, "L1\n")
    _profile(store, tmp_path, "b" * 40, "R1\n")

    log: list[str] = []
    _diff(cfg, store, tmp_path, log)
    assert any("No matching extensions" in m for m in log)
    assert store.diffs_for_pair("a" * 40, "b" * 40) == []
