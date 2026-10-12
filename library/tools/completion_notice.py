"""The macOS completion notice: one `display notification`, best-effort.

A run that finishes while the person looks elsewhere has to tell them,
and the notice is only useful when it says WHAT finished - the run's
status (`run_pipeline.notify_run_end`) or the file to watch
(`reel_preview`). Both post through `post` so the opt-out and the
platform rules live once.

`REN_NOTIFY=0` opts out, and off macOS (or with no `osascript` on PATH)
it is a silent no-op. A notice that cannot be posted never fails the
work it announces.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

#: How long the notification itself may take before the caller stops
#: waiting for it. Posting is instant; a wedged notification server
#: must not wedge a run's ending.
NOTIFY_TIMEOUT_S = 10


def post(text: str, title: str = "Ren") -> bool:
    """Post one notification. Returns whether one was posted."""
    if os.environ.get("REN_NOTIFY") == "0":
        return False
    if sys.platform != "darwin":
        return False
    if shutil.which("osascript") is None:
        return False

    def quoted(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{quoted(text)}" with title '
             f'"{quoted(title)}"'],
            capture_output=True, timeout=NOTIFY_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
