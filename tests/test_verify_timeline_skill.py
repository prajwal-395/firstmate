"""The timeline-SOP verifier as a gating pipeline skill.

`library/skills/verify_timeline` wraps
`timeline_conformance.verify_timeline` the way `verify_render` wraps
`render_qa`: the same receipt read-back, the same refusal-instead-of-a-
pass, the same registry-driven dispatch. Proved both ways on fake
Resolve (no live connection, no ffmpeg):

- a conforming timeline passes and receipts real per-check verdicts;
- a violating timeline fails naming the check;
- an unreachable timeline, or a malformed plan, is a refusal verdict -
  never an exception and never a pass.
"""
import json
import sys
from itertools import count
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.skills.verify_timeline import skill  # noqa: E402
from library.tools import pipeline_skills  # noqa: E402
from library.tools.timeline_layout import (  # noqa: E402
    A_ROLL,
    SPEECH,
    TrackPlan,
    TrackSpec,
)

_ids = count(500000)


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
def live_timeline(monkeypatch):
    """Resolve answers with a conforming timeline, by exact name."""
    timeline = _conforming_timeline()
    monkeypatch.setattr(skill, "open_timeline",
                        lambda project, name: timeline)
    return timeline


def test_passes_a_conforming_timeline(tmp_path, live_timeline):
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project",
                        plan=_plan().serializable())
    assert verdict["passed"] is True, verdict["issues"]
    assert verdict["receipt"].endswith("verify_timeline.json")
    assert verdict["checks_skipped"] == []
    assert all(c["passed"] for c in verdict["checks"])
    record = json.loads(Path(verdict["receipt"]).read_text(
        encoding="utf-8"))
    assert record["step_id"] == "build"
    assert record["result"]["passed"] is True


def test_fails_a_timeline_with_an_empty_row(tmp_path, live_timeline):
    live_timeline.tracks[("video", 2)] = []
    live_timeline.names[("video", 2)] = "Overlay Still"
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project",
                        plan=_plan().serializable())
    assert verdict["passed"] is False
    assert any("Empty row" in issue for issue in verdict["issues"]), (
        verdict["issues"])
    failed = [c["name"] for c in verdict["checks"] if not c["passed"]]
    assert failed == ["no_empty_tracks"]
    assert verdict["receipt"] is not None


def test_fails_unlinked_aroll(tmp_path, live_timeline):
    stray = FakeItem(200, 300)
    FakeTimeline._registry[stray.GetUniqueId()] = stray
    live_timeline.tracks[("video", 1)].append(stray)
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project",
                        plan=_plan().serializable())
    assert verdict["passed"] is False
    assert any("links to nothing" in issue for issue in verdict["issues"])


def test_without_a_plan_the_link_checks_skip_openly(
        tmp_path, live_timeline):
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project")
    assert verdict["passed"] is True
    assert sorted(verdict["checks_skipped"]) == [
        "aroll_linked", "captions_linked", "program_stream"]


def test_an_unreachable_timeline_is_a_refusal_not_a_pass(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        skill, "open_timeline",
        lambda project, name: (_ for _ in ()).throw(
            skill.TimelineUnreachable("Resolve is not running.")))
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project")
    assert verdict["passed"] is False
    assert verdict["issues"] == ["Resolve is not running."]
    assert verdict["receipt"].endswith("verify_timeline.json")


def test_a_malformed_plan_is_a_refusal_not_a_pass(
        tmp_path, live_timeline):
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project",
                        plan={"video_tracks": "not-a-list"})
    assert verdict["passed"] is False
    assert "video_tracks" in verdict["issues"][0]


def test_missing_names_are_refusals(tmp_path, live_timeline):
    assert skill.run(
        "", str(tmp_path), "build",
        project="Exact Project")["passed"] is False
    assert skill.run(
        "Reel 09", str(tmp_path), "build",
        project="")["passed"] is False


def test_dispatches_by_registry_name(tmp_path, live_timeline):
    """The new shape: `run_skill` reaches the verifier by catalogue name.

    On the old shape this raises UnknownSkill - the catalogue held
    exactly three skills, none the SOP verifier.
    """
    result = pipeline_skills.run_skill(
        "verify_timeline", timeline_name="Reel 09",
        project_folder=str(tmp_path), step_id="build",
        project="Exact Project", plan=_plan().serializable())
    assert result["passed"] is True


def test_declared_skill_reaches_the_prompt():
    manifest = {"skills": ["verify_timeline"]}
    block = pipeline_skills.prompt_block("build", manifest, "agent")
    assert "verify_timeline" in block
    assert "python3 -m library.skills.verify_timeline.skill" in block
    pipeline_skills.assert_declared_skills_reach_prompt(
        "build", manifest, block)
    assert pipeline_skills.gating_skills(manifest, "build") == [
        "verify_timeline"]


def test_ran_gating_skill_reads_back_from_disk(tmp_path, live_timeline):
    skill.run("Reel 09", str(tmp_path), "build",
              project="Exact Project", plan=_plan().serializable())
    receipts = pipeline_skills.assert_gating_skills_ran(
        "build", {"skills": ["verify_timeline"]}, str(tmp_path))
    assert receipts["verify_timeline"]["result"]["passed"] is True


def test_cli_passes_and_fails_by_exit_code(tmp_path, live_timeline,
                                           capsys):
    plan_json = json.dumps(_plan().serializable())
    base = ["--project", "Exact Project", "--timeline", "Reel 09",
            "--project-folder", str(tmp_path), "--step-id", "build",
            "--plan-json", plan_json]
    assert skill.main(base) == 0
    capsys.readouterr()
    live_timeline.tracks[("video", 2)] = []
    live_timeline.names[("video", 2)] = "Overlay Still"
    assert skill.main(base) == 1
    assert skill.main(base[:-1] + ["{not json"]) == 2
