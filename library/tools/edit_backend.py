"""One switch for the edit algebra's computational backend.

Rust is the default. Set the environment variable to select Python::

    REN_EDIT_BACKEND=python

If the extension is missing, stale, or broken, the backend falls back to
Python rather than refusing to edit. `edit_algebra`, `edit_patch` and
`patch_algebra` route their native-supported computations (span, verify,
validate, compose, carry, conditions) through `rust()`. `edit_algebra.key`
keeps the typed Python key contract; selecting Python runs all Python
implementations unchanged.

A build that predates the tree it runs against is refused the same way.
The extension carries a fingerprint of the `native/` sources it was
built from (`ren_native.source_fingerprint()`, baked in by
`native/ren-core/build.rs`); `edit_backend.rust()` first selects the
per-tree build from the machine's content-addressed cache
(`library/tools/ren_native_cache.py`) and falls back to the shared
venv's copy only where that copy matches the running tree. On a
mismatch it falls back to Python and says so
once. `ren setup` and `ren upgrade` run the rebuild;
`ren doctor` names a stale one. The parity harness
(`tests/unit/resolve/test_ren_core_parity.py`) skips naming a stale
build rather than failing on an old binary, and still fails when a
fresh build genuinely disagrees.
"""

import os
import sys

BACKEND_ENV = "REN_EDIT_BACKEND"
RUST = "rust"
PYTHON = "python"

_ren_native = None
_ren_native_tried = False
_ren_native_said_stale = False


def use_rust() -> bool:
    """Whether the switch selects Rust; Rust is the default backend."""
    return os.environ.get(BACKEND_ENV, RUST).strip().lower() == RUST


def _engine_root():
    """The engine root, or None when it cannot be resolved."""
    from ren.engine_root import find_engine_root

    return find_engine_root()


def _tree_fingerprint():
    """The `native/` source fingerprint of the running engine, or None
    when the tree holds no `native/` sources to fingerprint against."""
    from library.tools.ren_native_source import source_fingerprint

    root = _engine_root()
    if root is None:
        return None
    return source_fingerprint(root)


def rust():
    """The ren_native module when the switch is on and a build matching
    this tree is found; else None.

    The import is attempted once; a failed import is remembered so a run
    without the extension does not retry (and fail) on every call.

    Selection is per-tree: the machine's content-addressed cache
    (`library/tools/ren_native_cache.py`) holds one build per source
    fingerprint, and the entry matching the running tree is loaded -
    checkouts on different trees never share an artifact. A build for
    another interpreter is refused before loading (its ABI tag must be
    one the running interpreter imports as `ren_native`); loading it
    anyway aborts the process on the first FFI call, measured
    2026-10-10. The shared
    venv's copy is the legacy fallback, accepted only where its baked
    fingerprint matches the tree. A build whose fingerprint does not
    match the tree is refused, not used: it predates the algebra the
    current `library/tools` speaks and would answer ops with verdicts
    that no longer hold. The Python implementation runs instead, and
    the refusal is said once.
    """
    global _ren_native, _ren_native_tried, _ren_native_said_stale
    if not use_rust():
        return None
    if not _ren_native_tried:
        _ren_native_tried = True
        try:
            from library.tools import ren_native_cache
            selected = ren_native_cache.select(_engine_root())
        except Exception:  # noqa: BLE001 - selection never breaks the switch
            selected = None
        if selected is not None:
            _ren_native = selected
            return _ren_native
        try:
            import ren_native
        except Exception:  # noqa: BLE001 - an unloadable install is no build
            _ren_native = None
        else:
            baked = getattr(ren_native, "source_fingerprint", lambda: None)()
            current = _tree_fingerprint()
            if baked is not None and baked == current:
                _ren_native = ren_native
            else:
                _ren_native = None
                if not _ren_native_said_stale:
                    _ren_native_said_stale = True
                    print(
                        "ren_native: refusing the installed build - its "
                        "source fingerprint does not match this engine "
                        "tree; the Python edit backend runs. Rebuild with "
                        "scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>.",
                        file=sys.stderr,
                    )
    return _ren_native


def snapshot_handle(snapshot, operations, *, include_transform=False):
    """Prepare or reuse a Rust-resident timeline snapshot for a batch.

    A measured clip-property batch needs only clip identity and timeline
    bounds, plus transforms for read-back. Other operation sets retain the
    complete snapshot contract. With Python selected, return the original
    snapshot unchanged.
    """
    native = rust()
    if native is None:
        return snapshot
    if isinstance(snapshot, native.SnapshotHandle):
        return snapshot
    property_projection = all(
        operation["op"] == "clip.set_property" for operation in operations
    )
    from library.tools.perf_ledger import count_call, span
    count_call("edit_core_calls")
    with span("edit_core_rust", backend="rust", calls=1):
        return native.snapshot_handle(
            snapshot,
            operations,
            property_projection=property_projection,
            include_transform=include_transform,
        )
