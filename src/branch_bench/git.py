from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Commit:
    sha: str
    short_sha: str
    author: str
    timestamp: int
    message: str
    tree_sha: str = ""


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def is_dirty(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    )
    # Ignore untracked files (lines starting with ??); only tracked changes matter
    return any(not line.startswith("??") for line in result.stdout.splitlines())


def current_ref(repo: Path) -> str:
    try:
        return _run(["git", "symbolic-ref", "--short", "HEAD"], repo)
    except subprocess.CalledProcessError:
        return _run(["git", "rev-parse", "HEAD"], repo)


def find_merge_base(repo: Path, branch: str, base_branches: list[str] = ["main", "master"]) -> str | None:
    """Return the SHA of the merge-base between branch and the first found base branch."""
    for base in base_branches:
        try:
            sha = _run(["git", "merge-base", base, branch], repo)
            if sha:
                return sha
        except subprocess.CalledProcessError:
            continue
    return None


def list_commits(repo: Path, branch: str, max_count: int | None = None, exclude_before: str | None = None) -> list[Commit]:
    fmt = "%H\x1f%T\x1f%ae\x1f%at\x1f%s"
    # Use <merge-base>..<branch> to exclude commits shared with main/master
    ref = f"{exclude_before}..{branch}" if exclude_before else branch
    args = ["git", "log", f"--format={fmt}", ref]
    if max_count is not None:
        args += [f"-{max_count}"]
    output = _run(args, repo)
    if not output:
        return []
    commits = []
    for line in output.splitlines():
        sha, tree_sha, author, ts, message = line.split("\x1f", 4)
        commits.append(Commit(sha=sha, short_sha=sha[:8], author=author, timestamp=int(ts), message=message, tree_sha=tree_sha))
    # Return oldest-first so we process in chronological order
    return list(reversed(commits))


def checkout(repo: Path, sha: str) -> None:
    subprocess.run(["git", "checkout", "--quiet", sha], cwd=repo, check=True)


def restore(repo: Path, ref: str) -> None:
    subprocess.run(["git", "checkout", "--quiet", ref], cwd=repo, check=True)
