//! Bakes the source fingerprint of the `native/` workspace into the
//! extension, so `library/tools/edit_backend.py` can refuse a build
//! that predates the tree it is asked to run against.
//!
//! `scripts/build_ren_native.sh` computes the fingerprint - see
//! `library/tools/ren_native_source.py`, the one implementation - and
//! passes it here as `$REN_NATIVE_SOURCE_FINGERPRINT`.
//!
//! The variable is required only for the Python extension build (the
//! `extension-module` feature maturin enables): a direct `maturin
//! develop` without it fails loudly rather than producing an extension
//! no tree can validate. Every other cargo build - `ren-cli` and
//! `ren-graphics-fframes` compile `ren-core` as a plain dependency -
//! bakes the sentinel `"unknown"`, which no tree's fingerprint equals,
//! so an extension that somehow reached a run is refused by it.

use std::env;

fn main() {
    let fingerprint = env::var("REN_NATIVE_SOURCE_FINGERPRINT").unwrap_or_else(|_| {
        if cfg!(feature = "extension-module") {
            panic!(
                "REN_NATIVE_SOURCE_FINGERPRINT is not set: build ren_native \
                 through scripts/build_ren_native.sh, which computes the \
                 fingerprint of the native/ sources and passes it here",
            );
        }
        "unknown".to_string()
    });
    println!("cargo:rerun-if-env-changed=REN_NATIVE_SOURCE_FINGERPRINT");
    println!("cargo:rustc-env=REN_NATIVE_SOURCE_FINGERPRINT={fingerprint}");
}
