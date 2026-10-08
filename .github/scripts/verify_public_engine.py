#!/usr/bin/env python3
"""Fail unless a packaged engine conforms to Ren's public component inventory."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_REQUIREMENTS = ("core", "graphics", "analysis", "identity")


def _normalise(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(_normalise(re.split(r"[<>=!~;\s\[]", line, maxsplit=1)[0]))
    return names


def verify_public_engine(engine: Path, source: Path = ROOT) -> list[str]:
    """Return policy violations for an already-built engine tree."""
    engine = engine.resolve(strict=True)
    source = source.resolve(strict=True)
    failures: list[str] = []
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    build_record = engine / "ren/_build.py"
    if not build_record.is_file():
        return [f"missing build metadata: {build_record}"]
    try:
        from ren.edition import EditionError, _build_edition

        edition = _build_edition(engine)
    except (EditionError, OSError) as exc:
        return [f"invalid build metadata: {exc}"]
    if edition != "public":
        failures.append(f"build metadata says {edition!r}; expected 'public'")

    from ren.edition import read_components

    try:
        components = read_components(engine)
        source_components = read_components(source)
    except (EditionError, OSError) as exc:
        return failures + [f"cannot read THIRD_PARTY_NOTICES: {exc}"]

    for entry in components.values():
        if entry.edition != "personal-only":
            restricted_requirements = (
                entry.requirement_refs if entry.edition == "public-optional" else ())
        else:
            restricted_requirements = entry.requirement_refs
        if entry.edition == "personal-only":
            for relative in (*entry.package_paths, *entry.installer_paths):
                if (engine / relative).exists():
                    failures.append(
                        f"personal-only component {entry.id} remains at {relative}")
        for requirement_file, package in restricted_requirements:
            packaged_file = engine / requirement_file
            if packaged_file.is_file() and any(
                    _normalise(package) == name
                    for name in _requirement_names(packaged_file)):
                failures.append(
                    f"public policy excludes {package!r}, but it remains in "
                    f"{requirement_file} ({entry.id})")
    for entry in components.values():
        for source_path, destination in entry.package_copies:
            if not (engine / destination).is_file():
                failures.append(
                    f"inventoried component {entry.id} is missing packaged file "
                    f"{destination}")

    # Every direct runtime dependency in the shipped group manifests must
    # have a Python component row in the shipped notice inventory.
    declared: set[str] = set()
    for entry in components.values():
        if entry.id.startswith("python."):
            declared.add(_normalise(entry.id.removeprefix("python.")))
            declared.update(
                _normalise(name.strip())
                for name in re.split(r"[,()]", entry.label)
                if name.strip() and name.strip().lower() not in {"same cv2 module"})
    for group in RUNTIME_REQUIREMENTS:
        manifest = engine / "requirements" / f"{group}.txt"
        if not manifest.is_file():
            failures.append(f"missing runtime dependency manifest: requirements/{group}.txt")
            continue
        for name in sorted(_requirement_names(manifest) - declared):
            failures.append(
                f"runtime dependency {name!r} in requirements/{group}.txt "
                "has no python component in THIRD_PARTY_NOTICES")

    # Keep the copied notice inventory aligned with the source that defined
    # the build policy, and reject missing/extra component IDs.
    if components != source_components:
        missing = sorted(set(source_components) - set(components))
        extra = sorted(set(components) - set(source_components))
        failures.append(
            f"packaged THIRD_PARTY_NOTICES records differ from source "
            f"(missing={missing}, extra={extra})")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT,
                        help="source checkout whose THIRD_PARTY_NOTICES governs the build")
    args = parser.parse_args(argv)
    failures = verify_public_engine(args.engine, args.source)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"public engine verified: {args.engine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
