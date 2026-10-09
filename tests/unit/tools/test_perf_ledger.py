import json
import threading

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


def test_recording_is_thread_local_and_counts_calls(tmp_path):
    path = tmp_path / "perf.jsonl"
    rows_before = perf_ledger.read_rows(tmp_path, path)
    assert rows_before == []

    def work():
        with (perf_ledger.recording(path, "r1", "qualification_workflow"),
              perf_ledger.span("fframes_render", calls=2, subprocesses=1)):
            pass

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()

    rows = perf_ledger.read_rows(tmp_path, path)
    counts = {row["family"]: row["count"] for row in rows
              if row["kind"] == perf_ledger.COUNT}
    assert counts == {"subprocess_spawns": 1, "graphics_renders": 2}
    assert all(row["run_id"] == "r1" for row in rows)
    assert all(row["capability"] == "qualification_workflow" for row in rows)


def test_profile_cli_reads_a_standalone_ledger(tmp_path, capsys):
    path = tmp_path / "perf.jsonl"
    path.write_text(json.dumps({
        "kind": "capability", "run_id": "r1",
        "capability": "qualification_workflow", "wall_s": 1.0,
    }) + "\n", encoding="utf-8")

    assert perf_ledger.main(["--ledger", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Ren own code 1.0s (100.0%)" in output
    assert "qualification_workflow" in output
