"""Integration test: generate the java-maven-jmh sample repo and verify git structure."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "examples" / "java-maven-jmh" / "generate-repo.sh"


@pytest.fixture(scope="module")
def sample_repo(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("branch-bench-sample")
    result = subprocess.run(
        ["bash", str(SCRIPT), str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"generate-repo.sh failed:\n{result.stderr}")
    return dest


def test_repo_is_git(sample_repo):
    assert (sample_repo / ".git").is_dir()


def test_branch_name(sample_repo):
    ref = subprocess.check_output(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=sample_repo, text=True,
    ).strip()
    assert ref == "main"


def test_commit_count(sample_repo):
    log = subprocess.check_output(
        ["git", "log", "--format=%s"],
        cwd=sample_repo, text=True,
    ).strip().splitlines()
    assert len(log) == 5


def test_commit_messages(sample_repo):
    log = subprocess.check_output(
        ["git", "log", "--format=%s", "--reverse"],
        cwd=sample_repo, text=True,
    ).strip().splitlines()
    assert "baseline" in log[0].lower()
    assert "stringbuilder" in log[1].lower() or "optimize" in log[1].lower()
    assert "char" in log[2].lower() or "micro" in log[2].lower()
    assert "comment" in log[3].lower() or "docs" in log[3].lower() or "no logic" in log[3].lower()
    assert "refactor" in log[4].lower() or "concat" in log[4].lower() or "simplif" in log[4].lower()


def test_bench_toml_in_first_commit(sample_repo):
    # bench.toml must be present from commit 0 so `branch-bench run` works on a fresh clone
    first_sha = subprocess.check_output(
        ["git", "log", "--format=%H", "--reverse"],
        cwd=sample_repo, text=True,
    ).strip().splitlines()[0]
    files = subprocess.check_output(
        ["git", "show", "--name-only", "--format=", first_sha],
        cwd=sample_repo, text=True,
    ).strip().splitlines()
    assert "bench.toml" in files


def test_pom_present(sample_repo):
    assert (sample_repo / "pom.xml").is_file()


def test_stringbuilder_resolver_present(sample_repo):
    resolver = sample_repo / "src" / "main" / "java" / "bench" / "PropertyResolver.java"
    assert resolver.is_file()
    content = resolver.read_text()
    assert "StringBuilder" in content
    assert "Pattern" not in content


def _list_commits_via_branch_bench(repo: Path):
    from branch_bench.git import list_commits, find_merge_base
    merge_base = find_merge_base(repo, "main")
    return list_commits(repo, "main", exclude_before=merge_base)


def test_branch_bench_sees_five_commits(sample_repo):
    commits = _list_commits_via_branch_bench(sample_repo)
    assert len(commits) == 5


def test_branch_bench_commit_order(sample_repo):
    commits = _list_commits_via_branch_bench(sample_repo)
    # oldest-first order
    assert "baseline" in commits[0].message.lower()
    assert "stringbuilder" in commits[1].message.lower() or "optimize" in commits[1].message.lower()
    # no-op comment commit sits between perf commits and regression
    assert "comment" in commits[3].message.lower() or "docs" in commits[3].message.lower() or "no logic" in commits[3].message.lower()
    assert "refactor" in commits[4].message.lower() or "concat" in commits[4].message.lower() or "simplif" in commits[4].message.lower()


def test_tree_shas_differ(sample_repo):
    commits = _list_commits_via_branch_bench(sample_repo)
    assert commits[0].tree_sha != commits[1].tree_sha
