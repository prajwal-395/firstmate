//! ren-core: Ren's editorial correctness core.
//!
//! The edit algebra (one NLE-neutral vocabulary of reversible timeline
//! operations), the patch transaction (validation against the base
//! snapshot) and the conflict algebra (whether two patches can both
//! land), over ren-protocol's `EditOp`/`EditPatch` types. Modeled from
//! `library/tools/edit_algebra.py`, `library/tools/edit_patch.py` and
//! `library/tools/patch_algebra.py` - each module names its source.
//!
//! The native Resolve apply calls stay Python; everything else in those
//! three modules is here. Every dispatch matches over the `EditOp` enum
//! EXHAUSTIVELY (no wildcard arm), so adding a variant fails
//! compilation until its span, key, verify and declaration are defined.
//!
//! Exposed to Python as the `ren_native` extension module (built with
//! maturin); `library/tools/edit_backend.py` is the one switch that
//! routes the Python implementation through it.

pub mod algebra;
pub mod cdl;
pub mod compose;
pub mod conditions;
pub mod patch;
pub mod snapshot;
pub mod verify;
pub mod frame_map;

#[cfg(feature = "extension-module")]
pub mod python;
