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


def test_resolve_kpis_report_api_time_separately_from_wait_and_hold():
    rows = [
        {"kind": "capability", "run_id": "r1", "capability": "edit",
         "started_at": 10.0, "wall_s": 5.0},
        {"kind": "span", "run_id": "r1", "capability": "edit",
         "layer": perf_ledger.RESOLVE_WAIT, "started_at": 10.0,
         "wall_s": 0.5, "outcome": "granted"},
        {"kind": "span", "run_id": "r1", "capability": "edit",
         "layer": perf_ledger.RESOLVE_HOLD, "started_at": 10.5,
         "wall_s": 1.5, "held_s": 1.5, "exclusive": True},
        {"kind": "span", "run_id": "r1", "capability": "edit",
         "layer": "resolve_api_call", "started_at": 10.6,
         "wall_s": 0.4, "calls": 2},
    ]

    resolve = perf_ledger.profile(rows, "r1")["resolve"]

    assert resolve["blocked_s"] == 0.5
    assert resolve["exclusive_held_s"] == 1.5
    assert resolve["api_s"] == 0.4
    assert resolve["api_calls"] == 2


def test_the_resolve_kpi_separates_project_loads_from_avoided_loads():
    """Defect: the single-instance KPI said how many TIMELINE switches a
    run made and nothing about the PROJECTS it loaded - so a run that
    reloaded an already-open project read as a clean run."""
    rows = [
        {"kind": "capability", "run_id": "r1", "capability": "edit",
         "started_at": 10.0, "wall_s": 5.0},
        {"kind": "span", "run_id": "r1", "capability": "edit",
         "layer": perf_ledger.RESOLVE_HOLD, "started_at": 10.0,
         "wall_s": 1.5, "held_s": 1.5, "exclusive": True},
        {"kind": "count", "run_id": "r1", "family": "project_switches",
         "count": 2},
        {"kind": "count", "run_id": "r1",
         "family": "project_switches_avoided", "count": 5},
    ]

    resolve = perf_ledger.profile(rows, "r1")["resolve"]

    assert resolve["project_switches"] == 2
    assert resolve["project_switches_avoided"] == 5


def test_an_uninstrumented_project_switch_family_reads_as_unmeasured():
    """A count family nobody wrote a row for is `None`, never `0`: the
    difference is what keeps the KPI honest."""
    rows = [
        {"kind": "capability", "run_id": "r1", "capability": "edit",
         "started_at": 10.0, "wall_s": 5.0},
    ]

    resolve = perf_ledger.profile(rows, "r1")["resolve"]

    assert resolve["project_switches"] is None
    assert resolve["project_switches_avoided"] is None


def test_a_phase_longer_than_its_run_is_clamped(tmp_path):
    """A phase cannot outlast the run that recorded it.

    The phantom `otio_placement.fusion_comps_s` read 36 hours of a
    phase that took under a minute: a start timestamp that was really
    an elapsed average was subtracted from the monotonic clock, and both
    `ren profile` and the critical path read it as engine time. The
    run's own id carries the instant it started, so a phase longer than
    the run is clamped - and the row says it was clamped.
    """
    from library.tools import run_id as run_ids

    ledger = tmp_path / "perf.jsonl"
    run = run_ids.mint_run_id()

    with perf_ledger.recording(ledger, run, "reel_build"):
        perf_ledger.record("otio_placement.fusion_comps_s", 131422.0)

    rows = perf_ledger.read_rows(tmp_path, ledger)
    row = next(r for r in rows
               if r.get("layer") == "otio_placement.fusion_comps_s")
    assert row["clamped_from_s"] == pytest.approx(131422.0)
    assert row["wall_s"] < 5.0, "a phase cannot outlast its run"
    assert row["wall_s"] < row["clamped_from_s"]


def test_a_phase_inside_its_run_is_not_clamped(tmp_path):
    """The clamp touches the impossible, not the measured.

    The run id carries the instant the run started, so the run's own
    wall is the ceiling. A phase that fits inside it is left alone.
    """
    import time

    from library.tools import run_id as run_ids

    ledger = tmp_path / "perf.jsonl"
    run = run_ids.mint_run_id(now=time.time() - 30.0)

    with perf_ledger.recording(ledger, run, "reel_build"):
        perf_ledger.record("otio_placement.fusion_comps_s", 0.5)

    rows = perf_ledger.read_rows(tmp_path, ledger)
    row = next(r for r in rows
               if r.get("layer") == "otio_placement.fusion_comps_s")
    assert "clamped_from_s" not in row
    assert row["wall_s"] == pytest.approx(0.5)


def test_a_run_id_this_module_did_not_mint_is_not_clamped(tmp_path):
    """An unknown run has no window to clamp against.

    A wrongly clamped row is worse than an unclamped one, so a run id
    from another scheme is left alone.
    """
    ledger = tmp_path / "perf.jsonl"

    with perf_ledger.recording(ledger, "r1", "reel_build"):
        perf_ledger.record("otio_placement.fusion_comps_s", 131422.0)

    rows = perf_ledger.read_rows(tmp_path, ledger)
    row = next(r for r in rows
               if r.get("layer") == "otio_placement.fusion_comps_s")
    assert "clamped_from_s" not in row
    assert row["wall_s"] == pytest.approx(131422.0)


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


def test_media_decode_rows_match_source_and_purpose_and_refresh_on_mutation(
        tmp_path):
    ledger = tmp_path / "perf.jsonl"
    source = tmp_path / "source.mov"
    source.write_bytes(b"first source revision")

    with perf_ledger.recording(ledger, "r1", "semantic_analysis"):
        for _ in range(2):
            with perf_ledger.media_decode(
                    source, purpose="semantic.coarse_frames",
                    media_kind="video", access_pattern="random_seek",
                    calls=3):
                pass
        source.write_bytes(b"second source revision")
        with perf_ledger.media_decode(
                source, purpose="semantic.coarse_frames",
                media_kind="video", access_pattern="random_seek",
                calls=3):
            pass
        with perf_ledger.media_decode(
                source, purpose="semantic.coarse_frames",
                media_kind="video", access_pattern="random_seek",
                calls=0, cache_hits=3):
            pass

    rows = perf_ledger.read_rows(tmp_path, ledger)
    decodes = [row for row in rows
               if row.get("layer") == "media_decode"]
    assert len(decodes) == 4
    assert decodes[0]["source_fingerprint"] == decodes[1]["source_fingerprint"]
    assert decodes[2]["source_fingerprint"] != decodes[1]["source_fingerprint"]
    assert all(row["decode_purpose"] == "semantic.coarse_frames"
               for row in decodes)
    assert all(row["source_changed_during_decode"] is False
               for row in decodes)
    requests = [row for row in rows
                if row.get("family") == "media_decode_requests"]
    assert [row["count"] for row in requests] == [3, 3, 3]
    cache_hits = [row for row in rows
                  if row.get("family") == "media_decode_cache_hits"]
    assert [row["count"] for row in cache_hits] == [3]


def test_record_media_decode_measures_requests_and_cache_hits(tmp_path):
    ledger = tmp_path / "perf.jsonl"
    with perf_ledger.recording(ledger, "r1", "semantic_analysis"):
        perf_ledger.record(
            "media_decode", 2.5, started_at=10.0,
            decode_purpose="semantic.action_windows", media_kind="video",
            access_pattern="random_seek", source_fingerprint="abc",
            source_changed_during_decode=False, calls=2, cache_hits=1,
            bytes_written=1024)

    rows = perf_ledger.read_rows(tmp_path, ledger)
    measured = [row for row in rows if row.get("layer") == "media_decode"]
    assert len(measured) == 1
    assert measured[0]["decode_purpose"] == "semantic.action_windows"
    assert measured[0]["source_fingerprint"] == "abc"
    assert measured[0]["calls"] == 2
    assert measured[0]["cache_hits"] == 1
    assert measured[0]["bytes_written"] == 1024
    assert [(row["family"], row["count"]) for row in rows
            if row.get("kind") == "count"] == [
                ("media_decode_requests", 2),
                ("media_decode_cache_hits", 1)]


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


def test_the_repeated_work_report_names_the_source_measured_again(
        tmp_path, capsys):
    """`ren profile --repeated` says which source was measured twice.

    Defect caught: the ledger carries each decode's source identity
    and nothing ever reads it, so the same footage measured again for
    every edit stays invisible.
    """
    path = tmp_path / "perf.jsonl"
    rows = [
        {"kind": perf_ledger.COUNT, "family": "media_decode_requests",
         "count": 1, "run_id": "r1", "capability": "semantic_analysis",
         "source_fingerprint": "abc123",
         "decode_purpose": "semantic.coarse_frames"},
        {"kind": perf_ledger.COUNT, "family": "media_decode_requests",
         "count": 1, "run_id": "r2", "capability": "semantic_analysis",
         "source_fingerprint": "abc123",
         "decode_purpose": "semantic.coarse_frames"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")

    assert perf_ledger.main(["--ledger", str(path), "--repeated"]) == 0
    output = capsys.readouterr().out
    assert "abc123" in output
    assert "semantic.coarse_frames" in output
    assert "1 paid repeat" in output


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


def test_reel_edit_performance_records_every_required_measurement(
        tmp_path, monkeypatch):
    path = tmp_path / "perf.jsonl"
    monkeypatch.setenv(perf_ledger.LEDGER_ENV, str(path))
    monkeypatch.setenv(perf_ledger.RUN_ENV, "edit-run")
    monkeypatch.setenv(perf_ledger.CAPABILITY_ENV, "reel.build")
    values = {name: None for name in perf_ledger.REEL_EDIT_METRICS}
    values.update(total_s=14.5, cpu_s=3.0, attempts=2,
                  full_rebuilds=1, items_touched=8,
                  model_records_touched=3, project_switch_s=1.25,
                  project_switches=1)

    perf_ledger.record_reel_edit("Reel 07", 1000.0, values)

    row = perf_ledger.read_rows(tmp_path, path)[0]
    assert row["layer"] == "reel_edit"
    assert row["reel"] == "Reel 07"
    assert set(row["measurements"]) == set(perf_ledger.REEL_EDIT_METRICS)
    assert row["measurements"]["attempts"] == 2
    assert row["measurements"]["lease_queue_wait_s"] is None


def test_reel_edit_performance_refuses_an_omitted_measurement():
    with pytest.raises(ValueError, match="missing"):
        perf_ledger.record_reel_edit("Reel 07", 1000.0, {})


def test_edit_lease_totals_include_only_linked_waits_and_holds():
    totals = perf_ledger.edit_lease_totals([
        {"layer": perf_ledger.RESOLVE_WAIT, "wall_s": 0.25,
         "edit_ids": ["Reel 07"]},
        {"layer": perf_ledger.RESOLVE_HOLD, "wall_s": 8.0,
         "held_s": 7.75, "edit_ids": ["Reel 07", "Reel 08"]},
        {"layer": "project_switch", "wall_s": 2.25, "switches": 1,
         "edit_ids": ["Reel 07"]},
        {"layer": perf_ledger.RESOLVE_WAIT, "wall_s": 9.0,
         "edit_ids": ["Reel 09"]},
    ])

    assert totals == {
        "Reel 07": {"lease_queue_wait_s": 0.25,
                    "lease_hold_s": 7.75,
                    "project_switch_s": 2.25, "project_switches": 1,
                    "project_switches_avoided": 0},
        "Reel 08": {"lease_queue_wait_s": 0.0,
                    "lease_hold_s": 7.75,
                    "project_switch_s": 0.0, "project_switches": 0,
                    "project_switches_avoided": 0},
        "Reel 09": {"lease_queue_wait_s": 9.0,
                    "lease_hold_s": 0.0,
                    "project_switch_s": 0.0, "project_switches": 0,
                    "project_switches_avoided": 0},
    }


def test_a_task_records_the_latency_the_person_waited(tmp_path):
    """A task is a request and its completion, with the wait between them.

    Defect caught: no row saying how long the person waited, so the
    profile can only report tool durations and the gap between them -
    the wait - reads as nothing.
    """
    path = tmp_path / "perf.jsonl"
    task = perf_ledger.begin_task(tmp_path, "reels.build", run_id="r1",
                                  ledger_path_override=path)
    assert task is not None
    task.end(outcome="ok")

    rows = perf_ledger.read_rows(tmp_path, path)
    kinds = [(row["kind"], row["state"]) for row in rows]
    assert kinds == [(perf_ledger.TASK, "requested"),
                     (perf_ledger.TASK, "completed")]
    assert rows[1]["latency_s"] >= 0.0
    assert rows[1]["task_class"] == "reels.build"


def test_a_task_that_cannot_be_opened_never_raises(tmp_path, monkeypatch):
    """A ledger that cannot be written leaves the run alone.

    Defect caught: the task instrumentation refusing a build because
    the profile could not be written - a missing measurement must
    never cost the work.
    """
    monkeypatch.setattr(perf_ledger, "ledger_path",
                        lambda _folder: (_ for _ in ()).throw(OSError("no")))
    assert perf_ledger.begin_task(tmp_path, "reels.build", run_id="r1") is None


def test_a_decode_row_names_the_edit_it_served(tmp_path):
    """A decode carries the edit it was made for.

    Defect caught: the repeated-work report unable to say WHICH edit
    re-measured the source, so "measured again" has no addressee.
    """
    path = tmp_path / "perf.jsonl"
    source = tmp_path / "source.mov"
    source.write_bytes(b"the footage")
    with (perf_ledger.recording(path, "r1", "semantic_analysis"),
          perf_ledger.edit_scope(["Reel 07"]),
          perf_ledger.media_decode(
              source, purpose="semantic.coarse_frames",
              media_kind="video", access_pattern="random_seek",
              calls=1)):
        pass

    rows = perf_ledger.read_rows(tmp_path, path)
    requests = [row for row in rows
                if row.get("family") == "media_decode_requests"]
    assert requests and requests[0]["edit_ids"] == ["Reel 07"]


def test_a_layer_no_stage_claims_is_unclassified():
    """An unknown layer belongs to no stage.

    Defect caught: a layer with no stage silently counted as another
    stage's time, so the stage table reports work a stage never did.
    """
    assert perf_ledger.stage_of("gemma_inference") == "planning"
    assert perf_ledger.stage_of("a_layer_nobody_placed") is None


def test_a_measured_function_is_charged_to_its_layer(tmp_path):
    """A whole unit of work shows up in the profile.

    Defect caught: a stage of an edit whose cost the profile never
    saw, so the stage reads as free and the run's own wall is the
    only number there is.
    """
    path = tmp_path / "perf.jsonl"

    @perf_ledger.measured("footage_search")
    def search():
        return "answered"

    with perf_ledger.recording(path, "r1", "footage.scan"):
        assert search() == "answered"

    rows = perf_ledger.read_rows(tmp_path, path)
    spans = [row for row in rows if row.get("layer") == "footage_search"]
    assert len(spans) == 1
    assert spans[0]["run_id"] == "r1"
    assert spans[0]["wall_s"] >= 0.0


def test_a_capability_row_carries_the_machines_own_reading(tmp_path):
    """The capability row says what the machine was doing.

    Defect caught: a profile with no memory pressure, no swap and no
    disk reading, so a run that stalled on a swapping machine looks
    exactly like one that did not.
    """
    path = tmp_path / "perf.jsonl"
    capability = perf_ledger.begin(tmp_path, "reel.build", "r1",
                                   ledger_path_override=path)
    assert capability is not None
    capability.end()

    rows = perf_ledger.read_rows(tmp_path, path)
    assert rows and "memory_pressure" in rows[0]
def test_edit_lease_totals_count_the_project_switches_an_edit_avoided():
    """The avoidance is an edit's own saving, so it is attributed to the
    edit the way the switches it did pay for are."""
    totals = perf_ledger.edit_lease_totals([
        {"kind": perf_ledger.COUNT, "family": "project_switches_avoided",
         "count": 3, "edit_ids": ["Reel 07"]},
        {"kind": perf_ledger.COUNT, "family": "project_switches_avoided",
         "count": 2, "edit_ids": ["Reel 08"]},
    ])

    assert totals["Reel 07"]["project_switches_avoided"] == 3
    assert totals["Reel 08"]["project_switches_avoided"] == 2
    assert totals["Reel 07"]["project_switches"] == 0


def test_measured_strategy_costs_separate_a_targeted_edit_from_a_rebuild():
    """PERF-017: the choice between a targeted mutation and a bulk
    rebuild rests on what THIS project measured for both."""
    rows = [
        {"layer": "reel_edit", "reel": "Reel 01",
         "measurements": {"total_s": 10.0, "full_rebuilds": 0}},
        {"layer": "reel_edit", "reel": "Reel 02",
         "measurements": {"total_s": 14.0, "full_rebuilds": 0}},
        {"layer": "reel_edit", "reel": "Reel 03",
         "measurements": {"total_s": 50.0, "full_rebuilds": 1}},
    ]

    costs = perf_ledger.measured_strategy_costs(rows)

    assert costs["targeted"] == {"edits": 2, "total_s_p50": 10.0}
    assert costs["full_rebuild"] == {"edits": 1, "total_s_p50": 50.0}


def test_measured_strategy_costs_read_as_unmeasured_where_nothing_ran():
    """A cost nobody measured is None, never a zero and never a number
    from another project."""
    assert perf_ledger.measured_strategy_costs([]) is None
    assert perf_ledger.measured_strategy_costs(
        [{"layer": "reel_edit", "measurements": {}}]) is None
