"""A step's stdout is its result, and a dependency must not be able to
write to it: the step points process-wide stdout at stderr and keeps the
real handle private for the result. Run in a SUBPROCESS, because the
guarantee is about a real process's file descriptors.
History (whisperx on 001's temporal_index): docs/evidence/step_stdout.md.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
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


def test_no_dependency_write_to_stdout_corrupts_the_result():
    """The exact whisperx shape (a StreamHandler on sys.stdout, bound
    lazily AFTER the claim, which is why order must not matter), plain
    prints and raw writes (tqdm and friends), and a trailing print after
    the result: stdout carries exactly one JSON document, and everything
    else lands on stderr."""
    proc = _run("""
        step._claim_stdout()

        # Precisely what whisperx/log_utils.py:32 does, after the claim.
        import logging
        logger = logging.getLogger("pretend_whisperx")
        handler = logging.StreamHandler(sys.stdout)
        assert handler.stream is not sys.__stdout__, "handler holds real stdout"
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info("No language specified, language will be detected")
        logger.warning("No active speech found in audio")
        print("Fetching 9 files: 100%")
        sys.stdout.write("raw write\\n")

        step._emit({"temporal_event_indices": [1, 2, 3], "total_failed": 0})
        print("goodbye")
    """)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "temporal_event_indices": [1, 2, 3], "total_failed": 0}
    for line in ("No active speech found", "Fetching 9 files", "raw write",
                 "goodbye"):
        assert line in proc.stderr, line


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
