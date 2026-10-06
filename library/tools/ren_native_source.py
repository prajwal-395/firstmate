"""The source fingerprint that ties a `ren_native` build to its tree.

The Rust edit-algebra extension (`ren_native`, crate `native/ren-core`)
is built by `scripts/build_ren_native.sh` into the shared venv. A build
that predates the tree it is asked to run against is a correctness
hazard: it answers ops with verdicts the current `library/tools` no
longer agrees with. The mask-op port (PR 1786) left a stale extension
returning `None` for `mask.delete`/`mask.track` with no warning, and
nothing noticed or rebuilt it.

So the build bakes a fingerprint of the `native/` sources into the
extension (`native/ren-core/build.rs`, exposed as
`ren_native.source_fingerprint()`), and `library/tools/edit_backend.rust()`
refuses the extension unless the fingerprint matches the tree it is
running from. A mismatch falls back to the Python implementation - the
switch is an optimisation, never a prerequisite - and says so once.

The fingerprint covers exactly what the extension is built from: the
`ren-core` and `ren-protocol` sources and build manifests, and the
workspace manifest. `ren-cli` and `ren-graphics-fframes` are not part of
the extension and are not covered; `ren-protocol`'s generated schemas
and fixtures are derived from its sources and are not covered either.

One implementation: `scripts/build_ren_native.sh` calls this module to
compute the fingerprint at build time, and `edit_backend.rust()` and
`ren doctor` call it to compute the current tree's. The two never drift.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

NATIVE_DIR = "native"
"""The workspace directory the extension's crates live under."""

#: What the extension is built from, relative to `native/`. A directory
#: is walked recursively (skipping any `target/` build cache); a file is
#: hashed directly. Everything else under `native/` - the other crates,
#: the generated schemas, the fixtures - does not reach the extension.
SCOPE = (
    "ren-core/src",
    "ren-core/Cargo.toml",
    "ren-core/build.rs",
    "ren-core/pyproject.toml",
    "ren-protocol/src",
    "ren-protocol/Cargo.toml",
    "Cargo.toml",
)
"""The `native/` entries the fingerprint covers, relative to `native/`."""


def _entry(native: Path, path: Path) -> tuple[str, str]:
    """`(relative_posix_path, sha256_hex)` for one file under `native/`."""
    relative = path.relative_to(native).as_posix()
    return relative, hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(native: Path, base: Path) -> list[tuple[str, str]]:
    """Every file under `base`, as `(relative, sha256)`, skipping any
    `target/` build cache a stray in-crate cargo run might leave."""
    entries = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "target" in path.relative_to(native).parts:
            continue
        entries.append(_entry(native, path))
    return entries


def source_fingerprint(root: str | Path) -> str | None:
    """The fingerprint of the `native/` build sources under `root`.

    None when the tree holds no `native/` sources to fingerprint - an
    engine tree predating the Rust core. There is then nothing to tie a
    build to, and a caller must treat any extension as unverifiable.
    """
    native = Path(root) / NATIVE_DIR
    if not native.is_dir():
        return None
    entries: list[tuple[str, str]] = []
    for relative in SCOPE:
        base = native / relative
        if base.is_dir():
            entries += _walk(native, base)
        elif base.is_file():
            entries.append(_entry(native, base))
    if not entries:
        return None
    entries.sort()
    digest = hashlib.sha256()
    for relative, hex_hash in entries:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hex_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()
