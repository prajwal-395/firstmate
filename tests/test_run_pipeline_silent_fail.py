import pytest
import os
import json
from pathlib import Path
from library.processes.edit_video.run_pipeline import present_llm_step

def test_present_llm_step_raises_on_silent_no_answer(tmp_path):
    # Setup a mock step directory and manifest
    node_id = "step_6_01_render"
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Hello LLM", encoding="utf-8")
    
    # We need a manifest that EXPECTS an LLM output, otherwise it skips the call.
    # The step we removed render_review from no longer expects one,
    # so we mock a manifest that STILL expects one to prove the mechanism raises.
    manifest = {
        "interface": {
            "outputs": [
                {"name": "some_llm_output", "type": "object", "required": False}
            ]
        }
    }
    
    inputs = {"project_folder": str(tmp_path)}
    
    # Run with a backend that doesn't exist or without a backend so it falls through to qa_loop.
    # It will fail QA (because parsed_result=None) and best_output will be None, which evaluates to {}.
    # With our fix, it should raise RuntimeError instead of returning {}.
    with pytest.raises(RuntimeError) as exc_info:
        present_llm_step(
            prompt_path=str(prompt_path),
            inputs=inputs,
            node_id=node_id,
            manifest=manifest,
            full_auto=None,
            llm_timeout=1
        )
    
    assert "produced no LLM output" in str(exc_info.value)
