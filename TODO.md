# TODO

Items are roughly priority-ordered within sections. ✅ = implemented.

---

## Implemented (for reference)

- ✅ **Raw measurement data** — `rawData` from JMH parsed and stored in `benchmark_results.raw_data`.
- ✅ **Error bar modes** — mean ± CI / min / max / raw points toggle in report.
- ✅ **Aggregate runs mode** — pools `raw_data` across all runs for a commit, recomputes mean + 99% CI from the full sample. Available as "Aggregate" in the Runs dropdown.
- ✅ **Secondary metrics** — `secondaryMetrics` from JMH (e.g. `gc.alloc.rate.norm` from `-prof gc`) stored in `secondary_metrics` table and overlaid on charts via a right-hand Y-axis dropdown.
- ✅ **Benchmark group picker** — multi-select navbar toggle; filters charts and score cells. Groups by JMH class name. Hidden when only one class.
- ✅ **Tree-SHA reuse** — before running a commit, check if any current-epoch commit shares the same `tree_sha`; clone results instantly. Survives rebase/amend/squash.
- ✅ **Ref resolution** — `--from`/`--to`/`--sha` accept any git ref or range; resolved to full SHAs before any checkout.
- ✅ **Verbose streaming** — `-v` streams bench output live; `-vv` also streams test output.
- ✅ **`--open` flag** — opens report in browser after `run` or `report` command.
- ✅ **`migrate` command** — moves files from old layout to current `epoch-N/assets/` layout.
- ✅ **Jinja2 template** — report HTML extracted to `src/branch_bench/templates/report.html`; rendered to `epoch-N/index.html`; Python injects JSON data; JS lives in `{% raw %}` block.
- ✅ **GitHub Actions CI** — `.github/workflows/ci.yml`, Python 3.11/3.12/3.13 matrix.

---

## Near-term

### Adaptive sampling / refinement loop

After the initial bisect-order sweep, enter a refinement loop that allocates more runs to the most uncertain or most interesting commits.

**Scheduling signals:**
1. *Error bar width* — commits whose 99% CI > threshold (e.g. 10% of mean) get priority.
2. *Gradient* — commits near large step changes get extra focus for localisation.
3. *User hints* — `bench.toml` `priority_shas` always gets extra budget.

**Execution model:**
```
while budget_remaining():
    candidate = pick_best_candidate(commits, scores, errors)
    run_commit(candidate)
    pool raw_data with prior runs
    recompute CI; update report
```

**bench.toml additions needed:**
```toml
[sampling]
max_wall_minutes = 60
target_ci_percent = 5.0
gradient_threshold = 0.10
priority_shas = ["abc123"]
```

**Implementation order:**
1. `branch-bench status` extension — show per-commit CI width, flag wide ones.
2. `--refine` flag on `run` that triggers the refinement loop.
3. `bench.toml` sampling section.

---

### Flamegraph comparison UI

`branch-bench serve` (Python `http.server` over `.bench/`) to sidestep `file:` URL browser restrictions, then:

- Checkbox column in commit table; "Compare" button appears when exactly two rows are checked.
- Comparison page with two `<iframe>` elements side by side.
- Same-origin so no CORS/sandbox issues.

---

### `branch-bench export` — self-contained shareable bundle

Produce a `.bench/export/` (or zip) that is "email and open offline" friendly:

- `report.html` with Plotly.js inlined (no CDN dependency).
- All flamegraph HTML/SVG and JMH JSON files copied in, paths rewritten to be relative.
- Tiny `serve.py` (~15 lines, stdlib `http.server`) bundled alongside.

This is separate from the existing "zip epoch-N/" approach which already mostly works — this adds CDN independence and a convenience server script.

---

## Longer-term

### O(1) incremental report updates

Currently `generate()` re-reads every commit and all runs from SQLite on every call: O(N) per commit, O(N²) total for a long branch.

Design: keep an append-only `data.json` sidecar. The report HTML `fetch()`-es it on load. After each commit, only the new commit's row is appended. Plotly charts updated with `Plotly.extendTraces`. Requires `branch-bench serve` to serve the sidecar (can't `fetch()` local files).

### Multi-repo / monorepo support

Allow `bench.toml` to specify multiple `[[bench]]` entries with different `bench_cmd` strings (e.g. different JVM flag sets, or different subprojects). Each entry gets its own series in the chart.

### Baseline pinning

`--baseline SHA` flag: pin one commit as the reference; score cells and chart annotations show percentage change relative to it.
