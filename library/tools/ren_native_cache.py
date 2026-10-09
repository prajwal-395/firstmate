"""Per-tree builds of the `ren_native` extension, in a content-addressed cache.

One shared venv plus one freshness record per checkout meant every
checkout rebuilt one artifact: `native_refresh.ensure_current` compared
its own checkout's sources against its own checkout's sidecar, then
installed into the machine's shared venv - invalidating every other
checkout - and crashed before recording (the packaged-copy block ran
`joinpath(*None)` for the binary-less `ren_native` part), so the next
safe point rebuilt again. Measured 2026-10-08 in the shared broker log:
551 `ren_native is stale - rebuilding` lines and zero completions, each
attempt holding the machine heavy-work slot for a full `maturin`
compile while other lanes waited on it.

The cache ends the fight: builds live once per MACHINE under
`vep_home()/native/ren-native/<source-fingerprint>/`, keyed by the one
fingerprint in `library/tools/ren_native_source.py` - the same shape as
the Node store in `shared_environment` (keyed by lockfile), because it
is the same answer: two checkouts whose sources agree share one build
by construction, and two whose sources differ can never see each
other's. The importer (`library/tools/edit_backend.py`) selects the
entry matching its own tree; a shared-venv copy whose baked
fingerprint disagrees never triggers a rebuild - only the absence of
THIS tree's entry builds, and it builds from THIS tree's sources.

Layout of one entry: `ren_native*.so` (the built extension, ABI-tagged
by maturin) beside a `fingerprint` file recording what it was built
from. `select()` loads the `.so` for this tree's fingerprint and
refuses it unless the baked fingerprint matches - the entry directory
names the claim, the binary carries the proof, and the two must agree.
A whole entry is one tree's build; concurrent builders of the same
entry serialize on the heavy-work lock with a recheck first (the
`native_refresh` pattern), so the second finds the first's build.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

CACHE_ENV = "PIPELINE_REN_NATIVE_CACHE"
"""Names the cache outright: the escape hatch for tests and for a
machine whose layout this module did not anticipate."""

CACHE_DIRNAMES = ("native", "ren-native")
"""The cache location under `vep_home()`: machine-level binaries by tree."""

FINGERPRINT_FILENAME = "fingerprint"
"""The file inside an entry recording the source fingerprint it was
built from. The entry directory already names it; the file is what
`native_refresh` records, so a half-written entry (a `.so` with no
record) still reads as unbuilt."""

EXTENSION_GLOB = "ren_native*.so"
"""The built extension inside an entry, ABI-tagged by maturin
(`ren_native.cpython-312-darwin.so`)."""


def cache_dir(cache: str | Path | None = None) -> Path:
    """Where this machine's per-tree `ren_native` builds live.

    `cache` names it outright (tests); `PIPELINE_REN_NATIVE_CACHE`
    next; otherwise `vep_home()/native/ren-native`, beside the venv
    and the Node store rather than inside any checkout - a directory
    inside a worktree dies with that lane, and lanes are disposable.
    """
    if cache is not None:
        return Path(cache).expanduser()
    explicit = os.environ.get(CACHE_ENV)
    if explicit:
        return Path(explicit).expanduser()
    from library.tools.shared_environment import vep_home

    return vep_home().joinpath(*CACHE_DIRNAMES)


def entry_dir(fingerprint: str, cache: str | Path | None = None) -> Path:
    """The entry this source fingerprint's build belongs in."""
    return cache_dir(cache) / fingerprint


def extension_file(entry: str | Path) -> Path | None:
    """The built `.so` inside `entry`, or None where none is built."""
    candidates = sorted(Path(entry).glob(EXTENSION_GLOB))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def fingerprint_file(entry: str | Path) -> Path:
    return Path(entry) / FINGERPRINT_FILENAME


def read_recorded(entry: str | Path) -> str | None:
    """The fingerprint recorded for `entry` at its last build, if any."""
    try:
        return fingerprint_file(entry).read_text(
            encoding="utf-8").strip() or None
    except OSError:
        return None


def write_recorded(entry: str | Path, fingerprint: str) -> None:
    """Record `fingerprint` for `entry` (a build just succeeded)."""
    path = fingerprint_file(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fingerprint + "\n", encoding="utf-8")


def load_extension(path: str | Path) -> ModuleType:
    """Load the extension at `path` as the `ren_native` module.

    The `.so`'s init function is `PyInit_ren_native`, so the module
    MUST be named `ren_native` - a process serves one tree, and that
    tree's entry is the only `.so` it ever loads, so one name is
    correct. A caller that refuses the loaded module must evict it
    from `sys.modules` (see `select`), or a later plain
    `import ren_native` would inherit the refused build.
    """
    name = "ren_native"
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ren_native from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module


def select(root: str | Path, *, cache: str | Path | None = None,
           load: Callable[[Path], ModuleType] | None = None,
           ) -> ModuleType | None:
    """This tree's cached `ren_native` build, or None.

    None where the tree holds no native sources (nothing to tie a
    build to), where this tree's entry holds no built extension, where
    the build fails to load, or where its baked-in source fingerprint
    does not match this tree - a build for another tree is never
    returned, and a refused load never lingers in `sys.modules`. In
    every None case the Python edit backend runs; the switch is an
    optimisation, never a prerequisite.
    """
    from library.tools import ren_native_source

    if root is None:
        return None
    fingerprint = ren_native_source.source_fingerprint(root)
    if fingerprint is None:
        return None
    entry = entry_dir(fingerprint, cache)
    path = extension_file(entry)
    if path is None:
        return None
    if read_recorded(entry) != fingerprint:
        return None
    loader = load or load_extension
    try:
        module = loader(path)
    except Exception:  # noqa: BLE001 - an unloadable build is no build
        return None
    baked = getattr(module, "source_fingerprint", lambda: None)()
    if baked != fingerprint:
        if sys.modules.get("ren_native") is module:
            del sys.modules["ren_native"]
        return None
    return module
