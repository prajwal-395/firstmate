"""A step's result must survive being bigger than a pipe buffer, with
traffic on BOTH pipes at once - the shape that deadlocked the runner on
project 001's semantic_analysis. History: docs/evidence/step_subprocess.md.
"""

import json
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    _run_step_subprocess,
)

def _write_step(tmp_path: Path, body: str) -> Path:
    step = tmp_path / "step.py"
    step.write_text(textwrap.dedent(body), encoding="utf-8")
    return step


BIG_BOTH_STEP = """
    import json, sys
    data = json.loads(sys.stdin.read())
    for i in range(data["rows"]):
        print(f"progress line {i} " + "y" * 40, file=sys.stderr)
    json.dump({"rows": list(range(data["rows"]))}, sys.stdout)
"""


def test_large_stderr_and_large_stdout_together(tmp_path):
    """Both pipes over the buffer at once - the real analysis-phase shape.

    A long vision pass logs steadily while building a large result. If
    either pipe is left unserviced while the other is read, this hangs.
    """
    step = _write_step(tmp_path, BIG_BOTH_STEP)

    code, out, err = _run_step_subprocess(
        [sys.executable, str(step)], {"rows": 3000}, "both")

    assert code == 0
    assert json.loads(out)["rows"][-1] == 2999
    assert err.count("progress line") == 3000


