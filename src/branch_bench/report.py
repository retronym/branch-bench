from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, PackageLoader

from .storage import Store

_env = Environment(
    loader=PackageLoader("branch_bench", "templates"),
    autoescape=False,
    keep_trailing_newline=True,
)


def generate_index(cfg) -> None:
    """Generate .bench/index.html listing all epoch report directories."""
    base = cfg.base_dir()
    epoch_dirs = sorted(
        (p for p in base.iterdir() if p.is_dir() and p.name.startswith("epoch-")),
        key=lambda p: int(p.name.split("-", 1)[1]),
    ) if base.exists() else []

    rows = ""
    for ep_dir in epoch_dirs:
        report = ep_dir / "index.html"
        if not report.exists():
            continue
        num = ep_dir.name.split("-", 1)[1]
        mtime = datetime.fromtimestamp(report.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        rows += f'<tr><td><a href="{html.escape(ep_dir.name)}/">Epoch {html.escape(num)}</a></td><td>{mtime}</td></tr>\n'

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


def _bench_group(name: str) -> str:
    """Class portion of a fully-qualified JMH benchmark name (drops .methodName)."""
    idx = name.rfind(".")
    return name[:idx] if idx >= 0 else name


def _bench_display(group: str) -> str:
    """Short, human-readable label for a benchmark group (simple class name)."""
    idx = group.rfind(".")
    return group[idx + 1:] if idx >= 0 else group


def generate(store: Store, output_path: Path, github_url: str | None = None, next_sha: str | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = output_path.parent
    commits = store.all_commits()
    benchmark_names = store.all_benchmark_names()

    bench_data: dict[str, dict] = {
        name: {"unit": "", "mode": "", "points": [], "secUnits": {}} for name in benchmark_names
    }

    commit_rows = []
    for commit in commits:
        runs = store.runs_for_commit(commit["sha"])

        # Split runs by source for display
        bench_runs = [r for r in runs if (r.get("source") or "bench") == "bench"]
        profile_runs = [r for r in runs if (r.get("source") or "bench") == "profile"]

        commit_run_rows = []

        for run_index, run in enumerate(bench_runs):
            run_id = run["id"]
            test = store.test_run_for(run_id)
            bench_results = store.benchmark_results_for(run_id)
            profiles = store.profiles_for(run_id)

            secondary_metrics = store.secondary_metrics_for(run_id)
            sec_by_bench: dict[str, dict] = {}
            for sm in secondary_metrics:
                sec_by_bench.setdefault(sm["benchmark"], {})[sm["metric"]] = sm

            for r in bench_results:
                bd = bench_data.get(r["benchmark"])
                if bd is not None:
                    bd["unit"] = r["unit"]
                    bd["mode"] = r["mode"]
                    sec_snap: dict = {}
                    for metric, sm in sec_by_bench.get(r["benchmark"], {}).items():
                        bd["secUnits"][metric] = sm["unit"]
                        sec_snap[metric] = {
                            "y": sm["score"],
                            "error": sm["score_error"] if sm["score_error"] is not None else 0,
                            "raw": sm["raw_data"],
                        }
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
                        "sec": sec_snap,
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

        # Profile runs: show as a distinct list of artifact-only entries
        profile_run_rows = []
        for run_index, run in enumerate(profile_runs):
            run_id = run["id"]
            profiles = store.profiles_for(run_id)
            profile_run_rows.append({
                "run_index": run_index + 1,
                "run_at": _ts(run["run_at"]),
                "profile_cmd": run.get("bench_cmd") or "",
                "bench_output": run.get("bench_output") or "",
                "profiles": [
                    {"event": p["event"], "file_path": _rebase(p["file_path"], report_dir)}
                    for p in profiles
                ],
            })

        # Pre-computed diffs for this commit (as right side)
        raw_diffs = store.diffs_for_right_sha(commit["sha"])
        # Group by diff_vs, then list files with rebased paths
        diffs_by_vs: dict[str, list[dict]] = {}
        for d in raw_diffs:
            entry = {**d, "diff_path": _rebase(d["diff_path"], report_dir)}
            diffs_by_vs.setdefault(d["diff_vs"], []).append(entry)

        # Summary from latest bench run
        latest_test = None
        latest_scores: list[dict] = []
        if commit_run_rows:
            last = commit_run_rows[-1]
            latest_test = last["test"]
            if bench_runs:
                latest_scores = store.benchmark_results_for(bench_runs[-1]["id"])

        # Collect all profiles across bench + profile runs for the commit-level badge
        latest_bench_profiles: list[dict] = []
        if bench_runs:
            latest_bench_profiles = [
                {"event": p["event"], "file_path": _rebase(p["file_path"], report_dir)}
                for p in store.profiles_for(bench_runs[-1]["id"])
            ]
        latest_profile_profiles: list[dict] = []
        if profile_runs:
            latest_profile_profiles = [
                {"event": p["event"], "file_path": _rebase(p["file_path"], report_dir)}
                for p in store.profiles_for(profile_runs[-1]["id"])
            ]

        commit_rows.append({
            "sha": commit["sha"],
            "short_sha": commit["short_sha"],
            "message": commit["message"],
            "author": commit["author"],
            "ts": _ts(commit["timestamp"]),
            "runs": commit_run_rows,
            "profile_runs": profile_run_rows,
            "latest_test": latest_test,
            "latest_scores": latest_scores,
            "latest_bench_profiles": latest_bench_profiles,
            "latest_profile_profiles": latest_profile_profiles,
            "diffs_by_vs": diffs_by_vs,
            "in_progress": next_sha is not None and commit["sha"] == next_sha,
        })

    seen_groups: dict[str, None] = {}
    for name in benchmark_names:
        seen_groups[_bench_group(name)] = None

    current_epoch = store.current_epoch()
    all_epochs = store.all_epochs()

    tmpl = _env.get_template("report.html")
    html_content = tmpl.render(
        bench_json=json.dumps(bench_data),
        rows_json=json.dumps(commit_rows),
        github_url_json=json.dumps(github_url or ""),
        secondary_metric_names_json=json.dumps(store.all_secondary_metric_names()),
        bench_groups_json=json.dumps([
            {"group": g, "display": _bench_display(g)}
            for g in seen_groups
        ]),
        current_epoch=current_epoch,
        epoch_links_json=json.dumps([
            {"epoch": ep, "path": f"../epoch-{ep}/"}
            for ep in all_epochs
        ]),
    )
    output_path.write_text(html_content, encoding="utf-8")
