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


def _resolve(kind, segment_id, computed, intent, canvas=CANVAS):
    return resolve(kind, segment_id, computed, intent,
                   canvas=canvas, frame=FRAME)


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
    the production placer."""
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
    assert item.set_calls == {"Scaling": 1, "Pan": 0.0, "Tilt": -1700.0}


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
