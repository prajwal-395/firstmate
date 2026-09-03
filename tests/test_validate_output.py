import json
import subprocess
import sys
from pathlib import Path

def test_missing_deterministic_output_fails_the_step():
    """A step reporting success while measuring nothing is the exact defect
    class this repo exists to prevent. If the deterministic half emits
    nothing or malformed JSON, post_bridge must read it as a failure."""
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    
    # Input with NO deterministic_validation
    input_data = {
        "validation_result": {"status": "pass", "summary": "LLM says fine"}
    }
    
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "fail"
    assert v["distribution_ready"] is False
    assert "Deterministic validation failed" in v["summary"]

def test_missing_llm_output_fails_the_step():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det says fine"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "fail"
    assert v["distribution_ready"] is False

def test_both_pass_yields_pass():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det fine"},
        "validation_result": {"status": "pass", "summary": "LLM fine"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "pass"
    assert v["distribution_ready"] is True

def test_undetermined_yields_undetermined_and_not_ready():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det fine"},
        "validation_result": {"status": "undetermined", "summary": "LLM says undetermined"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "undetermined"
    assert v["distribution_ready"] is False
