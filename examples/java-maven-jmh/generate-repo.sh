#!/usr/bin/env bash
# generate-repo.sh — create a git repo with a curated performance-improvement history.
#
# Usage:
#   ./generate-repo.sh [DEST]          # default: /tmp/branch-bench-sample
#
# To push to GitHub:
#   ./generate-repo.sh ~/branch-bench-sample
#   cd ~/branch-bench-sample
#   git remote add origin git@github.com:YOU/branch-bench-sample.git
#   git push -u origin main

set -euo pipefail

DEST="${1:-/tmp/branch-bench-sample}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMITS_DIR="$SCRIPT_DIR/commits"
BRANCH="main"

# Commit messages in order — one per numbered directory
declare -a MESSAGES=(
  "Integrate benchmarks: baseline property resolution using regex"
  "Optimize resolver: StringBuilder with manual char scanning"
  "Micro-opt: scan over char[] to reduce charAt() overhead"
  "Docs: add complexity comment to resolve() — no logic change"
  "Refactor: simplify resolve() to plain string concatenation"
)

echo "Generating sample repo at $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"

git -C "$DEST" init
git -C "$DEST" symbolic-ref HEAD "refs/heads/$BRANCH"
git -C "$DEST" config user.email "branch-bench@example.com"
git -C "$DEST" config user.name "branch-bench"

i=0
for commit_dir in "$COMMITS_DIR"/*/; do
  msg="${MESSAGES[$i]}"
  cp -r "$commit_dir/." "$DEST/"
  git -C "$DEST" add -A
  git -C "$DEST" commit -m "$msg"
  echo "  [$(( i + 1 ))/${#MESSAGES[@]}] $msg"
  (( i++ )) || true
done

echo ""
echo "Done — $i commit(s) on branch '$BRANCH'."
echo ""
echo "To run branch-bench:"
echo "  cd $DEST && branch-bench run"
echo ""
echo "To push to GitHub:"
echo "  cd $DEST"
echo "  git remote add origin git@github.com:YOU/branch-bench-sample.git"
echo "  git push -u origin $BRANCH"
