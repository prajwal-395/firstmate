"""VC-13/VC-15: precise component dependency proofs over the cascade.

`cascade_graph.REEL_COMPONENTS` names each reel component's producing
operation-registry implementations; `selective_invalidation` answers
per-component reuse. Every test names the wrong skip it would leave:
a proof that reuses what changed is a reel silently dropped, so the
fail-closed answers are pinned row by row.
"""

from library.tools import cascade_graph as cascade
from library.tools import code_identity as identity


# ── VC-13: the component table names real, resolvable producers ─────

def test_every_component_names_its_producers():
    assert cascade.reel_components() == tuple(
        row["component"] for row in cascade.REEL_COMPONENTS)
    for component in cascade.reel_components():
        implementations = cascade.component_implementations(component)
        assert implementations, component
        for ref in implementations:
            assert identity.operation_implementation_ref(ref) is not None, (
                f"{component}: {ref} names no registry operation - "
                f"a renamed producer must land here under its new id")


def test_an_unknown_component_has_no_producers():
    assert cascade.component_implementations("no-such-component") is None
    proof = cascade.component_proof("no-such-component")
    assert not proof["complete"]


def test_component_proof_versions_every_producer():
    proof = cascade.component_proof("captions")
    assert proof["component"] == "captions"
    assert proof["complete"], proof["detail"]
    assert set(proof["versions"]) == set(
        cascade.component_implementations("captions"))


# ── VC-13: reuse needs a positive proof ──────────────────────────────

def _versions(component):
    return {ref: f"{ref}-v1" for ref in
            cascade.component_implementations(component)}


def test_matching_versions_reuse_and_say_why():
    reused, why = cascade.prove_component_reuse(
        "captions", _versions("captions"), _versions("captions"))
    assert reused
    assert "matches the record" in why


def test_every_other_answer_refuses_and_says_why():
    fresh = _versions("captions")
    stale = dict(fresh, **{"reel.build": "changed"})
    rows = [
        ("captions", {}, fresh, "no recorded implementation versions"),
        ("captions", fresh, {}, "could not be established"),
        ("captions", {"reel.build": "v1"}, fresh,
         "no recorded version for producing implementation"),
        ("captions", fresh, stale, "changed"),
        ("no-such-component", fresh, fresh, "never reused"),
    ]
    for component, recorded, new, because in rows:
        reused, why = cascade.prove_component_reuse(
            component, recorded, new)
        assert not reused, because
        assert because in why


# ── VC-15: one reel's declaration invalidates only that reel ─────────

def _change(kind, entity_id, reel=None, components=None):
    change = {"kind": kind, "entity_id": entity_id}
    if reel is not None:
        change["reel"] = reel
    if components is not None:
        change["components"] = components
    return change


def test_a_foreign_reel_declaration_keeps_every_component():
    report = cascade.selective_invalidation(
        _change("reel_declaration", "pin-1", reel="Reel 13"),
        reel="Reel 23")
    assert report["dirty_components"] == []
    assert not report["whole_reel"]


def test_a_lower_third_asset_reaches_only_its_lower_thirds():
    report = cascade.selective_invalidation(
        _change("lower_third_asset", "asset-7", reel="Reel 13"),
        reel="Reel 13")
    assert report["dirty_components"] == ["lower_thirds"]
    assert "footage" in report["reused_components"]
    assert "audio" in report["reused_components"]
    assert "captions" in report["reused_components"]
    assert not report["whole_reel"]


def test_an_unknown_kind_dirties_the_whole_reel():
    for change in (
            _change("no-such-kind", "x"),
            {"kind": "captions", "entity_id": ""},
            "not-a-mapping"):
        report = cascade.selective_invalidation(change, reel="Reel 01")
        assert report["whole_reel"], change
        assert report["dirty_components"] == list(
            cascade.reel_components()), change


def test_an_unknown_narrowing_dirties_the_whole_reel():
    report = cascade.selective_invalidation(
        _change("reel_declaration", "pin-1", reel="Reel 01",
                components=["no-such-component"]),
        reel="Reel 01")
    assert report["whole_reel"]


def test_a_footage_change_breaks_dependent_placements():
    report = cascade.selective_invalidation(
        _change("placement_timing", "retime-1", reel="Reel 01"),
        reel="Reel 01")
    assert "footage" in report["dirty_components"]
    # Captions/overlays/lower-thirds placed against the footage do not
    # re-render on a timing move: their frame mappings are recomputed.
    assert "captions" in report["remapped_components"]
    assert "captions" not in report["dirty_components"]


def test_a_reached_component_is_dirty_even_with_matching_versions():
    # The change event IS the input record: matching implementation
    # versions prove the code did not change, but the event proves
    # the input did - so the reached component regenerates and only
    # the unreached ones are reused.
    report = cascade.selective_invalidation(
        _change("lower_third_asset", "asset-7", reel="Reel 01"),
        reel="Reel 01")
    assert report["dirty_components"] == ["lower_thirds"]
    assert "captions" in report["reused_components"]
    assert "footage" in report["reused_components"]
    assert "audio" in report["reused_components"]


def test_cross_build_reuse_is_the_version_proof_not_the_event():
    # No change event names these artefacts: the version proof alone
    # decides, which is what lets an unchanged caption .mov survive
    # a build that re-placed its reel for other reasons.
    versions = _versions("captions")
    reused, _ = cascade.prove_component_reuse(
        "captions", versions, versions)
    assert reused
