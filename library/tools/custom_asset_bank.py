import os
import glob

def get_asset_bank_dir(project_folder: str) -> str:
    return os.path.join(project_folder, "assets", "fusion_presets")

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
