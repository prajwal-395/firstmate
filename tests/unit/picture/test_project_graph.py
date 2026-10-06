"""The Ren Project Graph: a persistent relational model of the project.

Every test names a behavioral defect the graph could have: a reaction
shot that is not found, an alternate take that is missed, a coverage
query that returns the wrong shots, a B-roll match that is not linked.
No test asserts a schema or count.
"""
import json
from pathlib import Path

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
