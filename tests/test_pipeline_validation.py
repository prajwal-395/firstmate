import json
import subprocess
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from library.processes.edit_video.run_pipeline import validate_step_output

def test_bridge_empty_inputs():
    """Test that feeding empty {} as inputs to each bridge raises ValueError."""
    # List of bridge/post_bridge scripts that were modified
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'library', 'steps'))
    scripts = [
        "step_2_02_speech_sequence/bridge.py",
        "step_2_05_mesh_spine/post_bridge.py",
        "step_3_02_select_broll/bridge.py",
        "step_4_02_plan_transitions/post_bridge.py",
        "step_4_03_plan_vfx/post_bridge.py",
        "step_4_04_plan_sfx/post_bridge.py",
    ]
    
    for script_rel in scripts:
        script_path = os.path.join(base_dir, script_rel)
        assert os.path.exists(script_path), f"Script not found: {script_path}"
        
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        env["PYTHONPATH"] = project_root
        
        result = subprocess.run(
            ["python3", script_path],
            input="{}",
            capture_output=True,
            text=True,
            env=env
        )
        
        # Depending on how the exception is handled:
        # Some scripts have a try-except that prints json with "error", others just crash.
        # Check either a non-zero exit code or "error" in stdout.
        if result.returncode == 0:
            try:
                out = json.loads(result.stdout)
                assert "error" in out
                assert "missing required keys" in out["error"] or "missing required input keys" in out["error"]
            except json.JSONDecodeError:
                assert False, f"Script {script_path} exited with 0 but invalid JSON: {result.stdout}"
        else:
            assert "ValueError" in result.stderr
            assert "missing required" in result.stderr


def test_validate_step_output():
    manifest = {
        "interface": {
            "outputs": [
                {"name": "req_key", "required": True, "type": "dict"},
                {"name": "opt_key", "required": False, "type": "list"}
            ]
        }
    }
    
    # Valid output (non-empty required values)
    issues = validate_step_output("test_node", {"req_key": {"data": 1}, "opt_key": []}, manifest)
    assert not issues
    
    # Missing optional key is OK
    issues = validate_step_output("test_node", {"req_key": {"data": 1}}, manifest)
    assert not issues

    # Semantically empty required output
    issues = validate_step_output("test_node", {"req_key": {}, "opt_key": []}, manifest)
    assert any("semantically empty" in issue for issue in issues)
    
    # Missing required key
    issues = validate_step_output("test_node", {"opt_key": []}, manifest)
    assert any("missing required key: 'req_key'" in issue for issue in issues)
        
    # Wrong type
    issues = validate_step_output("test_node", {"req_key": []}, manifest)
    assert any("expected type dict, got list" in issue for issue in issues)
