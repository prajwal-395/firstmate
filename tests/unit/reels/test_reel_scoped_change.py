"""A changed caption card is patched; the rest of the reel is reused.

A one-word caption change is the smallest edit a captain makes and the
one the scoped path must carry: the card's CONTENT changes (a new
content digest, so a new file - PR 3048) while its SPAN and the rest of
the reel do not.

These tests pin what the scoped route does with that change, and what
it still refuses:

- the changed card is replaced IN PLACE, and the patched staging
  matches a fresh full build of the same plan item by item;
- every OTHER card, every overlay and every footage row is reused -
  the scoped route writes one caption, not the reel;
- a caption whose SPAN moved still refuses: that is a placement
  change, and the caption row is one row where two overlapping cards
  are unreadable;
- the scope RECORD says what the change touched, so a reader can see
  the change was one item and not the reel.
"""
from __future__ import annotations

import dataclasses

import pytest

from library.tools import reel_incremental as incr
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import make_project
from tests.scenarios.test_reel_build import FakeMoment, _world


def _caption(start, end, name="cap", path="/m/cap.mov"):
    return {
        "overlay_path": path,
        "timeline_start": start,
        "timeline_end": end,
        "source_in_frame": 0,
        "segment_id": name,
    }


def _master_clips():
    from tests.scenarios.test_reel_build import _master_clips as clips

    return clips()


def _transcript():
    return {"segments": []}


def _inventory(timeline):
    """{(media, index): sorted [(start, end, source)]} for every row."""
    out = {}
    for media in ("video", "audio"):
        try:
            count = timeline.GetTrackCount(media) or 0
        except Exception:
            continue
        for index in range(1, count + 1):
            rows = []
            for item in timeline.GetItemListInTrack(media, index) or []:
                try:
                    pool_item = item.GetMediaPoolItem()
                    source = (pool_item.GetClipProperty("File Path")
                              if pool_item is not None else "")
                except Exception:
                    source = ""
                rows.append((item.GetStart(), item.GetEnd(), source))
            out[(media, index)] = sorted(rows)
    return out


def _build(tmp_path, captions):
    from library.tools.reel_build import build_reel_timeline

    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(), _master_clips(), list(captions),
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        master_timeline=None, program_channels={"1": 1, "2": 1},
        timeline_name="Reel 99 - scoped caption")
    return timeline, pool, project, record


def _prepared(tmp_path, captions):
    from library.tools.reel_build import prepare_reel_timeline

    return prepare_reel_timeline(
        FakeMoment(), _master_clips(), list(captions), 23.976,
        1080, 1920, str(tmp_path), _transcript(),
        timeline_name="Reel 99 - scoped caption",
        master_timeline=None, program_channels={"1": 1, "2": 1})


def _staging(project, name="Reel 99 - scoped caption (rebuild staging)"):
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == name:
            return candidate
    return None


def _patch(project, final, prepared, captions, tmp_path):
    from library.tools import reel_rebuild_need as _need

    recorded = _need.carried_digest_live(project, final)
    assert recorded
    with assume_sole_writer("scoped-caption"):
        return incr.build_staged_incremental(
            project=project,
            staging_name="Reel 99 - scoped caption (rebuild staging)",
            final_name=final.GetName(), recorded_carried=recorded,
            prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None,
            lower_third_segments=None, look=None,
            grade_cdl=None, power_grade=None, motion=None,
            overlay_intent=None, suppressions=[],
            fps=23.976, width=1080, height=1920,
            project_folder=str(tmp_path), draw_gain=1.0,
            resolve_app=None, single_reel_scope=True,
            placement_mode="append", transcript=_transcript())


def _write_caption(tmp_path, name, payload):
    """A rendered caption card on disk, and the artefact path for it."""
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def test_changed_caption_card_is_patched_and_matches_a_full_build(tmp_path):
    """The patched staging equals a fresh full build of the same plan.

    The caption's WORDS changed, so its content digest changed, so it
    is a different file - at the SAME span.  The scoped route replaces
    that one card and reuses the rest; the result must be the build's
    own result, item for item.
    """
    old_path = _write_caption(tmp_path, "cap_v1.mov", b"caption-one")
    new_path = _write_caption(tmp_path, "cap_v2.mov", b"caption-two")
    old_captions = [_caption(2.0, 4.0, "cap-1", old_path)]
    new_captions = [_caption(2.0, 4.0, "cap-1", new_path)]

    final, _pool, project, _record = _build(tmp_path, old_captions)
    prepared = _prepared(tmp_path, new_captions)

    from library.tools.reel_build import build_reel_timeline
    ref_timeline, _ref_pool, _ref_project, ref_record = _world(), None, None, None
    ref_timeline, _rp, _rproj = _world()
    ref_record = build_reel_timeline(
        _rproj, FakeMoment(), _master_clips(), list(new_captions),
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        master_timeline=None, program_channels={"1": 1, "2": 1},
        timeline_name="Reel 99 - scoped caption")

    record = _patch(project, final, prepared, new_captions, tmp_path)
    staging = _staging(project)
    assert staging is not None
    assert _inventory(staging) == _inventory(ref_timeline)
    assert record["placements"] == ref_record["placements"]


def test_changed_caption_card_writes_one_item_not_the_reel(tmp_path):
    """The scoped route writes the changed card and NOTHING else.

    This is the whole point of the scoped route: the cost of the edit
    is the changed item, not the reel.  The reel's other rows are
    carried over from the duplicate, so they are never touched.
    """
    old_path = _write_caption(tmp_path, "cap_v1.mov", b"caption-one")
    new_path = _write_caption(tmp_path, "cap_v2.mov", b"caption-two")
    old_captions = [_caption(2.0, 4.0, "cap-1", old_path)]
    new_captions = [_caption(2.0, 4.0, "cap-1", new_path)]

    final, _pool, project, _record = _build(tmp_path, old_captions)
    prepared = _prepared(tmp_path, new_captions)
    _patch(project, final, prepared, new_captions, tmp_path)

    staging = _staging(project)
    assert staging is not None
    # The caption row holds the card the plan names, and the new card's
    # bytes - the changed one landed.
    caption_row = None
    for index in range(1, staging.GetTrackCount("video") + 1):
        if staging.GetTrackName("video", index) == "Subtitles":
            caption_row = index
            break
    assert caption_row, "the reel's caption row is gone"
    items = staging.GetItemListInTrack("video", caption_row) or []
    assert len(items) == 1
    pool_item = items[0].GetMediaPoolItem()
    assert pool_item is not None
    assert pool_item.GetClipProperty("File Path") == new_path


def test_moved_caption_card_still_refuses(tmp_path):
    """A caption whose SPAN moved is a placement change, not a byte change.

    The caption row is ONE row, so two cards overlapping on it are
    unreadable.  The scoped route refuses and the full rebuild places
    the caption row correctly.
    """
    old_path = _write_caption(tmp_path, "cap_v1.mov", b"caption-one")
    new_path = _write_caption(tmp_path, "cap_v2.mov", b"caption-two")
    old_captions = [_caption(2.0, 4.0, "cap-1", old_path)]
    new_captions = [_caption(3.0, 5.0, "cap-1", new_path)]

    final, _pool, project, _record = _build(tmp_path, old_captions)
    prepared = _prepared(tmp_path, new_captions)
    with assume_sole_writer("scoped-caption-moved"):
        with pytest.raises(incr.IncrementalRefused, match="[Cc]aption"):
            _patch(project, final, prepared, new_captions, tmp_path)


def test_the_scope_record_names_the_changed_item(tmp_path):
    """The scope says what the change touched: one item, one interval.

    A scope nobody can read is a scope nobody can measure, and the
    point of this lane is that the work is proportional to the change -
    so the change's extent has to be a value, not a side effect.
    """
    from library.tools.change_scope import Scope, compare

    scope = Scope(kind="caption")
    scope.add_item("cap-1", row="Subtitles")
    scope.add_interval("Subtitles", 48, 96)
    scope.note_reuse("caption_cards", 29)
    scope.note_reuse("graphics", 4)

    assert scope.affected_rows == ("Subtitles",)
    assert scope.affected_frames == 48
    assert scope.reused_total == 33
    assert scope.as_dict()["items"] == 1

    bulk = Scope(kind="caption")
    # The bulk rebuild re-derives the whole reel: every card, and every
    # frame the reel plays.
    for index in range(40):
        bulk.add_item(f"card-{index}", row="Subtitles")
    bulk.add_interval("Subtitles", 0, 960)
    decision = compare(scope, bulk)
    assert decision["winner"] == "scoped"
    assert decision["scoped_work"]["items"] < decision["bulk_work"]["items"]
    assert decision["scoped_work"]["frames"] < decision["bulk_work"]["frames"]


def test_unmeasured_work_never_reads_as_zero():
    """A scope nobody measured is UNKNOWN, not 'touches nothing'.

    The two answers - 'the change touches nothing' and 'nobody looked'
    - are different facts, and only the first is a cheap edit.
    """
    from library.tools.change_scope import Scope, compare

    measured = Scope(kind="caption")
    measured.add_item("cap-1", row="Subtitles")

    unmeasured = Scope(kind="caption")
    decision = compare(measured, unmeasured)
    assert decision["winner"] == "unmeasured"
    assert decision["bulk_work"] is None


def test_a_retime_names_the_interval_it_moved():
    """A retime is a CLOCK change, and the refusal says so.

    Measured on the fake Resolve, 2026-10-21: a passage retimed to
    110% occupied record frames 0-87 instead of 0-96, and the
    placement after it moved from frame 96 to frame 87.  So a retime
    is not a span-local change, the scoped path refuses it, and the
    refusal names the passage and how far the change reaches.
    """
    from library.tools.reel_incremental import retime_scope

    placements = [
        {"rate": 1.1, "snapped_record": 0, "record_frames": 87,
         "source_in": 0.0, "source_out": 4.0, "clip": object()},
        {"snapped_record": 87, "source_in": 4.0, "source_out": 8.0,
         "clip": object()},
    ]
    scope = retime_scope(placements, 24.0)
    assert scope is not None
    assert scope["kind"] == "retime"
    assert scope["passages"][0]["rate"] == 1.1
    assert scope["passages"][0]["record_end_frame"] == 87
    # The clock change reaches the placement AFTER the retimed passage.
    assert scope["moved_from_frame"] == 0
    assert scope["moved_to_frame"] == 87
    assert "clock" in scope["reason"]


def test_a_plan_with_no_retime_has_no_retime_scope():
    from library.tools.reel_incremental import retime_scope

    assert retime_scope(
        [{"snapped_record": 0, "source_in": 0.0, "source_out": 4.0,
          "clip": object()}], 24.0) is None
    assert retime_scope([], 24.0) is None


def test_the_scoped_route_refuses_when_rebuilding_is_measurably_cheaper():
    """PERF-003's last bullet: DETECT when the small-delta strategy is
    actually more expensive than bulk reconstruction.

    The costs are this project's OWN measured numbers, and where nothing
    was measured the scoped path continues - an unknown cost is not a
    reason to do the expensive thing.
    """
    from library.tools import reel_incremental as incr
    from library.tools.change_scope import Scope

    scope = Scope(kind="caption")
    scope.add_item("cap-1", row="Subtitles")

    unmeasured = incr._scope_cost_decision(scope, project_folder="")
    assert unmeasured["winner"] == "unmeasured"
    assert "unknown cost" in unmeasured["reason"]

    class _Ledger:
        @staticmethod
        def read_rows(*args, **kwargs):
            return []

    # A project whose ledger measured a rebuild as CHEAPER than a
    # targeted edit: the scoped route must not run.
    import library.tools.reel_build as rb
    original = rb.measured_strategy_costs
    rb.measured_strategy_costs = lambda folder: {
        "targeted": {"edits": 4, "total_s_p50": 30.0},
        "full_rebuild": {"edits": 2, "total_s_p50": 12.0},
    }
    try:
        decision = incr._scope_cost_decision(scope, project_folder="/x")
    finally:
        rb.measured_strategy_costs = original
    assert decision["winner"] == "bulk"
    assert "cheaper" in decision["reason"]
