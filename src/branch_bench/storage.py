from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
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
"""


@dataclass
class BenchmarkResult:
    benchmark: str
    mode: str
    score: float
    score_error: float | None
    unit: str
    params: dict | None = None


@dataclass
class TestResult:
    passed: bool
    tests_run: int | None = None
    tests_failed: int | None = None
    duration_seconds: float | None = None
    output: str = ""


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        migrations = [
            ("runs",    "bench_output",  "TEXT"),
            ("runs",    "epoch",         "INTEGER NOT NULL DEFAULT 1"),
            ("runs",    "jmh_json_path", "TEXT"),
            ("commits", "epoch",         "INTEGER NOT NULL DEFAULT 1"),
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
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Epoch ─────────────────────────────────────────────────────────────────

    def current_epoch(self) -> int:
        row = self._conn.execute("SELECT value FROM settings WHERE key='epoch'").fetchone()
        return int(row[0]) if row else 1

    def new_epoch(self) -> int:
        epoch = self.current_epoch() + 1
        self._conn.execute(
            "INSERT INTO settings(key,value) VALUES('epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(epoch),),
        )
        self._conn.commit()
        return epoch

    # ── Commits ───────────────────────────────────────────────────────────────

    def has_runs(self, sha: str) -> bool:
        epoch = self.current_epoch()
        row = self._conn.execute(
            "SELECT 1 FROM runs WHERE commit_sha=? AND epoch=?", (sha, epoch)
        ).fetchone()
        return row is not None

    def save_commit(self, sha: str, short_sha: str, message: str, author: str, timestamp: int, branch: str) -> None:
        epoch = self.current_epoch()
        self._conn.execute(
            "INSERT INTO commits(sha, short_sha, message, author, timestamp, branch, epoch) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(sha) DO UPDATE SET epoch=excluded.epoch",
            (sha, short_sha, message, author, timestamp, branch, epoch),
        )
        self._conn.commit()

    # ── Runs ──────────────────────────────────────────────────────────────────

    def create_run(self, commit_sha: str, bench_cmd: str | None, test_cmd: str | None) -> int:
        epoch = self.current_epoch()
        cur = self._conn.execute(
            "INSERT INTO runs(commit_sha, epoch, run_at, bench_cmd, test_cmd) VALUES(?,?,?,?,?)",
            (commit_sha, epoch, int(time.time()), bench_cmd, test_cmd),
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
        rows = [
            (run_id, r.benchmark, r.mode, r.score, r.score_error, r.unit, json.dumps(r.params) if r.params else None)
            for r in results
        ]
        self._conn.executemany(
            "INSERT INTO benchmark_results(run_id, benchmark, mode, score, score_error, unit, params) VALUES(?,?,?,?,?,?,?)",
            rows,
        )
        self._conn.commit()

    def save_profile(self, run_id: int, event: str, file_path: str) -> None:
        self._conn.execute(
            "INSERT INTO profiles(run_id, event, file_path) VALUES(?,?,?)",
            (run_id, event, file_path),
        )
        self._conn.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    def all_commits(self) -> list[dict]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT sha, short_sha, message, author, timestamp, branch FROM commits WHERE epoch=? ORDER BY timestamp ASC",
            (epoch,),
        ).fetchall()
        return [dict(zip(["sha", "short_sha", "message", "author", "timestamp", "branch"], r)) for r in rows]

    def runs_for_commit(self, commit_sha: str) -> list[dict]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT id, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path FROM runs "
            "WHERE commit_sha=? AND epoch=? ORDER BY run_at ASC",
            (commit_sha, epoch),
        ).fetchall()
        return [dict(zip(["id", "run_at", "bench_cmd", "test_cmd", "bench_output", "jmh_json_path"], r)) for r in rows]

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
            "SELECT benchmark, mode, score, score_error, unit, params FROM benchmark_results WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [dict(zip(["benchmark", "mode", "score", "score_error", "unit", "params"], r)) for r in rows]

    def profiles_for(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT event, file_path FROM profiles WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [{"event": r[0], "file_path": r[1]} for r in rows]

    def all_benchmark_names(self) -> list[str]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT DISTINCT b.benchmark FROM benchmark_results b "
            "JOIN runs r ON b.run_id=r.id WHERE r.epoch=? ORDER BY b.benchmark",
            (epoch,),
        ).fetchall()
        return [r[0] for r in rows]
