# AGENTS.md — branch-bench internals for AI assistants

This file exists to give future AI assistants (or the original author) a fast, accurate mental model of this codebase. Read this before making changes.

---

## What this project does

`branch-bench` walks a git branch commit-by-commit, runs correctness tests and JMH benchmarks at each commit, collects async-profiler flamegraphs, stores everything in SQLite, and generates a self-contained HTML report with interactive Plotly.js charts. It is build-tool agnostic — all build interactions are shell strings configured by the user.

---

## Module map

```
src/branch_bench/
├── cli.py           Click entry point. Commands: init, run, report, show, epoch, status, migrate.
│                    _do_report() shared helper used by both `run --report` and the `report` command.
├── config.py        bench.toml loading via tomllib. TEMPLATE string for init.
│                    OutputConfig has only `dir` (default ".bench"). Handles TOML parse errors
│                    with file snippet + caret marker.
├── git.py           subprocess-based git ops: list_commits, find_merge_base, checkout, restore,
│                    is_dirty, current_ref, github_remote_url, rev_parse, expand_range.
├── storage.py       SQLite Store class. Schema + migrations. Epoch-aware queries.
│                    has_runs() requires benchmark_results rows by default (not just a run record).
├── commands.py      run_test(), run_bench() — shell=True subprocess execution.
│                    Both accept optional `tee` callback for real-time line-by-line streaming.
│                    run_bench() substitutes {out}/{out_dir}, collects *.svg/*.html flamegraphs.
│                    parse_jmh_json() skips secondary metrics with NaN score (text profilers
│                    like -prof stack produce "NaN" strings; also guarded by math.isfinite in
│                    storage.save_benchmark_results).
├── runner.py        run_branch() orchestrates the full loop.
│                    bisect_order() for commit scheduling (endpoints first, then midpoints).
│                    run_commit() runs one commit, returns bool success.
│                    _resolve_ref() / _expand_refs(): resolve git refs/ranges to full SHAs
│                    before any checkout — shown in log as "  --from resolved: 'HEAD~3' → abc1234".
│                    Tree-SHA reuse: before running a commit, checks if any commit in the current
│                    epoch shares the same tree_sha; if so, clones the results instantly.
│                    Verbose streaming: verbose=1 tees bench output; verbose=2 also tees test output.
│                    Regenerates report after each commit (live_report=True).
├── report.py        generate() builds epoch HTML report via Jinja2 template.
│                    generate_index() builds .bench/index.html (plain f-string, not Jinja2).
│                    _rebase() fixes stored file paths to be relative to the report directory.
│                    _bench_group() / _bench_display(): split JMH FQN into class / simple name.
│                    Uses jinja2.PackageLoader("branch_bench", "templates").
└── templates/
    └── report.html  Jinja2 template for the epoch report.
                     Data injected at top of <script> as {{ var | safe }} (pre-serialised JSON).
                     All JavaScript wrapped in {% raw %}...{% endraw %} — edit JS freely with
                     normal { } brace syntax, no escaping needed.
```

`profiler.py` is a legacy stub — profiling is wired into bench_cmd by the user. Not used by the main flow.

---

## Key design decisions and their reasons

### Build-tool agnostic
`test_cmd` and `bench_cmd` are plain shell strings run with `shell=True`. No sbt/Mill/Maven-specific code. The user wires up their own invocation.

### `{out}` and `{out_dir}` substitution
`{out}` — temp file path for JMH's `-rff` flag (JSON results).
`{out_dir}` — temp directory for async-profiler's `dir=` flag (flamegraph output).
Substitution happens in Python before the shell sees the string. Because `shell=True`, the user must single-quote the `-prof async:...` argument in `bench_cmd` to protect semicolons from the shell.

### Jinja2 template for the report
`report.html` lives in `src/branch_bench/templates/`. Python injects pre-serialised JSON at the top of the `<script>` block using `{{ var | safe }}`. The rest of the script (pure JavaScript) is inside `{% raw %}...{% endraw %}` so Jinja2 ignores all `{ }` braces. CSS is also outside `{% raw %}` and uses literal `{ }` — Jinja2 only triggers on `{{ }}`, not on single braces.

To add a new data variable: serialise it in Python's `generate()`, pass it to `tmpl.render()`, and add `const newVar = {{ new_var | safe }};` at the top of the script block in the template (before the `{% raw %}` tag).

### Flamegraph collection
After `run_bench` exits, `commands.py` globs `{out_dir}` recursively for `*.svg` and `*.html`. Modern async-profiler produces interactive HTML. Files are moved to `.bench/epoch-N/assets/<slug>/run-M/`.

### Epochs
Every `commit` and `run` row carries an `epoch` integer. All normal queries filter to `current_epoch()`. `new_epoch()` increments a counter in the `settings` table. Old data is preserved but invisible to normal queries. Re-running everything cleanly: `branch-bench epoch`.

### Tree-SHA reuse
Before executing a commit, `run_branch` checks whether any commit in the current epoch shares the same `tree_sha`. If found, it clones the run's results to the new commit SHA instantly (no rebuild, no JMH run). This lets the user rebase, squash, amend, or reorder commits freely — benchmark data survives as long as source content is unchanged.

### Bisect run order
`bisect_order(n)` always puts index 0 (oldest commit, the baseline) first, then n-1, then midpoints recursively. The commit-0 abort gate depends on `indices[0]` being the oldest commit.

### Commit-0 abort gate
After processing the first commit that is actually executed (not skipped/reused), `run_branch` checks the return of `run_commit`. If `False` (tests failed or no benchmark results), the run aborts. No useful baseline to compare against.

### Ref resolution
`--from`/`--to`/`--sha` all accept any git ref (`HEAD~N`, tags, branch names, SHA prefixes) or range specs (`x..y` for `--sha`). Resolution to full SHAs via `git rev-parse` and `git log` happens at the top of `run_branch`, before any `git checkout`. The resolved SHAs are what get stored and shown in logs/reports.

### `has_runs()` semantics
`has_runs(sha)` defaults to `run_benchmarks=True`, which requires at least one `benchmark_results` row — not just a `runs` record. This mirrors the runner's skip logic: a run with no benchmark data is treated as if it never ran. Tests that call `create_run` without saving results must not assert `has_runs()` returns True with defaults.

### NaN / NULL guard for secondary metrics
Python 3.14+ `sqlite3` converts `float('nan')` to SQL NULL, violating `NOT NULL`. Two guards:
1. `commands.parse_jmh_json`: skips secondary metrics where `score` is NaN (string or float).
2. `storage.save_benchmark_results`: filters `math.isfinite(sm.score)` before INSERT.
3. `storage.clone_run` and `secondary_metrics_for`: add `AND score IS NOT NULL`.

### Schema migrations
`Store._migrate()` runs on every `__init__`. It checks `sqlite_master + pragma_table_info` and `ALTER TABLE ... ADD COLUMN` for missing columns. Always add new columns here — never drop old ones. Adding a column to `SCHEMA` is not sufficient on its own for existing databases; it must also appear in `_migrate()`.

---

## Data flow for one commit

```
run_branch()
  ├─ resolve --from/--to/--sha refs to full SHAs
  ├─ git.list_commits() → all_commits
  ├─ store.save_commit() for each commit (upsert)
  ├─ store.retire_stale_commits() (removes rebased-away commits from report)
  ├─ store.backfill_by_tree_sha() (clone results to new SHAs with same tree)
  ├─ generate() → placeholder report (all pending)
  └─ for each commit (bisect order):
       ├─ [skip if has_runs or tree-SHA reuse]
       └─ run_commit()
            ├─ git.checkout(sha)
            ├─ store.create_run()           → run_id
            ├─ commands.run_test()          → TestResult
            │    store.save_test_run()
            └─ commands.run_bench()         → (results, flamegraphs, output, json_path)
                 store.save_bench_output()
                 store.save_jmh_json_path()
                 store.save_benchmark_results()   (primary + secondary metrics)
                 for each flamegraph:
                   shutil.move() to run_assets_dir
                   store.save_profile()
         generate()                         ← live update after each commit
  └─ git.restore(original_ref)
```

---

## SQLite schema (current)

```sql
settings            (key TEXT PK, value TEXT)
commits             (sha TEXT PK, short_sha, message, author, timestamp, branch,
                     epoch, position, tree_sha)
runs                (id PK, commit_sha FK, epoch, run_at, bench_cmd, test_cmd,
                     bench_output, jmh_json_path, reused_from_sha)
test_runs           (id PK, run_id FK, passed, tests_run, tests_failed,
                     duration_seconds, output)
benchmark_results   (id PK, run_id FK, benchmark, mode, score, score_error, unit,
                     params, raw_data)
secondary_metrics   (id PK, run_id FK, benchmark, metric, score NOT NULL,
                     score_error, unit, raw_data)
profiles            (id PK, run_id FK, event, file_path)
```

`epoch` on both `commits` and `runs` scopes all normal queries. Always filter by `current_epoch()` in new queries. `position` on `commits` is the display order in the report (oldest = 0). `tree_sha` on `commits` is the git tree object SHA (content hash, survives metadata rewrites).

---

## Report features (template-side)

- **Navbar**: sticky top bar with epoch picker (hidden when only one epoch), benchmark group picker (hidden when only one class), GitHub link.
- **Benchmark group picker**: multi-select toggle buttons. Empty set = all visible. Filters chart cards and score cells simultaneously.
- **Charts**: one Plotly.js chart per JMH benchmark method. Click a point to copy the full SHA.
- **Runs dropdown**: Latest / All / Aggregate / Run N. Aggregate pools `raw_data` across all runs for each commit and recomputes mean + 99% CI from the full sample.
- **Overlay dropdown**: secondary metric (e.g. `gc.alloc.rate.norm`) on a right-hand Y-axis. Hidden when no secondary metrics exist.
- **Error bars**: mean ± CI / min / max / raw points.
- **Commit table**: click to expand run rows with full stdout/stderr, reused-tree badges, flamegraph links, JMH JSON links. Score cells show method-name label when multiple benchmark classes are visible.
- **URL hash**: expanded commit SHAs are stored in the URL fragment; browser back/forward works.

---

## Output layout

```
.bench/
├── bench.db
├── index.html
└── epoch-N/
    ├── report.html
    └── assets/
        └── <short_sha>-<slug>/
            └── run-<N>/
                ├── jmh-results.json
                └── *.svg / *.html   (flamegraphs)
```

`epoch-N/` is self-contained — zip and share, or push to GitHub Pages.

---

## CI

`.github/workflows/ci.yml` runs `pytest tests/ -v` on Python 3.11, 3.12, 3.13 on every push to `main` and every PR. Install with `pip install -e ".[dev]"` (dev extra adds pytest).

---

## Common failure modes and their fixes

| Symptom | Cause | Fix |
|---|---|---|
| `/bin/sh: -wi: command not found` | Shell splitting on `;` in `-prof async:...` | Single-quote the `-prof` argument in `bench_cmd` |
| `Extra data` JSON parse error | JMH invoked without `-rf json`, produced CSV | Add `-rf json` to `bench_cmd` |
| `table X has no column Y` | DB predates schema migration | Delete DB or let `_migrate()` handle it |
| Old commits showing as pending after rebase | Stale epoch commits not retired | `branch-bench report` retires them; or `branch-bench epoch` for clean slate |
| Flamegraph links 404 | Paths stored relative to cwd, not to report dir | `_rebase()` fixes this — regenerate with `branch-bench report` |
| `[!] Working tree is dirty` | Tracked changes present | Stash or commit; untracked files are ignored |
| `NOT NULL constraint failed: secondary_metrics.score` | Python 3.14 converts `float('nan')` to NULL | Already fixed: `math.isfinite` guard in `save_benchmark_results` and NaN skip in `parse_jmh_json` |

---

## Things NOT to change without understanding the implications

- **`shell=True` in commands.py** — required for the user's shell quoting of `-prof async:...` to work. List-based subprocess would break semicolons in profiler arguments.
- **`bisect_order` always puts index 0 first** — the commit-0 abort gate depends on this. Don't reorder.
- **`ON CONFLICT(sha) DO UPDATE SET epoch=...` in `save_commit`** — commits are upserted to update epoch/position/tree_sha on re-registration. Must not be `INSERT OR IGNORE`.
- **`{% raw %}...{% endraw %}` in report.html** — all JavaScript lives inside this block. Jinja2 must not process JS braces. Never move the data injection lines (`const x = {{ y | safe }}`) inside `{% raw %}`.
- **`has_runs()` default is `run_benchmarks=True`** — the runner's skip logic requires benchmark data to consider a commit "done". If you add a `--no-bench` path that still calls `has_runs()`, pass `run_benchmarks=False`.
