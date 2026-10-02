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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.skills.verify_timeline import skill
from library.tools.timeline_layout import (
    A_ROLL,
    SPEECH,
    TrackPlan,
    TrackSpec,
)
from tests.resolve_double import FakeTimeline, TimelineItemSpec


def _plan():
    return TrackPlan(
        video_tracks=[
            TrackSpec(
                index=1,
                media_type="video",
                role=A_ROLL,
                name="A-Roll Cam A",
                occupant="a",
            )
        ],
        audio_tracks=[
            TrackSpec(
                index=1,
                media_type="audio",
                role=SPEECH,
                name="Speech Cam A",
                occupant="a",
            )
        ],
        material={"angles": [{"key": "a", "program_channel": 1}]},
    )


def _conforming_timeline():
    mapping = json.dumps(
        {
            "embedded_audio_channels": 4,
            "linked_audio": {},
            "track_mapping": {"1": {"channel_idx": [1], "mute": False, "type": "mono"}},
        }
    )
    timeline = FakeTimeline(
        "Reel 09",
        video=[("A-Roll Cam A", [TimelineItemSpec("picture", 0, 100)])],
        audio=[
            (
                "Speech Cam A",
                [
                    TimelineItemSpec(
                        "speech", 0, 100, source_audio_channel_mapping=mapping
                    )
                ],
            )
        ],
    )
    picture = timeline.GetItemListInTrack("video", 1)[0]
    speech = timeline.GetItemListInTrack("audio", 1)[0]
    timeline.SetClipsLinked([picture, speech], True)
    return timeline


@pytest.fixture()
def live_timeline(monkeypatch):
    """Resolve answers with a conforming timeline, by exact name."""
    timeline = _conforming_timeline()
    monkeypatch.setattr(skill, "open_timeline", lambda project, name: timeline)
    return timeline


def test_passes_a_conforming_timeline(tmp_path, live_timeline):
    verdict = skill.run(
        "Reel 09",
        str(tmp_path),
        "build",
        project="Exact Project",
        plan=_plan().serializable(),
    )
    assert verdict["passed"] is True, verdict["issues"]
    assert verdict["receipt"].endswith("verify_timeline.json")
    assert verdict["checks_skipped"] == []
    assert all(c["passed"] for c in verdict["checks"])
    record = json.loads(Path(verdict["receipt"]).read_text(encoding="utf-8"))
    assert record["step_id"] == "build"
    assert record["result"]["passed"] is True


def test_fails_a_timeline_with_an_empty_row(tmp_path, live_timeline):
    row = live_timeline.AddTrack("video")
    live_timeline.SetTrackName("video", row, "Overlay Still")
    verdict = skill.run(
        "Reel 09",
        str(tmp_path),
        "build",
        project="Exact Project",
        plan=_plan().serializable(),
    )
    assert verdict["passed"] is False
    assert any("Empty row" in issue for issue in verdict["issues"]), verdict["issues"]
    failed = [c["name"] for c in verdict["checks"] if not c["passed"]]
    assert failed == ["no_empty_tracks"]
    assert verdict["receipt"] is not None


def test_fails_unlinked_aroll(tmp_path, live_timeline):
    live_timeline.add_item("video", 1, TimelineItemSpec("stray", 200, 300))
    verdict = skill.run(
        "Reel 09",
        str(tmp_path),
        "build",
        project="Exact Project",
        plan=_plan().serializable(),
    )
    assert verdict["passed"] is False
    assert any("links to nothing" in issue for issue in verdict["issues"])


def test_without_a_plan_the_link_checks_skip_openly(tmp_path, live_timeline):
    verdict = skill.run("Reel 09", str(tmp_path), "build", project="Exact Project")
    assert verdict["passed"] is True
    assert sorted(verdict["checks_skipped"]) == [
        "aroll_linked",
        "captions_linked",
        "program_stream",
    ]


def test_an_unreachable_timeline_is_a_refusal_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(
        skill,
        "open_timeline",
        lambda project, name: (_ for _ in ()).throw(
            skill.TimelineUnreachable("Resolve is not running.")
        ),
    )
    verdict = skill.run("Reel 09", str(tmp_path), "build", project="Exact Project")
    assert verdict["passed"] is False
    assert verdict["issues"] == ["Resolve is not running."]
    assert verdict["receipt"].endswith("verify_timeline.json")


def test_a_malformed_plan_is_a_refusal_not_a_pass(tmp_path, live_timeline):
    verdict = skill.run(
        "Reel 09",
        str(tmp_path),
        "build",
        project="Exact Project",
        plan={"video_tracks": "not-a-list"},
    )
    assert verdict["passed"] is False
    assert "video_tracks" in verdict["issues"][0]


def test_cli_passes_and_fails_by_exit_code(tmp_path, live_timeline, capsys):
    plan_json = json.dumps(_plan().serializable())
    base = [
        "--project",
        "Exact Project",
        "--timeline",
        "Reel 09",
        "--project-folder",
        str(tmp_path),
        "--step-id",
        "build",
        "--plan-json",
        plan_json,
    ]
    assert skill.main(base) == 0
    capsys.readouterr()
    row = live_timeline.AddTrack("video")
    live_timeline.SetTrackName("video", row, "Overlay Still")
    assert skill.main(base) == 1
    assert skill.main(base[:-1] + ["{not json"]) == 2
