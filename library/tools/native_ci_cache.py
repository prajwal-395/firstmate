#!/usr/bin/env python3
"""Content key for native artifacts prepared by the local CI gate."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def _digest_sources(root: Path) -> str:
    digest = hashlib.sha256()
    native = root / "native"
    for path in sorted(native.rglob("*")):
        if not path.is_file() or "target" in path.relative_to(native).parts:
            continue
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    for rel in ("scripts/build_fframes_runtime.sh", "scripts/build_ren_native.sh"):
        path = root / rel
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _version(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            check=True)
    return result.stdout.strip()


def cache_key(root: Path, kind: str, python: str) -> str:
    cargo = _version(["cargo", "--version", "--verbose"])
    rustc = _version(["rustc", "-Vv"])
    build_environment = {
        name: value for name, value in os.environ.items()
        if name.startswith(("CARGO_", "RUST", "CC", "CXX", "AR_", "LD_",
                            "PKG_CONFIG"))
    }
    details = {
        "kind": kind,
        "native_sources": _digest_sources(root),
        "cargo": cargo,
        "rustc": rustc,
        "build_environment": build_environment,
        "cargo_build_jobs": "2",
        "cargo_target": os.environ.get("CARGO_BUILD_TARGET", ""),
        "cc": os.environ.get("CC", ""),
        "linker": os.environ.get("RUSTC_LINKER", ""),
        "build_commands": [
            ["cargo", "build", "--locked", "--bin", "ml-worker-client"],
            ["scripts/build_fframes_runtime.sh"],
            ["maturin", "build", "--locked", "--features", "extension-module"],
        ],
        "python": (_version([python, "-c", "import sys; print(sys.version)"])
                   if kind == "ren-native" else None),
        "python_soabi": (_version([python, "-c",
                                   "import sysconfig; print(sysconfig.get_config_var('SOABI'))"])
                         if kind == "ren-native" else None),
        "platform": platform.platform(),
    }
    encoded = json.dumps(details, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[2] not in {"cargo", "ren-native"}:
        raise SystemExit("usage: native_ci_cache.py <repo> <cargo|ren-native> <python>")
    print(cache_key(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]))
