"""Lane D (cascade engine): one dependency graph over the existing skips.

The report's section 3.1 edge table joined into a walker
(`library/tools/cascade_graph.py`) plus the per-reel
`derived_registry.json`. Every test names the wrong-skip it would
leave: a walker that authorises reuse is a reel silently dropped, so
the fail-closed answers are pinned row by row.
"""

import json
from pathlib import Path

from library.tools import cascade_graph as cascade
from library.tools import reel_rebuild_need as need

REPO = Path(__file__).resolve().parents[3]


# ── D1: the edge table is the report's, row for row ───────────────────

def test_the_edge_table_carries_every_report_row():
    edges = {row["edge"]: row for row in cascade.EDGES}
    assert [row["edge"] for row in cascade.EDGES] == list(cascade.EDGE_NAMES)
    for name in (
            "bytes -> lanes",
            "lanes+catalog -> index",
            "drawing -> caption .mov",
            "plan -> overlay .mov",
            "words+pixels+renderer -> caption binding",
            "picture-under-words",
            "reel derivation",
            "timeline as-built",
            "planning input",
            "verify scope",
            "mask file"):
        assert name in edges, name
        for key in ("hash", "dirty_test", "reuse"):
            assert edges[name][key], (name, key)


def test_each_edge_names_its_existing_owner():
    owners = {row["edge"]: row["hash"] for row in cascade.EDGES}
    assert "footage_identity" in owners["bytes -> lanes"]
    assert "render_cache" in owners["drawing -> caption .mov"]
    assert "render_cache" in owners["plan -> overlay .mov"]
    assert "plan_provenance" in owners[
        "words+pixels+renderer -> caption binding"]
    assert "plan_provenance" in owners["picture-under-words"]
    assert "reel_rebuild_need" in owners["reel derivation"]
    assert "reel_rebuild_need" in owners["timeline as-built"]
    assert "planning_reuse" in owners["planning input"]
    assert "dirty_regions" in owners["verify scope"]


# ── The shared dirty test only refuses skips ──────────────────────────

def test_matching_hashes_are_the_only_way_to_be_allowed():
    allowed, _ = cascade.skip_allowed(
        "drawing -> caption .mov", "k" * 8, "k" * 8)
    assert allowed


def test_every_other_answer_refuses_and_says_why():
    rows = [
        ("drawing -> caption .mov", "", "k" * 8, "could not be established"),
        ("drawing -> caption .mov", "k" * 8, "", "no hash on record"),
        ("drawing -> caption .mov", "a" * 8, "b" * 8, "differs"),
        ("drawing -> caption .mov", None, None, "could not be established"),
        ("no such edge", "k" * 8, "k" * 8, "unknown cascade edge"),
    ]
    for edge, fresh, recorded, because in rows:
        allowed, why = cascade.skip_allowed(edge, fresh, recorded)
        assert not allowed, because
        assert because in why


def test_the_three_skip_paths_share_one_predicate():
    # A skip path with a private compare is how one path re-renders on
    # a renderer edit while another reuses - the defect the walker
    # exists to remove. Each wrapper must refuse exactly what the
    # shared test refuses.
    assert cascade.caption_reuse_allowed("k", "k")[0]
    assert not cascade.caption_reuse_allowed("k", "other")[0]
    assert cascade.overlay_reuse_allowed("k", "k")[0]
    assert not cascade.overlay_reuse_allowed("", "k")[0]
    assert cascade.planning_reuse_allowed("k", "k")[0]
    assert not cascade.planning_reuse_allowed("k", "")[0]


# ── mark_dirty walks dependents and fails closed ─────────────────────

def test_an_unknown_change_dirties_the_whole_reel():
    for change in (
            {"entity_kind": "no-such-kind", "entity_id": "x", "revision": 1},
            {"entity_kind": "derivation", "entity_id": "", "revision": 1},
            {"entity_kind": "derivation", "revision": 1},
            "not-a-mapping"):
        result = cascade.mark_dirty(change, registry=None)
        assert result["whole_reel"], change
        assert result["dirty"] == list(cascade.EDGE_NAMES), change
        assert result["clean"] == [], change


def test_a_caption_change_reaches_only_its_edges():
    change = {"entity_kind": "caption_drawing", "entity_id": "sub_x",
              "revision": 3}
    result = cascade.mark_dirty(change, registry=None)
    assert result["whole_reel"]
    assert "drawing -> caption .mov" in result["dirty"]
    assert "words+pixels+renderer -> caption binding" in result["dirty"]
    assert "mask file" in result["clean"]
    assert "timeline as-built" in result["clean"]


def test_a_matching_registry_cleans_a_reached_edge():
    registry = {"format": cascade.REGISTRY_FORMAT, "reel": "Reel 01",
                "hashes": {"drawing -> caption .mov": "k" * 8}}
    change = {"entity_kind": "caption_drawing", "entity_id": "sub_x",
              "revision": 3,
              "fresh_hashes": {"drawing -> caption .mov": "k" * 8}}
    result = cascade.mark_dirty(change, registry=registry)
    assert "drawing -> caption .mov" in result["clean"]
    # The binding edge was reached but carries no fresh hash, so it is
    # dirty: a change that says what moved but not what it moved to
    # still costs the edges it reaches.
    assert "words+pixels+renderer -> caption binding" in result["dirty"]
    assert not result["whole_reel"] or result["dirty"]


def test_a_mismatching_registry_dirties_the_reached_edge():
    registry = {"format": cascade.REGISTRY_FORMAT, "reel": "Reel 01",
                "hashes": {"drawing -> caption .mov": "old"}}
    change = {"entity_kind": "caption_drawing", "entity_id": "sub_x",
              "revision": 4,
              "fresh_hashes": {"drawing -> caption .mov": "new"}}
    result = cascade.mark_dirty(change, registry=registry)
    assert "drawing -> caption .mov" in result["dirty"]


# ── The rebuild gate only refuses skips ───────────────────────────────

def test_a_rebuild_is_never_turned_into_a_leave_alone():
    decision = need.Decision("Reel 01", need.REBUILD, "drifted")
    assert cascade.gate_rebuild_decision(
        decision, fresh_derivation="d" * 64,
        recorded={"derivation": "d" * 64}) is decision


def test_a_matching_signature_without_a_registry_stands():
    decision = need.decide("Reel 01", "d" * 64, "c" * 64,
                           {"derivation": "d" * 64, "carried": "c" * 64})
    assert decision.leave_alone
    assert cascade.gate_rebuild_decision(
        decision, fresh_derivation="d" * 64,
        recorded={"derivation": "d" * 64,
                  "carried": "c" * 64}).leave_alone


def test_a_registry_that_disagrees_rebuilds_and_says_why():
    decision = need.decide("Reel 01", "d" * 64, "c" * 64,
                           {"derivation": "d" * 64, "carried": "c" * 64})
    assert decision.leave_alone
    registry = {"format": cascade.REGISTRY_FORMAT, "reel": "Reel 01",
                "hashes": {"reel derivation": "stale"}}
    gated = cascade.gate_rebuild_decision(
        decision, fresh_derivation="d" * 64,
        recorded={"derivation": "d" * 64, "carried": "c" * 64},
        registry=registry)
    assert gated.action == need.REBUILD
    assert "registry" in gated.reason


# ── The derived registry ─────────────────────────────────────────────

def test_registry_round_trip_merges_per_key(tmp_path):
    review = tmp_path / "review"
    cascade.write_registry(str(review), "Reel 01",
                           {"reel derivation": "d" * 64})
    cascade.write_registry(str(review), "Reel 01",
                           {"timeline as-built": "c" * 64})
    doc = cascade.read_registry(str(review), "Reel 01")
    assert doc["format"] == cascade.REGISTRY_FORMAT
    assert doc["hashes"] == {"reel derivation": "d" * 64,
                             "timeline as-built": "c" * 64}


def test_registry_refuses_what_it_did_not_write(tmp_path):
    review = tmp_path / "review"
    assert cascade.read_registry(str(review), "Reel 09") is None
    path = Path(cascade.registry_path(str(review), "Reel 09"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "something-else/9",
                                "hashes": {}}), encoding="utf-8")
    assert cascade.read_registry(str(review), "Reel 09") is None


def test_update_from_build_files_hashes_under_edge_names(tmp_path):
    review = tmp_path / "review"
    cascade.update_registry_from_build(
        str(review), "Reel 02", derivation="d" * 64, carried=None,
        caption_binding="v1:abc", footage_binding="v1:def",
        model_revision=7)
    doc = cascade.read_registry(str(review), "Reel 02")
    assert doc["hashes"]["reel derivation"] == "d" * 64
    assert "timeline as-built" not in doc["hashes"]
    assert doc["hashes"][
        "words+pixels+renderer -> caption binding"] == "v1:abc"
    assert doc["hashes"]["picture-under-words"] == "v1:def"
    assert doc["extra"]["model_revision"] == 7


# ── D3: scoped QA from dirty spans ────────────────────────────────────

def _dirty_span(start=10.0, end=12.0):
    return {"whole_reel": False, "whole_reel_reason": "",
            "dirty_domains": ["picture"],
            "dirty_spans": [{"domain": "picture",
                             "start_seconds": start, "end_seconds": end,
                             "core_start_seconds": start,
                             "core_end_seconds": end}]}


def test_drift_outside_the_dirty_spans_is_not_this_rebuilds():
    runs = [{"planned_start": 10.5, "planned_end": 11.0, "text": "in"},
            {"planned_start": 40.0, "planned_end": 41.0, "text": "out"}]
    kept = cascade.hearing_drift_in_scope(runs, _dirty_span())
    assert [row["text"] for row in kept] == ["in"]


def test_whole_reel_dirty_keeps_every_drift_run():
    runs = [{"planned_start": 40.0, "planned_end": 41.0, "text": "out"}]
    dirty = {"whole_reel": True, "whole_reel_reason": "unknown op",
             "dirty_domains": ["picture", "audio"], "dirty_spans": []}
    assert cascade.hearing_drift_in_scope(runs, dirty) == runs
    assert cascade.hearing_drift_in_scope([], _dirty_span()) == []


def test_scoped_verify_measures_drift_without_a_render(tmp_path):
    runs = [{"planned_start": 10.5, "planned_end": 11.0, "text": "in"},
            {"planned_start": 40.0, "planned_end": 41.0, "text": "out"}]
    report = cascade.scoped_verify(
        str(tmp_path / "missing.mp4"), _dirty_span(), hearing_runs=runs)
    assert report["drift_total"] == 2
    assert [row["text"] for row in report["drift_in_scope"]] == ["in"]
    assert "report-only" in report["detail"]


def test_scoped_verify_without_dirty_checks_nothing(tmp_path):
    report = cascade.scoped_verify(str(tmp_path / "missing.mp4"), None)
    assert report["results"] == []
    assert report["drift_in_scope"] == []
