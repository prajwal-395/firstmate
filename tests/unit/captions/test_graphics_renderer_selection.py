"""The graphics-engine seam: the project wins over the user, both lose to nothing.

The defect this names: without a precedence test, a later edit can make
the user's machine setting override the project's own declaration (or
make an unset key read as a choice), and every run on that machine
would draw with an engine nobody's video chose while reporting
success. Remotion staying the default is the other half: nothing he
sees changes unless something is selected.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import graphics_renderer as engines  # noqa: E402


def _project_with(tmp_path, value) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project.yaml").write_text(
        f"name: Probe\nslug: probe\npipeline:\n"
        f"  graphics_renderer: {value}\n",
        encoding="utf-8")
    return str(tmp_path)


def test_the_project_wins_over_the_user_and_both_lose_to_nothing(
        tmp_path, monkeypatch):
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    assert engines.resolve_engine(str(tmp_path)) == "remotion"
    assert engines.resolve_engine() == "remotion"
    assert not engines.is_hyperframes(str(tmp_path))

    # The user's machine setting selects, case- and space-insensitively.
    monkeypatch.setenv(engines.USER_SETTING_KEY, "  HyperFrames  ")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"
    assert engines.is_hyperframes(str(tmp_path))

    # The project's own declaration wins, in both directions ...
    for user, project in (("remotion", "hyperframes"),
                          ("hyperframes", "remotion")):
        monkeypatch.setenv(engines.USER_SETTING_KEY, user)
        folder = _project_with(tmp_path / project, project)
        assert engines.resolve_engine(folder) == project
    assert not engines.is_hyperframes(folder)

    # ... and a project that declares nothing falls back to the user.
    monkeypatch.setenv(engines.USER_SETTING_KEY, "hyperframes")
    (tmp_path / "project.yaml").write_text(
        "name: Probe\nslug: probe\npipeline:\n  brand_template: x\n",
        encoding="utf-8")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"


def test_an_unknown_engine_refuses_from_either_source(tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine()
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    folder = _project_with(tmp_path, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine(folder)
