"""Cold/warm split for the repeated footage-query path (scout item 6).

Item 6 lands in two halves: this benchmark/diagnostic first, the
opt-in resident worker (`library/tools/resident_query.py`) only when
the measurement justifies it. Every test here runs the fixture (a fake
embedder with controlled sleeps over `tmp_path` files), so the suite
never loads a real model, never touches the network, and never reads a
real project. The one real-model pass the item asks for is a manual
`--real --project` run, recorded separately - not this file.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools import query_benchmark
from library.tools.query_benchmark import (
    MODEL_LOAD_FRACTION_THRESHOLD,
    MODEL_LOAD_MIN_S,
    RESIDENT_RSS_BUDGET_MB,
    STAGES,
    FakeEmbedder,
    FixtureIndex,
    split_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_split_report_separates_startup_from_the_load_fraction():
    """Startup is reported but never buys residency: only the steady
    query path (load + encode + index + now) feeds the fraction."""
    rss = {"self_mb": 40.0, "children_mb": 10.0, "peak_mb": 40.0}
    report = split_report(
        {
            "process_startup_s": 100.0,
            "model_load_s": 2.0,
            "query_encode_s": 0.02,
            "index_read_s": 0.01,
            "now_read_s": 0.01,
        },
        repeats=21,
        model_backend="fake",
        cache_state={"kind": "fake"},
        rss=rss,
        real_modules=[],
    )
    assert report["cold_wall_s"] == pytest.approx(102.04)
    assert report["warm_wall_s"] == pytest.approx(0.04)
    assert report["model_load_fraction"] == pytest.approx(round(2.0 / 2.04, 4))
    assert report["verdict"]["model_load_material"] is True
    assert report["verdict"]["rss_fits_budget"] is True
    assert report["verdict"]["service_justified"] is True


def test_verdict_refuses_a_small_load_and_an_over_budget_rss():
    """Both gates must hold: a fast load is not worth a service, and a
    fat resident does not fit the Mac it would share with Resolve."""
    small = split_report(
        {
            "process_startup_s": 0.2,
            "model_load_s": 0.2,
            "query_encode_s": 0.02,
            "index_read_s": 0.01,
            "now_read_s": 0.01,
        },
        repeats=5,
        model_backend="fake",
        cache_state={"kind": "fake"},
        rss={"self_mb": 40.0, "children_mb": 0.0, "peak_mb": 40.0},
        real_modules=[],
    )
    assert small["verdict"]["model_load_material"] is False
    assert small["verdict"]["service_justified"] is False
    fat = split_report(
        {
            "process_startup_s": 0.2,
            "model_load_s": 2.0,
            "query_encode_s": 0.02,
            "index_read_s": 0.01,
            "now_read_s": 0.01,
        },
        repeats=5,
        model_backend="fake",
        cache_state={"kind": "fake"},
        rss={
            "self_mb": RESIDENT_RSS_BUDGET_MB + 1.0,
            "children_mb": 0.0,
            "peak_mb": RESIDENT_RSS_BUDGET_MB + 1.0,
        },
        real_modules=[],
    )
    assert fat["verdict"]["model_load_material"] is True
    assert fat["verdict"]["rss_fits_budget"] is False
    assert fat["verdict"]["service_justified"] is False


def test_fake_embedder_pays_load_once_and_encodes_deterministically():
    embedder = FakeEmbedder(load_s=0.01, encode_s=0.001)
    assert embedder.load() >= 0.01
    first = embedder.encode(["parking"])
    second = embedder.encode(["parking"])
    assert first == second
    assert embedder.loads == 1
    assert embedder.encodes == 2
    assert embedder.encode(["harbour"]) != first


def test_fixture_index_round_trips_a_fingerprint(tmp_path):
    index = FixtureIndex(tmp_path / "index")
    size = index.write([{"id": "a", "text": "parking"}], "fp-1")
    assert size > 0
    payload, wall_s, read_size = index.read()
    assert payload["fingerprint"] == "fp-1"
    assert read_size == size
    assert wall_s >= 0.0


def test_fixture_benchmark_reports_cold_warm_and_rss(tmp_path):
    """In-process fixture run: five named stages, cold above warm, a
    real peak RSS - and still no real model loader imported."""
    report = query_benchmark.run_fixture_benchmark(
        repeats=3, load_s=0.05, encode_s=0.002, workdir=tmp_path
    )
    assert tuple(report["stages"]) == STAGES
    assert set(report["stages"]) == set(STAGES)
    assert report["cold_wall_s"] > report["warm_wall_s"] > 0
    assert report["peak_rss_mb"]["peak_mb"] > 0
    assert report["model_backend"].startswith("fake:")
    assert report["loaded_real_modules"] == []
    assert report["cache_state"]["present"] is False


def test_fixture_benchmark_ignores_model_modules_loaded_before_call(
    tmp_path, monkeypatch
):
    """A caller's existing torch import is not attributed to the fixture."""
    monkeypatch.setitem(sys.modules, "torch", object())
    report = query_benchmark.run_fixture_benchmark(
        repeats=2, load_s=0.01, encode_s=0.001, workdir=tmp_path
    )
    assert report["loaded_real_modules"] == []


def test_benchmark_cli_reports_without_loading_a_real_model(tmp_path):
    """The same report through a FRESH interpreter: the subprocess that
    ran the fixture imported no torch/sentence-transformers at all."""
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "library.tools.query_benchmark",
            "--repeats",
            "2",
            "--load-s",
            "0.05",
            "--encode-s",
            "0.002",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            str(out),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["stages"]) == set(STAGES)
    assert report["cold_wall_s"] > report["warm_wall_s"]
    assert report["loaded_real_modules"] == []
    assert "cold" in proc.stdout and "warm" in proc.stdout


def test_thresholds_are_declared_alongside_the_budget():
    """The go/no-go numbers live next to the code that enforces them,
    not in prose: fraction, absolute floor, and resident budget."""
    assert MODEL_LOAD_FRACTION_THRESHOLD == 0.5
    assert MODEL_LOAD_MIN_S == 1.0
    assert RESIDENT_RSS_BUDGET_MB == 512.0
