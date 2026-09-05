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


# ── Timeline: the captions the captain's own notes are about ────────

def test_timeline_draws_the_caption_plan_on_v3(client, temp_project):
    """V3 carries one block per caption CARD, from `plan_subtitles`.

    The captain's real note off 001's timeline was "why are the
    subtitles so big?", and until this track existed the dashboard drew
    a timeline with no captions on it at all - so the one thing they had
    typed a note about was the one thing they could not point at.

    The unit is the PLAN's entry, not step 4.05's rendered `.mov`: on
    001 the same captions are 30 plan entries and 8 rendered segments,
    and 30 is what a reviewer is looking at.
    """
    timeline = client.get("/api/timeline").json()
    v3 = [b for b in timeline["blocks"] if b["track"] == "V3"]

    assert len(v3) == 6, (
        f"Expected 6 V3 caption blocks, got {len(v3)}. Reader should be "
        "reading plan_subtitles.subtitle_plan.subtitle_entries[]."
    )
    assert [b["id"] for b in v3] == [
        "sub_001", "sub_002", "sub_003", "sub_004", "sub_005", "sub_006"]
    assert all(b["block_type"] == "subtitle" for b in v3)

    # Times come from the entry's own timeline_start/timeline_end - the
    # entry is already in the TIMELINE domain, so nothing is converted.
    assert v3[0]["start_s"] == 0.0 and v3[0]["end_s"] == 1.6
    assert v3[0]["duration_s"] == pytest.approx(1.6)
    assert v3[-1]["end_s"] == 30.8

    # The caption text reaches the view; it is what the block is FOR.
    assert v3[0]["text"] == "listen if you're"
    assert v3[3]["text"] == "to really commit to this new routine"

    # A caption is not cut from a clip, so it names none. Putting the
    # caption text in clip_id would make it read as a filename.
    assert all(b["clip_id"] == "" for b in v3)


def test_track_count_counts_the_tracks_that_carry_something(client):
    """`track_count` was `2 if any V2 else 1`, which is not a count.

    It reported 1 for this fixture's V1 + V3 + A1 + A2. Nothing read the
    field, which is how it stayed wrong; adding a track would have made
    a wrong number wronger.
    """
    timeline = client.get("/api/timeline").json()
    tracks = {b["track"] for b in timeline["blocks"]}
    assert tracks == {"V1", "V3", "A1", "A2"}, sorted(tracks)
    assert timeline["track_count"] == 4


def test_the_ruler_covers_a_block_that_outlives_the_a_roll(tmp_path):
    """`total_duration_s` is measured over every block, not over A-roll.

    `timeline-view.js` positions a block at `start_s / total_duration_s`
    and draws the A2 music block to `total_duration_s` exactly, so a
    short total pushes picture off the end of the ruler AND cuts the bed
    short underneath it.

    This is not hypothetical. On 001, A-roll ends at 52.605s and the
    last B-roll cutaway runs 52.605 -> 56.605, so four seconds of
    picture were drawn off the ruler and the bed under them was four
    seconds short. The fixture above cannot show it - its A-roll and its
    captions end at the same second - so the disagreement is built here
    explicitly, because the view may not assume its producers agree
    about where the end is.
    """
    project_dir = tmp_path / "long_broll"
    project_dir.mkdir()
    state = json.loads(FIXTURE_PATH.read_text())
    # A-roll ends at 30.8; this cutaway runs to 34.0.
    state["step_outputs"]["select_broll"] = {
        "b_roll_assignments": [
            {"entry_id": "broll_tail", "clip_id": "IMG_1001",
             "timeline_start": 28.0, "duration": 6.0},
        ]
    }
    (project_dir / "pipeline_data.json").write_text(json.dumps(state))
    (project_dir / "project.yaml").write_text("name: Long\nslug: long\n")

    with patch("library.dashboard.server._get_project_dir",
               return_value=str(project_dir)):
        timeline = TestClient(app).get("/api/timeline").json()

    total = timeline["total_duration_s"]
    assert total == pytest.approx(34.0), (
        f"total_duration_s is {total}; the B-roll runs to 34.0s and "
        "would be drawn off the end of the ruler."
    )
    for block in timeline["blocks"]:
        assert block["end_s"] <= total + 1e-6, (
            f"{block['track']} block {block['id']} ends at "
            f"{block['end_s']}s, past total_duration_s {total}s."
        )
    # The bed is drawn under the whole picture, not under V1 alone.
    a2 = [b for b in timeline["blocks"] if b["track"] == "A2"]
    assert a2[0]["end_s"] == pytest.approx(34.0)


def test_timeline_survives_a_project_with_no_subtitle_plan(tmp_path):
    """No plan means no V3, not a 500 and not an empty track.

    A project that has not reached step 4.01 is the ordinary case, and
    the endpoint is the first thing the dashboard calls.
    """
    project_dir = tmp_path / "no_subs"
    project_dir.mkdir()
    state = json.loads(FIXTURE_PATH.read_text())
    del state["step_outputs"]["plan_subtitles"]
    (project_dir / "pipeline_data.json").write_text(json.dumps(state))
    (project_dir / "project.yaml").write_text("name: No Subs\nslug: no-subs\n")

    with patch("library.dashboard.server._get_project_dir",
               return_value=str(project_dir)):
        timeline = TestClient(app).get("/api/timeline").json()

    assert [b for b in timeline["blocks"] if b["track"] == "V3"] == []
    assert timeline["blocks"], "the rest of the timeline still draws"


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


# ── The handbrake is a real route now ───────────────────────────────

def test_pause_engages_the_handbrake(client, temp_project):
    """POST /api/pipeline/pause used to 404: the route did not exist and
    the button faked an alert.  Stage 2 (run control) made it real.

    What it must do is engage the handbrake - write the hold file the
    runner reads between steps - and say plainly what that will achieve,
    including when nothing is running.
    """
    from library.tools import run_control

    response = client.post("/api/pipeline/pause")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "hold_requested"
    assert run_control.hold_path(temp_project).exists()
    assert run_control.hold_requested(temp_project) is not None
    # Nothing is running in this fixture, and the response says so rather
    # than implying it stopped something.
    assert body["was_running"] is False
    assert "armed" in body["effect"]

    # And it can be taken off again without launching anything.
    response = client.delete("/api/pipeline/pause")
    assert response.status_code == 200
    assert response.json()["released"] is True
    assert not run_control.hold_path(temp_project).exists()
