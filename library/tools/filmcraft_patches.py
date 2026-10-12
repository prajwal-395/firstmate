"""Ren's fork-patch series for the pinned FilmCraft CLI, and where its build lives.

`library/tools/upstream_patches/filmcraft/*.patch` is replayed in sorted
order onto the pinned upstream checkout in two places - the local-CI
intake (`library/tools/local_ci/upstream.py`, which qualifies the
result) and the runtime install (`native/ren-filmcraft/src/lib.rs`,
which builds the CLI Ren actually runs on demand) - so both must agree
on the patch set AND on the cache directory the built CLI lands in.
This module is the Python side of that agreement.

The cache directory is keyed by revision plus a digest of the patch
set (`<revision>-p<key>`), so a binary built before a patch landed is
never reused once the set changes: the lookup simply points elsewhere.
The key is FNV-1a/64 over, for each sorted `*.patch` file, its file
name bytes, one `\n`, then its raw bytes. It is a cache key, not a
security digest - but it MUST be computed identically in Rust
(`native/ren-filmcraft/build.rs` bakes the same value as
`FILMCRAFT_PATCH_SET_KEY`, and a golden test on each side pins it), or
the Rust-built CLI lands where Python never looks.

Only `*.patch` files participate: `REQUESTS.md` and any other notes in
the patch directory are inert tracking text and must not move the key.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILMCRAFT_PATCH_DIR = (
    REPO_ROOT / "library" / "tools" / "upstream_patches" / "filmcraft")

_FNV_OFFSET_BASIS = 14695981039346656037
_FNV_PRIME = 1099511628211
_FNV_MASK = 0xFFFFFFFFFFFFFFFF


def patch_files(patch_dir: Path = FILMCRAFT_PATCH_DIR) -> list[Path]:
    """The replayed patch series: sorted `*.patch` files, nothing else."""
    return sorted(patch_dir.glob("*.patch"))


def patch_set_key(patch_dir: Path = FILMCRAFT_PATCH_DIR) -> str:
    """FNV-1a/64 hex of the patch series (16 lowercase hex chars).

    Feed order is the sorted file list; each file feeds its UTF-8 file
    name, one ``\\n`` byte, then its raw bytes. Must match
    ``FILMCRAFT_PATCH_SET_KEY`` baked by ``native/ren-filmcraft/build.rs``.
    """
    digest = _FNV_OFFSET_BASIS
    for path in patch_files(patch_dir):
        for byte in path.name.encode("utf-8") + b"\n" + path.read_bytes():
            digest ^= byte
            digest = (digest * _FNV_PRIME) & _FNV_MASK
    return f"{digest:016x}"


def cache_dir_name(revision: str, key: str) -> str:
    """The `~/.cache/ren/filmcraft/` entry for one revision + patch set."""
    return f"{revision}-p{key}"


def filmcraft_cache_dir(home: Path, revision: str,
                        patch_dir: Path = FILMCRAFT_PATCH_DIR) -> Path:
    """The digest-keyed install root the runtime and the intake share."""
    return (Path(home) / ".cache" / "ren" / "filmcraft"
            / cache_dir_name(revision, patch_set_key(patch_dir)))


def filmcraft_cli_path(home: Path, revision: str,
                       patch_dir: Path = FILMCRAFT_PATCH_DIR) -> Path:
    """The patched `filmcraft-cli` binary path both runtimes resolve."""
    return filmcraft_cache_dir(home, revision, patch_dir) / "bin" / (
        "filmcraft-cli.exe" if os.name == "nt" else "filmcraft-cli")


def install_cli_to_cache(source_binary: Path, home: Path, revision: str,
                         patch_dir: Path = FILMCRAFT_PATCH_DIR) -> Path:
    """Install a built CLI as the digest-keyed cached binary; return its path.

    Fail-closed: a missing source binary raises instead of leaving a
    half-installed cache entry behind.
    """
    if not source_binary.is_file():
        raise FileNotFoundError(
            f"qualified FilmCraft CLI is absent: {source_binary}")
    dest = filmcraft_cli_path(home, revision, patch_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_binary, dest)
    return dest
