"""The defects adaptive temporal inspection must not have.

Every test here names a DEFECT: the wrong behaviour it would produce
in the engine if the code regressed.  The measurement that shows the
defect's cost is in `docs/benchmarks/2026-10-21-adaptive-inspection.md`.
"""

import pytest

from library.tools.adaptive_inspection import (
    AdaptiveInspectionError,
    Trigger,
    plan_inspection,
    run_inspection,
    select_dense_samples,
    triggers_from_measurements,
)


def _trigger(kind, start, end=None, **kwargs):
    return Trigger(kind=kind, start=start,
                   end=start if end is None else end,
                   evidence=kwargs.pop("evidence", "test"), **kwargs)


def test_a_static_interval_is_never_re_inspected():
    """The defect: the dense tier re-decodes and re-measures a stretch
    where nothing changed, spending inference on a place the coarse
    tier already measured."""
    plan = plan_inspection(
        100.0, 25.0, [float(t) for t in range(0, 100, 2)],
        [_trigger("motion", 40.0)])

    covered = [(w["start"], w["end"]) for w in plan["dense"]["windows"]]
    assert covered == [(39.5, 40.5)]
    # The rest of the recording is named as left coarse, not silently
    # dropped: a reader can see the engine did not look again there.
    assert plan["skipped_static"]
    assert all(row["reason"].startswith("static:") for row in
               plan["skipped_static"])
    covered_seconds = sum(end - start for start, end in covered)
    assert covered_seconds < 100.0


def test_a_short_event_buys_dense_inspection_not_a_denser_whole_recording():
    """The defect: one short event turns the whole recording into a
    frame-cadence pass, so the cost scales with the RECORDING rather
    than with the event."""
    plan = plan_inspection(
        3600.0, 25.0, [float(t) for t in range(0, 3600, 2)],
        [_trigger("object_state_change", 100.0, 100.4)],
        max_dense_seconds=60.0)

    assert plan["dense"]["covered_seconds"] <= 60.0
    assert plan["dense"]["coverage_fraction_spent"] < 0.05


def test_the_dense_tier_is_a_minority_of_the_inspection():
    """The defect: the dense tier's budget exceeds the coarse tier's
    own sampling, so 'dense' is the new default by accident."""
    plan = plan_inspection(
        600.0, 25.0, [float(t) for t in range(0, 600, 2)],
        [_trigger("dialogue_boundary", float(t)) for t in range(0, 600, 4)],
        dense_coverage_fraction=0.25, max_dense_seconds=60.0)

    assert plan["dense"]["covered_seconds"] <= 60.0
    assert plan["dense"]["covered_seconds"] < plan["coarse"]["sampled_frames"] * 0.5


def test_a_state_change_window_is_tight_around_the_sample_that_saw_it():
    """The defect: the dense window is stretched back over the whole
    coarse gap, so it names a DIFFERENT event than the one the
    measurement pointed at.

    The M0 fixture measures this.  The coarse sample at 33.233 s
    measured the hand 0.694 face-widths from the lips, and the event
    the M0 case asks about (33.3-33.7 s) measures 0.13-0.43.  A
    DIFFERENT, earlier approach at 31.9-32.0 s measures 0.176 - closer
    than the event itself - so a window stretched back over the coarse
    gap names THAT approach, and the M0 case is missed by 8 frames.
    """
    coarse = [0.0, 10.0, 20.0, 30.0]
    plan = plan_inspection(100.0, 30.0, coarse,
                           [_trigger("object_state_change", 30.0, 30.2)],
                           max_window_seconds=12.0)

    window = plan["dense"]["windows"][0]
    assert window["start"] == 29.5
    assert window["end"] == 30.7
    # The bracket is REPORTED, so a reader can see how uncertain the
    # onset is even though the window did not stretch across it.
    assert window["onset_bracket"]["bracket_start"] == 20.0
    assert window["onset_bracket"]["onset_uncertainty_seconds"] == 10.0


def test_a_state_change_does_not_stretch_its_window_over_a_whole_coarse_gap():
    """The defect: the dense window is stretched back over the whole
    coarse gap, so it names a DIFFERENT event than the one the
    measurement pointed at.

    The M0 fixture measures this.  The coarse sample at 33.233 s
    measured the hand 0.694 face-widths from the lips, and the event
    the M0 case asks about (33.3-33.7 s) measures 0.13-0.43.  A
    DIFFERENT, earlier approach at 31.9-32.0 s measures 0.176 - closer
    than the event itself - so a window stretched back over the coarse
    gap names THAT approach, and the M0 case is missed by 8 frames.
    """
    coarse = [0.0, 10.0, 20.0, 30.0]
    plan = plan_inspection(100.0, 30.0, coarse,
                           [_trigger("object_state_change", 30.0, 30.2)])

    window = plan["dense"]["windows"][0]
    assert window["start"] >= 20.0
    # The bracket is REPORTED, so a reader can see how uncertain the
    # onset is even though the window did not stretch across it.
    assert window["onset_bracket"]["bracket_start"] == 20.0
    assert window["onset_bracket"]["onset_uncertainty_seconds"] == 10.0


def test_the_onset_bracket_is_reported_rather_than_silently_dropped():
    """The defect: the report says nothing about how uncertain the
    onset is, so a reader reads a tight window as a precise one."""
    coarse = [0.0, 5.0, 10.0]
    plan = plan_inspection(60.0, 30.0, coarse,
                           [_trigger("object_state_change", 10.0)])

    window = plan["dense"]["windows"][0]
    assert window["onset_bracket"]["bracket_end"] == 10.0
    assert window["onset_bracket"]["onset_uncertainty_seconds"] == 5.0


def test_a_deferred_trigger_is_named_rather_than_dropped():
    """The defect: the budget runs out and the plan silently forgets a
    trigger, so the report claims less coverage than it has."""
    plan = plan_inspection(
        600.0, 25.0, [float(t) for t in range(0, 600, 2)],
        [_trigger("dialogue_boundary", float(t)) for t in range(0, 600, 2)],
        max_dense_seconds=10.0)

    assert plan["deferred"]
    assert all(row["reason"] == "dense coverage budget reached"
               for row in plan["deferred"])
    assert all("trigger" in row for row in plan["deferred"])


def test_a_sample_keeps_its_own_media_timestamp():
    """The defect: a sample is re-timed to land on a window edge, so
    the timestamp the reader sees is not the media's own."""
    samples = [{"t": 12.5, "pts_seconds": 12.533333, "frame_index": 376}]
    plan = plan_inspection(20.0, 30.0, [12.5],
                           [_trigger("motion", 12.5)])

    rows = select_dense_samples(samples, plan)
    assert len(rows) == 1
    assert rows[0]["t"] == 12.5
    assert rows[0]["pts_seconds"] == 12.533333
    assert rows[0]["frame_index"] == 376


def test_an_unknown_trigger_kind_refuses_by_name():
    """The defect: a misspelled trigger kind is accepted and silently
    buys no dense inspection."""
    with pytest.raises(AdaptiveInspectionError):
        Trigger(kind="motions", start=0.0, end=1.0, evidence="x")


def test_a_measurement_with_no_time_refuses_rather_than_being_dropped():
    """The defect: a row the engine cannot place is dropped in
    silence, so a broken measurement reads as 'nothing happened'."""
    with pytest.raises(AdaptiveInspectionError):
        triggers_from_measurements(motion_peaks=[{"note": "no time here"}])


def test_the_dense_tier_never_claims_more_coverage_than_it_measured():
    """The defect: the report's coverage exceeds the recording's own
    length, so a reader believes in frames that do not exist."""
    plan = plan_inspection(
        30.0, 25.0, [0.0, 10.0, 20.0],
        [_trigger("motion", 10.0, 29.0)])

    for window in plan["dense"]["windows"]:
        assert 0.0 <= window["start"] <= window["end"] <= 30.0
    assert plan["dense"]["covered_seconds"] <= 30.0


def test_the_plan_names_the_signals_it_measured():
    """The defect: the plan does not say which measurement earned a
    window, so a reader cannot check the claim."""
    plan = plan_inspection(20.0, 25.0, [10.0],
                           [_trigger("shot_change", 10.0,
                                     evidence="M4 scene_boundaries[3]")])

    window = plan["dense"]["windows"][0]
    assert window["evidence"] == ["M4 scene_boundaries[3]"]


def test_the_dense_tier_reports_the_cost_of_what_it_ran(monkeypatch):
    """The defect: the dense tier runs and its cost is not reported,
    so 'inference cost' is unmeasured."""
    calls = []

    def fake_sample(source_file, timebase, start, end, out_dir, **kwargs):
        calls.append((start, end))
        return {"frames": [{"pts_seconds": start, "frame_index": 1,
                            "on_grid": True}],
                "cost": {"decode_seconds": 1.0}, "coverage": {}}

    monkeypatch.setattr("library.tools.frame_exact.sample_interval",
                        fake_sample)
    monkeypatch.setattr("library.tools.frame_exact.measure_frames",
                        lambda paths: [{"faces": [], "hands": []}])

    class _Timebase:
        pass

    plan = plan_inspection(20.0, 25.0, [10.0], [_trigger("motion", 10.0)])
    report = run_inspection(plan, "/nonexistent.mov", _Timebase(),
                            out_dir="/tmp/ren-vi005-test",
                            measure=False)

    assert calls
    assert report["cost"]["frames_decoded"] == len(calls)
    assert report["cost"]["wall_seconds"] >= 0.0
    assert "peak_rss_mb" in report["cost"]
    assert "no frame is re-timed" in report["timestamp_basis"]


def test_the_dense_tier_adds_frames_without_removing_the_coarse_grid():
    """The defect: the dense tier REPLACES the coarse grid instead of
    adding to it, so a moment nothing fired on is no longer findable.

    The punchlist asks for the coarse sampling to be RETAINED and the
    density increased around the candidate events.  A replacement would
    make the search blind to everything the coarse tier covered.
    """
    from library.tools.analysis import footage_frames

    coarse = [{"t": float(t), "frame_index": t} for t in range(0, 120, 2)]
    # The trigger sits between two ten-second grid marks, so the dense
    # window holds a coarse sample the grid does not.
    trigger = _trigger("object_state_change", 64.0, 64.2)
    selected, plan = footage_frames._adaptive_m2_frames(
        coarse, footage_frames.FRAME_STEP_S, [trigger], 120.0, 25.0)

    times = [round(float(f["t"]), 3) for f in selected]
    grid = [round(float(f["t"]), 3)
            for f in footage_frames._select_m2_frames(
                coarse, footage_frames.FRAME_STEP_S)]
    assert set(grid) <= set(times), "the coarse grid was not retained"
    assert len(times) > len(grid), "no frame was added"


def test_the_dense_frames_keep_their_own_frame_index():
    """The defect: a dense frame is re-timed or renumbered, so the
    frame index a reader sees is not the source's own."""
    from library.tools.analysis import footage_frames

    coarse = [{"t": float(t), "frame_index": t * 3} for t in range(0, 120, 2)]
    trigger = _trigger("object_state_change", 60.0, 60.2)
    selected, _plan = footage_frames._adaptive_m2_frames(
        coarse, footage_frames.FRAME_STEP_S, [trigger], 120.0, 25.0)

    assert selected
    for frame in selected:
        assert frame["frame_index"] == frame["t"] * 3
