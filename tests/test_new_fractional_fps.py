"""`manage_project.py new --fps` keeps fractional frame rates.

The defect: `cmd_new` passed `int(args.fps)`, so `--fps 23.976`
became 23 while `SourceConfig.fps` is a float. The schema half
already has its test (`test_a_fractional_frame_rate_survives_the_config`);
this pins the CLI half. No project is created here - `create_project`
is stubbed and the call is stopped at its own refusal.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import manage_project  # noqa: E402
from library.tools.ren_refusal import RenRefusal  # noqa: E402


def test_new_with_fractional_fps_reaches_create_project_untruncated(
        monkeypatch):
    """`--fps 23.976` must arrive as 23.976, not 23."""
    seen = {}

    def fake_create_project(**kwargs):
        seen.update(kwargs)
        raise FileExistsError("stop here - the fps value is what matters")

    monkeypatch.setattr(manage_project, "create_project",
                        fake_create_project)
    args = SimpleNamespace(
        slug="t", name="T", client="", template="", source_type="",
        resolution="", fps="23.976", resolve_name="", tags="",
        description="",
    )
    with pytest.raises(RenRefusal):
        manage_project.cmd_new(args)
    assert seen["fps"] == 23.976
