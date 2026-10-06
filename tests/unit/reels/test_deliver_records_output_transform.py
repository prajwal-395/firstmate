"""`deliver-reel` records the color lane's output transform per deliverable.

Covers the transform half of `reel_deliver.resolve_deliver_settings`:
the output transform the deliverable ships under is CONSUMED from the
color lane's merged declaration (PR 1852) - project override, brand
template, Rec.709 default - and recorded on the settings the deliver
sidecar carries. No color code here: the enumeration, precedence and
validation are the color lane's; this only reads the answer.
"""
from __future__ import annotations

import json

import pytest
import yaml

from library.tools import reel_deliver


def _project(tmp_path, pipeline_block=None, brand=None):
    cfg = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        cfg["pipeline"] = pipeline_block
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    if brand is not None:
        (tmp_path / "brand.json").write_text(json.dumps(brand))
    return str(tmp_path)


def test_declaring_nothing_ships_the_sdr_default(tmp_path):
    """A project that declares nothing ships Rec.709 Gamma 2.4 - the
    SDR delivery the pipeline has always rendered - and the record
    says nobody declared it."""
    settings = reel_deliver.resolve_deliver_settings(_project(tmp_path))
    assert settings["output_transform"] == "rec709_gamma24"
    assert settings["output_color_space"] == "Rec.709, Gamma 2.4"
    assert settings["output_transform_source"] == \
        "default (nothing declared)"


def test_the_project_override_beats_the_template(tmp_path):
    """Precedence mirrors the geometry: a project shipping one video
    under a different transform says so per video."""
    folder = _project(
        tmp_path, {"output_transform": "rec2020_gamma24"},
        brand={"series_id": "s", "output_transform": "rec709_gamma22"})
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["output_transform"] == "rec2020_gamma24"
    assert settings["output_color_space"] == "Rec.2020, Gamma 2.4"
    assert settings["output_transform_source"] == \
        "project.yaml pipeline.output_transform"


def test_the_brand_template_is_the_series_default(tmp_path):
    """The project's own brand.json declares the series default where
    the project says nothing per video."""
    folder = _project(
        tmp_path, {},
        brand={"series_id": "s", "output_transform": "rec709_gamma22"})
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["output_transform"] == "rec709_gamma22"
    assert settings["output_transform_source"] == \
        "brand template output_transform"


def test_an_unknown_project_transform_raises(tmp_path):
    """A silent fallback is how a project ships in a color space nobody
    chose - the color lane raises and delivery does not catch it."""
    folder = _project(tmp_path, {"output_transform": "dci_p3"})
    with pytest.raises(ValueError):
        reel_deliver.resolve_deliver_settings(folder)
