"""Reserve a step's stdout for its result.

A step's contract with the runner is: **JSON on stdout, logs on stderr**
(`run_pipeline.run_deterministic_step` parses stdout and raises "Step
produced invalid JSON" otherwise). Nothing in the runtime enforces it,
and on project 001 it was broken three times across two runs, by three
different dependencies, after the step had already done all of its work:

- `temporal_index` indexed all 17 clips - "Indexed: 17, Failed: 0", about
  forty minutes of CPU - and whisperx's `setup_logging` had attached
  `logging.StreamHandler(sys.stdout)` to its logger
  (whisperx/log_utils.py:32), so its INFO and WARNING lines were
  prepended to the result and the whole step was rejected.
- `render_subtitles` rendered all 8 segments and `vision_model` printed
  "Loading mlx-community/gemma-4-12b-it-4bit..." to stdout ahead of the
  JSON, with the same outcome.
- `semantic_analysis` collected all 11 clip profiles on the rung-0a
  proof run and its per-clip vision children inherited the step's
  stdout, so their progress chatter ("Model loaded/bound in ...")
  prepended the result. Children are a hole `claim_stdout` cannot
  close (it rebinds the step's `sys.stdout`; the child's fd 1 still
  points at the capture pipe), so the step captures each child and
  forwards both streams to stderr
  (`step_1_03_semantic_analysis.step._run_clip_vision`).

Both were correct work discarded by a logging default. The lesson is not
"silence that logger" - it is that a step cannot know what its imports
will print, so the guarantee has to be structural.

Usage, as the first statement in a step's `main()`:

    from library.tools.step_stdout import claim_stdout, emit

    def main():
        claim_stdout()
        ...
        emit(result)

`claim_stdout()` points the process-wide `sys.stdout` at stderr and keeps
the real handle private. Anything that logs or prints afterwards - the
step itself, a dependency, a lazily-configured logger - goes to stderr,
where the runner streams it as the step's log. `emit()` writes the one
JSON document to the reserved handle.

It is order-independent for any stream grabbed after the claim, which
covers the lazy binding both offenders above used. A dependency that
captured `sys.stdout` at import time, before `main()` ran, would still
get through; import such modules inside `main()` if that ever happens.
"""

import json
import sys
from typing import Any

_RESULT_STREAM = sys.stdout


def claim_stdout() -> None:
    """Reserve the real stdout for the result; send everything else to stderr."""
    global _RESULT_STREAM
    _RESULT_STREAM = sys.stdout
    sys.stdout = sys.stderr


def result_stream():
    """The reserved stdout, for a caller that must write to it directly."""
    return _RESULT_STREAM


def emit(payload: Any) -> None:
    """Write the step's one JSON result to the reserved stdout."""
    json.dump(payload, _RESULT_STREAM, indent=2)
    _RESULT_STREAM.flush()
