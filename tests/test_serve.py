"""`branch-bench serve`: follows the current epoch unless pinned; stubs a missing running.js."""
from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from branch_bench.cli import _make_server
from branch_bench.config import Config, OutputConfig
from branch_bench.storage import Store


def _write_report(cfg: Config, epoch: int) -> None:
    p = cfg.report_path(epoch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"<script>window.__runLog=[];</script>REPORT-EPOCH-{epoch}")


@contextmanager
def _serving(cfg: Config, epoch_override: int | None = None):
    server = _make_server(cfg, 0, branch="main", epoch_override=epoch_override)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield lambda path: _get(server.server_port, path)
    finally:
        server.shutdown()
        server.server_close()


def _get(port: int, path: str) -> tuple[int, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}") as r:
        return r.status, r.read().decode()


def _setup(tmp_path: Path) -> tuple[Config, Store]:
    cfg = Config(output=OutputConfig(dir=str(tmp_path / ".bench")))
    cfg.base_dir().mkdir(parents=True)
    store = Store(cfg.db_path())
    _write_report(cfg, 1)
    return cfg, store


def test_serve_follows_current_epoch(tmp_path):
    cfg, store = _setup(tmp_path)
    with _serving(cfg) as get:
        _, body = get("")
        assert "REPORT-EPOCH-1" in body
        assert "window.__serverEpoch=1;" in body

        store.new_epoch()
        _, body = get("")
        assert "Epoch 2 has no report yet" in body
        assert json.loads(get("api/report-mtime")[1]) == {"mtime": 0, "epoch": 2}

        _write_report(cfg, 2)
        _, body = get("")
        assert "REPORT-EPOCH-2" in body
        assert "window.__serverEpoch=2;" in body

        # Explicit ?epoch= still reaches older epochs
        assert "REPORT-EPOCH-1" in get("?epoch=1")[1]


def test_serve_pinned_epoch_stays_pinned(tmp_path):
    cfg, store = _setup(tmp_path)
    with _serving(cfg, epoch_override=1) as get:
        store.new_epoch()
        _write_report(cfg, 2)
        assert "REPORT-EPOCH-1" in get("")[1]
        assert json.loads(get("api/report-mtime")[1])["epoch"] == 1


def test_serve_report_changes_picked_up_without_restart(tmp_path):
    cfg, _ = _setup(tmp_path)
    with _serving(cfg) as get:
        assert "REPORT-EPOCH-1" in get("")[1]
        cfg.report_path(1).write_text("<script>window.__runLog=[];</script>UPDATED")
        assert "UPDATED" in get("")[1]


def test_serve_running_js(tmp_path):
    cfg, _ = _setup(tmp_path)
    with _serving(cfg) as get:
        status, body = get("running.js?123")
        assert status == 200
        assert "__runLogAbsent=true" in body

        (cfg.epoch_dir(1) / "running.js").write_text("window.__runLog=[];\n")
        status, body = get("running.js?456")
        assert status == 200
        assert body == "window.__runLog=[];\n"
