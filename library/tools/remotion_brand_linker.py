"""
Remotion render prep - stages brand assets before rendering.

The Remotion engine (remotion-subtitles/) has its src/compositions/ directory
symlinked to the shared assets folder (PIPELINE_REMOTION_COMPOSITIONS).
Compositions are always available through the symlink - no staging needed.

Brand assets (logos, fonts) are project-specific and need to be copied into
Remotion's public/brand/ directory before rendering so staticFile("brand/...")
resolves correctly.

Usage:
    from library.tools.remotion_brand_linker import prep_remotion, cleanup_remotion

    # Before rendering
    result = prep_remotion(project_folder="/path/to/project")

    # After rendering (optional cleanup)
    cleanup_remotion()
"""

import os
import shutil
from pathlib import Path

_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from library.tools.paths import REMOTION_DIR
except ImportError:
    REMOTION_DIR = _PILOT_ROOT / "remotion-subtitles"

REMOTION_BRAND_DIR = REMOTION_DIR / "public" / "brand"


# ─── Brand Assets ─────────────────────────────────────────────

def find_brand_assets(project_folder: str) -> Path | None:
    """Find brand assets for a project.

    Search order:
    1. <project>/brand_assets/remotion-brand/
    2. <project>/brand_assets/
    3. <projects_root>/<client>/_shared/brand-assets/remotion-brand/
    4. <projects_root>/<client>/_shared/brand-assets/
    """
    project_path = Path(project_folder)

    for sub in ("brand_assets/remotion-brand", "brand_assets"):
        p = project_path / sub
        if p.is_dir() and any(p.iterdir()):
            return p

    client_dir = project_path.parent
    for sub in ("_shared/brand-assets/remotion-brand", "_shared/brand-assets"):
        p = client_dir / sub
        if p.is_dir() and any(p.iterdir()):
            return p

    return None


def link_brand_assets(project_folder: str) -> dict:
    """Copy brand assets into Remotion's public/brand/ directory.

    Copies brand files (SVG, PNG, fonts) from the project's brand_assets
    into Remotion's public/brand/ so they're accessible via
    staticFile("brand/...") during rendering.
    """
    brand_source = find_brand_assets(project_folder)

    if brand_source is None:
        return {
            "linked": False,
            "reason": f"No brand assets found for project at {project_folder}",
        }

    REMOTION_BRAND_DIR.mkdir(parents=True, exist_ok=True)

    brand_extensions = {
        ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".otf", ".ttf", ".woff", ".woff2",
    }
    copied_files = []

    for f in brand_source.iterdir():
        if f.suffix.lower() in brand_extensions:
            dest = REMOTION_BRAND_DIR / f.name
            shutil.copy2(str(f), str(dest))
            copied_files.append(f.name)

    return {
        "linked": True,
        "source": str(brand_source),
        "target": str(REMOTION_BRAND_DIR),
        "files": copied_files,
        "count": len(copied_files),
    }


def cleanup_brand_assets() -> dict:
    """Remove brand assets from Remotion's public/brand/ directory."""
    if not REMOTION_BRAND_DIR.exists():
        return {"cleaned": False, "reason": "Brand directory doesn't exist"}

    removed = []
    for f in REMOTION_BRAND_DIR.iterdir():
        if f.name != "README.md":
            f.unlink()
            removed.append(f.name)

    return {"cleaned": True, "removed": removed, "count": len(removed)}


# ─── Convenience aliases ──────────────────────────────────────

def prep_remotion(project_folder: str = "", **_kwargs) -> dict:
    """Prep Remotion for rendering: stage brand assets.

    Compositions are always available via the symlink from
    src/compositions/ -> PIPELINE_REMOTION_COMPOSITIONS.
    Only brand assets need to be staged per-project.
    """
    result = {}

    if project_folder:
        result["brand"] = link_brand_assets(project_folder)
    else:
        result["brand"] = {"linked": False, "reason": "No project_folder provided"}

    return result


def cleanup_remotion() -> dict:
    """Clean up after rendering."""
    return {"brand": cleanup_brand_assets()}
