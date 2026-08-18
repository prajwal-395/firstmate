"""
Test dashboard readers against REAL captured pipeline state.

This test loads ``tests/fixtures/captured_run/broken_step_outputs.json``
- a 77KB file from a real broken pipeline run - and verifies the dashboard
readers extract non-empty values using the correct key names.

This is the guard against the dominant bug class (AGENTS.md section 10):
a reader .get()s a wrong key name, gets the default, and the dashboard
reports success over empty data.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from library.dashboard.server import app

CAPTURED_RUN_DIR = Path(__file__).parent / "fixtures" / "captured_run"
BROKEN_OUTPUTS = CAPTURED_RUN_DIR / "broken_step_outputs.json"


@pytest.fixture
def captured_project(tmp_path):
    """Build a realistic pipeline_data.json from the captured run fixture.

    The captured fixture stores step outputs at the top level (keyed by
    step id), not under step_outputs.  We wrap it into the full
    pipeline_data.json shape that the dashboard server expects.
    """
    project_dir = tmp_path / "captured_project"
    project_dir.mkdir()

    with open(BROKEN_OUTPUTS) as f:
        raw = json.load(f)

    # The captured fixture has keys like "mesh_spine", "catalog", etc.
    # at the top level.  Wrap them as step_outputs.
    pipeline_data = {
        "pipeline_version": "1.0",
        "started_at": "2026-08-14T18:51:18",
        "project_folder": str(project_dir),
        "steps_completed": {k: {"completed_at": "2026-08-14T19:00:00", "elapsed_s": 10} for k in raw.keys()},
        "failed_steps": [],
        "step_outputs": raw,
    }

    with open(project_dir / "pipeline_data.json", "w") as f:
        json.dump(pipeline_data, f, indent=2)

    with open(project_dir / "project.yaml", "w") as f:
        f.write("name: Captured Run\nslug: captured-run\n")

    return str(project_dir)


@pytest.fixture
def client(captured_project):
    with patch("library.dashboard.server._get_project_dir", return_value=captured_project):
        yield TestClient(app)


# ── Clips: real captured catalog has 17 clips ──────────────────────

def test_captured_clips_non_empty(client):
    """The captured run has 17 clips.  Every clip must show a real
    duration, resolution, and frame rate - not zeros/empty.
    """
    response = client.get("/api/clips")
    assert response.status_code == 200
    clips = response.json()

    assert len(clips) >= 10, (
        f"Expected >=10 clips from captured run, got {len(clips)}. "
        "The clip_catalog key is probably missing or the catalog wrapper is wrong."
    )

    for clip in clips:
        assert clip["duration_s"] > 0, (
            f"Clip {clip['clip_id']} has duration_s=0. "
            "Reader is using 'duration_s' instead of 'duration_seconds'."
        )
        assert clip["resolution"], (
            f"Clip {clip['clip_id']} has empty resolution. "
            "Reader is using 'resolution' key instead of width/height."
        )
        assert "x" in clip["resolution"], (
            f"Clip {clip['clip_id']} resolution={clip['resolution']!r} - not WxH format."
        )
        assert clip["fps"] > 0, (
            f"Clip {clip['clip_id']} has fps=0. "
            "Reader is using 'fps' instead of 'frame_rate'."
        )


# ── Step status: all captured steps should be completed ─────────────

def test_captured_step_status(client, captured_project):
    """All steps in the captured run should show as completed."""
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [
                {"id": "catalog", "name": "Catalog"},
                {"id": "mesh_spine", "name": "Mesh Spine"},
                {"id": "select_broll", "name": "Select B-Roll"},
                {"id": "plan_transitions", "name": "Plan Transitions"},
                {"id": "plan_sfx", "name": "Plan SFX"},
                {"id": "plan_vfx", "name": "Plan VFX"},
            ]
        }

        response = client.get("/api/steps")
        assert response.status_code == 200
        steps = response.json()

        for step in steps:
            assert step["status"] == "completed", (
                f"Step {step['id']} should be completed but is {step['status']!r}"
            )


# ── Step detail: catalog output has clip_catalog ────────────────────

def test_captured_step_detail_catalog(client, captured_project):
    """Step detail for catalog should return the full clip_catalog."""
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [{"id": "catalog", "name": "Catalog"}]
        }

        response = client.get("/api/steps/catalog")
        assert response.status_code == 200
        detail = response.json()
        assert detail["id"] == "catalog"
        assert "clip_catalog" in detail["output"]
        assert len(detail["output"]["clip_catalog"]) >= 10


# ── Pipeline status endpoint ───────────────────────────────────────

def test_captured_pipeline_status(client, captured_project):
    """Pipeline status should report all captured steps as completed."""
    response = client.get("/api/pipeline/status")
    assert response.status_code == 200
    status = response.json()
    assert len(status["completed_steps"]) >= 10
    assert len(status["failed_steps"]) == 0
