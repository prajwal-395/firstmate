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


def _snapshot(*clips):
    """A timeline snapshot: one video row holding these items."""
    return {"tracks": [{"type": "video", "index": 1, "name": "V1",
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
