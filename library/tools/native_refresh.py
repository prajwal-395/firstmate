"""Ren keeps its compiled Rust parts current by itself.

On a desk where `native/target/release` was never built, merged Rust
work silently fell back: caption renders refused over a missing
`ren-graphics-fframes`, the broker over a missing driver, the edit
backend over a stale extension. This module detects a native part that
is missing or built from an older source tree and rebuilds it with the
existing `scripts/build_*.sh`, reporting what it rebuilt. A failed
build keeps the previous binary and says so.

Each part names the sources it is built from; the fingerprint of those
sources is recorded beside the binary it describes - the build output
after a build, and a shipped copy when one is recorded for it - by this
module after a module-driven build, and by the build scripts themselves
for manual runs. Freshness is the recorded fingerprint matching the
tree: hashing a few dozen sources is milliseconds, so safe points check
freely and take the heavy-work lock only to rebuild, rechecking first in
case another lane already did. Renders hold that same lock while they
draw, so a rebuild never lands mid-render.

WHICH BINARY SERVES is this module's answer too: the one built from
this tree. A shipped copy that predates the tree must never shadow a
fresh build beside it (measured 2026-10-20: the broker served the older
prebuilt while the checkout's own build sat next to it, and the broker
ran code the tree had already replaced).

`ren_native` is the exception to "beside the build output": the
extension is loaded from a machine-wide content-addressed cache keyed
by source fingerprint (`library/tools/ren_native_cache.py`), so its
record lives inside its own cache entry rather than beside any one
checkout's target dir - and the shared venv is never consulted, so a
venv copy built from another tree can neither read as fresh nor
trigger a rebuild here.

Safe points that call `ensure_current`: a pipeline run starting
(`run_pipeline.main`), the broker starter (`resolved.client.ensure`,
with a short wait bound so a serving broker can start on its current
binary), an idle broker refresh (`resolved.client.ensure`), and a driver
session starting (`computer_use.client` where it spawns, scoped to the
`resolve-control` driver part only - a UI session must never rebuild
the edit extension). A deferred broker refresh is retried by a later
safe point; an idle broker is restarted after its Rust binary is
rebuilt. `ren setup` builds at install;
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
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _register_native_cache(root: str | Path, fingerprint: str) -> Path | None:
    """Record the exact built extension in the active project's lifecycle."""
    _lib()
    from library.tools import ren_native_cache
    from library.tools.cache_lifecycle import register_cache_artifact_from_environment

    entry = ren_native_cache.entry_dir(fingerprint)
    extension = ren_native_cache.extension_file(entry)
    if extension is None or ren_native_cache.read_recorded(entry) != fingerprint:
        return None
    register_cache_artifact_from_environment(
        extension, kind="native_extension",
        producer_key=f"{fingerprint}:{extension.name}",
        regeneration_method="native_refresh:ren_native build",
        source_available=Path(root).is_dir(), compatible=True)
    return extension


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
            "native/ren-protocol/src",
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
        "args": None,  # filled per call: the running venv's bin dir, cache dir
    },
}
"""Every compiled part Ren refreshes itself: the sources its build
reads, the build output it checks, and the script that rebuilds it.

`ren_native` names no binary because the extension is not a file in
any checkout: it lives in the per-tree cache
(`library/tools/ren_native_cache.py`), and `binary is None` is the
marker every helper below keys off - an extension-style part whose
freshness is its cache entry, never the shared venv."""


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
    the tree holds no such sources.

    `ren_native` delegates to the one implementation in
    `ren_native_source`: the build script bakes exactly that value into
    the extension, and the importer compares exactly it, so a second
    hashing here (over repo-root-relative rather than
    `native/`-relative paths) would fingerprint the same tree
    differently and no build could ever read as fresh.
    """
    repo = Path(root)
    spec = (parts or PARTS)[part]
    if part == "ren_native" and not spec["scope"]:
        _lib()
        from library.tools import ren_native_source

        return ren_native_source.source_fingerprint(repo)
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


def _sidecar_for(binary: Path | None, root: Path, part: str) -> Path:
    """Where a binary's own recorded fingerprint lives.

    Beside the binary it describes: a build output's record sits in
    the target dir's own `.ren-fingerprints/`, a packaged or shipped
    copy's beside that copy. One rule, so a record can never describe
    a binary other than the one it sits next to.
    """
    if binary is None:
        base = root / "native" / "target"
    elif binary.parent.name in {"release", "debug"}:
        base = binary.parent.parent
    else:
        base = binary.parent
    return base / ".ren-fingerprints" / part


def sidecar_path(root: str | Path, part: str,
                 parts: dict | None = None) -> Path:
    """Where `part`'s recorded fingerprint lives beside its selected binary.

    Extension-style parts (`binary is None`, today only `ren_native`)
    record inside their own cache entry instead: the entry directory
    already names the fingerprint, so the record is the entry's proof
    of a completed build rather than any one checkout's memory of one.
    """
    repo = Path(root)
    spec = (parts or PARTS)[part]
    if spec["binary"] is None:
        _lib()
        from library.tools import ren_native_cache

        current = current_fingerprint(root, part, parts)
        if current is None:
            return (ren_native_cache.cache_dir() / "unknown"
                    / ren_native_cache.FINGERPRINT_FILENAME)
        return ren_native_cache.fingerprint_file(
            ren_native_cache.entry_dir(current))
    binary = _binary_path(repo, part, parts)
    if binary is None or not binary.is_file():
        return _sidecar_for(None, repo, part)
    return _sidecar_for(binary, repo, part)


def recorded_fingerprint(root: str | Path, part: str,
                         parts: dict | None = None) -> str | None:
    """The fingerprint recorded beside the binary that serves, if any."""
    binary = serving_binary(root, part, parts)
    if binary is None:
        return None
    try:
        return _sidecar_for(binary, Path(root), part).read_text(
            encoding="utf-8").strip() or None
    except OSError:
        return None


def record(root: str | Path, part: str, parts: dict | None = None,
           binary: str | Path | None = None) -> str | None:
    """Record the current tree's fingerprint for `part` (a build just
    succeeded); return it.

    The record lands beside the binary it describes: `binary` names
    that binary, and where the caller does not, the part's build
    output is the one recorded.
    """
    hex_hash = current_fingerprint(root, part, parts)
    if hex_hash is None:
        return None
    sidecar = (sidecar_path(root, part, parts) if binary is None
               else _sidecar_for(Path(binary), Path(root), part))
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(hex_hash + "\n", encoding="utf-8")
    return hex_hash


def _binary_path(root: str | Path, part: str, parts: dict | None = None) -> Path | None:
    spec = (parts or PARTS)[part]
    if spec["binary"] is None:
        return None
    packaged = Path(root) / "native" / "bin" / Path(spec["binary"][-1])
    if packaged.is_file():
        return packaged
    target = Path(root).joinpath(*spec["binary"])
    if target.is_file():
        return target
    prebuilt = Path(root) / "native" / "prebuilt" / Path(spec["binary"][-1])
    return prebuilt


def candidate_binaries(root: str | Path, part: str,
                       parts: dict | None = None) -> list[Path]:
    """Every place this part's binary may live, in the order the engine
    prefers them.

    The packaged copy a released engine runs, then this checkout's
    release and debug builds, then the shipped prebuilt - the one a
    machine with no Rust toolchain runs.
    """
    spec = (parts or PARTS)[part]
    if spec["binary"] is None:
        return []
    name = Path(spec["binary"][-1])
    return [
        Path(root) / "native" / "bin" / name,
        Path(root) / "native" / "target" / "release" / name,
        Path(root) / "native" / "target" / "debug" / name,
        Path(root) / "native" / "prebuilt" / name,
    ]


def serving_binaries(root: str | Path, part: str,
                     parts: dict | None = None) -> list[Path]:
    """The candidates this part could run from, the ones built from THIS
    tree first.

    A shipped prebuilt that predates the tree must never shadow a fresh
    build. Measured 2026-10-20: the checkout had built `ren-resolved`
    from the tree - the fingerprint beside it matched - and the broker
    still served the older `native/prebuilt/ren-resolved` beside it, so
    the broker ran code the tree had already replaced: `ren resolved
    stop` left its worker running (ppid 1, alive 3h10m), and every
    later writer timed out waiting for the one Resolve.

    Each candidate carries the fingerprint of the sources it was built
    from, beside it; the candidates whose record matches this tree come
    first. Where the tree holds no sources for the part - a packaged
    engine - there is nothing to compare, and the engine's own order
    decides.
    """
    repo = Path(root)
    specs = parts or PARTS
    existing = [path for path in candidate_binaries(repo, part, specs)
                if path.is_file()]
    if not existing:
        return []
    current = current_fingerprint(repo, part, specs)
    if current is None:
        return existing
    fresh = []
    for candidate in existing:
        try:
            recorded = _sidecar_for(candidate, repo, part).read_text(
                encoding="utf-8").strip()
        except OSError:
            continue
        if recorded == current:
            fresh.append(candidate)
    return fresh + [path for path in existing if path not in fresh]


def serving_binary(root: str | Path, part: str,
                   parts: dict | None = None) -> Path | None:
    """The binary this part runs from: the one built from THIS tree.

    Where several candidates exist, this is the one whose recorded
    fingerprint matches the tree - see `serving_binaries` for why a
    stale prebuilt must not win.
    """
    candidates = serving_binaries(root, part, parts)
    return candidates[0] if candidates else None


def status(root: str | Path, part: str, parts: dict | None = None) -> tuple[str, str]:
    """`("fresh" | "stale" | "missing", detail)` for one part.

    The extension check answers the running environment, so callers run
    it in the live tree - which is exactly where the safe points run.

    Extension-style parts (`binary is None`, today only `ren_native`)
    are answered from the per-tree cache alone: the shared venv is
    never consulted, so a venv copy built from an unrelated checkout
    neither reads as fresh nor provokes a rebuild here - only the
    absence of THIS tree's own entry builds, from THIS tree's sources.

    A file part is answered about the binary that SERVES: the one
    built from this tree. A freshness report about some other binary
    is a claim the engine cannot keep - it said "the Rust broker is
    built from this tree" while the broker serving ran a prebuilt the
    tree had already replaced (measured 2026-10-20).
    """
    specs = parts or PARTS
    current = current_fingerprint(root, part, specs)
    if current is None:
        return "missing", "the tree holds no sources for it"
    if specs[part]["binary"] is None:
        _lib()
        from library.tools import ren_native_cache

        entry = ren_native_cache.entry_dir(current)
        built = ren_native_cache.extension_file(entry)
        recorded = ren_native_cache.read_recorded(entry)
        if built is not None and recorded == current:
            return "fresh", "built from this tree"
        if built is None and recorded is None:
            return "missing", f"no build for this tree in {entry}"
        if built is None:
            return "missing", (
                "no build for this interpreter "
                f"({ren_native_cache.this_interpreter_tag()}) in {entry}"
            )
        return "stale", "the entry holds no recorded build for this tree"
    binary = serving_binary(root, part, specs)
    if binary is None:
        return "missing", f"no build at {_binary_path(root, part, specs)}"
    recorded = recorded_fingerprint(root, part, specs)
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
        _lib()
        if spec["binary"] is None:
            from library.tools import ren_native_cache

            args = (str(Path(sys.executable).parent),
                    str(ren_native_cache.cache_dir()))
        else:
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
            if name == "ren_native":
                current = current_fingerprint(root, name, specs)
                if current:
                    _register_native_cache(root, current)
            report[name] = "fresh"
            continue
        try:
            profile = (f"native_refresh:{name.replace('_', '-')}"
                       if name in PARTS else heavy_work_lock.MACHINE)
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
                build_started = time.perf_counter()
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
                    if specs[name]["binary"] is not None:
                        packaged = Path(root) / "native" / "bin" / name
                        built = Path(root).joinpath(*specs[name]["binary"])
                        if packaged.is_file() and built.is_file():
                            import shutil
                            shutil.copy2(built, packaged)
                    # Extension-style parts (binary None) install into
                    # their cache entry from the build script itself;
                    # there is no per-checkout binary to promote. The
                    # copy must be SKIPPED, not attempted: joining a
                    # None binary raised TypeError here, which skipped
                    # the record below and rebuilt successfully on
                    # every safe point thereafter (measured 2026-10-08:
                    # 551 stale rebuilds, zero completions).
                    record(root, name, specs)
                    if name == "ren_native":
                        current = current_fingerprint(root, name, specs)
                        extension = (_register_native_cache(root, current)
                                     if current else None)
                        if extension is not None:
                            from library.tools.cache_lifecycle import (
                                cache_artifact_id_from_environment,
                                record_cache_metric,
                            )
                            record_cache_metric(
                                task="native-refresh", kind="native_extension",
                                outcome="regenerated",
                                elapsed_seconds=time.perf_counter() - build_started,
                                bytes_used=extension.stat().st_size,
                                artifact_id=cache_artifact_id_from_environment(
                                    extension, "native_extension"))
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
    rec.add_argument("--binary", default=None,
                     help="the binary the record describes; the part's "
                          "build output where the caller does not name one")
    status_cmd = sub.add_parser("status", help="report each part's freshness")
    status_cmd.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    root = args.root
    if args.command == "record":
        hex_hash = record(root, args.part, binary=args.binary)
        print(hex_hash or "no sources")
        return 0 if hex_hash else 1
    for name in PARTS:
        state, detail = status(root, name)
        print(f"{name}: {state} ({detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
