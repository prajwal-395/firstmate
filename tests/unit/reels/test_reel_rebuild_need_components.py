"""VC-13/VC-15: the precise engine half and the proof-gated decision.

`component_closure_digest` replaces the wholesale source-tree
fingerprint wherever versions prove every producing implementation;
`decide_with_component_proofs` gates the broad verdict through the
selective-invalidation report. Fail-closed throughout: an unknown
dependency rebuilds, and a proof never invents a LEAVE_ALONE.
"""

from library.tools import cascade_graph as cascade
from library.tools import reel_rebuild_need as need

MATCH = {"derivation": "d" * 64, "carried": "c" * 64}


def _derivation(**overrides):
    body = dict(
        reel_number=1,
        engine_code="e" * 64,
        project_wide="p" * 64,
        plan_content_hash="l" * 64,
        transcript_hash="t" * 64,
        master_digest="m" * 64,
        ranges=[(1.0, 2.0)],
        placements_list=[],
        cards=[],
        caption_segments=[],
        explainer_segments=[],
        semantic_segments=[],
        overlay_placements=[],
        motion_record={},
        ending=None,
        look=None,
        grade_cdl=None,
        grade_look=None,
        power_grade=None,
    )
    body.update(overrides)
    return need.derivation_digest(**body)


# ── The closure only closes on complete versions ─────────────────────

def test_versions_absent_is_byte_identical_to_before():
    assert _derivation() == _derivation(component_versions=None)
    assert _derivation() == _derivation(component_versions={})


def test_an_unversionable_implementation_falls_back_to_wholesale():
    versions = {"captions": {"subtitles.render": None}}
    assert _derivation(
        component_versions=versions) == _derivation()


def test_matching_versions_close_the_digest():
    versions = need.component_versions_for_reel()
    assert _derivation(
        component_versions=versions) != _derivation()


def test_a_changed_producer_moves_the_closure():
    versions = need.component_versions_for_reel()
    first = _derivation(component_versions=versions)
    moved = {component: dict(refs) for component, refs in versions.items()}
    moved["captions"] = dict(moved["captions"], **{"reel.build": "x" * 64})
    assert _derivation(component_versions=moved) != first


def test_component_versions_for_reel_covers_every_component():
    versions = need.component_versions_for_reel()
    assert set(versions) == set(cascade.reel_components())
    for component, refs in versions.items():
        assert set(refs) == set(
            cascade.component_implementations(component)), component


# ── The proof-gated decision never invents a skip ────────────────────

def _report(**overrides):
    report = dict(
        dirty_components=[], reused_components=list(
            cascade.reel_components()),
        remapped_components=[], whole_reel=False,
        explanations=[], reason="nothing moved")
    report.update(overrides)
    return report


def test_no_report_leaves_the_broad_verdict_untouched():
    broad = need.decide("Reel 01", "d" * 64, "c" * 64, MATCH)
    assert broad.leave_alone
    assert need.decide_with_component_proofs(
        "Reel 01", "d" * 64, "c" * 64, MATCH).leave_alone
    assert need.decide_with_component_proofs(
        "Reel 01", "d" * 64, "c" * 64, MATCH,
        selective_report=None).leave_alone


def test_an_unprovable_report_rebuilds_and_says_why():
    report = _report(whole_reel=True, reason="unknown change kind",
                     dirty_components=list(cascade.reel_components()),
                     reused_components=[])
    decision = need.decide_with_component_proofs(
        "Reel 01", "d" * 64, "c" * 64, MATCH,
        selective_report=report)
    assert decision.action == need.REBUILD
    assert "unprovable" in decision.reason


def test_a_dirty_component_overrules_a_matching_wholesale_digest():
    report = _report(
        dirty_components=["lower_thirds"],
        reused_components=[c for c in cascade.reel_components()
                           if c != "lower_thirds"],
        reason="lower_third_asset asset-7: 1 component(s) dirty")
    decision = need.decide_with_component_proofs(
        "Reel 01", "d" * 64, "c" * 64, MATCH,
        selective_report=report)
    assert decision.action == need.REBUILD
    assert "lower_thirds" in decision.reason


def test_a_confirmed_leave_alone_carries_the_reuse_proof():
    report = _report(reason="lower_third_asset asset-7, other reel")
    decision = need.decide_with_component_proofs(
        "Reel 01", "d" * 64, "c" * 64, MATCH,
        selective_report=report)
    assert decision.leave_alone
    assert "reuse" in decision.reason


def test_a_broad_rebuild_names_its_dirty_components():
    report = _report(
        dirty_components=["captions"],
        reused_components=[c for c in cascade.reel_components()
                           if c != "captions"],
        remapped_components=[],
        reason="caption_content c-1: 1 component(s) dirty")
    decision = need.decide_with_component_proofs(
        "Reel 01", "x" * 64, "c" * 64, MATCH,
        selective_report=report)
    assert decision.action == need.REBUILD
    assert "captions" in decision.reason
