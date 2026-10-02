"""A project may declare where its footage lives, and it is CHECKED.

The gap this closes, measured 2026-09-04: the GEO Podcast field test was
cut by hand in Resolve from footage under `Lucie consulting/Social
Media/podcast media`.  `<project>/raw` was empty, `enumerate_footage`
scans only that directory, and the readiness check therefore refused the
project as having no footage at all.

The alternative fix was to symlink or copy the media into `raw`.  That is
a write to the captain's own material to work around a missing
capability, so the capability exists instead.

What is tested hardest here is the REFUSAL. A declared root that is
relative or absent must not fall back to `raw`: falling back turns a typo
into "this project has no footage", which is the exact unhelpful refusal
this declaration exists to prevent.
"""

from __future__ import annotations

import pytest

from library.tools.footage_identity import (
    enumerate_footage,
    footage_root,
)


def _project(tmp_path, yaml_body=None):
    project = tmp_path / "project"
    (project / "raw").mkdir(parents=True)
    if yaml_body is not None:
        (project / "project.yaml").write_text(yaml_body)
    return project


def _media(tmp_path, *names):
    media = tmp_path / "elsewhere"
    media.mkdir(exist_ok=True)
    for name in names:
        (media / name).write_bytes(b"\x00" * 64)
    return media


def test_the_root_is_raw_unless_declared(tmp_path):
    project = _project(tmp_path)
    assert footage_root(str(project)) == str(project / "raw")
    media = _media(tmp_path, "LC4930.MXF")
    (project / "project.yaml").write_text(
        f"name: T\nslug: t\nsource:\n  footage_root: {media}\n")
    assert footage_root(str(project)) == str(media)


def test_footage_under_a_declared_root_is_enumerated_and_numbered(tmp_path):
    """Including .MXF in capitals - cameras write .MXF, not .mxf."""
    media = _media(tmp_path, "LCATL0011.MXF", "LC4930.MXF")
    project = _project(tmp_path, f"name: T\nslug: t\nsource:\n"
                                 f"  footage_root: {media}\n")
    files, skipped = enumerate_footage(str(project))
    assert [f["filename"] for f in files] == ["LC4930.MXF", "LCATL0011.MXF"]
    assert [f["clip_id"] for f in files] == ["clip_001", "clip_002"]
    assert skipped == []


# ── The refusals ─────────────────────────────────────────────────────

def test_a_bad_declared_root_is_refused_never_defaulted(tmp_path):
    """Falling back to raw would turn a typo into 'no footage'."""
    project = _project(tmp_path, "name: T\nslug: t\nsource:\n"
                                 "  footage_root: ../media\n")
    with pytest.raises(FileNotFoundError, match="relative"):
        footage_root(str(project))

    (project / "project.yaml").write_text(
        "name: T\nslug: t\nsource:\n  footage_root: /nowhere/at/all\n")
    with pytest.raises(FileNotFoundError) as excinfo:
        footage_root(str(project))
    message = str(excinfo.value)
    assert "/nowhere/at/all" in message
    assert "raw" in message, "the refusal must say what it declined to do"


# ── The frame rate a project declares ────────────────────────────────

def test_a_fractional_frame_rate_survives_the_config(tmp_path):
    """`fps` was an int, which silently truncated 23.976 to 23.

    `type`/`resolution` used to be declared beside it and are now
    ignored - nothing ever read them, the catalog measures both off
    the footage. A file that still carries them reads fine.
    """
    from library.schemas.project_config import load_project_config
    project = _project(tmp_path, "name: T\nslug: t\nsource:\n"
                                 "  type: mxf\n"
                                 "  resolution: 3840x2160\n"
                                 "  fps: 23.976\n")
    config = load_project_config(str(project / "project.yaml"))
    assert config.source.fps == 23.976
