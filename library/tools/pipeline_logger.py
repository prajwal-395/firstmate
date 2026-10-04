"""Structured per-project log: one JSON object per line in `logs/pipeline_log.jsonl`.

Every entry carries the correlation ids a support bundle needs to join
it to everything else - `run_id` (the provenance run that wrote it),
`project_id` (the project directory's name) and `ren_build` (the Ren
build that ran). The runner binds the run id once it mints it
(`get_logger(project_dir, run_id=...)`); entries written before that
carry `run_id: null`, which is honest - the run did not exist yet.

The file rotates: past `LOG_ROTATE_BYTES` the current log shifts to
`pipeline_log.1.jsonl` (keeping `LOG_ROTATE_KEEP` backups) instead of
growing without bound across every run since the project was created.

A logging failure is observable: the event already went to stderr
before the file write, the failure warns on stderr with its count,
`log()` returns False, and the count survives on the instance
(`write_failures`, `last_write_error`) for the support bundle to read.
Raising was considered and rejected - a logging failure must not fail
an edit.
"""

import json
import os
import subprocess
import sys
import time
from functools import lru_cache, wraps
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout
from library.tools.ren_refusal import RenRefusal

LOG_FILE_NAME = "pipeline_log.jsonl"
LOG_ROTATE_BYTES = 5_000_000
"""Rotate past 5 MB: large enough that one run never rotates, small
enough that a project worked for months stays bounded."""
LOG_ROTATE_KEEP = 3


@lru_cache(maxsize=1)
def ren_build_id() -> str:
    """The Ren build that is running, as `ren_build` log lines report it.

    The version lane owns the canonical answer. Until it lands, fall
    back to installed metadata, then the checkout version plus its git
    revision. A source tree without git metadata is reported as unknown
    rather than being given a made-up build id."""
    try:
        from ren.version import version_string
        return version_string()
    except (ImportError, AttributeError):
        pass
    version = None
    try:
        from importlib import metadata
        version = str(metadata.version("ren"))
    except Exception:  # noqa: BLE001 - any lookup failure falls through
        pass
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        import tomllib
        if version is None:
            with open(repo_root / "pyproject.toml", "rb") as f:
                version = str(tomllib.load(f)["project"]["version"])
    except Exception:  # noqa: BLE001 - an unreadable manifest is not fatal
        if version is None:
            return "0.0.0+unknown"
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo_root, capture_output=True, encoding="utf-8",
            timeout=5, check=False)
        revision = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        revision = ""
    if revision:
        base = version.split("+", maxsplit=1)[0]
        return f"{base}+{revision}.dev"
    return version or "0.0.0+unknown"


class PipelineLogger:
    def __init__(self, project_dir: str, run_id: str | None = None,
                 project_id: str | None = None, build_id: str | None = None):
        self.project_dir = Path(project_dir)
        self.run_id = run_id
        self.project_id = project_id or self.project_dir.name
        self.build_id = build_id or ren_build_id()
        # Resolved on the first write, not here: a run that REFUSES before
        # its first step must leave the project untouched, and creating
        # the logger is the first thing a run does.
        self.log_file = None
        # Logging-failure accounting: a failure warns on stderr at once
        # and is counted here, so nothing about it is silent.
        self.write_failures = 0
        self.last_write_error = None
        self.rotation_failures = 0
        self.last_rotation_error = None

    def log(self, step_id: str, event_type: str, duration_ms: float = None,
            token_count: dict = None, error: str = None,
            gate_decision: str = None, backend: str = None,
            latency: float = None, detail: dict = None, code: str = None,
            error_type: str = None) -> bool:
        """Write one JSON line. Returns True on write, False on failure.

        `code` is the refusal's machine-readable code when the event
        records one (`RenRefusal.code`); entries that record none carry
        `code: null`, so the schema is stable for machines to read."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": self.run_id,
            "project_id": self.project_id,
            "ren_build": self.build_id,
            "step_id": step_id,
            "event_type": event_type,
            "code": code,
            "error_type": error_type,
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
        # Whatever else the event carries.  An event that is neither a
        # duration, a token count nor an error had nowhere to record
        # what it observed, so it recorded only that it happened.
        if detail is not None:
            entry["detail"] = detail

        # Write to stderr (human-readable)
        print(f"[LOG] {event_type} {step_id}" + (f" | {duration_ms}ms" if duration_ms else "") + (f" | Error: {error}" if error else ""), file=sys.stderr)

        # Write to file
        try:
            json_line = json.dumps(entry)
            if self.log_file is None:
                self.log_file = ProjectLayout(self.project_dir).write_path(
                    Area.LOGS, LOG_FILE_NAME)
            try:
                _maybe_rotate(self.log_file,
                              len(json_line.encode("utf-8")) + 1)
            except OSError as e:
                self.rotation_failures += 1
                self.last_rotation_error = f"{type(e).__name__}: {e}"
                print("Warning: Failed to rotate log file "
                      f"(failure {self.rotation_failures}): {e}",
                      file=sys.stderr)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json_line + "\n")
            return True
        except Exception as e:
            self.write_failures += 1
            self.last_write_error = f"{type(e).__name__}: {e}"
            print(f"Warning: Failed to write to log file "
                  f"(failure {self.write_failures}): {e}", file=sys.stderr)
            return False

_logger_instance = None

def get_logger(project_dir: str = None, run_id: str = None) -> PipelineLogger:
    """The process-wide logger. Passing `run_id` binds it to that run;
    passing a different `project_dir` rebinds to that project - a
    singleton that kept serving the previous project after a switch
    would misattribute every line it wrote."""
    global _logger_instance
    if (_logger_instance is None
            or (project_dir is not None
                and Path(project_dir) != _logger_instance.project_dir)):
        if project_dir is None:
            return _logger_instance
        _logger_instance = PipelineLogger(project_dir)
    if run_id is not None:
        _logger_instance.run_id = run_id
    return _logger_instance


def _maybe_rotate(log_file, size_hint: int) -> None:
    """Shift the log past `LOG_ROTATE_BYTES`, keeping `LOG_ROTATE_KEEP`
    backups. Rotation is by rename, so concurrent step workers racing
    here churn backups but never lose a line they wrote."""
    if not os.path.isfile(log_file):
        return
    if os.path.getsize(log_file) + size_hint <= LOG_ROTATE_BYTES:
        return
    base = str(log_file)
    oldest = f"{base}.{LOG_ROTATE_KEEP}"
    if os.path.isfile(oldest):
        os.remove(oldest)
    for index in range(LOG_ROTATE_KEEP - 1, 0, -1):
        candidate = f"{base}.{index}"
        if os.path.isfile(candidate):
            os.replace(candidate, f"{base}.{index + 1}")
    os.replace(base, f"{base}.1")

def step_timer(step_id_kwarg="node_id"):
    """Decorator to time step execution and log it."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            error = None
            code = None
            error_type = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                if isinstance(e, RenRefusal):
                    code = e.code
                error_type = type(e).__name__
                raise
            finally:
                duration_ms = (time.time() - start) * 1000

                # Try to find node_id in args/kwargs
                node_id = kwargs.get(step_id_kwarg, "unknown_step")
                if node_id == "unknown_step":
                    import inspect
                    try:
                        sig = inspect.signature(func)
                        bound = sig.bind(*args, **kwargs)
                        bound.apply_defaults()
                        if step_id_kwarg in bound.arguments:
                            node_id = bound.arguments[step_id_kwarg]
                    except (ValueError, TypeError):
                        pass

                logger = get_logger()
                if logger:
                    logger.log(
                        step_id=node_id,
                        event_type="step_end" if not error else "step_error",
                        duration_ms=duration_ms,
                        error=error,
                        code=code,
                        error_type=error_type,
                    )
        return wrapper
    return decorator
