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
    raw_data: list[float] | None = None  # flattened fork×iteration measurements


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
            ("runs",    "bench_output",    "TEXT"),
            ("runs",    "epoch",           "INTEGER NOT NULL DEFAULT 1"),
            ("runs",    "jmh_json_path",   "TEXT"),
            ("runs",             "reused_from_sha", "TEXT"),
            ("commits",          "epoch",           "INTEGER NOT NULL DEFAULT 1"),
            ("commits",          "position",        "INTEGER NOT NULL DEFAULT 0"),
            ("commits",          "tree_sha",        "TEXT"),
            ("benchmark_results","raw_data",        "TEXT"),
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

    def has_runs(self, sha: str, run_benchmarks: bool = True, run_tests: bool = True) -> bool:
        epoch = self.current_epoch()
        if run_benchmarks:
            sql = ("SELECT 1 FROM benchmark_results b JOIN runs r ON b.run_id=r.id "
                   "WHERE r.commit_sha=? AND r.epoch=? LIMIT 1")
        elif run_tests:
            sql = ("SELECT 1 FROM test_runs t JOIN runs r ON t.run_id=r.id "
                   "WHERE r.commit_sha=? AND r.epoch=? LIMIT 1")
        else:
            sql = "SELECT 1 FROM runs WHERE commit_sha=? AND epoch=? LIMIT 1"
        return self._conn.execute(sql, (sha, epoch)).fetchone() is not None

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

    def find_run_by_tree_sha(self, tree_sha: str, exclude_sha: str) -> dict | None:
        """Return the most recent run for a commit with tree_sha in this epoch, excluding exclude_sha."""
        epoch = self.current_epoch()
        row = self._conn.execute(
            "SELECT r.id, c.short_sha FROM runs r "
            "JOIN commits c ON r.commit_sha = c.sha "
            "WHERE c.tree_sha=? AND r.epoch=? AND c.sha!=? "
            "ORDER BY r.run_at DESC LIMIT 1",
            (tree_sha, epoch, exclude_sha),
        ).fetchone()
        return {"run_id": row[0], "short_sha": row[1]} if row else None

    def clone_run(self, source_run_id: int, commit_sha: str, reused_from_sha: str) -> int:
        """Copy a run's results to a new run row for commit_sha. Returns new run_id."""
        epoch = self.current_epoch()
        source = self._conn.execute(
            "SELECT bench_cmd, test_cmd, bench_output, jmh_json_path, run_at FROM runs WHERE id=?",
            (source_run_id,),
        ).fetchone()
        cur = self._conn.execute(
            "INSERT INTO runs(commit_sha, epoch, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path, reused_from_sha) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (commit_sha, epoch, source[4], source[0], source[1], source[2], source[3], reused_from_sha),
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
            (
                run_id, r.benchmark, r.mode, r.score, r.score_error, r.unit,
                json.dumps(r.params) if r.params else None,
                json.dumps(r.raw_data) if r.raw_data else None,
            )
            for r in results
        ]
        self._conn.executemany(
            "INSERT INTO benchmark_results(run_id, benchmark, mode, score, score_error, unit, params, raw_data) VALUES(?,?,?,?,?,?,?,?)",
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
            "SELECT sha, short_sha, message, author, timestamp, branch FROM commits WHERE epoch=? ORDER BY position ASC",
            (epoch,),
        ).fetchall()
        return [dict(zip(["sha", "short_sha", "message", "author", "timestamp", "branch"], r)) for r in rows]

    def runs_for_commit(self, commit_sha: str) -> list[dict]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT id, run_at, bench_cmd, test_cmd, bench_output, jmh_json_path, reused_from_sha FROM runs "
            "WHERE commit_sha=? AND epoch=? ORDER BY run_at ASC",
            (commit_sha, epoch),
        ).fetchall()
        return [dict(zip(["id", "run_at", "bench_cmd", "test_cmd", "bench_output", "jmh_json_path", "reused_from_sha"], r)) for r in rows]

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

    def all_benchmark_names(self) -> list[str]:
        epoch = self.current_epoch()
        rows = self._conn.execute(
            "SELECT DISTINCT b.benchmark FROM benchmark_results b "
            "JOIN runs r ON b.run_id=r.id WHERE r.epoch=? ORDER BY b.benchmark",
            (epoch,),
        ).fetchall()
        return [r[0] for r in rows]
