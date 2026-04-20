#!/usr/bin/env bash
# demo.sh — regenerate the sample repo and run branch-bench end-to-end.
#
# Usage:
#   ./demo.sh [DEST]          # default: /tmp/branch-bench-sample
#   ./demo.sh ~/my-sample     # custom destination

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/tmp/branch-bench-sample}"

echo "=== Generating repo at $DEST ==="
bash "$SCRIPT_DIR/generate-repo.sh" "$DEST"

echo ""
echo "=== Running branch-bench ==="
cd "$DEST"
branch-bench run

echo ""
echo "=== Generating report ==="
branch-bench report

echo ""
echo "Done. Open the report with:"
echo "  branch-bench show"
echo "or:"
echo "  open $DEST/.bench/epoch-1/index.html"
