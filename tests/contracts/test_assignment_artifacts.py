"""Offline proof for first value, contextual onboarding, and delivery receipts."""
import json
from pathlib import Path

from library.tools import assignment_artifacts as artifacts


def test_catalog_publishes_first_value_before_deeper_analysis(tmp_path):
    catalog = {
        "total_clips": 1,
        "clip_catalog": [{"clip_id": "clip_001", "source_order": 1,
                          "filename": "interview.mov", "duration_seconds": 42.0,
                          "width": 1920, "height": 1080, "frame_rate": 30.0,
                          "path": "/media/interview.mov"}],
        "audio_catalog": [], "skipped_files": [],
    }
    path = artifacts.publish_first_value(str(tmp_path), catalog,
                                         assignment_id="assignment-1")
    first = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "first_value.json"
    assert first["available_after"] == "footage.catalog"
    assert first["deeper_analysis_complete"] is False
    assert first["clips"][0]["filename"] == "interview.mov"


def test_onboarding_is_contextual_and_dismissal_is_assignment_scoped(tmp_path):
    lesson = artifacts.load_onboarding(str(tmp_path), "assignment-1",
                                       context="review_ready")
    assert lesson["dismissible"] is True
    assert [row["stage"] for row in lesson["lessons"]] == ["review"]
    artifacts.dismiss_onboarding(str(tmp_path), "assignment-1")
    dismissed = artifacts.load_onboarding(str(tmp_path), "assignment-1",
                                          context="review_ready")
    other = artifacts.load_onboarding(str(tmp_path), "assignment-2",
                                      context="review_ready")
    assert dismissed["dismissed"] is True and dismissed["lessons"] == []
    assert other["lessons"]


def test_delivery_receipt_is_reproducible_and_names_failed_outputs():
    kwargs = {
        "assignment_id": "assignment-1",
        "approved_version": {"id": "v3", "assignment_id": "assignment-1",
                             "status": "approved"},
        "files": [
            {"name": "master.mp4", "path": "exports/master.mp4",
             "exists": True, "status": "ready", "spec": {"width": 1920},
             "qc": {"passed": True}},
            {"name": "vertical.mp4", "path": "exports/vertical.mp4",
             "platform": "shorts", "exists": False, "status": "missing",
             "spec": {"width": 1080}, "qc": None},
            {"name": "square.mp4", "path": "exports/square.mp4",
             "platform": "instagram", "exists": True, "status": "failed",
             "spec": {"width": 1080}, "qc": {"passed": False}},
        ],
        "qc": {"passed": True, "report": "qc.json"},
        "resume_point": {"action": "review_delivery", "stage": "deliver"},
        "selected_platforms": ["shorts"],
    }
    first = artifacts.build_delivery_receipt(**kwargs)
    second = artifacts.build_delivery_receipt(**kwargs)
    assert first == second
    assert [row["name"] for row in first["files"]] == ["master.mp4", "vertical.mp4"]
    assert first["missing_outputs"] == ["vertical.mp4"]
    assert first["failed_outputs"] == []
    assert first["status"] == "incomplete"
    assert first["resume_point"]["action"] == "review_delivery"


def test_failed_qc_is_reported_for_selected_platform_export():
    receipt = artifacts.build_delivery_receipt(
        assignment_id="assignment-1",
        approved_version={"id": "v1", "assignment_id": "assignment-1",
                          "status": "approved"},
        files=[{"name": "master.mp4", "path": "master.mp4", "exists": True,
                "status": "ready", "qc": {"passed": False}}],
        qc={"passed": False}, resume_point={"action": "fix_qc"})
    assert receipt["failed_outputs"] == ["master.mp4"]
    assert receipt["status"] == "failed"


def test_assignment_contract_exposes_onboarding_and_delivery(tmp_path):
    from library.tools import assignment_interaction

    lesson = assignment_interaction.contextual_onboarding(
        str(tmp_path), "assignment-1", context="delivery_ready")
    assert lesson["stage"] == "deliver"
    dismissed = assignment_interaction.dismiss_contextual_onboarding(
        str(tmp_path), "assignment-1")
    assert dismissed["status"] == "completed"
    receipt = assignment_interaction.delivery_receipt(
        str(tmp_path), assignment_id="assignment-1",
        approved_version={"id": "v2", "assignment_id": "assignment-1",
                          "status": "approved"},
        files=[{"name": "master.mp4", "path": "exports/master.mp4",
                "exists": True, "status": "ready", "spec": {"codec": "h264"},
                "qc": {"passed": True}}],
        qc={"passed": True}, resume_point={"action": "start_next_assignment"})
    assert receipt["operation_id"] == "assignment.deliver"
    assert receipt["status"] == "completed"
    saved = json.loads(Path(receipt["result"]["path"]).read_text(encoding="utf-8"))
    assert saved == receipt["result"]["receipt"]
