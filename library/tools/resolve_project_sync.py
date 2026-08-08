"""
Resolve project sync - connects project configs to DaVinci Resolve.

This is a lightweight connector that uses the project.yaml config to:
1. Ensure the correct Resolve project exists and is loaded
2. Import project media into the Resolve media pool
3. Export Resolve projects to the project's exports/ directory

All operations use the davinci-resolve MCP server - no direct database
manipulation. The connection is via project name matching.

Usage (from pipeline code):
    from library.tools.resolve_project_sync import (
        ensure_resolve_project,
        import_project_media,
        get_resolve_project_state,
    )

    config = get_project("geo-podcast")
    ensure_resolve_project(config)  # loads/creates the Resolve project
    import_project_media(config)    # imports raw/ into media pool

Usage (standalone CLI):
    python3 -m library.tools.resolve_project_sync check geo-podcast
    python3 -m library.tools.resolve_project_sync open geo-podcast
    python3 -m library.tools.resolve_project_sync import-media geo-podcast
    python3 -m library.tools.resolve_project_sync export geo-podcast
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Add repo root to path
_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PILOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PILOT_ROOT))

from library.schemas.project_config import ProjectConfig


def _get_resolve():
    """Get a connection to DaVinci Resolve via the scripting API.

    Returns the resolve object or None if Resolve is not running.
    """
    try:
        script_modules = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_modules not in sys.path:
            sys.path.append(script_modules)

        os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        )
        os.environ.setdefault(
            "RESOLVE_SCRIPT_LIB",
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        )

        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        return resolve
    except (ImportError, Exception):
        return None


def ensure_resolve_project(config: ProjectConfig, create_if_missing: bool = True) -> dict:
    """Ensure the Resolve project for this config exists and is loaded.

    Uses the Resolve scripting API directly to:
    1. Check if a project with config.resolve.project_name exists
    2. Load it if it does
    3. Create it if create_if_missing is True and it doesn't exist

    Returns a status dict with project info.
    """
    resolve = _get_resolve()
    if resolve is None:
        return {
            "success": False,
            "error": "DaVinci Resolve is not running or not accessible",
        }

    pm = resolve.GetProjectManager()
    if pm is None:
        return {"success": False, "error": "Could not get Project Manager"}

    target_name = config.resolve.project_name
    if not target_name:
        target_name = config.name

    # Navigate to the correct folder if specified
    if config.resolve.folder:
        folder_parts = config.resolve.folder.split("/")
        pm.GotoRootFolder()
        for part in folder_parts:
            if part:
                if not pm.OpenFolder(part):
                    if create_if_missing:
                        pm.CreateFolder(part)
                        pm.OpenFolder(part)
                    else:
                        return {
                            "success": False,
                            "error": f"Resolve folder '{config.resolve.folder}' not found",
                        }

    # Check current project
    current = pm.GetCurrentProject()
    if current and current.GetName() == target_name:
        return {
            "success": True,
            "action": "already_loaded",
            "project_name": target_name,
        }

    # Try to load existing project
    existing_projects = pm.GetProjectListInCurrentFolder()
    if target_name in (existing_projects or []):
        project = pm.LoadProject(target_name)
        if project:
            return {
                "success": True,
                "action": "loaded",
                "project_name": target_name,
            }
        return {
            "success": False,
            "error": f"Project '{target_name}' exists but failed to load",
        }

    # Create new project
    if create_if_missing:
        project = pm.CreateProject(target_name)
        if project:
            # Apply basic settings from config
            if config.source.resolution:
                w, h = config.source.width, config.source.height
                project.SetSetting("timelineResolutionWidth", str(w))
                project.SetSetting("timelineResolutionHeight", str(h))
            if config.source.fps:
                project.SetSetting("timelineFrameRate", str(config.source.fps))

            return {
                "success": True,
                "action": "created",
                "project_name": target_name,
            }
        return {
            "success": False,
            "error": f"Failed to create project '{target_name}'",
        }

    return {
        "success": False,
        "error": f"Project '{target_name}' not found and create_if_missing is False",
    }


def import_project_media(config: ProjectConfig) -> dict:
    """Import raw footage from the project's raw/ directory into Resolve's media pool.

    Returns a status dict with import results.
    """
    resolve = _get_resolve()
    if resolve is None:
        return {"success": False, "error": "Resolve not running"}

    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        return {"success": False, "error": "No project loaded"}

    # Verify we're in the right project
    if project.GetName() != (config.resolve.project_name or config.name):
        return {
            "success": False,
            "error": f"Wrong project loaded: '{project.GetName()}' "
                     f"(expected '{config.resolve.project_name or config.name}')",
        }

    raw_dir = config.raw_dir
    if not raw_dir.exists():
        return {"success": False, "error": f"Raw directory not found: {raw_dir}"}

    # Collect media files
    media_extensions = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".m4v", ".mpg"}
    media_files = [
        str(f) for f in raw_dir.iterdir()
        if f.suffix.lower() in media_extensions
    ]

    if not media_files:
        return {"success": False, "error": f"No media files found in {raw_dir}"}

    # Import via media storage
    ms = resolve.GetMediaStorage()
    if ms:
        imported = ms.AddItemListToMediaPool(media_files)
        return {
            "success": bool(imported),
            "imported_count": len(imported) if imported else 0,
            "total_files": len(media_files),
        }

    # Fallback: import via media pool
    mp = project.GetMediaPool()
    if mp:
        imported = mp.ImportMedia(media_files)
        return {
            "success": bool(imported),
            "imported_count": len(imported) if imported else 0,
            "total_files": len(media_files),
        }

    return {"success": False, "error": "Could not access media storage or media pool"}


def get_resolve_project_state(config: ProjectConfig) -> dict:
    """Get the current state of the Resolve project bound to this config.

    Returns project info without modifying anything.
    """
    resolve = _get_resolve()
    if resolve is None:
        return {"connected": False, "error": "Resolve not running"}

    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        return {"connected": True, "project_loaded": False}

    target_name = config.resolve.project_name or config.name

    return {
        "connected": True,
        "project_loaded": True,
        "current_project": project.GetName(),
        "is_correct_project": project.GetName() == target_name,
        "expected_project": target_name,
        "timeline_count": project.GetTimelineCount(),
    }


def export_resolve_project(config: ProjectConfig, include_media: bool = False) -> dict:
    """Export the Resolve project to the project's exports/ directory.

    Returns the path to the exported .drp file.
    """
    resolve = _get_resolve()
    if resolve is None:
        return {"success": False, "error": "Resolve not running"}

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        return {"success": False, "error": "No project loaded"}

    target_name = config.resolve.project_name or config.name
    if project.GetName() != target_name:
        return {
            "success": False,
            "error": f"Wrong project loaded: '{project.GetName()}' (expected '{target_name}')",
        }

    exports_dir = config.exports_dir
    exports_dir.mkdir(parents=True, exist_ok=True)

    export_path = str(exports_dir / f"{target_name}.drp")

    success = pm.ExportProject(target_name, export_path, include_media)
    return {
        "success": bool(success),
        "export_path": export_path if success else None,
    }


# ─── CLI ──────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Resolve project sync")
    parser.add_argument("command", choices=["check", "open", "import-media", "export", "state"])
    parser.add_argument("slug", help="Project slug")
    args = parser.parse_args()

    from library.tools.project_registry import get_project

    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "check":
        state = get_resolve_project_state(config)
        print(json.dumps(state, indent=2))

    elif args.command == "open":
        result = ensure_resolve_project(config)
        print(json.dumps(result, indent=2))

    elif args.command == "import-media":
        result = import_project_media(config)
        print(json.dumps(result, indent=2))

    elif args.command == "export":
        result = export_resolve_project(config)
        print(json.dumps(result, indent=2))

    elif args.command == "state":
        state = get_resolve_project_state(config)
        print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
