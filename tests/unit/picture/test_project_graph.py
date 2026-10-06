"""The Ren Project Graph: a persistent relational model of the project.

Every test names a behavioral defect the graph could have: a reaction
shot that is not found, an alternate take that is missed, a coverage
query that returns the wrong shots, a B-roll match that is not linked.
No test asserts a schema or count.
"""
import json
from pathlib import Path

import pytest

from library.tools import project_graph


def _person(person_id, name=None, face_spans=None, voice_spans=None,
             speech_face_links=None):
    return {
        "node_id": person_id,
        "node_type": project_graph.PERSON,
        "label": name or person_id,
        "payload": {
            "name": name,
            "face_spans": face_spans or [],
            "voice_spans": voice_spans or [],
            "speech_face_links": speech_face_links or [],
        },
    }


def _shot(clip_id, source_file=None, content_digest=None):
    return {
        "node_id": f"shot:{clip_id}",
        "node_type": project_graph.SHOT,
        "label": source_file or clip_id,
        "payload": {
            "clip_id": clip_id,
            "source_file": source_file,
            "content_digest": content_digest,
        },
    }


def _transcript_segment(node_id, digest, t, text="hello"):
    return {
        "node_id": node_id,
        "node_type": project_graph.TRANSCRIPT_SEGMENT,
        "label": f"speech at {t}s",
        "payload": {
            "content_digest": digest,
            "t": t,
            "text": text,
            "speaker": {},
        },
    }


def _edit_node(node_id, node_type, edit_payload=None, intent_refs=None):
    return {
        "node_id": node_id,
        "node_type": project_graph.EDIT,
        "label": node_id,
        "payload": {
            "edit_node_id": node_id,
            "node_type": node_type,
            "edit_payload": edit_payload or {},
            "intent_refs": intent_refs or [],
        },
    }


def _edge(from_id, to_id, rel_type, payload=None):
    return {
        "from": from_id,
        "to": to_id,
        "type": rel_type,
        "payload": payload or {},
    }


# ── find_reaction_shots ───────────────────────────────────────────


def test_a_reaction_shot_is_found_when_another_person_appears_while_the_first_speaks():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _person(
        "person_001", "Alice",
        voice_spans=[{"clip_id": "clip_001", "start": 5.0, "end": 10.0}],
    ))
    project_graph.add_node(graph, _person(
        "person_002", "Bob",
        face_spans=[{"clip_id": "clip_002", "start": 6.0, "end": 9.0}],
    ))
    project_graph.add_node(graph, _shot("clip_001"))
    project_graph.add_node(graph, _shot("clip_002"))
    project_graph.add_edge(graph, _edge(
        "person_001", "shot:clip_001", project_graph.SPEAKS_IN,
        {"start": 5.0, "end": 10.0}))
    project_graph.add_edge(graph, _edge(
        "person_002", "shot:clip_002", project_graph.APPEARS_IN,
        {"start": 6.0, "end": 9.0}))

    results = project_graph.find_reaction_shots(graph, "person_001", 7.0)

    assert len(results) == 1
    assert results[0]["shot_id"] == "shot:clip_002"
    assert results[0]["person_id"] == "person_002"


def test_a_reaction_shot_is_not_found_when_the_other_person_appears_at_a_different_time():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _person(
        "person_001", "Alice",
        voice_spans=[{"clip_id": "clip_001", "start": 5.0, "end": 10.0}],
    ))
    project_graph.add_node(graph, _person(
        "person_002", "Bob",
        face_spans=[{"clip_id": "clip_002", "start": 15.0, "end": 20.0}],
    ))
    project_graph.add_node(graph, _shot("clip_001"))
    project_graph.add_node(graph, _shot("clip_002"))
    project_graph.add_edge(graph, _edge(
        "person_001", "shot:clip_001", project_graph.SPEAKS_IN,
        {"start": 5.0, "end": 10.0}))
    project_graph.add_edge(graph, _edge(
        "person_002", "shot:clip_002", project_graph.APPEARS_IN,
        {"start": 15.0, "end": 20.0}))

    results = project_graph.find_reaction_shots(graph, "person_001", 7.0)

    assert results == []


def test_a_reaction_shot_is_not_found_when_the_same_person_appears():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _person(
        "person_001", "Alice",
        voice_spans=[{"clip_id": "clip_001", "start": 5.0, "end": 10.0}],
        face_spans=[{"clip_id": "clip_002", "start": 6.0, "end": 9.0}],
    ))
    project_graph.add_node(graph, _shot("clip_001"))
    project_graph.add_node(graph, _shot("clip_002"))
    project_graph.add_edge(graph, _edge(
        "person_001", "shot:clip_001", project_graph.SPEAKS_IN,
        {"start": 5.0, "end": 10.0}))
    project_graph.add_edge(graph, _edge(
        "person_001", "shot:clip_002", project_graph.APPEARS_IN,
        {"start": 6.0, "end": 9.0}))

    results = project_graph.find_reaction_shots(graph, "person_001", 7.0)

    assert results == []


# ── find_alternate_takes ─────────────────────────────────────────


def test_an_alternate_take_is_found_when_two_shots_share_transcript_words():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot(
        "clip_001", "/camA/take1.MXF", "digest_abc"))
    project_graph.add_node(graph, _shot(
        "clip_002", "/camB/take1.MXF", "digest_def"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:0", "digest_abc", 0, "hello world from camA"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:def:0", "digest_def", 0, "hello world from camB"))

    results = project_graph.find_alternate_takes(graph, "clip_001")

    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"
    assert results[0]["reason"] == "transcript-overlap"


def test_a_same_digest_shot_is_a_duplicate_not_an_alternate():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot(
        "clip_001", "/camA/take1.MXF", "digest_abc"))
    project_graph.add_node(graph, _shot(
        "clip_002", "/camA/take2.MXF", "digest_abc"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:0", "digest_abc", 0, "hello world"))

    results = project_graph.find_alternate_takes(graph, "clip_001")

    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"
    assert results[0]["reason"] == "same-digest"


def test_a_same_file_shot_is_a_duplicate_not_an_alternate():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot(
        "clip_001", "/camA/take1.MXF", "digest_abc"))
    project_graph.add_node(graph, _shot(
        "clip_002", "/camA/take1.MXF", "digest_def"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:0", "digest_abc", 0, "hello world"))

    results = project_graph.find_alternate_takes(graph, "clip_001")

    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"
    assert results[0]["reason"] == "same-file"


def test_no_alternate_take_is_found_when_shots_have_different_transcripts():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot(
        "clip_001", "/camA/take1.MXF", "digest_abc"))
    project_graph.add_node(graph, _shot(
        "clip_002", "/camB/take1.MXF", "digest_def"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:0", "digest_abc", 0, "hello world"))
    project_graph.add_node(graph, _transcript_segment(
        "transcript:def:0", "digest_def", 0, "goodbye moon"))

    results = project_graph.find_alternate_takes(graph, "clip_001")

    assert results == []


def test_no_alternate_take_is_found_for_an_unknown_clip():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot("clip_001"))

    results = project_graph.find_alternate_takes(graph, "clip_999")

    assert results == []


# ── find_coverage ────────────────────────────────────────────────


def test_coverage_returns_shots_whose_blocks_overlap_the_query_span():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:1", "spine_block",
        edit_payload={
            "position": 1, "clip_id": "clip_001",
            "timeline_start": 0.0, "timeline_end": 5.0,
        }))
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:2", "spine_block",
        edit_payload={
            "position": 2, "clip_id": "clip_002",
            "timeline_start": 5.0, "timeline_end": 10.0,
        }))
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:3", "spine_block",
        edit_payload={
            "position": 3, "clip_id": "clip_003",
            "timeline_start": 10.0, "timeline_end": 15.0,
        }))

    results = project_graph.find_coverage(graph, 3.0, 7.0)

    assert len(results) == 2
    clip_ids = {r["clip_id"] for r in results}
    assert clip_ids == {"clip_001", "clip_002"}


def test_coverage_returns_no_shots_when_no_block_overlaps():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:1", "spine_block",
        edit_payload={
            "position": 1, "clip_id": "clip_001",
            "timeline_start": 0.0, "timeline_end": 5.0,
        }))

    results = project_graph.find_coverage(graph, 10.0, 15.0)

    assert results == []


# ── find_broll_matches ───────────────────────────────────────────


def test_a_broll_match_is_found_when_a_broll_covers_a_block_playing_the_segment():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:5", "abc", 5, "hello world"))
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:1", "spine_block",
        edit_payload={
            "position": 1, "clip_id": "clip_001",
            "timeline_start": 0.0, "timeline_end": 5.0,
        }))
    project_graph.add_node(graph, _edit_node(
        "edit:broll:1", "broll_cover",
        edit_payload={
            "spine_block_position": 1, "clip_id": "clip_002",
            "timeline_start": 0.0, "timeline_end": 5.0,
        }))

    results = project_graph.find_broll_matches(graph, "transcript:abc:5")

    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"
    assert results[0]["covers_block"] == 1


def test_no_broll_match_is_found_when_no_broll_covers_the_block():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _transcript_segment(
        "transcript:abc:5", "abc", 5, "hello world"))
    project_graph.add_node(graph, _edit_node(
        "edit:spine_block:1", "spine_block",
        edit_payload={
            "position": 1, "clip_id": "clip_001",
            "timeline_start": 0.0, "timeline_end": 5.0,
        }))

    results = project_graph.find_broll_matches(graph, "transcript:abc:5")

    assert results == []


def test_no_broll_match_is_found_for_an_unknown_segment():
    graph = project_graph.new_graph()

    results = project_graph.find_broll_matches(graph, "transcript:xyz:99")

    assert results == []


# ── build_graph ──────────────────────────────────────────────────


def test_build_graph_produces_nodes_and_edges_from_a_small_project(
        tmp_path, monkeypatch):
    """A small project's graph: two shots, two persons, a transcript
    segment, an edit decision and the edges between them."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    digest_b = "b" * 64

    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
                {"clip_id": "clip_002", "source_file": "/camB/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
            "clip_002": {"content_digest": digest_b},
        },
        "intent_model": {
            "beats": [
                {"beat_id": 1, "purpose": "setup", "span": {
                    "clip_id": "clip_001", "source_start": 0.0,
                    "source_end": 5.0}},
            ],
            "key_moments": [],
            "claims": [],
            "pacing_curve": [],
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [
            {"t": 0, "speech": {"words": [
                {"word": "hello", "start": 0.1, "end": 0.5},
            ], "speaker": {"face_track": "face_001", "voice_track": "voice_001"}},
             "faces": [{"t": 0.0, "track_id": "face_001", "box": [0, 0, 100, 100]}],
             "scene": None, "sound": {"events": []},
             "events": {"on_screen": [], "speaking": [], "hand_near_mouth": []},
             "relationships": {"holding": [], "pointing": []}},
        ],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_world_model(memory_root, digest_b, {
        "timeline": [
            {"t": 0, "speech": {"words": [], "speaker": None},
             "faces": [{"t": 0.0, "track_id": "face_002", "box": [0, 0, 100, 100]}],
             "scene": None, "sound": {"events": []},
             "events": {"on_screen": [], "speaking": [], "hand_near_mouth": []},
             "relationships": {"holding": [], "pointing": []}},
        ],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })

    _write_identity(memory_root, digest_a, {
        "faces": [{"track_id": "face_001", "spans": [{"start": 0.0, "end": 5.0}],
                   "embedding": [0.1] * 512}],
        "voices": [{"track_id": "voice_001", "spans": [[0.0, 5.0]]}],
        "speech_face_links": [{"face_track": "face_001", "voice_track": "voice_001"}],
    })
    _write_identity(memory_root, digest_b, {
        "faces": [{"track_id": "face_002", "spans": [{"start": 0.0, "end": 5.0}],
                   "embedding": [0.9] * 512}],
        "voices": [],
        "speech_face_links": [],
    })

    graph = project_graph.build_graph(str(project))

    shot_ids = {n["node_id"] for n in graph["nodes"]
                if n["node_type"] == project_graph.SHOT}
    assert "shot:clip_001" in shot_ids
    assert "shot:clip_002" in shot_ids

    person_ids = {n["node_id"] for n in graph["nodes"]
                  if n["node_type"] == project_graph.PERSON}
    assert len(person_ids) >= 1

    intent_ids = {n["node_id"] for n in graph["nodes"]
                  if n["node_type"] == project_graph.REFERENCE}
    assert "intent:beat:1" in intent_ids

    speaks_edges = project_graph.edges_of_type(graph, project_graph.SPEAKS_IN)
    assert len(speaks_edges) >= 1


def test_build_graph_with_no_state_produces_an_empty_graph(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    graph = project_graph.build_graph(str(project))

    assert graph["node_count"] == 0
    assert graph["edge_count"] == 0


def test_room_tone_dropouts_are_registered_as_audio_events(
        tmp_path, monkeypatch):
    """The audio lane's outputs reach the graph: 5.02's measured
    room-tone dropouts are registered as audio events keyed on the
    source's own digest. A project that has not run the mix carries
    none - the graph builds from whatever exists."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "content_digest": digest_a, "duration_seconds": 10.0},
            ]},
            "audio_mix": {"audio_mix_spec": {"room_tone_continuity": {
                "sources": [{
                    "source_file": "/camA/take1.MXF",
                    "floor": {"level_dbfs": -42.0},
                    "dropouts": [
                        {"start": 2.5, "end": 3.0, "duration": 0.5},
                    ],
                }],
            }}},
        },
        "source_fingerprints": {"clip_001": {"content_digest": digest_a}},
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    dropout_events = [n for n in graph["nodes"]
                      if n["label"] == "room_tone_dropout"]
    assert len(dropout_events) == 1
    assert dropout_events[0]["payload"]["content_digest"] == digest_a
    assert dropout_events[0]["payload"]["start"] == 2.5
    assert dropout_events[0]["payload"]["end"] == 3.0
    assert dropout_events[0]["payload"]["unit"] == "source_seconds"


# ── load_or_build persistence ─────────────────────────────────────


def test_load_or_build_caches_the_graph_and_reuses_it_when_inputs_unchanged(
        tmp_path, monkeypatch):
    """A second call with unchanged inputs reuses the cached graph
    without rebuilding - the fingerprint matches."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    graph1 = project_graph.load_or_build(str(project))
    assert graph1["node_count"] > 0

    cache_path = project / project_graph.GRAPH_FILE_NAME
    assert cache_path.is_file()

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["input_fingerprint"]
    assert cached["graph"]["node_count"] == graph1["node_count"]

    graph2 = project_graph.load_or_build(str(project))
    assert graph2["node_count"] == graph1["node_count"]


def test_load_or_build_rebuilds_when_an_input_changes(
        tmp_path, monkeypatch):
    """A changed input (new clip in the catalog) changes the
    fingerprint and triggers a rebuild."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    graph1 = project_graph.load_or_build(str(project))
    assert graph1["node_count"] > 0

    state["capability_outputs"]["footage.catalog"]["clip_catalog"].append(
        {"clip_id": "clip_002", "source_file": "/camB/take1.MXF",
         "duration_seconds": 8.0})
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph2 = project_graph.load_or_build(str(project))
    assert graph2["node_count"] > graph1["node_count"]


# ── The colour registration ─────────────────────────────────────


def _colour_state(per_clip_adjustments=None, clip_exposure=None,
                  camera_match=None):
    """A colour track's recorded output, as the grade step leaves it."""
    record = {}
    if clip_exposure is not None:
        record["clip_exposure"] = clip_exposure
    if per_clip_adjustments is not None:
        record["color_grade_spec"] = {
            "per_clip_adjustments": per_clip_adjustments,
        }
    if camera_match is not None:
        record["camera_match"] = camera_match
    return {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0},
            {"clip_id": "clip_002", "source_file": "/camB/take1.MXF",
             "duration_seconds": 10.0},
        ]},
        "color_grade.resolve": record,
    }}


def test_build_graph_registers_the_colour_measurements_on_shot_nodes(
        tmp_path):
    """The colour track's three readings land on the shot nodes: the
    measured row (exposure, white balance, contrast, saturation, skin
    vector), the delivered grade, and the camera match against the
    scene's reference. Other tracks read them through find_shot_colour
    without re-measuring the footage."""
    project = tmp_path / "project"
    project.mkdir()
    state = _colour_state(
        clip_exposure=[
            {"clip_id": "clip_001", "luma": 145.5, "contrast": 42.0,
             "saturation": 0.31, "skin_rgb": [180, 150, 130],
             "neutral_rb": 1.02, "neutral_gb": 0.99},
            {"clip_id": "clip_002", "luma": 53.1, "contrast": 38.0,
             "saturation": 0.28, "skin_rgb": [170, 140, 120],
             "neutral_rb": 0.97, "neutral_gb": 1.01},
        ],
        per_clip_adjustments=[
            {"clip_id": "clip_001", "correction_terms": ["exposure_stops"],
             "cdl_values": {"slope_r": 0.87, "slope_g": 0.9, "slope_b": 0.95,
                            "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
                            "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
                            "saturation": 1.0}},
        ],
        camera_match={"matches": [
            {"camera": "camB", "clips": ["clip_002"],
             "saturation_factor": 1.02, "skin_slope": [1.03, 1.0, 0.97],
             "contrast_ratio": 1.1, "gap_direction": "cools"},
        ]},
    )
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    shot_1 = project_graph.node(graph, "shot:clip_001")
    colour_1 = shot_1["payload"]["colour"]
    assert colour_1["measurement"]["luma"] == 145.5
    assert colour_1["measurement"]["saturation"] == 0.31
    assert colour_1["grade"]["correction_terms"] == ["exposure_stops"]
    assert colour_1["grade"]["cdl_values"]["slope_r"] == 0.87
    # clip_001 is the reference camera: it has no match row of its own.
    assert colour_1["match"] is None

    shot_2 = project_graph.node(graph, "shot:clip_002")
    colour_2 = shot_2["payload"]["colour"]
    assert colour_2["measurement"]["luma"] == 53.1
    assert colour_2["match"]["saturation_factor"] == 1.02
    assert colour_2["match"]["gap_direction"] == "cools"
    # The colourist left clip_002 alone: no grade entry exists.
    assert colour_2["grade"] is None


def test_a_shot_with_no_colour_registration_carries_no_colour_key(
        tmp_path):
    """A project the colour track never ran has no colour payload.
    An absent measurement is absent, never a default."""
    project = tmp_path / "project"
    project.mkdir()
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0},
        ]},
    }}
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    shot = project_graph.node(graph, "shot:clip_001")
    assert "colour" not in shot["payload"]


def test_find_shot_colour_returns_the_registered_record_and_none_for_an_unregistered_shot(
        tmp_path):
    """The query other tracks call. A registered shot returns its three
    readings; an unregistered one returns None - the query does not
    invent a measurement."""
    project = tmp_path / "project"
    project.mkdir()
    state = _colour_state(
        clip_exposure=[
            {"clip_id": "clip_001", "luma": 145.5, "contrast": 42.0,
             "saturation": 0.31, "skin_rgb": [180, 150, 130]},
        ],
    )
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    colour = project_graph.find_shot_colour(graph, "clip_001")
    assert colour["measurement"]["luma"] == 145.5
    assert project_graph.find_shot_colour(graph, "clip_002") is None
    assert project_graph.find_shot_colour(graph, "clip_999") is None


def test_the_cache_rebuilds_when_the_colour_output_changes(tmp_path):
    """The colour output is a graph input: a re-grade changes the shot
    payloads, so the cached graph must not survive it. Before the
    fingerprint covered the colour record, a re-grade left every
    consumer reading the stale graph."""
    project = tmp_path / "project"
    project.mkdir()
    state = _colour_state(
        clip_exposure=[
            {"clip_id": "clip_001", "luma": 145.5, "contrast": 42.0,
             "saturation": 0.31, "skin_rgb": [180, 150, 130]},
        ],
    )
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph1 = project_graph.load_or_build(str(project))
    assert project_graph.find_shot_colour(
        graph1, "clip_001")["measurement"]["luma"] == 145.5

    state["capability_outputs"]["color_grade.resolve"][
        "clip_exposure"][0]["luma"] = 60.0
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    graph2 = project_graph.load_or_build(str(project))
    assert project_graph.find_shot_colour(
        graph2, "clip_001")["measurement"]["luma"] == 60.0


# ── Analysis nodes ────────────────────────────────────────────────


def test_colour_readings_are_registered_from_temporal_index(
        tmp_path, monkeypatch):
    """A clip's colour_curves in the temporal index become a
    colour_reading node in the graph."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    idx_dir = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
               / "index")
    idx_dir.mkdir(parents=True)
    (idx_dir / "clip_001.json").write_text(json.dumps({
        "source_file": "/camA/take1.MXF",
        "color_curves": {
            "hue_values": [120, 125, 130],
            "brightness_values": [0.5, 0.6, 0.7],
            "temperature_values": [5500, 5600, 5700],
        },
        "onset_classes": [{"time": 1.0, "kind": "strong"}],
        "unusable_ranges": [{"start": 5.0, "end": 7.0}],
    }), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    colour_nodes = [n for n in graph["nodes"]
                    if n["node_type"] == project_graph.COLOUR_READING]
    assert len(colour_nodes) == 1
    assert colour_nodes[0]["payload"]["clip_id"] == "clip_001"
    assert colour_nodes[0]["payload"]["hue_values"] == [120, 125, 130]


def test_onset_classes_are_registered_from_temporal_index(
        tmp_path, monkeypatch):
    """A clip's onset_classes in the temporal index become an
    onset_class node in the graph."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    idx_dir = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
               / "index")
    idx_dir.mkdir(parents=True)
    (idx_dir / "clip_001.json").write_text(json.dumps({
        "source_file": "/camA/take1.MXF",
        "onset_classes": [{"time": 1.0, "kind": "strong"},
                          {"time": 3.0, "kind": "weak"}],
    }), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    onset_nodes = [n for n in graph["nodes"]
                   if n["node_type"] == project_graph.ONSET_CLASS]
    assert len(onset_nodes) == 1
    assert onset_nodes[0]["payload"]["clip_id"] == "clip_001"
    assert len(onset_nodes[0]["payload"]["onsets"]) == 2


def test_dead_sections_are_registered_from_temporal_index(
        tmp_path, monkeypatch):
    """A clip's unusable_ranges in the temporal index become a
    dead_section node in the graph."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    idx_dir = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
               / "index")
    idx_dir.mkdir(parents=True)
    (idx_dir / "clip_001.json").write_text(json.dumps({
        "source_file": "/camA/take1.MXF",
        "unusable_ranges": [{"start": 5.0, "end": 7.0}],
    }), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    dead_nodes = [n for n in graph["nodes"]
                  if n["node_type"] == project_graph.DEAD_SECTION]
    assert len(dead_nodes) == 1
    assert dead_nodes[0]["payload"]["clip_id"] == "clip_001"
    assert dead_nodes[0]["payload"]["unusable_ranges"] == [
        {"start": 5.0, "end": 7.0}]


def test_vfx_detections_are_registered_from_edit_graph(
        tmp_path, monkeypatch):
    """VFX nodes in the edit graph become vfx_detection nodes in the
    project graph."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
            "vfx.resolve": {"enhancement_spec": [
                {"position": 1, "clip_id": "clip_001",
                 "effect": "stabilization"},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    graph = project_graph.build_graph(str(project))

    vfx_nodes = [n for n in graph["nodes"]
                 if n["node_type"] == project_graph.VFX_DETECTION]
    assert len(vfx_nodes) == 1
    assert vfx_nodes[0]["payload"]["clip_id"] == "clip_001"
    assert vfx_nodes[0]["payload"]["effect"] == "stabilization"


# ── Version nodes ─────────────────────────────────────────────────


def test_version_nodes_are_registered_from_reel_versions_ledger(
        tmp_path, monkeypatch):
    """Each version in the reel versions ledger becomes a version
    node in the graph."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 10.0},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    _write_world_model(memory_root, digest_a, {
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })

    review_dir = project / "pipeline_output" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "reel_versions.json").write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {
            "reel_001": [
                {"version": 1, "kind": "build", "at": "2026-10-06T10:00:00+00:00",
                 "rows": {}, "rows_digest": "abc123"},
                {"version": 2, "kind": "touch", "at": "2026-10-06T11:00:00+00:00",
                 "rows": {}, "rows_digest": "def456"},
            ],
        },
    }), encoding="utf-8")

    graph = project_graph.build_graph(str(project))

    version_nodes = [n for n in graph["nodes"]
                     if n["node_type"] == project_graph.VERSION]
    assert len(version_nodes) == 2
    assert version_nodes[0]["payload"]["final"] == "reel_001"
    assert version_nodes[0]["payload"]["version"] == 1
    assert version_nodes[0]["payload"]["kind"] == "build"
    assert version_nodes[1]["payload"]["version"] == 2


# ── Fixtures ─────────────────────────────────────────────────────


def _write_world_model(memory_root: Path, digest: str, model: dict):
    path = memory_root / digest / "world_model.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model), encoding="utf-8")


def _write_identity(memory_root: Path, digest: str, identity: dict):
    path = memory_root / digest / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity), encoding="utf-8")


# ── Editorial finding nodes ───────────────────────────────────────
#
# The compile reports dead sections, trim proposals, J/L placements
# with the plan's decision on each, and selected reaction cutaways.
# The defect: those findings lived only in assembly_manifest.json, so
# the event log and the version work had no node to reference - every
# consumer re-described the same timeline span in its own words.
#


def _write_manifest(project: Path, manifest: dict):
    from library.tools.project_layout import ProjectLayout
    out_dir = ProjectLayout(str(project)).step_dir(
        "compile_manifest", create=True)
    (out_dir / "assembly_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")


def _editorial_project(tmp_path, monkeypatch, manifest):
    """Two clips, a three-block spine, one person, and a manifest."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()

    digest_a = "a" * 64
    digest_b = "b" * 64
    state = {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 30.0},
                {"clip_id": "clip_002", "source_file": "/camB/take1.MXF",
                 "duration_seconds": 30.0},
            ]},
            "spine.mesh": {"audio_spine": {"structure": [
                {"position": 1, "block_type": "body", "clip_id": "clip_001",
                 "timeline_start": 0.0, "timeline_end": 10.0},
                {"position": 2, "block_type": "body", "clip_id": "clip_001",
                 "timeline_start": 10.0, "timeline_end": 20.0},
                {"position": 3, "block_type": "body", "clip_id": "clip_002",
                 "timeline_start": 20.0, "timeline_end": 30.0},
            ]}},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest_a},
            "clip_002": {"content_digest": digest_b},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    for digest in (digest_a, digest_b):
        _write_world_model(memory_root, digest, {
            "timeline": [],
            "entities": {"face_tracks": [], "voice_tracks": []},
            "multicam": None,
        })
    _write_identity(memory_root, digest_a, {
        "faces": [], "voices": [], "speech_face_links": [],
    })
    _write_identity(memory_root, digest_b, {
        "faces": [{"track_id": "face_002",
                   "spans": [{"start": 0.0, "end": 5.0}],
                   "embedding": [0.9] * 512}],
        "voices": [{"track_id": "voice_002", "spans": [[0.0, 5.0]]}],
        "speech_face_links": [{"face_track": "face_002",
                               "voice_track": "voice_002"}],
    })

    _write_manifest(project, manifest)
    return project


def _editorial_manifest():
    return {
        "dead_sections": [
            {"timeline_start": 14.0, "timeline_end": 17.5,
             "duration_seconds": 3.5, "block_position": 2,
             "clip_id": "clip_001",
             "reasons": ["no speech", "static picture"]},
        ],
        "dead_section_trims": [
            {"block_position": 2, "trim_edge": "tail", "trim_seconds": 3.5},
        ],
        "jl_placements": [
            {"cut_point_position": 3, "kind": "j_cut",
             "offset_seconds": 0.42, "timeline_position": 20.0},
        ],
        "jl_placement_results": [
            {"cut_point_position": 3, "kind": "j_cut",
             "measured_offset_seconds": 0.42,
             "chosen_offset_seconds": 0.3, "status": "chosen"},
        ],
        "reaction_cutaways": [
            {"speech_block_position": 1, "speech_clip_id": "clip_001",
             "speaking_person_id": "person_001",
             "speech_timeline_start": 2.0, "speech_timeline_end": 8.0,
             "reaction_clip_id": "clip_002",
             "reacting_person_id": "person_001",
             "reaction_source_start": 1.0, "reaction_source_end": 4.0,
             "timing": {"speech_moment": 8.0, "reaction_land": 8.0,
                        "offset_seconds": 0.0},
             "reason": "person_001 is visible in clip_002 while speaking"},
        ],
    }


def _finding_nodes(graph, kind):
    return [n for n in graph["nodes"]
            if n["node_type"] == project_graph.EDITORIAL_FINDING
            and n["payload"].get("kind") == kind]


def test_compile_dead_sections_become_nodes_covering_their_clip(
        tmp_path, monkeypatch):
    """A dead section the compile reported is a node carrying the span
    and reasons verbatim, linked to the shot it names. On origin/main
    the manifest's findings reach no node at all."""
    project = _editorial_project(
        tmp_path, monkeypatch, _editorial_manifest())

    graph = project_graph.build_graph(str(project))

    nodes = _finding_nodes(graph, "dead_section")
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "editorial:dead_section:0"
    record = nodes[0]["payload"]["record"]
    assert record["timeline_start"] == 14.0
    assert record["timeline_end"] == 17.5
    assert record["reasons"] == ["no speech", "static picture"]
    covers = [e for e in project_graph.edges_from(graph, nodes[0]["node_id"])
              if e["type"] == project_graph.COVERS]
    assert [e["to"] for e in covers] == ["shot:clip_001"]


def test_jl_placement_resolves_its_block_to_the_clip_playing_there(
        tmp_path, monkeypatch):
    """A J/L placement names a block position, not a clip: the node
    links to the clip the spine plays at that position, and the
    decision node carries the plan's chosen offset beside the measured
    bound. On origin/main neither node exists."""
    project = _editorial_project(
        tmp_path, monkeypatch, _editorial_manifest())

    graph = project_graph.build_graph(str(project))

    placements = _finding_nodes(graph, "jl_placement")
    assert len(placements) == 1
    covers = [e for e in
              project_graph.edges_from(graph, placements[0]["node_id"])
              if e["type"] == project_graph.COVERS]
    # Position 3 plays clip_002 in the fixture spine.
    assert [e["to"] for e in covers] == ["shot:clip_002"]

    decisions = _finding_nodes(graph, "jl_decision")
    assert len(decisions) == 1
    decision = decisions[0]["payload"]["record"]
    assert decision["status"] == "chosen"
    assert decision["measured_offset_seconds"] == 0.42
    assert decision["chosen_offset_seconds"] == 0.3


def test_a_trim_at_an_unrecorded_position_links_to_no_shot(
        tmp_path, monkeypatch):
    """A finding naming a position the graph never recorded still
    becomes a node - the link is absent, never a guessed clip."""
    manifest = _editorial_manifest()
    manifest["dead_section_trims"] = [
        {"block_position": 99, "trim_edge": "head", "trim_seconds": 1.0},
    ]
    project = _editorial_project(tmp_path, monkeypatch, manifest)

    graph = project_graph.build_graph(str(project))

    trims = _finding_nodes(graph, "dead_section_trim")
    assert len(trims) == 1
    assert trims[0]["payload"]["record"]["trim_seconds"] == 1.0
    assert project_graph.edges_from(graph, trims[0]["node_id"]) == []


def test_reaction_cutaway_links_both_clips_and_the_reacting_person(
        tmp_path, monkeypatch):
    """The cutaway node covers the speech clip and the reaction clip
    and reaches the reacting person over REACTS_TO - the spec's
    'camera shows person B reacting'. On origin/main the cutaway the
    compile selected is not in the graph."""
    project = _editorial_project(
        tmp_path, monkeypatch, _editorial_manifest())

    graph = project_graph.build_graph(str(project))

    cutaways = _finding_nodes(graph, "reaction_cutaway")
    assert len(cutaways) == 1
    covers = sorted(
        e["to"] for e in project_graph.edges_from(graph, cutaways[0]["node_id"])
        if e["type"] == project_graph.COVERS)
    assert covers == ["shot:clip_001", "shot:clip_002"]
    reacts = [e for e in project_graph.edges_from(graph, cutaways[0]["node_id"])
              if e["type"] == project_graph.REACTS_TO]
    assert len(reacts) == 1
    person = project_graph.node(graph, reacts[0]["to"])
    assert person["node_type"] == project_graph.PERSON


def _decision_manifest():
    """A reaction cutaway carrying the planner's EDIT DECISION record."""
    manifest = _editorial_manifest()
    manifest["reaction_cutaways"] = [
        {"speech_block_position": 1, "speech_clip_id": "clip_001",
         "speaking_person_id": "person_001",
         "speech_timeline_start": 2.0, "speech_timeline_end": 8.0,
         "reaction_clip_id": "clip_002",
         "reacting_person_id": "person_002",
         "reaction_source_start": 1.0, "reaction_source_end": 4.0,
         "timing": {"speech_moment": 8.0, "reaction_land": 8.0,
                    "offset_seconds": 0.0},
         "reason": "person_002 is visible in clip_002 while speaking",
         "decision": {
             "action": "Use clip_002 from 1.000s to 4.000s "
                       "as reaction cutaway at 8.000s",
             "reason": "person_002 is visible in clip_002 while speaking",
             "evidence": {
                 "speech_shot": "shot:clip_001",
                 "reaction_shot": "shot:clip_002",
                 "speaking_person": "person_001",
                 "reacting_person": "person_002",
                 "speech_span": [0.0, 5.0],
                 "reaction_span": [1.0, 4.0],
                 "speech_moment": 8.0,
             },
             "constraints": {
                 "reaction_not_in_spine": "clip_002 is in no spine block",
             },
             "style_source": None,
             "confidence": 0.6,
             "confidence_source": "measured span overlap",
         }},
    ]
    return manifest


def test_reaction_decisions_found_by_either_named_clip(
        tmp_path, monkeypatch):
    """A decision is returned for the speech clip and for the reaction
    clip, and for no clip it never names. On origin/main the query
    does not exist."""
    project = _editorial_project(tmp_path, monkeypatch, _decision_manifest())

    graph = project_graph.build_graph(str(project))

    for clip_id in ("clip_001", "clip_002"):
        rows = project_graph.find_reaction_decisions(graph, clip_id=clip_id)
        assert len(rows) == 1
        assert rows[0]["node_id"] == "editorial:reaction_cutaway:0"
        assert rows[0]["decision"]["action"].startswith("Use clip_002")
    assert project_graph.find_reaction_decisions(
        graph, clip_id="clip_007") == []


def test_reaction_decisions_found_by_speech_time(tmp_path, monkeypatch):
    """A time inside the speech span the decision reacts to returns it;
    a time outside returns nothing - the match is temporal, not
    positional. On origin/main the query does not exist."""
    project = _editorial_project(tmp_path, monkeypatch, _decision_manifest())

    graph = project_graph.build_graph(str(project))

    assert len(project_graph.find_reaction_decisions(
        graph, at_time=5.0)) == 1
    assert project_graph.find_reaction_decisions(graph, at_time=50.0) == []


def test_findings_without_decisions_read_as_no_rows(tmp_path, monkeypatch):
    """A cutaway the compile recorded before decisions existed carries
    no decision and yields no row - absence is absence, never a
    synthesised record. On origin/main the query does not exist."""
    project = _editorial_project(
        tmp_path, monkeypatch, _editorial_manifest())

    graph = project_graph.build_graph(str(project))

    assert project_graph.find_reaction_decisions(graph) == []


def test_decision_evidence_refs_resolve_in_the_graph(tmp_path, monkeypatch):
    """The shots a decision's evidence names are nodes in the same
    graph - refs a reader cannot resolve are not shipped. On
    origin/main the query does not exist."""
    project = _editorial_project(tmp_path, monkeypatch, _decision_manifest())

    graph = project_graph.build_graph(str(project))

    rows = project_graph.find_reaction_decisions(graph)
    assert len(rows) == 1
    evidence = rows[0]["decision"]["evidence"]
    for ref in (evidence["speech_shot"], evidence["reaction_shot"]):
        assert project_graph.node(graph, ref) != {}


def test_no_editorial_nodes_without_a_compile(tmp_path, monkeypatch):
    """A project the compile never reached builds a graph with no
    editorial nodes - absence is absence, never an empty finding."""
    project = _editorial_project(tmp_path, monkeypatch, _editorial_manifest())
    manifest_path = next(project.rglob("assembly_manifest.json"))
    manifest_path.unlink()

    graph = project_graph.build_graph(str(project))

    assert [n for n in graph["nodes"]
            if n["node_type"] == project_graph.EDITORIAL_FINDING] == []


def test_the_cache_rebuilds_when_the_findings_change(
        tmp_path, monkeypatch):
    """Defect: the cache fingerprinted the catalog and the colour
    output but not the manifest, so a recompile with new findings
    kept serving the old graph. A changed manifest rebuilds; an
    unchanged one reuses the cache."""
    manifest = _editorial_manifest()
    project = _editorial_project(tmp_path, monkeypatch, manifest)

    first = project_graph.load_or_build(str(project))
    assert len(_finding_nodes(first, "dead_section")) == 1

    manifest["dead_sections"].append(
        {"timeline_start": 24.0, "timeline_end": 25.0,
         "duration_seconds": 1.0, "block_position": 3,
         "clip_id": "clip_002", "reasons": ["no speech"]})
    _write_manifest(project, manifest)

    second = project_graph.load_or_build(str(project))
    assert len(_finding_nodes(second, "dead_section")) == 2

    third = project_graph.load_or_build(str(project))
    assert third["node_count"] == second["node_count"]


def test_the_cache_rebuilds_when_a_new_version_lands(
        tmp_path, monkeypatch):
    """Defect, same shape: the versions ledger was a graph input the
    fingerprint never covered, so a new reel version never reached
    the graph until some unrelated input changed."""
    project = _editorial_project(
        tmp_path, monkeypatch, _editorial_manifest())

    review_dir = project / "pipeline_output" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = review_dir / "reel_versions.json"

    def _write_ledger(versions):
        ledger_path.write_text(json.dumps({
            "format": "reel_versions/1",
            "reels": {"reel_001": versions},
        }), encoding="utf-8")

    _write_ledger([
        {"version": 1, "kind": "build", "at": "2026-10-06T10:00:00+00:00",
         "rows": {}, "rows_digest": "abc123"},
    ])
    first = project_graph.load_or_build(str(project))
    assert len(project_graph.find_reel_versions(first, "reel_001")) == 1

    _write_ledger([
        {"version": 1, "kind": "build", "at": "2026-10-06T10:00:00+00:00",
         "rows": {}, "rows_digest": "abc123"},
        {"version": 2, "kind": "touch", "at": "2026-10-06T11:00:00+00:00",
         "rows": {}, "rows_digest": "def456"},
    ])
    second = project_graph.load_or_build(str(project))
    found = project_graph.find_reel_versions(second, "reel_001")
    assert [r["version"] for r in found] == [1, 2]
    assert found[1]["kind"] == "touch"


# ── The new queries ───────────────────────────────────────────────


def _version_graph():
    graph = project_graph.new_graph()
    for version, kind in ((1, "build"), (2, "touch")):
        project_graph.add_node(graph, {
            "node_id": f"version:reel_001:{version}",
            "node_type": project_graph.VERSION,
            "label": f"version {version} of reel_001",
            "payload": {"final": "reel_001", "version": version,
                        "kind": kind, "at": "2026-10-06T10:00:00+00:00",
                        "rows_digest": "abc123"},
        })
    project_graph.add_node(graph, {
        "node_id": "version:reel_002:1",
        "node_type": project_graph.VERSION,
        "label": "version 1 of reel_002",
        "payload": {"final": "reel_002", "version": 1, "kind": "build",
                    "at": "2026-10-06T10:00:00+00:00",
                    "rows_digest": "zzz999"},
    })
    return graph


def test_find_reel_versions_lists_every_version_and_filters_by_final():
    graph = _version_graph()

    assert [r["version"] for r in
            project_graph.find_reel_versions(graph, "reel_001")] == [1, 2]
    assert len(project_graph.find_reel_versions(graph)) == 3
    assert project_graph.find_reel_versions(graph, "reel_999") == []


def test_find_editorial_findings_filters_by_kind_and_clip():
    graph = project_graph.new_graph()
    project_graph.add_node(graph, _shot("clip_001"))
    project_graph.add_node(graph, {
        "node_id": "editorial:dead_section:0",
        "node_type": project_graph.EDITORIAL_FINDING,
        "label": "dead_section 0",
        "payload": {"kind": "dead_section", "record": {
            "timeline_start": 14.0, "timeline_end": 17.5,
            "clip_id": "clip_001", "reasons": ["no speech"]}},
    })
    project_graph.add_node(graph, {
        "node_id": "editorial:jl_decision:0",
        "node_type": project_graph.EDITORIAL_FINDING,
        "label": "jl_decision 0",
        "payload": {"kind": "jl_decision", "record": {
            "cut_point_position": 3, "status": "chosen"}},
    })
    project_graph.add_edge(graph, _edge(
        "editorial:jl_decision:0", "shot:clip_001", project_graph.COVERS,
        {"finding": "jl_decision"}))

    assert len(project_graph.find_editorial_findings(graph)) == 2
    assert [r["node_id"] for r in project_graph.find_editorial_findings(
        graph, kind="dead_section")] == ["editorial:dead_section:0"]
    # The clip filter reads the record's clip keys AND the COVERS
    # edges, so a position-resolved J/L finding answers for its clip.
    assert len(project_graph.find_editorial_findings(
        graph, clip_id="clip_001")) == 2
    assert project_graph.find_editorial_findings(
        graph, kind="dead_section", clip_id="clip_999") == []
    assert project_graph.find_editorial_findings(
        graph, kind="no_such_kind") == []


def test_find_vfx_detections_filters_by_clip():
    graph = project_graph.new_graph()
    for clip_id, effect in (("clip_001", "stabilization"),
                            ("clip_002", "denoise")):
        project_graph.add_node(graph, {
            "node_id": f"vfx:edit:{clip_id}",
            "node_type": project_graph.VFX_DETECTION,
            "label": f"vfx on {clip_id}",
            "payload": {"edit_node_id": f"edit:{clip_id}",
                        "clip_id": clip_id, "position": 1,
                        "effect": effect},
        })

    found = project_graph.find_vfx_detections(graph, "clip_001")
    assert len(found) == 1
    assert found[0]["effect"] == "stabilization"
    assert len(project_graph.find_vfx_detections(graph)) == 2
    assert project_graph.find_vfx_detections(graph, "clip_999") == []


# ── The engine-checkout guard ───────────────────────────────────────
#
# Defect, reported by the delivery lane: the audio suite left an empty
# `project_graph.json` at the repository root. The compile tests pass
# a cwd-relative out_dir (`compile_manifest("dummy")` resolves its
# project root to the empty string), so `load_or_build` wrote the
# cache into whatever directory the suite ran from. The graph is
# project state - a root that contains this module is the engine
# checkout, never a project, and the load is refused before any write.
#


def test_load_or_build_refuses_the_engine_checkout(tmp_path, monkeypatch):
    """A cwd-relative test root must fail loudly, not write the cache
    into the repo. On origin/main the write lands beside the suite -
    this test fails there by leaving the stray file behind."""
    from library.tools.ren_refusal import RenRefusal

    repo_root = Path(project_graph.__file__).resolve().parents[2]
    assert (repo_root / "library" / "tools" / "project_graph.py").is_file()

    monkeypatch.chdir(tmp_path)
    with pytest.raises(RenRefusal):
        project_graph.load_or_build(str(repo_root))

    assert not (repo_root / project_graph.GRAPH_FILE_NAME).exists()
    assert not (tmp_path / project_graph.GRAPH_FILE_NAME).exists()


def test_load_or_build_still_caches_a_tmp_project(tmp_path, monkeypatch):
    """The guard refuses only the checkout itself: a project under
    tmp_path loads, writes its cache beside its own state, and reuses
    it unchanged."""
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    project = tmp_path / "project"
    project.mkdir()
    (project / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {"footage.catalog": {"clip_catalog": []}},
    }), encoding="utf-8")

    first = project_graph.load_or_build(str(project))
    assert first["node_count"] == 0
    assert (project / project_graph.GRAPH_FILE_NAME).is_file()
    second = project_graph.load_or_build(str(project))
    assert second["node_count"] == 0
