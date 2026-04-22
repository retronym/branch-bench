#!/usr/bin/env python3
"""Copy benchmark/test runs and artifacts from one epoch to another.

Paths in the DB are stored relative to the repo root (the parent of .bench/),
e.g. ".bench/epoch-29/assets/...". This script rewrites them to the target
epoch and copies the actual files.

Usage:
    python scripts/copy_epoch.py <src_epoch> <dst_epoch> <bench_dir>

Example:
    python scripts/copy_epoch.py 29 35 ~/code/coursier/.bench
"""

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    src_epoch = int(sys.argv[1])
    dst_epoch = int(sys.argv[2])
    bench_dir = Path(sys.argv[3]).expanduser().resolve()
    db_path = bench_dir / "bench.db"

    if not db_path.exists():
        sys.exit(f"Database not found: {db_path}")

    # --- Backup ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = bench_dir / f"bench.db.bak-{ts}"
    shutil.copy2(db_path, backup)
    print(f"Backed up database → {backup}")

    # Paths in DB use this prefix pattern
    src_prefix = f".bench/epoch-{src_epoch}/"
    dst_prefix = f".bench/epoch-{dst_epoch}/"
    # Absolute source/dest asset roots for file copies
    src_root = bench_dir.parent  # parent of .bench/

    def rewrite(path: str | None) -> str | None:
        if path and src_prefix in path:
            return path.replace(src_prefix, dst_prefix, 1)
        return path

    def copy_file(old_path: str | None, new_path: str | None) -> None:
        if not old_path or not new_path:
            return
        src = src_root / old_path
        dst = src_root / new_path
        if not src.exists():
            print(f"  ⚠ missing: {src}")
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            # --- Runs ---
            src_runs = conn.execute(
                "SELECT * FROM runs WHERE epoch = ?", (src_epoch,)
            ).fetchall()
            print(f"\nFound {len(src_runs)} runs in epoch {src_epoch}")

            run_id_map: dict[int, int] = {}  # old_id → new_id

            for run in src_runs:
                old_id = run["id"]
                new_jmh = rewrite(run["jmh_json_path"])
                copy_file(run["jmh_json_path"], new_jmh)

                cur = conn.execute(
                    """INSERT INTO runs
                       (commit_sha, run_at, bench_cmd, test_cmd, bench_output,
                        epoch, jmh_json_path, reused_from_sha, source)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (run["commit_sha"], run["run_at"], run["bench_cmd"],
                     run["test_cmd"], run["bench_output"], dst_epoch,
                     new_jmh, run["reused_from_sha"], run["source"]),
                )
                new_id = cur.lastrowid
                run_id_map[old_id] = new_id

            print(f"  Inserted {len(run_id_map)} runs with epoch={dst_epoch}")

            # --- test_runs ---
            test_count = 0
            for old_id, new_id in run_id_map.items():
                for tr in conn.execute(
                    "SELECT * FROM test_runs WHERE run_id = ?", (old_id,)
                ).fetchall():
                    conn.execute(
                        """INSERT INTO test_runs
                           (run_id, passed, tests_run, tests_failed, duration_seconds, output)
                           VALUES (?,?,?,?,?,?)""",
                        (new_id, tr["passed"], tr["tests_run"], tr["tests_failed"],
                         tr["duration_seconds"], tr["output"]),
                    )
                    test_count += 1
            print(f"  Copied {test_count} test_runs")

            # --- benchmark_results ---
            bench_count = 0
            for old_id, new_id in run_id_map.items():
                for br in conn.execute(
                    "SELECT * FROM benchmark_results WHERE run_id = ?", (old_id,)
                ).fetchall():
                    conn.execute(
                        """INSERT INTO benchmark_results
                           (run_id, benchmark, mode, score, score_error, unit, params, raw_data)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (new_id, br["benchmark"], br["mode"], br["score"],
                         br["score_error"], br["unit"], br["params"], br["raw_data"]),
                    )
                    bench_count += 1
            print(f"  Copied {bench_count} benchmark_results")

            # --- secondary_metrics ---
            sec_count = 0
            for old_id, new_id in run_id_map.items():
                for sm in conn.execute(
                    "SELECT * FROM secondary_metrics WHERE run_id = ?", (old_id,)
                ).fetchall():
                    conn.execute(
                        """INSERT INTO secondary_metrics
                           (run_id, benchmark, metric, score, score_error, unit, raw_data)
                           VALUES (?,?,?,?,?,?,?)""",
                        (new_id, sm["benchmark"], sm["metric"], sm["score"],
                         sm["score_error"], sm["unit"], sm["raw_data"]),
                    )
                    sec_count += 1
            print(f"  Copied {sec_count} secondary_metrics")

            # --- profiles (copy files too) ---
            prof_count = 0
            for old_id, new_id in run_id_map.items():
                for p in conn.execute(
                    "SELECT * FROM profiles WHERE run_id = ?", (old_id,)
                ).fetchall():
                    new_path = rewrite(p["file_path"])
                    copy_file(p["file_path"], new_path)
                    conn.execute(
                        "INSERT INTO profiles (run_id, event, file_path) VALUES (?,?,?)",
                        (new_id, p["event"], new_path),
                    )
                    prof_count += 1
            print(f"  Copied {prof_count} profiles (+ artifact files)")

            # --- diffs ---
            src_diffs = conn.execute(
                "SELECT * FROM diffs WHERE epoch = ?", (src_epoch,)
            ).fetchall()
            print(f"\nFound {len(src_diffs)} diffs in epoch {src_epoch}")

            diff_count = 0
            for d in src_diffs:
                new_path = rewrite(d["diff_path"])
                copy_file(d["diff_path"], new_path)
                conn.execute(
                    """INSERT INTO diffs
                       (epoch, left_sha, right_sha, diff_vs, source_ext, diff_path, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (dst_epoch, d["left_sha"], d["right_sha"], d["diff_vs"],
                     d["source_ext"], new_path, d["created_at"]),
                )
                diff_count += 1
            print(f"  Inserted {diff_count} diffs with epoch={dst_epoch}")

            # --- settings (epoch_head, epoch_base) ---
            for suffix in ["head", "base"]:
                key = f"epoch_{src_epoch}_{suffix}"
                res = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if res:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (f"epoch_{dst_epoch}_{suffix}", res["value"]),
                    )
            print(f"  Copied epoch_{dst_epoch}_head/base settings")

    finally:
        conn.close()

    print(f"\n✓ Done. epoch {src_epoch} → epoch {dst_epoch}")
    print(f"  DB backup: {backup}")


if __name__ == "__main__":
    main()
