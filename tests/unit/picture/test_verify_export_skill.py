"""`verify_export`: the gate the model reaches for before shipping a file.

Two things this skill must not do, and the tests name them:

- it must not PASS an export nobody could check.  The captain's rule is
  that "a verified export includes evidence for every required
  acceptance criterion", so a criterion that comes back UNKNOWN has to
  leave the verdict failing.
- it must not PASS an export that is not there.  An absent file is a
  refusal, recorded in the receipt, never a clean verdict.

The third test is the catalogue: the skill has to be in the registry
with its model-facing text where the prompt says it is, or the model is
told about a skill nothing holds.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from library.tools import pipeline_skills


def _build_video(path: str) -> None:
    """One second of picture with a tone, small enough to build fast."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "testsrc=s=320x568:r=30:d=1.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", "-t", "1.0", str(path)],
        capture_output=True, check=True)


@pytest.fixture()
def export_file(tmp_path):
    path = str(tmp_path / "export.mp4")
    _build_video(path)
    return path


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_an_absent_export_refuses_and_records_the_refusal(tmp_path):
    """An absent file is not a clean export. The refusal is a receipted
    verdict, so the step that asked for the check can read it."""
    from library.skills.verify_export.skill import run

    verdict = run(str(tmp_path / "nothing.mp4"), str(tmp_path), "verify-reel")
    assert verdict["passed"] is False
    assert verdict["criteria"] == []
    assert "not found" in verdict["issues"][0]
    receipt = _read(verdict["receipt"])
    assert receipt["skill"] == "verify_export"
    assert receipt["result"]["passed"] is False


def test_an_export_nobody_could_check_does_not_pass(tmp_path, export_file):
    """The defect this capability exists to prevent: the evidence was
    missing - no accepted plan, no build, no delivery - and the export
    was called good anyway."""
    from library.skills.verify_export.skill import run

    verdict = run(export_file, str(tmp_path), "verify-reel")
    assert verdict["passed"] is False
    assert "QA-001" in verdict["unknown"]
    assert "QA-002" in verdict["unknown"]
    assert verdict["issues"]


def test_the_receipt_names_every_criterion(tmp_path, export_file):
    """The receipt is what the must-check rule reads back, so it carries
    the whole verdict: one row per acceptance criterion, each with its
    own verdict."""
    from library.skills.verify_export.skill import run

    verdict = run(export_file, str(tmp_path), "verify-reel")
    receipt = _read(verdict["receipt"])
    criteria = receipt["result"]["criteria"]
    assert [row["criterion"] for row in criteria] == [
        "QA-001", "QA-002", "QA-003", "QA-004",
        "QA-005", "QA-006", "QA-007", "QA-008"]
    for row in criteria:
        assert row["verdict"] in ("pass", "fail", "unknown", "not_applicable")
        assert row["detail"]
        assert row["reader"]


def test_the_skill_is_in_the_catalogue_with_its_text_where_the_prompt_says(
        tmp_path):
    """The registry is what the prompt renders, and the text is what
    the model reads: a skill with no SKILL.md is a skill nobody can
    call."""
    skill = pipeline_skills.SKILLS.get("verify_export")
    assert skill is not None
    assert skill.kind == pipeline_skills.GATE
    text = os.path.join(pipeline_skills.SKILL_TEXT_ROOT, "verify_export",
                         "SKILL.md")
    assert os.path.isfile(text)
    with open(text, encoding="utf-8") as handle:
        body = handle.read()
    assert body.startswith("---")
    assert "name: verify_export" in body
