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
import sys
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
# Same defect, one layer up, and found the same day.  Callers
# assumed the interpreter was `<checkout>/.venv/bin/python3`,
# which has none of the ML stack on machines without one, so
# the failure surfaced inside a step's import rather than saying why.
# `scripts/full_suite_gate.sh` takes `FULL_SUITE_GATE_PYTHON` or the
# ambient `python3`.
#
# Measured 2026-09-14: NO checkout on the build machine has a `.venv`.
# The working environment is the durable one at
# `~/.local/share/vep/venv-py312`, which `docs/ML_ENVIRONMENT.md` put
# outside every checkout deliberately.

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

It is data because the ladder has a caller that cannot import this
module and must still obey it - `scripts/full_suite_gate.sh`
ASKS it, through `interpreter_report()` below, rather than carrying
its own copy. A ladder duplicated in prose would have drifted the
first time it changed. A caller that queries cannot drift, because
there is nothing on its side to drift.

There is NO stock-interpreter rung, and there must not be one, for
the reason a fallback fails:
`run_pipeline.py` imports whisperx, mlx_vlm and torch through its steps,
so a stock interpreter does not fail at launch where the message would
be read - it fails forty seconds in, inside a step, with a traceback
about a package nobody mentioned.
"""


def interpreter_candidates(repo_root: Optional[str | Path] = None):
    """`INTERPRETER_CANDIDATES` resolved to real paths, in order.

    A rung whose environment variable is unset yields nothing; a rung
    that needs a checkout and has none is skipped.
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

    Naming only the last rung is what let this defect survive: a report
    of a stock interpreter failing on an import is true and says
    nothing about where the interpreter should have been.
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
# `bin/vep` and `scripts/full_suite_gate.sh` both reach the ladder this
# way.  The query vehicle may be ANY `python3` - including a stock one
# that could never run the pipeline - because answering it evaluates
# only stdlib path predicates (`is_file`, `X_OK`) whose answer does not
# depend on the asker's version.  The asker NEVER runs pipeline code;
# the resolved interpreter is launched only with the answered path, and
# an unanswered query refuses.  That is what keeps the bootstrap from
# being a fallback wearing a query's clothes.
#
# The constraint this imposes is real: this module must stay parseable
# by the oldest stock interpreter a machine may carry (grammar
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


# ── the MFA half: the forced aligner for Voz transcription ──────────
#
# A third shared dependency, discovered the same way as the two above:
# one location per MACHINE under `vep_home()`, never one per checkout,
# and absence REFUSES with the command that fixes it rather than a
# stack trace. `library/tools/mfa_align.py` is the only runtime reader;
# `scripts/install_mfa.sh` is the one way to fill it.
#
# Why these paths and not others. The measurement task
# (`data/vep-try-mfa-instead-of-wav2vec2/report.md`) installed into
# `~/.local/share/vep/micromamba` - which is `vep_home()/micromamba`,
# the root this module already owns - and MFA keeps its side-state
# (config, model cache, temp DB) in `~/Documents/MFA`, which is MFA's
# own default rather than a choice of ours. Both are overridable
# outright, because a lane whose layout this module did not anticipate
# is a real machine and not a misconfiguration.

MICROMAMBA_DIRNAME = "micromamba"
"""The self-contained micromamba root under `vep_home()`.

No `conda init`, no shell profile touched - every command uses the
explicit binary path, per the measurement report.
"""

MFA_ENV_NAME = "mfa"
"""The conda env inside the micromamba root that carries MFA."""

MFA_BINARY_SUFFIX = os.path.join("envs", MFA_ENV_NAME, "bin", "mfa")

MFA_ACOUSTIC_MODEL = "english_us_arpa"
"""The acoustic model `mfa align` is run with. Spelled once, here."""

MFA_ACOUSTIC_MODEL_VERSION = "v3.0.0"
"""The acoustic model version the boundaries were measured on. PINNED.

An unpinned `english_us_arpa` floats, and the model is what places
the word boundaries - so the install script downloads exactly this,
and a second version beside it is a different instrument until it is
measured as one.
"""

MFA_DICTIONARY = "english_us_arpa"

MFA_INSTALL_SCRIPT = "scripts/install_mfa.sh"
"""The one way to fill the MFA environment. Named in every refusal."""


class MfaEnvironmentMissing(RuntimeError):
    """The MFA environment is not reachable from this machine.

    Raised rather than left for the align subprocess to report as a
    missing binary several layers down. The message carries the
    command that fixes it. `library/tools/mfa_align.py` treats this as a
    decline. There is no fallback aligner, so transcription cannot
    continue without MFA.
    """


def micromamba_root() -> Path:
    """The micromamba root. `PIPELINE_MICROMAMBA_ROOT` names it outright."""
    explicit = os.environ.get("PIPELINE_MICROMAMBA_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    return vep_home() / MICROMAMBA_DIRNAME


def mfa_binary(directory: Optional[str | Path] = None) -> Path:
    """The `mfa` executable this machine aligns with.

    `PIPELINE_MFA_BINARY` wins outright - the escape hatch for a
    machine whose layout this module did not anticipate. Otherwise it
    is `<micromamba_root>/envs/mfa/bin/mfa`, because that is where
    `scripts/install_mfa.sh` puts it. `directory` is accepted and
    ignored, so this reads like the Node half's queries.
    """
    _ = directory
    explicit = os.environ.get("PIPELINE_MFA_BINARY")
    if explicit:
        return Path(explicit).expanduser()
    return micromamba_root() / MFA_BINARY_SUFFIX


def mfa_models_dir() -> Path:
    """Where MFA keeps its config, model cache and temp DB.

    `PIPELINE_MFA_MODELS` names it outright. Otherwise MFA's own
    default, `~/Documents/MFA` - that default is MFA's choice rather
    than a path of ours, which is why it is read here and not moved.
    """
    explicit = os.environ.get("PIPELINE_MFA_MODELS")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / "Documents" / "MFA"


def mfa_available() -> tuple:
    """`(usable, detail)` - whether this machine can align with MFA."""
    binary = mfa_binary()
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        return False, mfa_missing_message()
    models = mfa_models_dir()
    if not models.is_dir():
        return False, mfa_missing_message()
    return True, f"MFA {MFA_ACOUSTIC_MODEL} v{MFA_ACOUSTIC_MODEL_VERSION} via {binary}"


def mfa_missing_message() -> str:
    """Why MFA is not reachable, and the command that fixes it."""
    lines = [
        f"The MFA forced aligner is not reachable "
        f"(binary {mfa_binary()}, models {mfa_models_dir()}).",
        f"Install once per machine:\n"
        f"    {MFA_INSTALL_SCRIPT}\n"
        f"Voz transcription cannot continue without MFA; there is no "
        f"wav2vec2 fallback (see `library/tools/mfa_align.py`).",
    ]
    return "\n".join(lines)


def require_mfa() -> Path:
    """Return the `mfa` executable, or REFUSE by name."""
    usable, detail = mfa_available()
    if not usable:
        raise MfaEnvironmentMissing(detail)
    return mfa_binary()


# ── the DeepFilterNet half: plan-requested dialogue cleanup ──────
#
# A fourth shared dependency, discovered the same way as the three
# above: one location per MACHINE under `vep_home()`, never one per
# checkout, and absence REFUSES with the command that fixes it rather
# than a stack trace. `library/tools/dialogue_cleanup.py` is the only
# runtime reader; `scripts/install_deepfilternet.sh` is the one way to
# fill it.
#
# Why a binary and not a pip install. Fidelity rung R5d measured
# DeepFilterNet3 against Resolve Voice Isolation on the captain's own
# dialogue (4-6 dB floor drop, words intact) through a scratch Python
# 3.11 venv - because the shared ML venv is Python 3.12 with numpy
# 2.x, `deepfilternet` pins `numpy>=1.22,<2.0`, and `deepfilterlib`
# 0.5.6 ships no cp312 macOS-arm64 wheel. Pinning it would downgrade
# numpy fleet-wide and break the torch pair every lane runs on, so it
# is NOT in `requirements.txt` by measurement, not by omission. The
# engine instead invokes the project's own prebuilt `deep-filter`
# Rust binary for Apple Silicon, which carries the model runtime and
# needs no Python at all. Licence: MIT OR Apache-2.0 (LICENSE-MIT and
# LICENSE-APACHE in the upstream repository, Rikorose/DeepFilterNet).

DEEPFILTER_VERSION = "0.5.6"
"""The DeepFilterNet release the floor measurements were taken on. PINNED.

The binary is a static Rust build, so unlike the MFA acoustic model
there is no floating tag to guard against - but a second version
beside it is a different instrument until it is measured as one, so
the install script downloads exactly this.
"""

DEEPFILTER_RELEASE_TAG = "v0.5.6"
"""The upstream release tag `DEEPFILTER_VERSION` ships under."""

DEEPFILTER_ASSET = "deep-filter-0.5.6-aarch64-apple-darwin"
"""The prebuilt binary asset for this machine (Apple Silicon)."""

DEEPFILTER_DOWNLOAD_URL = (
    "https://github.com/Rikorose/DeepFilterNet/releases/download/"
    f"{DEEPFILTER_RELEASE_TAG}/{DEEPFILTER_ASSET}"
)
"""Where the one install script fetches the binary from. Spelled once, here."""

DEEPFILTER_BINARY_DIRNAME = "bin"
"""The directory under `vep_home()` that holds machine-level binaries."""

DEEPFILTER_BINARY_NAME = "deep-filter"
"""The executable name, matching upstream and the `df` fallback's PATH rung."""

DEEPFILTER_INSTALL_SCRIPT = "scripts/install_deepfilternet.sh"
"""The one way to fill the DeepFilterNet binary. Named in every refusal."""


class DeepFilterEnvironmentMissing(RuntimeError):
    """The DeepFilterNet binary is not reachable on this machine.

    Raised rather than left for the staging subprocess to report as a
    missing binary several layers down. The message carries the
    command that fixes it. The cleanup treats this as a REFUSAL BY
    NAME of the deepfilternet tool, never a skip - see
    `library/tools/dialogue_cleanup.py`.
    """


def deepfilter_binary(directory: Optional[str | Path] = None) -> Path:
    """The `deep-filter` executable this machine cleans dialogue with.

    `PIPELINE_DEEPFILTER_BINARY` wins outright - the escape hatch for
    a machine whose layout this module did not anticipate, set in the
    per-user config (`~/.config/ren/config.env`) like every other
    external path. Otherwise it is `<vep_home>/bin/deep-filter`,
    because that is where `scripts/install_deepfilternet.sh` puts it.
    `directory` is accepted and ignored, so this reads like the MFA
    half's queries.
    """
    _ = directory
    explicit = os.environ.get("PIPELINE_DEEPFILTER_BINARY")
    if explicit:
        return Path(explicit).expanduser()
    return vep_home() / DEEPFILTER_BINARY_DIRNAME / DEEPFILTER_BINARY_NAME


def deepfilter_available() -> tuple:
    """`(usable, detail)` - whether this machine can clean with DeepFilterNet."""
    binary = deepfilter_binary()
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        return False, deepfilter_missing_message()
    return True, f"DeepFilterNet {DEEPFILTER_VERSION} via {binary}"


def deepfilter_missing_message() -> str:
    """Why DeepFilterNet is not reachable, and the command that fixes it."""
    lines = [
        f"The DeepFilterNet binary is not reachable "
        f"({deepfilter_binary()}).",
        f"Install once per machine:\n"
        f"    {DEEPFILTER_INSTALL_SCRIPT}\n"
        f"A machine without it still plans: a deepfilternet request "
        f"refuses by name and the model re-plans with voice_isolation "
        f"(the dialogue_cleanup tool module owns that refusal).",
    ]
    return "\n".join(lines)


def require_deepfilter() -> Path:
    """Return the `deep-filter` executable, or REFUSE by name."""
    usable, detail = deepfilter_available()
    if not usable:
        raise DeepFilterEnvironmentMissing(detail)
    return deepfilter_binary()


# ── the sound-events half: the PANNs checkpoint ─────────────────────
#
# A fifth shared dependency, discovered the same way as the four
# above: one location per MACHINE under `vep_home()`, never one per
# checkout, and absence REFUSES with the command that fixes it rather
# than a stack trace. `library/tools/analysis/sound_event_pipeline.py`
# is the only runtime reader; `scripts/install_panns.sh` is the one way
# to fill it.
#
# Why a checkpoint file and not just a pip install. `panns-inference`
# and `torchlibrosa` ARE pip packages (both MIT) and live in the
# shared ML venv beside torch 2.8 - they resolved cleanly against the
# numpy 2.5.3 / torch 2.8 stack with nothing else moving, so unlike
# DeepFilterNet there was no reason for an isolated tool location.
# The 327 MB weights are data, not code: they live once per machine
# under `vep_home()/models/panns/`, verified by md5 at install.
# Licence: CC-BY-4.0 (the Zenodo record 3987831 licence, commercial
# use with attribution - see the pipeline module).

PANNS_CHECKPOINT_NAME = "Cnn14_DecisionLevelMax_mAP=0.385.pth"
"""The PANNs weights the event measurements were taken on. PINNED.

Frame-level sound-event detection over the AudioSet vocabulary at
100 frames/s. A second checkpoint beside it is a different instrument
until it is measured as one, so the install script fetches exactly
this.
"""

PANNS_CHECKPOINT_MD5 = "70539c43c18b6a289b3199c503a82c5a"
"""The Zenodo md5 of `PANNS_CHECKPOINT_NAME`, verified at install."""

PANNS_CHECKPOINT_SIZE = 327428481
"""The Zenodo byte size of `PANNS_CHECKPOINT_NAME`."""

PANNS_DOWNLOAD_URL = (
    "https://zenodo.org/api/records/3987831/files/"
    "Cnn14_DecisionLevelMax_mAP%3D0.385.pth/content"
)
"""Where the one install script fetches the checkpoint from. Spelled once, here."""

PANNS_MODELS_DIRNAME = "models/panns"
"""The directory under `vep_home()` that holds the event checkpoint."""

PANNS_INSTALL_SCRIPT = "scripts/install_panns.sh"
"""The one way to fill the PANNs checkpoint. Named in every refusal."""


class PannsEnvironmentMissing(RuntimeError):
    """The PANNs checkpoint is not reachable on this machine.

    Raised rather than left for torch to report as a missing file
    several layers down. The message carries the command that fixes
    it. The measurement treats this as UNMEASURED with the reason -
    an event anchor on an unmeasured clip refuses by name, never a
    skip - see `library/tools/analysis/sound_event_pipeline.py`.
    """


def panns_checkpoint(directory: Optional[str | Path] = None) -> Path:
    """The Cnn14 DecisionLevelMax checkpoint this machine measures with.

    `PIPELINE_PANNS_CHECKPOINT` wins outright - the escape hatch for
    a machine whose layout this module did not anticipate, set in the
    per-user config (`~/.config/ren/config.env`) like every other
    external path. Otherwise it is
    `<vep_home>/models/panns/<PANNS_CHECKPOINT_NAME>`, because that is
    where `scripts/install_panns.sh` puts it. `directory` is accepted
    and ignored, so this reads like the halves above.
    """
    _ = directory
    explicit = os.environ.get("PIPELINE_PANNS_CHECKPOINT")
    if explicit:
        return Path(explicit).expanduser()
    return vep_home() / PANNS_MODELS_DIRNAME / PANNS_CHECKPOINT_NAME


def panns_available() -> tuple:
    """`(usable, detail)` - whether this machine can measure events."""
    checkpoint = panns_checkpoint()
    if not (checkpoint.is_file()
            and checkpoint.stat().st_size >= 3e8):
        return False, panns_missing_message()
    return True, f"PANNs {PANNS_CHECKPOINT_NAME} via {checkpoint}"


def panns_missing_message() -> str:
    """Why PANNs is not reachable, and the command that fixes it."""
    lines = [
        f"The PANNs checkpoint is not reachable "
        f"({panns_checkpoint()}).",
        f"Install once per machine:\n"
        f"    {PANNS_INSTALL_SCRIPT}\n"
        f"A machine without it still plans: a clip records "
        f"`sound_event_method: unmeasured` with the reason, and an "
        f"event anchor on it refuses by name (the sound_event_pipeline "
        f"module owns that record).",
    ]
    return "\n".join(lines)


def require_panns() -> Path:
    """Return the PANNs checkpoint, or REFUSE by name."""
    usable, detail = panns_available()
    if not usable:
        raise PannsEnvironmentMissing(detail)
    return panns_checkpoint()


# ── single-track diarization: the ECAPA speaker encoder ──────────────
#
# The fallback in `library/tools/single_track_diarization.py` diarizes
# one mixed track with open ECAPA embeddings rather than pyannote (gated
# weights, HF token - deliberately not ported; see the eval at
# `data/vep-single-track-diarization/eval/` in firstmate's home for the
# measured DER 0.02-0.09 vs 0.34-1.14 single-label baselines). The pip
# half (`speechbrain`, MIT) lives in the shared ML venv via
# requirements.txt. The weights are data, not code: they live once per
# machine under `vep_home()/models/ecapa/`, fetched at the PINNED
# HuggingFace revision by `scripts/install_ecapa.sh`. Public repo, no
# token. The recipe is speechbrain's (Apache-2.0); the encoder is trained
# on VoxCeleb, whose dataset terms govern the weights - check them for
# the use at hand (see `library/tools/single_track_diarization.py`).

ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
"""The public speaker-encoder repo the fallback embeds with. No token."""

ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
"""The HF commit the eval measured (2026-09-30). PINNED: a floating model
is a different instrument until it is measured as one."""

ECAPA_MODELS_DIRNAME = "models/ecapa"
"""The directory under `vep_home()` holding the encoder checkout."""

ECAPA_MODEL_DIRNAME = "spkrec-ecapa-voxceleb"
"""The checkout directory under `ECAPA_MODELS_DIRNAME`."""

ECAPA_INSTALL_SCRIPT = "scripts/install_ecapa.sh"
"""The one way to fill the ECAPA checkout. Named in every refusal."""

ECAPA_REQUIRED_FILES = ("hyperparams.yaml", "embedding_model.ckpt")
"""What makes a checkout usable: the recipe and the encoder weights."""


class EcapaEnvironmentMissing(RuntimeError):
    """The ECAPA checkout is not reachable on this machine.

    Raised rather than left for speechbrain to report as a download
    failure several layers down. The message carries the command that
    fixes it. The transcript treats this as single-label output with
    the reason recorded - today's behavior, said aloud - never a skip.
    """


def ecapa_model_dir(explicit: Optional[str | Path] = None) -> Path:
    """The ECAPA checkout this machine diarizes with.

    `PIPELINE_ECAPA_MODEL_DIR` wins outright - the escape hatch for a
    machine whose layout this module did not anticipate, set in the
    per-user config (`~/.config/ren/config.env`) like every other
    external path. `explicit` is the caller-supplied override (tests,
    eval harnesses). Otherwise it is
    `<vep_home>/models/ecapa/spkrec-ecapa-voxceleb`, because that is
    where `scripts/install_ecapa.sh` puts it at `ECAPA_REVISION`.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get("PIPELINE_ECAPA_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return vep_home() / ECAPA_MODELS_DIRNAME / ECAPA_MODEL_DIRNAME


def ecapa_available(explicit: Optional[str | Path] = None) -> tuple:
    """`(usable, detail)` - whether this machine can diarize one track."""
    directory = ecapa_model_dir(explicit)
    missing = [name for name in ECAPA_REQUIRED_FILES
               if not (directory / name).is_file()]
    if missing:
        return False, ecapa_missing_message(explicit)
    return True, f"ECAPA {ECAPA_REPO}@{ECAPA_REVISION[:12]} via {directory}"


def ecapa_missing_message(explicit: Optional[str | Path] = None) -> str:
    """Why ECAPA is not reachable, and the command that fixes it."""
    lines = [
        f"The ECAPA checkout is not reachable "
        f"({ecapa_model_dir(explicit)}).",
        f"Install once per machine:\n"
        f"    {ECAPA_INSTALL_SCRIPT}\n"
        f"A machine without it still transcribes: a single-track timeline "
        f"records single-label output with this reason, which is today's "
        f"behavior said aloud rather than a skip.",
    ]
    return "\n".join(lines)


def require_ecapa(explicit: Optional[str | Path] = None) -> Path:
    """Return the ECAPA checkout, or REFUSE by name."""
    usable, detail = ecapa_available(explicit)
    if not usable:
        raise EcapaEnvironmentMissing(detail)
    return ecapa_model_dir(explicit)


# ── person-entity store: the ArcFace face encoder ─────────────────────
#
# `library/tools/person_entity.py` (the M3b entity lane) measures face
# identity with insightface's `buffalo_l` bundle (detector + ArcFace
# recognizer in one pass) rather than Apple Vision, which has no public
# face-identity request (measured in the video-intelligence scout,
# `data/vep-video-intelligence-entities-and-search/report.md` section 4).
# The false-accept study at `data/vep-person-entity-store/eval/results.md`
# (83 real faces, 2 cameras, profile and eyes-closed frames included)
# measured FAR=0/FRR=0 at cosine >= `person_entity.FACE_MATCH_THRESHOLD`.
# The pip half (`insightface`, `onnxruntime`, both MIT/Apache) lives in
# the shared ML venv via requirements.txt. The weights (~281 MB) are
# data, not code, and insightface already keeps them once per machine in
# its own cache (`~/.insightface/models/buffalo_l/`) - this module reads
# that location rather than inventing a second one under `vep_home()`,
# so a machine the scout already ran on (as this one was) needs no
# re-fetch. `scripts/install_insightface.sh` fills it where absent.

INSIGHTFACE_ROOT_ENV = "PIPELINE_INSIGHTFACE_MODEL_DIR"
"""Names the insightface model root outright (insightface's own `root=`
kwarg), for a machine whose cache lives somewhere this module did not
anticipate."""

INSIGHTFACE_PACK_NAME = "buffalo_l"
"""The insightface model pack this machine identifies faces with."""

INSIGHTFACE_INSTALL_SCRIPT = "scripts/install_insightface.sh"
"""The one way to fill the buffalo_l checkout. Named in every refusal."""

INSIGHTFACE_REQUIRED_FILES = (
    "det_10g.onnx", "w600k_r50.onnx",
)
"""What makes a checkout usable: the detector and the ArcFace recognizer.
buffalo_l also ships landmark/attribute models this module never reads -
identity comes from the recognizer embedding only (AGENTS.md: never from
sex/age attributes), so their absence does not fail this check."""


class InsightfaceEnvironmentMissing(RuntimeError):
    """The buffalo_l checkout is not reachable on this machine.

    Raised rather than left for insightface to report a download failure
    mid-build. The message carries the command that fixes it.
    """


def insightface_model_dir(explicit: Optional[str | Path] = None) -> Path:
    """The insightface model root this machine identifies faces with
    (the `root=` insightface's own `FaceAnalysis` expects).

    `PIPELINE_INSIGHTFACE_MODEL_DIR` wins outright, the same escape
    hatch as `ecapa_model_dir`. Otherwise insightface's own default
    cache, `~/.insightface`, where `FaceAnalysis(name="buffalo_l")`
    already downloads to on first use.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get(INSIGHTFACE_ROOT_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".insightface"


def _insightface_pack_dir(explicit: Optional[str | Path] = None) -> Path:
    return insightface_model_dir(explicit) / "models" / INSIGHTFACE_PACK_NAME


def insightface_available(explicit: Optional[str | Path] = None) -> tuple:
    """`(usable, detail)` - whether this machine can embed a face.

    Both halves: the weights on disk AND the `insightface` package in
    THIS interpreter. Weights alone answered True on a venv without the
    package, and every clip of a geo-podcast build then failed on a
    bare `ModuleNotFoundError` instead of this refusal.
    """
    directory = _insightface_pack_dir(explicit)
    missing = [name for name in INSIGHTFACE_REQUIRED_FILES
               if not (directory / name).is_file()]
    if missing:
        return False, insightface_missing_message(explicit)
    import importlib.util
    for package in ("insightface", "onnxruntime"):
        if importlib.util.find_spec(package) is None:
            return False, (
                f"The insightface {INSIGHTFACE_PACK_NAME} weights are at "
                f"{directory}, but this interpreter ({sys.executable}) "
                f"cannot import `{package}`.\n"
                f"Install the declared requirement into it:\n"
                f"    uv pip install --python {sys.executable} "
                f"-r requirements.txt")
    return True, f"insightface {INSIGHTFACE_PACK_NAME} via {directory}"


def insightface_missing_message(explicit: Optional[str | Path] = None) -> str:
    """Why buffalo_l is not reachable, and the command that fixes it."""
    return (
        f"The insightface {INSIGHTFACE_PACK_NAME} checkout is not "
        f"reachable ({_insightface_pack_dir(explicit)}).\n"
        f"Install once per machine:\n"
        f"    {INSIGHTFACE_INSTALL_SCRIPT}\n"
        f"A machine without it builds no M3b identity record: the entity "
        f"lane refuses with InsightfaceEnvironmentMissing and the project "
        f"is left without face identity, said aloud rather than guessed "
        f"at from a default.")


def require_insightface(explicit: Optional[str | Path] = None) -> Path:
    """Return the insightface model root, or REFUSE by name."""
    usable, detail = insightface_available(explicit)
    if not usable:
        raise InsightfaceEnvironmentMissing(detail)
    return insightface_model_dir(explicit)


# ── the BUILD half: what a reel build needs in its own interpreter ───
#
# Four instances, all on 2026-09-10/11, each costing a lane a failed
# build and a diagnosis - and each lane fixed it LOCALLY with its own
# venv, every one missing something DIFFERENT:
#
#   1. a system cv2 whose `CascadeClassifier` was gone answered None
#      from the face probe, so every punch-in went unaimed;
#   2. a system cv2 5.0 refused the same aim, LOUD this time
#      (`SubjectProbeUnavailable`);
#   3. a purpose-built cv2 4.12 venv missing `jsonschema` failed the
#      very next attempt at the same build, inside
#      `manifest_validator`.
#
# (The fourth - Remotion `node_modules` absent - already has an owner
# in the Node half above.) The tell is that nothing DECLARED what a
# build requires, so every attempt rediscovered a different subset. A
# missing detector, cascade file or library is therefore ONE CLEAR
# MESSAGE BEFORE THE BUILD STARTS, naming what is missing and what
# would supply it - not a refusal three steps in and not a silent gap.
#
# The verdict on the detector belongs to its loader, not to this
# module: `subject_framing.load_face_cascade` is what the build really
# calls, so `face_detector_usable` asks IT rather than re-deriving the
# answer. What lives here is the REQUIREMENT - the pin, the message,
# and the combined pre-build check - because the value of the halves
# above is that there is one owner, and a second mechanism beside this
# module recreates the problem this row is about.
#
# Probing loads cv2, which costs about a second the first time. That
# is the point rather than a cost to avoid: the check proves the exact
# call the build is about to make, instead of asserting it.

FACE_CASCADE_FILENAME = "haarcascade_frontalface_default.xml"
"""The cascade file the reels punch-in aims with, from `cv2.data`."""

FACE_DETECTOR_PIN = "opencv-python>=4.8,<5"
"""The interpreter pin that ships the Haar cascade.
`requirements.txt` carries it twice - `opencv-python-headless` provides
the SAME `cv2` module and shadows a correct install when unpinned - so
the message names the bound, not just the package."""

BUILD_VENV_DOC = "docs/ML_ENVIRONMENT.md"
"""The procedure that builds a complete interpreter. Named in every
refusal, because "install opencv" into a disposable lane venv is the
local fix that produced four different ad-hoc environments."""

REQUIREMENTS_FILE = "requirements.txt"
"""The declared library set a purpose-built venv is completed from."""


class FaceDetectorMissing(RuntimeError):
    """This interpreter cannot load the Haar face detector.

    Raised rather than left for the probe to answer None (an unaimed
    punch-in) or raise mid-build (`SubjectProbeUnavailable`, after the
    derivation was paid for). The message carries the pin that fixes
    it.
    """


class ReelBuildEnvironmentMissing(RuntimeError):
    """This interpreter cannot run a reel build.

    The combined pre-build refusal: every missing detector, cascade
    file and library in ONE message, before anything is derived. A
    refusal per missing thing would send the operator through the
    build once per gap; there were four gaps in two days.
    """


def face_detector_usable() -> bool:
    """Can THIS interpreter load the Haar cascade the build aims with?

    Asked of the loader the build really calls
    (`subject_framing.load_face_cascade`) - imported lazily, so asking
    never moves the import cost onto a caller that only needed the
    Node half, and so this module keeps its stdlib-only import shape
    for the stock-interpreter query below.
    """
    try:
        from library.tools import subject_framing
    except ImportError:  # imported as `tools.*` from inside library/
        try:
            from tools import subject_framing
        except ImportError:
            return False
    try:
        return subject_framing.load_face_cascade() is not None
    except Exception:
        return False


def _face_detector_hint() -> str:
    """Which half of the detector is missing, as an observed fact.

    Read only after `face_detector_usable` answered False, so this
    never decides the verdict - it names the cause. Each branch is
    phrased from attributes observed here, never inferred, with a
    fallback for a combination this list did not anticipate.
    """
    try:
        import cv2
    except ImportError:
        return "cv2 is not installed in this interpreter"
    version = str(getattr(cv2, "__version__", "unknown"))
    if getattr(cv2, "CascadeClassifier", None) is None:
        return (
            f"cv2 {version} is installed but has no CascadeClassifier "
            f"(OpenCV 5 dropped Haar cascades entirely)")
    data = getattr(cv2, "data", None)
    haar_dir = getattr(data, "haarcascades", None) if data else None
    if not haar_dir:
        return (f"cv2 {version} has a CascadeClassifier but exposes no "
                f"haarcascades directory")
    candidate = os.path.join(haar_dir, FACE_CASCADE_FILENAME)
    if not os.path.exists(candidate):
        return (f"cv2 {version} exposes {haar_dir} but "
                f"{FACE_CASCADE_FILENAME} is not in it")
    return (f"cv2 {version} carries {candidate} but it fails to load "
            f"(the classifier comes back empty)")


def face_detector_available() -> tuple:
    """`(usable, detail)` - whether this interpreter can aim a punch-in."""
    if face_detector_usable():
        try:
            import cv2
            version = str(getattr(cv2, "__version__", "unknown"))
        except ImportError:  # pragma: no cover - agreed True above
            version = "unknown"
        return True, (f"Haar {FACE_CASCADE_FILENAME} loads "
                      f"(cv2 {version})")
    return False, face_detector_missing_message()


def face_detector_missing_message() -> str:
    """What is missing from the detector, and what would supply it."""
    return (
        f"No face detector in this interpreter: {_face_detector_hint()}. "
        f"The reels punch-in is aimed with the Haar cascade, and an "
        f"unaimed shot under the TV-frame look leaves black inside the "
        f"screen. Run the build under an interpreter carrying "
        f"{FACE_DETECTOR_PIN}, which ships the cascade - the durable "
        f"per-machine venv ({BUILD_VENV_DOC}) or PIPELINE_PYTHON pointed "
        f"at one that does.")


def require_face_detector():
    """Return True, or REFUSE naming the missing detector half."""
    if not face_detector_usable():
        raise FaceDetectorMissing(face_detector_missing_message())
    return True


REEL_BUILD_LIBRARIES: tuple = ("jsonschema", "yaml")
"""The third-party modules a reel build imports, DECLARED as data.

`jsonschema` killed a real build from inside `manifest_validator`;
`yaml` is read on every build through the project configuration and
the reel look. `requirements.txt` pins both; a purpose-built venv
completed from anything less is how the third instance happened. cv2
is deliberately NOT listed: its absence - and the worse case, its
presence without cascades - is owned above, where the message can
name which half is gone. Listing it here too would report one gap
twice.
"""


def missing_build_libraries() -> tuple:
    """The declared libraries THIS interpreter cannot import.

    `find_spec` rather than an import: answering must not execute
    module code, only report whether it is there to be executed.
    """
    import importlib.util

    missing = []
    for module in REEL_BUILD_LIBRARIES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return tuple(missing)


def build_libraries_present() -> bool:
    """Every declared library imports in THIS interpreter."""
    return not missing_build_libraries()


def build_libraries_missing_message() -> str:
    """Which libraries are missing, and what would supply them."""
    missing = missing_build_libraries()
    names = ", ".join(missing)
    return (
        f"This interpreter cannot import {names}, which the reel build "
        f"requires ({REQUIREMENTS_FILE} declares every one). Complete "
        f"the venv from that file - `pip install -r {REQUIREMENTS_FILE}` "
        f"- or build the durable per-machine one ({BUILD_VENV_DOC}) "
        f"rather than assembling another ad-hoc venv.")


def reel_build_environment_problems() -> list:
    """Every build-environment gap, each naming its own remedy."""
    problems = []
    if not face_detector_usable():
        problems.append(face_detector_missing_message())
    missing = missing_build_libraries()
    if missing:
        problems.append(build_libraries_missing_message())
    return problems


def reel_build_environment_available() -> tuple:
    """`(ready, detail)` - whether THIS interpreter can run a reel build.

    The pre-build check both surviving gaps share: the detector half
    and the library half, asked together so a missing renderer,
    detector or library is one clear message at the top.
    """
    problems = reel_build_environment_problems()
    if problems:
        return False, reel_build_environment_missing_message()
    return True, "reel build environment: face detector loads, " \
        + ", ".join(REEL_BUILD_LIBRARIES) + " importable"


def reel_build_environment_missing_message() -> str:
    """The one clear message: every gap, each with what supplies it."""
    import sys

    lines = [f"The reel build cannot start under {sys.executable}:"]
    lines += [f"  - {problem}"
              for problem in reel_build_environment_problems()]
    if len(lines) == 1:
        lines.append("  - (no gaps found - this message should not have "
                     "been built)")
    return "\n".join(lines)


def require_reel_build_environment() -> None:
    """REFUSE by name before the build starts, or pass silently."""
    if reel_build_environment_problems():
        raise ReelBuildEnvironmentMissing(
            reel_build_environment_missing_message())


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
