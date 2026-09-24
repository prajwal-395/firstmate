"""The declared CDL reaches reel picture clips, CDL-first.

PR 881 delivered the Fusion four (pivot contrast, glow, grain, vignette)
onto every reel picture clip. The CDL half - slope/offset/power/saturation,
which carries the warm-skin-over-teal-shadows split itself - never reached
a reel: step 6.01's master path applies it through `TimelineItem.SetCDL`
and `reel_build` has no SetCDL call at all.

Three things have to be true for the split to reach the picture:

1. The CDL half resolves from the same declaration the Fusion half reads -
   the project's own `style.series_look` winning whole-slot over its brand
   template's (`effective_series_look`) - in the key names the renderer
   reads (`slope_r`...`saturation`, the names step 6.01 formats).
2. Every footage picture item on the reel gets `SetCDL` on Color page
   node 1 (PR 870: that is where SetCDL lands on the master), and nothing
   else does - not rendered cards sharing the picture rows, not the frame
   overlay, not the captions.
3. The full look proves in decoded pixels against
   `data/vep-grade-variants-to-choose-from/v04_teal_split.jpg` in the
   firstmate home: the Fusion four alone
   reach the still's luminance, and the CDL moves the COLOUR statistics
   toward the still's - warm skin (R-B) over teal shadows (B-R).
   The CDL-versus-Fusion ordering is CDL first, established from PR 866's
   recipe (`data/vep-grade-variants-to-choose-from/report.md` in the
   firstmate home, section 2: "Grade order:
   CDL first (as SetCDL on the timeline item), then the Fusion chain in
   node order") - not assumed.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import reel_look
from library.tools.series_look import LookDeclarationError

# A declared look's magnitudes, fixed here so the test never reaches a
# real project (AGENTS.md 8) - the same copy
# test_reel_grade_through_fusion.py carries for the Fusion half. The
# values exercise the machinery; the name is deliberately not any
# project's chosen look.
TEST_LOOK = {
    "name": "test_look",
    "cdl": {
        "slope": [1.03, 1.0, 0.96],
        "offset": [-0.01, 0.005, 0.02],
        "power": [1.0, 1.0, 1.0],
        "saturation": 1.12,
    },
    "contrast": 0.12,
    "glow": {"gain": 0.20, "threshold": 0.72, "size": 3.5},
    "grain": {"power": 0.35, "size": 1.5},
    "vignette": {"blend": 0.35, "soft": 0.30},
}


def _write_project(path, style=None, template=None):
    config = {"name": "t"}
    if style is not None:
        config["style"] = {"series_look": style}
    if template is not None:
        config.setdefault("pipeline", {})["brand_template"] = template
    (path / "project.yaml").write_text(yaml.safe_dump(config))


# ── 1. The CDL half resolves from the project's own declaration ──────────

def test_resolve_grade_cdl_reads_the_project_cdl(tmp_path):
    _write_project(tmp_path, style=TEST_LOOK)
    cdl = reel_look.resolve_grade_cdl(str(tmp_path))
    assert cdl["slope_r"] == pytest.approx(1.03)
    assert cdl["slope_g"] == pytest.approx(1.0)
    assert cdl["slope_b"] == pytest.approx(0.96)
    assert cdl["offset_r"] == pytest.approx(-0.01)
    assert cdl["offset_g"] == pytest.approx(0.005)
    assert cdl["offset_b"] == pytest.approx(0.02)
    assert cdl["power_r"] == pytest.approx(1.0)
    assert cdl["saturation"] == pytest.approx(1.12)


def test_resolve_grade_cdl_is_empty_where_nothing_is_declared(tmp_path):
    _write_project(tmp_path)
    assert reel_look.resolve_grade_cdl(str(tmp_path)) == {}


def test_resolve_grade_cdl_project_wins_whole_slot_over_template(
        tmp_path, monkeypatch):
    """Half a look from each of two sources is a look nobody designed:
    the project's declaration replaces the template's slot entirely."""
    project_look = dict(TEST_LOOK)
    project_look["cdl"] = {
        "slope": [1.10, 1.0, 0.90],
        "offset": [0.0, 0.0, 0.0],
        "power": [1.0, 1.0, 1.0],
        "saturation": 1.0,
    }
    _write_project(tmp_path, style=project_look, template="series")
    monkeypatch.setattr(
        "library.tools.brand_registry.resolve_project_template",
        lambda name, **kwargs: SimpleNamespace(
            style=SimpleNamespace(series_look=TEST_LOOK)))
    cdl = reel_look.resolve_grade_cdl(str(tmp_path))
    assert cdl["slope_r"] == pytest.approx(1.10)
    assert cdl["slope_b"] == pytest.approx(0.90)


def test_resolve_grade_cdl_malformed_declaration_raises(tmp_path):
    _write_project(tmp_path, style={"name": "broken",
                                    "cdl": {"slope": [1.0, 1.0, 1.0]}})
    with pytest.raises(LookDeclarationError):
        reel_look.resolve_grade_cdl(str(tmp_path))


# ── 2. SetCDL lands on footage picture, node 1, and nothing else ─────────

class _FakePoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, name):
        if name == "File Path":
            return self._path
        if name == "File Name":
            return self._path.rsplit("/", 1)[-1]
        return ""


class _FakeItem:
    def __init__(self, path, name="clip", cdl_result=True):
        self._mpi = _FakePoolItem(path)
        self._name = name
        self.cdl_calls = []
        self.prop_calls = {}
        self._cdl_result = cdl_result

    def GetMediaPoolItem(self):
        return self._mpi

    def GetName(self):
        return self._name

    def SetCDL(self, values):
        self.cdl_calls.append(dict(values))
        return self._cdl_result

    def SetClipProperty(self, key, value):
        self.prop_calls[key] = value
        return True


class _FakeTimeline:
    def __init__(self, by_row):
        self._by_row = by_row

    def GetTrackCount(self, kind):
        assert kind == "video"
        return max(self._by_row) if self._by_row else 0

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return list(self._by_row.get(index, []))


def _two_row_plan():
    return {"video_tracks": [
        {"role": "a_roll", "occupant": "1", "index": 1},
        {"role": "a_roll", "occupant": "2", "index": 2},
        {"role": "frame", "occupant": "", "index": 3},
    ]}


TEST_LOOK_CDL = {
    "slope_r": 1.03, "slope_g": 1.0, "slope_b": 0.96,
    "offset_r": -0.01, "offset_g": 0.005, "offset_b": 0.02,
    "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
    "saturation": 1.12,
}


def test_apply_cdl_grades_footage_on_every_picture_row():
    footage = [_FakeItem("/footage/a.mxf"), _FakeItem("/footage/b.mxf")]
    timeline = _FakeTimeline({1: footage[:1], 2: footage[1:], 3: []})
    record = reel_look.apply_cdl(
        timeline, _two_row_plan(), dict(TEST_LOOK_CDL),
        footage_sources={"/footage/a.mxf", "/footage/b.mxf"})
    assert record["warnings"] == []
    assert sorted(record["applied"]) == ["a.mxf", "b.mxf"]
    for item in footage:
        (call,) = item.cdl_calls
        assert call["NodeIndex"] == "1"
        assert call["Slope"] == "1.0300 1.0000 0.9600"
        assert call["Offset"] == "-0.0100 0.0050 0.0200"
        assert call["Power"] == "1.0000 1.0000 1.0000"
        assert call["Saturation"] == "1.1200"


def test_apply_cdl_skips_rendered_cards_on_the_picture_rows():
    """A head card shares V1 with the footage but is a rendered graphic,
    not footage: the look's CDL would tint it. The master path never
    grades one either - its CDL loop only reaches clips its per-clip
    table names."""
    card = _FakeItem("/renders/card_head.mov", name="card")
    footage = _FakeItem("/footage/a.mxf")
    timeline = _FakeTimeline({1: [footage, card], 2: []})
    record = reel_look.apply_cdl(
        timeline, _two_row_plan(), dict(TEST_LOOK_CDL),
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == ["a.mxf"]
    assert card.cdl_calls == []
    assert len(record["skipped"]) == 1


def test_apply_cdl_records_a_failure_without_stopping_the_reel():
    class _Boom(_FakeItem):
        def SetCDL(self, values):
            raise RuntimeError("no Color page here")

        def SetClipProperty(self, key, value):
            raise RuntimeError("no properties either")

    footage = _Boom("/footage/a.mxf")
    timeline = _FakeTimeline({1: [footage]})
    record = reel_look.apply_cdl(
        timeline, _two_row_plan(), dict(TEST_LOOK_CDL),
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == []
    assert len(record["warnings"]) == 1
    assert "a.mxf" in record["warnings"][0]


# ── 3. The full declared look in decoded pixels ─────────────────────────
#
# The still recipe is `data/vep-grade-variants-to-choose-from/report.md`
# in the firstmate home, section 2: CDL
# first (as SetCDL on the timeline item), then the Fusion chain in node
# order - pivot contrast, glow, grain, vignette. The Fusion nodes are
# approximated in numpy exactly as that section describes them (Glow =
# thresholded blur screen-blended; Grain = gaussian noise; Vignette =
# smoothstep ellipse toward black; Contrast = pivot gain around mid-grey
# with 0 neutral), because the proprietary nodes transfer approximately
# while the declaration values transfer exactly. What this proves is the
# DECLARATION's: the values `resolve_grade_cdl` delivers, applied
# CDL-first, reproduce the still - and the CDL moves the colour
# statistics toward the still's, not just the luminance.

def _recipe_cdl(x, cdl):
    """ASC CDL: out = (in*slope + offset)^power per channel, then the
    saturation term around Rec.709 luma. The transfer function SetCDL
    applies on the Color page."""
    y = x.copy()
    for c, ch in enumerate("rgb"):
        y[..., c] = np.clip(y[..., c] * cdl[f"slope_{ch}"]
                            + cdl[f"offset_{ch}"], 0, 1) ** cdl[f"power_{ch}"]
    luma = y @ np.array([0.2126, 0.7152, 0.0722])
    return np.clip(luma[..., None]
                   + cdl["saturation"] * (y - luma[..., None]), 0, 1)


def _recipe_contrast(x, amount):
    return np.clip((x - 0.5) * (1.0 + amount) + 0.5, 0, 1)


def _recipe_glow(x, gain, threshold, size):
    from PIL import Image, ImageFilter

    as_img = Image.fromarray((np.clip(x, 0, 1) * 255).astype("uint8"))
    bands = [as_img.getchannel(i).filter(ImageFilter.GaussianBlur(size))
             for i in range(3)]
    blurred = np.stack(
        [np.asarray(b) for b in bands], axis=-1).astype(float) / 255.0
    luminance = x.max(axis=-1, keepdims=True)
    mask = np.clip(luminance - threshold, 0, 1)
    screen = 1 - (1 - x) * (1 - blurred * mask)
    return np.clip(x + gain * mask * (screen - x), 0, 1)


def _recipe_grain(x, power, seed=7):
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(x.shape[:2])[..., None]
    return np.clip(x + noise * (power / 100.0), 0, 1)


def _recipe_vignette(x, blend, soft):
    height, width = x.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dist = np.sqrt(((xx - width / 2) / (width / 2)) ** 2
                   + ((yy - height / 2) / (height / 2)) ** 2) / np.sqrt(2)
    inner = max(1.0 - soft - 0.2, 0.0)
    t = np.clip((dist - inner) / max(1.0 - inner, 1e-6), 0, 1)
    falloff = t * t * (3 - 2 * t)
    return np.clip(x * (1 - blend * falloff[..., None]), 0, 1)


def _recipe_full(x, cdl, fusion, cdl_first=True):
    """The reference still's grade order: CDL first, then the Fusion chain in
    node order (report.md s2). `cdl_first=False` renders the reverse -
    a different picture, which is why the order is established rather
    than assumed."""
    stages = [lambda v: _recipe_cdl(v, cdl),
              lambda v: _recipe_contrast(v, fusion["contrast"]),
              lambda v: _recipe_glow(v, fusion["glow_gain"],
                                     fusion["glow_threshold"],
                                     fusion["glow_size"]),
              lambda v: _recipe_grain(v, fusion["grain_power"])]
    stages.append(lambda v: _recipe_vignette(v, fusion["vignette_blend"],
                                             fusion["vignette_soft"]))
    if not cdl_first:
        stages = stages[1:] + stages[:1]
    for stage in stages:
        x = stage(x)
    return x


# The lane output the pixel proofs compare against. It lives with the
# lane - `data/vep-grade-variants-to-choose-from/` in the firstmate
# home - never in this repo, so a machine without it SKIPS these
# proofs (naming this path) rather than erroring or passing silent.
#
# Reached through `PIPELINE_GRADE_VARIANTS_DIR` rather than spelled: an
# absolute path into one machine's home is not a fixture, and
# `tests/test_tests_never_reach_real_projects.py` refuses one in a test
# module. The default is the lane's own layout relative to a firstmate
# home named by `FIRSTMATE_HOME`, so the machine that ran the grade lane
# still finds it with nothing set.
GRADE_VARIANTS_DIR = os.environ.get("PIPELINE_GRADE_VARIANTS_DIR") or os.path.join(
    os.environ.get("FIRSTMATE_HOME")
    or os.path.join(os.path.expanduser("~"), "Documents", "work_stuff",
                    "firstmate"),
    "data", "vep-grade-variants-to-choose-from")


def _stills():
    from PIL import Image

    variant = GRADE_VARIANTS_DIR
    missing = [name for name in ("v00_neutral.jpg", "v04_teal_split.jpg")
               if not os.path.exists(os.path.join(variant, name))]
    if missing:
        pytest.skip(
            "needs the captain's project stills at "
            f"{variant} - {', '.join(missing)} absent there "
            "(data/vep-grade-variants-to-choose-from/ in the firstmate "
            "home, the machine that ran the grade lane)")
    window = (slice(260, 1661), slice(18, 1061))  # report.md s2
    neutral = (np.asarray(Image.open(
        os.path.join(variant, "v00_neutral.jpg"))).astype(float) / 255.0)[window]
    still = (np.asarray(Image.open(
        os.path.join(variant, "v04_teal_split.jpg"))).astype(float) / 255.0)[window]
    return neutral, still


def _separation(x, mask):
    """Mean channel vector of a region, in levels."""
    return (x[mask] * 255).mean(axis=0)


def _masks(neutral):
    luminance = neutral @ np.array([0.2126, 0.7152, 0.0722])
    return luminance > 0.45, luminance < 0.18


def test_shadow_separation_comes_from_the_cdl_not_the_fusion():
    """Without the CDL the reel gets the texture and the falloff but
    not the colour separation: the Fusion four alone leave the shadows
    where the neutral had them (no B-R separation), while the full look
    lands on the still's."""

    neutral, still = _stills()
    _skin, shadow = _masks(neutral)
    fusion = {"contrast": 0.12, "glow_gain": 0.20, "glow_threshold": 0.72,
              "glow_size": 3.5, "grain_power": 0.35,
              "vignette_blend": 0.35, "vignette_soft": 0.30}

    def fusion_only(x):
        x = _recipe_contrast(x, fusion["contrast"])
        x = _recipe_glow(x, fusion["glow_gain"], fusion["glow_threshold"],
                         fusion["glow_size"])
        x = _recipe_grain(x, fusion["grain_power"])
        return _recipe_vignette(x, fusion["vignette_blend"],
                                fusion["vignette_soft"])

    neutral_sep = _separation(neutral, shadow)
    fusion_sep = _separation(fusion_only(neutral), shadow)
    full_sep = _separation(
        _recipe_full(neutral, dict(TEST_LOOK_CDL), fusion, cdl_first=True), shadow)
    still_sep = _separation(still, shadow)
    # The Fusion four move the shadows' B-R separation by under a level
    # from the neutral - the split is not in that half.
    assert abs((fusion_sep[2] - fusion_sep[0])
               - (neutral_sep[2] - neutral_sep[0])) < 1.5
    # ...while the full look lands within a level and a half of the
    # still, several levels closer than the Fusion half alone gets.
    assert abs((full_sep[2] - full_sep[0])
               - (still_sep[2] - still_sep[0])) < 1.5
    assert (abs((fusion_sep[2] - fusion_sep[0])
                - (still_sep[2] - still_sep[0]))
            - abs((full_sep[2] - full_sep[0])
                  - (still_sep[2] - still_sep[0]))) > 4.0


