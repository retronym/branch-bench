# AGENTS.md — branch-bench internals for AI assistants

This file exists to give future AI assistants (or the original author) a fast, accurate mental model of this codebase. Read this before making changes.

---

## What this project does

`branch-bench` walks a git branch commit-by-commit, runs correctness tests and JMH benchmarks at each commit, collects async-profiler flamegraphs, stores everything in SQLite, and generates a self-contained HTML report. It is designed for curating performance improvement branches in JVM (Scala/Java) projects, but is build-tool agnostic.

---

## Module map

```
src/branch_bench/
├── cli.py        Click entry point. Commands: init, run, report, show, epoch, status.
├── config.py     bench.toml loading via tomllib. TEMPLATE string for init.
│                 Handles TOML parse errors with file snippet + caret marker.
├── git.py        subprocess-based git ops: list_commits, find_merge_base,
│                 checkout, restore, is_dirty, current_ref.
├── storage.py    SQLite Store class. Schema + migrations. Epoch-aware queries.
├── commands.py   run_test(), run_bench() — shell=True subprocess execution.
│                 run_bench() substitutes {out} and {out_dir}, collects *.svg/*.html
│                 flamegraphs, copies raw JMH JSON to jmh_save_dir.
│                 parse_jmh_json() with snippet error reporting on JSONDecodeError.
├── runner.py     run_branch() orchestrates the full loop.
│                 bisect_order() for commit scheduling.
│                 run_commit() runs one commit, returns bool success.
│                 Regenerates report after each commit (live_report=True).
├── report.py     generate() builds self-contained HTML.
│                 _rebase() fixes file paths to be relative to report dir.
│                 Plotly.js via CDN for charts.
└── profiler.py   Legacy stub — profiling is now handled inline via bench_cmd.
                  Not used by the main flow; safe to ignore.
```

---

## Key design decisions and their reasons

### Build-tool agnostic
`test_cmd` and `bench_cmd` are plain shell strings run with `shell=True`. No sbt/Mill-specific code exists. The user wires up their own build tool invocation.

### `{out}` and `{out_dir}` substitution
`{out}` is a temp file path for JMH's `-rff` flag (JSON results).
`{out_dir}` is a temp directory for async-profiler's `dir=` flag (flamegraph output).
Substitution happens in Python before the shell sees the string. Because `shell=True`, the user must single-quote the `-prof async:...` argument in their `bench_cmd` to protect semicolons from the shell.

### Flamegraph collection
After `run_bench` exits, `commands.py` globs `{out_dir}` for `*.svg` and `*.html`. Modern async-profiler produces interactive HTML (`flame-cpu-forward.html`, etc.), not SVG. The glob covers both. Files are moved to `.branchbench/profiles/<short_sha>-<filename>`.

### Epochs
Every `commit` and `run` row carries an `epoch` integer. `has_runs()`, `all_commits()`, `all_benchmark_names()`, and `runs_for_commit()` all filter to `current_epoch()`. `new_epoch()` increments a value in the `settings` table. Old data is preserved but invisible to normal queries. This lets the user re-run everything cleanly without losing history.

### Bisect run order
`bisect_order(n)` returns indices 0..n-1 in an order that always processes both endpoints first, then midpoints recursively. This gives a useful curve shape after only O(log n) commits, rather than waiting for a linear sweep to finish.

### Commit-0 abort gate
After processing the first commit that is actually executed (not skipped), `run_branch` checks the return value of `run_commit`. If it returned `False` (tests failed or benchmarks produced no results), the run aborts. There is no useful baseline to compare against.

### Placeholder report
Before any commits are processed, all commits are saved to the DB and `generate()` is called. The report shows them all as "pending" (dimmed). As commits complete, the report is regenerated and the browser can be refreshed.

### File paths in the report
Stored file paths (profiles, JMH JSON) are relative to `cwd` (the project root). The report HTML lives in `.branchbench/report.html`. `_rebase()` in `report.py` converts stored paths to be relative to `output_path.parent` (i.e., `.branchbench/`) so browser `href` links resolve correctly.

### Schema migrations
`Store._migrate()` runs on every `__init__`. It reads `sqlite_master` + `pragma_table_info` to find existing columns and `ALTER TABLE ... ADD COLUMN` for any that are missing. This means new columns can be added to `storage.py` without requiring users to delete their DB. Always add new columns here — never drop old ones.

---

## Data flow for one commit

```
run_branch()
  └─ for each commit (bisect order):
       run_commit()
         ├─ git.checkout(sha)
         ├─ store.create_run()           → run_id
         ├─ commands.run_test()          → TestResult
         │    store.save_test_run()
         └─ commands.run_bench()         → (results, flamegraphs, output, json_path)
              store.save_bench_output()
              store.save_jmh_json_path()
              store.save_benchmark_results()
              for each flamegraph:
                move to profiles_dir
                store.save_profile()
       report.generate()                 ← live update
  └─ git.restore(original_ref)
```

---

## SQLite schema (current)

```sql
settings            (key TEXT PK, value TEXT)
commits             (sha TEXT PK, short_sha, message, author, timestamp, branch, epoch)
runs                (id PK, commit_sha FK, epoch, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path)
test_runs           (id PK, run_id FK, passed, tests_run, tests_failed, duration_seconds, output)
benchmark_results   (id PK, run_id FK, benchmark, mode, score, score_error, unit, params)
profiles            (id PK, run_id FK, event, file_path)
```

`epoch` on both `commits` and `runs` is how the tool scopes all queries. Always filter by `current_epoch()` when adding new queries.

---

## Common failure modes and their fixes

| Symptom | Cause | Fix |
|---|---|---|
| `/bin/sh: -wi: command not found` | Shell splitting on `;` in `-prof async:...` | Single-quote the `-prof` argument in `bench_cmd` |
| `Extra data` JSON parse error | JMH was invoked without `-rf json`, produced CSV | Add `-rf json` to bench_cmd |
| `table X has no column Y` | DB created before schema migration added column Y | Old DB — either delete it or let `_migrate()` handle it (it should auto-add) |
| Old commits showing as pending | Commits saved before merge-base logic, wrong epoch | Run `branch-bench epoch` to start fresh |
| Flamegraph links 404 in browser | Paths stored relative to cwd, not to report dir | Fixed in `_rebase()` — regenerate with `branch-bench report` |
| `[!] Working tree is dirty` | Untracked files in repo | Only tracked modifications block a run; check for staged changes |

---

## Things NOT to change without understanding the implications

- **`shell=True` in commands.py** — required for the user's shell quoting of the `-prof` argument to work. Switching to a list-based `subprocess.run` would break semicolons in the profiler arguments.
- **`bisect_order` always puts index 0 first** — the commit-0 abort gate depends on `indices[0]` being the oldest commit. Don't change this.
- **`ON CONFLICT(sha) DO UPDATE SET epoch=...` in `save_commit`** — commits are upserted to update the epoch on re-registration. Don't change to `INSERT OR IGNORE`.
- **Epoch filtering in `all_commits()`** — the report and status commands rely on this to hide stale data. If you add a new "list all" query, remember to add `WHERE epoch=?`.
