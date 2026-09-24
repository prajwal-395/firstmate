import pytest
import os
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
# Add the project root to sys.path so we can import from library
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from library.processes.edit_video.run_pipeline import (
    present_llm_step,
    run_hybrid_step,
    LLMError,
    PreBridgeError,
    PostBridgeError
)

@pytest.fixture
def temp_project_dir(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    return str(project_dir)

def test_full_auto_api(temp_project_dir):
    """Test that --full-auto api with a mocked LLMClient doesn't return awaiting_llm markers"""
    inputs = {"project_folder": temp_project_dir, "some_data": 123}
    manifest = {"interface": {"outputs": [{"name": "test_out"}]}}
    
    # Create a fake handoff.md
    prompt_path = Path(temp_project_dir) / "handoff.md"
    prompt_path.write_text("Test prompt")
    
    with patch("library.tools.llm_client.LLMClient.generate") as mock_generate:
        mock_generate.return_value = '```json\n{"test_out": "success"}\n```'
        
        output = present_llm_step(str(prompt_path), inputs, "test_step", manifest, full_auto="api")
        
        assert isinstance(output, dict)
        assert output.get("__status") != "awaiting_llm"
        assert output.get("test_out") == "success"
        mock_generate.assert_called_once()

def test_hybrid_step_full_auto(temp_project_dir):
    """Test that hybrid steps with --full-auto run full pre-bridge -> LLM -> post-bridge"""
    step_dir = Path(temp_project_dir) / "my_step"
    step_dir.mkdir()
    
    # Create required files
    (step_dir / "bridge.py").touch()
    (step_dir / "post_bridge.py").touch()
    (step_dir / "handoff.md").write_text("Hybrid prompt")
    
    inputs = {"project_folder": temp_project_dir}
    
    def mock_run_subprocess(script_path, in_data):
        if "post_bridge.py" in str(script_path):
            return {"final_out": True, "llm_val": in_data.get("llm_val")}
        elif "bridge.py" in str(script_path):
            return {"pre_bridge_out": True}
        return {}
        
    with patch("library.processes.edit_video.run_pipeline.run_subprocess", side_effect=mock_run_subprocess):
        with patch("library.processes.edit_video.run_pipeline.present_llm_step") as mock_present:
            mock_present.return_value = {"llm_val": "creative"}
            
            output = run_hybrid_step(step_dir, inputs, "my_step", full_auto="api", llm_timeout=10)
            
            assert mock_present.called
            assert output.get("final_out") is True
            assert output.get("llm_val") == "creative"
