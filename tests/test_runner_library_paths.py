"""The runner's own imports must resolve, and the asset libraries must land.

`run_pipeline.py` put ONE entry on sys.path - `library/tools` - and then
imported two different ways off it:

    from model_lifecycle import unload_all     # works: library/tools is on the path
    from tools.paths import sfx_library_path   # ImportError: library is NOT

All three `from tools.paths import ...` sites sat inside `except
ImportError` handlers, two of which were `pass`. So PIPELINE_SFX_LIBRARY
and PIPELINE_MUSIC_LIBRARY never reached a run. Projects whose
`pipeline_data.json` already carried an `sfx_library` from an earlier era
kept working, which is why it survived: a genuinely fresh project failed
at step 0.01 with "No sfx_library path provided" while the environment had
a valid library the whole time.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video import run_pipeline  # noqa: E402


def test_both_import_styles_the_runner_uses_resolve():
    """Importing the module must make both of its import styles work."""
    import importlib

    # The bare-module style, off library/tools.
    importlib.import_module("model_lifecycle")
    # The package style, off library. This one was broken.
    paths = importlib.import_module("tools.paths")
    assert hasattr(paths, "sfx_library_path")
    assert hasattr(paths, "music_library_path")


def test_fresh_state_picks_up_the_env_asset_libraries(tmp_path, monkeypatch):
    """A project with no pipeline_data.json still gets the shared libraries."""
    sfx = tmp_path / "sfx library"
    music = tmp_path / "music"
    sfx.mkdir()
    music.mkdir()

    monkeypatch.setitem(os.environ, "PIPELINE_SFX_LIBRARY", str(sfx))
    monkeypatch.setitem(os.environ, "PIPELINE_MUSIC_LIBRARY", str(music))

    # paths.py resolves the env vars at import time, so it has to be reloaded
    # for the monkeypatched values to take effect.
    import importlib

    import tools.paths as paths_mod
    importlib.reload(paths_mod)

    project = tmp_path / "project"
    project.mkdir()
    state = run_pipeline.load_pipeline_state(str(project))

    assert state["sfx_library"] == str(sfx), (
        "load_pipeline_state did not inject PIPELINE_SFX_LIBRARY; step 0.01 will fail "
        "any project whose state does not already carry a path."
    )
    assert state["music_library"] == str(music)

    importlib.reload(paths_mod)


def test_missing_paths_module_is_not_swallowed(monkeypatch, tmp_path):
    """If tools.paths ever stops resolving, the run must fail, not continue.

    The old code caught ImportError and carried on with no asset libraries.
    That is what turned a broken sys.path into a step-0.01 mystery.
    """
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *args, **kwargs):
        if name == "tools.paths":
            raise ImportError("blocked for the test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ImportError):
        run_pipeline.load_pipeline_state(str(project))
