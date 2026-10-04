"""render_frame's ffmpeg call reaches the binary as argv, never a shell.

The skill helper built its ffmpeg command with `os.system` and
f-string interpolation, so an output dir or name prefix carrying shell
syntax (`"; touch pwned; echo "`) escaped the intended command. The
defect names this test: hostile paths must arrive as single argv
elements with no shell involved.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODNAME = "render_frame_under_test"
SCRIPT = (Path(__file__).resolve().parents[3] / ".agents" / "skills"
          / "davinci_resolve_pipeline" / "scripts" / "render_frame.py")


def _load(monkeypatch):
    import library.tools.resolve_lock as lock
    monkeypatch.setattr(lock, "under_lease",
                        lambda *args, **kwargs: (lambda fn: fn))
    monkeypatch.delitem(sys.modules, MODNAME, raising=False)
    spec = importlib.util.spec_from_file_location(MODNAME, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODNAME] = module
    spec.loader.exec_module(module)
    return module


class _Project:
    def SetRenderSettings(self, settings):
        pass

    def AddRenderJob(self):
        return 1

    def StartRendering(self):
        pass

    def IsRenderingInProgress(self):
        return False

    def DeleteAllRenderJobs(self):
        pass


class _Resolve:
    def OpenPage(self, _page):
        pass


def test_hostile_paths_reach_ffmpeg_as_single_argv_elements(
        tmp_path, monkeypatch):
    module = _load(monkeypatch)
    hostile_dir = tmp_path / 'evil"; touch pwned; echo "'
    hostile_dir.mkdir()
    mov = hostile_dir / "frame_3.mov"
    mov.write_bytes(b"x" * 5000)
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    out = module.render_frame_to_png(
        _Resolve(), _Project(), 3, output_dir=str(hostile_dir))
    argv = calls["argv"]
    assert isinstance(argv, list), argv
    assert argv[:3] == ["ffmpeg", "-y", "-i"]
    assert argv[3] == str(mov)
    assert argv[4:6] == ["-frames:v", "1"]
    assert argv[6] == str(hostile_dir / "frame_3.png")
    assert "shell" not in calls["kwargs"]
    assert out == str(hostile_dir / "frame_3.png")
    assert not (tmp_path / "pwned").exists()
