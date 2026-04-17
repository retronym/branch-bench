from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .storage import Store


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


def generate(store: Store, output_path: Path) -> None:
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
                        "x": f"{commit['short_sha']} {commit['message'][:35]}",
                        "y": r["score"],
                        "error": r["score_error"] if r["score_error"] is not None else 0,
                        "run_at": _ts(run["run_at"]),
                        "bench_cmd": run["bench_cmd"] or "",
                        "short_sha": commit["short_sha"],
                        "run_index": run_index,
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
                "profiles": [
                    {"event": p["event"], "file_path": _rebase(p["file_path"], report_dir)}
                    for p in profiles
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
  tr.commit-row td {{ background: #0d1117; font-weight: 600; cursor: pointer; user-select: none; }}
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
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  details {{ margin-top: 0.4rem; }}
  summary {{
    cursor: pointer; font-size: 0.72rem; color: #8b949e;
    list-style: none; display: flex; align-items: center; gap: 0.3rem;
  }}
  summary::before {{ content: '▶'; font-size: 0.6rem; }}
  details[open] summary::before {{ content: '▼'; }}
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
</style>
</head>
<body>
<h1>branch-bench report</h1>

<div class="section">Benchmark trends</div>
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

// ── Charts ────────────────────────────────────────────────────────────────────
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

  const byRunIdx = {{}};
  for (const pt of bd.points) {{
    (byRunIdx[pt.run_index] = byRunIdx[pt.run_index] || []).push(pt);
  }}
  const palette = ['#388bfd','#3fb950','#d29922','#f85149','#bc8cff','#39d353'];
  const traces = Object.entries(byRunIdx).map(([ri, pts]) => {{
    const idx = parseInt(ri);
    const color = palette[idx % palette.length];
    return {{
      name: `run ${{idx + 1}}`,
      x: pts.map(p => p.x), y: pts.map(p => p.y),
      error_y: {{ type: 'data', array: pts.map(p => p.error), visible: true, color: color + '88' }},
      customdata: pts.map(p => [p.run_at, p.bench_cmd]),
      type: 'scatter', mode: 'lines+markers',
      marker: {{ color, size: 7 }},
      line: {{ color, dash: idx === 0 ? 'solid' : 'dot' }},
      hovertemplate:
        '<b>%{{x}}</b><br>%{{y:.4f}} ' + bd.unit + '<br>' +
        'run: %{{customdata[0]}}<br>' +
        '<span style="font-size:0.7em;color:#8b949e">%{{customdata[1]}}</span>' +
        '<extra>run ' + (idx+1) + '</extra>',
    }};
  }});
  Plotly.newPlot(plotDiv, traces, {{
    paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
    font: {{ color: '#e6edf3', size: 11 }},
    margin: {{ t: 10, r: 20, b: 90, l: 70 }},
    xaxis: {{ tickangle: -40, gridcolor: '#21262d', color: '#8b949e' }},
    yaxis: {{ title: bd.unit + ' (' + bd.mode + ')', gridcolor: '#21262d', color: '#8b949e' }},
    legend: {{ bgcolor: 'transparent', font: {{ size: 10 }} }},
    height: 300,
  }}, {{responsive: true}});
}}

// ── Commit table ──────────────────────────────────────────────────────────────
function esc(s) {{
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}}

const tbody = document.getElementById('tbody');
for (const c of commits) {{
  const runCount = c.runs.length;
  const isPending = runCount === 0;
  const commitTr = document.createElement('tr');
  commitTr.className = 'commit-row' + (isPending ? ' pending' : '');

  // Test cell
  let testCell = '<span class="na">—</span>';
  if (c.latest_test !== null && c.latest_test !== undefined) {{
    testCell = c.latest_test.passed
      ? '<span class="pass">✓</span>'
      : '<span class="fail">✗</span>';
  }}

  // Score cell — one line per benchmark
  let scoreCell = '<span class="na">—</span>';
  if (c.latest_scores && c.latest_scores.length > 0) {{
    scoreCell = c.latest_scores.map(s => {{
      const score = s.score.toPrecision(6);
      const err = s.score_error != null ? ` <span class="score-err">± ${{s.score_error.toPrecision(3)}}</span>` : '';
      const unit = `<span class="score-unit">${{esc(s.unit)}}</span>`;
      // Strip package prefix from benchmark name for display
      const shortName = s.benchmark.replace(/^.*\\.([^.]+\\.[^.]+)$/, '$1');
      return `<div class="score" title="${{esc(s.benchmark)}}">${{score}}${{err}} ${{unit}}</div>`;
    }}).join('');
  }}

  const runLabel = isPending
    ? '<span class="na">pending</span>'
    : `${{runCount}} run${{runCount !== 1 ? 's' : ''}} <span class="toggle">[expand]</span>`;

  commitTr.innerHTML = `
    <td><code style="font-size:0.78rem">${{esc(c.short_sha)}}</code></td>
    <td>${{esc(c.message.substring(0, 72))}}</td>
    <td style="white-space:nowrap">${{esc(c.ts)}}</td>
    <td>${{testCell}}</td>
    <td>${{scoreCell}}</td>
    <td>${{runLabel}}</td>
  `;
  tbody.appendChild(commitTr);

  const runRows = [];
  for (const r of c.runs) {{
    const testStatus = r.test === null
      ? '<span class="na">—</span>'
      : r.test.passed
        ? '<span class="pass">✓ pass</span>'
        : '<span class="fail">✗ fail</span>';

    const profileLinks = r.profiles.length
      ? r.profiles.map(p => `<a href="${{esc(p.file_path)}}">${{esc(p.event)}}</a>`).join(' ')
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

    const tr = document.createElement('tr');
    tr.className = 'run-row hidden';
    tr.innerHTML = `
      <td colspan="3">
        <div class="run-meta">Run #${{r.run_index}} &nbsp;·&nbsp; ${{esc(r.run_at)}}</div>
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

  commitTr.addEventListener('click', () => {{
    const hidden = runRows[0]?.classList.contains('hidden');
    runRows.forEach(tr => tr.classList.toggle('hidden', !hidden));
    const toggle = commitTr.querySelector('.toggle');
    if (toggle) toggle.textContent = hidden ? '[collapse]' : '[expand]';
  }});
}}
</script>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")
