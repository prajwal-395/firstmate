"""`deliver-reel` connects to Resolve inside the instance lease."""
from __future__ import annotations

import pytest

from library.tools import reel_build, reel_deliver, resolve_lock


class _Connected(Exception):
    pass


def test_deliver_connects_inside_the_instance_lease(tmp_path, monkeypatch):
    """Reel 01, 2026-10-03: every deliver refused at the handshake,
    because `scriptapp_preserving_locale` refuses outside the lease and
    `deliver_reel` connected before taking one."""
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    (tmp_path / "project.yaml").write_text(
        "resolve:\n  project_name: Scratch\n", encoding="utf-8")
    monkeypatch.setattr(reel_deliver, "resolve_deliver_settings",
                        lambda folder: {"naming": "", "extension": ".mp4"})
    monkeypatch.setattr(reel_deliver, "timeline_name_for_reel",
                        lambda folder, reel: "Reel 01 - x")
    monkeypatch.setattr(reel_deliver, "render_file_name",
                        lambda timeline, naming, ext: "reel.mp4")
    seen = {}

    def connect(name):
        seen["name"] = name
        seen["exclusive"] = resolve_lock.exclusive_held()
        raise _Connected

    monkeypatch.setattr(reel_build, "_connect_resolve_project", connect)
    with pytest.raises(_Connected):
        reel_deliver.deliver_reel(str(tmp_path), 1,
                                  output_dir=str(tmp_path / "out"))
    assert seen == {"name": "Scratch", "exclusive": True}
