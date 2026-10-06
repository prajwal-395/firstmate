"""Tests for note -> implicated-decision implication.

These tests pin behaviour: candidates come only from the note's own
anchors, no judge means unevaluated rather than guessed, a verdict
naming a decision nobody supplied is refused whole, and the proposal
preserves the parent version without writing history. On origin/main
the module does not exist, so every case here fails there.
"""
import json

import pytest

from library.tools import decision_implication as di
from library.tools import project_graph


def _record(speech_clip, reaction_clip, start, end, land,
            with_decision=True):
    record = {
        "speech_block_position": 1,
        "speech_clip_id": speech_clip,
        "speaking_person_id": "person_001",
        "speech_timeline_start": start,
        "speech_timeline_end": end,
        "reaction_clip_id": reaction_clip,
        "reacting_person_id": "person_002",
        "reaction_source_start": 1.0,
        "reaction_source_end": 4.0,
        "timing": {"speech_moment": end, "reaction_land": land,
                   "offset_seconds": 0.0},
        "reason": "person_002 is visible while person_001 speaks",
    }
    if with_decision:
        record["decision"] = {
            "action": f"Use {reaction_clip} as reaction cutaway",
            "reason": record["reason"],
            "evidence": {"speech_shot": f"shot:{speech_clip}",
                         "reaction_shot": f"shot:{reaction_clip}"},
            "constraints": {},
            "style_source": None,
            "confidence": 0.6,
            "confidence_source": "measured span overlap",
        }
    return record


def _graph():
    nodes = [
        {"node_id": "editorial:reaction_cutaway:0",
         "node_type": project_graph.EDITORIAL_FINDING,
         "label": "reaction_cutaway 0",
         "payload": {"kind": project_graph.FINDING_REACTION_CUTAWAY,
                     "record": _record("clip_001", "clip_002",
                                       2.0, 8.0, 8.0)}},
        {"node_id": "editorial:reaction_cutaway:1",
         "node_type": project_graph.EDITORIAL_FINDING,
         "label": "reaction_cutaway 1",
         "payload": {"kind": project_graph.FINDING_REACTION_CUTAWAY,
                     "record": _record("clip_003", "clip_004",
                                       20.0, 30.0, 30.0)}},
        {"node_id": "editorial:reaction_cutaway:2",
         "node_type": project_graph.EDITORIAL_FINDING,
         "label": "reaction_cutaway 2",
         "payload": {"kind": project_graph.FINDING_REACTION_CUTAWAY,
                     "record": _record("clip_005", "clip_006", 40.0, 50.0,
                                       50.0, with_decision=False)}},
        {"node_id": "editorial:dead_section:0",
         "node_type": project_graph.EDITORIAL_FINDING,
         "label": "dead_section 0",
         "payload": {"kind": project_graph.FINDING_DEAD_SECTION,
                     "record": {"clip_id": "clip_002"}}},
    ]
    return {"graph_id": "project_graph", "node_count": len(nodes),
            "edge_count": 0, "nodes": nodes, "edges": []}


def test_candidates_come_only_from_the_note_anchors():
    """Decisions outside the note's clips and times are not supplied -
    an unanchored note gets nothing, never the whole graph."""
    graph = _graph()
    by_clip = di.candidates_for_note(graph, clip_ids=["clip_002"])
    assert [row["node_id"] for row in by_clip] == [
        "editorial:reaction_cutaway:0"]
    by_time = di.candidates_for_note(graph, times=[25.0])
    assert [row["node_id"] for row in by_time] == [
        "editorial:reaction_cutaway:1"]
    assert di.candidates_for_note(graph) == []
    # The decision-less finding and the other kind never qualify.
    anchored = di.candidates_for_note(
        graph, clip_ids=["clip_006", "clip_002"], times=[45.0])
    assert [row["node_id"] for row in anchored] == [
        "editorial:reaction_cutaway:0"]


def test_no_judge_means_unevaluated_never_guessed():
    """Without an injected model every candidate returns unevaluated
    and none is implicated."""
    candidates = di.candidates_for_note(_graph(), clip_ids=["clip_002"])
    verdict = di.judge_candidates("make this more serious", candidates)
    assert verdict["implicated"] == []
    assert verdict["unaffected"] == []
    assert verdict["unevaluated"] == ["editorial:reaction_cutaway:0"]


def test_injected_judge_names_implicated_with_why():
    """The judge's verdict sorts implicated (with its reason kept
    verbatim) from unaffected."""
    candidates = di.candidates_for_note(
        _graph(), clip_ids=["clip_002", "clip_004"])

    def judge(text, rows):
        assert text == "make this more serious"
        assert len(rows) == 2
        return json.dumps({"implicated": [
            {"node_id": "editorial:reaction_cutaway:0",
             "why": "reaction plays the joke the note wants gone"}]})

    verdict = di.judge_candidates("make this more serious", candidates,
                                  judge)
    assert verdict["implicated"] == [
        {"node_id": "editorial:reaction_cutaway:0",
         "why": "reaction plays the joke the note wants gone"}]
    assert verdict["unaffected"] == ["editorial:reaction_cutaway:1"]
    assert verdict["unevaluated"] == []


def test_verdict_naming_an_unsupplied_decision_is_refused():
    """A verdict implicating a decision the graph never supplied, or
    with no reason, is refused whole - never partially applied."""
    candidates = di.candidates_for_note(_graph(), clip_ids=["clip_002"])
    with pytest.raises(di.ImplicationRefused):
        di.judge_candidates(
            "make this more serious", candidates,
            lambda text, rows: json.dumps({"implicated": [
                {"node_id": "editorial:reaction_cutaway:99",
                 "why": "invented"}]}))
    with pytest.raises(di.ImplicationRefused):
        di.judge_candidates(
            "make this more serious", candidates,
            lambda text, rows: json.dumps({"implicated": [
                {"node_id": "editorial:reaction_cutaway:0"}]}))


def test_proposal_preserves_the_parent_and_names_the_new():
    """The proposal carries the implicated rows whole, echoes the
    preserved parent and numbers the new version after it."""
    candidates = di.candidates_for_note(_graph(), clip_ids=["clip_002"])
    verdict = {"implicated": [{"node_id": "editorial:reaction_cutaway:0",
                               "why": "plays the joke"}],
               "unaffected": [], "unevaluated": []}
    proposal = di.propose("note-1", "make this more serious", verdict,
                          candidates, {"final": "reel01", "version": 6})
    assert proposal["proposed_version"] == 7
    assert proposal["parent_version"] == {"final": "reel01", "version": 6}
    assert [row["node_id"] for row in proposal["implicated"]] == [
        "editorial:reaction_cutaway:0"]
    assert proposal["direction"] == "make this more serious"


def test_proposal_without_a_parent_says_so():
    """A reel with no version on record gets no version number - an
    absent parent is absent, never version 0 extended to 1."""
    proposal = di.propose("note-1", "make this more serious",
                          {"implicated": [], "unaffected": [],
                           "unevaluated": []}, [], None)
    assert proposal["proposed_version"] is None
    assert proposal["parent_version"] is None
    assert "nothing to preserve" in proposal["preservation"]


def _project(tmp_path, monkeypatch, manifest):
    """Two clips, a manifest with one decided cutaway, versions at 6."""
    from library.tools.project_layout import ProjectLayout
    from library.tools.versions import reel_versions

    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT",
                       str(tmp_path / "memory"))
    project = tmp_path / "project"
    project.mkdir()
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001",
                 "source_file": "/camA/take1.MXF",
                 "duration_seconds": 30.0},
                {"clip_id": "clip_002",
                 "source_file": "/camB/take1.MXF",
                 "duration_seconds": 30.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": "a" * 64},
            "clip_002": {"content_digest": "b" * 64},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    out = ProjectLayout(str(project)).step_dir(
        "compile_manifest", create=True)
    (out / "assembly_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    versions_path = reel_versions.path_for(str(project))
    import os

    os.makedirs(os.path.dirname(versions_path), exist_ok=True)
    with open(versions_path, "w", encoding="utf-8") as handle:
        json.dump({"format": reel_versions.REEL_VERSIONS_FORMAT,
                   "reels": {"reel01": [{"version": 6,
                                          "kind": "build"}]}},
                  handle)
    return project


def test_cli_propose_reads_history_without_writing_it(tmp_path, monkeypatch,
                                                      capsys):
    """The propose command surfaces the anchored candidate, leaves the
    verdict unevaluated for the harness judge, and preserves version 6
    while proposing 7 - the ledger bytes are unchanged."""
    from library.tools.versions import reel_versions

    manifest = {"reaction_cutaways": [
        _record("clip_001", "clip_002", 2.0, 8.0, 8.0)]}
    project = _project(tmp_path, monkeypatch, manifest)
    ledger_path = reel_versions.path_for(str(project))
    with open(ledger_path, "rb") as handle:
        before = handle.read()

    assert di.main(["propose", "--project", str(project),
                    "--reel", "reel01",
                    "--text", "make this more serious",
                    "--clip", "clip_002"]) == 0
    with open(ledger_path, "rb") as handle:
        assert handle.read() == before

    answer = json.loads(capsys.readouterr().out)
    assert [row["node_id"] for row in answer["candidates"]] == [
        "editorial:reaction_cutaway:0"]
    assert answer["verdict"]["unevaluated"] == [
        "editorial:reaction_cutaway:0"]
    assert answer["proposal"]["proposed_version"] == 7
    assert answer["proposal"]["parent_version"] == {
        "final": "reel01", "version": 6}
