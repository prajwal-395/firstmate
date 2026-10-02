# A step's stdout is its result

Moved from `tests/unit/context/test_step_stdout_contract.py`.

`temporal_index` indexed all 17 clips of project 001 correctly - "Indexed:
17 clips, Failed: 0 clips", about forty minutes of CPU - and the runner threw
the whole thing away with "Step produced invalid JSON". whisperx's
`setup_logging` attaches `logging.StreamHandler(sys.stdout)` to the "whisperx"
logger (whisperx/log_utils.py:32) the first time anything logs through it, so
its INFO and WARNING lines were prepended to the result. `render_subtitles`
was corrupted the same way by `vision_model`'s model-loading line.

Nobody chose that. A dependency's default chose it, which is why the fix is
structural rather than a call to silence one logger: the step points
process-wide stdout at stderr and keeps the real handle private for the
result (`library/tools/step_stdout.py`, `claim_stdout()`).

The tests run the step module in a SUBPROCESS, because the guarantee is about
a real process's real file descriptors - asserting it in-process would only
prove that a StringIO got swapped. A control test reproduces the pre-fix
corruption so the guard tests cannot pass for the wrong reason.
