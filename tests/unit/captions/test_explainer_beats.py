"""The BEAT route: an explainer that is PICTURES, timed to the words.

The defect these name, in the captain's own words on the first
full-screen explainer (Reel 26, 2026-10-21):

    "the explainer animation that you made for reel 26 actually sucks,
    it was just the subtitles animated into the screen but it wasn't
    even times. there were no pictures or visual elements that actually
    composed an animation"

Two things were wrong with it, and they are two different defects:

1. **What was drawn was the reel's own words.**  The stage route builds
   ONE plan entry whose copy runs are the stages - so the thing that
   appears IS the caption, in pieces.  A beat route draws a PICTURE per
   beat and reduces the words to a supporting label.

2. **Nothing landed on the words.**  A picture that arrives at a
   moment the declaration chose is a picture beside the speech.  A beat
   is anchored by SEARCH over the reel's own word timings, and the
   offset between the picture and the word is MEASURED off the rendered
   file and reported per beat.

Every test here can fail.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from library.tools import explainer_plan as ex


def _lines():
    """The reel's own words, in reel seconds, with the word timings."""
    return [
        {"at": 0.0,
         "says": "so the best content is content that answers specific questions",
         "words": [{"word": w, "at": t} for w, t in [
             ("so", 0.10), ("the", 0.30), ("best", 0.50), ("content", 0.80),
             ("is", 1.10), ("content", 1.30), ("that", 1.60),
             ("answers", 1.90), ("specific", 2.30), ("questions", 2.70)]]},
        {"at": 3.0,
         "says": "imagine you had a customer and a very specific question",
         "words": [{"word": w, "at": t} for w, t in [
             ("imagine", 3.10), ("you", 3.50), ("had", 3.70), ("a", 3.90),
             ("customer", 4.10), ("and", 4.50), ("a", 4.70),
             ("very", 4.90), ("specific", 5.10), ("question", 5.50)]]},
    ]


def _declaration(**overrides):
    declaration = {
        "beats": [
            {"quote": "answers specific questions",
             "asset": "brand_assets/question.svg",
             "box": [90, 600, 990, 1200],
             "label": "a specific question",
             "label_box": [90, 1250, 990, 1350]},
            {"quote": "customer",
             "asset": "brand_assets/customer.svg",
             "box": [90, 600, 990, 1200]},
        ],
        "ground": "#0B1B2B",
        "font_family": "Montserrat",
        "label_size": 52,
        "hold_seconds": 1.0,
    }
    declaration.update(overrides)
    return declaration


def _resolve(name):
    return f"/project/{name}"


# ── What a beat IS ───────────────────────────────────────────────────

def test_a_beat_with_no_picture_is_refused_not_drawn_as_type():
    """A beat that names no asset draws NO PICTURE.

    The defect: the stage route's stages are copy runs, so a
    declaration that named only words produced a caption rail in
    pieces - "just the subtitles animated into the screen".  A beat
    without a picture is REFUSED, by name, and says why.
    """
    anchored = ex.plan_beats(
        [{"quote": "customer", "box": [0, 0, 10, 10]}],
        _lines(), 10.0, resolve_asset=_resolve)
    assert anchored.beats == []
    assert [r["reason"] for r in anchored.refused] == [ex.NO_PICTURE]
    assert "picture" in anchored.refused[0]["detail"]


def test_a_beat_is_anchored_to_the_word_it_illustrates_not_to_the_line():
    """The anchor is the WORD, and it is said which one answered.

    The two lines here are one sentence each, and the words inside them
    are seconds apart.  A beat anchored to the line would land at 0.00
    and 3.00 - four and one seconds early - and the build would stop
    being a build.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 10.0, resolve_asset=_resolve)
    assert [round(b.at_seconds, 2) for b in anchored.beats] == [1.90, 4.10]
    assert [b.precision for b in anchored.beats] == ["word", "word"]


def test_a_beat_whose_words_the_reel_never_says_is_refused():
    """An ungrounded quote is REFUSED, by name - never drawn."""
    anchored = ex.plan_beats(
        [{"quote": "a question nobody asked", "asset": "a.svg",
          "box": [0, 0, 10, 10]}],
        _lines(), 10.0, resolve_asset=_resolve)
    assert anchored.beats == []
    assert [r["reason"] for r in anchored.refused] == [ex.UNGROUNDED]


def test_a_beat_whose_picture_is_not_on_disk_is_refused():
    """A picture that is not there is a hole in the frame.

    The engine draws no artwork of its own (AGENTS.md 14), so a beat
    names a PROJECT file - and a name that resolves to nothing is
    refused before anything is rendered, not left to a hole.
    """
    anchored = ex.plan_beats(
        [{"quote": "customer", "asset": "missing.svg",
          "box": [0, 0, 10, 10]}],
        _lines(), 10.0, resolve_asset=lambda name: "")
    assert anchored.beats == []
    assert [r["reason"] for r in anchored.refused] == [ex.ASSET_NOT_FOUND]


def test_a_beat_in_another_order_than_the_reel_says_is_refused():
    """The SPEECH is the order, not the declaration."""
    anchored = ex.plan_beats(
        [{"quote": "customer", "asset": "c.svg", "box": [0, 0, 10, 10]},
         {"quote": "answers specific questions", "asset": "a.svg",
          "box": [0, 0, 10, 10]}],
        _lines(), 10.0, resolve_asset=_resolve)
    assert [b.index for b in anchored.beats] == [1]
    assert [r["reason"] for r in anchored.refused] == [ex.OUT_OF_ORDER]


# ── Where the picture lands ──────────────────────────────────────────

def test_the_picture_completes_on_the_word_it_illustrates():
    """The picture arrives BEFORE the word and is COMPLETE on it.

    The measured rule of `docs/ANIMATION_FIRST_REFERENCE.md` §1: the
    reference's illustrations arrive 67-839 ms before the noun they
    illustrate, and a word COMPLETES on its syllable rather than
    beginning there.  So the scene's opacity keyframes run from 0 at
    the arrival to 1 AT the anchor second.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 10.0, resolve_asset=_resolve)
    props = ex.beat_scene_props(
        anchored.beats, _declaration(), width=1080, height=1920,
        fps=23.976, duration_frames=240, start_seconds=0.0)
    image = [l for l in props["layers"] if l["kind"] == "image"][0]
    keys = image["placement"]["opacity"]
    assert keys[0]["value"] == 0.0
    landing = [k for k in keys if round(k["at"], 2) == 1.90]
    assert any(k["value"] == 1.0 for k in landing), keys


def test_one_picture_leaves_as_the_next_arrives():
    """Two pictures are never on the frame together.

    A CUT, by default, because that is what the reference does - "no
    dissolve, no wipe, no whip, no zoom-transition, no flash. Two hard
    cuts and one focus pull" (`docs/ANIMATION_FIRST_REFERENCE.md` §2).
    The exit lands on the NEXT beat's arrival, so the frame carries one
    idea at a time.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 10.0, resolve_asset=_resolve)
    props = ex.beat_scene_props(
        anchored.beats, _declaration(), width=1080, height=1920,
        fps=23.976, duration_frames=240, start_seconds=0.0)
    image = [l for l in props["layers"] if l["kind"] == "image"][0]
    keys = image["placement"]["opacity"]
    # The next beat arrives at 4.10s; the first picture is gone by then.
    exits = [k for k in keys if k["value"] == 0.0]
    assert round(exits[-1]["at"], 2) == 4.10, keys


def test_a_named_entrance_character_starts_the_picture_early():
    """The lead is the CHARACTER's ramp when the declaration names one.

    Not a number anybody chose: it is what `fade` means in this
    renderer, the same reading `MotionGraphics.RAMP_FRAMES` gets.
    """
    anchored = ex.plan_beats(
        [{"quote": "customer", "asset": "c.svg", "box": [0, 0, 10, 10],
          "entrance": "fade"}],
        _lines(), 10.0, resolve_asset=_resolve, fps=24.0)
    assert round(anchored.beats[0].lead_seconds, 4) == round(8 / 24.0, 4)
    assert round(anchored.beats[0].start_seconds, 2) == round(
        4.10 - 8 / 24.0, 2)


def test_a_beat_is_placed_in_an_absolute_canvas_box():
    """The placement is the ABSOLUTE box the declaration states.

    Not a share of the frame: a picture placed by a share moves when
    the frame's aspect changes, and a composition is looked at.  The
    round trip through the renderer's world units is what proves the
    box survives.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 10.0, resolve_asset=_resolve)
    props = ex.beat_scene_props(
        anchored.beats, _declaration(), width=1080, height=1920,
        fps=23.976, duration_frames=240, start_seconds=0.0)
    image = [l for l in props["layers"] if l["kind"] == "image"][0]
    placement = image["placement"]
    left, top, right, bottom = (90, 600, 990, 1200)
    assert round((placement["x"] * 1080) + 1080 / 2, 1) == (left + right) / 2
    assert round((placement["y"] * 1080) + 1920 / 2, 1) == (top + bottom) / 2
    assert round(placement["width"] * 1080, 1) == right - left


def test_the_label_is_a_label_and_not_the_sentence():
    """The words are reduced to a supporting label.

    The caption rail is the caption system's job (AGENTS.md 10.2), and
    a beat route that redrew the sentence would be the defect again.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 10.0, resolve_asset=_resolve)
    props = ex.beat_scene_props(
        anchored.beats, _declaration(), width=1080, height=1920,
        fps=23.976, duration_frames=240, start_seconds=0.0)
    texts = [run["text"]
             for layer in props["layers"] if layer["kind"] == "text"
             for run in layer["runs"]]
    assert texts == ["a specific question"]


# ── What the engine states of its own ────────────────────────────────

def test_the_engine_states_no_ground_no_typeface_and_no_label_size():
    """Every look in a scene is the DECLARATION's (AGENTS.md 10.5)."""
    with pytest.raises(ex.ExplainerError) as missing_ground:
        ex.normalise_beats_declaration(
            {"beats": [{"quote": "customer", "asset": "c.svg",
                        "box": [0, 0, 10, 10]}],
             "font_family": "Montserrat", "hold_seconds": 1.0})
    assert "ground" in str(missing_ground.value)

    with pytest.raises(ex.ExplainerError) as missing_font:
        ex.normalise_beats_declaration(
            {"beats": [{"quote": "customer", "asset": "c.svg",
                        "box": [0, 0, 10, 10]}],
             "ground": "#0B1B2B", "hold_seconds": 1.0})
    assert "font_family" in str(missing_font.value)

    with pytest.raises(ex.ExplainerError) as missing_size:
        ex.normalise_beats_declaration(
            {"beats": [{"quote": "customer", "asset": "c.svg",
                        "box": [0, 0, 10, 10], "label": "a customer",
                        "label_box": [0, 0, 10, 10]}],
             "ground": "#0B1B2B", "font_family": "Montserrat",
             "hold_seconds": 1.0})
    assert "label_size" in str(missing_size.value)


def test_a_box_with_no_area_is_refused_rather_than_drawn():
    """A picture with no area is a hole in the frame, not a picture."""
    with pytest.raises(ex.ExplainerError) as no_area:
        ex.normalise_beats_declaration(
            {"beats": [{"quote": "customer", "asset": "c.svg",
                        "box": [90, 600, 90, 1200]}],
             "ground": "#0B1B2B", "font_family": "Montserrat",
             "hold_seconds": 1.0})
    assert "no area" in str(no_area.value)


def test_a_beat_declaration_is_never_both_routes():
    """`beats` IS the beat route; a declaration without it is the stage
    route.  Never both: two shapes in one slot is a declaration nobody
    wrote."""
    assert ex.is_beat_declaration(_declaration()) is True
    assert ex.is_beat_declaration({"element": "list_build"}) is False
    normalised = ex.normalise_declaration(_declaration())
    assert ex.BEATS_KEY in normalised
    assert "element" not in normalised


# ── What the RENDER did ──────────────────────────────────────────────

def test_the_offset_is_measured_off_the_rendered_file(tmp_path):
    """The offset is MEASURED, not asserted.

    A plan says when a picture was supposed to arrive; only the picture
    settles when it did.  This renders a clip whose box lights up at a
    known second and measures it - and the measurement must find that
    second, not the frame the plan hoped for.
    """
    clip = tmp_path / "scene.mp4"
    made = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=64x64:r=10:d=2",
         "-vf", ("drawbox=x=8:y=8:w=48:h=48:color=white:t=fill:"
                 "enable='gte(t,1.0)'"),
         "-pix_fmt", "yuv420p", "-y", str(clip)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    if made.returncode != 0 or not clip.is_file():
        pytest.skip("ffmpeg could not write the probe clip")

    beat = ex.Beat(index=1, quote="customer", at_seconds=1.0,
                   asset="c.svg", box=(8, 8, 56, 56))
    measured = ex.measure_beat_offsets(
        str(clip), [beat], width=64, height=64, fps=10.0)
    row = measured[0]
    assert row["arrival_seconds"] is not None, row
    assert abs(row["arrival_seconds"] - 1.0) <= 0.15, row
    assert abs(row["arrival_offset_ms"]) <= 150.0, row


def test_a_beat_whose_box_drew_nothing_is_reported_rather_than_scored(tmp_path):
    """A box that never carried a picture has no offset, and says so."""
    clip = tmp_path / "flat.mp4"
    made = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=64x64:r=10:d=1", "-pix_fmt", "yuv420p",
         "-y", str(clip)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    if made.returncode != 0 or not clip.is_file():
        pytest.skip("ffmpeg could not write the probe clip")
    beat = ex.Beat(index=1, quote="customer", at_seconds=0.5,
                   asset="c.svg", box=(8, 8, 56, 56))
    row = ex.measure_beat_offsets(
        str(clip), [beat], width=64, height=64, fps=10.0)[0]
    assert row["arrival_seconds"] is None
    assert "nothing was drawn" in row["note"]


# ── The window the scene covers ──────────────────────────────────────

def test_the_scene_covers_the_whole_reel_unless_the_declaration_narrows_it():
    """An explainer over a reel's own audio is FULL SCREEN.

    A scene that started at its first picture would leave the reel's
    opening on the picture it was supposed to replace, which is the
    half-built composition the captain rejected.
    """
    anchored = ex.plan_beats(
        _declaration()["beats"], _lines(), 30.0, resolve_asset=_resolve)
    assert ex.beat_window(anchored.beats, 30.0, 1.0) == (0.0, 30.0)
    assert ex.beat_window(anchored.beats, 30.0, 1.0, [4.0, 12.0]) == (
        4.0, 12.0)


# ── A beat that BUILDS and MOVES ─────────────────────────────────────

def test_a_beat_may_be_a_composed_scene_not_one_icon():
    """A beat carries LAYERS: a picture and what builds beside it.

    "7 beats over ~35 s means one static icon roughly every 5 s with
    most of the canvas empty - a Vox-style explainer has continuous
    motion and denser composition (elements that build, move and
    transform between beats, not a picture appearing)" (the captain,
    2026-10-21).  A beat of one layer cannot be denser.
    """
    declaration = _declaration()
    declaration["beats"] = [{
        "quote": "customer",
        "label": "your customer asks",
        "label_box": [60, 1330, 1020, 1450],
        "layers": [
            {"asset": "brand_assets/customer.svg",
             "box": [40, 210, 1040, 1210],
             "entrance": "scale", "motion": {"scale_from": 0.86}},
            {"asset": "brand_assets/chip.svg",
             "box": [30, 140, 350, 460],
             "entrance": "slide", "motion": {"from": [-0.16, 0.0]}},
        ],
    }]
    declared = ex.normalise_beats_declaration(declaration)
    anchored = ex.plan_beats(declared[ex.BEATS_KEY], _lines(), 10.0,
                              resolve_asset=_resolve, fps=23.976)
    assert len(anchored.beats) == 1
    assert len(anchored.beats[0].layers) == 2
    props = ex.beat_scene_props(
        anchored.beats, declared, width=1080, height=1920, fps=23.976,
        duration_frames=240, start_seconds=0.0)
    images = [l for l in props["layers"] if l["kind"] == "image"]
    assert len(images) == 2


def test_a_picture_that_grows_carries_its_own_scale():
    """`scale` is a BUILD, and the magnitude is the declaration's.

    The engine holds no number of its own (AGENTS.md 10.5), so `scale`
    without `motion.scale_from` is REFUSED rather than completed from a
    constant.
    """
    declaration = _declaration()
    declaration["beats"] = [{
        "quote": "customer",
        "layers": [{"asset": "brand_assets/customer.svg",
                    "box": [40, 210, 1040, 1210],
                    "entrance": "scale"}],
    }]
    with pytest.raises(ex.ExplainerError) as no_magnitude:
        ex.normalise_beats_declaration(declaration)
    assert "scale_from" in str(no_magnitude.value)

    declaration["beats"][0]["layers"][0]["motion"] = {"scale_from": 0.86}
    declared = ex.normalise_beats_declaration(declaration)
    anchored = ex.plan_beats(declared[ex.BEATS_KEY], _lines(), 10.0,
                              resolve_asset=_resolve, fps=23.976)
    props = ex.beat_scene_props(
        anchored.beats, declared, width=1080, height=1920, fps=23.976,
        duration_frames=240, start_seconds=0.0)
    image = [l for l in props["layers"] if l["kind"] == "image"][0]
    width = image["placement"]["width"]
    assert isinstance(width, list), width
    assert width[-1]["value"] > width[0]["value"], width


def test_a_slide_without_a_direction_is_refused():
    """Where a picture comes from is the declaration's."""
    declaration = _declaration()
    declaration["beats"] = [{
        "quote": "customer",
        "layers": [{"asset": "brand_assets/customer.svg",
                    "box": [40, 210, 1040, 1210],
                    "entrance": "slide"}],
    }]
    with pytest.raises(ex.ExplainerError) as no_direction:
        ex.normalise_beats_declaration(declaration)
    assert "motion.from" in str(no_direction.value)


def test_a_layer_whose_picture_is_not_on_disk_is_refused():
    """A layer that is not there is a hole in the frame."""
    anchored = ex.plan_beats(
        [{"quote": "customer",
          "layers": [{"asset": "brand_assets/missing.svg",
                      "box": [0, 0, 10, 10]}]}],
        _lines(), 10.0, resolve_asset=lambda name: "", fps=23.976)
    assert anchored.beats == []
    assert [r["reason"] for r in anchored.refused] == [ex.ASSET_NOT_FOUND]


def test_a_picture_leaves_the_way_it_came():
    """The EXIT is the reverse of the arrival, over the exit's own ramp.

    A frame that CUTS from one idea to the next is not a
    transformation, and the reference's transitions are hard cuts only
    because its pieces are single objects
    (`docs/ANIMATION_FIRST_REFERENCE.md` §2).
    """
    declaration = _declaration()
    declaration["beats"] = [{
        "quote": "answers specific questions",
        "layers": [{"asset": "brand_assets/grid.svg",
                    "box": [40, 210, 1040, 1210],
                    "entrance": "scale",
                    "motion": {"scale_from": 0.86}}],
        "exit": "scale",
    }, {
        "quote": "customer",
        "layers": [{"asset": "brand_assets/customer.svg",
                    "box": [40, 210, 1040, 1210],
                    "entrance": "fade"}],
    }]
    declared = ex.normalise_beats_declaration(declaration)
    anchored = ex.plan_beats(declared[ex.BEATS_KEY], _lines(), 10.0,
                              resolve_asset=_resolve, fps=23.976)
    props = ex.beat_scene_props(
        anchored.beats, declared, width=1080, height=1920, fps=23.976,
        duration_frames=240, start_seconds=0.0)
    image = [l for l in props["layers"] if l["kind"] == "image"][0]
    width = image["placement"]["width"]
    assert isinstance(width, list), width
    # It grows into place and shrinks back out of it.
    assert width[1]["value"] > width[0]["value"], width
    assert width[-1]["value"] < width[1]["value"], width
