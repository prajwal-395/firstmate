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
