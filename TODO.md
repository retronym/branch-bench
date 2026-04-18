# TODO

## Raw measurement data & adaptive sampling

### Foundation (implemented)

`rawData` from JMH's `primaryMetric` is parsed and stored as a flat JSON array in
`benchmark_results.raw_data`. Each element is one fork×iteration measurement.
The report exposes three display modes via a toggle:

- **mean ± CI** (default) — the existing score ± scoreError view
- **min / max** — asymmetric error bars showing the actual observed range
- **raw points** — individual measurements overlaid as small dots on the line,
  giving an immediate sense of distribution shape and outliers

### Aggregate runs (planned)

When two or more runs share an identical `bench_cmd`, their `raw_data` arrays can be
pooled and statistics recomputed from scratch:

```
all_values = run1.raw_data + run2.raw_data + ...
mean  = sum(all_values) / n
sem   = std(all_values) / sqrt(n)
ci99  = t_{n-1, 0.005} * sem          # two-sided 99 % CI matching JMH default
```

This makes repeated short runs (e.g. `-i 3 -f 1`) equivalent to one long run, and
lets the user accumulate evidence incrementally without re-benchmarking from scratch.
The toggle would gain an **aggregate** mode that pools before rendering.

### Adaptive sampling (planned)

Goal: given a fixed wall-clock budget, maximise the information gained about the
performance curve across the branch.

**Scheduling signal — three sources:**

1. *Error bar width* — commits whose 99 % CI is wider than a threshold (e.g. > 10 %
   of the local mean) are candidates for more iterations.
2. *Gradient* — commits near a large step change (|score[i+1] − score[i]| / mean >
   threshold) are interesting; more samples there improve the localisation of the
   change.
3. *User hints* — `bench.toml` can list SHAs of particular interest that always get
   extra budget.

**Execution model:**

After the initial bisect-order sweep finishes, `run_branch` enters a refinement
loop:

```
while budget_remaining():
    candidate = pick_best_candidate(commits, scores, errors)
    run_commit(candidate, extra_iterations=True)
    pool raw_data with prior runs for this commit
    recompute CI; update report
```

`pick_best_candidate` ranks commits by a composite score:
`uncertainty_score * gradient_weight * user_hint_weight`.

**Corput / low-discrepancy ordering:**

The initial bisect pass is already a low-discrepancy sequence over commit indices.
The refinement loop can use a van der Corput sequence in base 2 to visit commits in
an order that keeps the density of samples proportional to local gradient, without
re-visiting the same commit twice in a row.

**bench.toml additions:**

```toml
[sampling]
max_wall_minutes = 60          # total budget
target_ci_percent = 5.0        # stop refining a commit when CI < this % of mean
gradient_threshold = 0.10      # flag commits near steps larger than 10 %
priority_shas = ["abc123"]     # always refine these
```

**Implementation order:**

1. Aggregate mode in report (pool raw_data across runs) — enables incremental runs
   today without any new scheduling logic.
2. `branch-bench status` extension — show per-commit CI width and flag wide ones.
3. Refinement loop in `run_branch` — triggered by `--refine` flag or automatically
   when budget is set.
4. `bench.toml` sampling section.

---

## Flamegraph comparison UI

Add `branch-bench serve` (Python `http.server` over `.branchbench/`) to sidestep
`file:` URL browser restrictions, then:

- Checkbox column in commit table; "Compare" button appears when exactly two rows
  are checked.
- Comparison page with two `<iframe>` elements side by side, each pointing at the
  selected flamegraph. Same-origin so no CORS/sandbox issues.

## O(1) incremental report updates

Currently `generate()` re-reads every commit and all their runs from SQLite on every
call, which becomes O(N) per commit → O(N²) total for a long branch.

Design: keep a partial data structure in memory (or a JSON sidecar) that holds the
already-rendered rows, and only append the newly-finished commit's row on each
update. The Plotly charts can be updated with `Plotly.extendTraces` instead of a
full `Plotly.newPlot`. The HTML file becomes a thin shell that `fetch()`-es a
rolling `data.json` sidecar; the sidecar is append-only so writing it is O(1).
This requires `branch-bench serve` (see above) to serve the sidecar.

## Self-contained shareable report directory

Make `branch-bench export` (or a flag on `branch-bench report`) produce a
`.branchbench/export/` directory that is "zip and email" friendly:

- `report.html` with all JS/CSS inlined (no CDN dependency).
- All flamegraph HTML/SVG files copied in, with paths rewritten to be relative.
- All raw JMH JSON files copied in.
- A tiny `serve.py` (stdlib `http.server`, ~15 lines) so the recipient can just
  run `python serve.py` and open the report locally without any install.
- Optionally a diff-viewer page (two iframes) bundled alongside.
