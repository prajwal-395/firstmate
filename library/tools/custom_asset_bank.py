import hashlib
import json
import os
import glob
import re

# Generated comps are banked as "<clip label>_<12 hex digest>". The digest
# covers everything the comp was built from, so a re-cut that changes a
# clip's duration or effect parameters never reuses the previous run's
# baked keyframes.
_VARIANT_SUFFIX = re.compile(r"^_[0-9a-f]{12}$")


def get_asset_bank_dir(project_folder: str) -> str:
    return os.path.join(project_folder, "assets", "fusion_presets")


def clip_asset_key(label: str, effects: dict, clip_dur,
                   source_res=None, played_frames=None) -> str:
    """Bank key identifying exactly the comp these inputs generate.

    `source_res` is part of the key because the comp's Background nodes
    are built at that size: two clips with identical effects and duration
    but different source frames generate different bytes, and replaying
    one for the other reintroduces the wrong-sized rectangle.

    `played_frames` is part of the key because end-anchored animations
    are clamped to it: the same effects over the same source render
    different keyframes for different played lengths, and replaying a
    pre-clamp comp reintroduces the animation parked past everything
    rendered. Same key means same bytes.
    """
    fingerprint = json.dumps(
        {"effects": effects, "clip_dur": clip_dur,
         "source_res": list(source_res) if source_res else None,
         "played_frames": played_frames},
        sort_keys=True, default=str,
    )
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    return f"{label.lower()}_{digest}"


def find_clip_assets(project_folder: str, label: str) -> list[str]:
    """Paths of every banked comp generated for *label*, any variant.

    Lets a caller find what was banked for a clip without recomputing the
    digest, while lookups that must not reuse a stale comp still go
    through the exact key.
    """
    prefix = label.lower()
    paths = []
    for name in list_custom_assets(project_folder):
        if not name.startswith(prefix):
            continue
        if not _VARIANT_SUFFIX.match(name[len(prefix):]):
            continue
        path = get_custom_asset(project_folder, name)
        if path:
            paths.append(path)
    return sorted(paths)

def list_custom_assets(project_folder: str) -> list[str]:
    """Lists available custom assets (without extensions)."""
    asset_dir = get_asset_bank_dir(project_folder)
    if not os.path.exists(asset_dir):
        return []
    
    assets = []
    for f in os.listdir(asset_dir):
        if f.endswith('.comp') or f.endswith('.setting'):
            name, _ = os.path.splitext(f)
            assets.append(name)
    return sorted(list(set(assets)))

def get_custom_asset(project_folder: str, name: str) -> str:
    """Returns path to custom .setting/.comp.
    Checks for both .comp and .setting extensions."""
    asset_dir = get_asset_bank_dir(project_folder)
    comp_path = os.path.join(asset_dir, f"{name}.comp")
    setting_path = os.path.join(asset_dir, f"{name}.setting")
    
    if os.path.exists(comp_path):
        return comp_path
    if os.path.exists(setting_path):
        return setting_path
    return ""

def save_custom_asset(project_folder: str, name: str, comp_content: str) -> str:
    """Saves a generated .comp as reusable template."""
    asset_dir = get_asset_bank_dir(project_folder)
    os.makedirs(asset_dir, exist_ok=True)
    
    path = os.path.join(asset_dir, f"{name}.comp")
    with open(path, "w", encoding="utf-8") as f:
        f.write(comp_content)
    return path

def import_custom_asset(clip, project_folder: str, name: str) -> bool:
    """Imports custom asset into timeline clip via ImportFusionComp()."""
    asset_path = get_custom_asset(project_folder, name)
    if not asset_path:
        return False
        
    return clip.ImportFusionComp(asset_path)
