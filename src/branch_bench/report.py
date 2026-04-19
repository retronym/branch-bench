from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .storage import Store


def generate_index(cfg) -> None:
    """Generate .bench/index.html listing all epoch report directories."""
    base = cfg.base_dir()
    epoch_dirs = sorted(
        (p for p in base.iterdir() if p.is_dir() and p.name.startswith("epoch-")),
        key=lambda p: int(p.name.split("-", 1)[1]),
    ) if base.exists() else []

    rows = ""
    for ep_dir in epoch_dirs:
        report = ep_dir / "report.html"
        if not report.exists():
            continue
        num = ep_dir.name.split("-", 1)[1]
        mtime = datetime.fromtimestamp(report.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        rows += f'<tr><td><a href="{html.escape(ep_dir.name)}/report.html">Epoch {html.escape(num)}</a></td><td>{mtime}</td></tr>\n'

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>branch-bench</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0d1117; color: #e6edf3; }}
  h1 {{ font-size: 1.1rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 1.2rem 0.4rem 0; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; font-weight: 600; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>branch-bench reports</h1>
<table>
<thead><tr><th>Epoch</th><th>Last updated</th></tr></thead>
<tbody>
{rows or '<tr><td colspan="2" style="color:#484f58">No reports yet.</td></tr>'}
</tbody>
</table>
</body>
</html>
"""
    base.mkdir(parents=True, exist_ok=True)
    cfg.index_path().write_text(content, encoding="utf-8")


def _ts(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _rebase(path_str: str, report_dir: Path) -> str:
    """Make a cwd-relative path relative to the report file's directory."""
    if not path_str:
        return path_str
    try:
        return str(Path(path_str).relative_to(report_dir))
    except ValueError:
        return path_str


def generate(store: Store, output_path: Path, github_url: str | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = output_path.parent
    commits = store.all_commits()
    benchmark_names = store.all_benchmark_names()

    bench_data: dict[str, dict] = {
        name: {"unit": "", "mode": "", "points": []} for name in benchmark_names
    }

    commit_rows = []
    for commit in commits:
        runs = store.runs_for_commit(commit["sha"])
        commit_run_rows = []

        for run_index, run in enumerate(runs):
            run_id = run["id"]
            test = store.test_run_for(run_id)
            bench_results = store.benchmark_results_for(run_id)
            profiles = store.profiles_for(run_id)

            for r in bench_results:
                bd = bench_data.get(r["benchmark"])
                if bd is not None:
                    bd["unit"] = r["unit"]
                    bd["mode"] = r["mode"]
                    bd["points"].append({
                        "x": f"{commit['message'][:40]}",
                        "y": r["score"],
                        "error": r["score_error"] if r["score_error"] is not None else 0,
                        "raw": r["raw_data"],
                        "run_at": _ts(run["run_at"]),
                        "bench_cmd": run["bench_cmd"] or "",
                        "short_sha": commit["short_sha"],
                        "run_index": run_index,
                        "sha": commit["sha"],
                    })

            commit_run_rows.append({
                "run_index": run_index + 1,
                "run_at": _ts(run["run_at"]),
                "bench_cmd": run["bench_cmd"] or "",
                "test_cmd": run["test_cmd"] or "",
                "test": test,
                "bench_count": len(bench_results),
                "bench_output": run.get("bench_output") or "",
                "jmh_json_path": _rebase(run.get("jmh_json_path") or "", report_dir),
                "reused_from_sha": run.get("reused_from_sha") or "",
                "profiles": [
                    {"event": p["event"], "file_path": _rebase(p["file_path"], report_dir)}
                    for p in profiles
                ],
                "scores": [
                    {"benchmark": r["benchmark"], "score": r["score"],
                     "score_error": r["score_error"], "unit": r["unit"],
                     "raw_data": r["raw_data"]}
                    for r in bench_results
                ],
            })

        # Summary from latest run for the commit-level row
        latest_test = None
        latest_scores: list[dict] = []
        if commit_run_rows:
            last = commit_run_rows[-1]
            latest_test = last["test"]
            latest_scores = store.benchmark_results_for(runs[-1]["id"])

        commit_rows.append({
            "sha": commit["sha"],
            "short_sha": commit["short_sha"],
            "message": commit["message"],
            "author": commit["author"],
            "ts": _ts(commit["timestamp"]),
            "runs": commit_run_rows,
            "latest_test": latest_test,
            "latest_scores": latest_scores,
        })

    bench_json = json.dumps(bench_data)
    rows_json = json.dumps(commit_rows)
    github_url_json = json.dumps(github_url or "")

    current_epoch = store.current_epoch()
    all_epochs = store.all_epochs()
    # Relative paths from epoch-N/report.html to epoch-M/report.html
    epoch_links_json = json.dumps([
        {"epoch": ep, "path": f"../epoch-{ep}/report.html"}
        for ep in all_epochs
    ])

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>branch-bench report</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js" crossorigin="anonymous"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #0d1117; color: #e6edf3; }}
  h1 {{ padding: 1rem 2rem; margin: 0; font-size: 1.2rem; background: #161b22; border-bottom: 1px solid #30363d; }}
  .section {{ padding: 0.75rem 2rem 0.25rem; font-size: 0.75rem; color: #8b949e; letter-spacing: 0.08em; text-transform: uppercase; }}
  .charts {{ display: flex; flex-wrap: wrap; gap: 1rem; padding: 0.5rem 2rem 1rem; }}
  .chart-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; flex: 1 1 600px; min-width: 0; }}
  .chart-title {{ font-size: 0.78rem; margin: 0 0 0.5rem; color: #8b949e; word-break: break-all; font-family: monospace; }}
  table {{ width: calc(100% - 4rem); margin: 0 2rem 2rem; border-collapse: collapse; font-size: 0.82rem; }}
  th, td {{ text-align: left; padding: 0.45rem 0.75rem; border-bottom: 1px solid #21262d; vertical-align: top; }}
  th {{ background: #161b22; color: #8b949e; font-weight: 600; position: sticky; top: 0; z-index: 1; }}
  tr.commit-row td {{ background: #0d1117; font-weight: 600; cursor: pointer; }}
  tr.commit-row:hover td {{ background: #161b22; }}
  tr.commit-row.pending td {{ color: #484f58; font-weight: 400; }}
  tr.run-row td {{ background: #0d1117; padding: 0.6rem 0.75rem 0.6rem 2rem; font-size: 0.78rem; }}
  tr.run-row.hidden {{ display: none; }}
  .run-meta {{ color: #8b949e; margin-bottom: 0.4rem; }}
  .cmd {{ font-family: monospace; font-size: 0.72rem; color: #58a6ff; word-break: break-all; margin: 0.2rem 0; }}
  .pass {{ color: #3fb950; font-weight: 600; }}
  .fail {{ color: #f85149; font-weight: 600; }}
  .na {{ color: #484f58; }}
  .score {{ font-family: monospace; font-size: 0.82rem; white-space: nowrap; }}
  .score-err {{ color: #8b949e; font-size: 0.75rem; }}
  .score-unit {{ color: #8b949e; font-size: 0.75rem; margin-left: 0.2rem; }}
  .toggle {{ font-size: 0.7rem; color: #388bfd; margin-left: 0.5rem; cursor: pointer; }}
  .copy-sha {{
    background: none; border: none; cursor: pointer; padding: 0 0.15rem;
    color: #484f58; font-size: 0.75rem; vertical-align: middle; line-height: 1;
    opacity: 0;
  }}
  tr.commit-row:hover .copy-sha {{ opacity: 1; }}
  .copy-sha:hover {{ color: #8b949e; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  details {{ margin-top: 0.4rem; }}
  summary {{
    cursor: pointer; font-size: 0.72rem; color: #8b949e;
    list-style: none; display: flex; align-items: center; gap: 0.3rem;
  }}
  summary::before {{ content: '▶'; font-size: 0.6rem; }}
  details[open] summary::before {{ content: '▼'; }}
  .mode-bar {{ padding: 0.25rem 2rem 0.5rem; display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }}
  .mode-label {{ font-size: 0.72rem; color: #8b949e; margin-right: 0.1rem; }}
  .mode-sep {{ color: #30363d; margin: 0 0.3rem; }}
  .mode-btn {{
    background: #21262d; border: 1px solid #30363d; color: #8b949e;
    border-radius: 4px; padding: 0.2rem 0.7rem; font-size: 0.72rem; cursor: pointer;
  }}
  .mode-btn.active {{ background: #388bfd22; border-color: #388bfd; color: #e6edf3; }}
  .mode-btn:hover {{ border-color: #8b949e; }}
  #runs-mode {{
    background: #21262d; border: 1px solid #30363d; color: #e6edf3;
    border-radius: 4px; padding: 0.2rem 0.5rem; font-size: 0.72rem; cursor: pointer;
  }}
  #runs-mode:focus {{ outline: none; border-color: #388bfd; }}
  #toast {{
    position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
    background: #388bfd; color: #fff; padding: 0.4rem 1rem; border-radius: 6px;
    font-size: 0.8rem; font-family: monospace; opacity: 0; pointer-events: none;
    transition: opacity 0.15s;
  }}
  #toast.show {{ opacity: 1; }}
  pre.output {{
    margin: 0.4rem 0 0;
    padding: 0.6rem 0.75rem;
    background: #010409;
    border: 1px solid #21262d;
    border-radius: 4px;
    font-size: 0.7rem;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 400px;
    overflow-y: auto;
    color: #c9d1d9;
  }}
  /* ── Top nav ── */
  .topnav {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 1.25rem; height: 2.6rem;
    background: #010409; border-bottom: 1px solid #21262d;
    font-size: 0.78rem; position: sticky; top: 0; z-index: 10;
  }}
  .nav-left {{ display: flex; align-items: center; gap: 0.6rem; }}
  .nav-brand {{
    font-weight: 700; font-size: 0.82rem; letter-spacing: -0.01em;
    color: #e6edf3; font-family: monospace;
  }}
  .nav-sep {{ color: #30363d; }}
  .epoch-picker {{ position: relative; }}
  .epoch-btn {{
    background: none; border: 1px solid #30363d; color: #8b949e;
    border-radius: 5px; padding: 0.18rem 0.55rem; font-size: 0.75rem;
    cursor: pointer; display: flex; align-items: center; gap: 0.3rem;
    white-space: nowrap;
  }}
  .epoch-btn:hover {{ border-color: #8b949e; color: #e6edf3; }}
  .epoch-btn .chevron {{ font-size: 0.6rem; opacity: 0.6; }}
  .epoch-menu {{
    position: absolute; top: calc(100% + 4px); left: 0;
    background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    min-width: 11rem; box-shadow: 0 8px 24px rgba(0,0,0,.5);
    z-index: 20; overflow: hidden;
  }}
  .epoch-menu.hidden {{ display: none; }}
  .epoch-filter {{
    width: 100%; background: #0d1117; border: none;
    border-bottom: 1px solid #21262d; color: #e6edf3;
    padding: 0.4rem 0.6rem; font-size: 0.75rem; outline: none;
    box-sizing: border-box;
  }}
  .epoch-filter::placeholder {{ color: #484f58; }}
  .epoch-list {{ list-style: none; margin: 0; padding: 0.25rem 0; max-height: 14rem; overflow-y: auto; }}
  .epoch-list li a {{
    display: block; padding: 0.3rem 0.75rem;
    color: #c9d1d9; text-decoration: none; font-size: 0.75rem;
  }}
  .epoch-list li a:hover {{ background: #21262d; color: #e6edf3; }}
  .epoch-list li.current a {{ color: #58a6ff; font-weight: 600; }}
  .epoch-list li.hidden {{ display: none; }}
  .nav-right {{ display: flex; align-items: center; }}
  .nav-gh {{
    display: flex; align-items: center; gap: 0.4rem;
    color: #484f58; text-decoration: none; font-size: 0.72rem;
    transition: color 0.15s;
  }}
  .nav-gh:hover {{ color: #8b949e; }}
  .nav-gh svg {{ width: 15px; height: 15px; fill: currentColor; flex-shrink: 0; }}
</style>
</head>
<body>
<div id="toast"></div>
<nav class="topnav">
  <div class="nav-left">
    <span class="nav-brand">branch-bench</span>
    <span class="nav-sep">·</span>
    <div class="epoch-picker" id="epochPicker">
      <button class="epoch-btn" id="epochBtn" onclick="toggleEpochMenu(event)">
        Epoch {current_epoch} <span class="chevron">▾</span>
      </button>
      <div class="epoch-menu hidden" id="epochMenu">
        <input class="epoch-filter" id="epochFilter" type="text" placeholder="filter epochs…"
               oninput="filterEpochs(this.value)" autocomplete="off" />
        <ul class="epoch-list" id="epochList"></ul>
      </div>
    </div>
  </div>
  <a class="nav-gh" href="https://github.com/retronym/branch-bench" target="_blank" rel="noopener">
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
               0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
               -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
               .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
               -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0
               1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82
               1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01
               1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
    </svg>
    Generated by branch-bench
  </a>
</nav>

<div class="section">Benchmark trends</div>
<div class="mode-bar">
  <span class="mode-label">runs:</span>
  <select id="runs-mode" onchange="setRunsMode(this.value)">
    <option value="latest">Latest</option>
    <option value="all">All</option>
    <option value="aggregate">Aggregate</option>
  </select>
  <span class="mode-sep">│</span>
  <span class="mode-label">error bars:</span>
  <button class="mode-btn active" data-mode="mean" onclick="setChartMode('mean')" >mean ± CI</button>
  <button class="mode-btn"        data-mode="min"  onclick="setChartMode('min')"  >min</button>
  <button class="mode-btn"        data-mode="max"  onclick="setChartMode('max')"  >max</button>
  <button class="mode-btn"        data-mode="raw"  onclick="setChartMode('raw')"  >raw points</button>
</div>
<div class="charts" id="charts"></div>

<div class="section">Commits</div>
<table>
  <thead>
    <tr>
      <th>Commit</th>
      <th>Message</th>
      <th>Date</th>
      <th>Test</th>
      <th>Benchmark</th>
      <th>Runs</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<script>
const benchData = {bench_json};
const commits = {rows_json};
const githubUrl = {github_url_json};
const currentEpoch = {current_epoch};
const epochLinks = {epoch_links_json};

// ── Epoch picker ──────────────────────────────────────────────────────────────
(function() {{
  const list = document.getElementById('epochList');
  for (const e of epochLinks) {{
    const li = document.createElement('li');
    if (e.epoch === currentEpoch) li.className = 'current';
    li.innerHTML = `<a href="${{esc(e.path)}}">Epoch ${{e.epoch}}${{e.epoch === currentEpoch ? ' ✓' : ''}}</a>`;
    list.appendChild(li);
  }}
  // Hide the button entirely when there is only one epoch
  if (epochLinks.length <= 1) {{
    document.getElementById('epochPicker').style.display = 'none';
  }}
}})();

function toggleEpochMenu(e) {{
  e.stopPropagation();
  const menu = document.getElementById('epochMenu');
  const hidden = menu.classList.toggle('hidden');
  if (!hidden) {{
    const f = document.getElementById('epochFilter');
    f.value = '';
    filterEpochs('');
    f.focus();
  }}
}}

function filterEpochs(q) {{
  const lower = q.toLowerCase();
  document.querySelectorAll('#epochList li').forEach(li => {{
    li.classList.toggle('hidden', !li.textContent.toLowerCase().includes(lower));
  }});
}}

document.addEventListener('click', e => {{
  if (!document.getElementById('epochPicker').contains(e.target)) {{
    document.getElementById('epochMenu').classList.add('hidden');
  }}
}});

let _toastTimer;
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 2000);
}}

// ── t-distribution 99% CI (two-tailed α=0.01) critical values ─────────────────
function tValue99(df) {{
  if (df <= 0) return 63.657;
  const tbl = [
    [1,63.657],[2,9.925],[3,5.841],[4,4.604],[5,4.032],[6,3.707],[7,3.499],
    [8,3.355],[9,3.250],[10,3.169],[15,2.947],[20,2.845],[30,2.750],
    [60,2.660],[120,2.617],
  ];
  if (df >= 120) return 2.576;
  for (let i = tbl.length - 1; i >= 0; i--) {{
    if (df >= tbl[i][0]) return tbl[i][1];
  }}
  return 63.657;
}}

function pooledStats(rawArrays) {{
  const all = rawArrays.flat();
  if (all.length < 2) return null;
  const n = all.length;
  const mean = all.reduce((a, b) => a + b, 0) / n;
  const variance = all.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1);
  const stderr = Math.sqrt(variance / n);
  return {{ mean, ci: tValue99(n - 1) * stderr, raw: all }};
}}

// ── Charts ────────────────────────────────────────────────────────────────────
let chartMode = 'mean'; // 'mean' | 'min' | 'max' | 'raw'
let runsMode  = 'latest'; // 'latest' | 'all' | 'aggregate' | 'run_N'
const chartRenderers = []; // functions(errMode, runsMode)

const palette = ['#388bfd','#3fb950','#d29922','#f85149','#bc8cff','#39d353'];
const categoryArray = commits.map(c => c.message.substring(0, 40));
const layout = (unit, mode_label) => ({{
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: {{ color: '#e6edf3', size: 11 }},
  margin: {{ t: 10, r: 20, b: 220, l: 70 }},
  xaxis: {{ tickangle: -55, gridcolor: '#21262d', color: '#8b949e', automargin: true,
            categoryorder: 'array', categoryarray: categoryArray }},
  yaxis: {{ title: unit + ' (' + mode_label + ')', gridcolor: '#21262d', color: '#8b949e', rangemode: 'tozero' }},
  legend: {{ bgcolor: 'transparent', font: {{ size: 10 }} }},
  height: 520,
}});

const xOrder = new Map(categoryArray.map((x, i) => [x, i]));
function byCommitOrder(pts) {{
  return [...pts].sort((a, b) => (xOrder.get(a.x) ?? Infinity) - (xOrder.get(b.x) ?? Infinity));
}}

function resolveRunGroups(bd, rMode) {{
  // Returns {{ label: string, pts: point[] }}[]
  if (rMode === 'all') {{
    const byIdx = {{}};
    for (const pt of bd.points) {{
      (byIdx[pt.run_index] = byIdx[pt.run_index] || []).push(pt);
    }}
    return Object.entries(byIdx).map(([ri, pts]) => ({{
      label: 'run ' + (parseInt(ri) + 1), pts: byCommitOrder(pts),
    }}));
  }}
  if (rMode === 'latest') {{
    const latestBySha = {{}};
    for (const pt of bd.points) {{
      if (!latestBySha[pt.sha] || pt.run_index > latestBySha[pt.sha].run_index)
        latestBySha[pt.sha] = pt;
    }}
    return [{{ label: 'latest', pts: byCommitOrder(Object.values(latestBySha)) }}];
  }}
  if (rMode.startsWith('run_')) {{
    const target = parseInt(rMode.slice(4));
    return [{{ label: 'run ' + (target + 1), pts: byCommitOrder(bd.points.filter(p => p.run_index === target)) }}];
  }}
  if (rMode === 'aggregate') {{
    const bySha = {{}};
    for (const pt of bd.points) {{
      (bySha[pt.sha] = bySha[pt.sha] || []).push(pt);
    }}
    const aggPts = Object.values(bySha).map(pts => {{
      const stats = pooledStats(pts.map(p => p.raw || []));
      const ref = pts[pts.length - 1];
      if (stats) {{
        return {{ ...ref, y: stats.mean, error: stats.ci, raw: stats.raw }};
      }}
      const mean = pts.reduce((a, p) => a + p.y, 0) / pts.length;
      return {{ ...ref, y: mean, error: ref.error, raw: null }};
    }});
    return [{{ label: 'aggregate', pts: byCommitOrder(aggPts) }}];
  }}
  return [{{ label: 'all', pts: byCommitOrder(bd.points) }}];
}}

function buildTraces(bd, errMode, rMode) {{
  const groups = resolveRunGroups(bd, rMode);
  const traces = [];
  groups.forEach(({{ label, pts }}, gi) => {{
    const color = palette[gi % palette.length];
    const hoverTmpl =
      '<b>%{{customdata[1]}}</b> %{{x}}<br>%{{y:.4f}} ' + bd.unit + '<br>' +
      '%{{customdata[0]}}<br>' +
      '<i style="font-size:0.75em;color:#8b949e">click to copy SHA</i>' +
      '<extra>' + label + '</extra>';

    const hasRaw = pts.some(p => p.raw && p.raw.length > 0);

    if (errMode === 'raw' && hasRaw) {{
      const bx = [], by = [];
      for (const pt of pts) {{
        if (pt.raw) {{ for (const v of pt.raw) {{ bx.push(pt.x); by.push(v); }} }}
        else {{ bx.push(pt.x); by.push(pt.y); }}
      }}
      traces.push({{
        name: label, x: bx, y: by,
        type: 'box',
        marker: {{ color, size: 5, opacity: 0.8 }},
        line: {{ color }},
        fillcolor: color + '22',
        boxpoints: 'all', jitter: 0.4, pointpos: 0,
        boxmean: true, whiskerwidth: 0.6,
        hovertemplate: '%{{x}}<br>%{{y:.4f}} ' + bd.unit + '<extra>' + label + '</extra>',
      }});
      return;
    }}

    let error_y;
    if (errMode === 'min' && hasRaw) {{
      error_y = {{
        type: 'data', symmetric: false, visible: true, color: color + '88',
        array:      pts.map(() => 0),
        arrayminus: pts.map(p => p.raw ? p.y - Math.min(...p.raw) : p.error),
      }};
    }} else if (errMode === 'max' && hasRaw) {{
      error_y = {{
        type: 'data', symmetric: false, visible: true, color: color + '88',
        array:      pts.map(p => p.raw ? Math.max(...p.raw) - p.y : p.error),
        arrayminus: pts.map(() => 0),
      }};
    }} else {{
      error_y = {{ type: 'data', array: pts.map(p => p.error), visible: true, color: color + '88' }};
    }}

    traces.push({{
      name: label,
      x: pts.map(p => p.x), y: pts.map(p => p.y),
      error_y,
      customdata: pts.map(p => [p.run_at, p.short_sha]),
      type: 'scatter', mode: 'lines+markers',
      marker: {{ color, size: 7 }},
      line: {{ color, dash: gi === 0 ? 'solid' : 'dot' }},
      hovertemplate: hoverTmpl,
    }});
  }});
  return traces;
}}

const chartsDiv = document.getElementById('charts');
for (const [name, bd] of Object.entries(benchData)) {{
  const card = document.createElement('div');
  card.className = 'chart-card';
  const title = document.createElement('p');
  title.className = 'chart-title';
  title.textContent = name;
  card.appendChild(title);
  const plotDiv = document.createElement('div');
  card.appendChild(plotDiv);
  chartsDiv.appendChild(card);

  const render = (eMode, rMode) => {{
    Plotly.react(plotDiv, buildTraces(bd, eMode, rMode), layout(bd.unit, bd.mode), {{responsive: true}});
    plotDiv.removeAllListeners?.('plotly_click');
    plotDiv.on('plotly_click', data => {{
      const pt = data.points[0];
      const sha = pt.customdata?.[1];
      if (!sha) return;
      navigator.clipboard.writeText(sha).then(() => showToast('Copied ' + sha));
    }});
  }};
  chartRenderers.push(render);
  render(chartMode, runsMode);
}}

// Populate "Run N" options in the dropdown
(function() {{
  const maxIdx = Math.max(-1, ...Object.values(benchData).flatMap(bd => bd.points.map(p => p.run_index)));
  const sel = document.getElementById('runs-mode');
  for (let i = 0; i <= maxIdx; i++) {{
    const opt = document.createElement('option');
    opt.value = 'run_' + i;
    opt.textContent = 'Run ' + (i + 1);
    sel.appendChild(opt);
  }}
}})();

function setChartMode(mode) {{
  chartMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === mode));
  chartRenderers.forEach(r => r(mode, runsMode));
}}

function setRunsMode(val) {{
  runsMode = val;
  chartRenderers.forEach(r => r(chartMode, runsMode));
  scoreUpdaters.forEach(fn => fn());
}}

// ── Score cell rendering (runs-mode aware) ────────────────────────────────────
const scoreUpdaters = [];

function renderScoreCell(td, c) {{
  let scores;
  if (runsMode === 'all' || runsMode === 'latest') {{
    scores = c.runs.length > 0 ? c.runs[c.runs.length - 1].scores : [];
  }} else if (runsMode.startsWith('run_')) {{
    const ri = parseInt(runsMode.slice(4));
    scores = ri < c.runs.length ? c.runs[ri].scores : [];
  }} else if (runsMode === 'aggregate') {{
    const byBench = {{}};
    for (const run of c.runs) {{
      for (const s of run.scores) {{
        (byBench[s.benchmark] = byBench[s.benchmark] || []).push(s);
      }}
    }}
    scores = Object.entries(byBench).map(([benchmark, arr]) => {{
      const stats = pooledStats(arr.map(s => s.raw_data || []));
      if (stats) return {{ benchmark, score: stats.mean, score_error: stats.ci, unit: arr[0].unit }};
      const mean = arr.reduce((a, s) => a + s.score, 0) / arr.length;
      return {{ benchmark, score: mean, score_error: null, unit: arr[0].unit }};
    }});
  }} else {{
    scores = [];
  }}

  if (!scores || scores.length === 0) {{
    td.innerHTML = '<span class="na">—</span>';
    return;
  }}
  td.innerHTML = scores.map(s => {{
    const scoreStr = s.score.toPrecision(6);
    const err = s.score_error != null
      ? ` <span class="score-err">± ${{s.score_error.toPrecision(3)}}</span>` : '';
    const unit = `<span class="score-unit">${{esc(s.unit)}}</span>`;
    return `<div class="score" title="${{esc(s.benchmark)}}">${{scoreStr}}${{err}} ${{unit}}</div>`;
  }}).join('');
}}

// ── Commit table ──────────────────────────────────────────────────────────────
const PROFILE_META = {{
  'cpu-forward':   {{ label: 'cpu (forward)',   tip: 'CPU flamegraph: roots at bottom, hot leaves at top. Shows what your code calls.' }},
  'cpu-reverse':   {{ label: 'cpu (reverse)',   tip: 'CPU flamegraph, reversed (icicle): roots at top. Shows what calls your hot code — useful for finding unexpected callers.' }},
  'alloc-forward': {{ label: 'alloc (forward)', tip: 'Allocation flamegraph: call stacks responsible for heap allocations.' }},
  'alloc-reverse': {{ label: 'alloc (reverse)', tip: 'Allocation flamegraph, reversed: shows callers of allocating methods.' }},
  'wall-forward':  {{ label: 'wall (forward)',  tip: 'Wall-clock flamegraph: includes blocked/waiting time, not just on-CPU time.' }},
  'wall-reverse':  {{ label: 'wall (reverse)',  tip: 'Wall-clock flamegraph, reversed.' }},
  'lock-forward':  {{ label: 'lock (forward)',  tip: 'Lock-contention flamegraph: call stacks holding or waiting on monitors.' }},
  'lock-reverse':  {{ label: 'lock (reverse)',  tip: 'Lock-contention flamegraph, reversed.' }},
  'cpu':   {{ label: 'cpu',   tip: 'CPU flamegraph.' }},
  'alloc': {{ label: 'alloc', tip: 'Allocation flamegraph.' }},
  'wall':  {{ label: 'wall',  tip: 'Wall-clock flamegraph.' }},
  'lock':  {{ label: 'lock',  tip: 'Lock-contention flamegraph.' }},
}};

function esc(s) {{
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}}

function expandedShas() {{
  return new Set(location.hash.slice(1).split(',').filter(Boolean));
}}

function pushHash(shas) {{
  const hash = shas.size ? '#' + [...shas].join(',') : ' ';
  history.pushState(null, '', hash);
}}

const tbody = document.getElementById('tbody');
const rowBySha = {{}};

for (const c of commits) {{
  const runCount = c.runs.length;
  const isPending = runCount === 0;
  const commitTr = document.createElement('tr');
  commitTr.className = 'commit-row' + (isPending ? ' pending' : '');
  commitTr.id = 'commit-' + c.short_sha;

  // Test cell
  let testCell = '<span class="na">—</span>';
  if (c.latest_test !== null && c.latest_test !== undefined) {{
    testCell = c.latest_test.passed
      ? '<span class="pass">✓</span>'
      : '<span class="fail">✗</span>';
  }}

  const runLabel = isPending
    ? '<span class="na">pending</span>'
    : `${{runCount}} run${{runCount !== 1 ? 's' : ''}} <span class="toggle">[expand]</span>`;

  const shaLink = githubUrl
    ? `<a href="${{githubUrl}}/commit/${{esc(c.sha)}}" target="_blank" rel="noopener"
          title="View diff on GitHub" style="font-family:monospace;font-size:0.78rem"
          onclick="event.stopPropagation()">${{esc(c.short_sha)}}</a>`
    : `<code style="font-size:0.78rem">${{esc(c.short_sha)}}</code>`;
  const copyBtn = `<button class="copy-sha" title="Copy full SHA"
    onclick="event.stopPropagation();navigator.clipboard.writeText('${{esc(c.sha)}}').then(()=>showToast('Copied ${{esc(c.short_sha)}}'))"
    >⎘</button>`;
  commitTr.innerHTML = `
    <td style="white-space:nowrap">${{shaLink}} ${{copyBtn}}</td>
    <td>${{esc(c.message.substring(0, 72))}}</td>
    <td style="white-space:nowrap">${{esc(c.ts)}}</td>
    <td>${{testCell}}</td>
    <td></td>
    <td>${{runLabel}}</td>
  `;
  tbody.appendChild(commitTr);

  // Score cell — populated dynamically via scoreUpdaters
  const scoreTd = commitTr.cells[4];
  renderScoreCell(scoreTd, c);
  scoreUpdaters.push(() => renderScoreCell(scoreTd, c));

  const runRows = [];
  for (const r of c.runs) {{
    const testStatus = r.test === null
      ? '<span class="na">—</span>'
      : r.test.passed
        ? '<span class="pass">✓ pass</span>'
        : '<span class="fail">✗ fail</span>';

    const profileLinks = r.profiles.length
      ? r.profiles.map(p => {{
          const meta = PROFILE_META[p.event] || {{ label: p.event, tip: '' }};
          return `<a href="${{esc(p.file_path)}}" title="${{esc(meta.tip)}}">${{esc(meta.label)}}</a>`;
        }}).join(' ')
      : '';
    const jmhLink = r.jmh_json_path
      ? `<a href="${{esc(r.jmh_json_path)}}" title="Raw JMH JSON">jmh.json</a>`
      : '';

    const testOutputBlock = (r.test && r.test.output)
      ? `<details><summary>test output</summary><pre class="output">${{esc(r.test.output)}}</pre></details>`
      : '';

    const benchOutputBlock = r.bench_output
      ? `<details><summary>bench output</summary><pre class="output">${{esc(r.bench_output)}}</pre></details>`
      : '';

    const reusedBadge = r.reused_from_sha
      ? ` &nbsp;<span style="color:#8b949e;font-size:0.72rem" title="Results copied from identical tree">≡ ${{esc(r.reused_from_sha)}}</span>`
      : '';

    const tr = document.createElement('tr');
    tr.className = 'run-row hidden';
    tr.innerHTML = `
      <td colspan="3">
        <div class="run-meta">Run #${{r.run_index}} &nbsp;·&nbsp; ${{esc(r.run_at)}}${{reusedBadge}}</div>
        ${{r.test_cmd ? '<div class="cmd">test: ' + esc(r.test_cmd) + '</div>' : ''}}
        ${{r.bench_cmd ? '<div class="cmd">bench: ' + esc(r.bench_cmd) + '</div>' : ''}}
        ${{testOutputBlock}}
        ${{benchOutputBlock}}
      </td>
      <td>${{testStatus}}</td>
      <td>${{r.bench_count > 0 ? r.bench_count + ' result(s)' : '<span class="na">—</span>'}}</td>
      <td>${{[profileLinks, jmhLink].filter(Boolean).join(' ')}}</td>
    `;
    tbody.appendChild(tr);
    runRows.push(tr);
  }}

  function setExpanded(expanded) {{
    runRows.forEach(tr => tr.classList.toggle('hidden', !expanded));
    const toggle = commitTr.querySelector('.toggle');
    if (toggle) toggle.textContent = expanded ? '[collapse]' : '[expand]';
  }}

  rowBySha[c.short_sha] = setExpanded;

  commitTr.addEventListener('click', () => {{
    // Don't toggle when the user is selecting text (e.g. copying the SHA)
    if (window.getSelection()?.toString()) return;
    const expanding = runRows[0]?.classList.contains('hidden');
    setExpanded(expanding);
    const shas = expandedShas();
    expanding ? shas.add(c.short_sha) : shas.delete(c.short_sha);
    pushHash(shas);
    if (expanding) commitTr.scrollIntoView({{ block: 'nearest' }});
  }});
}}

// Restore expanded state from URL hash (including on back-navigation)
function applyHash() {{
  const shas = expandedShas();
  for (const [sha, fn] of Object.entries(rowBySha)) fn(shas.has(sha));
  if (shas.size) {{
    const first = document.getElementById('commit-' + [...shas][0]);
    if (first) first.scrollIntoView({{ block: 'center' }});
  }}
}}
applyHash();
window.addEventListener('popstate', applyHash);
</script>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")
