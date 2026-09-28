#!/usr/bin/env python3
"""Connect to Resolve and yield its handles while holding the instance lease.

Usage::

    from connect_resolve import connect
    with connect(exclusive=False) as (resolve, project, timeline):
        ...  # Keep every Resolve API call inside this block.
"""
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from library.tools.resolve_locale import scriptapp_preserving_locale
from library.tools.resolve_lock import resolve_lease

RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


@contextmanager
def connect(retries=3, delay=2.0, *, exclusive=True):
    """Yield `(resolve, project, timeline)` inside the held instance lease.

    Pass ``exclusive=False`` only for a read that does not move the current
    project or timeline. The default protects writes and renders.

    Raises:
        RuntimeError: If Resolve is not running or not responding.
    """
    with resolve_lease("Resolve pipeline skill script", exclusive=exclusive):
        sys.path.append(os.path.join(RESOLVE_SCRIPT_API, "Modules"))
        os.environ["RESOLVE_SCRIPT_API"] = RESOLVE_SCRIPT_API
        os.environ["RESOLVE_SCRIPT_LIB"] = RESOLVE_SCRIPT_LIB

        import DaVinciResolveScript as dvr

        resolve = None
        for attempt in range(retries):
            resolve = scriptapp_preserving_locale(dvr, "Resolve")
            if resolve is not None:
                break
            if attempt < retries - 1:
                print(f"  Resolve not ready, retrying in {delay}s...")
                time.sleep(delay)

        if resolve is None:
            raise RuntimeError(
                "Cannot connect to Resolve. "
                "Make sure it's running and Preferences > General > "
                "External scripting using is set to 'Local'."
            )

        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        timeline = project.GetCurrentTimeline() if project else None
        yield resolve, project, timeline


if __name__ == "__main__":
    with connect(exclusive=False) as (resolve, project, timeline):
        print(f"Connected to Resolve {resolve.GetVersionString()}")
        print(f"Project: {project.GetName()}")
        if timeline:
            print(f"Timeline: {timeline.GetName()}")
            v1 = timeline.GetItemListInTrack("video", 1) or []
            print(f"V1 clips: {len(v1)}")
        else:
            print("No timeline open")
