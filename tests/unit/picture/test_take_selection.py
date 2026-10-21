"""Tests for take alternate collection.

These tests pin behaviour: the measured delivery descriptions are
collected for each alternate take, an alternate that is the clip
itself is skipped, and a clip with no alternates gets an empty list.
"""
import pytest

from library.tools import evidence_contract as evidence
from library.tools.evaluation_policy import (
    EvaluationPolicy,
    EvaluationPolicyError,
    PolicyRule,
)
from library.tools.take_selection import (
    collect_take_alternates,
    delivery_descriptions,
    index_semantic_documents,
    rank_take_instances,
)


def _semantic(blocks):
    return {"blocks": blocks}


def _temporal(face_values=None, speech_regions=None):
    idx = {}
    if face_values is not None:
        idx["face_presence"] = {"values": face_values,
                                "sample_rate_hz": 1.0}
    if speech_regions is not None:
        idx["speech_regions"] = speech_regions
    return idx


def _graph(alternates, target_clip="c1"):
    """Build a minimal project graph with shot and transcript nodes."""
    nodes = []
    digest = "d1"
    text = "hello world"
    nodes.append({
        "node_id": f"shot:{target_clip}",
        "node_type": "shot",
        "payload": {"clip_id": target_clip, "content_digest": digest},
    })
    for alt in alternates:
        clip_id = alt["clip_id"]
        nodes.append({
            "node_id": f"shot:{clip_id}",
            "node_type": "shot",
            "payload": {"clip_id": clip_id, "content_digest": digest},
        })
    nodes.append({
        "node_id": "ts:1",
        "node_type": "transcript_segment",
        "payload": {"content_digest": digest, "text": text},
    })
    return {"nodes": nodes, "edges": []}


class TestCollectTakeAlternates:
    def test_delivery_descriptions_collected(self):
        """The measured speech_cue and body_language are collected."""
        graph = _graph([
            {"clip_id": "c2", "reason": "transcript-overlap",
             "shared_words": ["hello"]},
        ])
        semantic = {
            "c2": _semantic([{"speech_cue": "measured pause before key line",
                              "body_language": "smiles, leans forward"}]),
        }
        temporal = {"c2": _temporal(face_values=[0.8] * 10)}
        result = collect_take_alternates("c1", semantic, temporal, graph)
        assert len(result) == 1
        alt = result[0]
        assert alt["clip_id"] == "c2"
        assert alt["speech_cue"] == "measured pause before key line"
        assert alt["body_language"] == "smiles, leans forward"
        assert alt["face_presence"] == 0.8

    def test_alternate_same_as_clip_is_skipped(self):
        """An alternate that is the clip itself is skipped."""
        graph = _graph([
            {"clip_id": "c1", "reason": "transcript-overlap",
             "shared_words": ["hello"]},
        ])
        result = collect_take_alternates("c1", {}, {}, graph)
        assert result == []

    def test_no_alternates_gets_empty_list(self):
        """A clip with no measured alternates gets an empty list."""
        graph = _graph([])
        result = collect_take_alternates("c1", {}, {}, graph)
        assert result == []

    def test_speech_spans_collected(self):
        """The measured speech regions are collected."""
        graph = _graph([
            {"clip_id": "c2", "reason": "transcript-overlap",
             "shared_words": ["hello"]},
        ])
        temporal = {"c2": _temporal(speech_regions=[
            {"start": 0.0, "end": 5.0},
            {"start": 6.0, "end": 8.0},
        ])}
        result = collect_take_alternates("c1", {}, temporal, graph)
        assert len(result) == 1
        assert result[0]["speech_spans"] == [(0.0, 5.0), (6.0, 8.0)]

    def test_unmeasurable_delivery_reported_as_absent(self):
        """An unmeasurable delivery is reported as absent, not defaulted."""
        graph = _graph([
            {"clip_id": "c2", "reason": "transcript-overlap",
             "shared_words": ["hello"]},
        ])
        semantic = {"c2": _semantic([{"speech_cue": None,
                                       "body_language": ""}])}
        result = collect_take_alternates("c1", semantic, {}, graph)
        assert len(result) == 1
        assert result[0]["speech_cue"] is None
        assert result[0]["body_language"] is None
        assert result[0]["face_presence"] is None

    def test_real_step_1_03_shape_is_indexed(self):
        """The raw step 1.03 wrapper shape yields measured descriptions.

        Regression: compile_manifest used to .get(clip_id) on the raw
        {"semantic_analysis_documents": [...]} wrapper, so on a real
        project every alternate read as unmeasured.
        """
        graph = _graph([
            {"clip_id": "c2", "reason": "transcript-overlap",
             "shared_words": ["hello"]},
        ])
        raw = {"semantic_analysis_documents": [
            {"clip_id": "c2", "blocks": [
                {"speech_cue": "measured pause before key line",
                 "body_language": "smiles, leans forward"}]},
        ]}
        result = collect_take_alternates("c1", raw, {}, graph)
        assert len(result) == 1
        assert result[0]["speech_cue"] == "measured pause before key line"
        assert result[0]["body_language"] == "smiles, leans forward"

    def test_index_passes_through_a_map(self):
        """An already-indexed map passes through unchanged."""
        semantic = {"c2": _semantic([{"speech_cue": "brisk"}])}
        assert index_semantic_documents(semantic) == semantic

    def test_index_handles_a_document_list(self):
        """A bare list of documents is indexed by clip_id."""
        raw = [{"clip_id": "c2", "blocks": []}]
        assert list(index_semantic_documents(raw)) == ["c2"]

    def test_coverage_group_keeps_instance_spans_and_repeated_clip(self, monkeypatch):
        from library.tools import project_graph

        comparison = {"group_id": "group-a", "kind": "retake",
                      "same_instant": False, "members": [
            {"clip_id": "c2", "content_digest": "d2",
             "source_start": 1.25, "source_end": 2.75,
             "speech_evidence": {"status": "present"},
             "picture_evidence": {"status": "available"},
             "audio_evidence": {"status": "available"}},
            {"clip_id": "c2", "content_digest": "d2",
             "source_start": 4.0, "source_end": 5.0,
             "speech_evidence": {"status": "present"},
             "picture_evidence": {"status": "available"},
             "audio_evidence": {"status": "available"}},
        ]}
        monkeypatch.setattr(project_graph, "find_take_comparisons",
                            lambda _graph, _clip: [comparison])

        result = collect_take_alternates("c1", {}, {}, {})

        assert [row["source_start"] for row in result] == [1.25, 4.0]
        assert result[0]["instance_id"] != result[1]["instance_id"]
        assert result[0]["take_relation"] == "retake"


class TestDeliveryDescriptions:
    """Per-clip delivery descriptions: measured or None, never defaulted."""

    def test_measured_descriptions_collected_per_clip(self):
        raw = {"semantic_analysis_documents": [
            {"clip_id": "c1", "blocks": [
                {"speech_cue": "brisk, no pauses",
                 "body_language": "still"}]},
            {"clip_id": "c2", "blocks": [
                {"speech_cue": "measured pause before key line",
                 "body_language": "smiles, leans forward"}]},
        ]}
        assert delivery_descriptions(raw) == {
            "c1": {"speech_cue": "brisk, no pauses",
                   "body_language": "still"},
            "c2": {"speech_cue": "measured pause before key line",
                   "body_language": "smiles, leans forward"},
        }

    def test_absent_description_is_none_not_empty(self):
        """A clip the vision pass never described reads None per field."""
        raw = {"c1": _semantic([{"speech_cue": None,
                                 "body_language": ""}])}
        assert delivery_descriptions(raw) == {
            "c1": {"speech_cue": None, "body_language": None},
        }

    def test_accepts_an_indexed_map(self):
        semantic = {"c1": _semantic([{"speech_cue": "brisk"}])}
        descriptions = delivery_descriptions(semantic)
        assert descriptions["c1"]["speech_cue"] == "brisk"
        assert descriptions["c1"]["body_language"] is None


def test_rank_uses_explicit_policy_and_preserves_duplicate_instances():
    policy = EvaluationPolicy(
        id="take-objective", version=1, project_id="project-a",
        preferences=(PolicyRule(
            id="complete-thought", kind="preference",
            measurement_type="speech.completeness", operator="eq",
            expected=True, origin="user", origin_ref="brief.md#complete"),))
    comparison = {"group_id": "group-a", "members": [
        {"clip_id": "same-clip", "content_digest": "digest-a",
         "source_start": 1.0, "source_end": 2.0},
        {"clip_id": "same-clip", "content_digest": "digest-a",
         "source_start": 3.0, "source_end": 4.0},
    ]}
    measurements = {}
    for index, complete in enumerate((False, True)):
        instance = f"group-a:{index}:digest-a:{index * 2 + 1}.0:{index * 2 + 2}.0"
        measurements[instance] = {f"m{index}": evidence.new_measurement(
            asset_id=f"asset-{index}", clock_id=f"source-{index}",
            start_tick=(index * 2 + 1) * 1000,
            end_tick=(index * 2 + 2) * 1000,
            measurement_type="speech.completeness", value=complete,
            method="fi13-transcript-v1", provenance_id=f"m1-{index}",
            references=({"kind": "stream",
                         "stream_id": f"stream-{index}"},))}

    ranked = rank_take_instances(
        [comparison], policy=policy, project_id="project-a",
        measurements_by_instance=measurements, provenance_id="selection-run")

    assert len(ranked) == 2
    assert ranked[0]["source_start"] == 3.0
    assert ranked[0]["policy_id"] == "take-objective@v1"
    assert ranked[0]["evaluation_evidence"][0].basis_measurement_ids == ("m1",)
    assert ranked[0]["evaluation_evidence"][0].basis_rule_ids == ("complete-thought",)


def test_rank_refuses_implicit_objective_and_unmeasured_take():
    with pytest.raises(EvaluationPolicyError, match="explicit evaluation policy"):
        rank_take_instances([], policy=None, project_id="project-a",
                            measurements_by_instance={}, provenance_id="run")
    policy = EvaluationPolicy(
        id="objective", version=1, project_id="project-a",
        preferences=(PolicyRule(
            id="complete", kind="preference",
            measurement_type="speech.completeness", operator="eq",
            expected=True, origin="user", origin_ref="brief.md"),))
    with pytest.raises(EvaluationPolicyError, match="no typed measurements"):
        rank_take_instances(
            [{"group_id": "group", "members": [{
                "clip_id": "c2", "content_digest": "d2",
                "source_start": 1.0, "source_end": 2.0}]}],
            policy=policy, project_id="project-a",
            measurements_by_instance={}, provenance_id="run")
