import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from library.dashboard.server import app

client = TestClient(app)

@patch("library.dashboard.server._load_pipeline_state")
@patch("library.tools.project_registry.list_projects")
@patch("library.tools.paths.PROJECTS_ROOT")
def test_get_projects(mock_projects_root, mock_list_projects, mock_load_state):
    mock_list_projects.return_value = []
    mock_projects_root.exists.return_value = False
    
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert isinstance(response.json(), list)



@patch("library.dashboard.server.get_all_gate_statuses")
@patch("library.dashboard.server._load_pipeline_state")
@patch("library.dashboard.server._get_project_dir")
def test_list_steps(mock_get_dir, mock_state, mock_gates):
    mock_get_dir.return_value = "/mock/project"
    mock_state.return_value = {"steps_completed": {"scan": {}}, "step_outputs": {"scan": {"out": "val"}}}
    mock_gates.return_value = {"scan": "approved"}
    
    response = client.get("/api/steps")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(step["id"] == "scan" for step in data)

@patch("library.dashboard.server.get_gate_status")
@patch("library.dashboard.server.load_step_summary")
@patch("library.dashboard.server._load_pipeline_state")
@patch("library.dashboard.server._get_project_dir")
def test_get_step_detail(mock_get_dir, mock_state, mock_summary, mock_gate_status):
    mock_get_dir.return_value = "/mock/project"
    mock_state.return_value = {"step_outputs": {"scan": {"raw_footage_files": []}}, "steps_completed": {"scan": {}}}
    mock_summary.return_value = "Mock summary"
    mock_gate_status.return_value = "approved"
    
    response = client.get("/api/steps/scan")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "scan"
    assert "raw_footage_files" in data["output"]
    assert data["summary_md"] == "Mock summary"

