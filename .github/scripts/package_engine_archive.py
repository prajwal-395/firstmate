#!/usr/bin/env python3
"""Create a portable ZIP from an already-built Ren engine tree."""

from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path


def package_engine(engine: Path, archive: Path) -> Path:
    engine = engine.resolve(strict=True)
    if not engine.is_dir():
        raise ValueError(f"engine tree is not a directory: {engine}")
    required = (
        "ren/_build.py",
        "ren/cli.py",
        "manage_project.py",
        "bin/ren",
        "bin/vep",
    )
    missing = [name for name in required if not (engine / name).is_file()]
    if missing:
        raise ValueError(f"engine tree is incomplete: missing {', '.join(missing)}")

    archive = archive.expanduser().resolve()
    if archive == engine or engine in archive.parents:
        raise ValueError(f"archive must be outside the engine tree: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        root_info = zipfile.ZipInfo("ren-engine/")
        root_info.date_time = (1980, 1, 1, 0, 0, 0)
        root_info.create_system = 3
        root_info.external_attr = (stat.S_IFDIR | 0o755) << 16
        output.writestr(root_info, b"")

        for path in sorted(engine.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise ValueError(f"engine tree contains a symlink: {path}")

            relative = path.relative_to(engine).as_posix()
            mode = stat.S_IMODE(path.stat().st_mode)
            info = zipfile.ZipInfo(f"ren-engine/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.create_system = 3
            if path.is_dir():
                info.filename += "/"
                info.external_attr = (stat.S_IFDIR | mode) << 16
                output.writestr(info, b"")
                continue
            if not path.is_file():
                raise ValueError(f"engine tree contains a non-regular entry: {path}")

            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            output.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
            )

    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    archive = package_engine(args.engine, args.archive)
    print(f"built {archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
