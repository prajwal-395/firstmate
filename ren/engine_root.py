"""Where the Ren engine lives: the ONE resolver every caller uses.

The engine used to BE the git checkout: `ren/__init__.py` defined it
as the parent of the installed package and `ren.cli` refused without
`manage_project.py` and `bin/vep` beside it (P0 "Create a real Ren
distribution": verified against origin/main - both claims held). A
packaged Ren instead installs immutable versioned trees:

    <vep_home>/versions/<ver>/   the engine: library/, manage_project.py,
                                 bin/vep, ren/, renderer source, skills
    <vep_home>/current -> versions/<ver>
                                 the atomic pointer; an update installs
                                 alongside, verifies, then switches it

(`ren/package_engine.py` builds the tree; the update/rollback commands
that switch it are queued separately.) `<vep_home>` is
`PIPELINE_VEP_HOME`, else `~/.local/share/vep` - the directory
`library/tools/shared_environment.py` already owns.

Resolution order - explicit choice, then the checkout beside the
package, then the installed pointer - so a developer checkout keeps
working exactly as today, with no environment set:

    1. `$REN_ENGINE_ROOT`, which names the engine root outright: a
       value that holds no engine resolves to NOTHING, so the caller
       refuses naming it rather than silently running a different
       engine than the one that was asked for;
    2. the directory beside this package (the checkout in dev, the
       built tree when `ren` is imported from one);
    3. `<vep_home>/current` (tolerating a split `current/engine`
       layout, which a future interpreter-packaging lane may use).

Standard library only: this runs before the ML environment exists
and - in the installed case - before `library/` is importable at
all, so nothing here imports the engine. In particular `vep_home()`
below mirrors `shared_environment.vep_home()` in six lines rather
than importing it; `tests/unit/context/test_engine_distribution.py`
fails when the two disagree, so the mirror cannot drift silently.
"""

from __future__ import annotations

import os
from pathlib import Path

ENGINE_ENV = "REN_ENGINE_ROOT"
"""Names the engine root outright: the escape hatch for a packaged
tree under test, a second checkout, or a layout this module did not
anticipate."""

VERSIONS_DIRNAME = "versions"

CURRENT_LINKNAME = "current"

#: What makes a directory an engine root. `manage_project.py` is the
#: engine CLI underneath `ren`, `library/` the pipeline, `ren/cli.py`
#: the front door, `bin/ren` the packaged launcher, and `bin/vep` the
#: interpreter wrapper every non-builtin verb execs through - a directory
#: missing any of them cannot serve a verb.
SENTINELS = {
    "manage_project.py": "file",
    "library": "directory",
    "ren/cli.py": "file",
    "bin/ren": "file",
    "bin/vep": "file",
}


def is_engine_root(path: str | Path) -> bool:
    """Does `path` hold the engine this resolver would run?"""
    root = Path(path).expanduser()
    if not root.is_dir():
        return False
    return all(
        (root / sentinel).is_file() if kind == "file"
        else (root / sentinel).is_dir()
        for sentinel, kind in SENTINELS.items()
    )


def package_parent() -> Path:
    """The directory beside this package: the checkout in dev."""
    return Path(__file__).resolve().parent.parent


def vep_home() -> Path:
    """The per-machine directory shared things live in.

    A six-line mirror of `shared_environment.vep_home()`, kept here
    because this resolver runs before `library/` is importable. The
    ownership stays there; the contract test pins the two equal.
    """
    explicit = os.environ.get("PIPELINE_VEP_HOME")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "vep"


def versions_root(home: str | Path | None = None) -> Path:
    """`<vep_home>/versions`: one immutable directory per built version."""
    base = Path(home).expanduser() if home is not None else vep_home()
    return base / VERSIONS_DIRNAME


def current_link(home: str | Path | None = None) -> Path:
    """`<vep_home>/current`: the symlink naming the live version."""
    base = Path(home).expanduser() if home is not None else vep_home()
    return base / CURRENT_LINKNAME


def installed_root(home: str | Path | None = None) -> Path | None:
    """The engine root the `current` pointer names, or None.

    Tolerates `current` pointing at the version directory itself
    (`current -> versions/0.1.0`) or at an `engine/` split inside it
    (`current/engine`), which a future interpreter-packaging lane may
    introduce. A dangling or absent pointer is None, never an error -
    the resolver falls through to its refusal below.
    """
    link = current_link(home)
    if not link.exists() and not link.is_symlink():
        return None
    try:
        target = link.resolve()
    except OSError:
        return None
    if is_engine_root(target):
        return target
    if is_engine_root(target / "engine"):
        return target / "engine"
    return None


def find_engine_root() -> Path | None:
    """The engine root by the resolution order, or None.

    Never raises: `ren/__init__.py` runs this at import, where a
    refusal would break even `ren --version`'s error path. Callers
    that need the engine refuse through `require_engine_root()`.
    """
    explicit = os.environ.get(ENGINE_ENV)
    if explicit:
        if is_engine_root(explicit):
            return Path(explicit).expanduser()
        return None
    adjacent = package_parent()
    if is_engine_root(adjacent):
        return adjacent
    installed = installed_root()
    if installed is not None:
        return installed
    return None


def resolve_engine_root(fallback: str | Path | None = None) -> Path | None:
    """Resolve the selected engine, optionally using a caller's local root.

    A configured `REN_ENGINE_ROOT` is an explicit choice: if it is bad,
    raise instead of silently falling back to a checkout. The fallback is
    only for low-level entry points that must still work when `ren` itself
    is unavailable to the process (for example Resolve's interpreter).
    """
    found = find_engine_root()
    if found is not None:
        return found
    explicit = os.environ.get(ENGINE_ENV)
    if explicit:
        raise RuntimeError(
            f"ren: no engine at ${ENGINE_ENV}={explicit}.\n"
            f"That variable names the engine root outright; it must hold "
            f"manage_project.py, library/ and ren/cli.py.\n"
            f"Unset it to let ren find the checkout or the installed "
            f"pointer instead.")
    return Path(fallback).expanduser().resolve() if fallback is not None else None


def _attempts() -> str:
    """Every place the resolver looked, for the refusal below."""
    lines = []
    explicit = os.environ.get(ENGINE_ENV)
    lines.append(f"    ${ENGINE_ENV}="
                 f"{explicit if explicit else '(unset)'}")
    lines.append(f"    beside the ren package: {package_parent()}")
    link = current_link()
    try:
        target = os.readlink(link)
    except OSError:
        target = "(no current pointer)"
    lines.append(f"    installed pointer: {link} -> {target}")
    return "\n".join(lines)


def require_engine_root() -> Path:
    """The engine root, or a refusal naming what was tried and the fix."""
    found = resolve_engine_root()
    if found is not None:
        return found
    raise RuntimeError(
        "ren: no engine found.\n"
        f"{_attempts()}\n"
        "Install Ren from a checkout (`pip install -e <checkout>`, README "
        "Quickstart), point $REN_ENGINE_ROOT at a built engine tree "
        "(`python3 -m ren.package_engine --dest <dir>`), or install a "
        "versioned Ren (which points <vep_home>/current at its tree).")
