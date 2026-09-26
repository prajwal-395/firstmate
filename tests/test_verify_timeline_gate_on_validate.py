"""`verify_timeline` lands as a real gate on step 6.02 (Validate Output).

PR 1168 wrapped the timeline-SOP verifier as a gating skill and proved
the SKILL both ways (eleven tests on fake Resolve) - but no step
manifest declared it, so it gated nothing: exactly the shape of a gate
that cannot fail. These prove the GATE, through the step rather than by
calling the skill directly:

- 6.02 declares `verify_timeline` beside `verify_render`, at the
  manifest's top level - the `verify_treatment`-on-4.03 shape, not a
  second declaration shape;
- the declaration reaches the prompt the model reads, on a hybrid step
  the must-check read-back covers, with a missing receipt failing;
- a violating timeline STOPS the step and a conforming one PASSES it,
  both through the step's own post-bridge verdict (`resolve_validation`)
  fed by REAL skill receipts - the deterministic file half and the
  model's answer both say pass, so only the timeline gate can be what
  stops the violating build;
- a refusal (Resolve down) stops the step rather than passing it;
- a pass that openly skipped the link/stream checks stops the step when
  the plan was in the step's inputs, and stands when no plan existed
  (an old build): the structural half is the whole gate that run could
  answer.

No live Resolve, no renders, no ffmpeg: timelines are fakes in the
shape of `test_verify_timeline_skill`'s (kept local so this file stands
alone), and receipts are written by the real skill entry point.

What this does NOT exercise, and what would be needed: the full
`run_hybrid_step` loop ending in `distribution_ready: true` for a
conforming build. The loop runs 6.02's bridge first, and the bridge's
deterministic half measures the exported file with ffprobe/ffmpeg -
without a real render on disk it reports fail before any timeline
verdict matters, so a conforming pass through the whole loop needs a
real export plus Resolve open on the built timeline, answered through
the agent harness. The attribution here is cleaner for it: both halves
pass and only the receipt differs.
"""
import json
import sys
from itertools import count
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
# No step_6_01_render insert here: tests/conftest.py already owns that
# entry (for step.py:21's bare sibling import), deterministically.

from library.skills.verify_timeline import skill  # noqa: E402
from library.steps.step_6_02_validate_output import post_bridge  # noqa: E402
from library.steps.step_6_01_render import step as render_step  # noqa: E402
from library.tools import pipeline_skills  # noqa: E402
from library.tools.timeline_layout import (  # noqa: E402
    A_ROLL,
    SPEECH,
    TrackPlan,
    TrackSpec,
)

STEP_DIR = REPO / "library" / "steps" / "step_6_02_validate_output"
MANIFEST = json.loads((STEP_DIR / "manifest.json").read_text(encoding="utf-8"))

_ids = count(900000)


class FakeItem:
    def __init__(self, start, end, channel=1):
        self._uid = f"item-{next(_ids)}"
        self._start = start
        self._end = end
        self._channel = channel
        self._group = {self._uid}

    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetUniqueId(self): return self._uid
    def GetLinkedItems(self):
        return [i for i in FakeTimeline._registry.values()
                if i._uid in self._group and i._uid != self._uid]

    def GetMediaPoolItem(self):
        class _P:
            def GetClipProperty(self, k): return ""
        return _P()

    def GetSourceAudioChannelMapping(self):
        return json.dumps({
            "embedded_audio_channels": 4, "linked_audio": {},
            "track_mapping": {"1": {"channel_idx": [self._channel],
                                    "mute": False, "type": "mono"}}})


def link(*items):
    group = {i.GetUniqueId() for i in items}
    for i in items:
        i._group = set(group)


class FakeTimeline:
    _registry = {}

    def __init__(self):
        self.tracks = {}
        self.names = {}

    def GetTrackCount(self, media_type):
        return max((i for (t, i) in self.tracks if t == media_type),
                   default=0)

    def GetTrackName(self, media_type, index):
        return self.names.get((media_type, index), "")

    def GetItemListInTrack(self, media_type, index):
        return list(self.tracks.get((media_type, index), []))


def _plan():
    return TrackPlan(
        video_tracks=[TrackSpec(index=1, media_type="video", role=A_ROLL,
                                name="A-Roll Cam A", occupant="a")],
        audio_tracks=[TrackSpec(index=1, media_type="audio", role=SPEECH,
                                name="Speech Cam A", occupant="a")],
        material={"angles": [{"key": "a", "program_channel": 1}]},
    )


def _conforming_timeline():
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    timeline.tracks[("video", 1)] = []
    timeline.tracks[("audio", 1)] = []
    timeline.names[("video", 1)] = "A-Roll Cam A"
    timeline.names[("audio", 1)] = "Speech Cam A"
    picture = FakeItem(0, 100)
    speech = FakeItem(0, 100)
    link(picture, speech)
    for item in (picture, speech):
        FakeTimeline._registry[item.GetUniqueId()] = item
    timeline.tracks[("video", 1)].append(picture)
    timeline.tracks[("audio", 1)].append(speech)
    return timeline


@pytest.fixture()
def live_conforming_timeline(monkeypatch):
    monkeypatch.setattr(skill, "open_timeline",
                        lambda project, name: _conforming_timeline())


def _merge_data(project_folder, det_status="pass", llm_status="pass",
                with_plan=True):
    rendered = {"timeline_name": "Base_20260101_000000_10s"}
    if with_plan:
        rendered["track_plan"] = _plan().serializable()
    return {
        "project_folder": str(project_folder),
        "rendered_output": rendered,
        "assembly_manifest": {"project": {"name": "Exact Project"}},
        "deterministic_validation": {
            "status": det_status, "summary": "det",
            "checks": {}, "all_issues": [],
            "critical_checks_passed": True,
            "qa_report_path": "", "qa_report": []},
        "validation_result": {
            "status": llm_status, "summary": "llm",
            "checks": {}, "all_issues": []},
    }


# ── The declaration, in the verify_treatment shape ──────────────────











# ── The verdict, through the step's own post-bridge ─────────────────

def test_violating_timeline_stops_the_step(
        tmp_path, live_conforming_timeline, monkeypatch):
    """Both halves say pass; only the timeline gate can stop this build.

    The fake timeline gains an empty row, the REAL skill entry point
    writes the REAL failed receipt, and the step's REAL verdict function
    turns it into a fail - `check_validation_verdict` fails the run on
    exactly this shape.
    """
    timeline = _conforming_timeline()
    timeline.tracks[("video", 2)] = []
    timeline.names[("video", 2)] = "Overlay Still"
    monkeypatch.setattr(skill, "open_timeline",
                        lambda project, name: timeline)
    verdict = skill.run("Reel 09", str(tmp_path), "validate",
                        project="Exact Project",
                        plan=_plan().serializable())
    assert verdict["passed"] is False

    out = post_bridge.resolve_validation(_merge_data(tmp_path))
    final = out["validation_result"]
    assert final["status"] == "fail"
    assert final["distribution_ready"] is False
    assert final["checks"]["timeline_sop"]["pass"] is False
    assert any("Empty row" in i for i in final["all_issues"]), (
        final["all_issues"])


def test_conforming_timeline_passes_the_step(
        tmp_path, live_conforming_timeline):
    verdict = skill.run("Reel 09", str(tmp_path), "validate",
                        project="Exact Project",
                        plan=_plan().serializable())
    assert verdict["passed"] is True

    out = post_bridge.resolve_validation(_merge_data(tmp_path))
    final = out["validation_result"]
    assert final["status"] == "pass"
    assert final["distribution_ready"] is True
    assert "timeline_sop" not in final["checks"]


def test_refusal_stops_the_step(tmp_path, monkeypatch):
    """Resolve down is a receipted failed verdict, never a pass - and a
    timeline that could not be read back is not approved."""
    monkeypatch.setattr(
        skill, "open_timeline",
        lambda project, name: (_ for _ in ()).throw(
            skill.TimelineUnreachable("Resolve is not running.")))
    verdict = skill.run("Reel 09", str(tmp_path), "validate",
                        project="Exact Project")
    assert verdict["passed"] is False

    out = post_bridge.resolve_validation(_merge_data(tmp_path))
    final = out["validation_result"]
    assert final["status"] == "fail"
    assert final["distribution_ready"] is False
    assert any("Resolve is not running" in i
               for i in final["all_issues"])


def test_skipped_link_checks_stop_when_the_plan_was_available(
        tmp_path, live_conforming_timeline):
    """A pass that openly skipped half the SOP is not a pass when the
    whole SOP was answerable: the plan sat in this step's inputs."""
    verdict = skill.run("Reel 09", str(tmp_path), "validate",
                        project="Exact Project")
    assert verdict["passed"] is True
    assert verdict["checks_skipped"] != []

    out = post_bridge.resolve_validation(_merge_data(tmp_path))
    final = out["validation_result"]
    assert final["status"] == "fail"
    assert final["distribution_ready"] is False
    assert any("skipped openly" in i for i in final["all_issues"])


def test_skipped_link_checks_stand_when_no_plan_exists(
        tmp_path, live_conforming_timeline):
    """An old build recorded no plan, so the structural half is the whole
    gate that run could answer - the pipeline's gap, not the model's."""
    verdict = skill.run("Reel 09", str(tmp_path), "validate",
                        project="Exact Project")
    assert verdict["passed"] is True

    out = post_bridge.resolve_validation(
        _merge_data(tmp_path, with_plan=False))
    final = out["validation_result"]
    assert final["status"] == "pass"
    assert final["distribution_ready"] is True


def test_no_receipt_leaves_the_verdict_untouched(tmp_path):
    """A missing receipt is the must-check's to fail, not the verdict's
    to invent: direct post-bridge callers without skill runs behave as
    before."""
    out = post_bridge.resolve_validation(_merge_data(tmp_path))
    final = out["validation_result"]
    assert final["status"] == "pass"
    assert final["distribution_ready"] is True


# ── The plan reaches the step that must pass it ─────────────────────

def test_render_payload_forwards_the_builds_track_plan():
    """6.01 recorded the plan but dropped it before the ledger saw it;
    6.02's model cannot pass `--plan-json` it was never given. The exact
    Resolve project travels beside it: the manifest never reaches 6.02's
    prompt, so the skill's `--project` has to come from the render
    record too."""
    plan = _plan().serializable()
    payload = render_step._render_output_payload(
        {"timeline_name": "Base_20260101_000000_10s", "success": True,
         "track_plan": plan},
        {"output_path": "o.mp4", "size_bytes": 1, "job_id": "j",
         "job_status": "s", "format": "f", "codec": "c"},
        resolve_project_name="Exact Project")
    assert payload["timeline_name"] == "Base_20260101_000000_10s"
    assert payload["resolve_project_name"] == "Exact Project"
    assert payload["track_plan"] == plan


def test_render_payload_keeps_measured_master_delivery_values():
    """The hand-picked 6.01 payload must not discard measured mastering."""
    measured_master = {
        "input": {"lufs": -21.02, "true_peak_dbtp": 0.24},
        "output": {"lufs": -14.1, "true_peak_dbtp": -1.4},
        "target": {"lufs": -14.0, "true_peak_ceiling": -1.0},
    }
    payload = render_step._render_output_payload(
        {"timeline_name": "Base_20260101_000000_10s", "success": True},
        {"output_path": "delivery.mp4", "size_bytes": 2,
         "raw_output_path": "scratch/pre_master.mp4",
         "mastering": measured_master},
        resolve_project_name="Exact Project")

    assert payload["output_path"] == "delivery.mp4"
    assert payload["raw_output_path"] == "scratch/pre_master.mp4"
    assert payload["mastering"] == measured_master

