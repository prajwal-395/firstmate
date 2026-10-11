"""Unit 4: editorial migration uses stable IDs and shared time semantics.

Fixture-only: no live project, no pipeline run, no model. Every test
builds legacy rows in memory and migrates them through
``library.tools.project_model_editorial``.
"""
import copy

import pytest

from library.tools import project_model_editorial as editorial
from library.tools.composed_edit import Insertion, ItemChange
from library.tools.edit_patch import EditPatch
from library.tools.project_model import validate_project_model


def _aroll_node():
    return {
        "node_id": "aroll:3",
        "node_type": "aroll_assignment",
        "producer": "aroll.assign",
        "label": "A-roll on block 3",
        "intent_refs": ["beat:3"],
        "evidence_refs": ["clip_catalog:clip_001"],
        "constraints": {},
        "depends_on": ["spine_block:3"],
        "payload": {
            "spine_block_position": 3,
            "clip_id": "clip_001",
            "source_start": 10.0,
            "source_end": 14.0,
            "timeline_start": 48.0,
            "timeline_end": 52.0,
        },
    }


def _clocks():
    return {
        "asset_by_clip": {"clip_001": "asset-1"},
        "source_clock_by_asset": {"asset-1": "clock-source"},
        "source_tps_by_asset": {"asset-1": 24},
        "timeline_clock": "clock-timeline",
        "timeline_tps": 24,
    }


def test_edit_graph_node_migrates_with_stable_id_and_lineage():
    first = editorial.edit_graph_node_to_operation(_aroll_node())
    second = editorial.edit_graph_node_to_operation(_aroll_node())

    assert first["id"] == "edit_graph:aroll:3"
    assert first == second
    assert first["operation"] == "edit_graph.aroll_assignment"
    assert first["target_ids"] == ["aroll:3"]
    assert first["after"]["evidence_refs"] == ["clip_catalog:clip_001"]
    # Legacy evidence refs are lineage, never typed observation links.
    assert first["evidence_ids"] == []
    assert first["intent_id"] is None


def test_project_graph_wrapper_converges_on_the_same_id():
    wrapper = {
        "node_id": "edit:aroll:3",
        "node_type": "edit",
        "label": "A-roll on block 3",
        "payload": {
            "edit_node_id": "aroll:3",
            "node_type": "aroll_assignment",
            "producer": "aroll.assign",
            "intent_refs": ["beat:3"],
            "evidence_refs": ["clip_catalog:clip_001"],
            "constraints": {},
            "depends_on": ["spine_block:3"],
            "edit_payload": _aroll_node()["payload"],
        },
    }

    assert (editorial.project_graph_edit_node_to_operation(wrapper)
            == editorial.edit_graph_node_to_operation(_aroll_node()))


def test_model_id_for_legacy_namespaces_without_rehashing():
    assert editorial.model_id_for_legacy("edit", "aroll:3") == "edit:aroll:3"
    assert editorial.model_id_for_legacy("shot", "shot:clip_001") == "shot:clip_001"
    with pytest.raises(editorial.EditorialMigrationError):
        editorial.model_id_for_legacy("", "aroll:3")


def test_aroll_node_becomes_clip_use_on_shared_clocks():
    use, reason = editorial.aroll_node_to_clip_use(_aroll_node(), **_clocks())

    assert reason is None
    assert use["asset_id"] == "asset-1"
    assert use["role"] == "a_roll"
    assert use["source_interval"] == {
        "clock_id": "clock-source", "start": 240, "end": 336}
    assert use["timeline_interval"] == {
        "clock_id": "clock-timeline", "start": 1152, "end": 1248}
    # Identity is Unit 3's to establish; the bridge never guesses it.
    assert use["entity_links"] == []
    assert use["intent_id"] is None


def test_aroll_node_without_an_asset_is_reported_not_guessed():
    use, reason = editorial.aroll_node_to_clip_use(
        _aroll_node(),
        asset_by_clip={},
        source_clock_by_asset={},
        source_tps_by_asset={},
        timeline_clock="clock-timeline",
        timeline_tps=24,
    )

    assert use is None
    assert "no model asset" in reason


def test_aroll_node_with_empty_span_is_refused():
    node = _aroll_node()
    node["payload"]["timeline_end"] = node["payload"]["timeline_start"]

    use, reason = editorial.aroll_node_to_clip_use(node, **_clocks())

    assert use is None
    assert "half-open" in reason


def test_migrate_edit_graph_rebuild_is_idempotent_and_validated():
    graph = {"nodes": [_aroll_node(), {
        "node_id": "transition:4",
        "node_type": "transition",
        "producer": "transitions.resolve",
        "label": "cut at block 4",
        "intent_refs": [],
        "evidence_refs": [],
        "constraints": {},
        "depends_on": [],
        "payload": {"cut_point_position": 4, "transition_type": "cut"},
    }]}
    fragment = editorial.migrate_edit_graph(graph, **_clocks())

    assert [op["id"] for op in fragment["edits"]] == [
        "edit_graph:aroll:3", "edit_graph:transition:4"]
    assert len(fragment["clip_uses"]) == 1
    assert fragment["skipped_clip_uses"] == []

    model = _empty_model()
    once = editorial.apply_editorial_fragment(model, {
        "edits": fragment["edits"], "clip_uses": fragment["clip_uses"]})
    twice = editorial.apply_editorial_fragment(once, {
        "edits": fragment["edits"], "clip_uses": fragment["clip_uses"]})
    assert twice == once
    validate_project_model(twice)
    with pytest.raises(editorial.EditorialMigrationError, match="unknown keys"):
        editorial.apply_editorial_fragment(model, fragment)


def _words_row():
    return {
        "op": "clip_lut",
        "anchor": {"kind": "words", "phrase": "hello world"},
        "params": {"lut": "Film / Rec709.cube", "node": 1},
        "stated_by": "captain",
        "reason": "match the host angle",
    }


def test_ledger_words_row_migrates_without_ticks():
    operation = editorial.ledger_row_to_operation(_words_row())

    assert operation["id"].startswith("ledger:")
    assert operation["operation"] == "edit_ledger.clip_lut"
    assert operation["author"] == "captain"
    assert operation["after"]["anchor"] == {"kind": "words", "phrase": "hello world"}
    assert "interval" not in operation["after"]


def test_ledger_frame_anchor_is_refused_not_promoted():
    row = _words_row()
    row["anchor"] = {"kind": "timeline_frames", "frame": 120}

    with pytest.raises(editorial.EditorialMigrationError, match="not stable"):
        editorial.ledger_row_to_operation(row)


def test_ledger_frame_pin_field_is_refused_not_promoted():
    for poison in ({"timeline_start": 48}, {"frame": 12}):
        row = _words_row()
        row["anchor"] = {"kind": "words", "phrase": "hello world", **poison}
        with pytest.raises(editorial.EditorialMigrationError, match="pin"):
            editorial.ledger_row_to_operation(row)


def test_ledger_migration_is_stable_and_dedupes_identical_rows():
    rows = [_words_row(), copy.deepcopy(_words_row())]
    fragment = editorial.migrate_ledger(rows)

    assert fragment["edits"][0]["id"] == editorial.ledger_row_to_operation(
        _words_row())["id"]
    assert fragment["edits"][0]["id"] != fragment["edits"][1]["id"]
    assert fragment["edits"][1]["id"].endswith("#2")


def _patch_operation():
    return {
        "op": "clip.insert",
        "unique_id": "card:head",
        "track_type": "video",
        "track_index": 1,
        "record_in": 100,
        "record_out": 148,
        "media_pool_item": "pool-1",
        "source_in": 0,
        "source_out": 48,
    }


def _patch():
    return EditPatch(
        id="patch-7",
        project="Podcast",
        timeline="Reel 09",
        base_generation=4,
        capability="reel.build",
        affected_spans=((100, 148),),
        conflict_domains=("timeline_structure",),
        operations=(_patch_operation(),),
        preconditions=(),
        postconditions=(),
    )


def test_patch_migrates_and_round_trips_verbatim():
    operation = editorial.patch_to_operation(_patch())

    assert operation["id"] == "edit_patch:patch-7"
    assert operation["operation"] == "timeline.patch"
    assert operation["parent_revision"] == "4"
    assert operation["after"]["conflict_domains"] == ["timeline_structure"]
    assert operation["after"]["operations"] == [_patch_operation()]

    revived = editorial.operation_to_patch(operation)
    assert revived["id"] == "patch-7"
    assert revived["base_generation"] == 4
    assert revived["affected_spans"] == [[100, 148]]
    assert revived["operations"] == [_patch_operation()]

    intervals = editorial.spans_to_intervals([[100, 148]], "clock-timeline")
    assert intervals == [{"clock_id": "clock-timeline", "start": 100, "end": 148}]


def test_operation_to_patch_refuses_foreign_operations():
    with pytest.raises(editorial.EditorialMigrationError, match="not a migrated patch"):
        editorial.operation_to_patch({"id": "edit_graph:aroll:3",
                                      "operation": "edit_graph.aroll_assignment"})


def _shift():
    return ItemChange(
        track_type="video", track_index=1, item_index=2,
        record_frame=200, duration=48, left_offset=0,
        previous_record=100, previous_duration=48, how="shift",
        comp_count=0, name="clip",
    )


def test_composed_shift_maps_one_to_one_and_carries_frames():
    operation = editorial.composed_change_to_operation(_shift())
    segment = editorial.composed_change_to_segment(
        _shift(), from_clock="clock-timeline", to_clock="clock-timeline")

    assert operation["id"] == "composed:V1:2:100->200x48"
    assert operation["before"] == {
        "record_frame": 100, "duration": 48, "left_offset": 0}
    assert segment == {
        "from": {"clock_id": "clock-timeline", "start": 100, "end": 148},
        "to": {"clock_id": "clock-timeline", "start": 200, "end": 248},
        "mapping_kind": "one_to_one",
        "rate": None,
        "source_clip_use": None,
    }


def test_composed_extend_maps_retimed_with_explicit_rate():
    change = ItemChange(
        track_type="video", track_index=1, item_index=2,
        record_frame=100, duration=72, left_offset=0,
        previous_record=100, previous_duration=48, how="extend",
        comp_count=0, name="clip",
    )
    segment = editorial.composed_change_to_segment(
        change, from_clock="clock-timeline", to_clock="clock-timeline")

    assert segment["mapping_kind"] == "retimed"
    assert segment["rate"] == {"numerator": 72, "denominator": 48}


def test_composed_insertion_becomes_an_operation_never_a_clip_use():
    insertion = Insertion(
        track_type="video", track_index=1, media_pool_item=object(),
        left_offset=0, duration=48, record_frame=300, name="card",
        properties={}, grade_from=None,
    )
    operation = editorial.composed_change_to_operation(insertion)

    assert operation["operation"] == "composed.insert"
    with pytest.raises(editorial.EditorialMigrationError, match="no source interval"):
        editorial.composed_change_to_segment(
            insertion, from_clock="clock-timeline", to_clock="clock-timeline")


def test_composed_undeclared_insertion_is_refused():
    insertion = Insertion(
        track_type="video", track_index=1, media_pool_item=object(),
        left_offset=0, duration=48, record_frame=300, name="card",
        properties=None, grade_from=None,
    )
    with pytest.raises(editorial.EditorialMigrationError, match="no treatment"):
        editorial.composed_change_to_operation(insertion)


def test_intervals_are_half_open_and_frames_stay_ticks():
    with pytest.raises(editorial.EditorialMigrationError, match="half-open"):
        editorial.interval_from_ticks("clock-timeline", 48, 48)
    with pytest.raises(editorial.EditorialMigrationError, match="integers"):
        editorial.interval_from_ticks("clock-timeline", 48.5, 52)
    assert editorial.interval_from_seconds(
        "clock-timeline", 48.0, 52.0, 24) == {
            "clock_id": "clock-timeline", "start": 1152, "end": 1248}


def _empty_model():
    import json
    from pathlib import Path
    model = json.loads(Path(
        "native/ren-protocol/fixtures/project-model.json").read_text(encoding="utf-8"))
    model["edits"] = []
    model["clip_uses"] = []
    return model


def test_e2e_fixture_editorial_content_rebuilds_idempotently():
    """The E2E qualification manifest's own edits/clip uses re-apply clean.

    This is the migration's rebuild proof on a real manifest: feeding the
    fixture's editorial content back through the adapter is a no-op, so a
    rebuilt run converges on the same rows instead of duplicating them.
    """
    import json
    from pathlib import Path
    manifest = json.loads(Path(
        "tests/fixtures/project_model_e2e/expected-project-model.json"
    ).read_text(encoding="utf-8"))
    fragment = {"edits": manifest["edits"], "clip_uses": manifest["clip_uses"]}

    rebuilt = editorial.apply_editorial_fragment(manifest, fragment)

    assert rebuilt == manifest
    validate_project_model(rebuilt)


# ── Pipeline assignments to clip uses (move minting) ──

def _assignment(position=1, clip="clip_001", source=(10.0, 14.0),
                timeline=(48.0, 52.0), fps=24.0, segments=None):
    segment = {"clip_id": clip, "source_file": f"/footage/{clip}.mov",
               "source_start": source[0], "source_end": source[1],
               "frame_rate": fps}
    return {"spine_block_position": position,
            "timeline_start": timeline[0], "timeline_end": timeline[1],
            "video_segments": [segment] if segments is None else segments}


def test_assignments_migrate_through_the_unit_4_functions():
    """The adapter is the migration, not a second id scheme.

    Migrating one assignment by hand through `aroll_node_to_clip_use`
    with the adapter's own names must name the same clip use the
    adapter returns - minted target ids are post-migration stable ids
    by construction, not by matching strings.
    """
    assignment = _assignment()
    adapted = editorial.clip_uses_from_assignments([assignment],
                                                   timeline_tps=24)
    assert adapted["skipped"] == []
    (use,) = adapted["uses_by_block"].values()
    node = {"node_id": "aroll:1", "payload": {
        "clip_id": "clip_001", "source_start": 10.0, "source_end": 14.0,
        "timeline_start": 48.0, "timeline_end": 52.0}}
    direct, reason = editorial.aroll_node_to_clip_use(
        node,
        asset_by_clip={"clip_001": editorial.asset_id_for_clip("clip_001")},
        source_clock_by_asset={
            "asset-clip_001": editorial.source_clock_for_clip("clip_001")},
        source_tps_by_asset={"asset-clip_001": 24},
        timeline_clock=editorial.TIMELINE_CLOCK_ID,
        timeline_tps=24)
    assert reason is None
    assert use == direct
    assert use["timeline_interval"] == {"clock_id": "clock-project",
                                       "start": 1152, "end": 1248}
    assert adapted["clocks"] == [
        {"id": "clock-project", "ticks_per_second": 24},
        {"id": "clock-src-clip_001", "ticks_per_second": 24}]


def test_assignment_skips_name_the_block_and_the_reason():
    adapted = editorial.clip_uses_from_assignments([
        _assignment(position=1),
        dict(_assignment(position=2),
             video_segments=[_assignment()["video_segments"][0],
                             _assignment()["video_segments"][0]]),
        dict(_assignment(position=3), video_segments=[]),
        {"spine_block_position": 4, "timeline_start": 0.0,
         "timeline_end": 1.0,
         "video_segments": [{"clip_id": "clip_004",
                             "source_start": 0.0, "source_end": 1.0}]},
        {"spine_block_position": 5, "timeline_start": 0.0,
         "timeline_end": 1.0,
         "video_segments": [{"clip_id": "", "source_start": 0.0,
                             "source_end": 1.0, "frame_rate": 24.0}]},
    ], timeline_tps=24)
    assert list(adapted["uses_by_block"]) == ["1"]
    reasons = {skip["spine_block_position"]: skip["reason"]
               for skip in adapted["skipped"]}
    assert "2 video segments" in reasons[2]
    assert "no video segment" in reasons[3]
    assert "no measured frame rate" in reasons[4]
    assert "names no clip" in reasons[5]


def test_duplicate_blocks_skip_every_claimant():
    adapted = editorial.clip_uses_from_assignments(
        [_assignment(position=1), _assignment(position=1)],
        timeline_tps=24)
    assert adapted["uses_by_block"] == {}
    assert len(adapted["skipped"]) == 1
    assert "two a-roll assignments" in adapted["skipped"][0]["reason"]


def test_fractional_timeline_rate_mints_nothing_but_resolves():
    """29.97 has no integer tick grid: no uses, one recorded reason,
    and the step that called the adapter resolves unchanged."""
    adapted = editorial.clip_uses_from_assignments([_assignment()],
                                                   timeline_tps=29.97)
    assert adapted["uses_by_block"] == {}
    assert len(adapted["skipped"]) == 1
    assert "no integer tick grid" in adapted["skipped"][0]["reason"]


def test_garbage_timeline_rate_raises():
    with pytest.raises(editorial.EditorialMigrationError):
        editorial.clip_uses_from_assignments([_assignment()],
                                             timeline_tps="fast")
    with pytest.raises(editorial.EditorialMigrationError):
        editorial.clip_uses_from_assignments([_assignment()],
                                             timeline_tps=0)
