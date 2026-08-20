"""A step's stdout is its result, and a dependency must not be able to
write to it.

`temporal_index` indexed all 17 clips of project 001 correctly - "Indexed:
17 clips, Failed: 0 clips", about forty minutes of CPU - and the runner
threw the whole thing away with "Step produced invalid JSON". whisperx's
`setup_logging` attaches `logging.StreamHandler(sys.stdout)` to the
"whisperx" logger (whisperx/log_utils.py:32) the first time anything logs
through it, so its INFO and WARNING lines were prepended to the result.

Nobody chose that. A dependency's default chose it, which is why the fix
is structural rather than a call to silence one logger: the step points
process-wide stdout at stderr and keeps the real handle private for the
result.

These tests run the step module in a SUBPROCESS, because the guarantee
being tested is about a real process's real file descriptors - asserting
it in-process would only prove that a StringIO got swapped.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STEP = REPO_ROOT / "library" / "steps" / "step_1_04_temporal_index" / "step.py"


def _run(snippet: str) -> subprocess.CompletedProcess:
    """Import the step module and run `snippet` against its stdout guard."""
    program = textwrap.dedent(f"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("step_1_04", r"{STEP}")
        step = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(step)
    """) + textwrap.dedent(snippet)
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


SUBTITLES = (REPO_ROOT / "library" / "steps" / "step_4_05_render_subtitles"
             / "step.py")
SHARED = REPO_ROOT / "library" / "tools" / "step_stdout.py"


def test_the_shared_guard_exists():
    src = SHARED.read_text()
    assert "def claim_stdout" in src
    assert "def emit" in src


@pytest.mark.parametrize("step_py", [STEP, SUBTITLES],
                         ids=["temporal_index", "render_subtitles"])
def test_a_json_emitting_step_claims_stdout(step_py):
    """Both steps that were corrupted in the wild must hold the guard.

    Each of these completed all of its work and had the result thrown
    away by a dependency writing to stdout - whisperx's log handler in
    one, vision_model's model-loading line in the other.
    """
    src = step_py.read_text()
    assert "claim_stdout()" in src, f"{step_py.name} must claim stdout in main()"
    assert "sys.stdout, indent" not in src, (
        f"{step_py.name} still writes a result to sys.stdout directly")


def test_vision_model_logs_to_stderr():
    """The offender that corrupted render_subtitles."""
    src = (REPO_ROOT / "library" / "tools" / "vision_model.py").read_text()
    for line in src.splitlines():
        if "print(" in line and "Loading" in line or "Model loaded" in line:
            assert "sys.stderr" in line, line


def test_a_dependency_logging_to_stdout_does_not_corrupt_the_result():
    """The exact whisperx shape, reproduced with its own handler line."""
    proc = _run("""
        step._claim_stdout()

        # Precisely what whisperx/log_utils.py:32 does, after the claim.
        import logging
        logger = logging.getLogger("pretend_whisperx")
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info("No language specified, language will be detected")
        logger.warning("No active speech found in audio")

        step._emit({"temporal_event_indices": [1, 2, 3], "total_failed": 0})
    """)

    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    assert parsed == {"temporal_event_indices": [1, 2, 3], "total_failed": 0}
    assert "No active speech found" in proc.stderr


def test_a_dependency_printing_to_stdout_does_not_corrupt_the_result():
    """Not every offender uses logging; tqdm and friends just print."""
    proc = _run("""
        step._claim_stdout()
        print("Fetching 9 files: 100%")
        sys.stdout.write("raw write\\n")
        step._emit({"ok": True})
    """)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True}
    assert "Fetching 9 files" in proc.stderr
    assert "raw write" in proc.stderr


def test_the_guard_holds_when_the_stream_is_grabbed_after_the_claim():
    """whisperx binds its stream lazily, which is why order matters.

    A handler created AFTER the claim must resolve to stderr. This is the
    property that makes the fix independent of import order.
    """
    proc = _run("""
        step._claim_stdout()
        import logging
        h = logging.StreamHandler(sys.stdout)   # grabbed after the claim
        assert h.stream is not sys.__stdout__, "handler still holds real stdout"
        step._emit({"ok": True})
    """)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True}


def test_stdout_carries_exactly_one_json_document():
    """A trailing print after the result would also break the parse."""
    proc = _run("""
        step._claim_stdout()
        step._emit({"a": 1})
        print("goodbye")
    """)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"a": 1}
    assert "goodbye" in proc.stderr


def test_without_the_claim_the_result_would_be_corrupted():
    """The control. Shows these tests can fail, and how the run failed.

    This is the pre-fix behaviour, asserted deliberately: it is the thing
    the guard prevents, and without it the tests above could pass for the
    wrong reason.
    """
    proc = _run("""
        import logging
        logger = logging.getLogger("pretend_whisperx")
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
        logger.info("INFO - No language specified")
        import json as _json
        _json.dump({"ok": True}, sys.stdout)
    """)

    assert proc.returncode == 0, proc.stderr
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)
