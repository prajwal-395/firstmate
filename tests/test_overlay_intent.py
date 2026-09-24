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










def test_missing_project_file_is_no_intent(tmp_path):
    assert load_intent(str(tmp_path)) == {}








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








# ── A hand-set zoom rides beside the place, never in `scaling` ────────
#
# Reel 01's graphic, moved AND scaled down 12%: `scaling` is Resolve's
# Scaling MODE (1 is Crop - native pixels, centred), not a
# magnification, so 0.88 there would name no mode at all. The verdict
# is a store: the optional `zoom` beside the place, held uniform on
# `ZoomX`/`ZoomY`.

ZOOM_PIN = {"canvas_centre": [540.0, 312.0], "scaling": 1, "zoom": 0.88}












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






# ── Re-keying digest pins onto labels drops nothing ──────────────────







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
