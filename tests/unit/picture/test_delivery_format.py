"""The delivery format: the frame the PRODUCT ships in.

Captain's ruling of 2026-08-19. Before it, `step_1_02_catalog_footage`
derived the render target from the MODAL SOURCE RESOLUTION, so project
001 - 17 landscape clips - shipped a 1920x1080 master with the vertical
overlays banded down the middle, and the whole framing mechanism
(letterbox vs tracked fill) was a no-op because target equalled source.

These tests hold the ruling to the same bar as `series_look` and
`transition_vocabulary`: one enumeration, an unknown name raises, and the
retired key cannot come back.
"""
import ast
import os
import sys

import pytest
import yaml

from library.tools.delivery_format import (
    DEFAULT_DELIVERY_FORMAT,
    DELIVERY_FORMATS,
    delivery_format_name,
    format_names,
    resolve_delivery_format,
    resolve_format_name,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── The enumeration ───────────────────────────────────────────────────

def _project(tmp_path, pipeline_block):
    cfg = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        cfg["pipeline"] = pipeline_block
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    return str(tmp_path)


def test_declaring_nothing_is_vertical_1080x1920(tmp_path):
    """The captain's default. This is the whole point of the ruling, and
    declaring nothing is legitimate rather than an error."""
    assert DEFAULT_DELIVERY_FORMAT == "vertical_1080x1920"
    assert DELIVERY_FORMATS[DEFAULT_DELIVERY_FORMAT] == (1080, 1920)
    assert resolve_delivery_format(None) == [1080, 1920]
    assert resolve_format_name("") == (1080, 1920)
    folder = _project(tmp_path, None)
    assert delivery_format_name(folder) == DEFAULT_DELIVERY_FORMAT
    assert resolve_delivery_format(folder) == [1080, 1920]


def test_an_unknown_format_raises_rather_than_defaulting():
    """A silent fallback is how a landscape master ships again."""
    with pytest.raises(ValueError) as exc:
        resolve_format_name("vertical_9x16")
    assert "vertical_9x16" in str(exc.value)
    for known in format_names():
        assert known in str(exc.value)


# ── Precedence: project override > template > default ─────────────────

def test_project_override_beats_the_template_which_beats_the_default(
        tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "wide_series.yaml").write_text(yaml.safe_dump({
        "series_id": "wide_series",
        "delivery_format": "horizontal_1920x1080",
    }))
    folder = _project(tmp_path, {"brand_template": "wide_series"})
    assert resolve_delivery_format(folder, templates_dir=str(templates)) == [1920, 1080]
    folder = _project(tmp_path, {
        "brand_template": "wide_series",
        "delivery_format": "square_1080x1080",
    })
    assert resolve_delivery_format(folder, templates_dir=str(templates)) == [1080, 1080]


def test_an_unknown_project_or_template_declaration_raises(tmp_path):
    folder = _project(tmp_path, {"delivery_format": "portrait"})
    with pytest.raises(ValueError):
        resolve_delivery_format(folder)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "bad.yaml").write_text(yaml.safe_dump({
        "series_id": "bad", "delivery_format": "9:16",
    }))
    folder = _project(tmp_path, {"brand_template": "bad"})
    with pytest.raises(ValueError):
        resolve_delivery_format(folder, templates_dir=str(templates))


# ── The synthetic project copies ──────────────────────────────────────

# ── The source resolution is a DESCRIPTION, and cannot be a target ────

def test_the_catalog_reports_source_resolution_not_a_render_target():
    """Landscape footage yields a landscape SOURCE description...

    ...and nothing named `project_resolution`. The catalog measuring the
    footage was never the bug; handing that measurement to Resolve as the
    timeline size was.
    """
    from unittest.mock import patch

    from library.steps.step_1_02_catalog_footage.step import catalog_footage

    meta = {
        "duration_seconds": 10.0, "width": 1920, "height": 1080,
        "frame_rate": 30.0, "video_codec": "h264", "audio_codec": "aac",
        "audio_channels": 2, "audio_sample_rate": 48000,
        "creation_time": "2026-01-01T00:00:00Z", "rotation": 0,
        "pixel_format": "yuv420p", "has_audio": True,
    }
    entry = {"path": "/mock/a.mov", "filename": "a.mov", "extension": ".mov",
             "size_bytes": 1000, "clip_id": "clip_001"}
    with patch("library.steps.step_1_02_catalog_footage.step.extract_metadata",
               return_value=meta), patch("os.path.isfile", return_value=True):
        out = catalog_footage([entry])

    assert out["source_resolution"] == [1920, 1080]
    assert "project_resolution" not in out


def test_landscape_footage_still_compiles_to_a_vertical_target(tmp_path):
    """The regression that shipped: source landscape, product vertical.

    `_conform_fields` returned `needs_conform: False` for every clip on
    project 001 because the target WAS the source, so letterbox-vs-fill
    had nothing to decide. Against a vertical target the same clip has a
    real conform decision to make.
    """
    sys.path.insert(0, os.path.join(REPO_ROOT, "library"))
    from library.steps.step_5_04_compile_manifest.step import _conform_fields

    landscape = {"clip_001": {"width": 1920, "height": 1080}}

    same_frame = _conform_fields(landscape, "clip_001", [1920, 1080])
    # `framing_intent` rides along so the render-side occupancy gate can
    # tell a declared letterbox from an accidental one; the conform half
    # is still "nothing to do".
    assert same_frame == {"needs_conform": False, "framing_intent": 1.0,
                          "framing_delivered": 1.0}, (
        "target == source is why the framing mechanism was a no-op"
    )

    vertical = _conform_fields(landscape, "clip_001", [1080, 1920],
                               framing_intent=1.0)
    assert vertical["needs_conform"] is True
    assert vertical["fill_zoom"] > 1.0


SOURCE_DIRS = ("library", "tests", "scripts")


def _python_sources():
    for top in SOURCE_DIRS:
        for root, dirs, files in os.walk(os.path.join(REPO_ROOT, top)):
            dirs[:] = [d for d in dirs if d not in
                       ("__pycache__", "node_modules", ".venv")]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def test_the_retired_key_cannot_come_back():
    """`project_resolution` is retired. Nothing may read or write it.

    It was the modal SOURCE resolution being used as the render target.
    Worse, no DAG edge ever carried it, so every
    `.get("project_resolution", [1080, 1920])` in the tree silently read
    its own fallback - the reader looked wired and was not. A rename only
    fails loudly if the old name stays gone.
    """
    offenders = []
    for path in _python_sources():
        if os.path.basename(path) in ("delivery_format.py",
                                      "test_delivery_format.py"):
            continue  # these two explain the retirement
        with open(path, encoding="utf-8") as f:
            source = f.read()
        if "project_resolution" not in source:
            continue
        # A mention inside a comment or docstring is history, not a read.
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue
            if isinstance(node, ast.Name) and node.id == "project_resolution":
                offenders.append(f"{path}:{node.lineno}")
        stripped = "\n".join(
            line for line in source.splitlines()
            if "project_resolution" in line and not line.lstrip().startswith("#")
        )
        if "project_resolution" in stripped:
            offenders.append(f"{path}: {stripped.strip()[:80]}")
    assert not offenders, (
        "`project_resolution` is retired - use "
        "library/tools/delivery_format.resolve_delivery_format:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


