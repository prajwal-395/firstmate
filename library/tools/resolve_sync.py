#!/usr/bin/env python3
"""
resolve_sync.py - Symlink manager for DaVinci Resolve integration.

Creates, verifies, and repairs symlinks between our pipeline's preset
library and DaVinci Resolve's application support directories. All
pipeline assets are placed under a namespaced subdirectory (default:
"Pipeline/") inside Resolve's directories so they don't collide with
user-installed assets or Resolve updates.

Symlink map:
    library/tools/execution/*.dctl -> Resolve/LUT/4th Wall/
    library/presets/fusion-macros/ -> Resolve/Fusion/Macros/Pipeline/
    library/presets/fairlight/     -> Resolve/Fairlight/Presets/Pipeline/

These are assets a human uses on the Color and Fusion pages. The
pipeline's own look is not among them: it is CDL plus Fusion values in
library/tools/house_look.py, applied by the renderer, with no file
inside a Resolve installation involved.

Usage:
    python resolve_sync.py sync      # Create/repair all symlinks
    python resolve_sync.py verify    # Check integrity, report broken links
    python resolve_sync.py clean     # Remove all pipeline-managed symlinks
    python resolve_sync.py status    # Show current state of all link targets
"""

import argparse
import os
import sys
from pathlib import Path

# Add tools to path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import (
    PRESETS_FUSION_MACROS,
    PRESETS_FAIRLIGHT,
    RESOLVE_LUT_DIR,
    RESOLVE_FUSION_DIR,
    RESOLVE_FAIRLIGHT_DIR,
    RESOLVE_SYNC_NAMESPACE,
    TOOLS_ROOT,
)


# ─── Symlink Definitions ─────────────────────────────────────
# Each entry: (source_dir, resolve_parent_dir, namespace_subdir, file_globs)
#
# source_dir:         our repo directory containing the assets
# resolve_parent_dir: the Resolve directory to create the namespace in
# namespace_subdir:   subdirectory name inside resolve_parent_dir (usually RESOLVE_SYNC_NAMESPACE)
# file_globs:         list of glob patterns to match (None = symlink the whole directory)

def _build_link_map():
    """Build the complete list of symlink operations."""
    ns = RESOLVE_SYNC_NAMESPACE
    links = []

    # There is no luts/ or dctls/ preset directory: nothing in the
    # pipeline ever read one, and the look now ships as CDL plus Fusion
    # values in library/tools/house_look.py.

    # Project DCTL (4thWall_Base_Memory.dctl) -> Resolve/LUT/4th Wall/
    # This one has its own namespace because it was already manually placed there
    links.append({
        "label": "Project DCTL",
        "source": TOOLS_ROOT / "execution",
        "target_dir": RESOLVE_LUT_DIR / "4th Wall",
        "globs": ["*.dctl"],
    })

    # Fusion Macros (.setting files) -> Resolve/Fusion/Macros/Pipeline/
    links.append({
        "label": "Fusion Macros",
        "source": PRESETS_FUSION_MACROS,
        "target_dir": RESOLVE_FUSION_DIR / "Macros" / ns,
        "globs": ["*.setting"],
    })

    # No PowerGrades: the pipeline ships no .drx and applies none.

    # Fairlight Presets -> Resolve/Fairlight/Presets/Pipeline/
    links.append({
        "label": "Fairlight Presets",
        "source": PRESETS_FAIRLIGHT,
        "target_dir": RESOLVE_FAIRLIGHT_DIR / "Presets" / ns,
        "globs": ["*.fairlight-preset", "*.xml"],
    })

    return links


# ─── Core Operations ─────────────────────────────────────────

def sync(dry_run=False):
    """Create or repair all symlinks."""
    link_map = _build_link_map()
    created = 0
    skipped = 0
    repaired = 0
    errors = 0

    for entry in link_map:
        label = entry["label"]
        source_dir = entry["source"]
        target_dir = entry["target_dir"]
        globs = entry["globs"]

        if not source_dir.exists():
            print(f"  - {label}: source dir missing ({source_dir})")
            errors += 1
            continue

        # Collect source files matching globs
        source_files = []
        for glob_pattern in globs:
            source_files.extend(sorted(source_dir.glob(glob_pattern)))

        if not source_files:
            print(f"  - {label}: no matching files in {source_dir}")
            skipped += 1
            continue

        # Ensure target directory exists
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)

        for src_file in source_files:
            link_path = target_dir / src_file.name
            _create_or_repair_link(label, src_file, link_path, dry_run,
                                   stats={"created": 0, "repaired": 0, "skipped": 0})

        # Report per-category
        print(f"  {label}: {len(source_files)} file(s) -> {target_dir}")

    return created, skipped, repaired, errors


def _create_or_repair_link(label, src_file, link_path, dry_run, stats):
    """Create a single symlink, repair if broken, skip if correct."""
    if link_path.is_symlink():
        # Already a symlink - check if it points to the right place
        current_target = link_path.resolve()
        if current_target == src_file.resolve():
            stats["skipped"] += 1
            return "ok"
        else:
            # Broken or wrong target - repair
            if not dry_run:
                link_path.unlink()
                link_path.symlink_to(src_file.resolve())
            print(f"    repaired: {link_path.name} -> {src_file}")
            stats["repaired"] += 1
            return "repaired"
    elif link_path.exists():
        # Real file exists at link location - don't overwrite
        print(f"    SKIP: {link_path.name} (real file exists, not overwriting)")
        stats["skipped"] += 1
        return "skip_real"
    else:
        # Create new symlink
        if not dry_run:
            link_path.symlink_to(src_file.resolve())
        print(f"    linked: {link_path.name} -> {src_file}")
        stats["created"] += 1
        return "created"


def verify():
    """Check all symlinks are intact. Returns True if all OK."""
    link_map = _build_link_map()
    all_ok = True

    for entry in link_map:
        label = entry["label"]
        source_dir = entry["source"]
        target_dir = entry["target_dir"]
        globs = entry["globs"]

        # Check source files first - if there are none, skip this category
        source_files = []
        for glob_pattern in globs:
            source_files.extend(sorted(source_dir.glob(glob_pattern)))

        if not source_files:
            print(f"  {label}: (no source files)")
            continue

        if not target_dir.exists():
            print(f"  {label}: target dir missing ({target_dir})")
            all_ok = False
            continue


        ok_count = 0
        broken = []
        missing = []

        for src_file in source_files:
            link_path = target_dir / src_file.name
            if not link_path.exists() and not link_path.is_symlink():
                missing.append(src_file.name)
            elif link_path.is_symlink():
                if link_path.resolve() == src_file.resolve():
                    ok_count += 1
                else:
                    broken.append(src_file.name)
            else:
                # Real file, not a symlink
                ok_count += 1

        status_parts = []
        if ok_count:
            status_parts.append(f"{ok_count} OK")
        if missing:
            status_parts.append(f"{len(missing)} missing")
            all_ok = False
        if broken:
            status_parts.append(f"{len(broken)} broken")
            all_ok = False

        print(f"  {label}: {', '.join(status_parts)}")
        for name in missing:
            print(f"    missing: {name}")
        for name in broken:
            print(f"    broken:  {name}")

    return all_ok


def clean():
    """Remove all pipeline-managed symlinks."""
    link_map = _build_link_map()
    removed = 0

    for entry in link_map:
        label = entry["label"]
        target_dir = entry["target_dir"]

        if not target_dir.exists():
            continue

        # Remove symlinks in this directory that point into our repo
        for item in target_dir.iterdir():
            if item.is_symlink():
                item.unlink()
                removed += 1
                print(f"    removed: {item}")

        # Remove the namespace directory if empty
        if target_dir.exists() and not any(target_dir.iterdir()):
            target_dir.rmdir()
            print(f"    removed dir: {target_dir}")

    print(f"\n  Removed {removed} symlink(s)")
    return removed


def status():
    """Show the current state of all link targets."""
    link_map = _build_link_map()

    for entry in link_map:
        label = entry["label"]
        source_dir = entry["source"]
        target_dir = entry["target_dir"]
        globs = entry["globs"]

        print(f"\n  {label}:")
        print(f"    Source:  {source_dir}")
        print(f"    Target:  {target_dir}")
        print(f"    Exists:  {'yes' if target_dir.exists() else 'no'}")

        if source_dir.exists():
            source_files = []
            for glob_pattern in globs:
                source_files.extend(sorted(source_dir.glob(glob_pattern)))
            print(f"    Sources: {len(source_files)} file(s)")
            for sf in source_files:
                link = target_dir / sf.name
                if link.is_symlink() and link.resolve() == sf.resolve():
                    sym = "-> linked"
                elif link.is_symlink():
                    sym = "-> BROKEN"
                elif link.exists():
                    sym = "   (real file)"
                else:
                    sym = "   NOT LINKED"
                print(f"      {sf.name} {sym}")
        else:
            print(f"    Sources: (dir missing)")


# ─── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Manage symlinks between pipeline presets and DaVinci Resolve",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  sync     Create or repair all symlinks
  verify   Check all symlinks are intact
  clean    Remove all pipeline-managed symlinks
  status   Show current state of all link targets
        """,
    )
    parser.add_argument("command", choices=["sync", "verify", "clean", "status"],
                        help="Operation to perform")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    args = parser.parse_args()

    print(f"resolve_sync: {args.command}")

    if args.command == "sync":
        sync(dry_run=args.dry_run)
        if args.dry_run:
            print("\n  (dry run - no changes made)")
    elif args.command == "verify":
        ok = verify()
        sys.exit(0 if ok else 1)
    elif args.command == "clean":
        clean()
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
