"""One switch for the edit algebra's computational backend.

Python is the default and the fallback. The Rust extension (`ren_native`,
crate `native/ren-core`) is opted into with the environment variable::

    REN_EDIT_BACKEND=rust

A missing or broken extension falls back to the Python implementation
rather than refusing to edit: the switch is an optimisation, never a
prerequisite. `edit_algebra`, `edit_patch` and `patch_algebra` route
their computations (span, key, verify, validate, compose, carry,
conditions) through `rust()`; with the switch off, or the extension
absent, the Python implementations run unchanged.

A build that predates the tree it runs against is refused the same way.
The extension carries a fingerprint of the `native/` sources it was
built from (`ren_native.source_fingerprint()`, baked in by
`native/ren-core/build.rs`); `rust()` compares it against the running
tree and, on a mismatch, falls back to Python and says so once. The
parity harness (`tests/unit/resolve/test_ren_core_parity.py`) skips
naming a stale build rather than failing on an old binary, and still
fails when a fresh build genuinely disagrees.
"""

import os

BACKEND_ENV = "REN_EDIT_BACKEND"
RUST = "rust"
PYTHON = "python"

_ren_native = None
_ren_native_tried = False
_ren_native_said_stale = False


def use_rust() -> bool:
    """Whether the switch asks for the Rust backend."""
    return os.environ.get(BACKEND_ENV, PYTHON).strip().lower() == RUST


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
    """The ren_native module when the switch is on, it imports, and its
    baked-in source fingerprint matches the running tree; else None.

    The import is attempted once; a failed import is remembered so a run
    without the extension does not retry (and fail) on every call.

    A build whose fingerprint does not match the tree is refused, not
    used: it predates the algebra the current `library/tools` speaks and
    would answer ops with verdicts that no longer hold. The Python
    implementation runs instead, and the refusal is said once.
    """
    global _ren_native, _ren_native_tried, _ren_native_said_stale
    if not use_rust():
        return None
    if not _ren_native_tried:
        _ren_native_tried = True
        try:
            import ren_native
        except ImportError:
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
                        "scripts/build_ren_native.sh <venv-bin-dir>."
                    )
    return _ren_native
