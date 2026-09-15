"""Where a SHARED dependency lives, and how a checkout finds it.

One location per MACHINE, not one per checkout - and the two halves of
that question have one answer here, because they had three separate
wrong ones.

  - the Node dependencies the Remotion renderer needs;
  - the Python interpreter that carries the ML stack.

Both were assumed to sit inside whichever checkout was running. Neither
does, on any machine this repository has been installed on.

The defect this replaces
------------------------
`paths.REMOTION_DIR` was `PILOT_ROOT / "remotion-subtitles"` with no
environment override - the only external path in that module without
one, beside `PIPELINE_PROJECTS_ROOT`, `PIPELINE_SFX_LIBRARY`,
`PIPELINE_MUSIC_LIBRARY`, `RESOLVE_SCRIPT_API` and the rest.
`remotion_batch.remotion_dir` recomputed the same answer a second time.
So the renderer's dependencies could only ever be a directory inside
whichever checkout was running, and the consequences were both ways
round:

  - Measured 2026-09-14: exactly ONE working copy on the build machine
    carried `remotion-subtitles/node_modules`, at 585 MB.  No treehouse
    lane had it, so **61 tests skipped in every lane** and the local
    gate could never return an unqualified pass (AGENTS.md 9, "a build
    that declines to measure something must say what").
  - The remedy on offer - `cd remotion-subtitles && npm install` - fixes
    one lane by paying 585 MB again, per lane, forever.

This module is the other shape: the bytes live once per machine, in a
store outside every checkout, and a checkout BINDS to them.

Why a store, and why keyed by the lockfile
------------------------------------------
The open question was whether a shared store can serve tracked
application code that differs between checkouts without a staleness bug.
It can, because **it never serves the application code at all.**
`remotion-subtitles/src/`, `package.json` and `public/fonts/` are
tracked, they differ between branches, and they stay in the checkout
where git put them.  Only `node_modules/` moves - and `node_modules/` is
not checkout-specific in any degree:

    measured 2026-09-14, on the 585 MB tree
      references to the checkout's own path inside node_modules:  0
      `.bin` entries that are absolute symlinks:                  0

It is a pure function of `package-lock.json` plus the platform.  So the
store is keyed by the sha256 of that lockfile: two checkouts whose locks
agree share one tree by construction, and two whose locks differ can
never see each other's.  A stale tree is not possible, because changing
the lock changes the key.  There is no invalidation step to forget.

The binding is a symlink at `remotion-subtitles/node_modules`, and it
has to be, because the thing that resolves modules is Node - not this
module.  Node, Remotion's bundler and its headless browser all look
beside `cwd`, and 58 tests independently ask
`os.path.isdir(REMOTION_DIR/"node_modules")`.  A symlink is true for
every one of them without a single edit, and `node_modules/` is already
in `.gitignore`, so binding a checkout never dirties it.

Measured on the build machine, 2026-09-14: a clean store entry
(`npm ci`) is **285 MB**, and Remotion bundles and renders real frames
through it from a checkout that holds only the symlink -
`tests/test_staged_scene.py tests/test_fullframe_word_cues.py`, 40
passed in 39.14s, where the same selection had skipped 10.

What this module does NOT do
----------------------------
It never installs anything.  Reading where the dependencies should be is
free and side-effect free; putting them there is `scripts/install_node_deps.sh`,
run deliberately.  See `docs/SHARED_ENVIRONMENT.md`, which is to this what
`docs/ML_ENVIRONMENT.md` is to the Python ML environment - the same
shape, for the same reason, and the two are independent: this changes
nothing about the venv.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

REMOTION_DIRNAME = "remotion-subtitles"
"""The renderer's directory name inside a checkout. Tracked code."""

NODE_MODULES_DIRNAME = "node_modules"

LOCKFILE_NAME = "package-lock.json"
MANIFEST_NAME = "package.json"

STORE_KEY_LENGTH = 12
"""Hex characters of the lockfile digest that name a store entry.

48 bits.  The store holds entries for one repository's own lockfiles -
tens over its life, not millions - so this is about being readable in a
path, and a collision would need two lockfiles this project actually
shipped to agree in 48 bits.
"""

INSTALL_SCRIPT = "scripts/install_node_deps.sh"
"""The one way to fill the store. Named in every refusal."""


class NodeDependenciesMissing(RuntimeError):
    """The Node dependencies are not reachable from this checkout.

    Raised rather than skipped, and rather than left for `node` to
    report as `ERR_MODULE_NOT_FOUND` several layers down.  The message
    carries the command that fixes it.
    """


# ── where the renderer's own source is ───────────────────────────────

def remotion_dir(repo_root: Optional[str | Path] = None) -> Path:
    """The Remotion project directory.

    With `repo_root`, that checkout's copy - the seam tests and the
    persistent renderer use.  Without it, `paths.REMOTION_DIR`, which
    honours `PIPELINE_REMOTION_DIR` like every other external path in
    that module.

    Imported lazily: `paths` loads `.env`, and a caller that passed an
    explicit root should not need that to have happened.
    """
    if repo_root is not None:
        return Path(repo_root) / REMOTION_DIRNAME
    try:
        from library.tools.paths import REMOTION_DIR
    except ImportError:  # imported as `tools.paths` from inside library/
        from tools.paths import REMOTION_DIR
    return REMOTION_DIR


# ── where the dependency trees are stored ────────────────────────────

def vep_home() -> Path:
    """The per-machine directory this pipeline's shared things live in.

    `PIPELINE_VEP_HOME` names it outright.  Otherwise it is derived from
    the running user's own data directory - `XDG_DATA_HOME` when the
    machine sets one, else `~/.local/share` - so nothing here is baked to
    one person's home.

    On the build machine it resolves to `~/.local/share/vep`, which is
    where `docs/ML_ENVIRONMENT.md` already put the ML venv, for the
    reason that page gives: a directory inside a `.treehouse/*` worktree
    dies with that lane, and lanes are disposable.  That choice was made
    before this module existed; this module reads it rather than moving
    it.
    """
    explicit = os.environ.get("PIPELINE_VEP_HOME")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "vep"


def store_root() -> Path:
    """The Node dependency store. `PIPELINE_NODE_STORE` names it outright."""
    explicit = os.environ.get("PIPELINE_NODE_STORE")
    if explicit:
        return Path(explicit).expanduser()
    return vep_home() / "node"


def lockfile(directory: Optional[str | Path] = None) -> Path:
    return Path(directory) / LOCKFILE_NAME if directory is not None \
        else remotion_dir() / LOCKFILE_NAME


def store_key(directory: Optional[str | Path] = None) -> str:
    """Name the store entry this checkout's dependencies belong in.

    The digest is of the LOCKFILE, which is the complete statement of
    what `npm ci` will produce.  `package.json` is deliberately not in
    it: a range there can resolve two ways, and the entry must name one.

    Raises if there is no lockfile, because a key invented without one
    would be a promise the store cannot keep.
    """
    lock = lockfile(directory)
    if not lock.is_file():
        raise NodeDependenciesMissing(
            f"{lock} does not exist, so the dependency set this checkout "
            f"needs is not stated. It is tracked in git; a checkout "
            f"without it is incomplete."
        )
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    return f"{_project_name(directory)}-{digest[:STORE_KEY_LENGTH]}"


def _project_name(directory: Optional[str | Path] = None) -> str:
    """The `name` from package.json, for a store entry a human can read.

    Falls back to the directory name, which is mechanical rather than
    creative: it is a label on a cache path, not a decision about the
    pipeline (AGENTS.md 10.5).
    """
    base = Path(directory) if directory is not None else remotion_dir()
    manifest = base / MANIFEST_NAME
    if manifest.is_file():
        try:
            name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
        except (ValueError, OSError):
            name = None
        if isinstance(name, str) and name:
            return name.replace("/", "-").lstrip("@")
    return base.name


def store_entry(directory: Optional[str | Path] = None) -> Path:
    """The directory in the store that holds this lockfile's tree."""
    return store_root() / store_key(directory)


def store_node_modules(directory: Optional[str | Path] = None) -> Path:
    return store_entry(directory) / NODE_MODULES_DIRNAME


# ── what Node will actually resolve against ──────────────────────────

def node_modules(directory: Optional[str | Path] = None) -> Path:
    """The path Node resolves modules from, reported truthfully.

    `PIPELINE_NODE_MODULES` wins outright - the escape hatch for a
    machine whose layout this module did not anticipate.  Otherwise it
    is `<remotion_dir>/node_modules`, because that is where Node looks
    and saying anything else would be a report of somewhere Node is not.
    Whether that path is a real directory or a symlink into the store is
    exactly the question this module declines to care about.
    """
    explicit = os.environ.get("PIPELINE_NODE_MODULES")
    if explicit:
        return Path(explicit).expanduser()
    base = Path(directory) if directory is not None else remotion_dir()
    return base / NODE_MODULES_DIRNAME


def dependencies_present(directory: Optional[str | Path] = None) -> bool:
    """Are the dependencies reachable from this checkout?

    `is_dir()` follows symlinks, so a bound checkout answers True and a
    dangling bind answers False - which is the right answer, since Node
    would fail on it.
    """
    try:
        return node_modules(directory).is_dir()
    except OSError:
        return False


def missing_message(directory: Optional[str | Path] = None) -> str:
    """Why the dependencies are not reachable, and the command that fixes it.

    Names the store entry by its full path, because "run npm install"
    was the remedy that produced 585 MB in one checkout and nothing in
    five others.
    """
    base = Path(directory) if directory is not None else remotion_dir()
    where = node_modules(directory)
    lines = [
        f"The Remotion dependencies are not reachable at {where}.",
    ]
    try:
        entry = store_entry(directory)
    except NodeDependenciesMissing as exc:
        lines.append(str(exc))
        return "\n".join(lines)

    if (entry / NODE_MODULES_DIRNAME).is_dir():
        lines.append(
            f"They ARE installed for this lockfile, at {entry}; this "
            f"checkout is simply not bound to them.")
    else:
        lines.append(
            f"No store entry exists for this checkout's {LOCKFILE_NAME}. "
            f"It belongs at {entry}.")
    lines.append(
        f"Install once per machine and bind this checkout:\n"
        f"    {INSTALL_SCRIPT} {base}\n"
        f"The store is shared by every checkout whose lockfile matches, "
        f"so this is paid once per dependency set, not once per checkout. "
        f"See docs/SHARED_ENVIRONMENT.md.")
    return "\n".join(lines)


def require_dependencies(directory: Optional[str | Path] = None) -> Path:
    """Return the resolved `node_modules`, or REFUSE by name.

    The loud half.  Absence used to reach `node` and come back as a
    module-resolution error about a package nobody named, or - worse -
    as a test that quietly skipped.
    """
    if not dependencies_present(directory):
        raise NodeDependenciesMissing(missing_message(directory))
    return node_modules(directory)


# ── the PYTHON half: which interpreter carries the ML stack ──────────
#
# Same defect, one layer up, and found the same day.  THREE places
# assumed the interpreter was `<checkout>/.venv/bin/python3`:
#
#   library/tools/panel/run_view.py   refused by name when absent
#   resolve_workflow_integration/.../main.js:261
#                                     fell back to `/usr/bin/python3`,
#                                     which has none of the ML stack, so
#                                     the bridge died inside a step's
#                                     import rather than saying why
#   scripts/full_suite_gate.sh        `FULL_SUITE_GATE_PYTHON` or the
#                                     ambient `python3`
#
# Measured 2026-09-14: NO checkout on the build machine has a `.venv`.
# The working environment is the durable one at
# `~/.local/share/vep/venv-py312`, which `docs/ML_ENVIRONMENT.md` put
# outside every checkout deliberately - so the plugin's button inside
# Resolve was resolving to a path that does not exist, on the one
# surface the captain actually presses.

DURABLE_VENV_DIRNAME = "venv-py312"
"""The venv `docs/ML_ENVIRONMENT.md` builds, under `vep_home()`.

Named for its interpreter version because `requirements.txt` is explicit
that the stack must be built on 3.12; a second venv on another version
is a different thing and gets a different name.
"""

VENV_INTERPRETER = os.path.join("bin", "python3")

INTERPRETER_CANDIDATES = (
    ("env", "PIPELINE_PYTHON"),
    ("vep_home", DURABLE_VENV_DIRNAME + "/bin/python3"),
    ("repo", ".venv/bin/python3"),
)
"""The resolution ladder, DECLARED as data rather than as code.

`(kind, spec)` in order of preference. `kind` says what `spec` is
relative to: an environment variable, `vep_home()`, or the checkout.

It is data because the ladder has callers that cannot import this
module and must still obey it - `scripts/full_suite_gate.sh` and
`resolve_workflow_integration/com.videoeditingpilot.vep/js/interpreter.js`
both ASK it, through `interpreter_report()` below, rather than carrying
their own copy. A ladder duplicated in prose would have drifted the
first time it changed; a ladder duplicated in a second language already
did (the plugin's mirrored array, removed 2026-09-15). A caller that
queries cannot drift, because there is nothing on its side to drift.

There is NO stock-interpreter rung, and there must not be one. The panel
has refused rather than fall back since it was written (AGENTS.md 15,
`library/tools/panel/__init__.py`), for the reason a fallback fails:
`run_pipeline.py` imports whisperx, mlx_vlm and torch through its steps,
so a stock interpreter does not fail at launch where the message would
be read - it fails forty seconds in, inside a step, with a traceback
about a package nobody mentioned.
"""


def interpreter_candidates(repo_root: Optional[str | Path] = None):
    """`INTERPRETER_CANDIDATES` resolved to real paths, in order.

    A rung whose environment variable is unset yields nothing; a rung
    that needs a checkout and has none is skipped, which is what lets
    this answer for the Resolve plugin as well as for a checkout.
    """
    for kind, spec in INTERPRETER_CANDIDATES:
        if kind == "env":
            value = os.environ.get(spec)
            if value:
                yield Path(value).expanduser()
        elif kind == "vep_home":
            yield vep_home() / spec
        elif kind == "repo":
            if repo_root:
                yield Path(repo_root) / spec


def python_interpreter(repo_root: Optional[str | Path] = None):
    """`(path, "")` when one carries the stack, `("", why not)` otherwise.

    Never falls back to `sys.executable` or to `/usr/bin/python3`.
    """
    for candidate in interpreter_candidates(repo_root):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate), ""
    return "", _no_interpreter_message(repo_root)


def _no_interpreter_message(repo_root: Optional[str | Path] = None) -> str:
    """Every rung that was tried, by path, and how to build the one that counts.

    Naming only the last rung is what made this defect survive: the
    plugin reported `/usr/bin/python3` failing on an import, which is
    true and says nothing about where the interpreter should have been.
    """
    tried = [str(c) for c in interpreter_candidates(repo_root)]
    lines = [
        "No Python interpreter carrying the ML stack was found.",
        "Tried, in order:",
    ]
    lines += [f"    {path}" for path in tried] or ["    (nothing - no rung applied)"]
    lines.append(
        f"`run_pipeline.py` imports whisperx, mlx_vlm and torch through its "
        f"steps, so a stock interpreter is not a fallback - it dies inside a "
        f"step rather than here.\n"
        f"Build the durable one ONCE PER MACHINE at "
        f"{vep_home() / DURABLE_VENV_DIRNAME}; docs/ML_ENVIRONMENT.md is the "
        f"procedure.")
    return "\n".join(lines)


# ── the query endpoint: the ladder as data for callers that cannot import it
#
# `js/interpreter.js` (Resolve's Electron host) and
# `scripts/full_suite_gate.sh` both reach the ladder this way.  The query
# vehicle may be ANY `python3` - including a stock one that could never
# run the pipeline - because answering it evaluates only stdlib path
# predicates (`is_file`, `X_OK`) whose answer does not depend on the
# asker's version.  The asker NEVER runs pipeline code; the bridge is
# launched only with the answered path, and an unanswered query refuses.
# That is what keeps the bootstrap from being a fallback wearing a query's
# clothes.
#
# The constraint this imposes is real and is pinned by
# `tests/test_plugin_interpreter_query.py`: this module must stay
# parseable by the oldest stock interpreter a machine may carry (grammar
# `ast.parse(..., feature_version=(3, 9))`, stdlib use to long-stable
# calls), or the vehicle cannot even ask.

def interpreter_report(repo_root: Optional[str | Path] = None) -> dict:
    """The ladder's answer as JSON-serializable data.

    `{"python": path or "", "error": "" or why-not, "tried": [...]}`.
    A transport success with an empty `python` is the ladder finding
    nothing - the caller refuses with `error` VERBATIM, so the wording
    has exactly one source.
    """
    path, why_not = python_interpreter(repo_root)
    return {
        "python": path,
        "error": why_not,
        "tried": [str(c) for c in interpreter_candidates(repo_root)],
    }


def main(argv=None) -> int:
    """`python -m library.tools.shared_environment --resolve-interpreter
    [--repo-root <checkout>]`.

    Prints one JSON `interpreter_report()` on stdout.  Exit 0 means the
    query was answered - even when the answer is "nothing found" - so a
    nonzero exit always means the vehicle itself failed, never the ladder.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Ask the interpreter ladder; print the answer as JSON.")
    parser.add_argument("--resolve-interpreter", action="store_true",
                        help="print interpreter_report() as JSON")
    parser.add_argument("--repo-root", default=None,
                        help="the checkout the `repo` rung is tried against")
    args = parser.parse_args(argv)
    if not args.resolve_interpreter:
        parser.print_usage()
        return 2
    print(json.dumps(interpreter_report(args.repo_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
