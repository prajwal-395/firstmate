"""The touchup gate: what routes, what pays, what refuses.

Drives `reel_touchup.qualify` and the composed path end to end against the
fake Resolve in `tests/composed_edit_harness.py`; nothing reaches a real
project. What each group pins: docs/evidence/reel_touchup.md.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from tests.composed_edit_harness import (  # noqa: E402
    FakeComp,
    FakeTool,
    build_reel,
    covering_window,
    duplicate,
    frames_of,
    media_pool,
    pool_clip,
)
from tests.composed_edit_harness import item as make_item  # noqa: E402
from tests.resolve_double import FakeProject  # noqa: E402


def _tracks(timeline):
    return reel_read.read_tracks(timeline)


def _v4_first_record(timeline):
    tracks = _tracks(timeline)
    for track in tracks:
        if track["type"] == "video" and int(track["index"]) == 4:
            return int(track["clips"][0]["record_in"])
    raise AssertionError("the fixture stopped carrying V4")


def test_live_touchup_read_holds_lease_and_makes_reel_current(monkeypatch):
    """A touchup's Resolve handshake and transform read are both guarded."""
    from library.tools import (
        project_registry, reel_build, reel_read, resolve_lock,
    )

    project = object()
    timeline = SimpleNamespace(GetName=lambda: "Reel 22 - moment")
    events = []
    state = {"lease": False, "excursion": False}

    @contextmanager
    def lease(purpose, *, exclusive):
        assert purpose == "read touchup Reel 22 - moment"
        assert exclusive is True
        state["lease"] = True
        events.append("lease-enter")
        try:
            yield
        finally:
            state["lease"] = False
            events.append("lease-exit")

    @contextmanager
    def excursion(actual_project, actual_timeline, purpose):
        assert state["lease"] is True
        assert actual_project is project
        assert actual_timeline is timeline
        assert purpose == "read touchup Reel 22 - moment"
        state["excursion"] = True
        events.append("cursor-enter")
        try:
            yield
        finally:
            state["excursion"] = False
            events.append("cursor-exit")

    def connect(name):
        assert state["lease"] is True
        assert name == "Podcast (field test)"
        events.append("connect")
        return project

    def read_tracks(actual_timeline, *, resolve_project):
        assert state["lease"] is True
        assert state["excursion"] is True
        assert actual_timeline is timeline
        assert resolve_project is project
        events.append("read")
        return [{"name": "Akshita"}]

    monkeypatch.setattr(
        project_registry, "get_project",
        lambda _folder: SimpleNamespace(
            resolve=SimpleNamespace(project_name="Podcast (field test)")))
    monkeypatch.setattr(reel_build, "_connect_resolve_project", connect)
    monkeypatch.setattr(
        reel_build, "timelines_to_replace",
        lambda _project, names: [timeline]
        if names == {"Reel 22 - moment"} else [])
    monkeypatch.setattr(resolve_lock, "resolve_lease", lease)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", excursion)
    monkeypatch.setattr(reel_read, "read_tracks", read_tracks)

    assert tu._live_tracks_for_reel("/project", 22,
                                    "Reel 22 - moment") == [
                                        {"name": "Akshita"}]
    assert events == ["lease-enter", "connect", "cursor-enter", "read",
                      "cursor-exit", "lease-exit"]


# ── The composed class ───────────────────────────────────────────────


def test_a_caption_whose_source_trim_is_unknown_is_never_re_placed(tmp_path):
    """Every edit that would re-place a Subtitles item whose `left_offset`
    was unreadable (or an add that declares none) refuses by name."""
    rows = [
        (0, {"op": "move", "row": "V4", "item": 0, "to_record": 1300},
         "was unreadable"),
        (0, {"op": "retime", "row": "V1", "item": 0, "duration": 480},
         "would move a subtitle item whose source trim"),
        (1, {"op": "remove_overlay", "row": "V4", "item": 0},
         r"would re-place caption V4\[1\]"),
        (None, {"op": "add_overlay", "row": "V4", "media": "/lab/new.mov",
                "record": 1300, "duration": 40, "properties": {}},
         "without an explicit `left_offset`"),
    ]
    timeline, _pool, _media = build_reel(tmp_path)
    for unreadable, edit, match in rows:
        tracks = _tracks(timeline)
        subtitles = next(track for track in tracks if track["index"] == 4)
        subtitles["name"] = "Subtitles"
        if unreadable is not None:
            subtitles["clips"][unreadable]["left_offset"] = None
        with pytest.raises(tu.TouchupRefused, match=match):
            tu.qualify(tracks, {"reel": 1, "edits": [edit]})


def test_a_known_source_trim_rides_into_the_insertion(tmp_path):
    """A declared `left_offset` wins; a swap with none keeps the live
    trim, so it never exposes the render's 12-frame head handle."""
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = _tracks(timeline)
    next(track for track in tracks if track["index"] == 4)["name"] = "Subtitles"
    qualification = tu.qualify(tracks, {"reel": 1, "edits": [{
        "op": "add_overlay", "row": "V4", "media": "/lab/new.mov",
        "record": 1300, "duration": 40, "left_offset": 12,
        "properties": {},
    }]})
    assert qualification.insertions[0].left_offset == 12

    timeline.rows["V4"][0]._left_offset = 12
    overlay = tmp_path / "caption-with-head-handle.mov"
    overlay.write_bytes(b"fake-rendered-overlay")
    for declared, expected in ((None, 12), (0, 0)):
        edit = {"op": "swap_pixels", "row": "V4", "item": 0,
                "media": str(overlay)}
        if declared is not None:
            edit["left_offset"] = declared
        qualification = tu.qualify(_tracks(timeline),
                                   {"reel": 1, "edits": [edit]})
        assert qualification.insertions[0].left_offset == expected


# ── Refusals ─────────────────────────────────────────────────────────


def test_each_unclassifiable_spec_refuses_by_name(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    first = _v4_first_record(timeline)
    card = {"op": "add_overlay", "row": "V4", "media": "/lab/card.mov",
            "duration": 40, "properties": {}}
    rows = [
        ([{"op": "move", "row": row, "item": 0, "to_row": "V4",
           "to_record": 1300}], "continuous program")
        for row in ("V1", "V2", "A1")
    ] + [
        ([{"op": "move", "row": "V4", "item": 0, "to_row": "V4",
           "to_record": first + 100}], "collides"),
        ([{"op": "add_overlay", "row": "V4", "media": "/lab/card.mov",
           "record": 1300, "duration": 40}], "declares no `properties`"),
        ([{"op": "swap_pixels", "row": "V1", "item": 0,
           "media": "/lab/opener.mov"}], "drawing"),
        # Each add is free against the live read; together they collide.
        ([dict(card, record=1300), dict(card, record=1310)],
         "overlaps on V4"),
        # Retime V4[0] ripples a SHIFT onto V4[2]; removing V4[1]
        # rewrites V4[2] too - a double place.
        ([{"op": "retime", "row": "V4", "item": 0, "duration": 50},
          {"op": "remove_overlay", "row": "V4", "item": 1}], "same item"),
    ]
    for edits, needle in rows:
        with pytest.raises(tu.TouchupRefused) as refusal:
            tu.qualify(_tracks(timeline), {"reel": 1, "edits": edits})
        assert needle in str(refusal.value), edits
    # Fail closed: an unreadable comp graph is not evidence of an empty one.
    timeline.rows["V4"][0].comps = [object()]  # answers no getter
    with pytest.raises(tu.TouchupRefused, match="drawing"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "swap_pixels", "row": "V4", "item": 0,
             "media": "/lab/card.mov"}]})


# ── The manifest check, for the length-changing class ────────────────


def _manifest_for(tracks, per_clip_labels):
    manifest_tracks = {}
    for track in tracks:
        row = ("V" if str(track["type"]).lower().startswith("v")
               else "A") + str(int(track["index"]))
        if not row.startswith("V"):
            continue
        manifest_tracks[row] = {
            "clips": [{"source_file": c["source_file"],
                       "label": f"{row}-{i}"}
                      for i, c in enumerate(track["clips"])]}
    return {"tracks": manifest_tracks,
            "fusion_effects": {"per_clip": {
                label: {} for label in per_clip_labels}}}


def test_diverged_manifest_refuses_and_names_rebuild(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = _tracks(timeline)
    manifest = _manifest_for(tracks, [])
    manifest["tracks"]["V1"]["clips"] = manifest["tracks"]["V1"][
        "clips"][:1]
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.check_manifest_matches(manifest, tracks)
    message = str(refusal.value)
    assert "V1" in message
    assert "build-reels" in message


# ── The null rederiver is not a bypass ───────────────────────────────


# ── The composed class through the module, verified by re-read ──────


def _null(tmp_path):
    return tu._NullRederiver("test: nothing to re-derive")


def test_move_runs_through_composed_edit_and_verifies(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "move", "row": "V4", "item": 0, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool,
        changes=qualification.changes, comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    assert receipt.verified["landed"] == 1
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    starts = sorted(c["record_in"] for c in v4["clips"])
    assert 1300 in starts
    assert len(starts) == 3


def test_length_change_without_a_generator_still_refuses(tmp_path):
    """The gate's class must agree with the module's refusal."""
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert (qualification.gate_class
            == tu.COMPOSED_WITH_REDERIVATION)
    with pytest.raises(ce.CompRederivationUnreachable):
        ce.apply_composed_edit(
            timeline=timeline, media_pool=pool,
            changes=qualification.changes,
            comp_dir=str(tmp_path / "c"),
            withheld_dir=str(tmp_path / "w"), rederiver=None)
    assert timeline.delete_calls == []


def _pool_holding(clips):
    """A media pool whose root bin holds ``clips`` - the one lookup the
    touchup needs is clips by full path."""
    pool = FakeProject().GetMediaPool()
    for clip in clips:
        pool.GetRootFolder().add_clip(clip)
    return pool


def _mock_pre_delete_patch(monkeypatch, timeline):
    """Keep these composition tests focused on rekeying around a patch."""
    from library.tools import edit_patch

    expected_timeline = timeline

    def apply_live_patch(*, timeline, operations, **kwargs):
        assert timeline is expected_timeline
        assert kwargs["capability"] == "reel.touchup"
        assert "timeline_structure" in kwargs["conflict_domains"]
        for operation in operations:
            assert operation["op"] == "clip.delete"
            matches = [item for row in reel_read.live_items(timeline)
                       for item in row["items"]
                       if item.GetUniqueId() == operation["unique_id"]]
            assert len(matches) == 1
            timeline.DeleteClips([matches[0]], False)
        return {"status": "committed", "generation": 1}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def test_remove_plus_move_on_one_row_rekeys_and_verifies(tmp_path,
                                                         monkeypatch):
    """Indexes planned before the pre-delete are re-seated after it."""
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "move", "row": "V4", "item": 2, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    _mock_pre_delete_patch(monkeypatch, timeline)
    tu._pre_delete_removed(object(), timeline, qualification.removals,
                           "test-journal")
    changes = tu._rekey_changes(_tracks(timeline), qualification)
    # V4[2]'s move was planned at index 2; the pre-delete of V4[1]
    # re-seated it to 1, and the rekey says so.
    moved = [c for c in changes if c.record_frame == 1300]
    assert len(moved) == 1 and moved[0].item_index == 1
    ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    assert len(v4["clips"]) == 2
    assert 1300 in [c["record_in"] for c in v4["clips"]]


# ── Structural exclusions: named, before anything is staged ───────────


def test_add_to_a_comp_row_refuses_and_names_rebuild(tmp_path):
    """A new item on V1/V2 would land with no treatment comp.

    Every clip on a comp-bearing row carries one, and a newly placed
    item has no manifest spec for the pass to key one to - so the
    null path would sail it through comp-less beside treated
    neighbours, and it would render looking like a choice. Excluded
    by name: the rebuild plans the new clip with its treatment.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    for row in ("V1", "V2"):
        with pytest.raises(tu.TouchupRefused) as refusal:
            tu.qualify(_tracks(timeline),
                       {"reel": 1, "edits": [
                           {"op": "add_overlay", "row": row,
                            "media": "/lab/card.mov", "record": 2000,
                            "duration": 24, "properties": {}}]})
        message = str(refusal.value)
        assert row in message
        assert "build-reels" in message
    # The overlay rows stay servable: the refusal is about the row,
    # not about adds.
    qualified = tu.qualify(_tracks(timeline),
                           {"reel": 1, "edits": [
                               {"op": "add_overlay", "row": "V4",
                                "media": "/lab/card.mov", "record": 1300,
                                "duration": 40, "properties": {}}]})
    assert qualified.gate_class == tu.COMPOSED


def test_cross_row_move_refuses_and_names_remove_add(tmp_path):
    """The composition addresses a delete by (row, position).

    A move keyed by its target row but its source position would
    capture whatever sits at that position on the target row -
    deleting a bystander while duplicating the moved item. So the
    gate refuses the cross-row move before anything is staged, and
    names the two edits that state the carrying explicitly.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "move", "row": "V4", "item": 0,
                        "to_row": "V3", "to_record": 1300}]})
    message = str(refusal.value)
    assert "across rows" in message
    assert "remove_overlay" in message and "add_overlay" in message
    # Same-row repositioning - the move the gate serves - is untouched.
    qualified = tu.qualify(_tracks(timeline),
                           {"reel": 1, "edits": [
                               {"op": "move", "row": "V4", "item": 0,
                                "to_row": "V4", "to_record": 1300}]})
    assert qualified.gate_class == tu.COMPOSED


# ── Every producer of a refused comp, accounted for ──────────────────


def test_swap_on_an_empty_auto_comp_still_qualifies(tmp_path):
    """Resolve gives every plain item its own empty composition.

    That deterministically-produced comp draws nothing, so it is not
    a treatment and the swap refusal written against builder comps
    must not catch it - a gate that failed this correct output would
    be no better than one that cannot fail (AGENTS.md 10.4).
    """

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "MediaOut1": FakeTool("MediaOut", {}),
        "AudioDisplay1": FakeTool("AudioDisplay", {})})]
    assert timeline.rows["V4"][0].GetFusionCompCount() == 1

    qualification = tu.qualify(_tracks(timeline),
                               {"reel": 1, "edits": [
                                   {"op": "swap_pixels", "row": "V4",
                                    "item": 0,
                                    "media": "/lab/card.mov"}]})
    assert qualification.gate_class == tu.COMPOSED


def test_graded_swap_refuses_at_resolve(tmp_path):
    """An Insertion cannot take a grade from an item being deleted.

    The V3 overlay carries no comp, so it passes qualify - and the
    resolve then refuses its 8-node grade by name, before anything
    is staged. The legitimate producer - Resolve's own default
    single node - is what every qualifying swap test above rides on.
    """

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].nodes = 8  # graded, but no drawing comp
    pixels = tmp_path / "graded-swap.mov"
    pixels.write_bytes(b"fake-rendered-overlay")
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": str(pixels)}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    stub = _pool_holding([pool_clip(str(pixels), frames=200)])
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu._resolve_insertions(stub, timeline,
                               qualification.insertions)
    assert "colour grade" in str(refusal.value)


def _overlay_swap(tmp_path):
    """A swap onto V4's first item, and the frames it needs."""
    timeline, _pool, _media = build_reel(tmp_path)
    overlay = tmp_path / "tv_frame_overlay.mov"
    overlay.write_bytes(b"fake-rendered-overlay")
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": str(overlay)}]}
    pending = tu.qualify(_tracks(timeline), spec).insertions
    return overlay, pending, pending[0].left_offset + pending[0].duration


def test_swap_pixels_names_the_replacement_file(tmp_path):

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0]._name = "logo_bulb_23976.mov"
    replacement = tmp_path / "logo_bulb_lines_23976.mov"
    replacement.write_bytes(b"replacement pixels")
    qualification = tu.qualify(
        _tracks(timeline),
        {"reel": 1, "edits": [{
            "op": "swap_pixels", "row": "V4", "item": 0,
            "media": str(replacement),
        }]})

    insertion, = tu._resolve_insertions(
        _pool_holding([pool_clip(str(replacement))]), timeline,
        qualification.insertions)

    assert insertion.name == "logo_bulb_lines_23976.mov"


def test_source_length_check_rereads_a_stale_pool_and_refuses_a_short_file(
        tmp_path):
    """The geo-podcast fit-picture run, 2026-09-25: the TV overlay was
    imported at 969 frames for a preview, then re-rendered at 2307 under
    the same name - and 26 reels refused on the pool's stale 969."""

    overlay, pending, needed = _overlay_swap(tmp_path)
    stale = pool_clip(str(overlay), frames=needed - 1,
                      on_disk=needed + 100)
    tu.check_source_lengths(_pool_holding([stale]), pending)
    assert frames_of(stale) == needed + 100

    short = pool_clip(str(overlay), frames=needed - 1)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.check_source_lengths(_pool_holding([short]), pending)
    assert f"which holds {needed - 1}f" in str(refusal.value)


# ── Grades ride from the approved timeline ───────────────────────────


class _Rederiver(ce.CompRederiver):
    """Re-keys covering comps for length-changed comp items, as the
    builder's pass does (it reads played lengths off the live items)."""

    def __init__(self, timeline):
        self.timeline = timeline

    def reachable_reason(self, changes):
        return None

    def rederive(self, changes):
        for change in changes:
            if not (change.played_length_changes and change.comp_count):
                continue
            row = self.timeline.rows[change.row]
            item = next(i for i in row
                        if i.GetStart() == change.record_frame)
            item.comps = make_item(
                item.GetMediaPoolItem(), item.GetStart(), item.GetDuration(), item.GetLeftOffset(),
                comp_windows=[covering_window(
                    item.GetDuration(), item.GetLeftOffset(),
                    frames_of(item.GetMediaPoolItem()))]).comps
        return {"ran": True, "ok": True}

    def expects_comp(self, row, record_frame):
        return False


def _staged_pair(tmp_path):
    """Approved + staging timelines with a pool bound to the staging.

    Mirrors `_edit_staged`: the plan qualifies off the staging read,
    the grades resolve off the approved reel, the composition runs on
    the staging copy.
    """
    approved, _pool, media = build_reel(tmp_path)
    staged = duplicate(approved)
    return approved, staged, media_pool(staged), media


def test_grade_sources_map_by_pre_edit_span(tmp_path, monkeypatch):
    approved, staged, _pool, _media = _staged_pair(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(staged), spec)
    changes = tu._rekey_changes(_tracks(staged), qualification)
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    head = next(c for c in changes
                if (c.row, c.item_index) == ("V1", 0))
    assert grades[("V1", 0)] is approved.rows["V1"][0]
    assert head.previous_record == approved.rows["V1"][0].GetStart()

    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "move", "row": "V4", "item": 2, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(staged), spec)
    _mock_pre_delete_patch(monkeypatch, staged)
    tu._pre_delete_removed(object(), staged, qualification.removals,
                           "test-journal")
    changes = tu._rekey_changes(_tracks(staged), qualification)
    # The rekey re-points the move at its re-seated change object.
    assert qualification.moves[0]["change"] in changes
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    moved = next(c for c in changes if c.record_frame == 1300)
    assert moved.item_index == 1  # re-seated past the pre-delete
    assert grades[(moved.row, moved.item_index)] is \
        approved.rows["V4"][2]


def test_retime_keeps_its_grade_through_the_qualified_plan(tmp_path,
                                                           monkeypatch):
    """A graded retime without the carry renders de-graded: 8 nodes in,
    one node out. The wiring carries each re-placed item's grade from
    the approved reel and the restore judges it by read-back."""
    approved, staged, pool, _media = _staged_pair(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(staged), spec)
    assert qualification.gate_class == tu.COMPOSED_WITH_REDERIVATION
    _mock_pre_delete_patch(monkeypatch, staged)
    tu._pre_delete_removed(object(), staged, qualification.removals,
                           "test-journal")
    changes = tu._rekey_changes(_tracks(staged), qualification)
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    receipt = ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"), withheld_dir=str(tmp_path / "w"),
        rederiver=_Rederiver(staged), grade_sources=grades)

    head = next(i for i in staged.rows["V1"] if i.GetStart() == 590)
    assert head.GetDuration() == 492
    assert head.GetNumNodes() == 8, "the retimed head lost its grade"
    assert head.GetProperty("ZoomX") == 2.307
    assert receipt.rederived["verified"]["checked"] == 1
    # The approved reel stands untouched behind the staging.
    assert approved.rows["V1"][0].GetDuration() == 479
    assert approved.rows["V1"][0].GetNumNodes() == 8
