#!/usr/bin/env python3
"""Connect to DaVinci Resolve and return the resolve, project, timeline objects.

Usage:
    from connect_resolve import connect
    resolve, project, timeline = connect()
"""
import sys
import os
import time

RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def connect(retries=3, delay=2.0):
    """Connect to a running DaVinci Resolve instance.

    Returns:
        tuple: (resolve, project, timeline) — timeline may be None if none is open.

    Raises:
        RuntimeError: If Resolve is not running or not responding.
    """
    sys.path.append(os.path.join(RESOLVE_SCRIPT_API, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = RESOLVE_SCRIPT_API
    os.environ["RESOLVE_SCRIPT_LIB"] = RESOLVE_SCRIPT_LIB

    import DaVinciResolveScript as dvr

    for attempt in range(retries):
        resolve = dvr.scriptapp("Resolve")
        if resolve is not None:
            break
        if attempt < retries - 1:
            print(f"  Resolve not ready, retrying in {delay}s...")
            time.sleep(delay)

    if resolve is None:
        raise RuntimeError(
            "Cannot connect to DaVinci Resolve. "
            "Make sure it's running and Preferences > General > "
            "External scripting using is set to 'Local'."
        )

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None

    return resolve, project, timeline


if __name__ == "__main__":
    resolve, project, timeline = connect()
    print(f"Connected to Resolve {resolve.GetVersionString()}")
    print(f"Project: {project.GetName()}")
    if timeline:
        print(f"Timeline: {timeline.GetName()}")
        v1 = timeline.GetItemListInTrack("video", 1) or []
        print(f"V1 clips: {len(v1)}")
    else:
        print("No timeline open")
