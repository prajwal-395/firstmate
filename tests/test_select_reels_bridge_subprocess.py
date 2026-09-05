import subprocess
import sys
import json
import os
from pathlib import Path

def test_select_reels_bridge_subprocess_contract():
    bridge_script = Path("library/steps/step_3_04_select_reels/bridge.py")
    
    transcript = {
        "segments": [
            {"speaker": "Craig", "text": "so tell me about your company", "timeline_start": 0.0, "timeline_end": 5.0, "resolve_item_id": "u", "source_file": "a.MXF", "source_start": 0.0, "source_end": 5.0},
            {"speaker": "Akshita", "text": "we do video editing", "timeline_start": 6.0, "timeline_end": 10.0, "resolve_item_id": "v", "source_file": "a.MXF", "source_start": 6.0, "source_end": 10.0},
            {"speaker": "Craig", "text": "that sounds cool. go to our website", "timeline_start": 11.0, "timeline_end": 15.0, "resolve_item_id": "w", "source_file": "a.MXF", "source_start": 11.0, "source_end": 15.0},
        ],
        "derived_from": {"duration_seconds": 100.0}
    }
    
    input_data = {"timeline_transcript": transcript}
    
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())

    proc = subprocess.run(
        [sys.executable, str(bridge_script)],
        input=json.dumps(input_data).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    
    assert proc.returncode == 0, f"bridge.py failed: {proc.stderr.decode('utf-8')}"
    
    try:
        out = json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise AssertionError(f"bridge.py output invalid JSON: {proc.stdout.decode('utf-8')}") from e
        
    assert "reel_candidates" in out
    assert "turns" in out

