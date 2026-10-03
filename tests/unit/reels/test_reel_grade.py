"""The declared CDL reaches reel picture clips, CDL-first: it resolves from
the same declaration the Fusion half reads, `SetCDL` lands on node 1 of
footage picture items only, and the pixel proof (skipped without the
grade-variant stills) shows the split comes from the CDL.

History: `docs/evidence/series_look.md` (test_reel_grade_cdl.py).
"""
from __future__ import annotations
import os
import sys
from types import SimpleNamespace
import numpy as np
import pytest
import yaml
from dataclasses import dataclass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

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
# `tests/tooling/test_tests_never_reach_real_projects.py` refuses one in a test
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


# --------------------------------------------------------------------------
# From test_reel_grade_through_fusion.py
#
# A declared look reaches every reel picture row through Fusion at the
# declared values (contrast emitted verbatim, Fusion's neutral is 0.0);
# no declared look means no grade keys.
#
# History: docs/evidence/series_look.md.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.series_look import resolve_look

# A declared look's magnitudes, fixed here so the test never reaches a
# real project (AGENTS.md 8). The values exercise the machinery; the
# name is deliberately not any project's chosen look.


@dataclass
class _Clip:
    source_file: str
    track_type: str = "video"
    track_index: int = 1
    timeline_start: float = 0.0
    source_in: float = 0.0


def _placement(source_file, record_frame, seconds, track_index=1, fps=24.0):
    return {
        "clip": _Clip(source_file, track_index=track_index),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": "SpeakerTwo",
    }


def _two_row_plan_2():
    return {"video_tracks": [
        {"role": "a_roll", "occupant": "1", "index": 1},
        {"role": "a_roll", "occupant": "2", "index": 2},
    ]}


# ── 1. The declaration reaches the tool in the tool's own units ───────────

def test_declared_contrast_reaches_the_tool_verbatim():
    """0.12 declared is a pivot gain with 0 neutral, and so is Fusion's
    own Contrast: 0.0 renders byte-identical to no node at all. Emitting
    1.12 for a declared 0.12 shipped eight times the grade."""
    comp = build_effect_comp({"grade_contrast": 0.12}, 120,
                             source_res=(1080, 1920))
    assert "Contrast = Input { Value = 0.12, }," in comp
    assert "Contrast = Input { Value = 1.12, }," not in comp


# ── 2. The reels path merges the grade onto every picture row ─────────────

def test_reel_manifest_merges_grade_onto_both_picture_rows():
    grade = resolve_look(TEST_LOOK).fusion()
    placements = [
        _placement("/a.mxf", 0, 5.0, track_index=1),
        _placement("/b.mxf", 120, 5.0, track_index=2),
    ]
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, [], 24.0,
        track_plan=_two_row_plan_2(), grade_look=dict(grade))
    per_clip = manifest["fusion_effects"]["per_clip"]
    assert set(manifest["tracks"]) == {"V1", "V2"}
    for label in ("reel_picture_00", "reel_picture_01"):
        effects = per_clip[label]
        assert effects["grade_contrast"] == pytest.approx(0.12)
        assert effects["glow_gain"] == pytest.approx(0.20)
        assert effects["film_grain"] is True
        assert effects["vignette"] is True
    # The switch animation still rides the first and last footage clip.
    assert per_clip["reel_picture_00"]["tv_power_head"] is True
    assert per_clip["reel_picture_01"]["tv_power_tail"] is True
    # The compile_manifest rule, unchanged: a value the planner asked
    # for wins over the look's.
    motion = [{"target_block_position": 0, "effect_type": "slow_zoom_in",
               "params": {"zoom_start": 1.0, "zoom_end": 1.04,
                          "grade_contrast": 0.05}}]
    planned = reel_look.fusion_manifest(
        [_placement("/a.mxf", 0, 10.0)], {"power": {}}, motion, 24.0,
        grade_look=dict(grade))
    assert planned["fusion_effects"]["per_clip"]["reel_picture_00"][
        "grade_contrast"] == pytest.approx(0.05)


def test_no_grade_look_means_no_grade_keys_on_reels():
    """A project that declares no look gets no grade on its reels
    either - not a comp with quiet values in it."""
    placements = [_placement("/a.mxf", 0, 10.0)]
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, [], 24.0, grade_look=None)
    effects = manifest["fusion_effects"]["per_clip"]["reel_picture_00"]
    assert "grade_contrast" not in effects
    assert "glow_gain" not in effects
    assert "film_grain" not in effects
    assert "vignette" not in effects
    assert effects["tv_power_head"] is True


# ── 3. The grade look resolves from the project's own project.yaml ────────


# --------------------------------------------------------------------------
# From test_reel_power_grade.py
#
# The declared PowerGrade is THE grade on a reel, and the CDL rides inside it.
#
# `SetCDL` returns True and can change nothing; a DRX applied and read back
# by node is the route that delivers. The pixel measurements behind this:
# docs/evidence/reel_look.md.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.color_page_grade import ColorPageGradeError

TEST_CDL = {
    "slope_r": 1.03, "slope_g": 1.0, "slope_b": 0.96,
    "offset_r": -0.01, "offset_g": 0.005, "offset_b": 0.02,
    "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
    "saturation": 1.12,
}

PROVENANCE = {"source": "built in the Resolve GUI for this test",
              "authorised_by": "test", "licence": "test fixture"}

# The eight nodes the reference `.drx` really builds, read back off the
# live graph. Named here so a fake graph is the shape Resolve returned,
# not a shape invented to make a test pass.
REAL_LABELS = ("Input", "BAL/EXP", "CONTRAST", "SAT",
               "W&B", "Output", "FLC", "Corrections")


# ── fakes ────────────────────────────────────────────────────────────────

class _FakeGraph:
    def __init__(self, item):
        self._item = item

    def GetNumNodes(self):
        return len(self._item.labels)

    def GetNodeLabel(self, index):
        return self._item.labels[index - 1]

    def ApplyGradeFromDRX(self, path, mode):
        self._item.drx_calls.append((path, mode))
        if self._item.drx_result:
            self._item.labels = list(REAL_LABELS)
        return self._item.drx_result


class _FakeItem_2:
    def __init__(self, path, name=None, drx_result=True, cdl_result=True,
                 labels=("",)):
        self._path = path
        self._name = name or os.path.basename(path)
        self.labels = list(labels)
        self.drx_calls = []
        self.cdl_calls = []
        self.drx_result = drx_result
        self._cdl_result = cdl_result

    def GetMediaPoolItem(self):
        item = self

        class _MPI:
            def GetClipProperty(self, key):
                return item._path if key == "File Path" else item._name
        return _MPI()

    def GetName(self):
        return self._name

    def GetNodeGraph(self):
        return _FakeGraph(self)

    def SetCDL(self, values):
        self.cdl_calls.append(dict(values))
        return self._cdl_result


class _FakeTimeline_2:
    def __init__(self, by_row):
        self._by_row = by_row

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return list(self._by_row.get(index, []))


def _plan():
    return {"video_tracks": [
        {"role": "a_roll", "occupant": "1", "index": 1},
        {"role": "a_roll", "occupant": "2", "index": 2},
        {"role": "frame", "occupant": "", "index": 3},
    ]}


def _declare(tmp_path, **extra):
    drx = tmp_path / "look.drx"
    drx.write_text("<Gallery::GyStill/>")
    block = {"path": "look.drx", "provenance": dict(PROVENANCE)}
    block.update(extra)
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t", "color": {"power_grade_drx": block}}))
    return drx


# ── 1. the declaration ───────────────────────────────────────────────────


def test_a_cdl_node_that_is_not_a_label_is_refused(tmp_path):
    for bad in (2, ["BAL/EXP"]):
        _declare(tmp_path, cdl_node=bad)
        with pytest.raises(ColorPageGradeError, match="cdl_node"):
            reel_look.resolve_power_grade(str(tmp_path))


# ── 2. the routing: a DRX REPLACES the CDL route, never joins it ─────────

def test_a_declared_drx_is_the_route_and_the_bare_cdl_is_not_applied():
    """`ApplyGradeFromDRX` replaces the whole node graph including the
    node `SetCDL` writes, so running both would leave whichever ran
    second and call it the grade."""
    item = _FakeItem_2("/footage/a.mxf")
    timeline = _FakeTimeline_2({1: [item]})
    record = reel_look.apply_grade(
        timeline, _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf"})
    assert record["route"] == "power_grade_drx"
    assert item.drx_calls == [("/look.drx", 0)]
    assert item.cdl_calls == []
    assert record["applied"] == ["a.mxf"]
    # verified by a node readback: a re-fetched graph carries the nodes
    # the file builds, which is the whole reason this is the route
    assert record["nodes"]["a.mxf"] == 8
    assert record["verified"] is True
    assert record["warnings"] == []


# ── 3. the CDL lands INSIDE the applied grade, on the named node ─────────

def test_the_cdl_lands_on_the_named_node_of_the_applied_graph():
    """`BAL/EXP` is index 2 of the eight nodes the reference grade
    builds, and that is the node its own README says a per-clip
    exposure and balance correction is dialled into."""
    item = _FakeItem_2("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    assert record["warnings"] == []
    (call,) = item.cdl_calls
    assert call["NodeIndex"] == "2"          # BAL/EXP, found by LABEL
    assert call["Slope"] == "1.0300 1.0000 0.9600"
    assert call["Offset"] == "-0.0100 0.0050 0.0200"
    assert call["Saturation"] == "1.1200"
    assert record["cdl_landed_on"]["a.mxf"]["landed"] is True
    # A grade whose nodes sit in a different order still gets the CDL
    # on the node that MEANS exposure, because the label is what is
    # matched.
    item = _FakeItem_2("/footage/a.mxf")
    reordered = ("Input", "FLC", "CONTRAST", "BAL/EXP", "Output")

    class _Reordered(_FakeGraph):
        def ApplyGradeFromDRX(self, path, mode):
            self._item.drx_calls.append((path, mode))
            self._item.labels = list(reordered)
            return True

    item.GetNodeGraph = lambda: _Reordered(item)
    reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    (call,) = item.cdl_calls
    assert call["NodeIndex"] == "4"


def test_a_missing_label_refuses_the_cdl_rather_than_guessing_node_1():
    """Node 1 of the reference grade is its INPUT colour-space
    transform. Writing an exposure correction into it because a label
    was misspelt would replace the conversion the whole grade is built
    on, so the CDL is dropped BY NAME instead."""
    item = _FakeItem_2("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "EXPOSURE"},
        footage_sources={"/footage/a.mxf"})
    assert item.drx_calls                      # the look still landed
    assert item.cdl_calls == []                # the correction did not
    assert record["cdl_landed_on"] == {}
    assert any("cdl_node_missing" in w for w in record["warnings"])


# ── 5. a refusal is recorded, never raised, and never silent ─────────────

def test_a_clip_resolve_refuses_is_recorded_and_the_reel_continues():
    bad = _FakeItem_2("/footage/a.mxf", drx_result=False)
    good = _FakeItem_2("/footage/b.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [bad], 2: [good]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf", "/footage/b.mxf"})
    assert record["applied"] == ["b.mxf"]
    assert any("a.mxf" in w and "ApplyGradeFromDRX" in w
               for w in record["warnings"])
    # a SetCDL refusal inside the graph is recorded by name
    item = _FakeItem_2("/footage/a.mxf", cdl_result=False)
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == ["a.mxf"]       # the look landed
    assert record["cdl_landed_on"]["a.mxf"]["landed"] is False
    assert any("BAL/EXP" in w for w in record["warnings"])


# ── 6. SetCDL RETURNS TRUE AND CHANGES NOTHING ───────────────────────────
#
# The regression this module exists for (docs/evidence/reel_look.md):
# do not report a grade this route claims to have applied.

def test_setcdl_true_is_not_evidence_the_grade_landed():
    """A clip whose `SetCDL` returns True but whose picture never
    changes is EXACTLY the measured failure, and the record must not
    call that a grade. `verified` is False and the reason carries the
    numbers, so a reader of a build record can tell what happened
    without re-running Resolve."""
    item = _FakeItem_2("/footage/a.mxf", cdl_result=True)
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade=None, footage_sources={"/footage/a.mxf"},
        allow_unverified_cdl=True)
    assert item.cdl_calls, "the call is still made"
    assert record["applied"] == ["a.mxf"], "SetCDL returned True"
    assert record["verified"] is False, (
        "a True return from SetCDL must never be recorded as a verified "
        "grade - it returned True on six clips that stayed ungraded")
    assert "2,073,600" in record["unverified_because"]
    assert "601,760" in record["unverified_because"]


def test_a_look_with_no_drx_is_refused_rather_than_reported():
    """The pipeline REFUSES rather than writing `applied` against clips
    nothing can be shown to have reached."""
    item = _FakeItem_2("/footage/a.mxf")
    with pytest.raises(reel_look.ReelLookRefused) as excinfo:
        reel_look.apply_grade(
            _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
            power_grade=None, footage_sources={"/footage/a.mxf"})
    message = str(excinfo.value)
    assert "power_grade_drx" in message, "the refusal names the fix"
    assert "2,073,600" in message, "the refusal carries the measurement"
    assert item.cdl_calls == [], "nothing was applied on the way out"


def test_declaring_no_look_at_all_is_not_a_refusal():
    """A project that declares nothing gets nothing, quietly - that is
    an absence, not an unverifiable grade (AGENTS.md 10.1)."""
    item = _FakeItem_2("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), {}, power_grade=None,
        footage_sources={"/footage/a.mxf"})
    assert record["route"] == "none"
    assert record["verified"] is False


def test_a_drx_that_says_yes_and_lands_no_nodes_is_not_verified():
    """The same lie in the other mechanism: `ApplyGradeFromDRX` returns
    True and the graph still reads the bare node. Applied, NOT verified,
    and the warning says to treat the reel as ungraded."""
    class _Liar(_FakeItem_2):
        def GetNodeGraph(self):
            item = self

            class _G(_FakeGraph):
                def ApplyGradeFromDRX(self, path, mode):
                    item.drx_calls.append((path, mode))
                    return True          # says yes, adds no nodes
            return _G(item)

    item = _Liar("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline_2({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == ["a.mxf"]
    assert record["nodes"] == {"a.mxf": 1}
    assert record["verified"] is False
    assert any("not VERIFIED" in w for w in record["warnings"])
