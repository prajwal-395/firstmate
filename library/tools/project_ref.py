"""One project's FOLDER, from whatever the caller named it by.

A measurement CLI takes a project reference - a path, or a slug under
`PIPELINE_PROJECTS_ROOT` - and every one of them reads
`pipeline_data.json` RELATIVE to the string it was given. A slug
therefore resolved to nothing, and the report read like "there was
nothing to measure": an empty catalog, exit 0, no error. The defect is
in `docs/RULE_EVIDENCE.md`.

**A measurement that cannot find its project REFUSES.** It never reports
an empty catalog: "the project has no clips" and "you named a project
that does not exist" are different facts, and only one of them is a
measurement.

    python3 -m library.tools.project_ref <project>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from library.tools.ren_refusal import RenRefusal

STATE_FILE = "pipeline_data.json"
"""The project-side state a measurement CLI reads its catalog from."""

CONFIG_FILE = "project.yaml"
"""The project-side config. A folder carrying only this is still a
project; a folder carrying neither is not."""


def projects_root() -> Path:
    """The root a slug is looked up under, read at call time.

    The environment, not an import-time constant: a caller that points
    the root elsewhere (a scratch root, a test) must be honoured by the
    resolver, not by whatever the module saw when it was imported.
    """
    raw = os.environ.get("PIPELINE_PROJECTS_ROOT")
    if raw:
        return Path(raw).expanduser()
    from library.tools import paths
    return paths.PROJECTS_ROOT


def _looks_like_a_project(folder: Path) -> bool:
    return ((folder / STATE_FILE).is_file()
            or (folder / CONFIG_FILE).is_file())


def _slug_candidates(root: Path, slug: str) -> list[Path]:
    """Where a slug may live under the projects root."""
    out = [root / slug]
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "_")):
                out.append(entry / slug)
    return out


def resolve_project_folder(ref: str) -> Path:
    """The project FOLDER a reference names, or a refusal.

    Accepted: a path to a project folder (absolute, relative or `~`),
    a path to its `project.yaml`, or a slug under
    `PIPELINE_PROJECTS_ROOT` (flat or client-grouped). Anything else is
    refused by name - a slug that resolves to nothing must not read as
    a project with no clips.
    """
    wanted = str(ref or "").strip()
    if not wanted:
        raise RenRefusal(
            "no project was named",
            "the project argument was empty",
            "pass a project folder or a slug under PIPELINE_PROJECTS_ROOT", blocker="ambiguous_intent")
    candidate = Path(wanted).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = Path(wanted).expanduser()
    if candidate.is_file() and candidate.name == CONFIG_FILE:
        candidate = candidate.parent
    if candidate.is_dir():
        if _looks_like_a_project(candidate):
            return candidate
        raise RenRefusal(
            f"{candidate} is not a Ren project",
            f"it carries neither {STATE_FILE} nor {CONFIG_FILE}, so it has "
            "no clip catalog to measure",
            "point the command at a Ren project folder, or create one "
            "with `ren new <slug>`", blocker="missing_media")
    if os.sep in wanted or wanted.startswith(("~", ".")):
        raise RenRefusal(
            f"{wanted} does not exist",
            "the path was given as a folder, and there is nothing there",
            "point the command at a Ren project folder, or create one "
            "with `ren new <slug>`", blocker="missing_media")
    root = projects_root()
    for folder in _slug_candidates(root, wanted):
        if folder.is_dir() and _looks_like_a_project(folder):
            return folder.resolve()
    raise RenRefusal(
        f"no project named {wanted!r}",
        f"the projects root {root} holds no project by that name, and "
        f"{wanted!r} is not a path on disk",
        f"list what is there (`manage_project.py list`), or create the "
        f"project with `ren new {wanted}`", blocker="missing_media")


def require_project_state(folder: Path) -> Path:
    """The folder, once it carries the state a measurement reads.

    A project with a `project.yaml` but no `pipeline_data.json` has no
    catalog yet: the measurement would report an empty project, which
    is true but useless - the fix is to scan it first.
    """
    if not (folder / STATE_FILE).is_file():
        raise RenRefusal(
            f"{folder} has no {STATE_FILE} yet",
            "the clip catalog a measurement reads is written by the scan "
            "and catalog steps, and this project has not been scanned",
            "run the preflight first: `ren edit <project> --only scan "
            "--only catalog`", blocker="missing_media")
    return folder


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="project_ref",
        description="Resolve a project reference (path or slug) to its "
                    "folder, or refuse by name.")
    parser.add_argument("project")
    args = parser.parse_args(argv)
    print(json.dumps({"project_folder": str(resolve_project_folder(args.project))},
                     indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
