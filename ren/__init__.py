"""Ren: the installed front door to this video editing engine.

`ren` is the one command a person or the chat skill types. It is a thin
layer over what already exists: every verb either runs `ren doctor` /
`ren config` (the only code of its own) or execs `manage_project.py` or
a library module through `bin/vep`, unchanged. See `ren.cli.VERBS`.

It is installed EDITABLE from a checkout (`pip install -e .`), because the
engine is the checkout: `ren` finds `manage_project.py`, `bin/vep` and
`library/` beside this package, never in site-packages.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
