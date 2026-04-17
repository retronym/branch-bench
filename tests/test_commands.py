import json
import tempfile
from pathlib import Path

from branch_bench.commands import parse_jmh_json
from branch_bench.storage import BenchmarkResult

JMH_SAMPLE = [
    {
        "benchmark": "com.example.MyBench.measure",
        "mode": "thrpt",
        "params": None,
        "primaryMetric": {
            "score": 1234.56,
            "scoreError": 12.3,
            "scoreUnit": "ops/s",
        },
    },
    {
        "benchmark": "com.example.MyBench.measure",
        "mode": "thrpt",
        "params": {"size": "100"},
        "primaryMetric": {
            "score": 999.0,
            "scoreError": "NaN",
            "scoreUnit": "ops/s",
        },
    },
]


def test_parse_jmh_json():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(JMH_SAMPLE, f)
        path = Path(f.name)

    results = parse_jmh_json(path)
    assert len(results) == 2

    r0 = results[0]
    assert r0.benchmark == "com.example.MyBench.measure"
    assert r0.mode == "thrpt"
    assert r0.score == 1234.56
    assert r0.score_error == 12.3
    assert r0.unit == "ops/s"
    assert r0.params is None

    r1 = results[1]
    assert r1.score_error is None  # NaN -> None
    assert r1.params == {"size": "100"}
