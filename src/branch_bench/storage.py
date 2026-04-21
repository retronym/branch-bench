from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
    sha       TEXT PRIMARY KEY,
    short_sha TEXT NOT NULL,
    message   TEXT NOT NULL,
    author    TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    branch    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    commit_sha   TEXT NOT NULL REFERENCES commits(sha),
    epoch        INTEGER NOT NULL DEFAULT 1,
    run_at       INTEGER NOT NULL,
    bench_cmd    TEXT,
    test_cmd     TEXT,
    bench_output TEXT
);

CREATE TABLE IF NOT EXISTS test_runs (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    passed           INTEGER NOT NULL,
    tests_run        INTEGER,
    tests_failed     INTEGER,
    duration_seconds REAL,
    output           TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    benchmark   TEXT NOT NULL,
    mode        TEXT NOT NULL,
    score       REAL NOT NULL,
    score_error REAL,
    unit        TEXT NOT NULL,
    params      TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id        INTEGER PRIMARY KEY,
    run_id    INTEGER NOT NULL REFERENCES runs(id),
    event     TEXT NOT NULL,
    file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secondary_metrics (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    benchmark   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    score       REAL NOT NULL,
    score_error REAL,
    unit        TEXT NOT NULL,
    raw_data    TEXT
);

CREATE TABLE IF NOT EXISTS diffs (
    id          INTEGER PRIMARY KEY,
    epoch       INTEGER NOT NULL,
    left_sha    TEXT NOT NULL,
    right_sha   TEXT NOT NULL,
    diff_vs     TEXT NOT NULL,
    source_ext  TEXT NOT NULL,
    diff_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS diffs_right_sha ON diffs (epoch, right_sha);
CREATE INDEX IF NOT EXISTS diffs_pair      ON diffs (epoch, left_sha, right_sha);
"""


@dataclass
class SecondaryMetric:
    metric: str              # e.g. "·gc.alloc.rate.norm"
    score: float
    score_error: float | None
    unit: str
    raw_data: list[float] | None = None


@dataclass
class BenchmarkResult:
    benchmark: str
    mode: str
    score: float
    score_error: float | None
    unit: str
    params: dict | None = None
    raw_data: list[float] | None = None  # flattened fork×iteration measurements
    secondary_metrics: list[SecondaryMetric] | None = None


@dataclass
class TestResult:
    passed: bool
    tests_run: int | None = None
    tests_failed: int | None = None
    duration_seconds: float | None = None
    output: str = ""


class Store:
    def __init__(self, db_path: Path, epoch_override: int | None = None) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._epoch_override = epoch_override

    def _migrate(self) -> None:
        migrations = [
            ("runs",    "bench_output",    "TEXT"),
            ("runs",    "epoch",           "INTEGER NOT NULL DEFAULT 1"),
            ("runs",    "jmh_json_path",   "TEXT"),
            ("runs",             "reused_from_sha", "TEXT"),
            ("commits",          "epoch",           "INTEGER NOT NULL DEFAULT 1"),
            ("commits",          "position",        "INTEGER NOT NULL DEFAULT 0"),
            ("commits",          "tree_sha",        "TEXT"),
            ("benchmark_results","raw_data",        "TEXT"),
            # 'bench' | 'profile' — distinguishes bench_cmd runs from profile_cmd runs
            ("runs",             "source",          "TEXT NOT NULL DEFAULT 'bench'"),
        ]
        existing = {
            (row[0], row[1])
            for row in self._conn.execute(
                "SELECT m.name, p.name FROM sqlite_master m "
                "JOIN pragma_table_info(m.name) p WHERE m.type='table'"
            ).fetchall()
        }
        for table, col, col_type in migrations:
            if (table, col) not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                if (table, col) == ("commits", "position"):
                    # Backfill: commits were inserted oldest-first from git log,
                    # so rowid order faithfully reflects branch position.
                    self._conn.execute("""
                        UPDATE commits SET position = (
                            SELECT COUNT(*) FROM commits c2
                            WHERE c2.epoch = commits.epoch AND c2.rowid < commits.rowid
                        )
                    """)
                if (table, col) == ("benchmark_results", "raw_data"):
                    try:
                        self._backfill_raw_data()
                    except Exception as e:
                        print(f"[!] raw_data backfill skipped: {e}")
        self._conn.commit()

    def _backfill_raw_data(self) -> None:
        """Populate raw_data for existing benchmark_results rows from saved JMH JSON files."""
        rows = self._conn.execute(
            "SELECT br.id, br.benchmark, r.jmh_json_path "
            "FROM benchmark_results br "
            "JOIN runs r ON br.run_id = r.id "
            "WHERE r.jmh_json_path IS NOT NULL"
        ).fetchall()

        # Group result-row ids by JSON file path
        by_path: dict[str, list[tuple[int, str]]] = {}
        for row_id, benchmark, path in rows:
            by_path.setdefault(path, []).append((row_id, benchmark))

        for path_str, entries in by_path.items():
            p = Path(path_str)
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            # Build benchmark-name → flat raw_data mapping from this file
            raw_by_name: dict[str, str] = {}
            for entry in data:
                metric = entry.get("primaryMetric", {})
                nested = metric.get("rawData") or []
                flat = [v for fork in nested for v in fork]
                if flat:
                    raw_by_name[entry["benchmark"]] = json.dumps(flat)
            for row_id, benchmark in entries:
                if benchmark in raw_by_name:
                    self._conn.execute(
                        "UPDATE benchmark_results SET raw_data=? WHERE id=?",
                        (raw_by_name[benchmark], row_id),
                    )

    def close(self) -> None:
        self._conn.close()

    # ── Epoch ─────────────────────────────────────────────────────────────────

    def current_epoch(self) -> int:
        if self._epoch_override is not None:
            return self._epoch_override
        row = self._conn.execute("SELECT value FROM settings WHERE key='epoch'").fetchone()
        return int(row[0]) if row else 1

    def all_epochs(self) -> list[int]:
        rows = self._conn.execute(
            """SELECT epoch FROM (
                 SELECT DISTINCT epoch FROM commits
                 UNION
                 SELECT DISTINCT epoch FROM runs
               ) ORDER BY epoch"""
        ).fetchall()
        return [r[0] for r in rows if r[0] > 0]

    def new_epoch(self) -> int:
        epoch = self.current_epoch() + 1
        self._conn.execute(
            "INSERT INTO settings(key,value) VALUES('epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(epoch),),
        )
        self._conn.commit()
        return epoch

    # ── Commits ───────────────────────────────────────────────────────────────

    def has_runs(
        self,
        sha: str,
        run_benchmarks: bool = True,
        run_tests: bool = True,
        source: str | None = None,
    ) -> bool:
        """Check whether a commit already has runs in the current epoch.

        *source* restricts the check to runs of that source ('bench' or 'profile').
        When None, any source is considered.
        """
        epoch = self.current_epoch()
        source_filter = f" AND r.source='{source}'" if source else ""
        if run_benchmarks:
            sql = (
                f"SELECT 1 FROM benchmark_results b JOIN runs r ON b.run_id=r.id "
                f"WHERE r.commit_sha=? AND r.epoch=?{source_filter} LIMIT 1"
            )
        elif run_tests:
            sql = (
                f"SELECT 1 FROM test_runs t JOIN runs r ON t.run_id=r.id "
                f"WHERE r.commit_sha=? AND r.epoch=?{source_filter} LIMIT 1"
            )
        else:
            sql = (
                f"SELECT 1 FROM runs WHERE commit_sha=? AND epoch=?{source_filter.replace(' AND r.', ' AND ')} LIMIT 1"
                if source_filter
                else "SELECT 1 FROM runs WHERE commit_sha=? AND epoch=? LIMIT 1"
            )
        return self._conn.execute(sql, (sha, epoch)).fetchone() is not None

    def has_profile_runs(self, sha: str) -> bool:
        """Check whether a commit already has profile-source runs with artifacts."""
        epoch = self.current_epoch()
        row = self._conn.execute(
            "SELECT 1 FROM profiles p JOIN runs r ON p.run_id=r.id "
            "WHERE r.commit_sha=? AND r.epoch=? AND r.source='profile' LIMIT 1",
            (sha, epoch),
        ).fetchone()
        return row is not None

    def retire_stale_commits(self, current_shas: set[str]) -> int:
        """Remove from the current epoch any commits whose SHA is not in current_shas.
        Returns the number of commits retired."""
        epoch = self.current_epoch()
        epoch_shas = {
            row[0] for row in self._conn.execute(
                "SELECT sha FROM commits WHERE epoch=?", (epoch,)
            ).fetchall()
        }
        stale = epoch_shas - current_shas
        if not stale:
            return 0
        for sha in stale:
            self._conn.execute("UPDATE commits SET epoch=0 WHERE sha=?", (sha,))
        self._conn.commit()
        return len(stale)

    def refresh_positions(self, ordered_shas: list[str]) -> None:
        """Update position for existing current-epoch commits based on their index in ordered_shas."""
        epoch = self.current_epoch()
        for i, sha in enumerate(ordered_shas):
            self._conn.execute(
                "UPDATE commits SET position=? WHERE sha=? AND epoch=?",
                (i, sha, epoch),
            )
        self._conn.commit()

    def backfill_by_tree_sha(self) -> int:
        """Clone runs for epoch commits that have no runs but share a tree_sha with one that does.
        Returns the number of commits backfilled."""
        epoch = self.current_epoch()
        no_run = self._conn.execute(
            "SELECT sha, tree_sha, short_sha FROM commits "
            "WHERE epoch=? AND tree_sha IS NOT NULL "
            "AND sha NOT IN (SELECT DISTINCT commit_sha FROM runs WHERE epoch=?)",
            (epoch, epoch),
        ).fetchall()
        count = 0
        for sha, tree_sha, short_sha in no_run:
            source = self.find_run_by_tree_sha(tree_sha, exclude_sha=sha)
            if source:
                self.clone_run(source["run_id"], sha, reused_from_sha=source["short_sha"])
                count += 1
        return count

    def find_run_by_tree_sha(
        self, tree_sha: str, exclude_sha: str,
        require_bench: bool = False,
        require_test: bool = False,
    ) -> dict | None:
        """Return the most recent run for a commit with tree_sha in this epoch, excluding exclude_sha.

        require_bench — only match runs that have at least one benchmark_result row.
        require_test  — only match runs that have a test_run row.
        """
        epoch = self.current_epoch()
        bench_join = (
            "JOIN benchmark_results br ON br.run_id = r.id " if require_bench else ""
        )
        test_join = (
            "JOIN test_runs tr ON tr.run_id = r.id " if require_test else ""
        )
        row = self._conn.execute(
            f"SELECT r.id, c.short_sha FROM runs r "
            f"JOIN commits c ON r.commit_sha = c.sha "
            f"{bench_join}{test_join}"
            f"WHERE c.tree_sha=? AND r.epoch=? AND c.sha!=? "
            f"ORDER BY r.run_at DESC LIMIT 1",
            (tree_sha, epoch, exclude_sha),
        ).fetchone()
        return {"run_id": row[0], "short_sha": row[1]} if row else None

    def clone_run(self, source_run_id: int, commit_sha: str, reused_from_sha: str) -> int:
        """Copy a run's results to a new run row for commit_sha. Returns new run_id."""
        epoch = self.current_epoch()
        source = self._conn.execute(
            "SELECT bench_cmd, test_cmd, bench_output, jmh_json_path, run_at, source FROM runs WHERE id=?",
            (source_run_id,),
        ).fetchone()
        cur = self._conn.execute(
            "INSERT INTO runs(commit_sha, epoch, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path, reused_from_sha, source) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (commit_sha, epoch, source[4], source[0], source[1], source[2], source[3], reused_from_sha, source[5]),
        )
        new_run_id = cur.lastrowid

        self._conn.execute(
            "INSERT INTO test_runs(run_id, passed, tests_run, tests_failed, duration_seconds, output) "
            "SELECT ?, passed, tests_run, tests_failed, duration_seconds, output FROM test_runs WHERE run_id=?",
            (new_run_id, source_run_id),
        )
        self._conn.execute(
            "INSERT INTO benchmark_results(run_id, benchmark, mode, score, score_error, unit, params, raw_data) "
            "SELECT ?, benchmark, mode, score, score_error, unit, params, raw_data FROM benchmark_results WHERE run_id=?",
            (new_run_id, source_run_id),
        )
        self._conn.execute(
            "INSERT INTO profiles(run_id, event, file_path) "
            "SELECT ?, event, file_path FROM profiles WHERE run_id=?",
            (new_run_id, source_run_id),
        )
        # Exclude rows where score is NULL (e.g. NaN that was stored as NULL by older
        # Python versions before the isfinite guard was added).
        self._conn.execute(
            "INSERT INTO secondary_metrics(run_id, benchmark, metric, score, score_error, unit, raw_data) "
            "SELECT ?, benchmark, metric, score, score_error, unit, raw_data "
            "FROM secondary_metrics WHERE run_id=? AND score IS NOT NULL",
            (new_run_id, source_run_id),
        )
        self._conn.commit()
        return new_run_id

    def save_commit(self, sha: str, short_sha: str, message: str, author: str, timestamp: int, branch: str, position: int = 0, tree_sha: str = "") -> None:
        epoch = self.current_epoch()
        self._conn.execute(
            "INSERT INTO commits(sha, short_sha, message, author, timestamp, branch, epoch, position, tree_sha) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(sha) DO UPDATE SET epoch=excluded.epoch, position=excluded.position, tree_sha=excluded.tree_sha",
            (sha, short_sha, message, author, timestamp, branch, epoch, position, tree_sha or None),
        )
        self._conn.commit()

    # ── Runs ──────────────────────────────────────────────────────────────────

    def create_run(
        self,
        commit_sha: str,
        bench_cmd: str | None,
        test_cmd: str | None,
        source: str = "bench",
    ) -> int:
        epoch = self.current_epoch()
        cur = self._conn.execute(
            "INSERT INTO runs(commit_sha, epoch, run_at, bench_cmd, test_cmd, source) VALUES(?,?,?,?,?,?)",
            (commit_sha, epoch, int(time.time()), bench_cmd, test_cmd, source),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def save_bench_output(self, run_id: int, output: str) -> None:
        self._conn.execute("UPDATE runs SET bench_output=? WHERE id=?", (output, run_id))
        self._conn.commit()

    def save_jmh_json_path(self, run_id: int, path: str) -> None:
        self._conn.execute("UPDATE runs SET jmh_json_path=? WHERE id=?", (path, run_id))
        self._conn.commit()

    def save_test_run(self, run_id: int, result: TestResult) -> None:
        self._conn.execute(
            "INSERT INTO test_runs(run_id, passed, tests_run, tests_failed, duration_seconds, output) VALUES(?,?,?,?,?,?)",
            (run_id, int(result.passed), result.tests_run, result.tests_failed, result.duration_seconds, result.output),
        )
        self._conn.commit()

    def save_benchmark_results(self, run_id: int, results: list[BenchmarkResult]) -> None:
        self._conn.executemany(
            "INSERT INTO benchmark_results(run_id, benchmark, mode, score, score_error, unit, params, raw_data) VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    run_id, r.benchmark, r.mode, r.score, r.score_error, r.unit,
                    json.dumps(r.params) if r.params else None,
                    json.dumps(r.raw_data) if r.raw_data else None,
                )
                for r in results
            ],
        )
        # Python 3.14 sqlite3 converts float('nan') → SQL NULL, which violates NOT NULL.
        # Filter out any secondary metric whose score is None / NaN / ±inf.
        sec_rows = [
            (
                run_id, r.benchmark, sm.metric, sm.score, sm.score_error, sm.unit,
                json.dumps(sm.raw_data) if sm.raw_data else None,
            )
            for r in results
            for sm in (r.secondary_metrics or [])
            if sm.score is not None and math.isfinite(sm.score)
        ]
        if sec_rows:
            self._conn.executemany(
                "INSERT INTO secondary_metrics(run_id, benchmark, metric, score, score_error, unit, raw_data) VALUES(?,?,?,?,?,?,?)",
                sec_rows,
            )
        self._conn.commit()

    def save_profile(self, run_id: int, event: str, file_path: str) -> None:
        self._conn.execute(
            "INSERT INTO profiles(run_id, event, file_path) VALUES(?,?,?)",
            (run_id, event, file_path),
        )
        self._conn.commit()

    # ── Diffs ─────────────────────────────────────────────────────────────────

    def save_diff(
        self,
        epoch: int,
        left_sha: str,
        right_sha: str,
        diff_vs: str,
        source_ext: str,
        diff_path: str,
    ) -> None:
        """Store one output file produced by a diff tool invocation."""
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            "INSERT INTO diffs(epoch, left_sha, right_sha, diff_vs, source_ext, diff_path, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (epoch, left_sha, right_sha, diff_vs, source_ext, diff_path, now),
        )
        self._conn.commit()

    def diffs_for_right_sha(self, right_sha: str) -> list[dict]:
        """Return all diff records where right_sha matches, for the current epoch."""
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT left_sha, right_sha, diff_vs, source_ext, diff_path, created_at "
            "FROM diffs WHERE epoch=? AND right_sha=? ORDER BY diff_vs, source_ext, diff_path",
            (epoch, right_sha),
        ).fetchall()
        return [
            dict(zip(["left_sha", "right_sha", "diff_vs", "source_ext", "diff_path", "created_at"], r))
            for r in rows
        ]

    def delete_diffs_for_pair(self, epoch: int, left_sha: str, right_sha: str, diff_vs: str, source_ext: str) -> None:
        """Remove all stored diff rows for this exact combination (used before a forced re-run)."""
        self._conn.execute(
            "DELETE FROM diffs WHERE epoch=? AND left_sha=? AND right_sha=? AND diff_vs=? AND source_ext=?",
            (epoch, left_sha, right_sha, diff_vs, source_ext),
        )
        self._conn.commit()

    def diffs_for_pair(self, left_sha: str, right_sha: str) -> list[dict]:
        """Return diff records for an exact (left, right) pair in the current epoch."""
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT left_sha, right_sha, diff_vs, source_ext, diff_path, created_at "
            "FROM diffs WHERE epoch=? AND left_sha=? AND right_sha=? ORDER BY diff_vs, source_ext, diff_path",
            (epoch, left_sha, right_sha),
        ).fetchall()
        return [
            dict(zip(["left_sha", "right_sha", "diff_vs", "source_ext", "diff_path", "created_at"], r))
            for r in rows
        ]

    def diff_exists(self, epoch: int, left_sha: str, right_sha: str, diff_vs: str, source_ext: str) -> bool:
        """Return True if at least one diff file exists for this combination."""
        row = self._conn.execute(
            "SELECT 1 FROM diffs WHERE epoch=? AND left_sha=? AND right_sha=? AND diff_vs=? AND source_ext=? LIMIT 1",
            (epoch, left_sha, right_sha, diff_vs, source_ext),
        ).fetchone()
        return row is not None

    def best_profiles_for_commit(self, commit_sha: str) -> list[dict]:
        """Return profiles from the most recent profile run; fall back to the most recent bench run."""
        epoch = self.current_epoch()
        for source in ("profile", "bench"):
            run_row = self._conn.execute(
                "SELECT id FROM runs WHERE commit_sha=? AND epoch=? AND source=? ORDER BY run_at DESC LIMIT 1",
                (commit_sha, epoch, source),
            ).fetchone()
            if run_row:
                profiles = self.profiles_for(run_row[0])
                if profiles:
                    return profiles
        return []

    # ── Queries ───────────────────────────────────────────────────────────────

    def commit_info(self, sha: str) -> dict | None:
        """Look up a single commit by full or prefix SHA (epoch-independent)."""
        row = self._conn.execute(
            "SELECT sha, short_sha, message, author, timestamp, branch "
            "FROM commits WHERE sha=? OR sha LIKE ? LIMIT 1",
            (sha, sha + "%"),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(["sha", "short_sha", "message", "author", "timestamp", "branch"], row))

    def all_commits(self) -> list[dict]:
        epoch = self.current_epoch()
        # Use commits.epoch first (fast path); fall back to joining through runs
        # so that epochs whose commit rows were overwritten by a later epoch still work.
        rows = self._conn.execute(
            "SELECT sha, short_sha, message, author, timestamp, branch FROM commits WHERE epoch=? ORDER BY position ASC",
            (epoch,),
        ).fetchall()
        if rows:
            return [dict(zip(["sha", "short_sha", "message", "author", "timestamp", "branch"], r)) for r in rows]
        # Fallback: find commits referenced by runs for this epoch
        rows = self._conn.execute(
            """SELECT DISTINCT c.sha, c.short_sha, c.message, c.author, c.timestamp, c.branch
               FROM commits c
               JOIN runs r ON r.commit_sha = c.sha
               WHERE r.epoch = ?
               ORDER BY c.timestamp ASC""",
            (epoch,),
        ).fetchall()
        return [dict(zip(["sha", "short_sha", "message", "author", "timestamp", "branch"], r)) for r in rows]

    def runs_for_commit(self, commit_sha: str) -> list[dict]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT id, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path, reused_from_sha, source FROM runs "
            "WHERE commit_sha=? AND epoch=? ORDER BY run_at ASC",
            (commit_sha, epoch),
        ).fetchall()
        return [dict(zip(["id", "run_at", "bench_cmd", "test_cmd", "bench_output", "jmh_json_path", "reused_from_sha", "source"], r)) for r in rows]

    def test_run_for(self, run_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT passed, tests_run, tests_failed, duration_seconds, output FROM test_runs "
            "WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(["passed", "tests_run", "tests_failed", "duration_seconds", "output"], row))

    def benchmark_results_for(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT benchmark, mode, score, score_error, unit, params, raw_data FROM benchmark_results WHERE run_id=?",
            (run_id,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(zip(["benchmark", "mode", "score", "score_error", "unit", "params", "raw_data"], r))
            d["raw_data"] = json.loads(d["raw_data"]) if d["raw_data"] else None
            results.append(d)
        return results

    def profiles_for(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT event, file_path FROM profiles WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [{"event": r[0], "file_path": r[1]} for r in rows]

    def profiles_for_migration(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, event, file_path FROM profiles WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [{"id": r[0], "event": r[1], "file_path": r[2]} for r in rows]

    def run_number_for_id(self, run_id: int) -> int:
        """Return the 1-based position of run_id among all same-source runs for its commit."""
        row = self._conn.execute(
            "SELECT commit_sha, epoch, source FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            return 1
        commit_sha, epoch, source = row
        siblings = self._conn.execute(
            "SELECT id FROM runs WHERE commit_sha=? AND epoch=? AND source=? ORDER BY run_at ASC",
            (commit_sha, epoch, source),
        ).fetchall()
        for i, (rid,) in enumerate(siblings):
            if rid == run_id:
                return i + 1
        return 1

    def all_runs_with_metadata(self) -> list[dict]:
        """Return every run (all epochs) with commit info — used by migrate command."""
        rows = self._conn.execute(
            "SELECT r.id, r.epoch, r.commit_sha, r.jmh_json_path, c.short_sha, c.message, r.source "
            "FROM runs r JOIN commits c ON r.commit_sha = c.sha "
            "ORDER BY r.epoch, r.commit_sha, r.run_at ASC"
        ).fetchall()
        return [
            dict(zip(["id", "epoch", "commit_sha", "jmh_json_path", "short_sha", "message", "source"], r))
            for r in rows
        ]

    def update_profile_path(self, profile_id: int, new_path: str) -> None:
        self._conn.execute("UPDATE profiles SET file_path=? WHERE id=?", (new_path, profile_id))
        self._conn.commit()

    def update_jmh_json_path(self, run_id: int, new_path: str) -> None:
        self._conn.execute("UPDATE runs SET jmh_json_path=? WHERE id=?", (new_path, run_id))
        self._conn.commit()

    def secondary_metrics_for(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT benchmark, metric, score, score_error, unit, raw_data "
            "FROM secondary_metrics WHERE run_id=? AND score IS NOT NULL",
            (run_id,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(zip(["benchmark", "metric", "score", "score_error", "unit", "raw_data"], r))
            d["raw_data"] = json.loads(d["raw_data"]) if d["raw_data"] else None
            results.append(d)
        return results

    def all_secondary_metric_names(self) -> list[str]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT DISTINCT sm.metric FROM secondary_metrics sm "
            "JOIN runs r ON sm.run_id=r.id WHERE r.epoch=? ORDER BY sm.metric",
            (epoch,),
        ).fetchall()
        return [r[0] for r in rows]

    def all_benchmark_names(self) -> list[str]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT DISTINCT b.benchmark FROM benchmark_results b "
            "JOIN runs r ON b.run_id=r.id WHERE r.epoch=? ORDER BY b.benchmark",
            (epoch,),
        ).fetchall()
        return [r[0] for r in rows]
