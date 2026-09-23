"""The approval gate on the caption render path.

`reel_proposal.assert_approved` had zero call sites in any render path,
so captions were rendered for reels whose rejection was already on
disk (measured on geo-podcast: full caption sets for quality-bar
rejected reels 2, 3, 4, 6, 8, 11, 14, 19 and 22). Step 4.05's
`render_one_segment` refuses a timeline label naming a REJECTED reel
BEFORE reuse, probe or render, reading the verdict LIVE off
`reel_proposals_v2.json` on every call - the captain rules on reels
while renders are in flight.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import render_one_segment
from library.tools.reel_proposal import (
    NotApproved,
    refuse_rejected_reel_timeline,
)


def _moment(number, approval, slug="some-slug", note=""):
    return {
        "number": number,
        "slug": slug,
        "reason": "why",
        "timeline_start": 10.0,
        "timeline_end": 60.0,
        "approval": approval,
        "approval_note": note,
    }


def _project_with_proposals(tmp_path, moments):
    project = tmp_path / "proj"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(json.dumps({
        "format": "reel_proposal/1",
        "moments": moments,
    }), encoding="utf-8")
    return str(project)


def _props():
    return {
        "_block_position": 1,
        "_timeline_start": 0.0,
        "_timeline_end": 2.0,
        "_source_in_frame": 0,
        "_source_out_frame": 60,
        "_speaker": None,
        "_source_clip_id": "clip_001",
        "_source_start": 10.0,
        "_source_end": 12.0,
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {"fontFamily": "Montserrat"},
        "subtitles": [],
    }


class _StubRenderer:
    def __init__(self):
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append(overlay_path)
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""

    def close(self):
        pass


# ── The helper reads the verdict, live ───────────────────────────────

def test_rejected_label_raises(tmp_path):
    project = _project_with_proposals(tmp_path, [
        _moment(22, "rejected", slug="a-score-is-not-a-fix",
                note="nothing quotable"),
    ])
    with pytest.raises(NotApproved, match="REJECTED"):
        refuse_rejected_reel_timeline(
            "Reel 22 - a-score-is-not-a-fix (rebuild staging)", project)


@pytest.mark.parametrize("case", [
    "approved_and_proposed_labels",
    "master_and_unnamed_labels",
    "missing_proposals_file",
    "reel_number_not_proposed",
])
def test_non_rejected_labels_proceed_without_reading(tmp_path, case):
    """B1 collapse: the four proceed-without-reading passes pin one
    property, so one parametrized test."""
    if case == "approved_and_proposed_labels":
        project = _project_with_proposals(tmp_path, [
            _moment(9, "approved"),
            _moment(2, "proposed"),
        ])
        refuse_rejected_reel_timeline("Reel 09 - whatever", project)
        refuse_rejected_reel_timeline("Reel 02 - whatever", project)
    elif case == "master_and_unnamed_labels":
        project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
        refuse_rejected_reel_timeline("GEO Podcast - Synced", project)
        refuse_rejected_reel_timeline("", project)
        refuse_rejected_reel_timeline(None, project)
    elif case == "missing_proposals_file":
        project = str(tmp_path / "proj")
        os.makedirs(project, exist_ok=True)
        refuse_rejected_reel_timeline("Reel 22 - whatever", project)
    else:
        project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
        refuse_rejected_reel_timeline("Reel 40 - never-proposed", project)


def test_verdict_is_read_live_not_cached(tmp_path):
    """The captain rules while renders fly: approve then reject the
    same reel and the gate follows the file, both directions."""
    project = _project_with_proposals(tmp_path, [_moment(22, "approved")])
    refuse_rejected_reel_timeline("Reel 22 - x", project)
    path = os.path.join(project, "pipeline_output", "review",
                        "reel_proposals_v2.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"format": "reel_proposal/1",
                   "moments": [_moment(22, "rejected")]}, handle)
    with pytest.raises(NotApproved):
        refuse_rejected_reel_timeline("Reel 22 - x", project)


# ── The render path refuses before drawing ───────────────────────────

def test_render_one_segment_refuses_a_rejected_reel_before_rendering(
        tmp_path):
    project = _project_with_proposals(tmp_path, [
        _moment(22, "rejected", note="nothing quotable"),
    ])
    stub = _StubRenderer()
    with pytest.raises(NotApproved, match="REJECTED"):
        render_one_segment(
            _props(), str(tmp_path / "out"),
            "Reel 22 - a-score-is-not-a-fix (rebuild staging)",
            renderer=stub, project_folder=project)
    assert stub.calls == []


def test_render_one_segment_renders_an_approved_reel(tmp_path):
    project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
    stub = _StubRenderer()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = render_one_segment(
        _props(), str(out_dir),
        "Reel 09 - your-website-is-only-20-percent (rebuild staging)",
        renderer=stub, project_folder=project,
        overlay_geometry="full")
    assert out["provenance"] == "rendered"
    assert stub.calls != []
