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
| `--from-sha SHA` | — | Start range at this commit (prefix match) |
| `--to-sha SHA` | — | End range at this commit (prefix match) |
| `--sha SHA` | — | Run only this commit; repeatable (`--sha A --sha B`); prefix match |
| `--all` | off | Re-run commits that already have results in the current epoch |
| `--no-test` | off | Skip correctness tests |
| `--no-bench` | off | Skip benchmarks |
| `--report` | off | Generate report after run completes (implied by `--no-live-report`) |
| `--no-live-report` | off | Disable per-commit report regeneration during the run |
| `--epoch N` | current | Run in a specific past epoch instead of the current one |

`--sha` is the fastest way to re-run noisy commits:

```bash
branch-bench run --sha abc123 --sha def456 --all
```

Then switch to **Aggregate** mode in the report to pool raw measurements across both runs.

### `report` options

| Flag | Description |
|---|---|
| `--epoch N` | Regenerate the report for a specific past epoch |

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

### Tree-SHA reuse

Before running a commit, `branch-bench` checks whether any other commit in the current epoch has the same **git tree SHA** — meaning the source tree is byte-for-byte identical. If so, the existing results are cloned to the new commit without re-running anything. This means:

- After a `git rebase` that rewrites commit metadata but not content, the next `run` or `report` restores all results instantly
- Stale pre-rebase commits are retired from the report automatically

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
│   ├── report.html                  # Self-contained epoch report
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
    ├── report.html
    └── assets/
        └── ...
```

**Zip-and-send:** `epoch-N/` is self-contained — `report.html` references profiles and JSON via relative paths within the same directory tree. Zip it and send it, or drag the folder to a shared drive; everything works offline.

**GitHub Pages:** push `.bench/` to a `gh-pages` branch. `index.html` at the root lists all epoch reports as clickable links.

```bash
tmp=$(mktemp -d) && cp -r .bench/. "$tmp" && git -C "$tmp" init -q && git -C "$tmp" add -A && git -C "$tmp" commit -q -m "Deploy report" && git -C "$tmp" push --force "$(git remote get-url origin)" HEAD:gh-pages && rm -rf "$tmp"
```

Add `.bench/` to `.gitignore` (or keep it — it has no build-tool-generated content and is fully reproducible by re-running).

### Report

The HTML report loads Plotly.js via CDN and contains all data inline. It shows:

**Benchmark trend charts** — one chart per benchmark variant. Hover over a point to see score, timestamp, and short SHA; click to copy the full SHA to the clipboard. Commit SHAs link directly to the GitHub diff when a GitHub remote is detected.

The toolbar above the charts has two layers of controls:

- **Runs dropdown** — how to handle multiple runs per commit:
  - *Latest* — show only the most recent run per commit (default)
  - *All* — one trace per run index; useful for spotting measurement drift
  - *Aggregate* — pool all raw measurements across runs, recompute mean and 99% CI from the full sample
  - *Run N* — show only a specific run index

- **Error bars** — what the error whiskers represent:
  - *mean ± CI* — 99% confidence interval from JMH (default)
  - *min* — downward whisker to the minimum raw measurement
  - *max* — upward whisker to the maximum raw measurement
  - *raw points* — box-and-whisker plot showing every individual JMH iteration

**Commit table** — all commits in branch order with test pass/fail, benchmark score for the selected run mode, and links to flamegraphs and raw JMH JSON. Click a row to expand all runs with full stdout/stderr output, reused-tree badges, and per-run artifact links. Commit SHAs are selectable and link to GitHub diffs when a remote is detected.

---

## Re-running noisy measurements

The recommended workflow when a measurement looks noisy:

```bash
# Re-run one or more commits (adds a new run, doesn't replace)
branch-bench run --sha abc123 --sha def456 --all

# Regenerate the report
branch-bench report
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
