import os
import json
import pytest
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch

from library.dashboard.server import app, _project_dir
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
    # Setup test project dir in the app
    with patch("library.dashboard.server._get_project_dir", return_value=temp_project):
        yield TestClient(app)

def test_api_projects(client, temp_project):
    # Depending on how PROJECTS_ROOT is configured, we may need to mock it.
    # For now, let's just test POST /api/projects/select to set the active project
    response = client.post("/api/projects/select", json={"project_dir": temp_project})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    # get_projects requires PROJECTS_ROOT which we shouldn't depend on,
    # but let's see if the endpoint works without throwing 500
    response = client.get("/api/projects")
    assert response.status_code == 200

def test_api_steps(client, temp_project):
    # Mock dag load so it matches our steps
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [
                {"id": "scan", "name": "Scan"},
                {"id": "catalog", "name": "Catalog"},
                {"id": "mesh_spine", "name": "Mesh Audio Spine"}
            ]
        }
        
        response = client.get("/api/steps")
        assert response.status_code == 200
        steps = response.json()
        assert len(steps) == 3
        # scan and catalog are in our fixture's steps_completed
        assert steps[0]["status"] == "completed"
        assert steps[1]["status"] == "completed"
        assert steps[2]["status"] == "pending"

def test_api_step_detail(client, temp_project):
    with patch("library.dashboard.server._load_dag") as mock_dag:
        mock_dag.return_value = {
            "nodes": [
                {"id": "catalog", "name": "Catalog"}
            ]
        }
        
        response = client.get("/api/steps/catalog")
        assert response.status_code == 200
        detail = response.json()
        assert detail["id"] == "catalog"
        assert "clip_catalog" in detail["output"]
        assert len(detail["output"]["clip_catalog"]) == 3

def test_api_gates(client, temp_project):
    # Create a mock gate for mesh_spine
    save_gate_snapshot(temp_project, "mesh_spine", "Mesh Audio Spine", {"mock": "data"})
    
    # Check it's pending
    response = client.get("/api/gates/mesh_spine")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    
    # Approve it
    response = client.post("/api/gates/mesh_spine/action", json={"action": "approve"})
    assert response.status_code == 200
    
    # Verify approved
    response = client.get("/api/gates/mesh_spine")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

def test_api_messages(client, temp_project):
    msg = {
        "id": "msg_123",
        "type": "decision",
        "step_id": "mesh_spine",
        "title": "Review",
        "body": "Please review",
        "options": [{"id": "approve", "label": "Approve", "description": "Approve it"}],
        "requires_response": True,
        "created_at": "2024-08-01T12:00:00"
    }
    
    response = client.post("/api/messages", json=msg)
    assert response.status_code == 200
    
    # Poll for message (since it's not responded, it should timeout if we set low timeout, 
    # but we can just check GET /api/messages/pending)
    response = client.get("/api/messages/pending")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "msg_123"
