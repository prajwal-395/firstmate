"""Ren keeps its compiled Rust parts current by itself.

On a desk where `native/target/release` was never built, merged Rust
work silently fell back: caption renders refused over a missing
`ren-graphics-fframes`, the broker over a missing driver, the edit
backend over a stale extension. This module detects a native part that
is missing or built from an older source tree and rebuilds it with the
existing `scripts/build_*.sh`, reporting what it rebuilt. A failed
build keeps the previous binary and says so.

Each part names the sources it is built from; the fingerprint of those
sources is recorded beside the build output (never beside tracked
files) whenever a build succeeds - by this module after a module-driven
build, and by the build scripts themselves for manual runs. Freshness
is the recorded fingerprint matching the tree: hashing a few dozen
sources is milliseconds, so safe points check freely and take the
heavy-work lock only to rebuild, rechecking first in case another lane
already did. Renders hold that same lock while they draw, so a rebuild
never lands mid-render.

Safe points that call `ensure_current`: a pipeline run starting
(`run_pipeline.main`), the broker starter (`resolved.client.ensure`,
with a short wait bound so a serving broker can start on its current
binary), an idle broker refresh (`resolved.client.ensure`), and a driver
session starting (`computer_use.client` where it spawns). A deferred
broker refresh is retried by a later safe point; an idle broker is
restarted after its Rust binary is rebuilt. `ren setup` builds at install;
`ren upgrade` rebuilds `ren_native` post-switch as before. `ren doctor`
reports each part's freshness.

Only stdlib at import: the build scripts invoke this file directly with
the system python3 (`python3 library/tools/native_refresh.py record
<part> --root <repo>`), where `library/` may not be importable, so
everything outside the stdlib is imported lazily off a `sys.path` this
file derives from its own location.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _lib():
    """Make `library.*` importable whatever interpreter runs this file."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


PARTS = {
    "ren-graphics-fframes": {
        "scope": (
            "native/ren-graphics-fframes/Cargo.toml",
            "native/ren-graphics-fframes/src",
            "native/Cargo.toml",
            "library/graphics_ir/model.py",
            "library/graphics_ir/lowering.py",
            "library/graphics_ir/fonts.py",
            "library/graphics_ir/layout.py",
            "library/tools/fframes_render.py",
        ),
        "binary": ("native", "target", "release", "ren-graphics-fframes"),
        "script": ("scripts", "build_fframes_runtime.sh"),
        "args": (),
    },
    "resolve-control": {
        "scope": (
            "native/resolve-control/Cargo.toml",
            "native/resolve-control/src",
            "native/Cargo.toml",
        ),
        "binary": ("native", "target", "release", "resolve-control"),
        "script": ("scripts", "build_resolve_control.sh"),
        "args": (),
    },
    "ren-resolved": {
        "scope": (
            "native/ren-resolved/Cargo.toml",
            "native/ren-resolved/src",
            "native/Cargo.toml",
            "scripts/build_ren_resolved.sh",
        ),
        "binary": ("native", "target", "release", "ren-resolved"),
        "script": ("scripts", "build_ren_resolved.sh"),
        "args": (),
    },
    "resolve-activity-overlay": {
        "scope": (
            "native/resolve-activity-overlay/main.swift",
            "scripts/build_resolve_activity_overlay.sh",
        ),
        "binary": ("native", "target", "release", "resolve-activity-overlay"),
        "script": ("scripts", "build_resolve_activity_overlay.sh"),
        "args": (),
    },
    "ren_native": {
        "scope": (),
        "binary": None,
        "script": ("scripts", "build_ren_native.sh"),
        "args": None,  # filled per call: the running venv's bin dir
    },
}
"""Every compiled part Ren refreshes itself: the sources its build
reads, the build output it checks, and the script that rebuilds it."""


def _walk_files(root: Path, base: Path) -> list[tuple[str, str]]:
    """`(root-relative posix path, sha256)` for files under `base`,
    skipping any `target/` build cache."""
    entries = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "target" in path.relative_to(root).parts:
            continue
        entries.append(
            (path.relative_to(root).as_posix(),
             hashlib.sha256(path.read_bytes()).hexdigest()))
    return entries


def _scope_entries(root: Path, scope: tuple) -> list[tuple[str, str]]:
    """Hash every scope entry (files directly, directories walked)."""
    entries = []
    for relative in scope:
        base = root / relative
        if base.is_dir():
            entries += _walk_files(root, base)
        elif base.is_file():
            entries.append(
                (relative,
                 hashlib.sha256(base.read_bytes()).hexdigest()))
    return entries


def current_fingerprint(root: str | Path, part: str,
                        parts: dict | None = None) -> str | None:
    """The fingerprint of what `part` would be built from, or None when
    the tree holds no such sources."""
    repo = Path(root)
    spec = (parts or PARTS)[part]
    if part == "ren_native" and not spec["scope"]:
        _lib()
        from library.tools import ren_native_source

        scope = tuple("native/" + s for s in ren_native_source.SCOPE)
    else:
        scope = spec["scope"]
    entries = _scope_entries(repo, scope)
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


def sidecar_path(root: str | Path, part: str) -> Path:
    """Where `part`'s recorded fingerprint lives: beside the build
    output, never beside tracked files."""
    return Path(root) / "native" / "target" / ".ren-fingerprints" / part


def recorded_fingerprint(root: str | Path, part: str) -> str | None:
    """The fingerprint recorded at the last successful build, if any."""
    try:
        return sidecar_path(root, part).read_text(
            encoding="utf-8").strip() or None
    except OSError:
        return None


def record(root: str | Path, part: str, parts: dict | None = None) -> str | None:
    """Record the current tree's fingerprint for `part` (a build just
    succeeded); return it."""
    hex_hash = current_fingerprint(root, part, parts)
    if hex_hash is None:
        return None
    sidecar = sidecar_path(root, part)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(hex_hash + "\n", encoding="utf-8")
    return hex_hash


def _binary_path(root: str | Path, part: str, parts: dict | None = None) -> Path | None:
    spec = (parts or PARTS)[part]
    if spec["binary"] is None:
        return None
    return Path(root).joinpath(*spec["binary"])


def status(root: str | Path, part: str, parts: dict | None = None) -> tuple[str, str]:
    """`("fresh" | "stale" | "missing", detail)` for one part.

    The extension check answers the running environment, so callers run
    it in the live tree - which is exactly where the safe points run.
    """
    import importlib.util

    current = current_fingerprint(root, part, parts)
    if current is None:
        return "missing", "the tree holds no sources for it"
    binary = _binary_path(root, part, parts)
    if binary is not None:
        if not binary.is_file():
            return "missing", f"no build at {binary}"
    elif importlib.util.find_spec("ren_native") is None:
        return "missing", "the ren_native extension is not installed"
    recorded = recorded_fingerprint(root, part)
    if recorded is None:
        return "stale", "built, but no fingerprint was recorded for this tree"
    if recorded != current:
        return "stale", "the sources changed since it was built"
    return "fresh", "built from this tree"


def _script_argv(root: str | Path, part: str, parts: dict | None = None) -> list[str]:
    spec = (parts or PARTS)[part]
    script = str(Path(root).joinpath(*spec["script"]))
    args = spec["args"]
    if args is None:
        args = (str(Path(sys.executable).parent),)
    return ["bash", script, *args]


def ensure_current(root: str | Path, *, parts: dict | None = None,
                   runner=None, owner: str = "native-refresh",
                   lock_timeout: float | None = None) -> dict:
    """Rebuild every stale or missing part; report what happened.

    `runner` runs a script argv and returns its exit code (default:
    `subprocess`); tests inject a fake. Only rebuilds take the
    machine heavy-work lock, rechecking first; freshness checks are
    lock-free hashing. Never raises: every outcome is reported, and a
    failed build keeps the previous binary and says so.
    """
    _lib()
    from library.tools import heavy_work_lock

    specs = parts or PARTS
    report: dict = {}
    for name in specs:
        try:
            state, detail = status(root, name, specs)
        except OSError as exc:
            report[name] = f"uncheckable ({exc}); left alone"
            continue
        if state == "fresh":
            report[name] = "fresh"
            continue
        try:
            profile = (f"native_refresh:{name}"
                       if name in PARTS else heavy_work_lock.MACHINE)
            profile = profile.replace("_", "-")
            with heavy_work_lock.heavy_work_lock(
                    owner, profile, timeout=lock_timeout):
                try:
                    state, detail = status(root, name, specs)
                except OSError as exc:
                    report[name] = f"uncheckable ({exc}); left alone"
                    continue
                if state == "fresh":
                    report[name] = "fresh (rebuilt by another lane)"
                    continue
                print(f"native: {name} is {state} ({detail}) - rebuilding",
                      file=sys.stderr, flush=True)
                try:
                    from library.tools.native_part_lock import native_part_lock
                    with native_part_lock(root, name, shared=False):
                        if runner is None:
                            done = subprocess.run(
                                _script_argv(root, name, specs),
                                capture_output=True, encoding="utf-8",
                                timeout=1800, check=False)
                            code = done.returncode
                        else:
                            code = runner(_script_argv(root, name, specs))
                except (OSError, subprocess.SubprocessError) as exc:
                    report[name] = (
                        f"build could not start ({exc}); kept the previous build")
                    print(f"native: {name} {report[name]}",
                          file=sys.stderr, flush=True)
                    continue
                if code == 0:
                    record(root, name, specs)
                    report[name] = "rebuilt"
                    print(f"native: {name} rebuilt from this tree",
                          file=sys.stderr, flush=True)
                elif code == 4:
                    report[name] = ("no toolchain; kept the previous build")
                    print(f"native: {name} {report[name]}",
                          file=sys.stderr, flush=True)
                else:
                    report[name] = (
                        f"build failed (exit {code}); kept the previous build")
                    print(f"native: {name} {report[name]}",
                          file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - refresh never breaks startup
            if isinstance(exc, heavy_work_lock.resource_scheduler.Cancelled):
                report[name] = "rebuild pending (heavy-work slot busy)"
            else:
                report[name] = f"refresh skipped ({exc}); left alone"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="native_refresh")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record a part's fingerprint")
    rec.add_argument("part")
    rec.add_argument("--root", required=True)
    status_cmd = sub.add_parser("status", help="report each part's freshness")
    status_cmd.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    root = args.root
    if args.command == "record":
        hex_hash = record(root, args.part)
        print(hex_hash or "no sources")
        return 0 if hex_hash else 1
    for name in PARTS:
        state, detail = status(root, name)
        print(f"{name}: {state} ({detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
