"""T17: creative QA after delivery, with one bounded repair.

Acceptance, each hermetic under `tmp_path` (no Resolve, no render, no
model - deliverable QC is faked at its seam, the way the assignment-QA
contract test fakes it):

1. a fixture render with a measurable defect (loudness out of spec)
   gets exactly one repair attempt, a re-measure, and a receipt showing
   both;
2. a subjective finding produces a question on the review page and no
   edit;
3. a clean render passes untouched.

Fails on main: `lufs` is not in `AUTO_REPAIRABLE` there, so the repair
is refused as editorial, and no driver or review-page question exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TIMELINE = "Reel 01 - pricing"
INTENT = {
    "duration": {"key": "delivery.duration", "provenance": "delivery spec tiktok"},
    "black_frames": {
        "key": "delivery.no_undeclared_black",
        "provenance": "delivery spec tiktok",
    },
    "lufs": {"key": "delivery.loudness", "provenance": "delivery spec tiktok"},
    "grade": {"key": "picture.grade_delivery", "provenance": "delivery spec tiktok"},
}
SPEC = SimpleNamespace(platform="tiktok")


def _result(metric, passed, detail="measured"):
    from library.tools.render_qa import RenderQAResult

    return RenderQAResult(
        metric,
        passed,
        {"observed": 1},
        {"target": 2},
        "error" if not passed else "info",
        detail,
    )


def _verdict(loudness_passed=True, grade_passed=True):
    return SimpleNamespace(
        duration=_result("duration", True),
        black_frames=_result("black_frames", True),
        loudness=_result(
            "lufs",
            loudness_passed,
            "LUFS: -14.02, True Peak: -1.62"
            if loudness_passed
            else "LUFS: -20.94, True Peak: -1.62 - integrated -20.94 LUFS is "
            "6.94 dB off the -14.0 target (tolerance +/-1.0)",
        ),
        grade=_result("grade", grade_passed),
        captions=None,
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    from library.tools.project_layout import ProjectLayout

    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    for marker in ("REN_HOST", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.delenv(marker, raising=False)
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\nproject_format_version: 1\n',
        encoding="utf-8",
    )
    ProjectLayout(str(root)).ensure()
    return root


@pytest.fixture
def loudness_qc(monkeypatch):
    """Deliverable QC: loudness fails once, then measures clean."""
    from library.tools import deliverable_qc

    calls = []

    def fake(*args, **kwargs):
        calls.append(True)
        return _verdict(loudness_passed=len(calls) > 1)

    monkeypatch.setattr(deliverable_qc, "run_deliverable_qc", fake)
    return calls


def _subjective_note():
    return {
        "overall": "concerns",
        "notes": [
            {
                "metric": "opening_tone",
                "detail": "Opening feels abrupt",
                "span": {"start": 0, "end": 2, "label": "opening"},
                "intent": {"key": "story.pacing", "provenance": "intent_model.intro"},
                "passed": False,
            }
        ],
    }


def test_loudness_defect_gets_one_repair_remeasure_and_receipt(project, loudness_qc):
    """Catches: loudness refused as editorial, a second repair attempt,
    a repair closed without re-measuring, a receipt showing only half."""
    from library.tools import post_delivery_qa as qa

    repairs = []

    def repair(finding):
        repairs.append((finding.id, finding.action))
        return {
            "receipt": {"operation": "loudness_normalize", "target_lufs": -14.0},
            "inverse": {"operation": "restore_prior_gain"},
        }

    result = qa.run_for_deliverable(
        str(project),
        timeline=TIMELINE,
        video_path="fixture.mp4",
        spec=SPEC,
        expected_duration=12.0,
        assignment_id="assignment-1",
        render_id="render-7",
        intent_by_metric=INTENT,
        render_review={},
        repair=repair,
        reexport=lambda _attempt: {
            "video_path": "fixture-fresh.mp4", "expected_duration": 12.0,
        },
    )

    assert len(repairs) == 1, "exactly one repair attempt"
    assert repairs[0][1] == "repair_once", (
        "loudness enters the repair as repair_once, not blocked"
    )
    assert len(loudness_qc) == 2, "measured, then re-measured after repair"
    assert result["status"] == "clean"
    assert result["report"]["deterministic"][0]["passed"] is True
    attempt = result["repair"]["attempt"]
    assert attempt["finding_id"] == repairs[0][0]
    assert attempt["receipt"]["operation"] == "loudness_normalize"
    assert attempt["inverse"]["operation"] == "restore_prior_gain"
    assert result["repair"]["verified"] == {
        "passed": True,
        "detail": "LUFS: -14.02, True Peak: -1.62",
        "all_criteria_passed": True,
        "regressions": [],
    }
    stored = json.loads(qa.store_path(str(project)).read_text(encoding="utf-8"))
    assert stored["receipts"][-1]["repair"]["attempt"] == attempt
    assert stored["receipts"][-1]["repair"]["verified"]["passed"] is True


def test_repair_that_fixes_loudness_but_regresses_grade_stays_blocked(
    project, monkeypatch
):
    """Catches: target-only success hiding a new failed detector."""
    from library.tools import deliverable_qc
    from library.tools import post_delivery_qa as qa

    calls = []

    def measure(*_args, **_kwargs):
        calls.append(True)
        return _verdict(loudness_passed=len(calls) > 1,
                        grade_passed=len(calls) == 1)

    monkeypatch.setattr(deliverable_qc, "run_deliverable_qc", measure)
    result = qa.run_for_deliverable(
        str(project), timeline=TIMELINE, video_path="fixture.mp4",
        spec=SPEC, expected_duration=12.0, render_id="render-regression",
        intent_by_metric=INTENT, render_review={},
        repair=lambda _finding: {
            "receipt": {"operation": "loudness_normalize"},
            "inverse": {"operation": "restore_prior_gain"},
        },
        reexport=lambda _attempt: {
            "video_path": "fixture-fresh.mp4", "expected_duration": 12.0,
        },
    )

    assert len(calls) == 2
    assert result["status"] == "repair_failed"
    assert result["repair"]["verified"] == {
        "passed": True,
        "detail": "LUFS: -14.02, True Peak: -1.62",
        "all_criteria_passed": False,
        "regressions": ["grade"],
    }
    assert result["report"]["deterministic"][-1]["passed"] is False


def test_a_repair_that_still_fails_remeasure_is_reported_not_closed(
    project, monkeypatch
):
    """Catches: a failed re-measure recorded as a repair."""
    from library.tools import deliverable_qc
    from library.tools import post_delivery_qa as qa

    monkeypatch.setattr(
        deliverable_qc,
        "run_deliverable_qc",
        lambda *a, **k: _verdict(loudness_passed=False),
    )

    result = qa.run_for_deliverable(
        str(project),
        timeline=TIMELINE,
        video_path="fixture.mp4",
        spec=SPEC,
        expected_duration=12.0,
        render_id="render-7",
        intent_by_metric=INTENT,
        render_review={},
        repair=lambda finding: {
            "receipt": {"operation": "x"},
            "inverse": {"operation": "un-x"},
        },
        reexport=lambda _attempt: {
            "video_path": "fixture-fresh.mp4", "expected_duration": 12.0,
        },
    )

    assert result["status"] == "repair_failed"
    assert result["repair"]["verified"]["passed"] is False
    assert "6.94 dB off the -14.0 target" in (result["repair"]["verified"]["detail"])
    assert result["report"]["blocks_delivery"] is True


def test_subjective_finding_becomes_a_review_page_question_and_no_edit(
    project, monkeypatch
):
    """Catches: an editorial call patched instead of asked, a question
    the review page never shows, a repeated run asking twice."""
    from library.tools import deliverable_qc, revise, round_page
    from library.tools import post_delivery_qa as qa

    monkeypatch.setattr(
        deliverable_qc,
        "run_deliverable_qc",
        lambda *a, **k: _verdict(loudness_passed=True),
    )

    def no_repair(finding):
        raise AssertionError("subjective findings are never repaired")

    first = qa.run_for_deliverable(
        str(project),
        timeline=TIMELINE,
        video_path="fixture.mp4",
        spec=SPEC,
        expected_duration=12.0,
        render_id="render-7",
        intent_by_metric=INTENT,
        render_review=_subjective_note(),
        repair=no_repair,
    )

    assert first["status"] == "creator_review"
    assert first["repair"] == {}
    assert len(first["questions"]) == 1
    assert "Opening feels abrupt" in first["questions"][0]["question"]
    assert revise.records(str(project)) == [], "no edit was made"

    page = round_page.build(str(project))
    asked = [entry for entry in page["questions"] if entry["kind"] == "creative_qa"]
    assert len(asked) == 1
    assert asked[0]["reel"] == TIMELINE
    assert "Opening feels abrupt" in asked[0]["note"]
    assert asked[0]["where"] == "0.00-2.00s"
    assert "ren revise" in asked[0]["command"]

    second = qa.run_for_deliverable(
        str(project),
        timeline=TIMELINE,
        video_path="fixture.mp4",
        spec=SPEC,
        expected_duration=12.0,
        render_id="render-7",
        intent_by_metric=INTENT,
        render_review=_subjective_note(),
        repair=no_repair,
    )
    assert second["questions"] == [], "the same finding is asked once"


def test_clean_render_passes_untouched(project, monkeypatch):
    """Catches: a clean render repaired, questioned, or left unrecorded."""
    from library.tools import deliverable_qc, revise, round_page
    from library.tools import post_delivery_qa as qa

    monkeypatch.setattr(
        deliverable_qc,
        "run_deliverable_qc",
        lambda *a, **k: _verdict(loudness_passed=True),
    )

    def no_repair(finding):
        raise AssertionError("a clean render needs no repair")

    result = qa.run_for_deliverable(
        str(project),
        timeline=TIMELINE,
        video_path="fixture.mp4",
        spec=SPEC,
        expected_duration=12.0,
        render_id="render-clean",
        intent_by_metric=INTENT,
        render_review={},
        repair=no_repair,
    )

    assert result["status"] == "clean"
    assert result["repair"] == {}
    assert result["questions"] == []
    assert revise.records(str(project)) == []
    assert [
        entry
        for entry in round_page.build(str(project))["questions"]
        if entry["kind"] == "creative_qa"
    ] == []
    stored = json.loads(qa.store_path(str(project)).read_text(encoding="utf-8"))
    assert stored["receipts"][-1]["status"] == "clean"


def test_default_loudness_repair_travels_the_revise_path(project, monkeypatch):
    """Catches: a repair that bypasses T10, a note that invents numbers,
    an inverse that cannot undo."""
    from library.tools import creative_qa, revise
    from library.tools import post_delivery_qa as qa

    started = []

    def fake_start(project_folder, note, **options):
        started.append({"note": note, "options": options})
        return {
            "revise_id": "revise_abc123",
            "status": "patched",
            "reply": "The verified change was applied.",
        }

    monkeypatch.setattr(revise, "start", fake_start)
    report = creative_qa.from_measurements(
        "assignment-1",
        "render-7",
        [_result("lufs", False, "LUFS: -20.94")],
        duration=12,
        intent_by_metric={"lufs": INTENT["lufs"]},
    )
    finding = report.findings[0]
    assert finding.action == "repair_once"

    out = qa.revise_routed_repair(str(project), TIMELINE)(finding)

    assert len(started) == 1
    assert started[0]["options"]["reel"] == TIMELINE
    assert "LUFS: -20.94" in started[0]["note"], (
        "the note carries the finding's own measurement"
    )
    assert out["receipt"]["route"] == "ren revise"
    assert out["receipt"]["revise_id"] == "revise_abc123"
    assert "ren undo" in out["inverse"]["undo"]


def test_an_untranslated_revision_is_not_recorded_as_an_applied_correction(
    project, monkeypatch
):
    """Catches: a translation request passing as an AXI edit receipt."""
    from library.tools import creative_qa, revise
    from library.tools import post_delivery_qa as qa

    monkeypatch.setattr(revise, "start", lambda *a, **k: {
        "revise_id": "revise_waiting", "status": "awaiting_translation",
        "reply": "Translating the note.",
    })
    report = creative_qa.from_measurements(
        "assignment-1", "render-7", [_result("lufs", False, "LUFS: -20.94")],
        duration=12, intent_by_metric={"lufs": INTENT["lufs"]})

    with pytest.raises(qa.PostDeliveryQARefused, match="not an AXI-verified"):
        qa.revise_routed_repair(str(project), TIMELINE)(report.findings[0])


def test_qa_verb_is_registered_in_both_front_doors():
    """Catches: `ren qa` known to one front door and refused by the other."""
    from ren.commands import VERBS

    by_name = {verb.name: verb for verb in VERBS}
    assert by_name["qa"].module_argv == ("library.tools.post_delivery_qa",)
    assert by_name["qa"].json
    native = (
        Path(__file__).resolve().parents[2]
        / "native"
        / "ren-cli"
        / "src"
        / "commands.rs"
    ).read_text(encoding="utf-8")
    assert 'name: "qa"' in native
