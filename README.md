# branch-bench

A CLI tool for curating a git branch of performance improvements. For each commit it:

1. **Verifies correctness** — runs your test command and aborts if the baseline fails
2. **Benchmarks** — runs your JMH benchmark command and stores results
3. **Profiles** — collects async-profiler flamegraphs produced as a side-effect of the benchmark
4. **Reports** — generates a live HTML report (refresh the browser after each commit)

Results are stored in a SQLite database and never discarded. Multiple runs per commit accumulate across epochs.

---

## Live example

**[retronym.github.io/branch-bench-sample](https://retronym.github.io/branch-bench-sample/)** — a Java + Maven + JMH project benchmarking five commits of a `PropertyResolver` optimisation, including a no-op commit and an intentional regression. Source: [github.com/retronym/branch-bench-sample](https://github.com/retronym/branch-bench-sample).

---

## Why this exists

Micro-optimisation work runs into a specific problem: the improvement you are trying to measure is often the same order of magnitude as the noise in the measurement. A 3% speedup and a lucky run look identical in a single JMH result. `branch-bench` is built around that reality.

**Multiple summary modes.** Switch between mean ± 99% CI, median, min, and max. For highly skewed workloads, median is a more stable signal than mean; min is useful for latency work where you care about best-case behaviour or where the benchmarking takes place on a developer machine with some level of competing processes; max surfaces tail effects. The [report section](#report) describes all available modes.

**Distribution-aware visualisation.** The 'raw points' view plots every JMH iteration as a raw point (jittered to avoid overlap), with a summary box overlay showing min / median / max. Variance, skew, and multimodal distributions are immediately visible — the kind of shape information that a mean-and-CI bar chart suppresses.

**Multi-run aggregation.** Re-run the same commits with `--sha HEAD~N..HEAD --all` and switch the report to *Aggregate* mode. This pools raw iteration data across all runs and recomputes statistics from the full sample — equivalent to having run more JMH forks, and a practical way to build confidence in a result on a noisy machine.

**Secondary metric overlays.** Add `-prof gc` to your bench command and the report can overlay `gc.alloc.rate.norm` (or any other secondary metric) on a right-hand axis alongside throughput or latency. When a benchmark moves, this makes it easier to tell whether the change is compute-bound or allocation-driven.

**Rebase-safe result storage.** Results are keyed on git tree SHA, not commit SHA. Squash, reorder, or amend commits during active branch development and existing benchmark data is reattached to the rewritten commits automatically — no re-run needed unless the source actually changed, so certain branch curation operations can be done without wiping the slate clean.

---

## Installation

Requires Python 3.11+. Install with [pipx](https://pipx.pypa.io/) for an isolated global CLI:

```bash
pipx install branch-bench
```

To update after code changes:

```bash
pipx install branch-bench --force
```

---

## Quick start

```bash
cd myproject           # must be a git repo
branch-bench init      # creates bench.toml
$EDITOR bench.toml     # configure test_cmd and bench_cmd
branch-bench run       # start processing
branch-bench show      # open report in browser
```

While `run` is executing, just refresh the browser — the report regenerates after every commit.

---

## bench.toml

```toml
[repo]
path = "."             # path to git repo (default: cwd)
branch = "main"        # branch to walk

[commands]
# Any shell command. Exit code determines pass/fail.
test_cmd = "./mill foo.test"

# Two substitutions are available:
#   {out}     — temp file path for JMH JSON results   (-rff {out})
#   {out_dir} — temp directory for profiler output    (dir={out_dir})
#
# Any *.svg or *.html files written into {out_dir} are collected as flamegraphs.
#
# Example with async-profiler CPU flamegraph:
bench_cmd = "./mill foo.jmh.run -- -rf json -rff {out} -prof 'async:dir={out_dir};libPath=/path/to/libasyncProfiler.dylib;event=cpu;output=flamegraph' -wi 5 -i 5 -f1"

[output]
dir = ".bench"         # all output lives here
```

The `bench_cmd` is passed to `/bin/sh` via `shell=True`, so normal shell quoting applies. Use single quotes around the async-profiler `-prof` argument to protect semicolons from shell interpretation. The TOML value itself should use double quotes so single quotes inside are passed through verbatim.

---

## Commands

All commands accept `--config PATH` to point at a `bench.toml` in a non-default location.

| Command | Description |
|---|---|
| `branch-bench init` | Scaffold `bench.toml` in the current directory |
| `branch-bench run` | Walk branch commits, test + benchmark + profile |
| `branch-bench report` | Regenerate the report from stored data |
| `branch-bench show` | Open the current epoch's report in the default browser |
| `branch-bench epoch` | Start a new epoch — re-run all commits next time (preserves history) |
| `branch-bench status` | Print a summary of what has been collected |
| `branch-bench migrate` | Migrate files from an old layout to the current one |

### `run` options

| Flag | Default | Description |
|---|---|---|
| `--strategy bisect\|linear` | `bisect` | bisect: endpoints + midpoints first for a quick curve shape; linear: oldest-to-newest |
| `-n N` / `--commits N` | all | Max commits to process |
| `--from REF` / `--from-sha REF` | — | Start range at this commit — any git ref (`HEAD~5`, `v1.0`, SHA prefix) |
| `--to REF` / `--to-sha REF` | — | End range at this commit — any git ref |
| `--sha REF\|RANGE` | — | Run only this commit; repeatable. Accepts any git ref (`HEAD~2`, tag, SHA) or a range (`HEAD~5..HEAD`, `v1.0..v2.0`) |
| `--all` | off | Re-run commits that already have results in the current epoch |
| `--no-test` | off | Skip correctness tests |
| `--no-bench` | off | Skip benchmarks |
| `--report` | off | Generate report after run completes |
| `--open` | off | Open the report in your browser when done (implies `--report`) |
| `--no-live-report` | off | Disable per-commit report regeneration during the run |
| `-v` / `--verbose` | off | Stream bench command output live as it runs. `-vv` also streams the test command |
| `--epoch N` | current | Run in a specific past epoch instead of the current one |

All ref arguments are resolved to full SHAs via `git rev-parse` before anything runs, so the resolved SHA is what gets stored and shown in the report. The log line confirms each resolution:

```
  --from resolved: 'HEAD~3' → 3063d732
  --sha  resolved: 'HEAD~5..HEAD' → 3063d732  (then 649a909c, ab114d0d …)
```

`--sha` with ranges is the fastest way to re-run a band of noisy commits:

```bash
# Re-run the last 3 commits
branch-bench run --sha HEAD~3..HEAD --all

# Re-run two specific commits by name
branch-bench run --sha HEAD~1 --sha v1.2.3 --all
```

Then switch to **Aggregate** mode in the report to pool raw measurements across runs.

### `report` options

| Flag | Description |
|---|---|
| `--epoch N` | Regenerate the report for a specific past epoch |
| `--open` | Open the report in your browser when done |

### `migrate` options

| Flag | Description |
|---|---|
| `--from-db OLD.db` | Bootstrap the new layout from an old-format database (copied to new location; originals untouched) |

---

## How it works

### Commit selection

On startup `branch-bench run`:

1. Finds the **merge base** between the configured branch and `main`/`master`
2. Lists only commits that diverged from that point (`<merge-base>..<branch>`), oldest-first
3. Saves all commits to the DB immediately and writes a placeholder report showing them all as **pending**
4. Processes them in **bisect order** by default: first commit, last commit, then midpoints — so the shape of the performance curve emerges after just a few runs

### Safety gate

If the **first commit processed** (baseline) fails tests or produces no benchmark results, the run aborts. There is no point profiling later commits against a broken baseline. Use `--no-test` or `--no-bench` to skip checks.

### Tree-SHA reuse — curate freely without losing benchmark data

This is the feature that makes `branch-bench` practical during active branch development. Git stores a **tree SHA** for every commit — a content hash of the entire source tree. Two commits with the same tree SHA are byte-for-byte identical in source content even if their commit metadata (SHA, author, timestamp, message) differs.

Before running a commit `branch-bench` checks whether any already-benchmarked commit in the current epoch shares the same tree SHA. If it finds one, it clones the results to the new commit instantly — no rebuild, no JMH run.

**What this means in practice:**

You can rebase, squash, reorder, split, or amend commits freely. As long as the *source content* of a commit is unchanged, its benchmark data survives the rewrite. The typical workflow looks like this:

```
Benchmark a few commits  →  rebase / squash / amend  →  run or report again
                                                          (results reappear instantly)
```

Concrete scenarios:

- **Interactive rebase to squash fixups** — you squash a `Fix typo` commit into the substantive commit. The squashed commit's tree is unchanged; its results are immediately visible in the report under the new SHA.
- **Reorder commits** — you move a refactor commit earlier in the stack. As long as the merge resolves identically, the tree SHA matches and results transfer.
- **Amend commit message or author** — metadata-only changes never alter the tree SHA, so results are preserved automatically.
- **Rebase onto a newer base** — after rebasing your branch onto a newer `main`, any commits whose source was not affected by the rebase (no conflict resolution changed them) keep their results. Only the commits that actually changed need re-running.
- **Split a commit** — you split one commit into two. The second commit's final tree is the same as the original; benchmark results attach to it immediately.

After any of these operations, run:

```bash
branch-bench report        # or: branch-bench run  (skips already-known trees)
```

Stale pre-rewrite commits are retired from the report automatically and replaced by their successors carrying the same results.

**What requires a fresh benchmark run:**

Only commits whose source tree genuinely changed — because you refactored code, applied a conflict resolution differently, or intentionally altered the implementation. Those show as *pending* in the report and are picked up on the next `run`.

> **Edge case — rebase, re-bench, then revert:** if you re-benchmark a commit during a rebased phase and then revert to the original branch, the original commit's pre-rebase results will show rather than the newer ones (the existing run shadows the tree-SHA match). Run `--sha <sha> --all` to add a fresh run, or `branch-bench epoch` for a clean slate.

### Epochs

Each run lives in an **epoch**. `branch-bench epoch` increments the counter. The report and skip-existing logic only see the current epoch, so old data is hidden but never deleted. Use epochs to:

- Re-run everything cleanly after changing the benchmark command or JVM flags
- Compare runs over time by querying the SQLite DB directly

### Output layout

Everything lives under `.bench/` (configurable via `output.dir`):

```
.bench/
├── bench.db                         # SQLite database (all epochs)
├── index.html                       # Epoch listing — link to share with colleagues
├── epoch-1/
│   ├── index.html                   # Self-contained epoch report
│   └── assets/
│       ├── dc2522e7-integrate-benchmarks/
│       │   ├── run-1/
│       │   │   ├── jmh-results.json
│       │   │   └── cpu-forward.svg
│       │   └── run-2/               # --all re-run
│       │       ├── jmh-results.json
│       │       └── cpu-forward.svg
│       └── ffa96aa0-cache-versionconstraint/
│           └── run-1/
│               └── jmh-results.json
└── epoch-2/
    ├── index.html
    └── assets/
        └── ...
```

**Zip-and-send:** `epoch-N/` is self-contained — `index.html` references profiles and JSON via relative paths within the same directory tree. Zip it and send it, or drag the folder to a shared drive; everything works offline.

**GitHub Pages:** push `.bench/` to a `gh-pages` branch. `index.html` at the root lists all epoch reports as clickable links.

```bash
tmp=$(mktemp -d) && cp -r .bench/. "$tmp" && git -C "$tmp" init -q && git -C "$tmp" add -A && git -C "$tmp" commit -q -m "Deploy report" && git -C "$tmp" push --force "$(git remote get-url origin)" HEAD:gh-pages && rm -rf "$tmp"
```

Add `.bench/` to `.gitignore` (or keep it — it has no build-tool-generated content and is fully reproducible by re-running).

### Report

The HTML report loads Plotly.js via CDN and contains all data inline. It shows:

**Navigation bar** — sticky top bar with:
- *Epoch picker* — jump between epochs; type to filter. Hidden when there is only one epoch.
- *Benchmark picker* — one toggle button per JMH benchmark class. All active by default (no filter). Click to deselect; click again to re-select. Hidden when there is only one class. Filters both charts and the table score column simultaneously.
- *GitHub link* — links to the repository when a GitHub remote is detected.

**Benchmark trend charts** — one chart per JMH benchmark method. Hover over a point to see score, timestamp, and short SHA; click to copy the full SHA to the clipboard. Commit SHAs link directly to the GitHub diff when a GitHub remote is detected.

The toolbar above the charts has three layers of controls:

- **Runs dropdown** — how to handle multiple runs per commit:
  - *Latest* — show only the most recent run per commit (default)
  - *All* — one trace per run index; useful for spotting measurement drift
  - *Aggregate* — pool all raw measurements across runs, recompute mean and 99% CI from the full sample
  - *Run N* — show only a specific run index

- **Overlay dropdown** — secondary metric to superimpose on a right-hand Y-axis (e.g. `gc.alloc.rate.norm` from `-prof gc`). Only visible when secondary metrics are present.

- **Error bars** — what the error whiskers represent:
  - *mean ± CI* — 99% confidence interval from JMH (default)
  - *min* — downward whisker to the minimum raw measurement
  - *max* — upward whisker to the maximum raw measurement
  - *raw points* — box-and-whisker plot showing every individual JMH iteration

**Commit table** — all commits in branch order with test pass/fail, benchmark score for the selected run mode, and links to flamegraphs and raw JMH JSON. Click a row to expand all runs with full stdout/stderr output, reused-tree badges, and per-run artifact links. Commit SHAs are selectable and link to GitHub diffs when a remote is detected. When multiple benchmark classes are visible the score column labels each result with its method name.

---

## Re-running noisy measurements

The recommended workflow when a measurement looks noisy:

```bash
# Re-run the last 5 commits (adds new runs, doesn't replace)
branch-bench run --sha HEAD~5..HEAD --all

# Or target specific commits by any git ref
branch-bench run --sha abc123 --sha HEAD~1 --all

# Regenerate and open the report
branch-bench report --open
```

Then open the report and switch **Runs → Aggregate**. This pools raw iteration data from all runs and recomputes statistics from the full sample, equivalent to having run more JMH forks.

---

## Build tool compatibility

`branch-bench` is build-tool agnostic. `test_cmd` and `bench_cmd` are arbitrary shell strings. It has been used with:

- **Maven** + JMH: `mvn -q package -DskipTests && java -jar target/benchmarks.jar -rf json -rff {out}`
- **Mill** + mill-jmh: `./mill foo.jmh.run -- -rf json -rff {out} ...`
- **sbt** + sbt-jmh: `sbt "jmh:run -rf json -rff {out} ..."`

Any build tool that can invoke JMH and write JSON results to a path works.

---

## SQLite schema

For direct analysis:

```sql
-- Core tables
commits             -- sha, short_sha, message, author, timestamp, branch, epoch, position, tree_sha
runs                -- id, commit_sha, epoch, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path, reused_from_sha
test_runs           -- id, run_id, passed, tests_run, tests_failed, duration_seconds, output
benchmark_results   -- id, run_id, benchmark, mode, score, score_error, unit, params, raw_data
secondary_metrics   -- id, run_id, benchmark, metric, score, score_error, unit, raw_data  (e.g. gc.alloc.rate.norm from -prof gc)
profiles            -- id, run_id, event, file_path
settings            -- key/value store (tracks current epoch)
```

Example — scores across commits for the current epoch in branch order:

```sql
SELECT c.short_sha, c.message, b.benchmark, b.score, b.score_error, b.unit
FROM benchmark_results b
JOIN runs r ON b.run_id = r.id
JOIN commits c ON r.commit_sha = c.sha
WHERE r.epoch = (SELECT value FROM settings WHERE key = 'epoch')
ORDER BY c.position;
```
