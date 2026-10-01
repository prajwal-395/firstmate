"""Which Python dependency group carries what, and how to read them.

`requirements.txt` is the whole machine and includes one flat file per
group under `requirements/`. Each package is declared in exactly ONE
group file; an extra that needs another group lists that group's file
too (pyproject.toml `[tool.setuptools.dynamic]`), so a floor is never
stated twice.

    core      the engine, the CLI, every step's orchestration
    graphics  picture measurement and drawing (cv2, Pillow)
    analysis  the preflight's ML instruments (torch, mlx-vlm, ...)
    identity  speaker and face identity (speechbrain, insightface)
    dev       pytest and the test gate's xdist

`pyproject.toml` exposes the same files: `core` as the install
dependencies and the rest as extras. Setuptools reads those files with
no `-r` support, which is why the group files stay flat and only
`requirements.txt` includes. The measured stack is the lock,
`requirements/lock/macos-arm64-py312.txt` (scripts/lock_python_env.sh).

Readers of the manifest go through `declared_requirements`, which
follows `-r` includes: a reader that skipped them would see no
requirement at all and enforce nothing, silently.

Stdlib only: `ren doctor` runs this inside the pipeline interpreter
before anything about that interpreter is known.
"""

from __future__ import annotations

from pathlib import Path

REQUIREMENTS_DIR = Path(__file__).resolve().parents[2] / "requirements"

RUNTIME_GROUPS = ("core", "graphics", "analysis", "identity")
"""The groups a capability can need (`machine_needs`); `dev` is the
test gate's alone, so no capability needs it."""


def group_file(group: str) -> Path:
    """The flat requirements file one group declares."""
    return REQUIREMENTS_DIR / f"{group}.txt"


def declared_requirements(path: Path) -> list:
    """Every requirement line `path` declares, `-r` includes followed.

    Comments and blank lines are dropped; other pip options are
    skipped. An include is resolved against the including file, as pip
    does. A missing file raises `OSError`.
    """
    path = Path(path)
    found = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for flag in ("-r ", "--requirement "):
            if line.startswith(flag):
                found.extend(declared_requirements(
                    path.parent / line[len(flag):].strip()))
                break
        else:
            if not line.startswith("-"):
                found.append(line)
    return found
