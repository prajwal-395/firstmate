"""A step subprocess must not be killed at somebody's estimate.

Every step ran under a hardcoded `timeout=600`. That is the same number
the DAG carries as `semantic_analysis`'s `estimated_duration_seconds` - an
estimate used as a deadline. On project 001 (17 clips, 13.5 minutes of
footage) the vision pass needs 45 to 90 minutes; it was killed four clips
in and then RETRIED, because `_is_transient` matches "timed out". The
same ceiling sits under `temporal_index`, `render_subtitles` and `render`,
all of which exceed ten minutes on real footage. That is why no project on
disk has ever had a completed run.

The remaining timeout exists to break a wedge, not to enforce an estimate.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video import run_pipeline  # noqa: E402


def test_the_default_ceiling_is_hours_not_minutes():
    assert run_pipeline.step_timeout_seconds() >= 3600, (
        "a step ceiling under an hour kills the vision pass on any real "
        "project's footage"
    )


def test_stderr_is_streamed_and_still_reaches_the_error(tmp_path, capsys):
    """A 90-minute step held behind capture_output looks like a wedge."""
    script = tmp_path / "loud.py"
    script.write_text(
        "import sys\n"
        "print('working on it', file=sys.stderr, flush=True)\n"
        "sys.exit(3)\n"
    )
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline.run_deterministic_step(str(script), {})
    assert "working on it" in str(excinfo.value)
    assert "working on it" in capsys.readouterr().err
