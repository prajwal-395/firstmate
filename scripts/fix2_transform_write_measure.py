"""The transform-write measurement, inside ONE broker session.

A reel build's transform writes, measured live: how many there are, and
how long they take. The build needs its project to be the OPEN Resolve
project, so the measurement opens a Ren-owned scratch project, builds
the reel, and restores the recorded origin - all inside ONE exclusive
broker session (`library/tools/resolve_session.py`), so no other lane can
switch the one Resolve's project in the middle of it.

    bin/vep scripts/fix2_transform_write_measure.py

The origin is released when the run ends, whatever the build did.

Firstmate's rule (2026-10-21): no hand-scheduled windows; a multi-call
DaVinci operation holds ONE exclusive broker session, so the whole
before/after measurement - open the scratch project, measure, restore -
runs inside that one session and no other lane can switch the one
Resolve's project in the middle of it.

Everything happens in this one process: the scratch project is opened
directly (not as a broker job, which a session refuses to queue), the
build runs in-process (its own session joins this one), and the recorded
origin is restored at the end whatever the build did.
"""

import json
import sys
import traceback

PROJECT = "ren-scratch-breaktest"
SCRATCH = "Ren Scratch - breaktest"


def _connect():
    from library.tools.resolve_locale import (
        LazyResolveScript,
        scriptapp_preserving_locale,
    )

    return scriptapp_preserving_locale(LazyResolveScript(), "Resolve")


def main() -> int:
    from library.tools import resolve_lock
    from library.tools import resolve_scratch_projects as scratch
    from library.tools.resolve_session import resolve_session

    outcome: dict = {}
    with resolve_session("FIX 2 live measurement", project=SCRATCH,
                         timeline="", timeout=2400, description=(
                             "open the scratch project, build reel 13, "
                             "restore the origin - one session")):
        with resolve_lock.resolve_lease(
                "FIX 2 measurement: read the open project",
                exclusive=True):
            resolve = _connect()
            manager = resolve.GetProjectManager()
            current = manager.GetCurrentProject()
            open_name = current.GetName() if current is not None else ""
        if open_name == SCRATCH:
            # Another writer already holds the scratch project open. Its
            # route back is ITS record to restore, so this measurement
            # neither opens nor restores it - it only builds.
            outcome["already_open"] = SCRATCH
        else:
            with resolve_lock.resolve_lease(
                    "FIX 2 measurement: open the scratch project",
                    exclusive=True):
                resolve = _connect()
                manager = resolve.GetProjectManager()
                outcome["opened"] = scratch.open_project(
                    resolve, manager, SCRATCH)
        try:
            import manage_project

            sys.argv = ["manage_project.py", "build-reels", PROJECT,
                        "--only-reel", "13"]
            try:
                manage_project.main()
                outcome["build_exit"] = 0
            except SystemExit as exc:
                outcome["build_exit"] = exc.code
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome["build_exit"] = f"error: {exc}"
                outcome["build_trace"] = traceback.format_exc()[-4000:]
        finally:
            if "opened" in outcome:
                try:
                    with resolve_lock.resolve_lease(
                            "FIX 2 measurement: restore the recorded origin",
                            exclusive=True):
                        resolve = _connect()
                        manager = resolve.GetProjectManager()
                        outcome["restored"] = scratch.restore_recorded(
                            resolve, manager)
                except BaseException as exc:  # noqa: BLE001 - reported below
                    outcome["restored_error"] = f"{exc}"
                    outcome["restore_trace"] = traceback.format_exc()[-4000:]
            else:
                outcome["restored"] = ("not this measurement's to restore: "
                                       "the open project belongs to another "
                                       "writer")
    print("MEASUREMENT_JSON " + json.dumps(outcome, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
