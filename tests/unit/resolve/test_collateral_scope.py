"""PLAN-006: the collateral a request may cause is modelled BEFORE the write.

The defects these tests name were open before the collateral scope
existed:

- a request that names the partner it links to was read as if it named
  only one item, so the partner's link state read as an UNAUTHORIZED
  change on the read-back;
- a ripple that would move the SPEECH and the OVERLAYS was invisible
  until after it had written, and the engine refused the primitive
  outright instead of naming the rows it would move;
- a caption card left covering a picture that is gone was not a
  conflict anybody could see before the write;
- a request's CONSEQUENCES reaching work the requester required
  unchanged was invisible: the feasibility check asked whether the
  request WROTE to that work, and a ripple reaches it without writing
  to it;
- the engine had no way to prefer the variant of a request that touches
  only the authorized media or interval.

Every test here names one of them.
"""
from __future__ import annotations

from library.tools import collateral_scope as scope_module
from library.tools import compound_transaction

# ── helpers ──────────────────────────────────────────────────────────


def _clip(unique_id, record_in=0, record_out=48, **extra):
    clip = {
        "unique_id": unique_id,
        "name": unique_id,
        "track_type": "video",
        "track_index": 1,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": record_in,
        "source_out_frame": record_out,
        "speed": 100,
        "enabled": True,
        "transform": {"ZoomX": 1.0, "Pan": 0.0, "Volume": 0.0},
        "color": {"cdl": {}, "color_group": "", "luts": []},
        "fusion": {"comp_count": 0, "comp_names": []},
        "keyframes": {},
        "markers": [],
        "linked_unique_ids": [],
        "media_pool_item_id": f"pool-{unique_id}",
        "source_file": f"/media/{unique_id}.mov",
    }
    clip.update(extra)
    return clip


def _snapshot(*, video=(), audio=(), captions=(), end_frame=192):
    tracks = []
    if video:
        tracks.append({"type": "video", "index": 1, "name": "V1",
                       "clips": list(video)})
    if audio:
        tracks.append({"type": "audio", "index": 1, "name": "A1",
                       "clips": list(audio)})
    if captions:
        tracks.append({"type": "subtitle", "index": 1, "name": "Subtitles",
                       "clips": list(captions)})
    return {
        "timeline": "Reel 01",
        "project": "Podcast",
        "fps": 24.0,
        "start_frame": 0,
        "end_frame": end_frame,
        "mode": "quick",
        "tracks": tracks,
        "markers": {"timeline": [], "notes": []},
    }


def _scope(operations, before, **kwargs):
    return scope_module.allowed_collateral(
        operations=operations, before=before, **kwargs)


# ── the request that names a partner ──────────────────────────────────


def test_a_link_names_the_partner_it_names():
    """Defect: a request that names two items was read as naming one.

    `clip.link` names the partner it links to.  The authorization scope
    read only `unique_id`, so the partner's link state was a change on
    an item the request had named - and the read-back called it
    unauthorized.
    """
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.link", "unique_id": "v1",
                   "with_unique_ids": ["v2"]}]

    scope = _scope(operations, before)

    targets = {item.target for item in scope.items}
    assert "clip:v2" in targets
    assert scope_module.named_targets(operations[0]) == ("v1", "v2")


def test_a_link_partner_is_a_requested_movement_not_an_unauthorized_one():
    """Defect: the partner a link names read as an unauthorized change."""
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.link", "unique_id": "v1",
                   "with_unique_ids": ["v2"]}]

    scope = _scope(operations, before)

    assert scope.unauthorized == ()


def test_an_unlink_names_the_partner_it_breaks():
    """Defect: unlinking one item of a group wrote the OTHER one's state.

    A link group is one audio in two rows, so breaking it writes both
    sides.  The request does not name the partner, so the movement is
    NECESSARY - but it is visible.
    """
    before = _snapshot(
        video=[_clip("v1", linked_unique_ids=["a1"])],
        audio=[_clip("a1", track_type="audio", track_index=1,
                     linked_unique_ids=["v1"])])
    operations = [{"op": "clip.unlink", "unique_id": "v1"}]

    scope = _scope(operations, before)

    partner = [item for item in scope.items if item.target == "clip:a1"]
    assert partner and partner[0].aspect == "linked_audio"
    assert partner[0].kind == scope_module.NECESSARY


# ── the ripple that reaches further than the request ──────────────────


def test_a_native_ripple_names_the_rows_it_would_move():
    """Defect: Resolve's native ripple moved the speech and the overlays.

    `clip.remove` closes the gap with Resolve's own ripple, which moves
    EVERY row's tail.  The engine refused the primitive outright, so
    the agent learned nothing about what the request would have moved.
    """
    before = _snapshot(
        video=[_clip("v1"), _clip("v2", record_in=48, record_out=96)],
        audio=[_clip("a1", track_type="audio", track_index=1,
                     record_in=48, record_out=96)])
    operations = [{"op": "clip.remove", "unique_id": "v1"}]

    scope = _scope(operations, before, affected_spans=[(0, 192)])

    moved = {item.target: item for item in scope.items
             if item.aspect == "timing"}
    assert "clip:v2" in moved
    assert moved["clip:v2"].kind == scope_module.REQUESTED
    assert "clip:a1" in moved
    assert moved["clip:a1"].kind == scope_module.UNAUTHORIZED
    assert scope.unauthorized


def test_a_scoped_ripple_does_not_claim_the_other_rows():
    """Defect: a ripple's reach was guessed from its name.

    The engine's own scoped ripple moves the row it names.  A model that
    reached every row would report collateral the engine does not
    cause.
    """
    before = _snapshot(
        video=[_clip("v1"), _clip("v2", record_in=48, record_out=96)],
        audio=[_clip("a1", track_type="audio", track_index=1,
                     record_in=48, record_out=96)])
    operations = [{"op": "clip.ripple", "unique_id": "v1",
                   "delta_frames": -48}]

    scope = _scope(operations, before, affected_spans=[(0, 192)])

    assert scope.unauthorized == ()
    assert not scope.by_aspect("timing") or all(
        item.target == "clip:v2" for item in scope.by_aspect("timing"))


def test_a_ripple_beyond_the_declared_interval_is_unauthorized():
    """Defect: a movement outside the declared interval read as covered."""
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.ripple", "unique_id": "v1",
                   "delta_frames": -48}]

    scope = _scope(operations, before, affected_spans=[(0, 48)])

    assert [item.target for item in scope.unauthorized] == ["clip:v2"]


def test_a_request_that_declares_no_interval_is_not_caught_out_by_one():
    """Defect: a request with no declared interval read as outside it.

    There is nothing to be outside of, so the interval rule cannot
    refuse the request's own row.
    """
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.ripple", "unique_id": "v1",
                   "delta_frames": -48}]

    scope = _scope(operations, before)

    assert scope.unauthorized == ()


# ── the caption card left behind ──────────────────────────────────────


def test_a_caption_card_over_the_removed_picture_is_a_conflict():
    """Defect: a caption card left covering a picture that is gone.

    A caption card does not move with the picture under it.  Nothing
    said so before the write, and the caption was wrong afterwards.
    """
    before = _snapshot(
        video=[_clip("v1")],
        captions=[_clip("c1", track_type="subtitle", track_index=1)])
    operations = [{"op": "clip.delete", "unique_id": "v1"}]

    scope = _scope(operations, before, affected_spans=[(0, 48)])

    caption = [item for item in scope.items if item.aspect == "captions"]
    assert caption and caption[0].target == "clip:c1"
    assert caption[0].kind == scope_module.NECESSARY
    assert caption[0].carried is False
    assert caption[0].conflict


def test_a_caption_the_request_carries_is_not_a_conflict():
    """Defect: a caption the request re-renders was reported as stranded."""
    before = _snapshot(
        video=[_clip("v1")],
        captions=[_clip("c1", track_type="subtitle", track_index=1)])
    operations = [{"op": "clip.delete", "unique_id": "v1"},
                  {"op": "caption_fix", "unique_id": "c1", "text": "new"}]

    scope = _scope(operations, before, affected_spans=[(0, 48)])

    caption = [item for item in scope.items if item.aspect == "captions"]
    assert caption and caption[0].carried is True
    assert caption[0].conflict is False


# ── the approved work the request reaches ─────────────────────────────


def test_a_ripple_that_would_move_approved_work_is_refused_before_the_write():
    """Defect: the feasibility check asked whether the request WROTE to
    the approved clip, and a ripple REACHES it without writing to it.

    The request required one clip unchanged and asked for a ripple that
    would move it.  Nothing noticed before the write.
    """
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.ripple", "unique_id": "v1",
                   "delta_frames": -48}]

    result = compound_transaction.preflight(
        project_folder="", transaction_id="t1", operations=operations,
        before=before,
        requirements=[{"kind": "clip_unchanged", "unique_id": "v2"}],
        affected_spans=[(0, 192)])

    assert result["status"] == "refused"
    assert result["category"] == "conflict"
    assert "v2" in result["reason"]


def test_a_request_that_names_the_approved_clip_is_still_refused():
    """Defect: naming the clip the request requires unchanged was the
    only half of the check; the other half is the consequence."""
    before = _snapshot(video=[_clip("v1")])
    operations = [{"op": "clip.delete", "unique_id": "v1"}]

    result = compound_transaction.preflight(
        project_folder="", transaction_id="t1", operations=operations,
        before=before,
        requirements=[{"kind": "clip_unchanged", "unique_id": "v1"}],
        affected_spans=[(0, 48)])

    assert result["status"] == "refused"
    assert "unchanged" in result["reason"]


def test_a_request_that_keeps_the_approved_work_is_not_refused():
    """Defect: a gate that refuses correct work is not coverage."""
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    operations = [{"op": "clip.set_property", "unique_id": "v1",
                   "key": "ZoomX", "value": 1.5}]

    result = compound_transaction.preflight(
        project_folder="", transaction_id="t1", operations=operations,
        before=before,
        requirements=[{"kind": "clip_unchanged", "unique_id": "v2"}],
        affected_spans=[(0, 48)])

    assert result["status"] == "ok"
    assert result["collateral"]["conflicts"] == []


# ── the report the agent reads ────────────────────────────────────────


def test_the_preflight_report_carries_the_collateral():
    """Defect: the agent could not see what a request would move."""
    before = _snapshot(
        video=[_clip("v1"), _clip("v2", record_in=48, record_out=96)],
        audio=[_clip("a1", track_type="audio", track_index=1,
                     record_in=48, record_out=96)])
    operations = [{"op": "clip.remove", "unique_id": "v1"}]

    result = compound_transaction.preflight(
        project_folder="", transaction_id="t1", operations=operations,
        before=before, affected_spans=[(0, 192)])

    assert result["status"] == "ok"
    moved = {row["target"] for row in result["collateral"]["items"]
             if row["aspect"] == "timing"}
    assert "clip:a1" in moved
    assert [row["target"] for row in result["collateral"]["unauthorized"]] \
        == ["clip:a1"]


def test_an_operation_with_no_declared_scope_is_reported_not_cleaned():
    """Defect: an operation the algebra does not know read as safe."""
    before = _snapshot(video=[_clip("v1")])

    scope = _scope([{"op": "not.an.operation", "unique_id": "v1"}], before)

    assert scope.unbounded_ops == ("not.an.operation",)


def test_a_state_that_could_not_be_read_is_undetermined():
    """Defect: a model that cannot see the timeline cannot say the
    request is clean."""
    scope = scope_module.allowed_collateral(
        operations=[{"op": "clip.delete", "unique_id": "v1"}], before={})

    assert scope.status == "undetermined"


# ── the preference ────────────────────────────────────────────────────


def test_the_preference_picks_the_variant_that_touches_the_least():
    """Defect: two ways to do the same thing, and no way to choose.

    The punch list asks the engine to PREFER the change that affects
    only the authorized media or interval.  A ripple that reaches the
    speech is not that.
    """
    before = _snapshot(
        video=[_clip("v1"), _clip("v2", record_in=48, record_out=96)],
        audio=[_clip("a1", track_type="audio", track_index=1,
                     record_in=48, record_out=96)])
    ripple = [{"op": "clip.remove", "unique_id": "v1"}]
    local = [{"op": "clip.delete", "unique_id": "v1"}]

    index, scope = scope_module.prefer_least_collateral(
        [ripple, local], before=before, affected_spans=[(0, 192)])

    assert index == 1
    assert scope.unauthorized == ()


def test_the_preference_keeps_the_first_variant_when_both_cost_the_same():
    """Defect: a preference that reorders equal variants invents an
    intent nobody had."""
    before = _snapshot(video=[_clip("v1"), _clip("v2", record_in=48,
                                                 record_out=96)])
    first = [{"op": "clip.set_property", "unique_id": "v1", "key": "ZoomX",
              "value": 1.5}]
    second = [{"op": "clip.set_property", "unique_id": "v2", "key": "ZoomX",
               "value": 1.5}]

    index, _scope = scope_module.prefer_least_collateral(
        [first, second], before=before, affected_spans=[(0, 192)])

    assert index == 0
