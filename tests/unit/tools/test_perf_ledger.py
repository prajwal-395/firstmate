from library.tools import perf_ledger


def test_profile_splits_own_code_from_external_engine_time():
    rows = [
        {"kind": "capability", "run_id": "r1", "capability": "catalog",
         "started_at": 10.0, "wall_s": 10.0, "cpu_s": 1.0},
        {"kind": "span", "run_id": "r1", "capability": "catalog",
         "layer": "ffmpeg_encode", "started_at": 12.0, "wall_s": 3.0},
        {"kind": "span", "run_id": "r1", "capability": "catalog",
         "layer": "resolve_render", "started_at": 14.0, "wall_s": 2.0},
        {"kind": "count", "run_id": "r1", "family": "probes", "count": 4},
    ]

    result = perf_ledger.profile(rows, "r1")

    assert result["own_code_s"] == 6.0
    assert result["external_s"] == 4.0
    assert result["own_code_pct"] == 60.0
    assert result["call_counts"]["probes"] == 4
    assert result["call_counts"]["broker_requests"] is None


def test_external_layer_overlap_is_subtracted_once_from_own_code():
    rows = [
        {"kind": "capability", "run_id": "r1", "capability": "render",
         "started_at": 10.0, "wall_s": 8.0},
        {"kind": "span", "run_id": "r1", "capability": "render",
         "layer": "resolve_render", "started_at": 11.0, "wall_s": 4.0},
        {"kind": "span", "run_id": "r1", "capability": "render",
         "layer": "ffmpeg_encode", "started_at": 13.0, "wall_s": 4.0},
    ]

    result = perf_ledger.profile(rows, "r1")

    assert result["own_code_s"] == 2.0
    assert result["external_s"] == 6.0
