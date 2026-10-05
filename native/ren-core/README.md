# ren-core

Ren's editorial correctness core, in Rust: the edit algebra (one
NLE-neutral vocabulary of reversible timeline operations), the patch
transaction (validation against the base snapshot) and the conflict
algebra (whether two patches can both land), over `ren-protocol`'s
`EditOp`/`EditPatch` types.

Modeled from `library/tools/edit_algebra.py`, `library/tools/edit_patch.py`
and `library/tools/patch_algebra.py` - each module names its source. The
native Resolve apply calls stay Python; everything computational in those
three modules is here.

## The compiler becomes another contract test

Every dispatch matches over the `EditOp` enum **exhaustively** (no
wildcard arm), so adding a variant fails compilation until its span,
key, verify, merge, temporal effect and inverse are all defined. A trim
with no edge cannot exist; the edge is a field of the variant.

## Building

The crate is a member of the ONE Cargo workspace at `native/`. It is
exposed to Python as the `ren_native` extension module (the lib name),
built with maturin:

    scripts/build_ren_native.sh <venv-bin-dir>

`native/ren-core/pyproject.toml` is the maturin config. No binary is ever
committed: `native/target/` is gitignored and the script installs into
the target venv with `maturin develop`.

## The switch

`library/tools/edit_backend.py` (`REN_EDIT_BACKEND=rust`) routes the
computations through the extension. Python is the default and the
fallback: a missing or broken extension falls back to the Python
implementation rather than refusing to edit.

## Parity

`tests/unit/resolve/test_ren_core_parity.py` runs every algebra op and
the patch cases the existing test suite exercises through BOTH
implementations and compares span, keys, conflicts, inverse and verify
verdicts. It is skipped, naming the missing build, when the extension
is not importable.
