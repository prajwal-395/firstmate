import json
import subprocess
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from library.tools.pipeline_validation import require_keys, require_type
from library.processes.edit_video.run_pipeline import validate_step_output

def test_require_keys():
    data = {"a": 1, "b": 2}
    # Should not raise
    require_keys(data, ["a"])
    require_keys(data, ["a", "b"])
    
    # Should raise
    with pytest.raises(ValueError, match="missing required input keys: \\['c'\\]"):
        require_keys(data, ["a", "c"])

def test_require_type():
    require_type(1, int, "my_int")
    
    with pytest.raises(TypeError, match="key 'my_list' expected list, got int"):
        require_type(1, list, "my_list")

def test_bridge_empty_inputs():
    """Test that feeding empty {} as inputs to each bridge raises ValueError."""
    # List of bridge/post_bridge scripts that were modified
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'library', 'steps'))
    scripts = [
        "step_2_02_speech_sequence/bridge.py",
        "step_2_05_mesh_spine/bridge.py",
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
    
    # Valid output
    validate_step_output("test_node", {"req_key": {}, "opt_key": []}, manifest)
    
    # Missing optional key is OK
    validate_step_output("test_node", {"req_key": {}}, manifest)
    
    # Missing required key
    with pytest.raises(RuntimeError, match="missing required key: 'req_key'"):
        validate_step_output("test_node", {"opt_key": []}, manifest)
        
    # Wrong type
    with pytest.raises(RuntimeError, match="expected type dict, got list"):
        validate_step_output("test_node", {"req_key": []}, manifest)
