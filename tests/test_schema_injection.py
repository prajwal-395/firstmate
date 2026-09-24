import pytest
from pathlib import Path
import json

from library.processes.edit_video.run_pipeline import generate_output_schema_text, validate_step_output

def test_validate_step_output_warnings():
    """Verify validate_step_output produces warnings without raising errors."""
    manifest = {
        "interface": {
            "outputs": [
                {"name": "score", "type": "int", "required": True},
                {"name": "notes", "type": "str", "required": False}
            ]
        }
    }
    
    # 1. Valid output
    valid_output = {"score": 8, "notes": "good"}
    issues = validate_step_output("test_node", valid_output, manifest)
    assert not issues, "Valid output should have no issues"
    
    # 2. Missing required
    missing_req = {"notes": "good"}
    issues = validate_step_output("test_node", missing_req, manifest)
    assert any("missing required key: 'score'" in i for i in issues), issues
    
    # 3. Missing optional
    missing_opt = {"score": 8}
    issues = validate_step_output("test_node", missing_opt, manifest)
    assert not issues, "Missing optional should not produce an issue"
    
    # 4. Wrong type
    wrong_type = {"score": "eight", "notes": "good"}
    issues = validate_step_output("test_node", wrong_type, manifest)
    assert any("expected type int, got str" in i for i in issues), issues
    
    # 5. Extra fields
    extra = {"score": 8, "extra_field": True}
    issues = validate_step_output("test_node", extra, manifest)
    assert any("unexpected extra fields: extra_field" in i for i in issues), issues
