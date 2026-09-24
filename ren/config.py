"""`ren config`: where each machine setting comes from.

The loading itself belongs to `library/tools/paths.py` (environment, then
the per-user file, then the checkout's `.env`, then the code default);
this only reports its answer, and `--init` writes a starter file.
"""

from __future__ import annotations

import argparse
import os
import sys

from ren import REPO_ROOT

SETTINGS = (
    ("PIPELINE_PROJECTS_ROOT", "where projects live"),
    ("PIPELINE_SHARED_ASSETS", "root the three libraries below default under"),
    ("PIPELINE_SFX_LIBRARY", "sound-effect library (audio files + profiles/)"),
    ("PIPELINE_MUSIC_LIBRARY", "music library"),
    ("PIPELINE_REMOTION_COMPOSITIONS", "project-owned Remotion compositions"),
    ("PIPELINE_GRAPHICS_RENDERER", "graphics engine: remotion (default) or hyperframes"),
    ("REN_RETENTION", "lean (purge after sign-off) or keep; default lean"),
    ("HF_TOKEN", "HuggingFace token, for gated model downloads"),
    ("GEMMA_SERVER_URL", "resident vision-model server (optional)"),
    ("RESOLVE_SCRIPT_API", "Resolve scripting API folder"),
    ("RESOLVE_SCRIPT_LIB", "Resolve fusionscript.so"),
)

_SECRET = ("HF_TOKEN",)

STARTER = """\
# Ren's per-user configuration - one file per person, outside every checkout.
# Read by library/tools/paths.py: an exported variable beats this file, and
# this file beats a checkout's .env. `ren config` shows the effective values.
#
# Uncomment and set the paths for this machine.

# Where projects live (`ren init` creates it).
# PIPELINE_PROJECTS_ROOT="$HOME/Movies/Ren/projects"

# Shared asset libraries. The three below default to subfolders of this root.
# PIPELINE_SHARED_ASSETS="$HOME/Movies/Ren/assets"
# PIPELINE_SFX_LIBRARY="$HOME/Movies/Ren/assets/sfx library"
# PIPELINE_MUSIC_LIBRARY="$HOME/Movies/Ren/assets/music"

# What a project keeps once a reel is signed off: `lean` (the default)
# plans a purge of superseded renders, quarantine, scratch and stale
# journals; `keep` retains them all.
# REN_RETENTION=lean

# Which graphics engine draws the programmatic pictures: `remotion`
# (the default - unset means Remotion, so nothing changes unless this
# names hyperframes) or `hyperframes` (the fully open-source second
# renderer). A project's own pipeline.graphics_renderer wins over this.
# PIPELINE_GRAPHICS_RENDERER=remotion

# HuggingFace token, for gated model downloads.
# HF_TOKEN=
"""


def _paths():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import library.tools.paths as paths
    return paths


def effective_settings(environ_before: dict) -> list:
    """`[(key, value, source, meaning)]`: source is "environment", a file path, or "default"."""
    paths = _paths()
    defaults = {
        "PIPELINE_PROJECTS_ROOT": paths.PROJECTS_ROOT,
        "PIPELINE_SHARED_ASSETS": paths.SHARED_ASSETS_ROOT,
        "PIPELINE_SFX_LIBRARY": paths.SFX_LIBRARY,
        "PIPELINE_MUSIC_LIBRARY": paths.MUSIC_LIBRARY,
        "PIPELINE_REMOTION_COMPOSITIONS": paths.REMOTION_COMPOSITIONS,
        "REN_RETENTION": "lean",
        "RESOLVE_SCRIPT_API": paths.RESOLVE_SCRIPT_API,
        "RESOLVE_SCRIPT_LIB": paths.RESOLVE_SCRIPT_LIB,
    }
    rows = []
    for key, meaning in SETTINGS:
        if key in environ_before:
            source = "environment"
        elif key in paths.CONFIG_SOURCES:
            source = paths.CONFIG_SOURCES[key]
        else:
            source = "default"
        value = os.environ.get(key, str(defaults.get(key, "")))
        if key in _SECRET and value:
            value = "(set)"
        rows.append((key, value or "(unset)", source, meaning))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ren config", description=__doc__.splitlines()[0])
    parser.add_argument("--init", action="store_true",
                        help="Write a commented starter file at the config path; "
                             "refuses to overwrite one that exists")
    args = parser.parse_args(argv)

    environ_before = dict(os.environ)
    paths = _paths()
    config_file = paths.user_config_path()

    if args.init:
        if config_file.exists():
            print(f"ren config: {config_file} already exists; left as it is.",
                  file=sys.stderr)
            return 1
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(STARTER, encoding="utf-8")
        print(f"Wrote {config_file}. Edit it, then run `ren doctor`.")
        return 0

    state = "exists" if config_file.is_file() else "not created - `ren config --init`"
    print(f"User config:   {config_file} ({state})")
    checkout_env = paths.CHECKOUT_ENV_FILE
    print(f"Checkout .env: {checkout_env} "
          f"({'exists' if checkout_env.is_file() else 'none'})")
    print("Precedence:    environment > user config > checkout .env > default\n")
    for key, value, source, meaning in effective_settings(environ_before):
        print(f"{key}\n    {value}\n    from {source} - {meaning}")
    return 0
