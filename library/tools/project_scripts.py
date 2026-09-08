"""Standalone scripts living in a project folder, where the repo cannot see them.

The gap, recorded 2026-09-06: three scripts in the captain's project
folder (`place_subtitles.py`, `generate_podcast_subtitles.py`,
`export_audio.py`) re-implemented steps 4.01/4.05 and timeline
placement outside the pipeline, and every repository-side guard - the
Ruling-1 no-second-implementation test, the import greps, the operation
registry's `run` resolving into `library/steps/` - looks at the
REPOSITORY. A script living next to the footage is invisible to all of
them. A later look found a fourth (`render_subtitle_segments.py`).

So this matches the general shape, never the observed names: any loose
`*.py` under the project folder. The executable bit is deliberately
NOT consulted - it is not a reliable signal on a folder shared across
macOS and sync clients; presence beside the footage is the signal.

What is excluded, and why: the pipeline's own output area. Everything
under it is reproducible pipeline product (AGENTS.md 8:
"`pipeline_output/steps/` IS the pipeline"), so flagging it would be
noise. The boundary is derived from the layout's own read route -
`read_dir`, which never creates - so a read-only scan cannot mkdir the
captain's project as a side effect. `write_dir` (and with it
`output_root`) would create it.

What the reader does with a finding is REPORT, never refuse. Refusing
would dictate how the captain works in their own directories, and that
call is the captain's. The pipeline's debt is visibility, and
`manage_project.py check` pays it the way mixed frame rates do: a
REPORT line naming the files, and the check still passes.
"""

from __future__ import annotations

import os
from typing import List


def find_standalone_scripts(project_folder: str) -> List[str]:
    """Absolute paths of loose `*.py` files in the project folder, sorted.

    Walks the whole project folder EXCEPT the pipeline's own output
    area (derived from `project_layout`, never a literal). A missing
    project folder reads as no scripts rather than an error: the
    readiness check runs this on projects whose layout predates any
    convention, and "no layout" is not "a hidden implementation".
    """
    from library.tools.project_layout import Area, layout_for

    root = os.path.realpath(str(project_folder))
    if not os.path.isdir(root):
        return []
    try:
        excluded = os.path.realpath(
            str(layout_for(root).read_dir(Area.OUTPUT_ROOT)))
    except Exception:
        excluded = os.path.join(root, "\0")

    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        # Prune the excluded tree without descending into it: os.walk
        # still lists it, so it is removed from dirnames wherever met.
        for dirname in list(dirnames):
            if os.path.realpath(os.path.join(dirpath, dirname)) == excluded:
                dirnames.remove(dirname)
        for filename in sorted(filenames):
            if os.path.splitext(filename)[1].lower() != ".py":
                continue
            found.append(os.path.join(os.path.realpath(dirpath), filename))
    found.sort()
    return found
