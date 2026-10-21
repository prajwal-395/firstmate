"""AXI-013: a mutation is judged against a fresh pre/post state.

The defect these tests name: a mutation could ANSWER SUCCESS - the
writer's receipt said "committed", the operation's acceptance contract
passed, the API returned True - while it changed something the request
never asked for.  Nothing compared the state the engine captured BEFORE
the write with the state the timeline carried AFTER it, so the only
witness to what happened was the writer.

The engine's own comparison is what closes that.  These tests pin it:
the gate, the wiring that runs it on a single-operation request, and
the count of production routes that carry it.
"""

from __future__ import annotations

import contextlib


def _clip(unique_id, record_in, record_out, **over):
    """One timeline item, in the shape a live snapshot carries."""
    clip = {
        "unique_id": unique_id,
        "name": unique_id,
        "record_in": record_in,
        "record_out": record_out,
        "source_in_frame": 0,
        "source_out_frame": record_out - record_in,
        "source_file": f"/media/{unique_id}.mov",
        "media_pool_item_id": f"media-{unique_id}",
        "media_type": "video",
        "enabled": True,
        "speed": 100.0,
        "transform": {},
        "color": {},
        "fusion": {},
        "linked_unique_ids": [],
    }
    clip.update(over)
    return clip


def _snapshot(*clips, name="V1"):
    """A timeline snapshot: one video row holding these items.

    A snapshot carries the timeline's own frame bounds, because a base
    with no frame bounds is not a timeline snapshot: the edit core
    compiles a request against the base snapshot, and a base it cannot
    place is refused before the comparison it was asked for.
    """
    record_end = max((int(clip.get("record_out") or 0) for clip in clips),
                     default=0)
    return {"timeline": "Reel 01", "start_frame": 0,
            "end_frame": record_end, "fps": 23.976,
            "tracks": [{"type": "video", "index": 1, "name": name,
                        "clips": [dict(clip) for clip in clips]}]}


INSERT_OPERATION = {
    "op": "clip.insert",
    "unique_id": "clip-new",
    "track_type": "video",
    "track_index": 1,
    "record_in": 100,
    "record_out": 120,
    "media_pool_item": "pool:a.mov",
}


def test_the_gate_catches_a_change_the_operation_never_asked_for():
    """Defect: a write that touched an unrelated item was invisible.

    The operation places a clip at 100-120.  The timeline also carries
    an item at 500-520, and the write changed it.  Nothing the request
    asked for covers that item, and the engine's own comparison of the
    two states says so.
    """
    from library.tools import mutation_verification

    before = _snapshot(_clip("clip-other", 500, 520))
    after = _snapshot(
        _clip("clip-new", 100, 120),
        _clip("clip-other", 500, 520, source_in_frame=5))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",))

    assert verdict["status"] == "failed"
    assert verdict["failures"], "the refusal says nothing about what moved"
    named = " ".join(verdict["failures"])
    assert "clip-other" in named


def test_the_gate_accepts_the_change_the_operation_asked_for():
    """The gate is not a blanket refusal: the requested change passes."""
    from library.tools import mutation_verification

    before = _snapshot(_clip("clip-other", 500, 520))
    after = _snapshot(
        _clip("clip-new", 100, 120),
        _clip("clip-other", 500, 520))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",))

    assert verdict["status"] == "verified"
    assert verdict["failures"] == []


def test_a_ripple_removal_is_judged_as_the_shift_it_is():
    """A ripple remove moves the later clips; that movement is permitted.

    The removal closes the gap, so the clips after the removed one move
    one removed-duration earlier.  The gate has to see that as the
    change the operation asked for, and not as collateral.
    """
    from library.tools import mutation_verification

    before = _snapshot(_clip("clip-1", 0, 50), _clip("clip-2", 50, 100))
    after = _snapshot(_clip("clip-2", 0, 50))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[{"op": "clip.remove", "unique_id": "clip-1"}],
            snapshot=before),
        after=after,
        affected_spans=((0, 100),),
        conflict_domains=("timeline_structure",))

    assert verdict["status"] == "verified", verdict["failures"]


def test_a_placement_is_judged_by_the_span_it_was_asked_for():
    """Resolve names the placed item, so the scope covers it by span.

    The operation carries the CLIENT's id and the timeline carries
    Resolve's; the scope covers the item by the span the request asked
    for, which is where it landed.
    """
    from library.tools import mutation_verification

    before = _snapshot(_clip("clip-other", 500, 520))
    after = _snapshot(_clip("clip-other", 500, 520),
                      _clip("clip-placed-by-resolve", 100, 120))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",))

    assert verdict["status"] == "verified", verdict["failures"]


def test_a_caption_restyle_is_judged_as_the_media_change_it_is():
    """A restyled caption card is a NEW file on the same card.

    The permitted delta has to name it: without the media-replacing
    declaration the swap reads as an unauthorized media change and the
    gate would refuse a caption restyle that landed exactly as asked.
    The new file brings its own name and its own length, so those are
    the replacement's own doing on the card it names.
    """
    from library.tools import mutation_verification

    before = _snapshot(_clip("card-1", 0, 40, source_file="/caps/one.mov",
                              media_pool_item_id="media-one"))
    after = _snapshot(_clip("card-1", 0, 40, source_file="/caps/two.mov",
                             media_pool_item_id="media-two",
                             name="two.mov", source_in_frame=0,
                             source_out_frame=40))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[{"op": "caption.style", "unique_id": "card-1",
                         "style": "big"}], snapshot=before),
        after=after,
        affected_spans=((0, 40),),
        conflict_domains=("captions",))

    assert verdict["status"] == "verified", verdict["failures"]


def test_a_caption_restyle_that_touched_another_card_is_collateral():
    """The replacement is permitted on the card it names, and nowhere else."""
    from library.tools import mutation_verification

    before = _snapshot(_clip("card-1", 0, 40, source_file="/caps/one.mov",
                              media_pool_item_id="media-one"),
                       _clip("card-2", 40, 80))
    after = _snapshot(_clip("card-1", 0, 40, source_file="/caps/two.mov",
                             media_pool_item_id="media-two"),
                      _clip("card-2", 40, 80, source_in_frame=5))

    verdict = mutation_verification.verify_delta(
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[{"op": "caption.style", "unique_id": "card-1",
                         "style": "big"}], snapshot=before),
        after=after,
        affected_spans=((0, 40),),
        conflict_domains=("captions",))

    assert verdict["status"] == "failed"
    assert "card-2" in " ".join(verdict["failures"])


def test_the_baseline_names_the_target_the_intent_the_scope_and_the_revision():
    """The four things AXI-013 asks to be captured BEFORE the write."""
    from library.tools import mutation_verification

    baseline = mutation_verification.capture_baseline(
        project="Project", timeline="Reel 01", revision=7,
        operations=[INSERT_OPERATION], snapshot=_snapshot(),
        preservation_scope=("timeline_structure",))

    assert baseline.revision == 7
    assert any("clip-new" in identity
               for identity in baseline.target_identity)
    assert baseline.intended_changes[0]["op"] == "clip.insert"
    assert baseline.preservation_scope == ("timeline_structure",)


def _install_a_writer_that_says_success(monkeypatch, after):
    """A writer that answers SUCCESS while the timeline says otherwise.

    The receipt is the shape a real adapter returns: the acceptance
    contract passed and the operation-level verification says "verified".
    The timeline behind it is the truth, and it disagrees.
    """
    from library.tools import resolve_axi_core
    from library.tools.edit_execution_adapters import _place

    def writer(context):
        return {
            "status": "committed",
            "backend": "resolve_axi",
            "mutated": True,
            "operation_id": "clip.insert",
            "request_id": context.request.id,
            "operation_index": 0,
            "target_identity": {"unique_id": "clip-new",
                                "client_unique_id": "clip-new"},
            "readback": {"snapshot": after},
            "acceptance_contracts": [{
                "id": "acceptance:clip.insert",
                "operation": "clip.insert",
                "passed": True,
                "results": [{"check": "target_identity", "passed": True,
                             "status": "passed"}],
                "missing": [],
                "failed": [],
            }],
            "execution_contract": {
                "verification": {
                    "status": "verified",
                    "checks": ({"operation": "clip.insert", "passed": True},),
                    "evidence": {"acceptance": "the writer says so"},
                },
                "recovery": {"status": "unavailable", "action": "none"},
            },
        }

    key = ("clip.insert", "resolve_axi")
    previous = resolve_axi_core._EDIT_EXECUTORS.get(key)
    resolve_axi_core.register_edit_executor(key[0], key[1], writer,
                                            replace=True)
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda project, timeline, resolve=None: after)
    return previous, _place


def test_a_single_operation_request_is_judged_by_the_engine_not_the_writer(
        monkeypatch):
    """Defect: a ONE-operation request got no engine-owned comparison.

    The engine's whole-request verification only ran for requests of
    more than one operation, so the common case - one operation - was
    judged by the writer that performed it.  A writer that changed the
    wrong thing and said "committed" was believed.

    Here the writer says success and the timeline says otherwise, and
    the engine refuses.
    """
    from types import SimpleNamespace

    from library.tools import resolve_axi_core, timeline_shadow

    before = _snapshot(_clip("clip-other", 500, 520))
    after = _snapshot(
        _clip("clip-new", 100, 120),
        _clip("clip-other", 500, 520, source_in_frame=5))

    _previous, _place = _install_a_writer_that_says_success(monkeypatch, after)

    class _Head:
        project = "Project"
        timeline_name = "Reel 01"
        model_revision = None

    class _Store:
        """The shadow store: the head before the write, the commit after.

        The committed generation moves only because the write landed,
        so a route that verifies nothing would commit.  That is what
        makes the test a defect-naming one rather than a shape check.
        """

        def __init__(self):
            self.calls = 0

        def current_snapshots(self, project):
            self.calls += 1
            head = _Head()
            head.generation = 7 if self.calls == 1 else 8
            return [(head, before)]

    store = _Store()
    monkeypatch.setattr(timeline_shadow, "ShadowStore", lambda: store)
    monkeypatch.setattr("library.tools.resolve_lock.resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())

    request = SimpleNamespace(
        id="request-1", idempotency_key="request-1", project="Project",
        timeline="Reel 01", preconditions=(), preservation=(),
        operations=(), expected_model_revision=None,
        to_dict=dict)
    compiled = SimpleNamespace(
        operations=(dict(INSERT_OPERATION),),
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",),
        order=(0,))

    try:
        result = resolve_axi_core._execute_registered_backend(
            backend="resolve_axi",
            plan={"routing": {"backend": "resolve_axi"}},
            request=request,
            preparation=SimpleNamespace(as_dict=dict),
            compiled=compiled,
            generation=SimpleNamespace(generation=7),
            snapshot=before,
            owner="mutation-verification-test")
    finally:
        from library.tools import resolve_axi_core as core
        core.register_edit_executor("clip.insert", "resolve_axi", _place,
                                    replace=True)

    # The write landed, so the receipt is a PARTIAL FAILURE and never a
    # commit: the engine refused the request even though the writer
    # said it succeeded.
    assert result["status"] == "partial_failure"
    assert result["refusal"]["category"] == "verification_failed"
    evidence = result["failure_evidence"]["delta_verification"]
    assert evidence["status"] == "failed"
    assert "clip-other" in " ".join(evidence["failures"])


def test_the_coverage_report_counts_every_qualified_route():
    """The report carries a denominator, and names what it does not cover."""
    from library.tools import mutation_verification

    report = mutation_verification.coverage_report()

    assert report["denominator"] == len(
        mutation_verification.qualified_pairs())
    assert report["denominator"] > 0
    assert report["covered"] + report["uncovered"] == report["denominator"]
    for row in report["covered_routes"]:
        assert mutation_verification.gate_covers(row["operation"],
                                                 row["backend"])


def test_every_qualified_route_the_gate_claims_is_really_qualified():
    """The route table and the algebra must agree about what is qualified.

    A route that names an operation the algebra does not mark
    production-enabled is a claim about a route that may not run; a
    qualified operation the table forgets is a gap nobody can count.
    """
    from library.tools import mutation_verification

    qualified = set(mutation_verification.qualified_pairs())
    declared = {(route.operation, route.backend)
                for route in mutation_verification.ROUTES}

    assert declared == qualified


# ── QA-001: the whole-timeline read-back ──────────────────────────────


def test_the_whole_timeline_gate_refuses_a_structure_nobody_authorized():
    """Defect: a whole-timeline outcome judged only by its writer.

    The two clips landed where the request asked for them, and the
    delta says so.  One of them plays frames its media does not have,
    and only the STRUCTURE says that: the writer's receipt never
    mentions the media.
    """
    from library.tools import mutation_verification

    before = _snapshot()
    after = _snapshot(
        _clip("clip-new", 100, 120),
        _clip("clip-second", 200, 220, source_out_frame=2000,
              source_frames=1000))

    verdict = mutation_verification.whole_timeline_gate(
        "multi_operation_revision",
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120), (200, 220)),
        conflict_domains=("timeline_structure",),
        media_on_disk=True)

    assert verdict["status"] == "failed"
    assert any("source_alignment" in failure
               for failure in verdict["failures"]), verdict["failures"]


def test_the_whole_timeline_gate_passes_a_clean_outcome(tmp_path):
    """The control: a clean whole-timeline outcome is not refused."""
    from library.tools import mutation_verification

    media = tmp_path / "one.mov"
    media.write_bytes(b"not really a movie")
    before = _snapshot()
    after = _snapshot(_clip("clip-new", 100, 120, source_file=str(media)))

    verdict = mutation_verification.whole_timeline_gate(
        "multi_operation_revision",
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",),
        media_on_disk=True)

    assert verdict["status"] == "verified", verdict["failures"]


def test_the_whole_timeline_coverage_counts_the_paths_and_names_the_gap():
    """The coverage of the whole-timeline paths, with a denominator.

    The count is read out of the CODE, so a path that stops running the
    gate is reported as uncovered the moment it does.
    """
    from library.tools import mutation_verification

    report = mutation_verification.whole_timeline_coverage()

    assert report["denominator"] == len(
        mutation_verification.WHOLE_TIMELINE_ROUTES) - len(
            report["unreachable_routes"])
    assert report["denominator"] > 0
    assert report["covered"] + report["uncovered"] == report["denominator"]
    covered = {row["path"] for row in report["covered_routes"]}
    assert "multi_operation_revision" in covered
    assert "protected_compound_touchup" in covered
    assert "reel_build" in covered
    # The touch-up path stages a copy and re-reads the track itself;
    # the engine does not read that outcome back structurally yet, and
    # the report says so by name.
    assert {row["path"] for row in report["uncovered_routes"]} == {
        "reel_touchup"}
    # A path the transaction planner refuses never reaches a write, so
    # it is reported as unreachable rather than as covered.
    unreachable = {row["path"] for row in report["unreachable_routes"]}
    assert "multi_delete_batch" in unreachable
    assert "multi_delete_batch" not in covered


def test_a_multi_operation_revision_is_judged_by_the_structural_read_back(
        monkeypatch):
    """The whole-timeline path runs the gate, and the gate refuses.

    Two caption restyles landed exactly where they were asked to land,
    and the delta says so: a restyle is a media replacement, and the
    card it names may carry a new file.  The new card plays frames its
    media does not have, and only the STRUCTURE says that - the
    writer's receipt never mentions the media.
    """
    from types import SimpleNamespace

    from library.tools import resolve_axi_core, timeline_shadow

    before = _snapshot(
        _clip("card-1", 0, 40, source_file="/caps/one.mov",
              media_pool_item_id="media-one"),
        _clip("card-2", 40, 80, source_file="/caps/three.mov",
              media_pool_item_id="media-three"), name="Subtitles")
    after = _snapshot(
        _clip("card-1", 0, 40, source_file="/caps/two.mov",
              media_pool_item_id="media-two", name="two.mov",
              source_out_frame=2000, source_frames=1000),
        _clip("card-2", 40, 80, source_file="/caps/four.mov",
              media_pool_item_id="media-four", name="four.mov"),
        name="Subtitles")

    def writer(context):
        return {
            "status": "committed",
            "backend": "resolve_axi",
            "mutated": True,
            "operation_id": context.operation["op"],
            "request_id": context.request.id,
            "operation_index": context.operation_index,
            "target_identity": {"unique_id": context.operation["unique_id"]},
            "readback": {"snapshot": after},
            "acceptance_contracts": [{
                "id": f"acceptance:{context.operation['op']}",
                "operation": context.operation["op"],
                "passed": True,
                "results": [{"check": "target_identity", "passed": True,
                             "status": "passed"}],
                "missing": [],
                "failed": [],
            }],
            "execution_contract": {
                "verification": {
                    "status": "verified",
                    "checks": ({"operation": context.operation["op"],
                                "passed": True},),
                    "evidence": {"acceptance": "the writer says so"},
                },
                "recovery": {"status": "unavailable", "action": "none"},
            },
        }

    resolve_axi_core.register_edit_executor(
        "caption.style", "resolve_axi", writer, replace=True)
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda project, timeline, resolve=None: after)
    monkeypatch.setattr(resolve_axi_core, "_revalidate_registered_targets",
                        lambda **kwargs: [])

    class _Head:
        project = "Project"
        timeline_name = "Reel 01"
        model_revision = None

    class _Store:
        """The shadow store: the head before the write, the commit after."""

        def __init__(self):
            self.calls = 0

        def current_snapshots(self, project):
            self.calls += 1
            head = _Head()
            head.generation = 7 if self.calls == 1 else 8
            return [(head, before)]

    store = _Store()
    monkeypatch.setattr(timeline_shadow, "ShadowStore", lambda: store)
    monkeypatch.setattr("library.tools.resolve_lock.resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())

    first = {"op": "caption.style", "unique_id": "card-1", "style": "bold",
             "record_in": 0, "record_out": 40}
    second = {"op": "caption.style", "unique_id": "card-2", "style": "bold",
              "record_in": 40, "record_out": 80}
    request = SimpleNamespace(
        id="request-2", idempotency_key="request-2", project="Project",
        timeline="Reel 01", preconditions=(), preservation=(),
        operations=(), expected_model_revision=None,
        to_dict=dict)
    compiled = SimpleNamespace(
        operations=(first, second),
        affected_spans=((0, 40), (40, 80)),
        conflict_domains=("captions",),
        order=(0, 1))

    try:
        result = resolve_axi_core._execute_registered_backend(
            backend="resolve_axi",
            plan={"routing": {"backend": "resolve_axi"}},
            request=request,
            preparation=SimpleNamespace(as_dict=dict),
            compiled=compiled,
            generation=SimpleNamespace(generation=7),
            snapshot=before,
            owner="whole-timeline-test")
    finally:
        from library.tools.edit_execution_adapters import _caption_style
        resolve_axi_core.register_edit_executor("caption.style",
                                                "resolve_axi",
                                                _caption_style, replace=True)

    assert result["status"] == "partial_failure", result
    assert result["refusal"]["category"] == "verification_failed"
    evidence = result["failure_evidence"]["whole_timeline_verification"]
    assert evidence["status"] == "failed"
    assert any("source_alignment" in failure
               for failure in evidence["failures"]), evidence["failures"]


# ── QA-005: the LOOK read-back ─────────────────────────────────────────


def test_the_whole_timeline_gate_refuses_a_look_nobody_authorized():
    """Defect: a shot's comp moved and nothing said so.

    The request asked for a trim on one clip.  The write also re-authored
    the Fusion comp of ANOTHER clip on another canvas - the same comp
    names, a different picture - and the delta cannot see it: the comp
    names did not change.  The LOOK read-back can.
    """
    from library.tools import mutation_verification

    graded = _clip("clip-approved", 500, 520,
                   fusion={"comp_count": 1, "comp_names": ["MediaIn1"],
                           "comps": [{"comp_index": 1, "name": "MediaIn1",
                                      "canvas": {"width": 1080,
                                                 "height": 1920},
                                      "tools": ["Background", "MediaIn1"]}]})
    moved = _clip("clip-approved", 500, 520,
                  fusion={"comp_count": 1, "comp_names": ["MediaIn1"],
                          "comps": [{"comp_index": 1, "name": "MediaIn1",
                                     "canvas": {"width": 3840,
                                                "height": 2160},
                                     "tools": ["Background", "MediaIn1"]}]})

    before = _snapshot(graded)
    after = _snapshot(_clip("clip-new", 100, 120), moved)

    verdict = mutation_verification.whole_timeline_gate(
        "multi_operation_revision",
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",),
        media_on_disk=True)

    assert verdict["look"]["status"] == "failed"
    assert any("canvas" in failure for failure in verdict["failures"]), \
        verdict["failures"]


def test_the_whole_timeline_gate_passes_a_look_that_did_not_move():
    """The control: the same timeline read twice carries the same look."""
    from library.tools import mutation_verification

    clip = _clip("clip-approved", 500, 520,
                 fusion={"comp_count": 1, "comp_names": ["MediaIn1"],
                         "comps": [{"comp_index": 1, "name": "MediaIn1",
                                    "canvas": {"width": 1080, "height": 1920},
                                    "tools": ["Background", "MediaIn1"]}]})
    before = _snapshot(clip)
    after = _snapshot(_clip("clip-new", 100, 120), dict(clip))

    verdict = mutation_verification.whole_timeline_gate(
        "multi_operation_revision",
        baseline=mutation_verification.capture_baseline(
            project="Project", timeline="Reel 01", revision=7,
            operations=[INSERT_OPERATION], snapshot=before),
        after=after,
        affected_spans=((100, 120),),
        conflict_domains=("timeline_structure",),
        media_on_disk=True)

    assert verdict["look"]["status"] == "verified", verdict["look"]
