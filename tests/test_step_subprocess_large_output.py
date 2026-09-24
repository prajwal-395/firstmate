"""A step's result must survive being bigger than a pipe buffer.

The runner deadlocked on project 001 exactly here. `semantic_analysis`
finished all 17 clips, printed "Collected 17 clip profiles", and stopped.
`sample` showed the child parked in `write()` and BOTH parent threads in
`read()`: stderr was being echoed from a pump thread while
`communicate()` - which reads stdout AND stderr - ran on the same Popen.
Two readers on one pipe, so the main thread blocked on stderr bytes the
pump had already taken, nothing drained stdout, and the child wedged the
moment its JSON exceeded the 64KB pipe buffer.

The tell is that it is invisible on small steps. `scan` and `catalog`
emit a few KB and pass; the deadlock only appears once a step has real
work to report, which is why it survived every test and every small
project. So these tests use an output deliberately far past one buffer.

Which test actually reproduces it, measured against the broken revision:
**`test_large_stderr_and_large_stdout_together`, and only that one.** A
large stdout alone passes even on the deadlocking version, because with
stderr quiet the pump simply blocks and the selector still services
stdout. The race needs traffic on BOTH pipes - the selector reports
stderr readable, the pump has already taken those bytes, and the main
thread parks in a read() that will never return, after which stdout is
never serviced again. That is the analysis phase exactly: a step that
logs steadily for an hour and then emits a large result.

So do not treat the other five as the guard. They pin the surrounding
behaviour - exit codes, stderr on failure, a large stdin, a fast step not
losing its bytes - which is what a fix for a hang is most likely to break.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    _run_step_subprocess,
    run_deterministic_step,
)

# Comfortably past the 64KB pipe buffer, and past a few of them.
PAYLOAD_ROWS = 4000


def _write_step(tmp_path: Path, body: str) -> Path:
    step = tmp_path / "step.py"
    step.write_text(textwrap.dedent(body), encoding="utf-8")
    return step


BIG_OUTPUT_STEP = """
    import json, sys
    data = json.loads(sys.stdin.read())
    rows = [{"clip_id": f"clip_{i:05d}",
             "note": "x" * 64} for i in range(data["rows"])]
    print("done collecting", file=sys.stderr)
    json.dump({"rows": rows}, sys.stdout)
"""






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






