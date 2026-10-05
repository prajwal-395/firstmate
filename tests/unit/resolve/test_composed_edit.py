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
file: `tests/unit/resolve/test_composed_edit.py`.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
import ast
import dataclasses
from library.tools.composed_edit import set_properties


REPO = Path(__file__).resolve().parents[3]
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


def _write_context(tmp_path, timeline):
    return {
        "project": "composed-edit-test",
        "project_folder": str(tmp_path),
        "timeline_name": timeline.GetName(),
        "timeline_id": timeline.GetUniqueId(),
        "run_id": "composed-edit-test-run",
    }


def _bare_write_context(tmp_path):
    return {
        "project": "composed-edit-test",
        "project_folder": str(tmp_path),
        "timeline_name": "test timeline",
        "timeline_id": "test-timeline-id",
        "run_id": "composed-edit-test-run",
    }


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
                              link_with=[timeline.rows["A1"][0]],
                              write_context=_write_context(tmp_path,
                                                           timeline))

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
        ce.restore_item(placed, capture,
                        write_context=_write_context(tmp_path, timeline))
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
        grade_sources=grade_sources, link_rows={"V1": "A1"},
        write_context=_write_context(tmp_path, timeline))

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
        grade_sources={("V3", 0): control.rows["V3"][0]},
        write_context=_write_context(tmp_path, timeline))

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
                           rederiver=_Rederiver(timeline),
                           write_context=_write_context(tmp_path, timeline))
    assert timeline.delete_calls == [len(changes)]
    assert pool.append_calls == [len(changes)]


# ── A touch is judged on what IT changes ─────────────────────────────
#
# Ren refused five of the captain's reels (07, 08, 09, 10, 13) for ANY
# edit: `assert_every_frame_covered(..., row="V1")` read an interior V1
# gap in the post-edit plan as the edit's defect, and those reels'
# approved shape is a V1 hole covered by a V2 cutaway (Reel 07: V1 gap
# 116..665 with V2 at 116..665).


def _span_clip(start: int, duration: int, name: str = "c") -> dict:
    return {"record_in": start, "record_out": start + duration,
            "duration": duration, "left_offset": 0, "name": name}


def test_only_a_v1_gap_the_edit_opens_refuses():
    """A V6 logo swap on Reel 07's shape never touches V1, so the
    pre-existing covered hole does not refuse it; shifting a V1 item so
    the plan opens a hole the reel did not have still refuses, naming
    the new gap."""
    tracks = [
        {"type": "video", "index": 1,
         "clips": [_span_clip(0, 116, "v1-head"),
                   _span_clip(665, 335, "v1-tail")]},
        {"type": "video", "index": 2,
         "clips": [_span_clip(116, 549, "v2-cutaway")]},
        {"type": "video", "index": 6,
         "clips": [_span_clip(900, 100, "old-logo")]},
    ]
    swap = ce.Insertion(track_type="video", track_index=6,
                        media_pool_item=None, left_offset=0, duration=100,
                        record_frame=900, name="new-logo", properties={})
    spans = ce.assert_every_frame_covered(tracks, [], [swap], row="V1")
    assert spans == [(0, 116), (665, 1000)]

    tracks = [{"type": "video", "index": 1,
               "clips": [_span_clip(0, 100, "head"),
                         _span_clip(100, 100, "tail")]}]
    shifted = ce.ItemChange(
        track_type="video", track_index=1, item_index=1,
        record_frame=150, duration=100, left_offset=0,
        previous_record=100, previous_duration=100, how=ce.SHIFT,
        name="tail")
    with pytest.raises(ce.PlacementNotVerified) as refusal:
        ce.assert_every_frame_covered(tracks, [shifted], [], row="V1")
    assert "[100, 150]" in str(refusal.value)


# --------------------------------------------------------------------------
# From test_composed_edit_refusal.py
#
# The condition the captain attached, and every attempt to get round it.
#
# History: docs/evidence/resolve_test_history.md#test_composed_edit_refusal.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


MODULE = REPO / "library" / "tools" / "composed_edit.py"


def _trimmed(timeline):
    """The plan for the in-clip trim, and the one item it lengthens."""
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    head = next(c for c in changes
                if c.row == "V1" and c.played_length_changes)
    return changes, head


class _Generator(ce.CompRederiver):
    """A comp generator whose behaviour each bypass test dials in."""

    def __init__(self, reason=None, ran=True, ok=True, on_run=None):
        self.reason, self.ran, self.ok, self.on_run = reason, ran, ok, on_run
        self.calls = 0

    def reachable_reason(self, changes):
        return self.reason

    def rederive(self, changes):
        self.calls += 1
        if self.on_run is not None:
            self.on_run(changes)
        return {"ran": self.ran, "ok": self.ok}


# ── 1. No comp generator at all ─────────────────────────────────────


def test_bypass_1_no_generator_refuses_before_anything_is_destroyed(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    changes, head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    with pytest.raises(ce.CompRederivationUnreachable) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld, rederiver=None)

    assert "489 of 492 frames" in str(refusal.value)
    assert f"V1[{head.item_index}] 479->492" in str(refusal.value)
    # The refusal is worth nothing if it lands after the delete.
    assert timeline.delete_calls == []
    assert pool.append_calls == []
    assert not Path(comp_dir).exists() and not Path(withheld).exists(), (
        "it captured before it checked")
    assert [i.GetDuration() for i in timeline.rows["V1"]] == [479, 186, 19]


def test_an_edit_that_changes_no_played_length_needs_no_generator(tmp_path):
    """The refusal must not fail correct output (AGENTS.md 10.4).

    A pure shift changes no played length, so the captured comp IS the
    comp the edit implies and no generator is required. A gate that
    refused this would be no better than one that cannot fail.
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes = [c for c in ce.plan_ripple(reel_read.read_tracks(timeline),
                                         CUT, RESTORE)
               if not c.played_length_changes and c.row == "V4"]
    assert changes, "the fixture stopped producing pure shifts"
    comp_dir, withheld = _dirs(tmp_path)

    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=comp_dir, withheld_dir=withheld, rederiver=None,
        write_context=_write_context(tmp_path, timeline))

    assert receipt.rederivation_required == {"required": False, "trimmed": 0,
                                             "with_comps": 0, "insertions": 0}
    assert [i.GetStart() for i in timeline.rows["V4"]] == [1092, 1142, 1192]


def test_an_insertion_with_no_declared_treatment_refuses(tmp_path):
    """A new item comes back at IDENTITY, so its treatment is DECLARED.

    Measured on exported pixels: a 24-frame ending appended beside three
    shots carrying `ZoomX 2.307` rendered at `ZoomX 1.0` and diverged
    from a rebuild of the same edit on 100% of its own frames, mean
    21.8/255 - while all 264 frames before it were byte-identical. It
    placed, it verified, and it looked like a deliberate wide shot.

    The engine may not invent the framing (AGENTS.md 10.5), so the
    caller declares it - and `properties={}` is how identity is chosen
    on purpose, which is why this is a declaration and not a default.
    """
    timeline, pool, media = build_reel(tmp_path)
    comp_dir, withheld = _dirs(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), 1274, 24,
                             exclude=[("V1", 2)])
    undeclared = ce.Insertion(track_type="video", track_index=1,
                              media_pool_item=media["aroll"],
                              left_offset=4000, duration=24,
                              record_frame=1274, name="new ending")

    with pytest.raises(ce.InsertionUndeclared) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, insertions=[undeclared],
                               comp_dir=comp_dir, withheld_dir=withheld,
                               rederiver=_Generator(),
                               write_context=_write_context(tmp_path,
                                                            timeline))
    assert "comes back" in str(refusal.value)
    assert "'ZoomX': 2.307" in str(refusal.value), (
        "the refusal does not say what its neighbours carry")
    assert timeline.delete_calls == []

    # Identity, chosen on purpose, is accepted.
    chosen = dataclasses.replace(undeclared, properties={})
    ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                           changes=changes, insertions=[chosen],
                           comp_dir=comp_dir, withheld_dir=withheld,
                           rederiver=_Generator(),
                           write_context=_write_context(tmp_path, timeline))
    assert [i.GetStart() for i in timeline.rows["V1"]] == [590, 1069, 1255, 1274]


# ── 2. A generator that cannot be reached ───────────────────────────


def test_bypass_2_an_unreachable_generator_refuses_by_its_own_reason(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    with pytest.raises(ce.CompRederivationUnreachable) as refusal:
        ce.apply_composed_edit(
            timeline=timeline, media_pool=pool, changes=changes,
            comp_dir=comp_dir, withheld_dir=withheld,
            rederiver=_Generator(reason="the manifest declares no per-clip "
                                        "Fusion effects"),
            write_context=_write_context(tmp_path, timeline))
    assert "no per-clip Fusion effects" in str(refusal.value)
    assert timeline.delete_calls == []


def test_a_trimmed_comp_on_a_row_the_pass_never_writes_is_refused(tmp_path):
    """The pass writes comps on V1 and V2 (`execution/fusion_tracks.py`).

    A comp on any other row cannot be re-derived through it, so an edit
    that trims one has no route to the generator for that clip and must
    say so - rather than running the pass, watching it succeed, and
    leaving the stale comp in place.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    changes, head = _trimmed(timeline)
    on_v4 = dataclasses.replace(head, track_index=4, item_index=0,
                                comp_count=1)
    manifest = {"tracks": {"V1": {"clips": [{"source_file": "/lab/a.mov"}] * 3}},
                "fusion_effects": {"per_clip": {"a_roll_0": {"glow_gain": 1.4}}}}
    rederiver = ce.ReelLookRederiver(manifest, str(tmp_path), "P", "T")

    assert rederiver.reachable_reason(changes) is None
    reason = rederiver.reachable_reason(list(changes) + [on_v4])
    assert "V4 carries a comp on a clip this edit trims" in reason


# ── 3 and 4. Putting the comp into the capture by hand ──────────────


def test_bypass_3_a_hand_built_capture_with_the_comp_is_refused(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    comp = ce.CapturedComp(index=1, path=str(tmp_path / "stale.comp"),
                           window={"GlobalIn": 0})

    with pytest.raises(ce.CompRestoreRefused) as refusal:
        ce.ItemCapture(change=head, properties={}, geometry={},
                       media_pool_item=None, restorable_comps=(comp,))
    assert "489 of 492 frames" in str(refusal.value)
    assert "RE-DERIVED through the builder" in str(refusal.value)


def test_bypass_4_mutating_a_real_capture_to_carry_the_comp_is_refused(tmp_path):
    """`dataclasses.replace` is the obvious route, and it goes through
    `__post_init__` like any other construction."""
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    capture = ce.capture_item(timeline.rows["V1"][head.item_index], head,
                              *_dirs(tmp_path))
    assert capture.restorable_comps == ()
    assert len(capture.withheld_comps) == 1

    with pytest.raises(ce.CompRestoreRefused):
        dataclasses.replace(capture,
                            restorable_comps=capture.withheld_comps)


# ── 5. Reaching for the artefact on disk ────────────────────────────


def test_bypass_5_the_stale_comp_is_not_in_the_restore_directory(tmp_path):
    """The restore looks in `comp_dir`. A stale comp is never written there.

    Withholding the ARTEFACT, not only the accessor: there is nothing in
    the directory the restore reads for it to find, whatever a caller
    believes about the data model.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)
    capture = ce.capture_item(timeline.rows["V1"][head.item_index], head,
                              comp_dir, withheld)

    assert sorted(Path(comp_dir).iterdir()) == []
    written = sorted(Path(withheld).iterdir())
    assert len(written) == 1
    assert "WITHHELD_stale_across_trim" in written[0].name
    assert capture.withheld_comps[0].path == str(written[0])
    # And a shift's comp DOES land in the restore directory, so the
    # withholding is about the length change and not about comps.
    shift = next(c for c in _changes
                 if not c.played_length_changes and c.comp_count)
    ce.capture_item(timeline.rows[shift.row][shift.item_index], shift,
                    comp_dir, withheld)
    assert len(sorted(Path(comp_dir).iterdir())) == 1


# ── 6 and 7. A generator that runs and does not deliver ─────────────


def test_bypass_6_a_generator_that_reports_success_and_does_nothing(tmp_path):
    """The timeline is the verdict, not the pass's own report."""
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)
    generator = _Generator()          # reports ran/ok, touches nothing

    with pytest.raises(ce.CompRederivationNotProven) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld, rederiver=generator,
                               write_context=_write_context(tmp_path,
                                                            timeline))
    assert generator.calls == 1
    assert "carries no comp after the pass" in str(refusal.value)
    assert "keyed to the length it used to play" in str(refusal.value)


def test_bypass_7_a_generator_that_leaves_an_uncovered_window(tmp_path):
    """A comp whose MediaIn does not cover the frames the item now plays.

    Resolve does not draw black there: it FAILS the whole render job at
    the first uncovered frame (`comp_media_window`).
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    def _short(_changes):
        from tests.composed_edit_harness import FakeComp, FakeTool
        for row in ("V1", "V3"):
            for item in timeline.rows[row]:
                item.comps = [FakeComp({"MediaIn1": FakeTool("MediaIn", {
                    "MediaSource": "MediaPool", "MediaID": "x",
                    "AudioTrack": "No_Audo_Track", "GlobalIn": 1,
                    "GlobalOut": 400, "ClipTimeStart": 1,
                    "ClipTimeEnd": 400})})]

    with pytest.raises(ce.CompRederivationNotProven) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld,
                               rederiver=_Generator(on_run=_short),
                               write_context=_write_context(tmp_path,
                                                            timeline))
    assert "GlobalIn 1 is past comp frame 0" in str(refusal.value)


# ── 8. The one bypass only the source can rule out ──────────────────


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_bypass_8_the_restore_imports_only_from_restorable_comps():
    """The one `ImportFusionComp` in the restore iterates the accessor.

    A future edit that pointed it at `withheld_comps`, or at a path
    computed some other way, would reinstate exactly the defect this
    whole path exists to avoid - and every behavioural test above would
    still pass, because the data model would be satisfied. So it is
    pinned at the source.
    """
    restore = _function("restore_item")

    imports = [node for node in ast.walk(restore)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "ImportFusionComp"]
    assert len(imports) == 1, "the restore has more than one route to a comp"

    loops = [node for node in ast.walk(restore)
             if isinstance(node, ast.For)
             and any(isinstance(inner, ast.Call)
                     and isinstance(inner.func, ast.Attribute)
                     and inner.func.attr == "ImportFusionComp"
                     for inner in ast.walk(node))]
    assert len(loops) == 1
    iterated = loops[0].iter
    assert isinstance(iterated, ast.Attribute)
    assert iterated.attr == "restorable_comps", (
        f"the comp import iterates {ast.dump(iterated)}, not the accessor "
        f"that is empty across a played-length change")

    names = {node.attr for node in ast.walk(restore)
             if isinstance(node, ast.Attribute)}
    assert "withheld_comps" not in names, (
        "the restore path reads the withheld comp - the artefact is "
        "kept for diagnosis and must stay unreachable from here")


def test_the_refusal_runs_before_the_capture_in_the_orchestrator():
    """Order is the guarantee: a refusal after the delete is no refusal.

    Pinned at the source because the behavioural test above can only
    show that the timeline survived ONE refusal - this shows the check
    cannot be moved below the destructive calls without failing.
    """
    body = _function("apply_composed_edit").body
    positions = {}
    for index, node in enumerate(body):
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                if inner.func.id in ("assert_rederivation_reachable",
                                     "capture_item", "delete_all",
                                     "place_all"):
                    positions.setdefault(inner.func.id, index)
    assert set(positions) == {"assert_rederivation_reachable", "capture_item",
                              "delete_all", "place_all"}
    assert (positions["assert_rederivation_reachable"]
            < positions["capture_item"] < positions["delete_all"]
            < positions["place_all"]), positions


# --------------------------------------------------------------------------
# From test_readback_tolerance.py
#
# A property Resolve hands back one ulp off was still written.
#
# 2026-09-25: two post-header swaps were refused because a Pan of
# -8.610478359908884 read back as -8.610478359908885 - the item was placed
# exactly where declared, and exact float equality called it a framing
# nobody chose.

class _Item:
    def __init__(self, drift):
        self.props, self.drift = {}, drift

    def SetProperty(self, key, value):
        self.props[key] = value + self.drift if isinstance(
            value, float) else value
        return True

    def GetProperty(self):
        return dict(self.props)


def test_a_one_ulp_readback_is_the_value_written(tmp_path):
    assert set_properties(_Item(1e-15), {"Pan": -8.610478359908884,
                                         "Scaling": 1},
                          write_context=_bare_write_context(tmp_path),
                          item_identity={"test_item": "one"}) == {}


def test_a_value_that_did_not_take_is_still_refused(tmp_path):
    assert "Tilt" in set_properties(
        _Item(0.5), {"Tilt": 1844.0},
        write_context=_bare_write_context(tmp_path),
        item_identity={"test_item": "one"})


# --------------------------------------------------------------------------
# The master touch-up path (C-03)
#
# A small instruction on the master timeline ("make the captions a
# little less aggressive") changes only the affected region in place,
# without a rebuild - the same guarantee the reel touch-up path gives
# reels.  Driven against the fake Resolve; nothing reaches a real
# project.

MASTER = "Main Edit"


def _pass_lease(purpose, **kwargs):
    def decorator(func):
        return func
    return decorator


class _Fence:
    def __init__(self):
        self.drift_seen = []


from contextlib import contextmanager


@contextmanager
def _pass_fence(project, timeline, purpose):
    yield _Fence()


@contextmanager
def _pass_excursion(project, timeline, purpose):
    """Move the cursor to `timeline` and restore it, as the real one does."""
    previous = project.GetCurrentTimeline()
    project.SetCurrentTimeline(timeline)
    try:
        yield
    finally:
        project.SetCurrentTimeline(previous)


def _mock_edit_patch(monkeypatch):
    """Apply touchup EditPatch operations against the Resolve double."""
    from library.tools import edit_patch

    def apply_live_patch(*, project, timeline, operations, **_kwargs):
        for operation in operations:
            matches = [item for row in reel_read.live_items(timeline)
                       for item in row["items"]
                       if item.GetUniqueId() == operation["unique_id"]]
            assert len(matches) == 1
            item = matches[0]
            if operation["op"] == "clip.set_property":
                item.SetProperty(operation["key"], operation["value"])
                if item.GetProperty(operation["key"]) != operation["value"]:
                    return {"status": "refused",
                            "reason": "property read-back differed"}
            else:
                raise AssertionError(operation)
        return {"status": "committed", "generation": 1}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def _mock_shadow(monkeypatch):
    from library.tools import timeline_shadow

    class _Head:
        def summary(self):
            return {"generation": 1}

    monkeypatch.setattr(timeline_shadow, "observe",
                        lambda *a, **k: _Head())


def _master_project(tmp_path, monkeypatch):
    """A fake project holding the master timeline, with a project.yaml."""
    from library.tools import resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(MASTER)
    project = timeline._project
    project.SetCurrentTimeline(timeline)
    monkeypatch.setattr(resolve_lock, "under_lease", _pass_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _pass_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "resolve:\n"
        f"  project_name: {project.GetName()}\n"
        f"  timeline_name: {MASTER}\n",
        encoding="utf-8")
    return project, folder


def _live_master(project):
    return next(t for t in project.timelines if t.GetName() == MASTER)


def _v3_opacity(project):
    tracks = reel_read.read_tracks(_live_master(project))
    v3 = next(t for t in tracks
              if t["type"] == "video" and int(t["index"]) == 3)
    return [c["transform"]["Opacity"] for c in v3["clips"]]


def test_a_one_op_master_touchup_changes_only_the_touched_item(
        tmp_path, monkeypatch):
    """A 1-op master touch-up changes only that item's read-back, is
    reversed by the undo journal, and is carried by editor_edit_carry.

    The defect this names: a master-timeline instruction that re-ran
    the whole pipeline (changing things the instruction did not touch)
    or wrote ad hoc through resolve-axi (no ledger row, no undo journal
    entry).  The touch-up stages a copy, writes in place, verifies by
    re-reading, and promotes - and the undo journal reverses it.
    """
    from library.tools import master_touchup, plan_provenance
    from library.tools import undo_journal as uj

    project, folder = _master_project(tmp_path, monkeypatch)

    spec = {"edits": [
        {"op": "set_properties", "row": "V3", "item": 0,
         "properties": {"Opacity": 50}}]}
    receipt = master_touchup.apply_master_touchup(
        str(folder), spec, connect=lambda _name: project)

    assert receipt["master"] == MASTER
    assert receipt["gate"]["class"] == "composed"

    # Only the touched item's read-back changed.
    assert _v3_opacity(project) == [50]

    # The next build's editor_edit_carry carries it.
    review_dir = str(folder / "pipeline_output" / "review")
    carried = plan_provenance.carried_editor_edits(review_dir, MASTER)
    assert len(carried) == 1
    assert carried[0]["kind"] == "transform"
    assert carried[0]["field"] == "transform.Opacity"
    assert carried[0]["after"] == 50
    assert carried[0]["source"].startswith("ren_touch:")

    # The undo journal reverses it in place.
    entry = uj.read_entry(str(folder), receipt["journal"])
    assert entry["status"] == "applied"
    live = _live_master(project)
    pool = media_pool(live)
    by_path = {m.GetClipProperty("File Path"): m
               for m in pool.GetRootFolder().GetClipList()}
    from library.tools.transform_write_log import write_scope
    with write_scope(project="lab", project_folder=str(folder),
                     timeline_name=live.GetName(),
                     timeline_id=live.GetUniqueId(),
                     run_id="master-touchup-test"):
        undo_receipt = uj.undo_in_place(
            timeline=live, media_pool=pool, entry=entry,
            entry_root=uj.entry_dir(str(folder), entry["id"]),
            reference=duplicate(live, name="reference"),
            rederiver=master_touchup._reel_touchup._NullRederiver("test"),
            resolve_media=by_path.get,
            work_dir=str(tmp_path / "undo"),
            project_folder=str(folder))
    assert undo_receipt["verified"] is True
    assert _v3_opacity(project) == [100.0]


def test_a_set_properties_on_an_uncarryable_item_refuses_by_name(
        tmp_path, monkeypatch):
    """An in-place write on an item that plays the same source as another
    item on its row refuses: the carry ledger states edits in source
    terms, and the next build could not know which item to apply it to.

    The defect this names: the write landing but the carry silently
    superseding it, so the next rebuild drops the change with no
    refusal and no reader.
    """
    from library.tools import master_touchup

    _project, folder = _master_project(tmp_path, monkeypatch)

    spec = {"edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"Opacity": 50}}]}
    with pytest.raises(master_touchup.TouchupRefused) as refusal:
        master_touchup.apply_master_touchup(
            str(folder), spec, connect=lambda _name: _project)
    message = str(refusal.value)
    assert "carry ledger cannot address" in message
    assert "3 items" in message


def test_a_played_length_change_on_the_master_refuses_with_rebuild(
        tmp_path, monkeypatch):
    """A retime on the master refuses: it desyncs every reel cut from the
    master, and the master's comps are the pipeline's, not a recorded
    manifest's.

    The defect this names: a length-changing edit reaching the master
    timeline without re-deriving its comps, rendering wrong on most of
    the clip's frames while looking right - or silently desyncing every
    reel cut from it.
    """
    from library.tools import master_touchup

    _project, folder = _master_project(tmp_path, monkeypatch)

    spec = {"edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    with pytest.raises(master_touchup.TouchupRefused) as refusal:
        master_touchup.apply_master_touchup(
            str(folder), spec, connect=lambda _name: _project)
    message = str(refusal.value)
    assert "composed_with_rederivation" in message
    assert "rebuild" in message


def test_the_master_timeline_name_comes_from_the_projects_declaration(
        tmp_path):
    """The master touch-up edits the timeline the project declares, not
    a reel number.

    The defect this names: a touch-up that guessed the master's name
    (or required a reel number) would edit the wrong timeline or refuse
    the only timeline that has no reel number.
    """
    from library.tools import master_touchup

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "resolve:\n  project_name: Fixture Project\n"
        "  timeline_name: Rough Cut\n",
        encoding="utf-8")
    assert master_touchup.master_timeline_name(str(folder)) == "Rough Cut"
