"""The timeline-SOP verifier as a gating pipeline skill, and the gate it
is on step 6.02 (Validate Output).

`library/skills/verify_timeline` wraps `timeline_conformance.verify_timeline`
the way `verify_render` wraps `render_qa`: a conforming timeline passes with
receipted per-check verdicts, a violating one fails naming the check, and an
unreachable timeline or malformed plan is a refusal verdict - never an
exception and never a pass. Fake Resolve only. Why the 6.02 half exists:
docs/evidence/verify_timeline.md.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.skills.verify_timeline import skill
from library.steps.step_6_01_render import step as render_step
from library.steps.step_6_02_validate_output import post_bridge
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


def test_a_violating_timeline_fails_naming_the_check(tmp_path, live_timeline):
    row = live_timeline.AddTrack("video")
    live_timeline.SetTrackName("video", row, "Overlay Still")
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project", plan=_plan().serializable())
    assert verdict["passed"] is False
    assert any("Empty row" in issue for issue in verdict["issues"]), verdict["issues"]
    failed = [c["name"] for c in verdict["checks"] if not c["passed"]]
    assert failed == ["no_empty_tracks"]
    assert verdict["receipt"] is not None

    live_timeline.add_item("video", 1, TimelineItemSpec("stray", 200, 300))
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project", plan=_plan().serializable())
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


def test_a_refusal_is_a_receipted_failed_verdict_not_a_pass(
        tmp_path, live_timeline, monkeypatch):
    verdict = skill.run("Reel 09", str(tmp_path), "build",
                        project="Exact Project",
                        plan={"video_tracks": "not-a-list"})
    assert verdict["passed"] is False
    assert "video_tracks" in verdict["issues"][0]

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


# ── The gate on step 6.02, through the step's own post-bridge ───────


def _merge_data(project_folder, with_plan=True):
    rendered = {"timeline_name": "Base_20260101_000000_10s"}
    if with_plan:
        rendered["track_plan"] = _plan().serializable()
    return {
        "project_folder": str(project_folder),
        "rendered_output": rendered,
        "assembly_manifest": {"project": {"name": "Exact Project"}},
        "deterministic_validation": {
            "status": "pass",
            "summary": "det",
            "checks": {},
            "all_issues": [],
            "critical_checks_passed": True,
            "qa_report_path": "",
            "qa_report": [],
        },
        "validation_result": {
            "status": "pass",
            "summary": "llm",
            "checks": {},
            "all_issues": [],
        },
    }


def _violating_timeline():
    timeline = _conforming_timeline()
    row = timeline.AddTrack("video")
    timeline.SetTrackName("video", row, "Overlay Still")
    return timeline


def _unreachable(project, name):
    raise skill.TimelineUnreachable("Resolve is not running.")


def test_the_receipt_decides_step_6_02(tmp_path, monkeypatch):
    """Both the deterministic half and the model say pass in every row, so
    only the REAL skill receipt can be what stops a build: a violation,
    a refusal (Resolve down) and a pass that skipped the link checks
    while the plan sat in the step's inputs all stop it; a conforming
    timeline, an old build with no plan, and no receipt at all (the
    must-check's to fail, not the verdict's to invent) stand."""
    rows = [
        # (opener, plan to the skill, plan in step inputs, status, needle)
        (lambda p, n: _violating_timeline(), True, True, "fail",
         "Empty row"),
        (_unreachable, False, True, "fail", "Resolve is not running"),
        (lambda p, n: _conforming_timeline(), False, True, "fail",
         "skipped openly"),
        (lambda p, n: _conforming_timeline(), True, True, "pass", None),
        (lambda p, n: _conforming_timeline(), False, False, "pass", None),
        (None, None, True, "pass", None),
    ]
    for index, (opener, skill_plan, step_plan, status, needle) in enumerate(rows):
        folder = tmp_path / str(index)
        folder.mkdir()
        if opener is not None:
            monkeypatch.setattr(skill, "open_timeline", opener)
            kwargs = {"plan": _plan().serializable()} if skill_plan else {}
            skill.run("Reel 09", str(folder), "validate",
                      project="Exact Project", **kwargs)
        final = post_bridge.resolve_validation(
            _merge_data(folder, with_plan=step_plan))["validation_result"]
        assert final["status"] == status, index
        assert final["distribution_ready"] is (status == "pass"), index
        if needle:
            assert any(needle in i for i in final["all_issues"]), (
                index, final["all_issues"])
        if index == 0:
            assert final["checks"]["timeline_sop"]["pass"] is False
        if index == 3:
            assert "timeline_sop" not in final["checks"]


def test_render_payload_forwards_the_plan_and_measured_master():
    """6.01 once recorded the plan but dropped it before the ledger saw it,
    so 6.02's model could not pass `--plan-json`. The exact Resolve project
    travels beside it (the manifest never reaches 6.02's prompt), and the
    hand-picked payload must not discard measured mastering."""
    plan = _plan().serializable()
    measured_master = {
        "input": {"lufs": -21.02, "true_peak_dbtp": 0.24},
        "output": {"lufs": -14.1, "true_peak_dbtp": -1.4},
        "target": {"lufs": -14.0, "true_peak_ceiling": -1.0},
    }
    payload = render_step._render_output_payload(
        {
            "timeline_name": "Base_20260101_000000_10s",
            "success": True,
            "track_plan": plan,
        },
        {
            "output_path": "delivery.mp4",
            "size_bytes": 2,
            "raw_output_path": "scratch/pre_master.mp4",
            "mastering": measured_master,
        },
        resolve_project_name="Exact Project",
    )
    assert payload["timeline_name"] == "Base_20260101_000000_10s"
    assert payload["resolve_project_name"] == "Exact Project"
    assert payload["track_plan"] == plan
    assert payload["output_path"] == "delivery.mp4"
    assert payload["raw_output_path"] == "scratch/pre_master.mp4"
    assert payload["mastering"] == measured_master
