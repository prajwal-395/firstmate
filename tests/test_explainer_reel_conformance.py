"""F21: the animated explainer, graded against the plan the build wrote.

Both directions.  A check that only catches an absence reads as coverage
while an out-of-band append walks past it (AGENTS.md 10.4), and the
master already refuses that one by name
(`bookends.assert_no_invented_bookends`).

The other half - what an explainer refuses before it is ever built - is
`tests/test_explainer_plan.py`.
"""

from __future__ import annotations

from library.tools import explainer_plan as ex
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelTimeline,
    TimelineItem,
    _snapshot_to_reel_timeline,
    check_explainer,
)

FPS = 24000 / 1001


def _item(start, frames, track=ex.EXPLAINER_TRACK, name="explainer_r07_00.mov"):
    return TimelineItem(
        track_type="video", track_index=track,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file="/x.mov", speaker=None, name=name,
        unique_id=f"id-{start}")


def _plan(*segments, basis=ex.PLANNED):
    return {"reel": "Reel 07", "basis": basis, "declared": True,
            "segments": [{"timeline_start": s, "total_frames": f,
                          "timeline_end": s + f / FPS,
                          "overlay_path": "/x.mov",
                          "elements": ["list_build"]}
                         for s, f in segments]}


# ── The record survives partial builds and rebuilds ──────────────────

def _stored_plans(project, plans):
    import json

    from library.tools.project_layout import Area, ProjectLayout
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / ex.PLAN_FILENAME).write_text(
        json.dumps({"format": "explainer_plans/1", "plans": plans}),
        encoding="utf-8")
    return review


def _read_plans(project):
    import json

    from library.tools.project_layout import Area, ProjectLayout
    path = (project / "pipeline_output" / "review" / ex.PLAN_FILENAME)
    return json.loads(path.read_text(encoding="utf-8"))["plans"]


def _explainer(reel, basis=ex.PLANNED):
    return ex.ExplainerPlan(reel_name=reel, declared=True, basis=basis)


def test_a_partial_build_keeps_the_reels_it_did_not_touch(tmp_path):
    """`write_plans` overwrote the whole file with only this build's
    reels, so a single-reel rebuild deleted every other reel's
    explainer baseline and F21 graded those timelines against an
    absence (which returns nothing - silent coverage loss). The write
    merges: reels this build touched are replaced, the rest stand."""
    project = tmp_path / "proj"
    _stored_plans(project, [{"reel": "Reel 07", "declared": True,
                             "basis": ex.PLANNED, "entries": [],
                             "anchored": None, "band": None,
                             "segments": []}])
    ex.write_plans(str(project), [_explainer("Reel 09")])
    by_reel = {p["reel"]: p for p in _read_plans(project)}
    assert set(by_reel) == {"Reel 07", "Reel 09"}
    assert by_reel["Reel 09"]["basis"] == ex.PLANNED


def test_promotion_replaces_the_previous_final_plan(tmp_path):
    """The staging/rename half of the same defect the semantic-visual
    records had: renaming beside the previous final leaves two plans
    for one reel and `plan_for_reel` reads the stale first."""
    project = tmp_path / "proj"
    final = "Reel 09"
    staging = final + " (rebuild staging)"
    _stored_plans(project, [
        {"reel": final, "declared": True, "basis": ex.NOT_DECLARED,
         "entries": [], "anchored": None, "band": None, "segments": []},
        {"reel": staging, "declared": True, "basis": ex.PLANNED,
         "entries": [], "anchored": None, "band": None, "segments": []}])
    ex.rename_plan_reels(str(project), {staging: final})
    kept = [p for p in _read_plans(project) if p["reel"] == final]
    assert len(kept) == 1
    assert kept[0]["basis"] == ex.PLANNED


# ── It passes what is right ──────────────────────────────────────────

def test_a_placed_explainer_matching_its_plan_passes():
    start = int(round(40.707 * FPS))
    findings = check_explainer(
        "Reel 07", [_item(start, 197)], _plan((40.707, 197)), FPS)
    assert findings == []


def test_a_reel_that_declared_nothing_and_carries_nothing_passes():
    findings = check_explainer(
        "Reel 07", [], _plan(basis=ex.NOT_DECLARED), FPS)
    assert findings == []


def test_a_build_that_recorded_nothing_is_not_graded():
    """A project built before explainers existed has no record, and
    grading it against an absence would fail every correct reel."""
    assert check_explainer("Reel 07", [], None, FPS) == []


# ── It fails what is wrong, in both directions ───────────────────────

def test_a_planned_explainer_the_timeline_does_not_carry_fails():
    findings = check_explainer("Reel 07", [], _plan((40.707, 197)), FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F21]
    assert "no item" in findings[0].message


def test_an_item_no_plan_accounts_for_fails():
    """The out-of-band append. `bookends.assert_no_invented_bookends`
    refuses this on the master; here it is F21."""
    findings = check_explainer(
        "Reel 07", [_item(100, 50)], _plan(basis=ex.NO_PARTS), FPS)
    assert findings and findings[0].finding_class == FindingClass.F21
    assert "no explainer" in findings[0].message


def test_an_item_on_a_reel_with_no_recorded_plan_at_all_fails():
    findings = check_explainer("Reel 07", [_item(100, 50)], None, FPS)
    assert findings and "no recorded explainer plan" in findings[0].message


def test_an_explainer_at_the_wrong_reel_second_fails():
    start = int(round(40.707 * FPS))
    findings = check_explainer(
        "Reel 07", [_item(start + 30, 197)], _plan((40.707, 197)), FPS)
    codes = [f.finding_class for f in findings]
    assert codes.count(FindingClass.F21) == 2  # missing there, extra here
    assert any("nearest is frame" in f.message for f in findings)


def test_an_explainer_of_the_wrong_length_fails():
    start = int(round(40.707 * FPS))
    findings = check_explainer(
        "Reel 07", [_item(start, 196)], _plan((40.707, 197)), FPS)
    assert findings and "196 frames, planned 197" in findings[0].message


def test_two_stacked_explainer_items_fail():
    """One build having run twice into one timeline."""
    start = int(round(40.707 * FPS))
    findings = check_explainer(
        "Reel 07",
        [_item(start, 197), _item(start + 10, 197)],
        _plan((40.707, 197)), FPS)
    assert any("overlap" in f.message for f in findings)


def test_every_finding_is_an_error_rather_than_a_warning():
    """A missing, extra or mis-placed explainer is not an opinion."""
    findings = check_explainer("Reel 07", [], _plan((40.707, 197)), FPS)
    assert all(f.severity == "error" for f in findings)


# ── The track the verifier had been dropping ─────────────────────────

class _Clip:
    def __init__(self, track_type, track_index, start, end, name):
        self.track_type = track_type
        self.track_index = track_index
        self.timeline_start = start
        self.timeline_end = end
        self.duration = end - start
        self.source_in_frame = 0
        self.source_out_frame = 10
        self.source_file = "/x.mov"
        self.speaker = None
        self.name = name
        self.resolve_item_id = name
        self.transform = {}


class _Snapshot:
    timeline_name = "Reel 07"
    fps = FPS
    start_frame = 0
    end_frame = 1000
    width = 1080
    height = 1920

    def __init__(self, clips):
        self.clips = clips


def test_the_explainer_track_is_no_longer_dropped_on_the_floor():
    """`_snapshot_to_reel_timeline` classified by `track <= 2`,
    `== 3`, `audio`, and an implicit else that dropped the item. A clip
    on the explainer track was not read as a duplicate placement and
    not read as a picture hole - it was not read at all, which is the
    gate-that-cannot-fail shape wearing a different hat."""
    snapshot = _Snapshot([
        _Clip("video", 1, 0.0, 4.0, "LC4932.MXF"),
        _Clip("video", 3, 0.5, 2.0, "sub_x.mov"),
        _Clip("video", ex.EXPLAINER_TRACK, 1.0, 3.0, "explainer_r07_00.mov"),
        _Clip("audio", 1, 0.0, 4.0, "LC4932.MXF"),
    ])
    reel = _snapshot_to_reel_timeline(snapshot)
    assert len(reel.explainer_items) == 1
    assert reel.explainer_items[0].name == "explainer_r07_00.mov"
    # And it is NOT counted as picture, which would make F4 report an
    # extra item on a correct build.
    assert all(i.track_index <= 2 for i in reel.video_items)
    assert len(reel.video_items) == 1


def test_a_reel_timeline_defaults_to_no_explainer_items():
    """Every existing construction of ReelTimeline keeps working."""
    reel = ReelTimeline(reel_name="Reel 07", fps=FPS, total_frames=100,
                        video_items=(), audio_items=(), caption_items=())
    assert reel.explainer_items == ()


def test_verify_reel_runs_f21_without_being_asked_twice():
    """F21 is wired into `verify_reel`, not only callable on its own -
    a check nothing calls is no coverage at all."""
    import inspect

    from library.tools import reel_conformance_verifier as verifier
    source = inspect.getsource(verifier.verify_reel)
    assert "check_explainer(" in source
    assert "explainer_plan" in inspect.signature(
        verifier.verify_reel).parameters
