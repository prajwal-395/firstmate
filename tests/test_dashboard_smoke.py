#!/usr/bin/env python3
"""
Smoke test for the review dashboard.

Creates a mock project directory with sample pipeline data and verifies
that the dashboard server starts and all key endpoints respond correctly.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


def create_mock_project(base_dir: str) -> str:
    """Create a mock project directory with sample pipeline data."""
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
            "pipeline": {"brand_template": "default_brand"},
            "resolve": {"project_name": "Test Project", "timeline_name": "Main Edit"},
        }
        with open(os.path.join(project_dir, "project.yaml"), "w") as f:
            yaml.dump(config, f)
    except ImportError:
        pass

    # Create pipeline_data.json with sample step outputs
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
        },
        "step_outputs": {
            "scan": {
                "raw_footage_files": [
                    f"{project_dir}/raw/IMG_1001.MOV",
                    f"{project_dir}/raw/IMG_1002.MOV",
                    f"{project_dir}/raw/IMG_1003.MOV",
                ]
            },
            "catalog": {
                "clip_catalog": [
                    {"clip_id": "IMG_1001", "filename": "IMG_1001.MOV", "duration_s": 45.2, "resolution": "1080x1920", "fps": 30},
                    {"clip_id": "IMG_1002", "filename": "IMG_1002.MOV", "duration_s": 32.1, "resolution": "1080x1920", "fps": 30},
                    {"clip_id": "IMG_1003", "filename": "IMG_1003.MOV", "duration_s": 28.5, "resolution": "1080x1920", "fps": 30},
                ]
            },
            "semantic_analysis": {
                "semantic_analysis_documents": [
                    {
                        "clip_id": "IMG_1001",
                        "mood": "energetic",
                        "energy": "high",
                        "interest_score": 0.85,
                        "summary": "Person doing morning workout routine in a bright kitchen, high energy and enthusiasm",
                        "detected_objects": ["person", "kitchen", "water bottle"],
                    },
                    {
                        "clip_id": "IMG_1002",
                        "mood": "reflective",
                        "energy": "medium",
                        "interest_score": 0.72,
                        "summary": "Outdoor walk through park, reflective monologue about daily habits",
                        "detected_objects": ["person", "trees", "path"],
                    },
                    {
                        "clip_id": "IMG_1003",
                        "mood": "motivational",
                        "energy": "high",
                        "interest_score": 0.91,
                        "summary": "Direct-to-camera speech about commitment and consistency, strong delivery",
                        "detected_objects": ["person", "desk", "laptop"],
                    },
                ]
            },
            "temporal_index": {
                "temporal_index": [
                    {
                        "clip_id": "IMG_1001",
                        "speech_regions": [
                            {
                                "start": 2.5,
                                "end": 12.3,
                                "text": "so this morning I decided to really commit to this new routine",
                                "words": [{"word": "so", "start": 2.5, "end": 2.7}],
                            },
                            {
                                "start": 15.0,
                                "end": 28.1,
                                "text": "and I've been thinking about how consistency is the key to everything",
                                "words": [{"word": "and", "start": 15.0, "end": 15.2}],
                            },
                        ],
                    },
                    {
                        "clip_id": "IMG_1002",
                        "speech_regions": [
                            {
                                "start": 1.0,
                                "end": 18.5,
                                "text": "walking through the park and just reflecting on how far we've come",
                                "words": [{"word": "walking", "start": 1.0, "end": 1.4}],
                            },
                        ],
                    },
                    {
                        "clip_id": "IMG_1003",
                        "speech_regions": [
                            {
                                "start": 0.5,
                                "end": 15.0,
                                "text": "listen if you're watching this right now I want you to know that this is your sign",
                                "words": [{"word": "listen", "start": 0.5, "end": 0.9}],
                            },
                            {
                                "start": 16.0,
                                "end": 25.0,
                                "text": "every single day you show up is a vote for the person you want to become",
                                "words": [{"word": "every", "start": 16.0, "end": 16.3}],
                            },
                        ],
                    },
                ]
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
                    "narrative_theme": "A personal journey of commitment to daily routines and the transformative power of consistency",
                    "target_mood": "motivational and aspirational",
                    "target_energy": "building",
                    "energy_arc": "Start reflective, build through personal examples, climax with direct call-to-action",
                    "emotional_landscape": "Begins with quiet determination, builds through shared vulnerability, peaks with empowering declaration",
                    "audience_emotion": "Inspired and motivated to start their own journey",
                    "key_moments": [
                        "The morning routine commitment declaration",
                        "The direct-to-camera call to action about showing up every day",
                    ],
                    "rationale": "Strongest thread is the consistency/commitment narrative, supported by both reflective and high-energy footage",
                }
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
        completed = [s for s in steps if s["status"] == "completed"]
        assert len(completed) >= 6, f"Expected >= 6 completed steps, got {len(completed)}"

        # Test step detail
        resp = client.get("/api/steps/creative_direction")
        results.append(("GET /api/steps/creative_direction", resp.status_code))
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == "creative_direction"
        assert detail["summary_md"]  # Should have a generated summary

        # Test transcript
        resp = client.get("/api/transcript")
        results.append(("GET /api/transcript", resp.status_code))
        assert resp.status_code == 200
        transcript = resp.json()
        assert transcript["clip_count"] == 3
        assert len(transcript["regions"]) == 5

        # Test clips
        resp = client.get("/api/clips")
        results.append(("GET /api/clips", resp.status_code))
        assert resp.status_code == 200
        clips = resp.json()
        assert len(clips) == 3

        # Test timeline (should be empty since no A/B-roll yet)
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
        assert len(status["completed_steps"]) >= 6

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

        # Test gate action (save gate first)
        from library.tools.review_gate import save_gate_snapshot
        
        # We need realistic output to test deep merge
        initial_output = data.get("step_outputs", {}).get("creative_direction", {}) if "step_outputs" in data else {
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
        
        state = server._load_pipeline_state(project_dir)
        assert state["step_outputs"]["creative_direction"].get("__rejected") is True
        
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
        import subprocess
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
