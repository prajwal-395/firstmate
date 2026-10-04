"""The regular render entry points reuse keyed renders unless told fresh."""

import io
import json
import sys

import pytest

# tests/conftest.py owns the non-root step directories these modules need.
from library.steps.step_4_05_render_subtitles import step as subtitles  # noqa: E402
from library.steps.step_4_06_render_motion_graphics import (  # noqa: E402
    post_bridge as graphics,
)


@pytest.mark.parametrize("module,render_name", [
    (subtitles, "render_subtitle_overlays"),
    (graphics, "render_motion_graphics"),
])
@pytest.mark.parametrize("force_fresh,expected_reuse", [
    (False, True),
    (True, False),
])
def test_regular_step_entry_opts_into_reuse_with_force_fresh_escape(
        monkeypatch, module, render_name, force_fresh, expected_reuse):
    seen = {}

    def fake_render(*_args, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(module, render_name, fake_render)
    monkeypatch.setattr(module, "claim_stdout", lambda: None, raising=False)
    monkeypatch.setattr(module, "emit", lambda _value: None, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "force_fresh_render": force_fresh,
    })))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    module.main()

    assert seen["reuse"] is expected_reuse
