"""F22: semantic visuals, graded against the record the build wrote.

Both directions, mirroring F21 beside it. A check that only catches an
absence reads as coverage while an out-of-band append walks past it
(AGENTS.md 10.4).

The planning half - the request the model answers, and what an
unanswered request builds - is tested below against
`library/tools/reel_semantic_visual.py` without Resolve and without a
model: a missing answer file must build nothing and say
`awaiting_model_answer`, never block and never invent.
"""

from __future__ import annotations

import json

from library.tools import reel_semantic_visual as sem
from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_semantic_visuals,
)

FPS = 24000 / 1001


def _item(start, frames, track=sem.SEMANTIC_TRACK, name="vox_reel_09_00.mov"):
    return TimelineItem(
        track_type="video", track_index=track,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file="/x.mov", speaker=None, name=name,
        unique_id=f"id-{start}")


def _record(*segments, basis=sem.PLANNED):
    return {"reel": "Reel 09", "basis": basis,
            "entries": [{"element": "subject_emblem"}],
            "dropped": [],
            "segments": [{"timeline_start": s, "total_frames": f,
                          "timeline_end": s + f / FPS,
                          "elements": ["subject_emblem"]}
                         for s, f in segments]}


# ── F22 passes what is right ─────────────────────────────────────────

def test_a_placed_visual_matching_its_record_passes():
    start = int(round(4.901 * FPS))
    findings = check_semantic_visuals(
        "Reel 09", [_item(start, 75)], _record((4.901, 75)), FPS)
    assert findings == []


# ── F22 fails what is wrong, in both directions ──────────────────────

def test_a_planned_visual_the_timeline_does_not_carry_fails():
    findings = check_semantic_visuals("Reel 09", [], _record((4.901, 75)),
                                      FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F22]
    assert "no item" in findings[0].message


def test_an_item_no_record_accounts_for_fails():
    """The out-of-band append."""
    findings = check_semantic_visuals(
        "Reel 09", [_item(100, 50)],
        _record(basis=sem.AWAITING_MODEL_ANSWER), FPS)
    assert findings and findings[0].finding_class == FindingClass.F22


def test_promotion_replaces_the_previous_final_record(tmp_path):
    """A rebuild records under the staging name and promotion renames
    it to the final one - but the previous build's record is already
    there under the final name. Renaming beside it leaves TWO records
    for one reel (live catch on Reel 09: a stale `awaiting_model_answer`
    beside the new `planned`), and `record_for_reel` reads the first,
    so the next verifier grades the promoted timeline against the
    absence. Promotion replaces; it does not shelve beside."""
    from library.tools.project_layout import Area, ProjectLayout

    project = tmp_path / "proj"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    final = "Reel 09 - your-website-is-only-20-percent"
    staging = final + " (rebuild staging)"
    (review / sem.PLAN_FILENAME).write_text(
        json.dumps({"format": "semantic_visual_plans/1", "plans": [
            {"reel": final, "basis": sem.AWAITING_MODEL_ANSWER,
             "entries": [], "dropped": [], "segments": []},
            {"reel": staging, "basis": sem.PLANNED,
             "entries": [{"element": "subject_emblem"}], "dropped": [],
             "segments": []}]}),
        encoding="utf-8")
    sem.rename_record_reels(str(project), {staging: final})
    stored = json.loads(
        (review / sem.PLAN_FILENAME).read_text(encoding="utf-8"))
    kept = [p for p in stored["plans"] if p["reel"] == final]
    assert len(kept) == 1
    assert kept[0]["basis"] == sem.PLANNED


def test_a_visual_of_the_wrong_length_fails():
    start = int(round(4.901 * FPS))
    findings = check_semantic_visuals(
        "Reel 09", [_item(start, 74)], _record((4.901, 75)), FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F22]
    assert "74 frames" in findings[0].message


def test_two_overlapping_visuals_fail():
    start = int(round(4.901 * FPS))
    findings = check_semantic_visuals(
        "Reel 09", [_item(start, 75), _item(start + 10, 75)],
        _record((4.901, 75), (5.5, 75)), FPS)
    assert FindingClass.F22 in [f.finding_class for f in findings]
    assert any("overlap" in f.message for f in findings)


# ── The track is named once ──────────────────────────────────────────


# ── An unanswered ask builds nothing and says so ─────────────────────

class _Moment:
    number = 9
    timeline_name = "Reel 09 - your-website-is-only-20-percent"


def _ranges():
    return [(631.12, 693.3)]


def test_no_answer_file_builds_nothing_and_names_its_basis(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    segments, record = sem.build_for_reel(
        _Moment(), {"structure": []}, _ranges(), str(project),
        fps=FPS, width=1080, height=1920)
    assert segments == []
    assert record["basis"] == sem.AWAITING_MODEL_ANSWER
    assert record["reel"] == _Moment.timeline_name


# ── The ask is the pipeline's own planning surface ───────────────────

def test_the_request_carries_the_reel_spine_and_the_roster():
    spine = {"structure": [{
        "position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 4.0,
        "content": {"text": "we spent a lot of money on the website"},
    }]}
    context = sem.bridge_context(spine, "", FPS)
    assert "money" in context["timeline_context_toon"]
    assert "subject_emblem" in context["motion_elements_toon"]
    assert "anchor_phrase" in sem.handoff_text()


# ── The project reaches the renderer ─────────────────────────────────
#
# `build_for_reel` drove `motion_graphics.render_segment` without the
# project, so `render_one_segment` resolved the geometry against
# nothing and every reel graphic rendered full canvas even on a
# project declaring `motion_graphics_overlay_geometry: tight` - while
# the master pass beside it rendered tight. The project is forwarded
# so the declaration is read live, per render.


class _CapturedRender:
    """The render operation, recording what the build handed it."""

    def __init__(self):
        self.calls = []

    def run(self, planned, out_dir, **kwargs):
        self.calls.append((planned, out_dir, kwargs))
        return {
            "overlay_path": f"{kwargs.get('segment_name')}.mov",
            "timeline_start": 0.0,
            "timeline_end": 1.0,
            "total_frames": 24,
            "elements": ["title_lockup"],
        }


class _Resolved:
    def __init__(self):
        self.moments = [{"element": "title_lockup"}]
        self.proposed = 1
        self.dropped = []


def test_build_for_reel_forwards_the_project_to_the_render(
        tmp_path, monkeypatch):
    import library.tools.motion_graphics_plan as mg
    import library.tools.operations as operations
    import library.tools.reel_spine as reel_spine

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses"
     / "reel_semantic_09.json").write_text(
        json.dumps({"motion_graphics_plan": [{"element": "title_lockup"}]}),
        encoding="utf-8")
    captured = _CapturedRender()
    monkeypatch.setattr(
        reel_spine, "spine_for_reel", lambda *a, **k: {"structure": []})
    monkeypatch.setattr(
        mg, "resolve_plan", lambda *a, **k: _Resolved())
    monkeypatch.setattr(
        mg, "plan_segments",
        lambda *a, **k: [{"index": 0, "props": {}}])
    monkeypatch.setattr(
        operations, "get", lambda name: captured)

    segments, _record = sem.build_for_reel(
        _Moment(), {"structure": []}, _ranges(), str(project),
        fps=FPS, width=1080, height=1920)

    assert len(segments) == 1
    assert len(captured.calls) == 1
    _planned, _out_dir, kwargs = captured.calls[0]
    assert kwargs.get("project_folder") == str(project)
