# Profile & Diff Feature Design

This document describes the design for flamegraph profiling, artifact diffing, and interactive
comparison features in branch-bench.

---

## Motivation

branch-bench captures time-boxed benchmark profiles (e.g. async-profiler flamegraphs) as
incidental artifacts of the `bench_cmd`. These are useful for spot-checking, but have a
fundamental problem for diffing: a faster implementation does more work in the same wall time,
so comparing two flamegraphs reports a relative shift in the workload rather than the true
structural difference between the two branches. Tools like `jfrconv --diff` and IntelliJ's
flamegraph diff expose this problem clearly.

The fix is a separate profiling mode that runs a **fixed number of operations** (using JMH's
`SingleShotTime` mode or a fixed-iteration count) so that both sides of a diff have done
identical amounts of work. This makes percentage-based flamegraph comparisons meaningful.

---

## TOML Configuration

### New keys

```toml
[commands]
test_cmd    = "..."   # existing — correctness check
bench_cmd   = "..."   # existing — primary metrics, secondary metrics, incidental artifacts

# Fixed-workload profiling for meaningful diffs.
# Use -bm ss (SingleShotTime) with a fixed iteration count, or -i 1 -r <long> for a single
# timed window. Secondary metrics are NOT collected from this command.
# Example: -bm ss -wi 20 -i 5000 gives 20 warmup shots then 5000 measurement invocations;
# both branches do exactly the same number of operations.
profile_cmd = """
  ./mill foo.jmh.run -- -bm ss -wi 20 -i 5000 -f1 \
    -prof 'async:dir={out_dir};libPath=/path/to/libasyncProfiler.dylib;event=cpu;output=flamegraph'
"""

[diff]
# Key is the file extension of the *input* artifact that triggers a diff.
# The command is called once per matched artifact pair, with environment variables:
#
#   LEFT_FILE         absolute path to left artifact
#   LEFT_SHA          full commit SHA
#   LEFT_COMMIT_MSG   commit message (first line)
#   LEFT_BRANCH       branch name (if known)
#   RIGHT_FILE        absolute path to right artifact
#   RIGHT_SHA         full commit SHA
#   RIGHT_COMMIT_MSG  commit message (first line)
#   RIGHT_BRANCH      branch name (if known)
#   OUT_DIR           directory where the tool must write its output file(s)
#
# The tool may write 0-N files of any name/extension to OUT_DIR.
# Exit code 0 = success; non-zero = failure (logged, pair skipped, run continues).
svg = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"
jfr = "jfrconv diff $LEFT_FILE $RIGHT_FILE --outdir $OUT_DIR"
```

### What each command collects

| Command       | Primary metrics | Secondary metrics | Profile artifacts |
|---------------|:-:|:-:|:-:|
| `bench_cmd`   | yes | yes | yes (incidental) |
| `profile_cmd` | no  | no  | yes (intentional) |

Secondary metrics (e.g. `·gc.alloc.rate.norm` from `-prof gc`) are only collected from
`bench_cmd`. They are meaningless in SingleShotTime mode over thousands of fixed iterations.

---

## CLI

### Existing commands (unchanged behavior)

```
branch-bench run     # runs bench_cmd (+ profile_cmd if configured) for each commit
branch-bench report  # regenerates static report from DB
branch-bench serve   # (new) interactive HTTP server with on-demand diffing
```

### New / modified commands

```
branch-bench profile [--sha RANGE] [--diff-vs previous|branch-base|both]
    Run only profile_cmd for each commit in the branch. Useful as a standalone step
    (e.g. after bench results already exist) or as a separate pipeline stage via `&&`.

branch-bench diff SHA1 SHA2
    AOT diff between two specific commits. Finds all profile artifacts for each commit
    and calls the configured diff tool for each matched extension pair.

branch-bench diff SHA1..SHA2 [--diff-vs previous|branch-base|both]
    AOT diff over a commit range. Diffs adjacent pairs ('previous'), every commit vs the
    merge-base ('branch-base'), or both (default).

branch-bench run --strategy linear [--diff] [--diff-vs previous|branch-base|both]
    Linear run with optional inline diffing after each commit is benchmarked+profiled.

branch-bench serve [--port 7823]
    Serve the static report with an added HTTP API for on-demand diffing.
    Injects window.__serverMode = true into the HTML so the UI enables interactive compare.
```

### `--diff-vs` modes

Applies to any operation that diffs a range:

| Mode | Meaning |
|------|---------|
| `previous` | commit[i] vs commit[i-1] in walk order |
| `branch-base` | commit[i] vs the merge-base with main/master |
| `both` | both sets independently (default) |

**`branch-base` setup:** when this mode is requested, branch-bench first checks out the
merge-base commit and runs `profile_cmd` on it (no bench, no tests). This is stored as
`source='profile'` with the merge-base SHA. Tree-SHA reuse applies: if the merge-base was
already profiled in a prior run, it is not re-run. The merge-base result is then used as the
fixed LEFT for every commit's diff.

---

## Asset Directory Structure

```
.bench/
  bench.db
  epoch-N/
    index.html
    running.js          (live log, cleared after run)
    assets/
      <slug>/           # <short_sha>-<sanitized-message>
        bench/
          run-1/
            jmh-results.json
            cpu-forward.svg     ← incidental (user put profiler in bench_cmd)
          run-2/
            jmh-results.json
        profile/
          run-1/
            cpu-forward.svg     ← from profile_cmd (fixed-workload)
            alloc-forward.svg
      diffs/
        <left_sha8>-<right_sha8>/   ← one directory per pair
          cpu-forward/              ← one subdirectory per source artifact
            diff.html               ← written by diff tool
            reverse-diff.html       ← written by diff tool
          alloc-forward/
            diff.html
```

Separating `bench/` and `profile/` under each commit slug prevents file clobbering and makes
the source of each artifact unambiguous. Temp directories are also scoped:
`/tmp/bb-bench-<uuid>/` and `/tmp/bb-profile-<uuid>/`.

The `diff_vs` tag (previous vs branch-base) is stored in the database only — the left/right
SHAs already encode the directionality, so there is no need to encode it in the path.

---

## Database Schema

### Modified: `runs` table

Add a `source` column (migration bumps schema version):

```sql
ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'bench';
-- Values: 'bench' | 'profile'
```

All existing rows get `source='bench'`. New profile-only invocations use `source='profile'`.

This is simpler than a separate table: the shape is identical (cmd, output, artifacts, tree-SHA
reuse), only the semantics differ.

### New: `diffs` table

One row per output file (a single diff tool invocation may produce N files):

```sql
CREATE TABLE diffs (
  id          INTEGER PRIMARY KEY,
  epoch       INTEGER NOT NULL,
  left_sha    TEXT NOT NULL,
  right_sha   TEXT NOT NULL,
  diff_vs     TEXT NOT NULL,   -- 'previous' | 'branch-base'
  source_ext  TEXT NOT NULL,   -- extension of the input artifact that triggered this diff
                               -- e.g. 'svg' or 'jfr'
  diff_path   TEXT NOT NULL,   -- path relative to epoch-N/ directory
  created_at  TEXT NOT NULL
);

CREATE INDEX diffs_right_sha ON diffs (epoch, right_sha);
CREATE INDEX diffs_pair ON diffs (epoch, left_sha, right_sha);
```

### Profile artifact storage

Profile artifacts are stored in the existing `profiles` table, linked to the `runs` row whose
`source='profile'`. No schema change needed beyond the `source` column on `runs`.

---

## Diff Execution Logic

For a given (left_sha, right_sha, diff_vs) triple:

1. Query DB for all artifacts for each commit, preferring `source='profile'` over `source='bench'`
   (fall back to bench artifacts if no profile run exists).
2. For each artifact on the LEFT side, find the extension. If `[diff]` has an entry for that
   extension, look for a matching artifact on the RIGHT side (same event type, same extension).
3. If both sides have a match, invoke the diff tool:
   - Set env vars: `LEFT_FILE`, `LEFT_SHA`, `LEFT_COMMIT_MSG`, `LEFT_BRANCH`, `RIGHT_*`, `OUT_DIR`
   - `OUT_DIR` = `epoch-N/assets/diffs/<left8>-<right8>/<artifact_stem>/`
   - Run command via shell, capture stdout/stderr for logging
   - On exit 0: glob `OUT_DIR/**` and insert one `diffs` row per file found
   - On non-zero exit: log warning, skip pair, continue
4. Multiple artifact pairs (e.g. cpu.svg + alloc.svg) each get separate `OUT_DIR` subdirectories
   and separate diff tool invocations.

During a `branch-bench run --strategy linear --diff`, diffing happens immediately after each
commit's profile step completes, before moving to the next commit.

---

## Report: Static Artifacts

Pre-computed diffs appear as "virtual artifacts" on the RIGHT commit's row in the commit table.
They are grouped separately from primary artifacts and labeled by their `diff_vs` source.

```
Artifacts:    [bench: cpu  alloc]   [profile: cpu  alloc]
Diffs:        [vs prev:  diff.html  reverse-diff.html]
              [vs base:  diff.html  reverse-diff.html]
```

Badge styling: diff badges use a `⊕` prefix and a distinct color (e.g. amber) to distinguish
them visually from primary artifact badges (blue/green/gold).

---

## Interactive Server (`branch-bench serve`)

A lightweight Flask server that:

1. Serves `epoch-N/index.html` and all static assets
2. Injects `window.__serverMode = true` (and current epoch number) into the HTML at serve time
3. Provides a diff API:

```
POST /api/diff
  Body: { "left_sha": "...", "right_sha": "..." }
  Response: { "diffs": [ { "diff_vs": "on-demand", "files": ["...path..."] } ] }
  Behavior: checks DB for existing diffs; runs missing ones synchronously; returns all results.

GET /diffs/<path:relative>
  Serves files under epoch-N/assets/diffs/
```

### UI changes in report.html

The commit table gains a "select" checkbox column, visible only when `window.__serverMode` is
true. When exactly two commits are checked, a floating "Compare" button appears.

Clicking "Compare":
1. POST to `/api/diff` with the two selected SHAs
2. Display returned diff files in an inline panel (iframe for HTML, `<img>` for SVG, download
   link for JFR/binary)
3. Panel shows left and right commit messages as context

In static mode (`window.__serverMode = false`), the checkbox column is hidden. Clicking any
pre-computed diff badge opens the file directly. A tooltip on the compare affordance reads:
"Run `branch-bench serve` to compare any two commits on demand."

On-demand diffs produced by the server are stored in the DB with `diff_vs='on-demand'` and
appear as virtual artifacts on the RIGHT commit if the report is regenerated afterward.

---

## Implementation Order

1. **Config** — add `profile_cmd` to `Commands` dataclass; add `[diff]` section with
   extension-keyed dict; validate at load time.
2. **Asset layout** — update `run_assets_dir()` to include `bench/` or `profile/` segment;
   update report asset path resolution.
3. **DB migration** — add `source` column to `runs`; add `diffs` table.
4. **`profile` command** — new CLI command; reuse existing commit-walking + artifact-collection
   logic with `source='profile'`; skip benchmark/test steps; skip secondary metric parsing.
5. **`run` integration** — when `profile_cmd` is set, run it after `bench_cmd` for each commit
   (same epoch, same commit row, different `source`).
6. **Diff engine** — shared module called by AOT `diff` command, `--diff` flag, and serve API;
   handles artifact matching, tool invocation, `OUT_DIR` globbing, DB insertion.
7. **AOT `diff` command** — two-SHA form and range form; `--diff-vs` flag.
8. **`--diff` on linear run** — wire diff engine into commit loop after profile step.
9. **Report UI** — diff badge rendering; static-mode graceful degradation.
10. **`serve` command** — Flask server; `/api/diff` endpoint; `__serverMode` injection; inline
    diff panel in report HTML.
