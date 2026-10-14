"""Build an isolated Remotion project for tests that render in parallel.

The machine-wide Node dependency store is shared by every checkout. Remotion
places its Webpack cache under ``node_modules/.cache/webpack`` and clears the
whole production cache when it sees a different bundle hash. Keeping a local
``node_modules/.cache`` directory prevents one test or checkout from deleting
another render's active cache while still reusing the installed packages.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def stage_remotion_test_project(remotion_root: Path, destination: Path) -> Path:
    """Copy the app and link installed packages, with a private Webpack cache."""
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ("package.json", "package-lock.json", "remotion.config.ts",
                     "tsconfig.json"):
        shutil.copy2(remotion_root / filename, destination / filename)
    for directory in ("src", "public"):
        shutil.copytree(remotion_root / directory, destination / directory)

    installed_modules = (remotion_root / "node_modules").resolve(strict=True)
    local_modules = destination / "node_modules"
    local_modules.mkdir()
    for entry in installed_modules.iterdir():
        if entry.name == ".cache":
            continue
        os.symlink(entry, local_modules / entry.name,
                   target_is_directory=entry.is_dir())
    (local_modules / ".cache").mkdir()
    return destination
