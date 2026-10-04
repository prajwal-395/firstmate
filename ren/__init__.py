"""Ren: the installed front door to this video editing engine.

`ren` is the one command a person or the chat skill types. It is a thin
layer over what already exists: every verb either runs `ren doctor` /
`ren config` / `ren version` (the only code of its own) or execs
`manage_project.py` or a library module through `bin/vep`, unchanged.
See `ren.cli.VERBS`.

`ren` is installed from a checkout (`pip install -e .`) for
development, or ships inside a versioned engine tree
(`ren/package_engine.py`). Either way the engine is found through the
one resolver, `ren.engine_root`: `$REN_ENGINE_ROOT` first, then the
checkout beside this package, then the `<vep_home>/current` pointer.
"""

from pathlib import Path

from ren.engine_root import find_engine_root

#: Where the engine lives. `None` until something resolves it - `ren`
#: refuses with the fix (`ren/engine_root.py`) rather than asserting.
ENGINE_ROOT: Path | None = find_engine_root()

#: The checkout this package was imported from. `ENGINE_ROOT` is what
#: new code reads; this stays as the name the engine already imports.
REPO_ROOT: Path = (
    ENGINE_ROOT if ENGINE_ROOT is not None
    else Path(__file__).resolve().parent.parent
)

__all__ = ["ENGINE_ROOT", "REPO_ROOT"]
