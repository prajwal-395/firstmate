import os
import json
import time
import sys
from pathlib import Path
from functools import wraps

class PipelineLogger:
    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.log_file = self.project_dir / "pipeline_output" / "pipeline_log.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
    def log(self, step_id: str, event_type: str, duration_ms: float = None, token_count: dict = None, error: str = None, gate_decision: str = None, backend: str = None, latency: float = None):
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "step_id": step_id,
            "event_type": event_type,
        }
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if token_count is not None:
            entry["token_count"] = token_count
        if error is not None:
            entry["error"] = error
        if gate_decision is not None:
            entry["gate_decision"] = gate_decision
        if backend is not None:
            entry["backend"] = backend
        if latency is not None:
            entry["latency"] = latency
            
        json_line = json.dumps(entry)
        
        # Write to stderr (human-readable)
        print(f"[LOG] {event_type} {step_id}" + (f" | {duration_ms}ms" if duration_ms else "") + (f" | Error: {error}" if error else ""), file=sys.stderr)
        
        # Write to file
        try:
            with open(self.log_file, "a") as f:
                f.write(json_line + "\n")
        except Exception as e:
            print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)

_logger_instance = None

def get_logger(project_dir: str = None) -> PipelineLogger:
    global _logger_instance
    if _logger_instance is None and project_dir is not None:
        _logger_instance = PipelineLogger(project_dir)
    return _logger_instance

def step_timer(step_id_kwarg="node_id"):
    """Decorator to time step execution and log it."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            error = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start) * 1000
                
                # Try to find node_id in args/kwargs
                # This is a bit hacky, normally we'd pass it explicitly
                node_id = kwargs.get(step_id_kwarg, "unknown_step")
                
                logger = get_logger()
                if logger:
                    logger.log(
                        step_id=node_id,
                        event_type="step_end" if not error else "step_error",
                        duration_ms=duration_ms,
                        error=error
                    )
        return wrapper
    return decorator
