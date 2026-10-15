import json
import threading
from pathlib import Path

import pytest

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


def test_heavy_work_queue_and_execution_are_in_the_same_profile_ledger(
        tmp_path, monkeypatch, capsys):
    from library.tools import heavy_work_lock

    path = tmp_path / "perf.jsonl"
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        tmp_path / "heavy-work.lock")
    with (perf_ledger.recording(path, "r1", "semantic_analysis"),
          heavy_work_lock.heavy_work_lock("fixture inference", "machine")):
        import time
        time.sleep(0.01)

    rows = perf_ledger.read_rows(tmp_path, path)
    report = perf_ledger.profile(rows, "r1")
    scheduled = report["scheduled_work"]
    assert scheduled["jobs"] == 1
    assert scheduled["queue_wait_s"] >= 0
    assert scheduled["execution_s"] >= 0
    assert scheduled["rows"] == [{
        "owner": "fixture inference", "profile": "machine",
        "capability": "semantic_analysis",
        "queue_wait_s": scheduled["queue_wait_s"],
        "execution_s": scheduled["execution_s"],
    }]

    assert perf_ledger.main(["--ledger", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Scheduled work: 1 job(s), queue " in output
    assert "fixture inference [machine] semantic_analysis" in output
    assert "execution " in output


def test_required_ledger_persistence_writes_rows_and_inherits_to_children(
        tmp_path):
    path = tmp_path / "workflow" / "perf_ledger.jsonl"
    parent = perf_ledger.begin(
        tmp_path, "qualification_workflow", "workflow-run",
        ledger_path_override=path, required=True)
    assert path.is_file()

    perf_ledger.count_call("cli_starts")
    child = perf_ledger.begin(tmp_path, "analysis", "analysis-run",
                              ledger_path_override=path)
    assert child is not None and child.required
    child.end()
    parent.end()

    rows = perf_ledger.read_rows(tmp_path, path, required=True)
    assert {(row["kind"], row.get("run_id")) for row in rows} == {
        (perf_ledger.COUNT, "workflow-run"),
        (perf_ledger.CAPABILITY, "workflow-run"),
        (perf_ledger.CAPABILITY, "analysis-run"),
    }


def test_required_ledger_append_surfaces_write_errors(tmp_path, monkeypatch):
    path = tmp_path / "perf.jsonl"
    real_open = open

    def failing_open(target, *args, **kwargs):
        if Path(target) == path:
            raise OSError("disk full")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    perf_ledger._append(path, {"kind": perf_ledger.SPAN})
    with pytest.raises(OSError, match="disk full"):
        perf_ledger._append(path, {"kind": perf_ledger.SPAN}, required=True)
