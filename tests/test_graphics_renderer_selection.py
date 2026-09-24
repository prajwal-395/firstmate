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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import graphics_renderer as engines  # noqa: E402


def _project_with(tmp_path, value) -> str:
    (tmp_path / "project.yaml").write_text(
        f"name: Probe\nslug: probe\npipeline:\n"
        f"  graphics_renderer: {value}\n",
        encoding="utf-8")
    return str(tmp_path)


def test_default_is_remotion_with_nothing_selected(tmp_path, monkeypatch):
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    assert engines.resolve_engine(str(tmp_path)) == "remotion"
    assert engines.resolve_engine() == "remotion"
    assert not engines.is_hyperframes(str(tmp_path))


def test_user_setting_selects_hyperframes(tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "hyperframes")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"
    assert engines.is_hyperframes(str(tmp_path))


def test_project_wins_over_user_hyperframes_over_remotion(
        tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "remotion")
    folder = _project_with(tmp_path, "hyperframes")
    assert engines.resolve_engine(folder) == "hyperframes"


def test_project_wins_over_user_remotion_over_hyperframes(
        tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "hyperframes")
    folder = _project_with(tmp_path, "remotion")
    assert engines.resolve_engine(folder) == "remotion"
    assert not engines.is_hyperframes(folder)


def test_project_without_declaration_falls_back_to_user(
        tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "hyperframes")
    (tmp_path / "project.yaml").write_text(
        "name: Probe\nslug: probe\npipeline:\n  brand_template: x\n",
        encoding="utf-8")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"


def test_unknown_user_engine_refuses(monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine()


def test_unknown_project_engine_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    folder = _project_with(tmp_path, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine(folder)


def test_selection_is_case_and_space_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "  HyperFrames  ")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"
