import json
import threading
import time
from pathlib import Path
import pytest

from library.processes.edit_video.run_pipeline import PostBridgeError, run_hybrid_step

def test_a_post_bridge_refusal_written_to_stdout_reaches_the_retry_message_non_empty(tmp_path):
    """Proves a post-bridge refusal written to stdout reaches the retry message with its content intact."""
    project = tmp_path / "project"
    project.mkdir()
    step = tmp_path / "step_4_04_plan_sfx"
    step.mkdir()
    (step / "handoff.md").write_text("Plan the SFX.\n", encoding="utf-8")
    
    # Write a post_bridge that prints to stdout and exits 1
    # This simulates what step_4_04_plan_sfx/post_bridge.py does
    counter = tmp_path / "post_bridge_calls"
    violation_message = '{"error": "SfxDurationRefused: camera soft click.wav is asked to play for 0.46s and measures 0.459s.", "step": "4.04_bridge"}'
    
    (step / "post_bridge.py").write_text(
        "import json, sys\n"
        f"c = {json.dumps(str(counter))}\n"
        "try:\n"
        "    n = int(open(c).read())\n"
        "except OSError:\n"
        "    n = 0\n"
        "n += 1\n"
        "open(c, 'w').write(str(n))\n"
        "json.load(sys.stdin)\n"
        "if n <= 1:\n"
        f"    print({json.dumps(violation_message)})\n" # print to stdout
        "    sys.exit(1)\n"
        "print(json.dumps({'sfx_spec': {'sfx_list': []}}))\n",
        encoding="utf-8"
    )
    
    req = project / "pipeline_output" / "llm_requests" / "plan_sfx.json"
    res = project / "pipeline_output" / "llm_responses" / "plan_sfx.json"
    seen = []
    
    def run():
        deadline = time.time() + 10
        for _ in range(2):
            while time.time() < deadline:
                if req.exists() and not res.exists():
                    seen.append(json.loads(req.read_text(encoding="utf-8")))
                    res.parent.mkdir(parents=True, exist_ok=True)
                    res.write_text(json.dumps({"sfx_plan": "ok"}), encoding="utf-8")
                    break
                time.sleep(0.05)
    
    threading.Thread(target=run, daemon=True).start()
    
    manifest = {"interface": {"outputs": [{"name": "sfx_plan"}]}}
    result = run_hybrid_step(
        step, {"project_folder": str(project)}, "plan_sfx",
        manifest=manifest, full_auto="agy", llm_timeout=30)
        
    assert result == {'sfx_spec': {'sfx_list': []}}
    assert len(seen) == 2, "Expected two model calls"
    
    first, second = seen[0]["context"], seen[1]["context"]
    assert "SfxDurationRefused" not in first
    assert "SfxDurationRefused" in second
    assert violation_message in second, f"Retry context missing stdout payload:\n{second}"
