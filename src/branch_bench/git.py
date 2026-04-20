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
    """Return the SHA of the merge-base between branch and the first found base branch.
    Skips self-comparison so that branch = "main" works correctly."""
    for base in base_branches:
        if base == branch:
            continue  # merge-base X X returns HEAD, making X..X empty
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


def github_remote_url(repo: Path) -> str | None:
    """Return the base GitHub HTTPS URL for this repo (e.g. https://github.com/owner/repo),
    inferred from the first github.com remote, preferring 'origin'.
    Returns None if no github.com remote is found.
    """
    import re
    try:
        output = _run(["git", "remote", "-v"], repo)
    except subprocess.CalledProcessError:
        return None

    candidates: dict[str, str] = {}  # name -> normalised url
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        if "github.com" not in url:
            continue
        # Normalise SSH and HTTPS variants to https://github.com/owner/repo
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        if m:
            candidates.setdefault(name, f"https://github.com/{m.group(1)}")

    return candidates.get("origin") or next(iter(candidates.values()), None)


def rev_parse(repo: Path, ref: str) -> str | None:
    """Resolve any git ref (SHA prefix, HEAD~N, branch, tag) to a full commit SHA.

    Returns None when the ref does not exist or does not resolve to a commit.
    The ``^{commit}`` peeling ensures annotated tags are dereferenced.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def expand_range(repo: Path, range_spec: str) -> list[str]:
    """Return full SHAs for all commits selected by *range_spec*.

    Accepts any syntax ``git log`` understands: ``x..y``, ``x...y``,
    ``HEAD~5..HEAD``, etc.  Returns commits newest-first (``git log`` order).
    Returns an empty list when the range is invalid or empty.
    """
    result = subprocess.run(
        ["git", "log", "--format=%H", range_spec, "--"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [s for s in result.stdout.splitlines() if s.strip()]


def checkout(repo: Path, sha: str) -> None:
    subprocess.run(["git", "checkout", "--quiet", sha], cwd=repo, check=True)


def restore(repo: Path, ref: str) -> None:
    """Checkout ref, removing any untracked files that would block the checkout."""
    import re as _re
    result = subprocess.run(
        ["git", "checkout", "--quiet", ref],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode == 0:
        return
    # Git names the blocking files in stderr — remove them and retry once.
    blocking = _re.findall(r"^\s+(.+)$", result.stderr, _re.MULTILINE)
    if blocking:
        for f in blocking:
            (repo / f.strip()).unlink(missing_ok=True)
        subprocess.run(["git", "checkout", "--quiet", ref], cwd=repo, check=True)
    else:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stderr)
