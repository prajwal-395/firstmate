"""The seven steps of a composed edit, one failure mode each.

`library/tools/composed_edit.py` composes an edit Resolve has no verb
for out of delete and place.  It is seven steps and none of them is
optional, because each one exists because something measurably failed
without it.  This file pins that: for every step there is a test that
DRIVES THE DEFECT - against a fake Resolve that reproduces Resolve's own
measured behaviour (`tests/composed_edit_harness.py`) - and shows the
step catching it.

The condition the captain attached to this path (a comp must be
RE-DERIVED across a played-length change, never restored) has its own
file: `tests/test_composed_edit_refusal.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402
from library.tools import reel_read  # noqa: E402
from tests.composed_edit_harness import (  # noqa: E402
    IDENTITY_PROPERTIES,
    build_reel,
    covering_window,
    duplicate,
    frames_of,
    media_pool,
    pool_clip,
    reel_timeline,
)
from tests.composed_edit_harness import item as make_item  # noqa: E402

CUT = 1069        # where the restored phrase goes in, on the lab reel
RESTORE = 13      # frames of speech put back
OLD_END = 1274    # the lab reel's last frame + 1
ENDING = 24       # frames of new ending appended


def _dirs(tmp_path):
    return (str(tmp_path / "comps"), str(tmp_path / "withheld"))


class _Rederiver(ce.CompRederiver):
    """A comp generator that really re-keys, as `apply_comps` does.

    `apply_fusion_comps` reads each clip's played length off the LIVE
    timeline item (`played = tl_clip.GetDuration()`), so this writes a
    comp whose recorded key count is the item's NEW duration - which is
    what makes the re-derivation a route into the builder rather than
    arithmetic over the capture.
    """

    def __init__(self, timeline, reason=None, ran=True, ok=True,
                 touch=True):
        self.timeline = timeline
        self.reason = reason
        self._ran, self._ok, self._touch = ran, ok, touch
        self.calls = []

    def reachable_reason(self, changes):
        return self.reason

    def rederive(self, changes):
        self.calls.append([c.row for c in changes if c.played_length_changes])
        if self._touch:
            for change in changes:
                if not (change.played_length_changes and change.comp_count):
                    continue
                row = self.timeline.rows[change.row]
                item = next(i for i in row
                            if i.GetStart() == change.record_frame)
                item.comps = make_item(
                    item.GetMediaPoolItem(), item.GetStart(), item.GetDuration(), item.GetLeftOffset(),
                    comp_windows=[covering_window(
                        item.GetDuration(), item.GetLeftOffset(), frames_of(item.GetMediaPoolItem()))],
                ).comps
                item.rederived_over = item.GetDuration()
        return {"ran": self._ran, "ok": self._ok}


def _through_step6(timeline, pool, changes, tmp_path):
    """Steps 3 to 6 for a whole plan: capture, delete, place, verify.

    A single item cannot be re-placed on its own - it would collide with
    the downstream items that have not moved yet, which is exactly what
    step 4 exists to prevent - so the isolated step-7 tests compose the
    whole plan and then restore the one item they are about.
    """
    captures = [ce.capture_item(timeline.rows[c.row][c.item_index], c,
                                *_dirs(tmp_path)) for c in changes]
    ce.delete_all(timeline, [timeline.rows[c.change.row][c.change.item_index]
                             for c in captures])
    ordered = ce.placement_order(captures)
    ce.place_all(pool, ordered)
    return captures, ce.verify_placement(ce._rows_of(timeline), ordered)


# ── Step 1: stage, and conform the copy's comp windows ──────────────
#
# A copy is not automatically a faithful control: copying a timeline was
# measured to NORMALISE some MediaIn windows with nothing else in the
# readable structure differing, and the only symptom is a render that
# differs or fails.


def test_step1_conform_repairs_a_copys_drifted_window_and_verifies(tmp_path):
    source, _pool, _media = build_reel(tmp_path)
    staged = duplicate(source, drift=[("V1", 0, 1)])

    source_rows = {r: source.rows[r] for r in source.rows}
    staged_rows = {r: staged.rows[r] for r in staged.rows}

    before = ce.read_windows(staged_rows)[("V1", 0, 1)]
    wanted = ce.read_windows(source_rows)[("V1", 0, 1)]
    assert before != wanted, "the harness did not drift the window"

    receipt = ce.conform_comp_windows(source_rows, staged_rows)

    assert [r["where"] for r in receipt["repaired"]] == [["V1", 0, 1]]
    assert ce.read_windows(staged_rows)[("V1", 0, 1)] == wanted


def test_step1_reimports_the_source_comp_when_setinput_will_not_move(tmp_path):
    """Measured in the lab: `SetInput` cannot always pull a window back.

    A copy of the lab reel came back with `GlobalIn -3150` against the
    source's `-3151`, and `SetInput(-3151)` returned `None` and changed
    nothing - exactly as it returns `None` when it succeeds. Re-importing
    the SOURCE item's own comp is the repair that works
    (`comp_media_window`), and the verdict is a fresh re-read.
    """
    source, _pool, _media = build_reel(tmp_path)
    staged = duplicate(source, drift=[("V1", 0, 1)])
    for comp in staged.rows["V1"][0].comps:
        comp.GetToolList()["MediaIn1"].inert = True
    source_rows = {r: source.rows[r] for r in source.rows}
    staged_rows = {r: staged.rows[r] for r in staged.rows}

    # Without anywhere to write the export, only the write that does not
    # work is available - and it refuses rather than claiming a repair.
    with pytest.raises(ce.StagingNotConformed) as refusal:
        ce.conform_comp_windows(source_rows, staged_rows)
    assert "`SetInput` did not move it" in str(refusal.value)

    receipt = ce.conform_comp_windows(source_rows, staged_rows,
                                      comp_dir=str(tmp_path / "conform"))
    assert [(r["where"], r["how"]) for r in receipt["repaired"]] == [
        (["V1", 0, 1], "re-import")]
    after = ce.read_windows(staged_rows)[("V1", 0, 1)]
    wanted = ce.read_windows(source_rows)[("V1", 0, 1)]
    assert ce._frames_of(after) == ce._frames_of(wanted)
    assert receipt["repaired"][0]["wrote_after_import"]["wrote"]




def test_step1_writes_the_binding_and_stops_when_that_is_enough(tmp_path):
    """The window is DERIVED from the binding, and over-writing corrupts it.

    Measured on Resolve Studio 21.1: rebinding `MediaSource` to
    `Timeline` recomputes the window exactly, and writing the four frame
    terms on top of a correct window moves `GlobalIn` one frame LATER -
    which is the `1 / 19` that stopped six of the captain's eight reels
    from rendering their own endings. Every write returns `None`, so only
    the read-back tells the two apart.
    """
    from tests.composed_edit_harness import FakeComp, FakeTool

    source, _pool, _media = build_reel(tmp_path)
    staged = duplicate(source)
    wanted = ce.read_windows({"V1": source.rows["V1"]})[("V1", 0, 1)]
    item = staged.rows["V1"][0]
    rebound = {"MediaSource": "MediaPool", "MediaID": "x",
               "AudioTrack": "No_Audo_Track", "GlobalIn": 1,
               "GlobalOut": 2249, "ClipTimeStart": 0, "ClipTimeEnd": 2248}
    derived = {k: wanted[k] for k in ce.WINDOW_FRAME_TERMS}
    item.comps = [FakeComp(
        {"MediaIn1": FakeTool("MediaIn", rebound, derived=derived)})]

    after, wrote = ce.apply_window(item, 1, wanted)

    assert wrote["derived_from_binding"] is True
    assert wrote["wrote"] == list(ce.WINDOW_BINDING_TERMS), (
        "it wrote the frame terms onto a window that was already correct")
    assert ce._frames_of(after) == ce._frames_of(wanted)


def test_step1_removes_an_empty_composition_it_cannot_conform(tmp_path):
    """A comp that draws nothing is REMOVED from the copy, not conformed.

    Measured 2026-09-12: Resolve's own auto-created `MediaIn -> MediaOut`
    composition will not take a window write at all - setting `GlobalIn`
    to 0 left it at 1, the uncovered first frame that fails a whole
    render job - and it is not stable between reads either. It draws no
    picture, so it goes, and the byte comparison against a rebuild is
    what says that changes nothing.

    An UNREADABLE graph stays: it is not evidence of an empty one.
    """
    from tests.composed_edit_harness import FakeComp, FakeTool

    source, _pool, _media = build_reel(tmp_path)
    staged = duplicate(source)
    bare = lambda window, inert: FakeComp({  # noqa: E731
        "MediaIn1": FakeTool("MediaIn", window, inert=inert),
        "MediaOut1": FakeTool("MediaOut", {})})
    source.rows["V3"][0].comps = [bare(covering_window(264, 0, 1675), False)]
    staged.rows["V3"][0].comps = [bare(
        dict(covering_window(264, 0, 1675), GlobalOut=1676), True)]
    staged.rows["V4"][0].comps = [object()]        # answers no getter

    receipt = ce.conform_comp_windows(
        {r: source.rows[r] for r in source.rows},
        {r: staged.rows[r] for r in staged.rows},
        comp_dir=str(tmp_path / "conform"))

    assert [e["where"] for e in receipt["emptied"]] == [["V3", 0]]
    assert staged.rows["V3"][0].GetFusionCompCount() == 0
    assert staged.rows["V4"][0].GetFusionCompCount() == 1, (
        "an unreadable graph was removed as though it were empty")
    # The V1 comps really do draw, and are conformed rather than removed.
    assert all(item.GetFusionCompCount() == 1 for item in staged.rows["V1"])


# ── Step 2: plan by arithmetic over a full read ─────────────────────


def test_step2_plan_reaches_every_row_including_audio_and_captions(tmp_path):
    """Never a list of names.

    The prior spike planned over V1 and measured "13 frames of caption
    desync" on V4 as an API defect. It was V4 simply not being in the
    plan.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)

    by_row = {}
    for change in changes:
        by_row.setdefault(change.row, []).append(change.how)

    assert by_row == {"V1": [ce.EXTEND, ce.SHIFT, ce.SHIFT],
                      "V3": [ce.EXTEND],
                      "V4": [ce.SHIFT, ce.SHIFT, ce.SHIFT],
                      "A1": [ce.EXTEND, ce.SHIFT]}, by_row
    # V2 ends before the cut and does not move. V3 spans the cut and is
    # EXTENDED with it - an overlay that did not carry out would leave
    # the last 13 frames of the reel with nothing over them, which is
    # the eighteen bare frames the prior spike left behind.
    assert "V2" not in by_row




def test_step2_refuses_an_item_with_no_source_headroom(tmp_path):
    """The freeze hold cannot be lengthened from the timeline.

    `reel_freeze_*.mov` is exactly as long as the hold: left_offset 0,
    right_offset 0. A longer hold needs the asset re-rendered, which is
    a file operation. The plan refuses BY NAME rather than placing a
    hole.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(ce.SourceHeadroomExhausted) as refusal:
        ce.plan_ripple(reel_read.read_tracks(timeline), OLD_END, ENDING)
    assert "freeze" in str(refusal.value)
    assert "headroom_frames': 0" in str(refusal.value)


def test_step2_carries_the_overlay_out_when_the_ending_changes(tmp_path):
    """The other rows end where the old ending ended and must follow.

    The prior spike left eighteen frames of the timeline with no TV
    frame over them. V3 has 401 frames of source headroom, so it goes.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), OLD_END,
                             ENDING, exclude=[("V1", 2)])
    assert [(c.row, c.how, c.duration) for c in changes] == [
        ("V3", ce.EXTEND, OLD_END + ENDING)]


def test_step2_a_gap_refuses_before_anything_is_deleted(tmp_path):
    """Every frame of the timeline must show a clip (AGENTS.md 10.2)."""
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    good = ce.plan_ripple(tracks, CUT, RESTORE)
    assert ce.assert_every_frame_covered(tracks, good)

    # The plan with the downstream V1 items left out: the extension
    # opens a 13-frame hole that Resolve renders as black.
    holed = [c for c in good if not (c.row == "V1" and c.item_index)]
    with pytest.raises(ce.PlacementNotVerified) as refusal:
        ce.assert_every_frame_covered(tracks, holed)
    assert "gap" in str(refusal.value)


def test_step2_ripple_refuses_to_replace_subtitles_with_unknown_trim(
        tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    subtitles = next(track for track in tracks if track["index"] == 4)
    subtitles["name"] = "Subtitles"
    subtitles["clips"][0]["left_offset"] = None

    with pytest.raises(ce.CaptionSourceTrimUnreadable,
                       match=r"V4\[0\].*left_offset"):
        ce.plan_ripple(tracks, CUT, RESTORE)




def test_step2_an_unreadable_comp_counts_as_a_treatment(tmp_path):
    """Fail closed: an unreadable graph is not evidence of an empty one."""
    timeline, _pool, _media = build_reel(tmp_path)
    overlay = timeline.rows["V3"][0]
    overlay.comps = [object()]        # answers no getter at all
    changes = {(c.row, c.item_index): c
               for c in ce.plan_ripple(reel_read.read_tracks(timeline),
                                       CUT, RESTORE)}
    assert changes[("V3", 0)].comp_count == 1


# ── Step 3: capture ─────────────────────────────────────────────────




def test_step3_capture_records_the_comps_media_window(tmp_path):
    """`ImportFusionComp` discards it, so the capture must carry it."""
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    change = next(c for c in ce.plan_ripple(tracks, CUT, RESTORE)
                  if (c.row, c.item_index) == ("V1", 1))
    capture = ce.capture_item(timeline.rows["V1"][1], change, *_dirs(tmp_path))

    assert capture.restorable_comps[0].window["GlobalIn"] == -1200
    assert capture.restorable_comps[0].window["MediaSource"] == "Timeline"


# ── Step 4: delete everything that changes, before placing anything ─


def test_step4_placing_before_deleting_silently_places_nothing(tmp_path):
    """The defect the ordering defends against, driven end to end.

    `AppendToTimeline` returns a truthy list of live-looking handles and
    places NOTHING where it would collide. A composition that places
    before it deletes loses every colliding item, silently.
    """
    timeline, pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    changes = ce.plan_ripple(tracks, CUT, RESTORE)
    captures = [ce.capture_item(timeline.rows[c.row][c.item_index], c,
                                *_dirs(tmp_path)) for c in changes]
    ordered = ce.placement_order(captures)

    placed = ce.place_all(pool, ordered)          # WITHOUT deleting first
    assert placed["returned_truthy"] is True
    assert placed["returned_count"] == len(ordered)

    with pytest.raises(ce.PlacementNotVerified) as refusal:
        ce.verify_placement(ce._rows_of(timeline), ordered)
    assert "not on the track" in str(refusal.value)




# ── Step 5: place in one call, in increasing record order ───────────


def test_step5_end_frame_is_exclusive(tmp_path):
    """`left + duration - 1` places an item one frame SHORT.

    The prior spike's "two black frames" attributed to the API. It is a
    caller bug, and `clip_info` is the one place the arithmetic lives.
    """
    mpi = pool_clip("/lab/x.mov", frames=100)
    info = ce.clip_info(mpi, left_offset=10, duration=24, track_index=1,
                        record_frame=500)
    assert info["endFrame"] - info["startFrame"] == 24

    timeline = reel_timeline("short", {"V1": []})
    pool = media_pool(timeline)
    pool.AppendToTimeline([dict(info, endFrame=info["endFrame"] - 1)])
    assert timeline.rows["V1"][0].GetDuration() == 23, (
        "the harness must reproduce the off-by-one, or the test proves "
        "nothing")






# ── Step 6: verify by re-reading the track ──────────────────────────


def test_step6_the_return_value_is_not_the_verdict(tmp_path):
    """A truthy return and an empty track: the track wins."""
    timeline = reel_timeline("empty", {"V1": []})
    mpi = pool_clip("/lab/x.mov", frames=100)

    pool = media_pool(timeline)
    # placed NOWHERE, a handle returned anyway
    pool.AppendToTimeline = lambda infos: [make_item(mpi, 0, 10)
                                           for _ in infos]
    ordered = [(0, 1, 1, ce.clip_info(mpi, 0, 10, 1, 0), None)]
    assert ce.place_all(pool, ordered)["returned_truthy"] is True

    with pytest.raises(ce.PlacementNotVerified):
        ce.verify_placement(ce._rows_of(timeline), ordered)




# ── Step 7: restore, then re-derive ─────────────────────────────────


def test_step7_restores_transform_comp_window_and_grade(tmp_path):
    """A re-placed item comes back at identity with one colour node.

    Everything the delete destroyed, put back and READ BACK: the
    transform (the letterbox the prior spike measured), the comp, the
    comp's media window (without which the clip plays from source frame
    0 - a different moment of the take) and the grade.
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    change = next(c for c in changes if (c.row, c.item_index) == ("V1", 1))
    reference = duplicate(timeline).rows["V1"][1]
    captures, landed = _through_step6(timeline, pool, changes, tmp_path)
    capture = next(c for c in captures if c.change is change)
    placed = landed[("V1", change.record_frame)]

    assert placed.GetProperty("ZoomX") == IDENTITY_PROPERTIES["ZoomX"]
    assert placed.GetFusionCompCount() == 0
    assert placed.GetNumNodes() == 1

    receipt = ce.restore_item(placed, capture, grade_source=reference,
                              timeline=timeline,
                              link_with=[timeline.rows["A1"][0]])

    assert placed.GetProperty("ZoomX") == 2.307
    assert placed.GetFusionCompCount() == 1
    assert receipt["windows"][0]["readback"] == capture.restorable_comps[0].window
    assert placed.GetNumNodes() == 8
    assert receipt["property_readback_diff"] == {}
    assert timeline.rows["A1"][0] in placed.GetLinkedItems()


def test_step7_refuses_when_the_media_window_will_not_go_back(tmp_path):
    """Without the window the clip plays a different moment of the take.

    It renders. It looks plausible. It is the wrong footage - 19.83/255
    of picture on the spike's reel, on a floor of zero.
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    change = next(c for c in changes if (c.row, c.item_index) == ("V1", 1))
    captures, landed = _through_step6(timeline, pool, changes, tmp_path)
    capture = next(c for c in captures if c.change is change)
    placed = landed[("V1", change.record_frame)]
    placed.inert_media_in = True      # SetInput takes nothing

    with pytest.raises(ce.RestoreNotVerified) as refusal:
        ce.restore_item(placed, capture)
    assert "media window read back" in str(refusal.value)




# ── The whole composition, both cases ───────────────────────────────


def test_case1_an_in_clip_trim_composes_and_every_step_is_receipted(tmp_path):
    """Case 1 - restore a 13-frame phrase inside a V1 clip."""
    timeline, pool, _media = build_reel(tmp_path)
    control = duplicate(timeline)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    grade_sources = {(c.row, c.item_index):
                     control.rows[c.row][c.item_index]
                     for c in changes if c.track_type == "video"}
    rederiver = _Rederiver(timeline)

    comp_dir, withheld = _dirs(tmp_path)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=comp_dir, withheld_dir=withheld, rederiver=rederiver,
        grade_sources=grade_sources, link_rows={"V1": "A1"})

    assert receipt.deleted["asked"] == len(changes)
    assert receipt.verified == {"landed": len(changes), "asked": len(changes)}
    # V1[0], V3[0] and A1[0] play a different number of frames.
    assert receipt.rederivation_required["trimmed"] == 3
    # Only V1[0] carries a builder-written comp: the audio item and the
    # overlay row do not, so there is nothing there to re-derive.
    assert receipt.rederived["verified"]["checked"] == 1

    rows = ce._rows_of(timeline)
    spans = [(i.GetStart(), i.GetEnd()) for i in rows["V1"]]
    assert spans == [(590, 1082), (1082, 1268), (1268, 1287)]
    assert [b[0] - a[1] for a, b in zip(spans, spans[1:])] == [0, 0]
    # The downstream rows moved with it, exactly.
    assert [i.GetStart() for i in rows["V4"]] == [1092, 1142, 1192]
    assert [i.GetStart() for i in rows["A1"]] == [590, 1082]
    # The row that ends before the cut did not move.
    assert [(i.GetStart(), i.GetEnd()) for i in rows["V2"]] == [(0, 590)]


def test_case2_an_ending_change_composes_and_carries_the_rows_out(tmp_path):
    """Case 2 - append a new ending and carry the other rows to meet it."""
    timeline, pool, media = build_reel(tmp_path)
    control = duplicate(timeline)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), OLD_END, ENDING,
                             exclude=[("V1", 2)])
    # A new item comes back at IDENTITY, so how it is treated is
    # DECLARED. The tail it follows is punched in, and so is this.
    ending = ce.Insertion(track_type="video", track_index=1,
                          media_pool_item=media["aroll"], left_offset=4000,
                          duration=ENDING, record_frame=OLD_END,
                          name="new ending",
                          properties={"ZoomX": 2.307, "Pan": 99.656},
                          grade_from=control.rows["V1"][2])
    comp_dir, withheld = _dirs(tmp_path)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        insertions=[ending], comp_dir=comp_dir, withheld_dir=withheld,
        rederiver=_Rederiver(timeline),
        grade_sources={("V3", 0): control.rows["V3"][0]})

    rows = ce._rows_of(timeline)
    v1 = [(i.GetStart(), i.GetEnd()) for i in rows["V1"]]
    assert v1 == [(590, 1069), (1069, 1255), (1255, 1274), (1274, 1298)]
    assert [b[0] - a[1] for a, b in zip(v1, v1[1:])] == [0, 0, 0]
    # Zero frames of the timeline with no overlay over them.
    assert [(i.GetStart(), i.GetEnd()) for i in rows["V3"]] == [(0, 1298)]
    assert receipt.rederivation_required["trimmed"] == 1
    assert receipt.rederivation_required["insertions"] == 1
    # The new ending carries the treatment that was declared for it, and
    # the grade of the shot it follows - not identity.
    assert rows["V1"][3].GetProperty("ZoomX") == 2.307
    assert rows["V1"][3].GetNumNodes() == 8
    assert receipt.inserted[0]["property_readback_diff"] == {}


def test_the_composition_is_one_delete_and_one_place(tmp_path):
    """Ordering is the whole defence, and it is one call each."""
    timeline, pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    comp_dir, withheld = _dirs(tmp_path)
    ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                           changes=changes, comp_dir=comp_dir,
                           withheld_dir=withheld,
                           rederiver=_Rederiver(timeline))
    assert timeline.delete_calls == [len(changes)]
    assert pool.append_calls == [len(changes)]
