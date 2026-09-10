"""
Resolve media relinker - fixes offline clips when project files move.

This is a general-purpose utility that should be added to the project
management workflow. When files are migrated, run this to relink.

Usage:
    # As a module
    from library.tools.resolve_relinker import relink_project
    relink_project("my-show")

    # As CLI
    python3 -m library.tools.resolve_relinker my-show
    python3 -m library.tools.resolve_relinker --scan  # just detect, don't fix
"""
import sys
import os
from pathlib import Path

_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PILOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PILOT_ROOT))


class DestinationMismatchError(RuntimeError):
    """The current Resolve project does not match the expected destination.

    `relink_project` rewrites the media pool of whatever project is
    open (H12 from the statefulness hazards investigation).  The
    function's `project_slug` argument was used only to build path
    mappings, never to check the open project's name.  Run the relinker
    for project A while project B is open and B's media pool is
    rewritten.  Verify-immediately-before-and-refuse is what protects
    the write.
    """


def _get_resolve():
    """Connect to DaVinci Resolve."""
    try:
        sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
        os.environ.setdefault("RESOLVE_SCRIPT_API", "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting")
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def scan_offline_clips(resolve=None):
    """Scan the media pool for offline clips.

    Returns a list of dicts with clip info and old file paths.
    """
    if resolve is None:
        resolve = _get_resolve()
    if not resolve:
        return []

    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        return []

    mp = project.GetMediaPool()

    def _scan_folder(folder):
        offline = []
        clips = folder.GetClipList()
        if clips:
            for clip in clips:
                file_path = clip.GetClipProperty("File Path")
                if file_path and not os.path.exists(file_path):
                    offline.append({
                        "clip": clip,
                        "name": clip.GetName(),
                        "old_path": file_path,
                    })
        subfolders = folder.GetSubFolderList()
        if subfolders:
            for sub in subfolders:
                offline.extend(_scan_folder(sub))
        return offline

    return _scan_folder(mp.GetRootFolder())


def build_path_mappings(project_slug: str = "") -> list[tuple[str, str]]:
    """Build path mappings for a project migration.

    Generates old->new prefix pairs based on the project registry.
    Also includes common migration patterns.
    """
    mappings = []

    # Common migration: repo projects/ -> external video_projects/
    try:
        from library.tools.paths import PILOT_ROOT, PROJECTS_ROOT
        from library.tools.project_registry import scan_projects

        # For each known project, map old repo paths to new paths
        for config in scan_projects():
            project_root = config.project_root

            # Old patterns that might exist
            old_patterns = [
                PILOT_ROOT / "projects" / config.client / config.slug,
                PILOT_ROOT / "projects" / config.slug,
                PILOT_ROOT / config.name,  # e.g., "Lucie Podcast"
            ]

            for old in old_patterns:
                mappings.append((str(old) + "/", str(project_root) + "/"))

        # Client-level shared assets
        for client_dir in PROJECTS_ROOT.iterdir():
            if client_dir.is_dir() and not client_dir.name.startswith((".", "_")):
                shared = client_dir / "_shared"
                if shared.exists():
                    old_shared = PILOT_ROOT / "projects" / client_dir.name / "brand-assets"
                    mappings.append((str(old_shared) + "/", str(shared / "brand-assets") + "/"))

    except ImportError:
        pass

    return mappings


def relink_project(project_slug: str = "", dry_run: bool = False,
                   expected_project: str = "") -> dict:
    """Relink offline clips in the current Resolve project.

    Scans the media pool for offline clips, builds path mappings,
    and relinks clips whose files exist at the new locations.

    Args:
        project_slug: Optional project slug for targeted mappings
        dry_run: If True, just report what would be relinked
        expected_project: If non-empty, the current Resolve project
            must have this exact name or the function refuses.  This
            is the H12 guard: rewriting a media pool is a mutation
            with no easy reversal, and "not silent" only helps if
            someone is watching.

    Returns a status dict.

    Raises:
        DestinationMismatchError: If expected_project is set and the
            current project does not match.
    """
    resolve = _get_resolve()
    if not resolve:
        return {"success": False, "error": "Resolve not running"}

    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        if expected_project:
            raise DestinationMismatchError(
                f"No Resolve project is open. Expected project "
                f"{expected_project!r}."
            )
        return {"success": False, "error": "No project open"}

    # Verify destination BEFORE any mutation.
    actual_project = project.GetName()
    if expected_project and actual_project != expected_project:
        raise DestinationMismatchError(
            f"Wrong Resolve project: expected {expected_project!r}, "
            f"got {actual_project!r}. Refusing to rewrite the media pool "
            f"of the wrong project."
        )

    offline = scan_offline_clips(resolve)
    if not offline:
        return {"success": True, "offline_count": 0, "message": "No offline clips"}

    mappings = build_path_mappings(project_slug)

    # Match offline clips to new paths
    fixable = []
    unfixable = []

    for clip_info in offline:
        old_path = clip_info["old_path"]
        new_path = None

        for old_prefix, new_prefix in mappings:
            if old_path.startswith(old_prefix):
                candidate = new_prefix + old_path[len(old_prefix):]
                if os.path.exists(candidate):
                    new_path = candidate
                    break

        if new_path:
            clip_info["new_path"] = new_path
            fixable.append(clip_info)
        else:
            unfixable.append(clip_info)

    result = {
        "success": True,
        "offline_count": len(offline),
        "fixable_count": len(fixable),
        "unfixable_count": len(unfixable),
        "dry_run": dry_run,
    }

    if dry_run:
        result["fixable"] = [
            {"name": c["name"], "old": c["old_path"], "new": c["new_path"]}
            for c in fixable
        ]
        result["unfixable"] = [
            {"name": c["name"], "old": c["old_path"]}
            for c in unfixable
        ]
        return result

    # Re-verify destination IMMEDIATELY before the first mutation.
    # The window between the earlier check and now is where another
    # process could have changed the current project.
    actual_project = project.GetName()
    if expected_project and actual_project != expected_project:
        raise DestinationMismatchError(
            f"Project changed between scan and mutation: expected "
            f"{expected_project!r}, got {actual_project!r}. "
            f"Refusing to rewrite the media pool of the wrong project."
        )

    # Execute relinking
    relinked = 0
    failed = 0
    for clip_info in fixable:
        try:
            ok = clip_info["clip"].ReplaceClip(clip_info["new_path"])
            if ok:
                relinked += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    result["relinked"] = relinked
    result["failed"] = failed

    return result


# ─── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Relink offline media in Resolve")
    parser.add_argument("slug", nargs="?", default="", help="Project slug")
    parser.add_argument("--scan", action="store_true", help="Scan only, don't relink")
    parser.add_argument(
        "--expected-project", default="",
        help="Refuse if the current Resolve project does not match this name",
    )
    args = parser.parse_args()

    result = relink_project(
        args.slug, dry_run=args.scan, expected_project=args.expected_project,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
