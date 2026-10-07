"""Measured icon templates: provenance, refusal, no invented numbers.

Each test names the defect it catches. The registry is a measurement
record, not a config: an entry with no crop and no threshold is a
declaration the resolver treats as no anchor, and a threshold is a
measured score, never a tuned default.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.templates import (
    ICON_TEMPLATES,
    TemplateMissing,
    load_template,
    template_path,
    templates_dir,
)


def test_the_magic_mask_entry_is_measured_not_declared():
    """The toolbar entry carries a crop, a scale and a threshold inside
    the measured true/false gap (true 1.0000, best false 0.5437 on
    three held-out captures): a default threshold here would be tuning
    without data."""
    entry = ICON_TEMPLATES["magic-mask-toolbar"]
    assert entry.threshold == 0.8
    assert entry.cut_scale == 2.0
    assert entry.crop == (1472, 1162, 40, 36)
    assert "1.0000" in entry.source and "0.5437" in entry.source


def test_the_shipped_crop_loads_at_its_recorded_shape():
    """The committed PNG reads back at the size the provenance
    records: a crop that drifted from its record would click the
    wrong point."""
    pixels, entry = load_template("magic-mask-toolbar")
    assert pixels.shape == (36, 40, 3)
    assert entry.name == "magic-mask-toolbar"


def test_an_unknown_template_name_is_refused():
    """A name nobody registered is a refusal naming the registry, not
    a KeyError and not a guess at a file."""
    with pytest.raises(TemplateMissing) as exc:
        template_path("no-such-icon")
    assert exc.value.code == "TEMPLATE_UNKNOWN"


def test_loading_a_template_with_no_file_refuses_with_the_fix(monkeypatch):
    """An entry whose crop file is absent refuses with what to
    capture, instead of crashing on open or matching a stand-in -
    the anchor falls through to whatever it names next."""
    from library.tools.computer_use.templates import ICON_TEMPLATES, IconTemplate

    monkeypatch.setitem(
        ICON_TEMPLATES, "ghost-icon",
        IconTemplate(name="ghost-icon", filename="ghost_icon.png",
                     threshold=None, cut_scale=2.0,
                     source="test-only", crop=(0, 0, 0, 0)))
    with pytest.raises(TemplateMissing) as exc:
        load_template("ghost-icon")
    assert exc.value.code == "TEMPLATE_UNMEASURED"


def test_template_paths_resolve_under_the_engine_root():
    """Template files live with the engine, found from the engine root
    and never the cwd, so verbs work from anywhere."""
    path = template_path("magic-mask-toolbar")
    assert path == templates_dir() / "magic_mask_toolbar.png"
    assert str(path).endswith("library/tools/computer_use/templates/magic_mask_toolbar.png")
