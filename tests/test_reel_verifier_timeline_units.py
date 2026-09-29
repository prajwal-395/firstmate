"""The pipeline verifier restores Pan/Tilt units without moving Resolve.

Resolve scales Pan and Tilt returned through a non-current timeline handle
by the current timeline's dimensions over the read timeline's dimensions.
This models the exact Reel 24 TV-window geometry and exercises the real
`reel.verify` operation against a read-only Resolve stand-in.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from library.tools import operations, reel_look
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import Approval, ReelMoment, write_proposal
from library.tools.reel_quality_bar import BarReport
from library.tools.timeline_ingest import TimelineClip, TimelineSnapshot
from library.tools.timeline_transcript import transcript_path

MASTER = "GEO Podcast - Synced"
FINAL = "Reel 24 - why-ai-trusts-youtube"
STAGING = FINAL + " (rebuild staging)"
SOURCE = "/media/LC4932.MXF"
WINDOW = (56.106, 530.6365, 1022.967, 1829.827)
LOOK = {
    "asset": "TV 4k.png", "punch_in": 2.3,
    # The live project scales the portrait frame to 0.9298. This gives
    # the same effective 2.138585 punch-in recorded by Reel 24's build.
    "scale": 0.9298, "power": {},
}
FPS = 24000 / 1001


class _Timeline:
    def __init__(self, name, width, height):
        self.name = name
        self.width = width
        self.height = height

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return {
            "timelineResolutionWidth": str(self.width),
            "timelineResolutionHeight": str(self.height),
        }.get(key, "")


class _Project:
    def __init__(self, timelines, current):
        self.timelines = timelines
        self.current = current

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetCurrentTimeline(self):
        return self.current


def _snapshot(timeline, transform=None):
    clips = ()
    if timeline.GetName() == STAGING:
        clips = (TimelineClip(
            resolve_item_id="uid-lc4932",
            track_type="video", track_index=1, track_name="Akshita",
            speaker="Akshita", source_file=SOURCE,
            source_in=0.0, source_out=10.0,
            source_in_frame=0, source_out_frame=240,
            source_frames=1000,
            timeline_start=0.0, timeline_end=10.0,
            name="LC4932.MXF", transform=dict(transform or {})),)
    width, height = timeline.width, timeline.height
    return TimelineSnapshot(
        project_name="Mock Project", timeline_name=timeline.GetName(),
        fps=FPS, reported_fps=23.976,
        width=width, height=height,
        start_frame=0, end_frame=240, clips=clips)


def _focused_f12_result(plan, timeline, **kwargs):
    """Run the production F12 check while leaving unrelated gates neutral."""
    findings = verifier.check_delivered_framing(
        plan.reel_name, timeline.video_items, timeline.width,
        timeline.height, source_sizes=kwargs["source_sizes"],
        declared_intent=kwargs["declared_intent"],
        declared_crop_factor=kwargs["declared_crop_factor"],
        cards=plan.cards, look=kwargs["look"],
        draw_gain=kwargs["draw_gain"])
    return verifier.ReelResult(
        reel_name=plan.reel_name, reel_number=24,
        plan_seconds=10.0, plan_frames=round(10.0 * FPS),
        actual_frames=240, items_expected=1, items_actual=1,
        one_frame_holes=0, big_holes=[], captions_expected=0,
        captions_actual=0, speech_seconds=0.0,
        uncaptioned_seconds=0.0, uncaptioned_pct=0.0,
        short_captions=0, edge_cuts=0, bad_take_cuts=0,
        markers=0, findings=findings)


def test_reel_verify_operation_corrects_noncurrent_transform_read(
        tmp_path, capsys):
    """A quarter-size current timeline used to turn a valid aim into F12."""
    properties = reel_look.punch_in_properties(
        LOOK, SimpleNamespace(center_x=0.5023, center_y=0.3132),
        3840, 2160, 1080, 1920, window=WINDOW, draw_gain=1.0)
    assert properties["ZoomX"] == pytest.approx(2.138585, abs=0.000001)
    assert properties["Tilt"] == pytest.approx(-696.041, abs=0.001)

    # Resolve reads the target timeline's transforms in current-timeline
    # units. With a 270x480 current timeline, both axes come back at 1/4.
    raw_transform = dict(properties)
    raw_transform["Pan"] *= 0.25
    raw_transform["Tilt"] *= 0.25
    raw_snapshot = _snapshot(_Timeline(STAGING, 1080, 1920), raw_transform)
    raw_timeline = verifier._snapshot_to_reel_timeline(raw_snapshot)
    with patch.object(reel_look, "screen_window_rect_for",
                      return_value=WINDOW):
        raw_findings = verifier.check_delivered_framing(
            STAGING, raw_timeline.video_items, 1080, 1920,
            source_sizes={SOURCE: {"width": 3840, "height": 2160}},
            declared_intent=0.0, declared_crop_factor=1.0,
            look=LOOK, draw_gain=1.0)
    assert len(raw_findings) == 1
    assert "bottom 165.8px" in raw_findings[0].message

    current = _Timeline("Probe 270x480", 270, 480)
    master = _Timeline(MASTER, 3840, 2160)
    staging = _Timeline(STAGING, 1080, 1920)
    project = _Project([master, staging, current], current)
    resolve = MagicMock()
    resolve.GetProjectManager.return_value = MagicMock()

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    plan_path = review / "reel_proposals_v2.json"
    write_proposal(
        plan_path,
        [ReelMoment(
            number=24, slug="why-ai-trusts-youtube",
            reason="Exercise the built Reel 24 framing.",
            timeline_start=0.0, timeline_end=10.0,
            approval=Approval.APPROVED)],
        {"derived_from": {"duration_seconds": 10.0}})
    (tmp_path / "pipeline_output").mkdir(exist_ok=True)
    record = {
        "resolve_project_name": "Mock Project",
        "master_timeline_name": MASTER,
        "plan_path": str(plan_path),
        "timelines_built": [STAGING],
        "staged_timelines": {FINAL: STAGING},
        # This is the gain recorded by Reel 24's build log.
        "draw_gain_calibration": {"gain": 1.0},
    }
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {"build_reels": {"reel_build": record}},
    }), encoding="utf-8")
    transcript = transcript_path(tmp_path)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")

    def snapshot_for(timeline, _project_name):
        transform = (raw_transform if timeline.GetName() == STAGING else None)
        return _snapshot(timeline, transform)

    # A pass promotes in this operation; isolate the read-only verifier
    # from that separate Resolve write.
    with patch("library.tools.marker_feedback.connect_resolve",
               return_value=resolve), \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=project), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=snapshot_for), \
            patch.object(verifier, "_catalog_source_sizes",
                         return_value={SOURCE: {
                             "width": 3840, "height": 2160,
                             "rotation": 0}}), \
            patch("library.tools.delivery_format.resolve_delivery_format",
                  return_value=(1080, 1920)), \
            patch.object(reel_look, "resolve_look", return_value=LOOK), \
            patch.object(reel_look, "screen_window_rect_for",
                         return_value=WINDOW), \
            patch.object(verifier, "_declared_framing",
                         return_value=(0.0, 1.0)), \
            patch("library.tools.reel_quality_bar.judge",
                  return_value=BarReport()), \
            patch.object(verifier, "verify_reel",
                         side_effect=_focused_f12_result), \
            patch("library.tools.reel_build.promote_staged_reels",
                  return_value={"promoted": [FINAL],
                                "organised": None, "markers": {}}), \
            patch("library.tools.reel_build."
                  "sweep_all_reels_informational"), \
            patch("library.tools.versions.store.record_reel_promotion",
                  return_value={"committed": False,
                                "reason": "offline verifier test"}):
            result = operations.get("reel.verify").execute(str(tmp_path))

    assert result.completed, result.error
    output = capsys.readouterr().err
    assert "restored Pan x4, Tilt x4 to 1080x1920" in output
    report_path = tmp_path / "pipeline_output" / "review" / \
        "conformance_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = [finding for reel in report["reels"]
                for finding in reel["findings"]]
    assert not [finding for finding in findings
                if finding["finding_class"] == "F12"]
    assert report["read_only_proof"]["all_identical"] is True
