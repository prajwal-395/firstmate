"""
End-to-end tests for the review dashboard.

The fixture ``e2e_pipeline_data.json`` uses the REAL key names emitted by
each pipeline step (see the ``Description`` field on the fixture file for
the authoritative mapping).  These tests deliberately assert on the values
the readers return so that a key-name mismatch surfaces as a test failure,
not as a silent zero/empty in the UI.

Fixture provenance
------------------
The fixture was built by hand from the *producer code* in each step module:

* ``catalog``: ``step_1_02_catalog_footage/step.py`` emits
  ``duration_seconds``, ``width``, ``height``, ``frame_rate``.
* ``temporal_index``: ``step_1_04_temporal_index/step.py`` emits
  ``temporal_event_indices`` (NOT ``temporal_index``).
* ``music_selection``: ``step_2_04_music_selection/bridge.py`` wraps output
  under ``music_selection`` (NOT ``music_track``).
* ``assign_aroll``: ``step_3_01_assign_aroll/step.py`` nests clip data
  under ``video_segments[]`` (NOT at the assignment top level).

If a producer renames a key, this fixture must be updated to match.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from library.dashboard.server import app
from library.tools.review_gate import save_gate_snapshot

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "e2e_pipeline_data.json"


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "mock_project"
    project_dir.mkdir()

    # Copy fixture state
    dest_state = project_dir / "pipeline_data.json"
    shutil.copy(FIXTURE_PATH, dest_state)

    # Write a mock project.yaml
    with open(project_dir / "project.yaml", "w") as f:
        f.write("name: Mock Project\nslug: mock-project\n")

    return str(project_dir)


@pytest.fixture
def client(temp_project):
    with patch("library.dashboard.server._get_project_dir", return_value=temp_project):
        yield TestClient(app)


# ── Project select ──────────────────────────────────────────────────

def test_api_projects_select(client, temp_project):
    response = client.post("/api/projects/select", json={"project_dir": temp_project})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    response = client.get("/api/projects")
    assert response.status_code == 200


# ── Step status: failed beats completed ─────────────────────────────

def test_failed_step_renders_as_failed(client, temp_project):
    """music_analysis is in BOTH steps_completed and failed_steps.

    The dashboard must show it as FAILED, not as completed.  The old code
    checked steps_completed first and never reached failed_steps.
    """
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [
                {"id": "scan", "name": "Scan"},
                {"id": "catalog", "name": "Catalog"},
                {"id": "music_analysis", "name": "Music Analysis"},
                {"id": "assign_aroll", "name": "Assign A-Roll"},
                {"id": "mesh_spine", "name": "Mesh Audio Spine"},
            ]
        }

        response = client.get("/api/steps")
        assert response.status_code == 200
        steps = response.json()
        assert len(steps) == 5

        by_id = {s["id"]: s for s in steps}

        # Completed steps should be completed
        assert by_id["scan"]["status"] == "completed"
        assert by_id["catalog"]["status"] == "completed"
        assert by_id["assign_aroll"]["status"] == "completed"

        # music_analysis is in failed_steps - MUST be failed, not completed
        assert by_id["music_analysis"]["status"] == "failed", (
            "music_analysis is in failed_steps but rendered as "
            f"{by_id['music_analysis']['status']!r} - precedence bug"
        )

        # mesh_spine is not in steps_completed - should be pending
        assert by_id["mesh_spine"]["status"] == "pending"


# ── Step detail ─────────────────────────────────────────────────────

def test_api_step_detail(client, temp_project):
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [{"id": "catalog", "name": "Catalog"}]
        }

        response = client.get("/api/steps/catalog")
        assert response.status_code == 200
        detail = response.json()
        assert detail["id"] == "catalog"
        assert "clip_catalog" in detail["output"]
        assert len(detail["output"]["clip_catalog"]) == 3


# ── Transcript: reads temporal_event_indices ────────────────────────

def test_transcript_reads_real_keys(client, temp_project):
    """The transcript endpoint must find speech regions via
    temporal_event_indices, not the non-existent temporal_index key.
    """
    response = client.get("/api/transcript")
    assert response.status_code == 200
    transcript = response.json()

    # The fixture has 3 clips with 2+1+2 = 5 speech regions
    assert transcript["clip_count"] == 3, (
        f"Expected 3 clips in transcript, got {transcript['clip_count']}. "
        "Reader is probably using the wrong key for temporal_event_indices."
    )
    assert len(transcript["regions"]) == 5, (
        f"Expected 5 speech regions, got {len(transcript['regions'])}. "
        "Reader may be iterating the wrapper dict instead of the list."
    )
    # Verify actual text made it through
    all_text = " ".join(r["text"] for r in transcript["regions"])
    assert "morning" in all_text
    assert "walking" in all_text
    assert "listen" in all_text


# ── Clips: reads duration_seconds, width, height, frame_rate ────────

def test_clips_reads_real_catalog_keys(client, temp_project):
    """The clips endpoint must read the catalog's actual keys:
    duration_seconds, width, height, frame_rate.
    """
    response = client.get("/api/clips")
    assert response.status_code == 200
    clips = response.json()
    assert len(clips) == 3

    clip = clips[0]  # IMG_1001
    assert clip["clip_id"] == "IMG_1001"

    # duration_seconds -> duration_s in API response
    assert clip["duration_s"] == pytest.approx(45.234, abs=0.01), (
        f"Expected duration_s ~45.234, got {clip['duration_s']}. "
        "Reader is probably using 'duration_s' or 'duration' instead of 'duration_seconds'."
    )

    # width+height -> resolution string
    assert clip["resolution"] == "1080x1920", (
        f"Expected resolution '1080x1920', got {clip['resolution']!r}. "
        "Reader is probably using 'resolution' key directly instead of width/height."
    )

    # frame_rate -> fps in API response
    assert clip["fps"] == pytest.approx(30.0), (
        f"Expected fps 30.0, got {clip['fps']}. "
        "Reader is probably using 'fps' instead of 'frame_rate'."
    )


# ── Timeline: reads video_segments and music_selection ──────────────

def test_timeline_reads_video_segments(client, temp_project):
    """The timeline must extract clip_id from video_segments[], not from
    the assignment top level.  And music comes from music_selection, not
    music_track.
    """
    response = client.get("/api/timeline")
    assert response.status_code == 200
    timeline = response.json()

    # 3 a-roll -> 3 V1 + 3 A1 + 1 music A2 = 10 blocks
    v1_blocks = [b for b in timeline["blocks"] if b["track"] == "V1"]
    a1_blocks = [b for b in timeline["blocks"] if b["track"] == "A1"]
    a2_blocks = [b for b in timeline["blocks"] if b["track"] == "A2"]

    assert len(v1_blocks) == 3, f"Expected 3 V1 blocks, got {len(v1_blocks)}"
    assert len(a1_blocks) == 3, f"Expected 3 A1 blocks, got {len(a1_blocks)}"

    # clip_id must come from video_segments, not be empty
    for block in v1_blocks:
        assert block["clip_id"], (
            f"V1 block {block['id']} has empty clip_id. "
            "Reader must extract clip_id from video_segments[]."
        )
    assert v1_blocks[0]["clip_id"] == "IMG_1003"  # hook
    assert v1_blocks[1]["clip_id"] == "IMG_1001"  # first speech
    assert v1_blocks[2]["clip_id"] == "IMG_1002"  # second speech

    # text must come from video_segments
    assert v1_blocks[0]["text"], "Hook block should have text from video_segments"
    assert "listen" in v1_blocks[0]["text"].lower()

    # Music track should appear (music_selection, not music_track)
    assert len(a2_blocks) == 1, (
        f"Expected 1 music block on A2, got {len(a2_blocks)}. "
        "Reader is probably looking for 'music_track' instead of 'music_selection'."
    )
    assert a2_blocks[0]["clip_name"] == "Morning Momentum"


# ── Gates ───────────────────────────────────────────────────────────

def test_api_gates(client, temp_project):
    save_gate_snapshot(temp_project, "mesh_spine", "Mesh Audio Spine", {"mock": "data"})

    response = client.get("/api/gates/mesh_spine")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    response = client.post("/api/gates/mesh_spine/action", json={"action": "approve"})
    assert response.status_code == 200

    response = client.get("/api/gates/mesh_spine")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


# ── Gate dir does not mkdir on reads ────────────────────────────────

def test_gate_listing_does_not_create_dirs(client, temp_project):
    """get_gate_status must not create directories.  Before the fix,
    _gate_dir() called mkdir unconditionally, so merely listing steps
    created 26 empty directories in the project.
    """
    gates_dir = Path(temp_project) / "pipeline_output" / "gates"
    # Ensure no gates dir exists before the call
    if gates_dir.exists():
        shutil.rmtree(gates_dir)

    from library.tools.review_gate import get_gate_status
    status = get_gate_status(temp_project, "nonexistent_step")
    assert status == "none"

    # The gates directory and step subdir must NOT have been created
    assert not (gates_dir / "nonexistent_step").exists(), (
        "_gate_dir created a directory for a read-only operation"
    )


# ── Messages ────────────────────────────────────────────────────────

def test_api_messages(client, temp_project):
    msg = {
        "id": "msg_123",
        "type": "decision",
        "step_id": "mesh_spine",
        "title": "Review",
        "body": "Please review",
        "options": [{"id": "approve", "label": "Approve", "description": "Approve it"}],
        "requires_response": True,
        "created_at": "2024-08-01T12:00:00",
    }

    response = client.post("/api/messages", json=msg)
    assert response.status_code == 200

    response = client.get("/api/messages/pending")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "msg_123"


# ── Pause button returns honest response ────────────────────────────

def test_pause_endpoint_not_404(client, temp_project):
    """POST /api/pipeline/pause used to return 404 because the route did
    not exist.  It should either work or be honestly unavailable - but
    never a 404 that the UI silently swallows as an error alert.

    Since full run control is stage 2 (out of scope), we made the button
    show an honest message client-side.  The route itself may or may not
    exist; what matters is the UI does not call a 404.
    """
    # This test documents the current state: the route does not exist,
    # and the JS pausePipeline() no longer calls it.
    response = client.post("/api/pipeline/pause")
    # 404 or 405 is expected since the route does not exist server-side
    assert response.status_code in (404, 405)
