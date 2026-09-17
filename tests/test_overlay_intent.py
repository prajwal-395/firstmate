"""Declared overlay positions, honoured exactly or refused loudly.

The Reel 09 numbers are the fixtures: 22 captions the captain pinned
to one place, four motion graphics pinned by segment id. What is
asserted is the MECHANISM - segment beats kind beats computed, and
anything malformed refuses - with his values as the data, so a
regression that drops or silently ignores a pin fails here rather
than on his timeline.

A pin names a PLACE, not a transform (version 2). The transform is
computed from it against the canvas going down, so a correction to the
engine's model of the Resolve transform moves nothing that was pinned.
Version 1 held the raw Pan/Tilt and is refused: on 2026-09-11 the law
was corrected and honouring this project's own v1 pins verbatim would
have moved Reel 13's approved captions 108px.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_intent import (  # noqa: E402
    CAPTION_KIND,
    OverlayIntentError,
    load_intent,
    parse_intent,
    resolve,
)
from library.tools.overlay_placement import (  # noqa: E402
    place_overlay_segment,
)

FRAME = (1080, 1920)
#: The caption canvas the Reel 09 pins were measured on.
CANVAS = (840, 480)

#: The Reel 09 pin set, as places: one kind default for all 22
#: captions, one position per motion graphic. The caption centre is
#: the row `Tilt -1700` reaches on a 480-tall canvas - `960 + 1700 *
#: (480/1920)` - so the fixture is the captain's own correction,
#: re-expressed rather than re-decided.
REEL_09_INTENT = {
    "version": 2,
    "targets": {
        "caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1},
        "mg_geo-podcast_19fe552d": {"canvas_centre": [223.7, 899.75],
                                    "scaling": 1},
        "mg_geo-podcast_0c3a697f": {"canvas_centre": [819.0, 960.0],
                                    "scaling": 1},
        "mg_geo-podcast_7a07c11c": {"canvas_centre": [270.5, 960.0],
                                    "scaling": 1},
        "mg_geo-podcast_5e1c3efb": {"canvas_centre": [540.0, 960.0],
                                    "scaling": 1},
    },
}

COMPUTED_CAPTION = {"scaling": 1, "pan": 0.0, "tilt": -1744.0}


#: The 2026-09-11 draw gain: these tests pin routing against pins
#: recorded under that calibration (see HISTORY_GAIN in
#: test_tight_box.py). Today's gain is proven separately
#: (`tests/test_draw_gain_measured.py`) and by the rebuild gate.
HISTORY_GAIN = 1.0


def _resolve(kind, segment_id, computed, intent, canvas=CANVAS,
             placement_label=None):
    return resolve(kind, segment_id, computed, intent,
                   canvas=canvas, frame=FRAME,
                   placement_label=placement_label,
                   draw_gain=HISTORY_GAIN)


def test_kind_default_pins_every_caption_alike():
    intent = parse_intent(REEL_09_INTENT)
    for segment_id in ("sub_akshita_x_1491914-1493721_6bdaa694",
                       "sub_craig_y_1421571-1426656_8f6f0b4c",
                       "sub_anything_unseen_before"):
        placement, provenance = _resolve(CAPTION_KIND, segment_id,
                                         COMPUTED_CAPTION, intent)
        assert provenance == "declared"
        assert placement == {"pan": 0.0, "tilt": -1700.0, "scaling": 1}, (
            "the place resolves to the transform the captain set")


def test_segment_pin_beats_kind_default():
    intent = parse_intent({
        "version": 2,
        "targets": {
            "caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1},
            "sub_special": {"canvas_centre": [541.25, 1360.0],
                            "scaling": 1},
        },
    })
    placement, provenance = _resolve(
        CAPTION_KIND, "sub_special", COMPUTED_CAPTION, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -1600.0


def test_motion_graphics_have_no_kind_fallback():
    intent = parse_intent(REEL_09_INTENT)
    placement, provenance = _resolve(
        "semantic visual", "mg_geo-podcast_unseen",
        {"scaling": 1, "pan": 99.0, "tilt": 9.0}, intent)
    assert provenance == "computed"
    assert placement == {"scaling": 1, "pan": 99.0, "tilt": 9.0}


def test_unpinned_keeps_computed():
    placement, provenance = _resolve(
        CAPTION_KIND, "sub_x", COMPUTED_CAPTION, {})
    assert provenance == "computed"
    assert placement == COMPUTED_CAPTION
    assert placement is not COMPUTED_CAPTION


def test_nothing_computed_nothing_declared_is_no_transform():
    placement, provenance = _resolve("caption", "sub_x", None, {})
    assert (placement, provenance) == (None, "computed")


def test_partial_pin_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 2,
                      "targets": {"caption": {"scaling": 1}}})


def test_non_numeric_pin_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 2, "targets": {
            "caption": {"canvas_centre": [540.0, "low"], "scaling": 1}}})


def test_a_centre_that_is_not_a_pair_is_refused():
    for bad in (1385.0, [540.0], [540.0, 1385.0, 0.0], {"y": 1385.0}):
        with pytest.raises(OverlayIntentError):
            parse_intent({"version": 2, "targets": {
                "caption": {"canvas_centre": bad, "scaling": 1}}})


def test_a_version_1_file_is_refused_and_says_how_to_migrate():
    """The raw-transform spelling cannot be reinterpreted safely.

    Its numbers mean nothing without the canvas they were measured on,
    so this reader refuses rather than guessing - and names the shape
    to re-express them in.
    """
    with pytest.raises(OverlayIntentError) as excinfo:
        parse_intent({"version": 1, "targets": {
            "caption": {"pan": 0.0, "tilt": -1700.0, "scaling": 1}}})
    assert "canvas_centre" in str(excinfo.value)
    assert "version 2" in str(excinfo.value)


def test_wrong_version_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 7, "targets": {}})


def test_a_pin_the_placer_cannot_size_raises_rather_than_reading_honoured():
    """A pin needs the canvas to become a transform, and a pin that
    silently did not apply is the whole failure this module exists to
    stop."""
    intent = parse_intent(REEL_09_INTENT)
    with pytest.raises(OverlayIntentError) as excinfo:
        resolve(CAPTION_KIND, "sub_x", COMPUTED_CAPTION, intent,
                canvas=None, frame=FRAME)
    assert "cannot be honoured" in str(excinfo.value)


def test_one_place_is_two_transforms_on_two_canvases():
    """The reason a place is the right unit: the SAME pin reaches the
    same screen row from canvases of different heights, with different
    stored numbers - which a raw Pan/Tilt pin cannot do."""
    from library.tools.resolve_transform import drawn_origin

    intent = parse_intent(REEL_09_INTENT)
    rows = []
    for canvas in ((840, 480), (840, 540)):
        placement, _ = _resolve(CAPTION_KIND, "sub_x", None, intent,
                                canvas=canvas)
        _ox, oy = drawn_origin(canvas[0], canvas[1], *FRAME,
                               placement["pan"], placement["tilt"])
        rows.append((placement["tilt"], oy + canvas[1] / 2.0))
    assert rows[0][0] != rows[1][0], "different canvases, different numbers"
    assert rows[0][1] == pytest.approx(rows[1][1]), "one place"


def test_non_object_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent([])


def test_missing_project_file_is_no_intent(tmp_path):
    assert load_intent(str(tmp_path)) == {}


def test_missing_explicit_file_is_refused(tmp_path):
    with pytest.raises(OverlayIntentError):
        load_intent(intent_file=str(tmp_path / "absent.json"))


def test_malformed_file_is_refused(tmp_path):
    path = tmp_path / "overlay_intent.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverlayIntentError):
        load_intent(intent_file=str(path))


def test_explicit_file_loads(tmp_path):
    path = tmp_path / "overlay_intent.json"
    path.write_text(json.dumps(REEL_09_INTENT), encoding="utf-8")
    intent = load_intent(intent_file=str(path))
    placement, provenance = _resolve(
        CAPTION_KIND, "sub_x", COMPUTED_CAPTION, intent)
    assert (placement["tilt"], provenance) == (-1700.0, "declared")


class _Item:
    """A timeline item fake: records SetProperty, serves GetStart."""

    def __init__(self, start):
        self._start = start
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        return True

    def GetProperty(self, prop=None):
        if prop is None:
            return dict(self.set_calls)
        return self.set_calls[prop]


class _Pool:
    def __init__(self, result=True):
        self._result = result

    def AppendToTimeline(self, specs):
        return self._result


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return self._items


def test_declared_intent_wins_over_computed_on_the_timeline():
    """Reel 09 captions: computed -1744.0, declared -1700.0 - the
    placed item carries the declared position, end to end through
    the production placer.

    The declared number follows the measured draw gain: the pin
    centre [540, 1385] resolves to -850 under today's renderer
    (it resolved to -1700 under the 2026-09-11 gain). What this
    test pins is that the declared place wins, whichever gain
    computes it.
    """
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement=dict(COMPUTED_CAPTION),
        label="seg", kind="caption", segment_id="sub_x",
        intent=parse_intent(REEL_09_INTENT),
        canvas=CANVAS, frame=FRAME)
    assert ok and note == ""
    assert item.set_calls == {"Scaling": 1, "Pan": 0.0, "Tilt": -850.0}


def test_no_intent_keeps_computed_on_the_timeline():
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement=dict(COMPUTED_CAPTION),
        label="seg", kind="caption", segment_id="sub_x",
        intent=None)
    assert ok and note == ""
    assert item.set_calls["Tilt"] == -1744.0


#: Two segments off one Craig clip on Reel 13: same speaker, same
#: source clip, different source spans. The captain's own ids, which
#: is the point - a re-key that merged these two would bind his pin
#: to the neighbour's overlay.
SPAN_A_OLD = "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf"
SPAN_A_NEW = "sub_craig_341446bc-389b-468c-9add_1853716-1855056_fdc48282"
SPAN_B_OLD = "sub_craig_341446bc-389b-468c-9add_1855196-1856821_6b66c72d"
SPAN_B_NEW = "sub_craig_341446bc-389b-468c-9add_1855196-1856821_c39e8475"

PIN_A = {"canvas_centre": [540.0, 1385.0], "scaling": 1}
PIN_B = {"canvas_centre": [540.0, 960.0], "scaling": 1}


def test_a_pin_written_under_the_old_hash_still_resolves_exact():
    """Backward compatibility: where the render still stands under
    the recorded filename, the pin binds it exactly as before. No
    captain record is orphaned by the re-key."""
    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    placement, provenance = _resolve(
        CAPTION_KIND, SPAN_A_OLD, COMPUTED_CAPTION, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -1700.0


def test_a_pin_survives_a_rerender_under_its_prefix():
    """The 2026-09-13 wipe: the artefact re-rendered under a new
    content hash, the provenance prefix unchanged. The pin recorded
    against the old filename still puts the overlay where the
    captain put it."""
    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    placement, provenance = _resolve(
        CAPTION_KIND, SPAN_A_NEW, COMPUTED_CAPTION, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -1700.0


def test_two_same_speaker_segments_with_different_spans_stay_distinct():
    """The widened key must not bind the wrong artefact: two pins
    for two spans off one clip each resolve their own re-render,
    and to different places. Fails if prefix matching merges them."""
    intent = parse_intent({"version": 2,
                           "targets": {SPAN_A_OLD: PIN_A,
                                       SPAN_B_OLD: PIN_B}})
    place_a, prov_a = _resolve(CAPTION_KIND, SPAN_A_NEW,
                               COMPUTED_CAPTION, intent)
    place_b, prov_b = _resolve(CAPTION_KIND, SPAN_B_NEW,
                               COMPUTED_CAPTION, intent)
    assert (prov_a, prov_b) == ("declared", "declared")
    assert place_a["tilt"] == -1700.0
    assert place_b["tilt"] != place_a["tilt"], (
        "span B must resolve its own pin, never span A's")


def test_a_pin_never_claims_a_span_it_does_not_name():
    """One pin for span A, and span B re-renders: B keeps the
    computation. The prefix widens the match from one filename to
    one overlay, never to the neighbour."""
    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    placement, provenance = _resolve(
        CAPTION_KIND, SPAN_B_NEW, COMPUTED_CAPTION, intent)
    assert provenance == "computed"
    assert placement == COMPUTED_CAPTION


def test_an_mg_pin_matches_exactly_never_by_project_prefix():
    """Motion-graphics names carry no stable prefix - the whole
    suffix is content - so an mg pin binds its own artefact or
    nothing. Stripping to `mg_geo-podcast` would match all 43
    graphics on the captain's project."""
    from library.tools.overlay_intent import resolve as mg_resolve

    intent = parse_intent(
        {"version": 2,
         "targets": {"mg_geo-podcast_622f69cb": PIN_A}})
    same, prov_same = mg_resolve(
        "semantic visual", "mg_geo-podcast_622f69cb",
        COMPUTED_CAPTION, intent, canvas=CANVAS, frame=FRAME)
    assert prov_same == "declared"
    other, prov_other = mg_resolve(
        "semantic visual", "mg_geo-podcast_a072b160",
        COMPUTED_CAPTION, intent, canvas=CANVAS, frame=FRAME)
    assert (other, prov_other) == (COMPUTED_CAPTION, "computed")


def test_two_pins_claiming_one_prefix_refuse_rather_than_guess():
    """The captain pinned one overlay twice under two hashes. No
    claimant wins quietly - the refusal names both, so the stale
    one can be retired."""
    from library.tools.overlay_intent import OverlayIntentError

    intent = parse_intent(
        {"version": 2,
         "targets": {SPAN_A_OLD: PIN_A, SPAN_A_NEW: PIN_B}})
    with pytest.raises(OverlayIntentError) as excinfo:
        _resolve(CAPTION_KIND,
                 "sub_craig_341446bc-389b-468c-9add_1853716-1855056_00000000",
                 COMPUTED_CAPTION, intent)
    message = str(excinfo.value)
    assert SPAN_A_OLD in message and SPAN_A_NEW in message


def test_unmatched_is_prefix_aware_and_still_reports_the_dead():
    """`unmatched` applies the same lookup `resolve` does: a pin
    whose overlay re-rendered is matched, not reported. What is
    reported is genuinely unbound - the Reel 13 span no reel
    captions any more."""
    from library.tools.overlay_intent import unmatched

    intent = parse_intent(
        {"version": 2,
         "targets": {
             SPAN_A_OLD: PIN_A,
             "sub_craig_58b86d7d-824a-44d5-9b0e_1898556-1901284_724fbe6c":
                 PIN_A,
         }})
    assert unmatched(intent, [SPAN_A_NEW]) == [
        "sub_craig_58b86d7d-824a-44d5-9b0e_1898556-1901284_724fbe6c"]


def test_report_unmatched_names_the_pin_aloud(capsys):
    """The silence is the defect: a pin that matches nothing must
    print its own name. Fails if the report goes quiet."""
    from library.tools.overlay_intent import report_unmatched

    dead = "sub_craig_58b86d7d-824a-44d5-9b0e_1898556-1901284_724fbe6c"
    intent = parse_intent({"version": 2,
                           "targets": {SPAN_A_OLD: PIN_A, dead: PIN_A}})
    missed = report_unmatched(intent, [SPAN_A_NEW],
                              source="overlay_intent.json (Reel 13)")
    assert missed == [dead]
    err = capsys.readouterr().err
    assert dead in err, "an unmatched pin is REPORTED, not dropped"


def test_report_unmatched_is_quiet_when_every_pin_bound(capsys):
    from library.tools.overlay_intent import report_unmatched

    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    assert report_unmatched(intent, [SPAN_A_NEW]) == []
    assert capsys.readouterr().err == ""


# ── A hand-set zoom rides beside the place, never in `scaling` ────────
#
# Reel 01's graphic, moved AND scaled down 12%: `scaling` is Resolve's
# Scaling MODE (1 is Crop - native pixels, centred), not a
# magnification, so 0.88 there would name no mode at all. The verdict
# is a store: the optional `zoom` beside the place, held uniform on
# `ZoomX`/`ZoomY`.

ZOOM_PIN = {"canvas_centre": [540.0, 312.0], "scaling": 1, "zoom": 0.88}


def test_a_zoom_parses_beside_the_place():
    intent = parse_intent({"version": 2, "targets": {
        "mg_geo-podcast_a072b160": dict(ZOOM_PIN)}})
    assert intent["mg_geo-podcast_a072b160"]["zoom"] == 0.88


def test_a_non_positive_zoom_is_refused():
    for bad in (0, 0.0, -0.5, "0.88", True, float("inf"),
                float("nan")):
        with pytest.raises(OverlayIntentError):
            parse_intent({"version": 2, "targets": {
                "mg_geo-podcast_a072b160": {
                    "canvas_centre": [540.0, 312.0], "scaling": 1,
                    "zoom": bad}}})


def test_no_zoom_is_no_zoom_key():
    """The failing input the zoom store exists to end: a pin that
    names a place and no magnification resolves a placement with no
    zoom in it, so the clip plays at the build's zoom - which is what
    let Reel 01's 12% scale-down die on the rebuild."""
    intent = parse_intent({"version": 2, "targets": {
        "mg_geo-podcast_a072b160": {
            "canvas_centre": [540.0, 312.0], "scaling": 1}}})
    placement, provenance = _resolve(
        "explainer", "mg_geo-podcast_a072b160",
        {"scaling": 1, "pan": 0.0, "tilt": 0.0}, intent,
        canvas=(296, 480))
    assert provenance == "declared"
    assert "zoom" not in placement


def test_a_declared_zoom_resolves_beside_the_place():
    from library.tools.overlay_intent import transform_for

    intent = parse_intent({"version": 2, "targets": {
        "mg_geo-podcast_a072b160": dict(ZOOM_PIN)}})
    placement = transform_for(intent["mg_geo-podcast_a072b160"],
                              (296, 480), FRAME,
                              "mg_geo-podcast_a072b160")
    assert placement["zoom"] == 0.88
    assert placement["scaling"] == 1


def test_a_declared_zoom_overrules_loudly():
    from library.tools.overlay_intent import disagreement

    assert "zoom 0.88" in disagreement(
        {"scaling": 1, "pan": 0.0, "tilt": 0.0, "zoom": 0.88},
        {"scaling": 1, "pan": 0.0, "tilt": 0.0}, "mg_x")
    assert disagreement(
        {"scaling": 1, "pan": 0.0, "tilt": 0.0},
        {"scaling": 1, "pan": 0.0, "tilt": 0.0}, "mg_x") == ""


def test_a_declared_zoom_holds_on_the_timeline():
    """End to end through the production placer: the item carries the
    zoom uniform on ZoomX/ZoomY, judged by return AND read-back like
    every other property."""
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 0.0, "tilt": 0.0},
        label="seg", kind="explainer",
        segment_id="mg_geo-podcast_a072b160",
        intent=parse_intent({"version": 2, "targets": {
            "mg_geo-podcast_a072b160": dict(ZOOM_PIN)}}),
        canvas=(296, 480), frame=FRAME)
    assert ok and note == ""
    assert item.set_calls["ZoomX"] == 0.88
    assert item.set_calls["ZoomY"] == 0.88
    assert item.set_calls["Tilt"] != 0.0  # the place still applied


def test_a_zoom_survives_a_rebuild_that_recomputes_the_place():
    """The survival case: one parse, two placements computed against
    fresh canvases the way two builds compute them - the zoom rides
    both, by value, while the Pan/Tilt are recomputed each time."""
    from library.tools.overlay_intent import transform_for

    intent = parse_intent({"version": 2, "targets": {
        "mg_geo-podcast_a072b160": dict(ZOOM_PIN)}})
    first = transform_for(intent["mg_geo-podcast_a072b160"],
                          (296, 480), FRAME, "mg_x")
    second = transform_for(intent["mg_geo-podcast_a072b160"],
                            (296, 480), FRAME, "mg_x")
    assert first["zoom"] == second["zoom"] == 0.88


# ── A pin on the PLACING survives the re-render that kills the id ────
#
# Measured 2026-09-17: Reel 26's title lockup re-rendered from
# `mg_geo-podcast_589d4594` to `mg_geo-podcast_c43f73d8`, the only
# render-input difference `timeline_start: 9.773 -> 9.75` - a 23ms
# consequence of the aligner switch on a graphic that moved zero
# pixels. The digest id dies with the render; the placing label (which
# placing the file serves - a pure function of reel and index that
# never sees `timeline_start`) stands still. A pin written against the
# label binds both eras.

#: The measured pair: one placing, two digests, 23ms apart.
MG_OLD = "mg_geo-podcast_589d4594"
MG_NEW = "mg_geo-podcast_c43f73d8"
MG_PIN = {"canvas_centre": [540.0, 312.0], "scaling": 1}

REEL_26 = "Reel 26 - geo-podcast"


def _reel_26_label(index=0):
    """The placing label, computed the way the renderer was given it -
    from the reel and the slot, never from the render inputs."""
    from library.tools.speaker_identity import segment_name
    return segment_name(REEL_26, index)


def test_label_is_stable_against_the_measured_23ms_shift():
    """The label's inputs exclude everything the re-render changed: the
    same reel and slot compute the same label in both eras, while the
    digest ids differ. The exact spelling is pinned - a rename of the
    label orphans every pin written against it, so it fails loudly
    here rather than silently on the timeline. Fails if label
    computation ever reads a render input (timing, canvas, copy)."""
    assert _reel_26_label(0) == "lt_reel_26_geo_podcast_00"


def test_a_label_pin_binds_both_eras_of_the_measured_pair():
    """The proof the brief demands: a pin written for `mg_geo-podcast
    _589d4594`'s placing resolves onto `mg_geo-podcast_c43f73d8`
    when the only change is `timeline_start: 9.773 -> 9.75`."""
    from library.tools.overlay_intent import resolve as mg_resolve

    label = _reel_26_label(0)
    intent = parse_intent({"version": 2, "targets": {label: dict(MG_PIN)}})
    for segment_id in (MG_OLD, MG_NEW):
        placement, provenance = mg_resolve(
            "speaker lower third", segment_id,
            {"scaling": 1, "pan": 99.0, "tilt": 9.0}, intent,
            canvas=CANVAS, frame=FRAME, placement_label=label)
        assert provenance == "declared", segment_id
        assert placement["scaling"] == 1


def test_a_label_pin_does_not_bind_every_graphic_on_the_project():
    """The docstring's warning, kept: one label names one placing. A
    pin for Reel 26's slot binds neither the same project's other
    graphic nor another reel's same-index slot."""
    from library.tools.overlay_intent import resolve as mg_resolve

    label = _reel_26_label(0)
    intent = parse_intent({"version": 2, "targets": {label: dict(MG_PIN)}})
    computed = {"scaling": 1, "pan": 99.0, "tilt": 9.0}
    other, prov_other = mg_resolve(
        "speaker lower third", "mg_geo-podcast_a072b160",
        dict(computed), intent, canvas=CANVAS, frame=FRAME,
        placement_label=_reel_26_label(1))
    assert (other, prov_other) == (computed, "computed")
    foreign, prov_foreign = mg_resolve(
        "speaker lower third", "mg_geo-podcast_ffffffff",
        dict(computed), intent, canvas=CANVAS, frame=FRAME,
        placement_label=_reel_26_label(0).replace("26", "01"))
    assert (foreign, prov_foreign) == (computed, "computed")


def test_exact_and_label_pins_claiming_one_segment_refuse():
    """A stale digest pin and a label pin both naming one live
    segment: neither outranks the other (both are specific), so the
    build refuses and names both instead of overruling one in
    silence. Same discipline as two pins on one prefix."""
    intent = parse_intent({"version": 2, "targets": {
        MG_OLD: PIN_A, _reel_26_label(0): PIN_B}})
    with pytest.raises(OverlayIntentError) as excinfo:
        _resolve("speaker lower third", MG_OLD, COMPUTED_CAPTION, intent,
                 placement_label=_reel_26_label(0))
    message = str(excinfo.value)
    assert MG_OLD in message and _reel_26_label(0) in message


# ── One caption pin over several karaoke cards sharing a prefix ──────
#
# Measured, not assumed (task 2 of the 2026-09-17 brief): since the
# karaoke change, caption segments are one per card rather than one
# per transcript row, and several cards of one block share one
# provenance prefix - three of Reel 26's read
# `sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_<digest>`. The
# docstring covers TWO PINS naming one prefix (refused) but not ONE
# PIN naming THREE SEGMENTS. What happens: the pin fans out - each
# card resolves it independently, deterministically, with no guessing
# between candidates because there is only one claimant. That is the
# correct behaviour (cards of one block caption one block's speech
# and sit in one band), so these tests MEASURE it and it stays.

#: Three karaoke cards off one Reel 26 block: one prefix, three digests.
CARD_A = "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_aaaa1111"
CARD_B = "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_bbbb2222"
CARD_C = "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_cccc3333"


def test_one_pin_fans_out_over_cards_sharing_its_prefix():
    """One pin written under an old digest, three current cards under
    new ones: every card resolves the pin to the same place, and the
    pin is not reported unmatched. No refusal, no ambiguity - one
    claimant, three independent applications."""
    from library.tools.overlay_intent import unmatched

    intent = parse_intent({"version": 2, "targets": {
        "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_dddd4444": PIN_A}})
    for card in (CARD_A, CARD_B, CARD_C):
        placement, provenance = _resolve(
            CAPTION_KIND, card, COMPUTED_CAPTION, intent)
        assert provenance == "declared", card
        assert placement["tilt"] == -1700.0, card
    assert unmatched(intent, [CARD_A, CARD_B, CARD_C]) == []


def test_a_bare_prefix_pin_fans_out_the_same_way():
    """A pin already stripped to the provenance prefix behaves
    identically: every card of the block resolves it."""
    intent = parse_intent({"version": 2, "targets": {
        "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173": PIN_A}})
    for card in (CARD_A, CARD_B, CARD_C):
        _, provenance = _resolve(CAPTION_KIND, card,
                                 COMPUTED_CAPTION, intent)
        assert provenance == "declared", card


def test_cards_of_a_neighbouring_span_stay_unpinned():
    """Fan-out stops at the prefix boundary: a card off the next span
    keeps the computation while its neighbour's three cards resolve
    the pin."""
    intent = parse_intent({"version": 2, "targets": {
        "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_dddd4444": PIN_A}})
    placement, provenance = _resolve(
        CAPTION_KIND,
        "sub_craig_f24c6416-7523-42bb-b9fe_167200-169000_eeee5555",
        COMPUTED_CAPTION, intent)
    assert provenance == "computed"
    assert placement == COMPUTED_CAPTION


# ── The build records which pins applied, durably ────────────────────

def test_resolve_records_the_winning_pin_key():
    """`matched` collects the key that won, once per declared
    application, and nothing on the computed path - the build's
    applied-count reads this, never a re-derivation."""
    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    matched: list = []
    _, provenance = resolve(CAPTION_KIND, SPAN_A_NEW, COMPUTED_CAPTION,
                            intent, canvas=CANVAS, frame=FRAME,
                            matched=matched)
    assert provenance == "declared"
    assert matched == [SPAN_A_OLD]
    quiet: list = []
    _, provenance = resolve(CAPTION_KIND, SPAN_B_NEW, COMPUTED_CAPTION,
                            intent, canvas=CANVAS, frame=FRAME,
                            matched=quiet)
    assert provenance == "computed"
    assert quiet == []


def test_intent_report_counts_pins_not_applications():
    """The durable sentence: one caption pin fanning out over three
    cards is one pin honoured. `declared`/`applied` are pin keys;
    `unmatched` is what matched nowhere; kind defaults ride beside."""
    from library.tools.overlay_intent import intent_report

    intent = parse_intent({"version": 2, "targets": {
        "caption": {"canvas_centre": [540.0, 1395.0], "scaling": 1},
        "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_dddd4444": PIN_A,
        "sub_craig_deadbeef-0000-4000-8000_100-200_eeee5555": PIN_B}})
    report = intent_report(
        intent,
        ["sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_dddd4444"] * 3,
        [CARD_A, CARD_B, CARD_C],
        source="overlay_intent.json (Reel 26)")
    assert report["applied"] == [
        "sub_craig_f24c6416-7523-42bb-b9fe_162173-167173_dddd4444"]
    assert report["unmatched"] == [
        "sub_craig_deadbeef-0000-4000-8000_100-200_eeee5555"]
    assert report["kind_defaults"] == ["caption"]
    assert len(report["declared"]) == 2


def test_unmatched_is_label_aware():
    """A label pin whose placing played under a new digest is matched,
    not reported - the same promise the prefix tier already keeps."""
    from library.tools.overlay_intent import unmatched

    label = _reel_26_label(0)
    intent = parse_intent({"version": 2, "targets": {label: dict(MG_PIN)}})
    assert unmatched(intent, [MG_NEW], [label]) == []
    assert unmatched(intent, ["mg_geo-podcast_other"], []) == [label]


# ── Re-keying digest pins onto labels drops nothing ──────────────────

def test_rekey_maps_live_digests_and_keeps_the_rest_verbatim():
    """The migration: a live digest becomes its placing's label
    carrying its value; a stale digest with no record, a caption pin
    and the kind default stay byte-identical. Nothing is deleted."""
    from library.tools.overlay_intent import rekey_targets

    label = _reel_26_label(0)
    targets = {MG_OLD: dict(MG_PIN),
               "mg_geo-podcast_deadbeef": dict(MG_PIN),
               SPAN_A_OLD: dict(PIN_A),
               "caption": {"canvas_centre": [540.0, 1395.0],
                           "scaling": 1}}
    new_targets, report = rekey_targets(targets, {MG_OLD: label})
    assert new_targets[label] == dict(MG_PIN)
    assert new_targets["mg_geo-podcast_deadbeef"] == dict(MG_PIN)
    assert new_targets[SPAN_A_OLD] == dict(PIN_A)
    assert report["mapped"] == {MG_OLD: label}
    assert report["unmapped"] == sorted(
        ["mg_geo-podcast_deadbeef", SPAN_A_OLD, "caption"])
    assert report["collisions"] == {}
    assert len(new_targets) == len(targets)


def test_rekey_refuses_two_digests_claiming_one_placing():
    """Two recorded digests mapping onto one label is the double-claim
    the resolver refuses at build time: the re-key leaves BOTH
    verbatim and names the collision instead of picking a winner."""
    from library.tools.overlay_intent import rekey_targets

    label = _reel_26_label(0)
    targets = {MG_OLD: dict(PIN_A), MG_NEW: dict(PIN_B)}
    new_targets, report = rekey_targets(
        targets, {MG_OLD: label, MG_NEW: label})
    assert new_targets == targets
    assert report["collisions"] == {label: sorted([MG_NEW, MG_OLD])}
    assert report["mapped"] == {}


def test_rekey_refuses_to_overwrite_a_live_label_pin():
    """A digest mapping onto a label that is already pinned is the
    same collision: the digest stays, the label stays, both named."""
    from library.tools.overlay_intent import rekey_targets

    label = _reel_26_label(0)
    targets = {MG_OLD: dict(PIN_A), label: dict(PIN_B)}
    new_targets, report = rekey_targets(targets, {MG_OLD: label})
    assert new_targets == targets
    assert report["collisions"] == {label: sorted([MG_OLD, label])}


def test_rekeyed_pin_resolves_the_measured_pair_end_to_end():
    """The whole migration story in one test: the pre-rebuild record
    maps `589d4594` to its placing, the re-key moves the pin onto the
    label, the rebuild re-renders under `c43f73d8` with the placing
    unchanged, and the pin binds the new render."""
    from library.tools.overlay_intent import rekey_targets
    from library.tools.overlay_intent import resolve as mg_resolve

    label = _reel_26_label(0)
    new_targets, _ = rekey_targets({MG_OLD: dict(MG_PIN)}, {MG_OLD: label})
    intent = parse_intent({"version": 2, "targets": new_targets})
    placement, provenance = mg_resolve(
        "speaker lower third", MG_NEW,
        {"scaling": 1, "pan": 99.0, "tilt": 9.0}, intent,
        canvas=CANVAS, frame=FRAME, placement_label=label)
    assert provenance == "declared"
    assert placement["scaling"] == 1


def test_lt_segment_name_matches_the_reel_build_spelling():
    """One name, one spelling: the canonical lower-third label equals
    what the reel placer passes the renderer, on ordinary names and
    on the edge cases (empty, punctuation-only, overlong)."""
    from library.tools.reel_build import _reel_slug
    from library.tools.speaker_identity import segment_name

    names = [REEL_26, "Reel 01", "", "!!!", "x" * 60,
             "Reel 26 ... (all three fixes)"]
    for reel_name in names:
        for index in (0, 3):
            assert segment_name(reel_name, index) == (
                f"lt_{_reel_slug(reel_name)}_{index:02d}"), reel_name


# ── The map behind the re-key, off the build's own records ───────────

def _write_review_records(project_folder):
    """A last build's review records: Reel 26's title lockup as a
    lower-third entry in the legacy shape (path only - label
    recomputed), a Reel 27 explainer in the legacy shape, and a Reel
    01 lower third in the new shape (both fields explicit)."""
    from library.tools.explainer_plan import ExplainerPlan, write_plans
    from library.tools.speaker_identity import SpeakerPlan
    from library.tools.speaker_identity import write_plans as write_lt

    write_lt(project_folder, [SpeakerPlan(
        reel_name=REEL_26, declared=True, basis="planned",
        segments=[{"overlay_path":
                   f"/renders/{MG_OLD}.mov",
                   "timeline_start": 9.773, "timeline_end": 12.0,
                   "total_frames": 54, "elements": ["title_lockup"]}],
    )])
    write_plans(project_folder, [ExplainerPlan(
        reel_name="Reel 27", declared=True, basis="planned",
        segments=[{"overlay_path":
                   "/renders/mg_geo-podcast_bbbb2222.mov",
                   "timeline_start": 4.0, "timeline_end": 9.0,
                   "total_frames": 120, "elements": ["stat_callout"]}],
    )])
    write_lt(project_folder, [SpeakerPlan(
        reel_name="Reel 01", declared=True, basis="planned",
        segments=[{"overlay_path": "/renders/mg_geo-podcast_aaaa1111.mov",
                   "segment_id": "mg_geo-podcast_aaaa1111",
                   "placement_label": "lt_reel_01_00",
                   "timeline_start": 3.0, "timeline_end": 6.0,
                   "total_frames": 72, "elements": ["lower_third"]}],
    )])


def test_collect_reads_explicit_fields_and_recomputes_legacy_ones():
    """The re-key map: legacy entries (path only) map through the
    recomputed label of their own layer, the new-shape entry through
    its explicit one. All three agree with what the renderer was
    given."""
    from library.tools import explainer_plan as _explainer
    from library.tools.overlay_intent import collect_placement_labels

    import tempfile
    with tempfile.TemporaryDirectory() as project_folder:
        _write_review_records(project_folder)
        found = collect_placement_labels(project_folder)
    assert found[MG_OLD] == _reel_26_label(0)
    assert found["mg_geo-podcast_bbbb2222"] == _explainer.segment_name(
        "Reel 27", 0)
    assert found["mg_geo-podcast_aaaa1111"] == "lt_reel_01_00"


def test_collect_skips_semantic_records_with_no_file_identity():
    """Pre-label semantic records carry spans, not files: they map no
    pin, rather than mapping one onto a guess."""
    from library.tools import reel_semantic_visual as sem
    from library.tools.overlay_intent import collect_placement_labels

    import tempfile
    with tempfile.TemporaryDirectory() as project_folder:
        sem.write_records(project_folder, [{
            "reel": REEL_26, "basis": "planned", "entries": [],
            "dropped": [],
            "segments": [{"timeline_start": 9.773, "timeline_end": 12.0,
                          "total_frames": 54,
                          "elements": ["title_lockup"]}]}])
        found = collect_placement_labels(project_folder)
    assert found == {}


def test_check_names_each_pin_state_and_rekey_rewrites_mapped(tmp_path,
                                                              capsys):
    """The CLI without Resolve or renders: `check` reports every pin,
    `--rekey` moves the mapped digest onto its label and leaves the
    rest byte-identical."""
    from library.tools.overlay_intent import main

    from library.tools.external_inputs import external_dir
    intent_dir = external_dir(str(tmp_path))
    os.makedirs(intent_dir, exist_ok=True)
    path = os.path.join(intent_dir, "overlay_intent.json")
    label = _reel_26_label(0)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": 2, "targets": {
            MG_OLD: dict(MG_PIN),
            "mg_geo-podcast_deadbeef": dict(MG_PIN),
            CARD_A: dict(PIN_A)}}, handle)
    _write_review_records(str(tmp_path))

    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"{MG_OLD}: EXACT" in out and "REKEYABLE" in out
    assert "mg_geo-podcast_deadbeef: UNMAPPED" in out
    assert f"{CARD_A}: PREFIX" in out

    assert main([str(tmp_path), "--rekey"]) == 0
    with open(path, encoding="utf-8") as handle:
        body = json.load(handle)
    assert body["targets"][label] == dict(MG_PIN)
    assert MG_OLD not in body["targets"]
    assert body["targets"]["mg_geo-podcast_deadbeef"] == dict(MG_PIN)
    assert body["targets"][CARD_A] == dict(PIN_A)
    assert body["version"] == 2
