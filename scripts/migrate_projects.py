#!/usr/bin/env python3
"""
migrate_projects.py - Migrate in-repo project data to PROJECTS_ROOT.

Moves project-specific data (footage references, subtitles, transcripts,
compositions, brand assets) from within the repo to the dedicated
video_projects directory. The engine (repo) stays clean; the assets
move to their new home.

Dry-run by default. Use --execute to actually move files.

Usage:
    python3 scripts/migrate_projects.py                  # dry-run (preview)
    python3 scripts/migrate_projects.py --execute        # actually move files
    python3 scripts/migrate_projects.py --execute --symlink  # move + leave symlinks
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.paths import PROJECTS_ROOT
from library.tools.project_registry import _write_project_yaml
from library.schemas.project_config import (
    ProjectConfig,
    ProjectStatus,
    PipelineConfig,
    ResolveConfig,
    SourceConfig,
)


# ─── Migration Plan ──────────────────────────────────────────

MIGRATIONS = [
    {
        "label": "Lucie Content - GEO Podcast",
        "source": REPO_ROOT / "projects" / "lucie-content" / "geo-podcast",
        "dest_client": "lucie",
        "dest_slug": "geo-podcast",
        "project_config": {
            "name": "GEO Podcast",
            "client": "lucie",
            "status": "in_progress",
            "description": "Geography-focused podcast episode with Craig and Akshita",
            "tags": ["podcast", "interview", "vertical", "shortform"],
            "source_type": "iphone_mov",
            "resolve_project_name": "GEO Podcast",
            "resolve_folder": "Lucie Content",
            "brand_template": "cinematic_narrative",
        },
    },
    {
        "label": "Lucie Content - Brand Assets",
        "source": REPO_ROOT / "projects" / "lucie-content" / "brand-assets",
        "dest_client": "lucie",
        "dest_slug": "_shared",
        "dest_subdir": "brand-assets",
        "skip_project_yaml": True,
    },
    {
        "label": "4th Wall - Compositions",
        "source": REPO_ROOT / "projects" / "4th-wall" / "compositions",
        "dest_client": "",
        "dest_slug": "4th-wall",
        "dest_subdir": "compositions",
        "project_config": {
            "name": "4th Wall",
            "client": "",
            "status": "in_progress",
            "description": "4th wall break overlay effects and compositions",
            "tags": ["vfx", "overlay", "remotion"],
            "source_type": "mixed",
            "resolve_project_name": "4th Wall",
            "brand_template": "default_brand",
        },
    },
    {
        "label": "Lucie Podcast - Roughcut Data",
        "source": REPO_ROOT / "Lucie Podcast",
        "dest_client": "lucie",
        "dest_slug": "podcast-roughcut",
        "project_config": {
            "name": "Lucie Podcast Roughcut",
            "client": "lucie",
            "status": "complete",
            "description": "Podcast roughcut with EDL and timeline structure JSON",
            "tags": ["podcast", "roughcut", "longform"],
            "source_type": "mixed",
            "resolution": "1920x1080",
            "resolve_project_name": "Lucie Podcast",
            "resolve_folder": "Lucie Content",
            "brand_template": "default_brand",
        },
    },
]


def compute_plan(execute: bool, symlink: bool) -> list[dict]:
    """Compute the migration plan without executing it."""
    actions = []

    for migration in MIGRATIONS:
        source = migration["source"]
        if not source.exists():
            actions.append({
                "type": "skip",
                "label": migration["label"],
                "reason": f"Source not found: {source}",
            })
            continue

        # Determine destination
        client = migration.get("dest_client", "")
        slug = migration["dest_slug"]
        subdir = migration.get("dest_subdir", "")

        if client:
            dest_base = PROJECTS_ROOT / client / slug
        else:
            dest_base = PROJECTS_ROOT / slug

        if subdir:
            dest = dest_base / subdir
        else:
            dest = dest_base

        # Count files
        file_count = sum(1 for _ in source.rglob("*") if _.is_file())
        dir_count = sum(1 for _ in source.rglob("*") if _.is_dir())
        total_size = sum(f.stat().st_size for f in source.rglob("*") if f.is_file())

        action = {
            "type": "move",
            "label": migration["label"],
            "source": str(source),
            "dest": str(dest),
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
            "create_project_yaml": not migration.get("skip_project_yaml", False),
            "symlink": symlink,
        }

        if not migration.get("skip_project_yaml", False):
            action["project_config"] = migration.get("project_config", {})

        actions.append(action)

    return actions


def execute_plan(actions: list[dict]) -> None:
    """Execute the migration plan."""
    for action in actions:
        if action["type"] == "skip":
            print(f"  SKIP: {action['label']} - {action['reason']}")
            continue

        source = Path(action["source"])
        dest = Path(action["dest"])

        print(f"\n  Moving: {action['label']}")
        print(f"    From: {source}")
        print(f"    To:   {dest}")
        print(f"    Files: {action['file_count']}, Dirs: {action['dir_count']}, Size: {action['total_size_mb']}MB")

        # Create destination parent
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Copy (not move) first so we can verify
        if dest.exists():
            print(f"    WARNING: Destination already exists, merging...")
            # Copy tree contents into existing directory
            for item in source.iterdir():
                src_item = source / item.name
                dst_item = dest / item.name
                if src_item.is_dir():
                    if dst_item.exists():
                        # Merge directory contents
                        for sub_item in src_item.rglob("*"):
                            rel = sub_item.relative_to(src_item)
                            dst_sub = dst_item / rel
                            if sub_item.is_dir():
                                dst_sub.mkdir(parents=True, exist_ok=True)
                            else:
                                dst_sub.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(str(sub_item), str(dst_sub))
                    else:
                        shutil.copytree(str(src_item), str(dst_item))
                else:
                    shutil.copy2(str(src_item), str(dst_item))
        else:
            shutil.copytree(str(source), str(dest))

        print(f"    Copied successfully")

        # Create standard project subdirectories if this is a project root
        if action.get("create_project_yaml") and "project_config" in action:
            pc = action["project_config"]
            project_root = dest

            # Ensure standard dirs exist
            for subdir in ["raw", "pipeline_output", "exports", "brand_assets", "compositions"]:
                (project_root / subdir).mkdir(parents=True, exist_ok=True)

            # Write project.yaml
            config = ProjectConfig(
                name=pc.get("name", ""),
                slug=dest.name,
                client=pc.get("client", ""),
                created=time.strftime("%Y-%m-%d"),
                status=ProjectStatus(pc.get("status", "draft")),
                source=SourceConfig(
                    type=pc.get("source_type", "iphone_mov"),
                    resolution=pc.get("resolution", "1080x1920"),
                ),
                pipeline=PipelineConfig(
                    brand_template=pc.get("brand_template", "default_brand"),
                ),
                resolve=ResolveConfig(
                    project_name=pc.get("resolve_project_name", pc.get("name", "")),
                    folder=pc.get("resolve_folder", pc.get("client", "")),
                ),
                tags=pc.get("tags", []),
                description=pc.get("description", ""),
            )
            config._project_root = project_root

            yaml_path = project_root / "project.yaml"
            if not yaml_path.exists():
                _write_project_yaml(yaml_path, config)
                print(f"    Created project.yaml")
            else:
                print(f"    project.yaml already exists, skipping")

        # Remove source and optionally leave symlink
        if action.get("symlink"):
            shutil.rmtree(str(source))
            source.symlink_to(dest)
            print(f"    Left symlink: {source} -> {dest}")
        else:
            shutil.rmtree(str(source))
            print(f"    Removed source directory")

    print(f"\n  Migration complete!")


def print_plan(actions: list[dict]) -> None:
    """Pretty-print the migration plan."""
    print(f"\n  Migration Plan")
    print(f"  {'═' * 60}")
    print(f"  Projects root: {PROJECTS_ROOT}")
    print()

    total_files = 0
    total_size = 0

    for action in actions:
        if action["type"] == "skip":
            print(f"  ⏭  {action['label']}")
            print(f"     {action['reason']}")
            continue

        print(f"  📦  {action['label']}")
        print(f"     {action['source']}")
        print(f"     → {action['dest']}")
        print(f"     {action['file_count']} files, {action['dir_count']} dirs, {action['total_size_mb']}MB")
        if action.get("create_project_yaml"):
            print(f"     + project.yaml will be created")
        if action.get("symlink"):
            print(f"     + symlink will be left at source")
        print()

        total_files += action["file_count"]
        total_size += action["total_size_mb"]

    print(f"  {'─' * 60}")
    print(f"  Total: {total_files} files, {total_size:.1f}MB")
    print()
    print(f"  This is a DRY RUN. To execute:")
    print(f"  python3 scripts/migrate_projects.py --execute")
    print(f"  python3 scripts/migrate_projects.py --execute --symlink  (leave backward-compat symlinks)")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate in-repo project data to PROJECTS_ROOT",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually move files (default is dry-run)",
    )
    parser.add_argument(
        "--symlink", action="store_true",
        help="Leave symlinks at old locations for backward compatibility",
    )
    args = parser.parse_args()

    # Ensure PROJECTS_ROOT exists
    if not PROJECTS_ROOT.exists():
        if args.execute:
            PROJECTS_ROOT.mkdir(parents=True)
            print(f"  Created projects root: {PROJECTS_ROOT}")
        else:
            print(f"  Projects root will be created: {PROJECTS_ROOT}")

    actions = compute_plan(args.execute, args.symlink)

    if args.execute:
        print(f"\n  Executing migration...")
        execute_plan(actions)
    else:
        print_plan(actions)


if __name__ == "__main__":
    main()
