import hashlib
import os
import re

# Generated comps are banked as "<clip label>_<12 hex digest>", and the
# digest is taken over THE COMP'S OWN BYTES.
#
# It used to be taken over the inputs the comp was built from - the
# effects, the clip duration, the source frame, the played length - and
# that is a key over half of what decides the answer.  The other half is
# the code that turns those inputs into nodes, and it changes.  On
# 2026-09-10 the old-TV switch-on was rebuilt from a `Crop` node (which
# had never drawn: `CropTop`/`CropBottom` are not inputs `Crop` has) to a
# masked black `Background`.  The head clip's inputs did not move across
# that change, so its key did not either, and the next build imported the
# pre-fix comp out of the bank verbatim - a 3840x2160 source cropped to
# its bottom-left quadrant, on the captain's finished Reel 09, six
# minutes after the fix had shipped.  The tail clip's `source_in` had
# moved by two seconds, so the tail alone missed the bank and got the
# repaired recipe: one timeline, two builders, told apart by nothing but
# whether a number in the manifest happened to change.
#
# Hashing the bytes ends the class.  A hit now means the banked file IS
# what this build generates, not that it was generated from the same
# request by some earlier version of the engine.  There is nothing left
# to add to the key when the next generator changes, which is the point:
# the previous two repairs each added a field (`source_res`,
# `played_frames`) and each left the same trap armed.
#
# Generating the bytes is string assembly and costs microseconds; the
# expensive call is `ImportFusionComp`, which happens either way.  The
# bank was never saving the work it appeared to save.
_VARIANT_SUFFIX = re.compile(r"^_[0-9a-f]{12}$")


def get_asset_bank_dir(project_folder: str) -> str:
    return os.path.join(project_folder, "assets", "fusion_presets")


def comp_asset_key(label: str, comp_content: str) -> str:
    """Bank name for exactly these comp bytes, under this clip's label.

    The label prefix keeps `find_clip_assets` able to answer "what was
    banked for this clip"; the digest is the content, so two names are
    equal exactly when the two comps are.
    """
    digest = hashlib.sha1(comp_content.encode("utf-8")).hexdigest()[:12]
    return f"{label.lower()}_{digest}"


def bank_comp(project_folder: str, label: str,
              comp_content: str) -> tuple[str, bool]:
    """Put these comp bytes in the bank and return `(path, reused)`.

    `reused` says the bank already held this file, which is a fact about
    the bytes and nothing else - it can never mean "close enough".  The
    caller imports the returned path, so what reaches the timeline is
    always what this build generated.
    """
    path = get_custom_asset(project_folder, comp_asset_key(label, comp_content))
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == comp_content:
                    return path, True
        except OSError:
            pass
    return save_custom_asset(project_folder,
                             comp_asset_key(label, comp_content),
                             comp_content), False


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
