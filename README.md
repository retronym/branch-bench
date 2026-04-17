# branch-bench

A CLI tool for curating a git branch of performance improvements. For each commit it:

1. **Verifies correctness** — runs your test command and aborts if commit 0 fails
2. **Benchmarks** — runs your JMH benchmark command and captures results
3. **Profiles** — collects async-profiler flamegraphs produced as a side-effect of the benchmark
4. **Reports** — generates a live HTML report (refresh the browser after each commit)

Results are stored in a SQLite database and never discarded. Multiple runs per commit are tracked via epochs.

---

## Installation

Requires Python 3.11+. Install with [pipx](https://pipx.pypa.io/) for an isolated global CLI:

```bash
pipx install /path/to/branch-bench
```

To update after code changes:

```bash
pipx install /path/to/branch-bench --force
```

---

## Quick start

```bash
cd myproject           # must be a git repo
branch-bench init      # creates bench.toml
$EDITOR bench.toml     # configure test_cmd and bench_cmd
branch-bench run       # start processing
branch-bench show      # open report.html in browser
```

While `run` is executing, just refresh the browser — the report updates after every commit.

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
db           = ".branchbench/bench-results.db"
report       = ".branchbench/report.html"
profiles_dir = ".branchbench/profiles"
```

The `bench_cmd` is passed to `/bin/sh` via `shell=True`, so normal shell quoting applies. Use single quotes around the async-profiler `-prof` argument to protect semicolons from shell interpretation. The TOML value itself should use double quotes so single quotes inside are passed through verbatim.

---

## Commands

| Command | Description |
|---|---|
| `branch-bench init` | Scaffold `bench.toml` in the current directory |
| `branch-bench run` | Walk branch commits, test + benchmark + profile |
| `branch-bench report` | Regenerate `report.html` from stored data |
| `branch-bench show` | Open report in the default browser |
| `branch-bench epoch` | Start a new epoch — re-run all commits next time (preserves history) |
| `branch-bench status` | Print a summary of what has been collected |

### `run` options

| Flag | Default | Description |
|---|---|---|
| `--strategy bisect\|linear` | `bisect` | bisect: process endpoints and midpoints first for a quick curve shape; linear: oldest-to-newest |
| `-n N` / `--commits N` | all | Max commits to process |
| `--from-sha SHA` | — | Start range at this SHA |
| `--to-sha SHA` | — | End range at this SHA |
| `--all` | off | Re-run commits that already have results in the current epoch |
| `--no-test` | off | Skip correctness tests |
| `--no-bench` | off | Skip benchmarks |
| `--no-live-report` | off | Disable per-commit report regeneration |

---

## How it works

### Commit selection

On startup `branch-bench run`:

1. Finds the **merge base** between the configured branch and `main`/`master`
2. Lists only commits that diverged from that point (`<merge-base>..<branch>`)
3. Saves all commits to the DB immediately and writes a placeholder report showing them all as **pending**
4. Processes them in **bisect order** by default: first commit, last commit, middle, quarters — so the shape of the performance curve is visible after just a few runs

### Safety gate

If the **first commit** (baseline) fails tests or produces no benchmark results, the run aborts immediately. There is no point profiling later commits against a broken baseline.

### Epochs

Each run lives in an **epoch**. `branch-bench epoch` increments the epoch counter. The report and `skip-existing` logic only see the current epoch, so old data is hidden but never deleted. This lets you:

- Re-run everything cleanly after tuning the benchmark command
- Compare epochs by querying the SQLite DB directly

### Output files

All working files go in `.branchbench/` in the project directory:

```
.branchbench/
├── bench-results.db      # SQLite database (all epochs)
├── report.html           # Latest generated report
├── profiles/             # Flamegraph files per commit
│   └── <short_sha>-<filename>.(html|svg)
└── jmh/                  # Raw JMH JSON result files
    └── <short_sha>-<run_id>.json
```

Add `.branchbench/` to `.gitignore`.

### Report

The HTML report is self-contained (Plotly.js via CDN). It shows:

- **Benchmark trend charts** — one per benchmark, one trace per epoch run, with error bars. Hover shows score, timestamp, and the exact command used.
- **Commit table** — all commits with test pass/fail, latest benchmark score ± error, and links to flamegraphs and raw JMH JSON. Click a row to expand all runs for that commit with full stdout/stderr output.

---

## Build tool compatibility

`branch-bench` is build-tool agnostic. `test_cmd` and `bench_cmd` are arbitrary shell strings. It has been used with:

- **Mill** + mill-jmh: `./mill foo.jmh.run -- -rf json -rff {out} ...`
- **sbt** + sbt-jmh: `sbt "jmh:run -rf json -rff {out} ..."`

Any build tool that can invoke JMH and write JSON results to a path works.

---

## SQLite schema

For direct analysis:

```sql
-- Core tables
commits             -- sha, short_sha, message, author, timestamp, branch, epoch
runs                -- id, commit_sha, epoch, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path
test_runs           -- id, run_id, passed, tests_run, tests_failed, duration_seconds, output
benchmark_results   -- id, run_id, benchmark, mode, score, score_error, unit, params
profiles            -- id, run_id, event, file_path
settings            -- key/value store (tracks current epoch)
```

Example query — scores across commits for the current epoch:

```sql
SELECT c.short_sha, c.message, b.score, b.score_error, b.unit
FROM benchmark_results b
JOIN runs r ON b.run_id = r.id
JOIN commits c ON r.commit_sha = c.sha
WHERE r.epoch = (SELECT value FROM settings WHERE key = 'epoch')
ORDER BY c.timestamp;
```
