import pytest
from pathlib import Path
import json

from library.processes.edit_video.run_pipeline import generate_output_schema_text, validate_step_output

def test_no_hardcoded_schemas_in_handoff():
    """Verify no handoff.md contains a hardcoded JSON output schema block."""
    steps_dir = Path("library/steps")
    handoff_files = list(steps_dir.rglob("handoff.md"))
    
    assert len(handoff_files) > 0, "Should find at least some handoff.md files"
    
    # Let's verify none of them have Output Format + json block
    for f in handoff_files:
        content = f.read_text()
        # Ensure we removed the previous hardcoded schema block that starts with Output Format and has a json block
        assert "## Output Format\n\n```json\n{" not in content, f"{f} still has hardcoded Output Format schema"
        assert "## Required Output Format\n\n```json\n{" not in content, f"{f} still has hardcoded Required Output Format schema"

def test_generate_output_schema_text():
    """Verify schema injection produces valid prompt content."""
    outputs = [
        {"name": "score", "type": "int", "description": "1 to 10", "required": True},
        {"name": "notes", "type": "str", "required": False},
        {"name": "tags", "type": "list"} # defaults to required: True
    ]
    
    schema_text = generate_output_schema_text(outputs)
    
    assert "## Required Output Format" in schema_text
    assert "```json" in schema_text
    assert '"score": 0' in schema_text
    assert '// 1 to 10 (required)' in schema_text
    assert '"notes": "..."' in schema_text
    assert '// (optional)' in schema_text
    assert '"tags": []' in schema_text

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
