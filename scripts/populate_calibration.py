"""Populate the draw-gain calibration record from a disposable project.

2026-10-04: the live draw-gain probe used to write timeline resolution
settings on a scratch timeline in the captain's open project, which is
the same ``SetSetting`` / ``FusionApp::SyncProjectSettings`` ->
``RenderTask::ObtainRenderLock`` deadlock seen in the 2026-10-01 and
2026-10-02 Resolve crash reports. The routine path now reads a
qualified calibration record instead
(``library/tools/draw_gain_calibration.py``).

This script populates that record ONCE, by running the live probe in a
DISPOSABLE project at the target delivery geometry. It never touches
the captain's project: it borrows Resolve the way
``qualification_project.borrowed`` does - saving and restoring whatever
project was open - and works in ``ZZ Calibration Disposable``, creating
that project where it does not exist.

The probe creates and deletes its OWN scratch timeline at the target
size; no project or timeline resolution is changed by this script.

Usage::

    bin/vep scripts/populate_calibration.py [--width 1080] [--height 1920]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from library.tools.resolve_lock import under_lease

DISPOSABLE_PROJECT = "ZZ Calibration Disposable"
"""The project this calibration is measured in. Never the captain's."""


def _disposable_folder() -> Path:
    """A local folder for the disposable project, with a project.yaml.

    The probe's ``set_property`` writes a transform log under the project
    folder, so the folder must exist and carry a ``project.yaml`` naming
    the project. This is a scratch folder under the projects root, never
    the captain's project.
    """
    root = Path(os.environ.get("PIPELINE_PROJECTS_ROOT",
                              "~/Documents/content_stuff/video_projects"))
    root = root.expanduser()
    folder = root / DISPOSABLE_PROJECT
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "project.yaml"
    if not config.exists():
        config.write_text(
            f"name: {DISPOSABLE_PROJECT}\n"
            f"slug: {DISPOSABLE_PROJECT.lower().replace(' ', '-')}\n",
            encoding="utf-8")
    return folder


@under_lease("populate draw-gain calibration", exclusive=True)
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="populate_calibration",
        description="Measure the draw gain in a disposable project and "
                    "store it in the calibration record.")
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    args = parser.parse_args(argv)

    from library.tools import draw_gain_calibration as cal
    from library.tools import draw_gain_probe as probe
    from library.tools.marker_feedback import connect_resolve

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    folder = _disposable_folder()

    # Borrow Resolve: save the captain's project, restore it at the end.
    current = pm.GetCurrentProject()
    if not current:
        print("Resolve has no project open to return to", file=sys.stderr,
              flush=True)
        return 1
    captain_name = current.GetName()
    if captain_name == DISPOSABLE_PROJECT:
        print(f"Resolve is already on {DISPOSABLE_PROJECT!r}; reopen the "
              f"captain's project by hand first", file=sys.stderr, flush=True)
        return 1
    captain_timeline = current.GetCurrentTimeline()
    captain_tl_id = captain_timeline.GetUniqueId() if captain_timeline else None
    if not pm.SaveProject():
        print(f"could not save {captain_name!r}; not borrowing",
              file=sys.stderr, flush=True)
        return 1

    try:
        names = pm.GetProjectListInCurrentFolder()
        if DISPOSABLE_PROJECT in names:
            project = pm.LoadProject(DISPOSABLE_PROJECT)
        else:
            project = pm.CreateProject(DISPOSABLE_PROJECT)
        if not project:
            print(f"could not open {DISPOSABLE_PROJECT!r}", file=sys.stderr,
                  flush=True)
            return 1

        record = probe._calibrate_live(
            resolve, project, (args.width, args.height),
            project_folder=str(folder))
        print(json.dumps(record, indent=2, default=str), flush=True)
        if record.get("source") != "measured":
            print("REFUSING to store: the probe did not measure a gain",
                  file=sys.stderr, flush=True)
            return 1
        cal.store(resolve, args.width, args.height, record["gain"])
        print(f"stored gain {record['gain']:.4f} for "
              f"{args.width}x{args.height} "
              f"(resolve {cal.resolve_version(resolve)})", flush=True)
        print(f"record: {cal.calibration_path()}", flush=True)
        return 0
    finally:
        current = pm.GetCurrentProject()
        if current and current.GetName() == DISPOSABLE_PROJECT:
            pm.SaveProject()
        captain = pm.LoadProject(captain_name)
        if not captain or captain.GetName() != captain_name:
            print(f"{captain_name!r} did not reload; reopen it by hand",
                  file=sys.stderr, flush=True)
        elif captain_tl_id is not None:
            from library.tools.resolve_lock import assert_current_timeline
            timelines = [captain.GetTimelineByIndex(i)
                         for i in range(1, captain.GetTimelineCount() + 1)]
            for tl in timelines:
                if tl and tl.GetUniqueId() == captain_tl_id:
                    assert_current_timeline(captain, tl)
                    break


if __name__ == "__main__":
    raise SystemExit(main())
