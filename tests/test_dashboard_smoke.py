#!/usr/bin/env python3
"""
Smoke test for the review dashboard.

Creates an inline project directory whose key names match the REAL
pipeline producers, then verifies every key dashboard endpoint returns
real data (not zero/empty).

Fixture provenance: keys are taken from the producer code, not invented
to satisfy the reader.  See ``tests/test_e2e_dashboard.py`` docstring
for the authoritative mapping of producer -> key name.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def create_mock_project(base_dir: str) -> str:
    """Create a mock project directory with sample pipeline data.

    Key names match the real producers:
    - catalog: duration_seconds, width, height, frame_rate
    - temporal_index: temporal_event_indices (NOT temporal_index)
    - music_selection: nested under music_selection (NOT music_track)
    - assign_aroll: clip data inside video_segments[]
    """
    project_dir = os.path.join(base_dir, "test-project")
    os.makedirs(os.path.join(project_dir, "raw"), exist_ok=True)
    os.makedirs(os.path.join(project_dir, "pipeline_output"), exist_ok=True)

    # Create project.yaml
    try:
        import yaml
        config = {
            "name": "Test Project",
            "slug": "test-project",
            "client": "test-client",
            "status": "in_progress",
            "source": {"type": "iphone_mov", "resolution": "1080x1920", "fps": 30},
            "pipeline": {},
            "resolve": {"project_name": "Test Project", "timeline_name": "Main Edit"},
        }
        with open(os.path.join(project_dir, "project.yaml"), "w") as f:
            yaml.dump(config, f)
    except ImportError:
        pass

    # Create pipeline_data.json with REAL key names
    pipeline_data = {
        "pipeline_version": "1.0",
        "started_at": "2026-08-08T12:00:00",
        "project_folder": project_dir,
        "steps_completed": {
            "scan": {"completed_at": "2026-08-08T12:00:05", "elapsed_s": 1.2},
            "catalog": {"completed_at": "2026-08-08T12:00:10", "elapsed_s": 5.3},
            "semantic_analysis": {"completed_at": "2026-08-08T12:05:00", "elapsed_s": 290.0},
            "temporal_index": {"completed_at": "2026-08-08T12:07:00", "elapsed_s": 120.0},
            "prosody_analysis": {"completed_at": "2026-08-08T12:08:00", "elapsed_s": 45.0},
            "creative_direction": {"completed_at": "2026-08-08T12:08:30", "elapsed_s": 15.0},
            "music_analysis": {"completed_at": "2026-08-08T12:09:00", "elapsed_s": 30.0},
        },
        "failed_steps": ["music_analysis"],
        "step_outputs": {
            "scan": {
                "raw_footage_files": [
                    f"{project_dir}/raw/IMG_1001.MOV",
                    f"{project_dir}/raw/IMG_1002.MOV",
                    f"{project_dir}/raw/IMG_1003.MOV",
                ]
            },
            # Catalog: the producer emits duration_seconds, width, height,
            # frame_rate - NOT duration_s, resolution, fps.
            "catalog": {
                "clip_catalog": [
                    {
                        "clip_id": "IMG_1001", "filename": "IMG_1001.MOV",
                        "path": f"{project_dir}/raw/IMG_1001.MOV",
                        "duration_seconds": 45.234, "width": 1080, "height": 1920,
                        "frame_rate": 30.0, "video_codec": "hevc",
                        "audio_codec": "aac", "has_audio": True,
                    },
                    {
                        "clip_id": "IMG_1002", "filename": "IMG_1002.MOV",
                        "path": f"{project_dir}/raw/IMG_1002.MOV",
                        "duration_seconds": 32.100, "width": 1080, "height": 1920,
                        "frame_rate": 30.0, "video_codec": "hevc",
                        "audio_codec": "aac", "has_audio": True,
                    },
                    {
                        "clip_id": "IMG_1003", "filename": "IMG_1003.MOV",
                        "path": f"{project_dir}/raw/IMG_1003.MOV",
                        "duration_seconds": 28.500, "width": 1080, "height": 1920,
                        "frame_rate": 30.0, "video_codec": "hevc",
                        "audio_codec": None, "has_audio": False,
                    },
                ]
            },
            "semantic_analysis": {
                "semantic_analysis_documents": [
                    {
                        "clip_id": "IMG_1001",
                        "scene": [{"description": "Kitchen workout"}],
                        "assessment": {"summary": "Person doing morning workout in kitchen", "interest_score": 0.85},
                        "objects": [{"label": "person"}, {"label": "kitchen"}],
                    },
                    {
                        "clip_id": "IMG_1002",
                        "scene": [{"description": "Park walk"}],
                        "assessment": {"summary": "Walk through park, reflective monologue", "interest_score": 0.72},
                        "objects": [{"label": "person"}, {"label": "trees"}],
                    },
                    {
                        "clip_id": "IMG_1003",
                        "scene": [{"description": "Desk speech"}],
                        "assessment": {"summary": "Direct-to-camera about commitment", "interest_score": 0.91},
                        "objects": [{"label": "person"}, {"label": "desk"}],
                    },
                ]
            },
            # temporal_index: the producer emits temporal_event_indices,
            # NOT temporal_index.
            "temporal_index": {
                "temporal_event_indices": [
                    {
                        "clip_id": "IMG_1001",
                        "speech_regions": [
                            {
                                "start": 2.5, "end": 12.3,
                                "text": "so this morning I decided to really commit to this new routine",
                                "words": [{"word": "so", "start": 2.5, "end": 2.7}],
                                "speaker": "SPEAKER_00",
                            },
                            {
                                "start": 15.0, "end": 28.1,
                                "text": "and I've been thinking about how consistency is the key to everything",
                                "words": [{"word": "and", "start": 15.0, "end": 15.2}],
                                "speaker": "SPEAKER_00",
                            },
                        ],
                    },
                    {
                        "clip_id": "IMG_1002",
                        "speech_regions": [
                            {
                                "start": 1.0, "end": 18.5,
                                "text": "walking through the park and just reflecting on how far we've come",
                                "words": [{"word": "walking", "start": 1.0, "end": 1.4}],
                                "speaker": "SPEAKER_00",
                            },
                        ],
                    },
                    {
                        "clip_id": "IMG_1003",
                        "speech_regions": [
                            {
                                "start": 0.5, "end": 15.0,
                                "text": "listen if you're watching this right now I want you to know that this is your sign",
                                "words": [{"word": "listen", "start": 0.5, "end": 0.9}],
                                "speaker": "SPEAKER_00",
                            },
                            {
                                "start": 16.0, "end": 25.0,
                                "text": "every single day you show up is a vote for the person you want to become",
                                "words": [{"word": "every", "start": 16.0, "end": 16.3}],
                                "speaker": "SPEAKER_00",
                            },
                        ],
                    },
                ],
                "total_indexed": 3,
                "total_failed": 0,
            },
            "prosody_analysis": {
                "prosody_analysis": [
                    {"clip_id": "IMG_1001", "average_energy": 0.7, "speech_rate": 3.2},
                    {"clip_id": "IMG_1002", "average_energy": 0.45, "speech_rate": 2.8},
                    {"clip_id": "IMG_1003", "average_energy": 0.85, "speech_rate": 3.5},
                ]
            },
            "creative_direction": {
                "creative_direction": {
                    "narrative_theme": "A personal journey of commitment to daily routines",
                    "target_mood": "motivational and aspirational",
                    "target_energy": "building",
                    "energy_arc": "Start reflective, build through examples, climax with call-to-action",
                    "emotional_landscape": "Begins with quiet determination, peaks with empowering declaration",
                    "audience_emotion": "Inspired and motivated",
                    "key_moments": [
                        "The morning routine commitment declaration",
                        "The direct-to-camera call to action",
                    ],
                    "rationale": "Strongest thread is the consistency/commitment narrative",
                }
            },
            "music_analysis": {
                "available": False,
                "error": "reported success but produced no usable output: ModuleNotFoundError: No module named 'numpy'",
            },
        },
    }

    with open(os.path.join(project_dir, "pipeline_data.json"), "w") as f:
        json.dump(pipeline_data, f, indent=2)

    return project_dir


def run_smoke_test():
    """Run smoke tests against the dashboard API."""
    from fastapi.testclient import TestClient
    from library.dashboard import server

    # Create mock project
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_mock_project(tmp)
        server._project_dir = project_dir
        server._project_slug = "test-project"

        client = TestClient(server.app)

        results = []

        # Test root page
        resp = client.get("/")
        results.append(("GET /", resp.status_code))
        assert resp.status_code == 200, f"Root page failed: {resp.status_code}"

        # Test project endpoint
        resp = client.get("/api/project")
        results.append(("GET /api/project", resp.status_code))
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Project" or data["slug"] == "test-project"

        # Test steps endpoint
        resp = client.get("/api/steps")
        results.append(("GET /api/steps", resp.status_code))
        assert resp.status_code == 200
        steps = resp.json()
        assert len(steps) > 0
        # 6 steps completed, but music_analysis is in failed_steps
        completed = [s for s in steps if s["status"] == "completed"]
        failed = [s for s in steps if s["status"] == "failed"]
        assert len(completed) >= 5, f"Expected >= 5 completed steps, got {len(completed)}"
        # music_analysis must show as failed
        music_steps = [s for s in steps if s["id"] == "music_analysis"]
        if music_steps:
            assert music_steps[0]["status"] == "failed", (
                f"music_analysis in failed_steps but shows as {music_steps[0]['status']!r}"
            )

        # Test step detail
        resp = client.get("/api/steps/creative_direction")
        results.append(("GET /api/steps/creative_direction", resp.status_code))
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == "creative_direction"
        assert detail["summary_md"]  # Should have a generated summary

        # Test transcript - must find 5 regions across 3 clips
        resp = client.get("/api/transcript")
        results.append(("GET /api/transcript", resp.status_code))
        assert resp.status_code == 200
        transcript = resp.json()
        assert transcript["clip_count"] == 3, (
            f"Transcript shows {transcript['clip_count']} clips, expected 3. "
            "The reader is probably using the wrong key for temporal_event_indices."
        )
        assert len(transcript["regions"]) == 5, (
            f"Transcript shows {len(transcript['regions'])} regions, expected 5. "
            "The reader may be iterating wrapper dict values instead of the list."
        )

        # Test clips - must show real durations, not zeros
        resp = client.get("/api/clips")
        results.append(("GET /api/clips", resp.status_code))
        assert resp.status_code == 200
        clips = resp.json()
        assert len(clips) == 3
        # Verify actual values, not zeros
        c0 = clips[0]
        assert c0["duration_s"] > 0, f"Clip duration_s is {c0['duration_s']}, expected >0"
        assert c0["resolution"] != "", f"Clip resolution is empty"
        assert c0["fps"] > 0, f"Clip fps is {c0['fps']}, expected >0"

        # Test timeline (should be empty since no A/B-roll in smoke data)
        resp = client.get("/api/timeline")
        results.append(("GET /api/timeline", resp.status_code))
        assert resp.status_code == 200

        # Test gates
        resp = client.get("/api/gates")
        results.append(("GET /api/gates", resp.status_code))
        assert resp.status_code == 200

        # Test pipeline status
        resp = client.get("/api/pipeline/status")
        results.append(("GET /api/pipeline/status", resp.status_code))
        assert resp.status_code == 200
        status = resp.json()
        assert len(status["completed_steps"]) >= 5
        assert "music_analysis" in status["failed_steps"]

        # Test annotation creation
        resp = client.post("/api/steps/creative_direction/annotations", json={
            "annotations": [{
                "annotation_type": "comment",
                "content": "I want a more energetic opening",
            }]
        })
        results.append(("POST annotation", resp.status_code))
        assert resp.status_code == 200

        # Test annotation retrieval
        resp = client.get("/api/steps/creative_direction/annotations")
        results.append(("GET annotations", resp.status_code))
        assert resp.status_code == 200
        anns = resp.json()
        assert len(anns) == 1

        # Test gate action
        from library.tools.review_gate import save_gate_snapshot

        initial_output = {
            "creative_direction": {
                "target_mood": "motivational",
                "target_energy": "building"
            }
        }

        save_gate_snapshot(
            project_dir, "creative_direction", "Creative Direction",
            initial_output
        )
        resp = client.post("/api/gates/creative_direction/action", json={
            "action": "approve",
            "feedback": "Looks good, proceed",
        })
        results.append(("POST gate action", resp.status_code))
        assert resp.status_code == 200

        # Test gate action (reject)
        resp = client.post("/api/gates/creative_direction/action", json={
            "action": "reject",
            "feedback": "No, fix this",
        })
        results.append(("POST gate action (reject)", resp.status_code))
        assert resp.status_code == 200

        # Reject saves gate feedback, not pipeline_data.json step output.
        # Check the gate status reflects the rejection.
        resp_status = client.get("/api/gates/creative_direction")
        assert resp_status.json()["status"] == "rejected"

        # Test gate action (revise with deep merge)
        resp = client.post("/api/gates/creative_direction/action", json={
            "action": "revise",
            "feedback": "Make it more energetic",
            "revisions": {
                "creative_direction": {
                    "target_mood": "very energetic"
                }
            }
        })
        results.append(("POST gate action (revise)", resp.status_code))
        assert resp.status_code == 200

        state = server._load_pipeline_state(project_dir)
        cd = state["step_outputs"]["creative_direction"]["creative_direction"]
        assert cd["target_mood"] == "very energetic"
        assert cd["target_energy"] == "building"  # Unchanged

        # Test pipeline run and resume endpoints
        from unittest.mock import patch

        with patch("subprocess.Popen") as mock_popen:
            resp = client.post("/api/pipeline/run", json={"review_mode": True})
            results.append(("POST /api/pipeline/run", resp.status_code))
            assert resp.status_code == 200

            resp = client.post("/api/pipeline/resume")
            results.append(("POST /api/pipeline/resume", resp.status_code))
            assert resp.status_code == 200

        # Print results
        print("\n  Dashboard Smoke Test Results")
        print("  " + "-" * 45)
        for endpoint, status_code in results:
            icon = "PASS" if status_code == 200 else "FAIL"
            print(f"  [{icon}] {endpoint}: {status_code}")
        print(f"\n  All {len(results)} tests passed!\n")


if __name__ == "__main__":
    run_smoke_test()
