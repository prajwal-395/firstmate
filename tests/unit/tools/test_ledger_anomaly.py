"""A duration that cannot be true is an anomaly, never a number.

Every test here names the DEFECT it would catch.

The defect: the performance ledger recorded `otio_placement.fusion_comps_s`
as 131,422-167,228 s per placement - the MACHINE'S UPTIME - for a
phase that took under a minute, and both cost readers charged it to
Resolve (`perf_ledger.STAGE_LAYERS` -> "axi", `critical_path.BUCKET_BY_LAYER`
-> RESOLVE).  Every "Resolve or the native renderer?" comparison then
read 36 hours of phantom Resolve time, and the run's own window was
stretched by the same 46 hours.

The rule these tests hold: a phase is checked against the wall of the
run that recorded it.  A phase that fails the check keeps the number it
claimed and is charged to NOTHING - no stage, no engine, no resource.
"""

import json

import pytest

from library.tools import critical_path, ledger_anomaly, perf_ledger
from library.tools import run_id as run_identity


def _capability(wall, started=1000.0, run_id="r1"):
    return {"kind": "capability", "run_id": run_id, "capability": "reel.build",
            "started_at": started, "wall_s": wall}


def _span(layer, wall, started=1000.0, run_id="r1"):
    return {"kind": "span", "run_id": run_id, "capability": "reel.build",
            "layer": layer, "started_at": started, "wall_s": wall}


#: The shape the shipped geo-podcast ledger carries: 17 rows of
#: 131,422-167,228 s, all of them the machine's uptime.
PHANTOM = 167228.132


def test_a_phase_longer_than_the_run_is_charged_to_nothing():
    """Defect: the profile charged 167,228 s of phantom Resolve time to
    the phase, so the run's biggest cost was a number that could not be
    true - the machine's uptime, for a phase that took under a minute."""
    rows = [
        _capability(582.786),
        _span("otio_placement.fusion_comps_s", PHANTOM),
        _span("reel_build_place", 16.545),
    ]

    report = perf_ledger.profile(rows, "r1")

    charged = {line["name"]: line["wall_s"] for line in report["lines"]}
    assert "otio_placement.fusion_comps_s" not in charged
    assert charged["reel_build_place"] == 16.545
    assert report["anomalies"]["count"] == 1
    assert report["anomalies"]["claimed_wall_s"] == PHANTOM
    assert report["anomalies"]["layers"] == ["otio_placement.fusion_comps_s"]


def test_the_critical_path_does_not_charge_a_phantom_resolve_phase():
    """Defect: the critical path's own window was stretched by the same
    46 hours, so every stage's share of the run was a share of a lie."""
    rows = [
        _capability(582.786),
        _span("otio_placement.fusion_comps_s", PHANTOM, started=1000.0),
        _span("reel_build_place", 16.545),
    ]

    report = critical_path.analyse_run(rows, "r1")

    layers = {row["layer"]: row["seconds"]
              for stage in report["stages"] for row in stage["layers"]}
    assert "otio_placement.fusion_comps_s" not in layers
    assert report["instrumented_wall_s"] == 16.545
    assert report["anomalies"]["count"] == 1
    # The run's window is the run's window, not the phantom's reach:
    # the capability's own wall ends it, 582.8 s later.
    assert critical_path._run_window(rows, "r1") == (1000.0, 1582.786)


def test_a_measured_phase_within_the_run_is_still_charged():
    """The clamp must not cost the engine a real measurement: a phase
    that fits inside the run that recorded it is charged as always."""
    rows = [
        _capability(582.786),
        _span("otio_placement.fusion_comps_s", 21.4),
    ]

    report = perf_ledger.profile(rows, "r1")

    charged = {line["name"]: line["wall_s"] for line in report["lines"]}
    assert charged["otio_placement.fusion_comps_s"] == 21.4
    assert report["anomalies"]["count"] == 0


def test_reading_the_ledger_clamps_the_stored_phantom(tmp_path):
    """Defect: the stored ledger carried the bad numbers, so every
    reader of the file - `ren profile`, the critical path - believed
    them.  The file stays append-only; what a reader GETS is clamped."""
    path = tmp_path / "perf_ledger.jsonl"
    path.write_text(
        json.dumps(_capability(582.786)) + "\n"
        + json.dumps(_span("otio_placement.fusion_comps_s", PHANTOM)) + "\n",
        encoding="utf-8")

    rows = perf_ledger.read_rows(tmp_path, path)

    assert len(rows) == 2
    assert rows[1]["kind"] == ledger_anomaly.ANOMALY
    assert rows[1]["anomaly"] == ledger_anomaly.PHASE_EXCEEDS_RUN_WALL
    assert rows[1]["claimed_wall_s"] == PHANTOM
    assert rows[1]["run_wall_s"] == 582.786
    # An anomaly carries no duration and no start: no reader can charge
    # it to a stage by accident.
    assert "wall_s" not in rows[1]
    assert "started_at" not in rows[1]


def test_recording_a_phase_longer_than_the_run_writes_an_anomaly(
        tmp_path, monkeypatch):
    """Defect: the write path stored the machine's uptime as a duration,
    so the wrong number was in the file before any reader could check
    it.  A phase cannot claim more wall time than the run has been
    alive."""
    path = tmp_path / "perf_ledger.jsonl"
    run = run_identity.mint_run_id()
    monkeypatch.setenv(perf_ledger.LEDGER_ENV, str(path))
    monkeypatch.setenv(perf_ledger.RUN_ENV, run)
    monkeypatch.setenv(perf_ledger.CAPABILITY_ENV, "reel.build")

    perf_ledger.record("otio_placement.fusion_comps_s", 167228.132)

    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["kind"] == ledger_anomaly.ANOMALY
    assert rows[0]["claimed_wall_s"] == 167228.132
    assert "wall_s" not in rows[0]


def test_a_measured_phase_within_the_run_is_written_as_a_duration(
        tmp_path, monkeypatch):
    """The clamp must not cost the engine a real measurement."""
    import time

    path = tmp_path / "perf_ledger.jsonl"
    # Minted in the past, on purpose: the id carries its start truncated
    # to the second, so a run minted "now" reads as up to a second old
    # and a 0.25 s phase would race the fractional second. The test is
    # about a phase WITHIN its run, so the run is unambiguously older.
    run = run_identity.mint_run_id(now=time.time() - 30.0)
    monkeypatch.setenv(perf_ledger.LEDGER_ENV, str(path))
    monkeypatch.setenv(perf_ledger.RUN_ENV, run)
    monkeypatch.setenv(perf_ledger.CAPABILITY_ENV, "reel.build")

    perf_ledger.record("otio_placement.fusion_comps_s", 0.25)

    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["kind"] == perf_ledger.SPAN
    assert rows[0]["wall_s"] == 0.25


def test_a_phase_start_cannot_be_a_duration():
    """Defect: the producer subtracted an elapsed AVERAGE from the
    monotonic clock, and macOS's `time.perf_counter()` IS the machine's
    uptime - so the phase reported 134,590 s on a 37-hour-uptime Mac.
    A start and a duration are now different types."""
    from library.tools.reel_otio_placement import record_duration

    with pytest.raises(TypeError) as caught:
        record_duration({"timeline_name": "Reel 26"}, "fusion_comps_s", 0.042)

    assert "start" in str(caught.value)


def test_a_run_with_no_recorded_wall_bounds_nothing():
    """Unknown is never zero: a run that recorded no capability wall
    cannot say a phase is impossible, so no phase is called impossible.
    """
    rows = [_span("otio_placement.fusion_comps_s", PHANTOM)]

    clamped = ledger_anomaly.clamp_rows(rows)

    assert clamped[0] is rows[0]
    assert clamped[0]["kind"] == perf_ledger.SPAN


def test_the_run_id_carries_the_run_start():
    """The write path's bound needs the run's age, and the run's id
    carries its start: every process that knows the id knows how long
    the run has been alive, without reading a file."""
    run = "run-20261018T074200Z-020e0b"

    started = run_identity.started_at(run)

    assert started == 1792309320.0
    assert run_identity.started_at("20261018T074200-12345") is None
    assert run_identity.started_at(None) is None
