import os
import json
import tempfile
from pathlib import Path
from library.tools.pipeline_logger import PipelineLogger, get_logger, step_timer

def test_pipeline_logger_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = PipelineLogger(tmpdir)
        logger.log(
            step_id="step_1",
            event_type="step_start",
            duration_ms=100.5,
            token_count={"raw": 100, "projected": 50, "toon": 20},
            error="None"
        )
        
        log_file = Path(tmpdir) / "pipeline_output" / "pipeline_log.jsonl"
        assert log_file.exists()
        
        with open(log_file) as f:
            lines = f.readlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["step_id"] == "step_1"
            assert entry["event_type"] == "step_start"
            assert entry["duration_ms"] == 100.5
            assert entry["token_count"]["raw"] == 100

def test_step_timer_decorator():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize the singleton logger
        logger = get_logger(tmpdir)
        
        @step_timer(step_id_kwarg="node_id")
        def dummy_step(node_id):
            return "success"
            
        dummy_step(node_id="step_2")
        
        log_file = Path(tmpdir) / "pipeline_output" / "pipeline_log.jsonl"
        with open(log_file) as f:
            lines = f.readlines()
            # Find step_2 log
            step_2_lines = [json.loads(line) for line in lines if json.loads(line).get("step_id") == "step_2"]
            assert len(step_2_lines) == 1
            assert "duration_ms" in step_2_lines[0]
            assert step_2_lines[0]["event_type"] == "step_end"
