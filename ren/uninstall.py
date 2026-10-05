"""Remove Ren-owned runtime and cache without touching customer data."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from ren.engine_root import require_engine_root
from ren.setup import INSTALL_RECORD, PROFILE_END, PROFILE_START, VENV_DIRNAME


def _home() -> Path:
    from library.tools.shared_environment import vep_home

    return vep_home().expanduser().resolve()


def _record(home: Path) -> dict:
    path = home / INSTALL_RECORD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot read Ren's install record at {path}: {exc}"
        ) from exc
    if data.get("schema_version") != 1 or data.get("home") != str(home):
        raise RuntimeError(f"the install record at {path} does not belong to {home}")
    return data


def _remove_profile_block(path_value: str) -> None:
    if not path_value:
        return
    profile = Path(path_value).expanduser()
    if not profile.is_file():
        return
    content = profile.read_text(encoding="utf-8")
    start = content.find(PROFILE_START)
    if start < 0:
        return
    end = content.find(PROFILE_END, start)
    if end < 0:
        print(
            f"kept incomplete PATH block in {profile}; remove it manually",
            file=sys.stderr,
        )
        return
    end += len(PROFILE_END)
    if end < len(content) and content[end : end + 1] == "\n":
        end += 1
    updated = content[:start] + content[end:]
    profile.write_text(updated, encoding="utf-8")


def removal_paths(home: Path, include_models: bool) -> list[Path]:
    paths = [
        home / "current",
        home / "versions",
        home / VENV_DIRNAME,
        home / "python",
        home / "node",
        home / "micromamba",
        home / "cache",
        home / "bin" / "ren",
        home / "bin" / "deep-filter",
    ]
    if include_models:
        paths.append(home / "models")
    return paths


def _remove(path: Path, home: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    # Paths are constructed from fixed Ren-owned children of the selected
    # home; never follow a replacement symlink out of that home.
    resolved_parent = path.parent.resolve()
    if resolved_parent != home and home not in resolved_parent.parents:
        raise RuntimeError(f"refusing to remove path outside Ren home: {path}")
    shutil.rmtree(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren uninstall",
        description="Remove Ren runtime and cache; keep customer data.",
    )
    parser.add_argument(
        "--models",
        action="store_true",
        help="also remove downloaded model weights and model caches",
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm removal without prompting"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list Ren-owned paths without changing anything",
    )
    args = parser.parse_args(argv)

    try:
        require_engine_root()
        home = _home()
        record = _record(home)
        paths = removal_paths(home, args.models)
        present = [path for path in paths if path.exists() or path.is_symlink()]
        print(f"Ren home: {home}")
        for path in paths:
            state = "remove" if path in present else "absent"
            print(f"  {state}: {path}")
        if not args.models and (home / "models").exists():
            print(
                f"  keep: {home / 'models'} (pass --models to remove model downloads)"
            )
        print(
            "  keep: ~/Movies/Ren/projects, ~/Movies/Ren/assets, ~/.config/ren, and all other customer paths"
        )
        if args.dry_run or not present:
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print(
                    "ren uninstall: pass --yes to confirm in a non-interactive shell",
                    file=sys.stderr,
                )
                return 2
            answer = input("Remove the listed Ren paths? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Ren uninstall cancelled.")
                return 0

        _remove_profile_block(record.get("shell_profile", ""))
        for path in paths:
            _remove(path, home)
        record_path = home / INSTALL_RECORD
        if record_path.is_file():
            record_path.unlink()
        model_result = (
            "model downloads were removed"
            if args.models
            else "model downloads were kept"
        )
        print(
            f"Ren runtime and cache removed. Customer projects, footage, exports, shared assets, configuration, and {model_result}."
        )
        return 0
    except (RuntimeError, OSError) as exc:
        print(f"ren uninstall: refused - {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
