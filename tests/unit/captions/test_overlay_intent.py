"""Declared overlay positions, honoured exactly or refused loudly.

Segment beats kind beats computed; a pin names a PLACE (version 2), and a
version 1 raw-transform file is refused. Reel 09's pins are the fixtures.
History: docs/evidence/overlay_position.md#overlay-intent-pins.
"""
import json
import os
import sys
import pytest
from library.tools import overlay_intent
import shutil
import subprocess
from library.tools.overlay_placement import _intent_reason
from library.tools.tight_box import (
    INTENT_TOLERANCE_PX,
    canvas_screen_origin,
    ink_screen_box,
    placement_for_box,
    verify_ink_against_intent,
)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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
#: (`tests/unit/resolve/test_resolve_transform.py`) and by the rebuild gate.
HISTORY_GAIN = 1.0


def _resolve(kind, segment_id, computed, intent, canvas=CANVAS,
             placement_label=None):
    return resolve(kind, segment_id, computed, intent,
                   canvas=canvas, frame=FRAME,
                   placement_label=placement_label,
                   draw_gain=HISTORY_GAIN)


def test_kind_default_pins_every_caption_and_a_segment_pin_beats_it():
    intent = parse_intent(REEL_09_INTENT)
    for segment_id in ("sub_akshita_x_1491914-1493721_6bdaa694",
                       "sub_craig_y_1421571-1426656_8f6f0b4c",
                       "sub_anything_unseen_before"):
        placement, provenance = _resolve(CAPTION_KIND, segment_id,
                                         COMPUTED_CAPTION, intent)
        assert provenance == "declared"
        assert placement == {"pan": 0.0, "tilt": -1700.0, "scaling": 1}, (
            "the place resolves to the transform the captain set")
    # A segment pin beats the kind default.
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


def test_a_pin_binds_its_old_hash_exactly_and_survives_a_rerender():
    """Backward compatibility: where the render still stands under
    the recorded filename, the pin binds it exactly as before. No
    captain record is orphaned by the re-key."""
    intent = parse_intent({"version": 2, "targets": {SPAN_A_OLD: PIN_A}})
    placement, provenance = _resolve(
        CAPTION_KIND, SPAN_A_OLD, COMPUTED_CAPTION, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -1700.0
    # The 2026-09-13 wipe: re-rendered under a new content hash, prefix
    # unchanged - the pin still puts the overlay where the captain did.
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
    # The label pin binds BOTH eras of the measured pair (timeline_start
    # 9.773 -> 9.75 re-rendered 589d4594 as c43f73d8, zero pixels moved).
    for segment_id in (MG_OLD, MG_NEW):
        placement, provenance = mg_resolve(
            "speaker lower third", segment_id,
            {"scaling": 1, "pan": 99.0, "tilt": 9.0}, intent,
            canvas=CANVAS, frame=FRAME, placement_label=label)
        assert provenance == "declared", segment_id
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


# --------------------------------------------------------------------------
# From test_overlay_draw_intent.py
#
# Both halves of "does it land where intended" are ARMED.
#
# Finding 1: `draw_intent` was supplied by no production caller, so the
# ink-against-intent check could not run - five reels shipped 17 motion
# graphics stored at Tilt 5184 drawing entirely off the top of the frame
# while every gate passed. Finding 2: `overlay_verify` had no production
# caller at all - Reel 09's captions stored at -7680 were reported "held
# exactly" by the read-back gate.
#
# These tests prove the arming against the two MEASURED inputs, through
# the production helpers (not hand-made dicts): the pixel half refuses
# Tilt 5184, the values half refuses stored -7680 against computed -7929.
# Companion no-false-alarm tests prove correct output still passes and
# unverifiable segments ride exactly as before. Nothing here reaches
# Resolve or a real project: fakes stand in for timeline items, `tmp_path`
# for projects and renders.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_draw_intent import (  # noqa: E402
    caption_draw_intent,
    draw_intent_for_segment,
    pin_draw_intent,
)
from library.tools.overlay_intent import matching_target  # noqa: E402
from library.tools.overlay_verify import sweep_reel_overlays  # noqa: E402
from library.tools.tight_box import (  # noqa: E402
    constant_caption_box,
)


#: Reel 30's first motion graphic, measured 2026-09-11: a 724x480 canvas
#: stored at Tilt 5184 draws at frame rows -576..-96 - entirely off the
#: top - while reading back exactly what was set.
OFF_FRAME_CANVAS = (724, 480)
OFF_FRAME_TILT = 5184.0

#: Reel 09 captions: the computation asked for Tilt -7929 and Resolve
#: past its silent clamp held -7680, which the read-back gate reported
#: as "held exactly".
REEL09_COMPUTED = {"scaling": 1, "pan": 0.0, "tilt": -7929.0}
REEL09_CLAMPED = {"scaling": 1, "pan": 0.0, "tilt": -7680.0}
REEL09_CANVAS = (840, 480)


# ── Fakes: a Resolve timeline item and timeline ───────────────────────

class _Item_2:
    def __init__(self, start, held=None):
        self._start = start
        self._held = dict(held or {})

    def GetStart(self):
        return self._start

    def GetProperty(self, prop=None):
        if prop is None:
            return dict(self._held)
        return self._held.get(prop)

    def SetProperty(self, prop, value):
        self._held[prop] = value
        return True


class _Pool_2:
    def AppendToTimeline(self, specs):
        return ("placed",)


def _timeline_with(items_by_track, size=FRAME):
    """A fake timeline keyed by track index (GetStart is the record frame)."""

    class _T:
        def GetName(self):
            return "Reel"

        def GetSetting(self, name):
            return str(size[0] if name == "timelineResolutionWidth"
                       else size[1])

        def GetItemListInTrack(self, kind, index):
            assert kind == "video"
            return items_by_track.get(index, [])

    return _T()


def _project_with_current_timeline(timeline):
    class _P:
        def GetCurrentTimeline(self):
            return timeline

    return _P()


# ── Finding 1, proved: Tilt 5184 is REFUSED by the pixel half ─────────

def test_pin_path_refuses_the_off_frame_tilt():
    """The production pin helper against the measured Reel 30 input.

    The graphic is pinned where the captain put such graphics (frame
    centre, upper band); the stored Tilt 5184 draws its canvas at rows
    -576..-96. The helper must refuse it - a test that passes this
    input has armed nothing.
    """
    # History gain: this still measured rows -576..-96 on 2026-09-11
    # (see HISTORY_GAIN in test_tight_box.py).
    ox, oy = canvas_screen_origin(
        *OFF_FRAME_CANVAS,
        {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT}, *FRAME,
        draw_gain=1.0)
    assert (oy, oy + OFF_FRAME_CANVAS[1]) == pytest.approx((-576.0, -96.0),
                                                          abs=1.0)
    segment = {"tight_box": {"width": OFF_FRAME_CANVAS[0],
                             "height": OFF_FRAME_CANVAS[1],
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": OFF_FRAME_TILT}}}
    intent = {"mg_reel30_first": {"canvas_centre": [540.0, 312.0],
                                  "scaling": 1}}
    draw_intent = draw_intent_for_segment(
        segment, kind="motion graphic",
        segment_id="mg_reel30_first", intent=intent, frame_wh=FRAME)
    assert draw_intent is not None, (
        "a pinned graphic must produce an intent, or the check is still "
        "a check-shaped parameter")
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        {"Pan": 0.0, "Tilt": OFF_FRAME_TILT})
    assert reason, "Tilt 5184 on a 480 canvas must not pass this intent"
    assert "ENTIRELY OUTSIDE THE FRAME" in reason


# ── The caption row path ──────────────────────────────────────────────

def _project_with_caption_row(tmp_path, row=None):
    project = tmp_path / "proj"
    project.mkdir()
    body = "name: t\n"
    if row is not None:
        body += ("pipeline:\n  subtitle_position:\n"
                 f"    caption_row: {row}\n    reason: test\n")
    (project / "project.yaml").write_text(body, encoding="utf-8")
    return str(project)


def _caption_segment(tmp_path, canvas=(904, 480), tilt=-1744.0,
                     position="bottom"):
    render_dir = tmp_path / "renders"
    render_dir.mkdir(exist_ok=True)
    overlay_path = str(render_dir / "sub_test.mov")
    open(overlay_path, "wb").close()
    props_path = str(render_dir / "sub_test_props.json")
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump({"style": {"position": position}}, handle)
    return {"overlay_path": overlay_path,
            "segment_id": "sub_test",
            "tight_box": {"width": canvas[0], "height": canvas[1],
                          "placement": {"scaling": 1, "pan": 0.0,
                                        "tilt": tilt}}}


def test_caption_row_path_refuses_a_stale_sidecar_placement(tmp_path):
    """Reel 28's shape: 18 tight captions placed under a superseded row
    draw ~220px below the declared one. The row path refuses them."""
    from library.tools.safe_area import resolve_safe_area

    project = _project_with_caption_row(tmp_path)
    insets = resolve_safe_area(project, width=FRAME[0], height=FRAME[1])
    intended = constant_caption_box({
        "width": FRAME[0], "height": FRAME[1],
        "style": {"safeArea": {**insets.as_props(),
                               "bottom": insets.bottom + 1},
                  "captionMaxWidth": insets.centered_usable_width,
                  "position": "bottom"},
        "subtitles": [{"text": ""}]})
    segment = _caption_segment(tmp_path,
                               canvas=(intended.width, intended.height))
    draw_intent = caption_draw_intent(
        segment, frame_wh=FRAME, project_folder=project)
    assert draw_intent is not None
    stale_tilt = intended.placement["tilt"] - 220.0 * (FRAME[1] / 480.0)
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": stale_tilt},
        {"Pan": 0.0, "Tilt": stale_tilt})
    assert reason, "a placement 220px off the declared row must be refused"
    assert "off by" in reason


# ── matching_target: the quiet lookup the pin path reads ─────────────

def test_matching_target_exact_prefix_and_kind():
    intent = {"sub_x": {"canvas_centre": [1.0, 2.0], "scaling": 1},
              "caption": {"canvas_centre": [3.0, 4.0], "scaling": 1}}
    key, target = matching_target(intent, "caption", "sub_x")
    assert key == "sub_x" and target["canvas_centre"] == [1.0, 2.0]
    key, target = matching_target(intent, "caption", "sub_other")
    assert key == "caption", "the kind default is the fallback"
    assert matching_target(intent, "mg", "zzz") == (None, None)
    assert matching_target({}, "caption", "sub_x") == (None, None)
    assert pin_draw_intent((100, 100), "caption", "sub_x", intent,
                           frame_wh=FRAME) is not None
    assert pin_draw_intent((100, 100), "mg", "zzz", intent,
                           frame_wh=FRAME) is None


# ── Finding 2, proved: stored -7680 is REFUSED by the values half ─────

def _sweep_entry(label, stored, track=3, frame=10, canvas=REEL09_CANVAS,
                 computed=None, placement_label=None):
    return {"label": label, "kind": "caption", "segment_id": label,
            "placement_label": placement_label,
            "track_index": track, "record_frame": frame,
            "canvas_wh": canvas,
            "placement": dict(computed or REEL09_COMPUTED),
            "overlay_path": "", "frames_dir": ""}


def test_sweep_honours_a_label_pin():
    """The sweep resolves the label tier too: a store at the label
    pin's transform passes, while the same store judged against the
    computation alone would cry foul on the captain's correction."""
    from library.tools.overlay_intent import resolve as resolve_intent

    intent = {"vox_r1_00": {"canvas_centre": [540.0, 312.0], "scaling": 1}}
    canvas = (724, 480)
    computed = {"scaling": 1, "pan": 0.0, "tilt": 100.0}
    expected, provenance = resolve_intent(
        "explainer", "mg_proj_newdigest", computed, intent,
        canvas=canvas, frame=FRAME, placement_label="vox_r1_00")
    assert provenance == "declared"
    entry = _sweep_entry("vox_r1_00", expected, track=5, frame=30,
                         canvas=canvas, computed=computed,
                         placement_label="vox_r1_00")
    entry["kind"] = "explainer"
    entry["segment_id"] = "mg_proj_newdigest"
    timeline = _timeline_with({5: [_Item_2(30, {"Scaling": 1,
                                              "Pan": expected["pan"],
                                              "Tilt": expected["tilt"]})]})
    report = sweep_reel_overlays(timeline, [entry], intent=intent,
                                 full_wh=FRAME,
                                 resolve_project=_project_with_current_timeline(
                                     timeline))
    assert report["passed"] and report["values"]["checked"] == 1, (
        "a label-pinned store must pass the sweep, not foul it")


def test_sweep_refuses_the_reel09_clamp():
    """The production sweep against the measured Reel 09 input: stored
    -7680 where the computation asked -7929. A sweep that passes this
    has armed nothing."""
    timeline = _timeline_with({3: [_Item_2(10, {"Scaling": 1, "Pan": 0.0,
                                              "Tilt": -7680.0})]})
    report = sweep_reel_overlays(
        timeline, [_sweep_entry("sub_r09_x", REEL09_CLAMPED)], intent={},
        full_wh=FRAME,
        resolve_project=_project_with_current_timeline(timeline))
    assert not report["passed"]
    assert not report["values"]["passed"]
    finding = report["values"]["findings"][0]
    assert finding["stored"]["tilt"] == -7680.0
    assert finding["expected"]["tilt"] == -7929.0


def test_overlay_sweep_restores_cross_current_pan_and_tilt_units():
    """Intent checks use the same target-timeline units as picture checks."""
    expected = {"scaling": 1.0, "pan": 23.0, "tilt": -400.0}
    timeline = _timeline_with({
        3: [_Item_2(10, {"Scaling": 1.0,
                       "Pan": expected["pan"] * 3840 / FRAME[0],
                       "Tilt": expected["tilt"] * 2160 / FRAME[1]})]})
    current = _timeline_with({}, size=(3840, 2160))
    report = sweep_reel_overlays(
        timeline, [_sweep_entry("overlay_units", expected,
                                computed=expected)],
        intent={}, full_wh=FRAME,
        resolve_project=_project_with_current_timeline(current))

    assert report["passed"]
    assert report["values"]["checked"] == 1
    assert report["values"]["findings"] == []


# ── The placer evaluates what the callers now supply ──────────────────

def test_placer_reports_a_stale_value_through_draw_intent():
    """Before this change the placer could not see this class at all:
    the value reads back exactly what was set."""
    draw_intent = {"canvas": OFF_FRAME_CANVAS, "frame": FRAME,
                   "ink_in_canvas": (0.0, 40.0, 724.0, 440.0),
                   "intent_box": (272.0, 1415.0, 800.0, 1572.0)}
    item = _Item_2(10)
    ok, note = place_overlay_segment(
        _Pool_2(), _timeline_with({3: [item]}), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        label="mg_stale", draw_intent=draw_intent)
    assert ok, "a misplaced overlay is REPORTED, never failed"
    assert "draws at" in note and "mg_stale" in note


def test_placer_stays_quiet_without_draw_intent():
    item = _Item_2(10)
    ok, note = place_overlay_segment(
        _Pool_2(), _timeline_with({3: [item]}), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        label="mg_stale")
    assert ok and note == "", (
        "no intent supplied behaves exactly as before")


def test_placer_normalizes_cross_current_transform_readback():
    class _ScaledItem(_Item_2):
        def GetProperty(self, prop=None):
            values = dict(self._held)
            values["Pan"] *= 3840 / FRAME[0]
            values["Tilt"] *= 2160 / FRAME[1]
            return values if prop is None else values.get(prop)

    item = _ScaledItem(10)
    timeline = _timeline_with({3: [item]})
    current = _timeline_with({}, size=(3840, 2160))
    ok, note = place_overlay_segment(
        _Pool_2(), timeline, object(), track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 23.0, "tilt": -400.0},
        label="overlay_units",
        resolve_project=_project_with_current_timeline(current))

    assert ok and note == ""


# ── The wiring itself, pinned so it cannot drift back to zero ─────────

def _library_source(relative):
    path = os.path.join(PROJECT_ROOT, *relative.split("/"))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# From test_overlay_intent_disagreement.py
#
# A pin may overrule the computation. It may not do it in silence.
#
# The defect, 2026-09-11: nineteen caption pins recorded during one
# rebuild encoded a position a later fix superseded. They outranked the
# computation by design and said nothing, so the only way to find out a
# pin had gone stale was to look at the picture and disbelieve it.
#
# Measured on the field test they turned out to be INERT as well - not one
# of them matched a caption id on any current reel - which is the other
# half of the same silence: a pin that matches nothing is as quiet as one
# that matches wrongly, and a re-render can make it match again tomorrow.
#
# Neither half changes who wins. A pin still beats the computation; that
# is what a pin is for.

COMPUTED = {"scaling": 1, "pan": 0.0, "tilt": -435.0}


def _pin(tilt):
    """A version-2 pin naming the place that `tilt` reaches on CANVAS.

    Spelled as a place, because that is what a pin is now; written from
    a tilt so the fixtures below stay the numbers the incident was
    reported in.
    """
    # Positive Tilt moves the clip UP, so the centre goes the other way.
    return {"canvas_centre": [540.0, 960.0 - tilt * (CANVAS[1] / FRAME[1])],
            "scaling": 1}


#: The 2026-09-11 draw gain, as in test_overlay_intent.py: these
#: tests pin disagreement reporting, not the conversion.


def _resolve_2(kind, segment_id, computed, intent):
    return overlay_intent.resolve(kind, segment_id, computed, intent,
                                  canvas=CANVAS, frame=FRAME,
                                  draw_gain=HISTORY_GAIN)


def test_a_pin_still_wins():
    intent = {"seg-1": _pin(-870.0)}
    placement, provenance = _resolve_2(
        "caption", "seg-1", COMPUTED, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -870.0


def test_a_material_disagreement_is_reported(capsys):
    intent = {"seg-1": _pin(-870.0)}
    _resolve_2("caption", "seg-1", COMPUTED, intent)
    err = capsys.readouterr().err
    assert "OVERRULES" in err
    assert "-870" in err and "-435" in err
    # It says which way and by how much, so a reader can judge it.
    assert "-435" in err


def test_agreement_says_nothing(capsys):
    """Below the threshold the two answers are the same place, and
    saying so on every overlay would teach a reader to skip the line."""
    intent = {"seg-1": _pin(-437.0)}
    _resolve_2("caption", "seg-1", COMPUTED, intent)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("field,value", [
    ("pan", 900.0),
])
def test_every_placement_field_is_compared(field, value):
    pinned = dict(COMPUTED)
    pinned[field] = value
    note = overlay_intent.disagreement(pinned, COMPUTED, "seg-1")
    assert field in note and "OVERRULES" in note


def test_the_threshold_is_stated_in_stored_units():
    """A number, not a formula, and small enough that a real stale pin
    cannot hide under it: the caption defect missed by 435 units."""
    assert overlay_intent.INTENT_DISAGREEMENT_UNITS < 435


# ── The inert half ─────────────────────────────────────────────────

def test_pins_that_matched_nothing_are_named():
    intent = {
        "sub_akshita_old-id_1_aaaa": {"scaling": 1, "pan": 0.0, "tilt": -870.0},
        "sub_akshita_old-id_2_bbbb": {"scaling": 1, "pan": 0.0, "tilt": -870.0},
        "mg_live_one": {"scaling": 1, "pan": 0.0, "tilt": 895.0},
    }
    stale = overlay_intent.unmatched(intent, ["mg_live_one"])
    assert stale == ["sub_akshita_old-id_1_aaaa", "sub_akshita_old-id_2_bbbb"]


# --------------------------------------------------------------------------
# From test_overlay_position_is_pixels.py
#
# An overlay's position is carried on Pan/Tilt Resolve actually holds.
#
# See `docs/evidence/overlay_position.md` for the incident and root cause analysis.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import overlay_placement, tight_box  # noqa: E402
from library.tools.overlay_mode import (  # noqa: E402
    OVERLAY_CARRIAGE,
    resolve_overlay_geometry,
)
from library.tools.overlay_placement import (  # noqa: E402
    apply_placement_transform,
)
from library.tools.tight_box import (  # noqa: E402
    TightBoxMismatch,
    placement_holds,
)

FULL_W, FULL_H = 1080, 1920

#: What step 4.05 ASKED Resolve for, and what Resolve HELD - read off
#: the live timeline on 2026-09-09 (Resolve 21, project "Podcast (field
#: test)", both Reel 09 timelines at 1080x1920) against
#: `4_05_render_subtitles/render_ledger.json`. `(canvas h, asked, held)`.
SHIPPED_AND_HELD = [
    (152, -7578.9473684210525, -3840.0),
    (158, -7254.683544303798, -3840.0),
    (160, -7152.0, -3840.0),
    (164, -6954.1463414634145, -3840.0),
    (166, -6858.795180722892, -3840.0),
    (220, -4939.636363636363, -3840.0),
    (224, -4834.285714285714, -3840.0),
    (232, -4634.48275862069, -3840.0),
    (236, -4539.661016949152, -3840.0),
    (242, -4403.305785123967, -3840.0),
    (244, -4359.3442622950815, -3840.0),
    (246, -4316.09756097561, -3840.0),
    (300, -3366.4, -3366.4),
]

#: Every DISTINCT box geometry among the 37 clamped items, as
#: `(width, height)`. This is the discriminating evidence: the
#: Pan/Tilt UNIT is relative to the clip's own size (`shift_y = -Tilt *
#: placed_H / timeline_H`), so if the RAIL were also clip-relative -
#: a cap on the resulting on-screen shift - these 17 geometries would
#: have read back 12 different values spread over -3840..-2373. They
#: all read back the same one.
CLAMPED_BOX_GEOMETRIES = [
    (840, 152), (902, 158), (866, 160), (840, 160), (840, 164),
    (888, 166), (840, 166), (870, 220), (840, 224), (896, 232),
    (884, 232), (840, 236), (876, 242), (840, 242), (840, 244),
    (898, 244), (840, 246),
]

#: The value Resolve pinned Tilt at, MEASURED, on a 1080x1920 timeline.
#: A NUMBER, deliberately not a formula. It happens to equal 2 x 1920,
#: but one timeline geometry cannot tell 2 x the height from 2 x the
#: longer side from a fixed constant - and writing a formula that fits
#: one data point into the code is exactly how the retired carriage came
#: to carry `4 x the timeline dimensions` as though it were measured.
#: Nothing depends on this number; it is recorded as evidence.
MEASURED_TILT_RAIL_1080x1920 = 3840.0

#: The bottom edge every caption canvas was anchored to.
CANVAS_BOTTOM = 1636


def _required_tilt(canvas_h: int) -> float:
    """The retired carriage's arithmetic, from the shipped geometry."""
    dy = CANVAS_BOTTOM - canvas_h / 2.0 - FULL_H / 2.0
    return -dy * (FULL_H / canvas_h)


# ── The root cause, reproduced from what shipped ──────────────────────


def test_the_floor_clears_the_cliff():
    """The composed answer to the cliff: the cliff is rail-relative.
    Under the 3840 row of the 2026-09-10 incident it sat at h = 270.4
    and every shipped 152..246px canvas overflowed unfloored; under
    the widened 4x-law rail it sits at h ~= 150.2, so only canvas
    140 overflows and 152 already holds. Either way the 480 floor is
    what clears it - the placement it asks for is -1744, thousands of
    units inside the rail. The gate below proves that per segment
    before anything reaches a timeline."""
    from library.tools.tight_box import MIN_CANVAS_HEIGHT
    assert MIN_CANVAS_HEIGHT == 480
    for canvas_h in (140, 152, 246, 270, MIN_CANVAS_HEIGHT):
        # History gain: this test pins the rail cliff the 2026-09-11
        # calibration measured (see HISTORY_GAIN in test_tight_box.py).
        placement = placement_for_box(
            840, canvas_h, FULL_W / 2.0,
            CANVAS_BOTTOM - canvas_h / 2.0, FULL_W, FULL_H,
            draw_gain=1.0)
        assert placement["scaling"] == 1
        if canvas_h <= 150:
            assert placement_holds(placement, FULL_W, FULL_H) != "", (
                f"h={canvas_h} is below the cliff and must overflow")
        else:
            assert placement_holds(placement, FULL_W, FULL_H) == ""
    assert abs(placement_for_box(
        840, MIN_CANVAS_HEIGHT, FULL_W / 2.0,
        CANVAS_BOTTOM - MIN_CANVAS_HEIGHT / 2.0,
        FULL_W, FULL_H, draw_gain=1.0)["tilt"]) <= 3400


# ── The placer: it sets the transform, then reads it back ────────────

class _Item_3:
    """A Resolve timeline item. `frozen` props simulate the silent
    clamp: `SetProperty` returns True but the held value does not
    move - the captain's -3840."""

    def __init__(self, start, held=None, frozen=None, unread=()):
        self._start = start
        self._held = dict(held or {})
        self._frozen = dict(frozen or {})
        self._held.update(self._frozen)
        self._unread = set(unread)
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def GetProperty(self, prop):
        if prop in self._unread:
            return None
        return self._held.get(prop)

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        if prop not in self._frozen:
            self._held[prop] = value
        return True


class _Pool_3:
    def __init__(self, result=("placed",)):
        self._result = result
        self.calls = []

    def AppendToTimeline(self, specs):
        self.calls.append(specs)
        return self._result


def _box_placement():
    return {"scaling": 1, "pan": 140.0, "tilt": -1720.0}


def test_a_clamped_overlay_is_reported_by_name():
    """Resolve holding the rail value read off the captain's live
    timeline is a REPORT naming the overlay - the clip IS on the
    timeline, and failing the build over a movable graphic would
    trade a misplaced one for a missing one."""
    item = _Item_3(10, frozen={"Tilt": -MEASURED_TILT_RAIL_1080x1920})
    pool = _Pool_3()
    ok, note = place_overlay_segment(
        pool, _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement=_box_placement(), label="sub_reel-09_akshita_9")
    assert ok
    assert "sub_reel-09_akshita_9" in note
    assert "Tilt" in note and "-3840" in note


def test_the_step_record_carries_a_placement_for_tight(tmp_path):
    """4.05's entry shape keeps `tight_box`, and a tight segment fills
    it with the placement the build applies - a reader finds the box,
    never an origin and never nothing."""
    from library.steps.step_4_05_render_subtitles import step as render_step

    source = open(render_step.__file__, encoding="utf-8").read()
    assert '"tight_box": _placement_record(),' in source
    assert '"placement": tight.placement,' in source


def test_every_overlay_row_goes_through_the_one_placer():
    """Captions, motion graphics AND timed text.

    Timed text used to carry its own inline `AppendToTimeline`, which
    made it the one overlay row nobody ever asked Resolve what it held.
    A second placer is also a second chance to land one frame off.
    """
    source = open(os.path.join(
        PROJECT_ROOT, "library", "steps", "step_6_01_render",
        "resolve_build_timeline.py"), encoding="utf-8").read()
    # One call each for captions, motion graphics and timed text.
    assert "    place_overlay_segment,\n" in source
    assert source.count("place_overlay_segment(") >= 3
    assert '"trackIndex": _tt_row' not in source, (
        "timed text appends inline again, outside the one placer")
    assert '"trackIndex": _caption_row' not in source
    assert '"trackIndex": _mg_row' not in source


class _EntryTimeline:
    def __init__(self, width, height):
        self._settings = {"timelineResolutionWidth": width,
                          "timelineResolutionHeight": height}

    def GetSetting(self, key):
        return self._settings[key]

    def GetName(self):
        return "entry"


class _EntryProject:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetCurrentTimeline(self):
        return self._timeline


def test_mismatched_entry_size_is_a_refusal():
    """The Reel 09 positioning proof, 2026-09-10: Pan/Tilt sets are
    interpreted in the entry timeline's units and silently converted
    to the target's (-1700 stored as -1912.5 across 3840x2160 ->
    1080x1920, as -1511.11 the other way), while same-process
    read-back echoes the set value. The refusal names both sizes so
    the build reconnects from a same-size timeline instead of
    placing a wrong-but-stored one."""
    from library.tools.overlay_placement import entry_unit_mismatch

    project = _EntryProject(_EntryTimeline("3840", "2160"))
    reason = entry_unit_mismatch(project, (1080, 1920))
    assert reason != ""
    assert "3840x2160" in reason
    assert "1080x1920" in reason


# --------------------------------------------------------------------------
# From test_overlay_positioning_rule.py
#
# One positioning rule, and a stored value judged against INTENT.
#
# Pan/Tilt move a clip by a fraction of its OWN canvas, not of the frame
# (`library/tools/resolve_transform.py`); the numbers below are measured off
# exported stills of "Podcast (field test)" on 2026-09-11, and a value is
# verified against intent because a read-back passes an off-frame Tilt.
# History: docs/evidence/overlay_position.md#one-positioning-rule.

#: The 2026-09-11 draw gain: every measurement this file reproduces
#: comes from that calibration's stills (see HISTORY_GAIN in
#: test_tight_box.py). Today's gain is proven separately
#: (`tests/unit/resolve/test_resolve_transform.py`) and by the rebuild gate.
#: Reel 13 @854, measured: 840x480 canvas, ink rows 279..432, cols 47..775.
TIGHT_CANVAS = (840.0, 480.0)
TIGHT_INK = (47.0, 279.0, 775.0, 432.0)
TIGHT_TILT = -1740.0
#: Where that caption is measured to draw on an exported still.
MEASURED_TIGHT_INK_ROWS = (1434.0, 1587.0)
#: Reel 13 @567, measured: full-frame canvas, ink rows 1415..1572.
FULL_INK = (272.0, 1415.0, 800.0, 1572.0)
#: Reel 30's first graphic: a 724x480 canvas stored at Tilt 5184, whose
#: whole canvas draws at frame rows -576..-96.  Its ink fills the
#: canvas from row 40 down, so nothing of it reaches row 0.
OFF_FRAME_CANVAS_2 = (724.0, 480.0)
OFF_FRAME_INK = (0.0, 40.0, 724.0, 440.0)
MEASURED_OFF_FRAME_CANVAS_ROWS = (-576.0, -96.0)


def _p(tilt, pan=0.0):
    return {"scaling": 1, "pan": pan, "tilt": tilt}


def test_the_rule_predicts_the_exported_still_from_both_carriages():
    """The one relation, checked against pixels off a real timeline."""
    ox, oy = canvas_screen_origin(*TIGHT_CANVAS, _p(TIGHT_TILT), *FRAME, draw_gain=HISTORY_GAIN)
    assert oy == pytest.approx(1155.0, abs=0.5), (
        "the canvas top measured on the exported still is row 1155")
    assert ox == pytest.approx(120.0, abs=0.5)
    box = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME, draw_gain=HISTORY_GAIN)
    assert (box[1], box[3]) == pytest.approx(MEASURED_TIGHT_INK_ROWS, abs=1.0)

    # Two Inspector numbers, one screen row - the captain's question:
    # full-frame needs 0, tight needs -1740, computed from DIFFERENT
    # canvases and landing within tolerance of each other.
    tight = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME, draw_gain=HISTORY_GAIN)
    full = ink_screen_box(*FRAME, None, FULL_INK, *FRAME, draw_gain=HISTORY_GAIN)
    assert full == pytest.approx(FULL_INK), (
        "a full-frame artefact with no transform draws 1:1")
    assert abs(tight[3] - full[3]) < INTENT_TOLERANCE_PX, (
        "the same caption row, reached from two carriages")
    assert tight[3] != full[3], (
        "and reached by different stored numbers, so this is a real "
        "comparison and not the same arithmetic twice")


def test_placement_for_box_and_canvas_screen_origin_round_trip():
    """The forward rule and its inverse are the SAME rule."""
    for canvas_w, canvas_h in ((840.0, 480.0), (1080.0, 1920.0),
                               (772.0, 540.0), (484.0, 480.0)):
        for cx, cy in ((540.0, 1395.0), (436.0, 512.0), (540.0, 960.0)):
            placement = placement_for_box(canvas_w, canvas_h, cx, cy, *FRAME, draw_gain=HISTORY_GAIN)
            ox, oy = canvas_screen_origin(canvas_w, canvas_h, placement,
                                          *FRAME, draw_gain=HISTORY_GAIN)
            assert ox + canvas_w / 2.0 == pytest.approx(cx, abs=1e-6)
            assert oy + canvas_h / 2.0 == pytest.approx(cy, abs=1e-6)


def test_intent_accepts_both_carriages_and_refuses_the_off_frame_one_a_readback_passes():
    """One intent, two carriages accepted, the off-frame value refused."""
    intent = FULL_INK  # the caption row, in frame pixels
    assert verify_ink_against_intent(*TIGHT_CANVAS, _p(TIGHT_TILT),
                                     TIGHT_INK, intent, *FRAME, draw_gain=HISTORY_GAIN) == ""
    assert verify_ink_against_intent(*FRAME, None, FULL_INK, intent,
                                     *FRAME, draw_gain=HISTORY_GAIN) == ""
    ox, oy = canvas_screen_origin(*OFF_FRAME_CANVAS_2, _p(OFF_FRAME_TILT),
                                  *FRAME, draw_gain=HISTORY_GAIN)
    assert (oy, oy + OFF_FRAME_CANVAS_2[1]) == pytest.approx(
        MEASURED_OFF_FRAME_CANVAS_ROWS, abs=1.0), (
        "the still of that frame contains no part of this artefact")
    reason = verify_ink_against_intent(*OFF_FRAME_CANVAS_2,
                                       _p(OFF_FRAME_TILT), OFF_FRAME_INK,
                                       intent, *FRAME, draw_gain=HISTORY_GAIN)
    assert reason, "Tilt 5184 on a 480 canvas must not pass this intent"
    assert "ENTIRELY OUTSIDE THE FRAME" in reason
    assert "5184" not in reason, (
        "the reason must name the PICTURE, not re-state the number")

    # Through the placer itself: `_intent_reason` is handed the value
    # Resolve HELD - identical to the value set, which is exactly the
    # case a read-back passes.
    draw_intent = {"canvas": TIGHT_CANVAS, "frame": FRAME,
                   "ink_in_canvas": TIGHT_INK, "intent_box": FULL_INK}
    good = _intent_reason(draw_intent, _p(TIGHT_TILT),
                          {"Pan": 0.0, "Tilt": TIGHT_TILT}, draw_gain=HISTORY_GAIN)
    assert good == ""
    off_frame_intent = dict(draw_intent, canvas=OFF_FRAME_CANVAS_2,
                            ink_in_canvas=OFF_FRAME_INK)
    off_frame = _intent_reason(off_frame_intent, _p(OFF_FRAME_TILT),
                               {"Pan": 0.0, "Tilt": OFF_FRAME_TILT}, draw_gain=HISTORY_GAIN)
    assert "off by" in off_frame and "ENTIRELY OUTSIDE THE FRAME" in off_frame


# --------------------------------------------------------------------------
# From test_overlay_verify.py
#
# The check that catches a wrong-but-stored position.
#
# PR 927 read placements back and reported "held exactly" - fidelity of
# storage, never correctness of intent. These tests pin the two halves
# that would have caught Reel 09: the value half fails a clamped store
# against the intent it disobeys, and the pixel half fails ink that
# renders away from intent. Every test builds its fixtures under
# `tmp_path` or in memory; nothing reaches Resolve or a real project.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_verify import (  # noqa: E402
    canvas_origin,
    verify_pixels,
    verify_values,
)

FULL = (1080, 1920)

#: What the Reel 09 caption computation asked for - and what Resolve
#: past its silent clamp actually holds on the small-canvas era.
COMPUTED_2 = {"scaling": 1, "pan": 0.0, "tilt": -7929.0}
CLAMPED = {"scaling": 1, "pan": 0.0, "tilt": -7680.0}


#: The tight canvas the Reel 09 captions were placed on.


def _clip(label, stored, kind="caption", segment_id="sub_x"):
    return {"label": label, "kind": kind, "segment_id": segment_id,
            "stored": stored, "canvas_wh": CANVAS}


def test_matching_store_passes_values():
    report = verify_values(
        [_clip("cap", dict(COMPUTED_2))], {},
        computed={("caption", "sub_x"): dict(COMPUTED_2)}, full_wh=(1080, 1920))
    assert report["passed"] and report["checked"] == 1


def test_clamped_store_fails_values_with_both_numbers():
    report = verify_values(
        [_clip("cap", dict(CLAMPED))], {},
        computed={("caption", "sub_x"): dict(COMPUTED_2)}, full_wh=(1080, 1920))
    assert not report["passed"]
    finding = report["findings"][0]
    assert finding["stored"]["tilt"] == -7680.0
    assert finding["expected"]["tilt"] == -7929.0
    assert finding["gap"]["tilt"] == -7680.0 - -7929.0


def test_declared_intent_is_the_expectation():
    # The pin names the PLACE Tilt -1700 reaches on this canvas
    # under the 2026-09-11 gain (pinned as history - see HISTORY_GAIN
    # in test_tight_box.py).
    intent = {"caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1}}
    computed = {("caption", "sub_x"): {"scaling": 1, "pan": 0.0,
                                       "tilt": -1744.0}}
    report = verify_values(
        [_clip("cap", {"scaling": 1, "pan": 0.0, "tilt": -1700.0})],
        intent, computed=computed, full_wh=(1080, 1920),
        draw_gain=1.0)
    assert report["passed"]
    assert report["findings"] == []


def test_computed_store_fails_against_declared_intent():
    intent = {"caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1}}
    computed = {("caption", "sub_x"): {"scaling": 1, "pan": 0.0,
                                       "tilt": -1744.0}}
    report = verify_values(
        [_clip("cap", {"scaling": 1, "pan": 0.0, "tilt": -1744.0})],
        intent, computed=computed, full_wh=(1080, 1920))
    assert not report["passed"]
    assert report["findings"][0]["provenance"] == "declared"


def _asset_frame(path, canvas_wh=(200, 120), ink=(40, 30, 160, 90)):
    from PIL import Image

    image = Image.new("RGBA", canvas_wh, (0, 0, 0, 0))
    pixels = image.load()
    for y in range(ink[1], ink[3]):
        for x in range(ink[0], ink[2]):
            pixels[x, y] = (255, 255, 255, 255)
    image.save(path)


def test_matching_store_passes_pixels(tmp_path):
    frame = str(tmp_path / "asset.png")
    _asset_frame(frame)
    placement = placement_for_box(200, 120, 540.0, 960.0, *FULL)
    clip = _clip("cap", dict(placement))
    clip.update({"asset_frame": frame, "canvas_wh": (200, 120)})
    key = ("caption", "sub_x")
    report = verify_pixels([clip], {}, computed={key: dict(placement)}, full_wh=(1080, 1920))
    assert report["passed"] and report["checked"] == 1


def test_shifted_store_fails_pixels_with_gap(tmp_path):
    frame = str(tmp_path / "asset.png")
    _asset_frame(frame)
    expected = placement_for_box(200, 120, 540.0, 960.0, *FULL)
    stored = placement_for_box(200, 120, 540.0, 971.0, *FULL)
    clip = _clip("cap", dict(stored))
    clip.update({"asset_frame": frame, "canvas_wh": (200, 120)})
    key = ("caption", "sub_x")
    report = verify_pixels([clip], {}, computed={key: dict(expected)}, full_wh=(1080, 1920))
    assert not report["passed"]
    assert report["findings"][0]["gap_px"] == 11.0
