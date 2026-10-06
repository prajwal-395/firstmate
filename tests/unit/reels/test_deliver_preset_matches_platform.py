"""A declared deliver preset must be what the named platform accepts.

Covers the platform cross-check in `reel_deliver.resolve_deliver_settings`:
a `pipeline.deliver_preset` codec/container the project's delivery platform
does not accept is refused at the declaration, before a render the platform
re-encodes on arrival.
"""
from __future__ import annotations

import pytest
import yaml

from library.tools import reel_deliver
from library.tools.reel_deliver import DeliverRefused


def _project(tmp_path, pipeline_block):
    cfg = {"name": "T", "slug": "t", "pipeline": pipeline_block}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    return str(tmp_path)


def test_a_matching_preset_resolves(tmp_path):
    """The declared codec and container the platform accepts pass through
    untouched - the check refuses only what the platform rejects."""
    folder = _project(tmp_path, {
        "delivery_platform": "tiktok",
        "deliver_preset": {"format": "mp4", "codec": "H264"},
    })
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["format"] == "mp4"
    assert settings["codec"] == "H264"


def test_case_only_differences_pass(tmp_path):
    """`h264` names the same codec as `H264` - the check compares what the
    declaration MEANS, not its capitalisation."""
    folder = _project(tmp_path, {
        "deliver_preset": {"format": "MP4", "codec": "h264"},
    })
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["format"] == "MP4"
    assert settings["codec"] == "h264"


def test_an_undeclared_preset_never_refuses(tmp_path):
    """Declaring nothing is legitimate: the mechanism's own mp4/H264
    fallback is what the registry rows accept."""
    folder = _project(tmp_path, {"delivery_platform": "master"})
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["preset_declared"] is False
    assert settings["codec"] == "H264"


def test_a_foreign_codec_is_refused_at_the_declaration(tmp_path):
    """The defect: a preset codec no platform row accepts sailed through
    to a render the platform re-encodes on arrival. Now it refuses,
    naming the platform and what it accepts."""
    folder = _project(tmp_path, {
        "delivery_platform": "tiktok",
        "deliver_preset": {"codec": "ProRes"},
    })
    with pytest.raises(DeliverRefused) as exc:
        reel_deliver.resolve_deliver_settings(folder)
    assert "ProRes" in str(exc.value)
    assert "tiktok" in str(exc.value)


def test_a_foreign_container_is_refused_at_the_declaration(tmp_path):
    """Same defect on the container half: only the declared half is
    checked, so a lone foreign format refuses while the codec falls
    back quietly."""
    folder = _project(tmp_path, {
        "deliver_preset": {"format": "mov"},
    })
    with pytest.raises(DeliverRefused) as exc:
        reel_deliver.resolve_deliver_settings(folder)
    assert "mov" in str(exc.value)
