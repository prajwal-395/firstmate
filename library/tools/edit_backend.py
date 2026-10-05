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

The parity harness (`tests/unit/resolve/test_ren_core_parity.py`) runs
both backends over the same corpus and compares.
"""

import os

BACKEND_ENV = "REN_EDIT_BACKEND"
RUST = "rust"
PYTHON = "python"

_ren_native = None
_ren_native_tried = False


def use_rust() -> bool:
    """Whether the switch asks for the Rust backend."""
    return os.environ.get(BACKEND_ENV, PYTHON).strip().lower() == RUST


def rust():
    """The ren_native module when the switch is on and it imports; else None.

    The import is attempted once; a failed import is remembered so a run
    without the extension does not retry (and fail) on every call.
    """
    global _ren_native, _ren_native_tried
    if not use_rust():
        return None
    if not _ren_native_tried:
        _ren_native_tried = True
        try:
            import ren_native
            _ren_native = ren_native
        except ImportError:
            _ren_native = None
    return _ren_native
