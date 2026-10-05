"""Footage search: what it cuts, what it finds, and that it stands alone.

Search is a footage-intelligence capability, not an editing-pipeline
stage (AGENTS.md §2). Agents and people query it through `ren search` /
`ren search-index`; what keeps it a capability is that it imports nothing
from the pipeline, so it answers on any analysed project with no run
behind it. `test_search_does_not_import_the_pipeline` pins that.

Every test builds its project under `tmp_path`. No test reads a
real project (§8).
"""
import json
import re
import sys
from pathlib import Path
import numpy as np
import pytest
from library.tools.analysis import footage_query, footage_segments
from library.tools.analysis.footage_query import FootageIndex, build_index
from library.tools.analysis.footage_segments import (
    SEGMENT_KINDS,
    build_segments,
    curve_facets,
)
from library.tools import footage_identity, source_memory
from library.tools.ren_refusal import RenRefusal
from library.tools.analysis import footage_frames
from library.tools.analysis.footage_frames import FrameIndex
import os
from library.tools import footage_analysis, memory_export


REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_MODULES = ("footage_query", "footage_segments", "footage_frames")


# ─── A project on disk, built from nothing ────────────────────────


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path) -> Path:
    """A two-clip project carrying the ingest shapes 1.02/1.03/1.04 emit."""
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"

    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV",
             "source_file": str(root / "raw" / "IMG_0001.MOV"),
             "path": str(root / "raw" / "IMG_0001.MOV"), "duration_seconds": 20.0},
            {"clip_id": "clip_002", "filename": "IMG_0002.MOV",
             "source_file": str(root / "raw" / "IMG_0002.MOV"),
             "path": str(root / "raw" / "IMG_0002.MOV"), "duration_seconds": 10.0},
        ]}},
    })

    # 1.04: one clip speaks, the other is silent. Curves at three rates.
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001",
        "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "we", "start": 1.0, "end": 1.2},
                       {"word": "parking", "start": 2.0, "end": 2.4},
                       {"word": "spot", "start": 2.5, "end": 3.0}]},
            {"start": 6.0, "end": 7.5, "text": "the pollen is terrible today",
             "words": [{"word": "pollen", "start": 6.2, "end": 6.6}]},
        ],
        # 1 Hz for arithmetic that is obvious by eye: 0..9 then 1.0 from 10s.
        "speech_activity": {"sample_rate_hz": 1, "values": [0.0] * 10 + [1.0] * 10},
        "motion_energy": {"sample_rate_hz": 1, "values": [0.5] * 20},
        "face_presence": {"sample_rate_hz": 1, "values": [1.0] * 20},
        "color_curves": {"sample_rate_hz": 1,
                         "brightness_values": [0.4] * 20,
                         "saturation_values": [0.2] * 20},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0, "speech_regions": [],
        "motion_energy": {"sample_rate_hz": 1, "values": [0.9] * 10},
    })

    # 1.03: keyed by FILE STEM, not clip_id (§10.1).
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0001_v3.json", {
        "clip_id": "IMG_0001",
        "file_path": str(root / "raw" / "IMG_0001.MOV"),
        "duration_s": 20.0,
        "scene": [{"start": 0.0, "end": 20.0, "location": "Outdoor parking lot",
                   "type": "outdoor", "lighting": "Daylight",
                   "notable_features": ["Parked cars", "Brick building"]}],
        "camera": [{"start": 0, "end": 20, "mode": "selfie",
                    "framing": "close-up", "stability": "shaky",
                    "movement": "walking"}],
        "actions": [{"window": [0, 10], "actions": [
            {"start": 0, "end": 10,
             "action": "The man walks across the lot",
             "body_language": "He smiles broadly and squints",
             "speech_cue": None}]}],
        "objects": [{"label": "red bicycle", "category": "object",
                     "role": "background", "readable_text": None,
                     "appearances": [[2.0, 5.0], [12.0, 15.0]]}],
        "assessment": {"content_type": "person_talking_to_camera"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0002_v3.json", {
        "clip_id": "IMG_0002",
        "file_path": str(root / "raw" / "IMG_0002.MOV"),
        "duration_s": 10.0,
        "scene": [{"start": 0.0, "end": 10.0, "location": "Empty street",
                   "type": "outdoor", "lighting": "Overcast",
                   "notable_features": []}],
        "camera": [{"start": 0, "end": 10, "mode": "mounted",
                    "framing": "wide", "stability": "stable",
                    "movement": "static"}],
        "actions": [], "objects": [],
        "assessment": {"content_type": "scenery"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })

    # 1.05 produced a file and measured nothing - the shape 001 really has.
    _write(steps / "1_05_prosody_analysis" / "clip_001_prosody.json",
           {"clip_id": "clip_001", "prosody": {"method": None,
                                               "error": "parselmouth not installed"}})
    return root


@pytest.fixture
def offline_index(project, tmp_path, monkeypatch):
    """An index built with a deterministic stand-in for the embedder.

    A test must not download or run a 22M-parameter model, and it does not
    need to: the retrieval arithmetic is the thing under test. The stub
    embeds each text as a normalised bag-of-characters, which is enough
    for "similar text scores higher" to hold.
    """
    def fake_loader():
        def encode(texts):
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                norm = np.linalg.norm(vector) or 1.0
                rows.append(vector / norm)
            return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
        return encode, "test-stub"

    monkeypatch.setattr(footage_query, "_load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    stats = build_index(project, index_dir=index_dir)
    return FootageIndex(project, index_dir=index_dir), stats


# ─── The unit of retrieval ────────────────────────────────────────


def test_every_source_is_cut_at_its_own_boundary(project):
    segments = build_segments(project)
    kinds = {s.kind for s in segments}
    assert kinds == set(SEGMENT_KINDS), (
        "each ingest source must contribute its own kind of segment"
    )

    speech = [s for s in segments if s.kind == "speech"]
    assert [s.text for s in speech] == [
        "we need to find a parking spot", "the pollen is terrible today",
    ], "one segment per WhisperX utterance, in time order"

    # The vision join goes through the file path: 1.03 keys by file stem,
    # the catalog by clip_XXX (§10.1).
    scene = next(s for s in segments if s.kind == "scene" and s.clip_id == "clip_001")
    assert "parking lot" in scene.text.lower()
    assert scene.facets["framing"] == "close-up"


def test_unknown_kind_raises_rather_than_matching_nothing(project):
    with pytest.raises(ValueError, match="Unknown segment kind"):
        build_segments(project, kinds=("speech", "vibes"))


# ─── Curves become facets, never segments ─────────────────────────


def test_an_unmeasured_curve_is_absent_not_zero(project):
    """"no face curve" and "no face" are different answers."""
    doc = footage_segments.load_temporal_index(project)["clip_002"]
    facets = curve_facets(doc, 0.0, 10.0)
    assert "face_presence" not in facets
    assert facets["motion"] == 0.9


# ─── Building and querying ────────────────────────────────────────


def test_search_finds_the_utterance_and_the_word(offline_index):
    idx, _ = offline_index
    hits = idx.search("parking", top_k=3, mode="lexical")
    top = hits[0]
    assert top["kind"] == "speech" and top["clip_id"] == "clip_001"
    assert top["start"] == 1.0 and top["end"] == 3.0
    assert top["word_hits"] == [{"word": "parking", "start": 2.0, "end": 2.4}]


def test_lexical_search_works_with_no_embedder_at_all(project, tmp_path, monkeypatch):
    """The index must degrade to keyword search, not to nothing.

    `sentence-transformers` is not in requirements.txt and was not
    installed in the environment this was built in, so "the embedder is
    missing" is the normal case, not the exotic one.
    """
    monkeypatch.setattr(footage_query, "_load_embedder", lambda: (None, "none"))
    before = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    index_dir = tmp_path / "nodense"
    build_index(project, index_dir=index_dir)
    after = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    assert before == after, "building the index must not touch the project"
    assert (index_dir / footage_query.SEGMENTS_FILE).exists()
    idx = FootageIndex(project, index_dir=index_dir)
    assert idx.matrix is None
    assert idx.search("pollen", top_k=1, mode="hybrid")[0]["kind"] == "speech"
    assert "error" in idx.search("pollen", mode="dense")[0]


# ─── The floor, and the abstain ───────────────────────────────────
#
# The captain will type something that is not in the footage on their
# first afternoon with this. A search that cannot say "not here" reads as
# broken, so these are about the answer NOTHING being a real answer.


def test_a_query_nothing_clears_returns_nothing_and_says_why(offline_index):
    """Not three confident wrong rows. Nothing, plus the near miss.

    The floor here is set just above what the best segment really scores,
    rather than to a magic number, so the test asserts the MECHANISM and
    not a threshold that only holds for one stub embedder.
    """
    idx, _ = offline_index
    unfloored = idx.search_report("parking", top_k=5, floor=0)
    best = unfloored["results"][0]["dense_score"]

    report = idx.search_report("parking", top_k=5, floor=best + 0.01)
    assert report["results"] == [], "nothing cleared the floor, so nothing comes back"
    assert report["abstained"] is True
    assert report["best_rejected"]["dense_score"] == best, (
        "an abstain that cannot name its near miss reads as a broken search"
    )
    assert report["considered"] == len(idx.segments)
    assert report["retained"] == 0

    # And the list-returning form agrees: an empty list, not an error row.
    assert idx.search("parking", floor=best + 0.01) == []


# ─── Noticing that the ingest moved ───────────────────────────────


def test_a_downstream_step_writing_state_does_not_make_the_ingest_stale(
        offline_index, project):
    """`pipeline_data.json` is not an ingest file, it is every file.

    `save_pipeline_state` rewrites it after EVERY step, so fingerprinting
    it whole would report the index stale within seconds of a run
    starting - which is what it did when this was first written, against
    a live run of 001. Only the subtree the segment builder reads counts.
    """
    idx, _ = offline_index
    state_path = project / "pipeline_data.json"
    state = json.loads(state_path.read_text())
    state["step_outputs"]["color_grade"] = {"applied": True, "look": "warm"}
    state["steps_completed"] = {"color_grade": {}}
    state_path.write_text(json.dumps(state))

    assert idx.staleness()["stale"] is False, (
        "a downstream step landing is not a change to the footage"
    )

    # ...and a change to the CATALOG still is.
    state["step_outputs"]["catalog"]["clip_catalog"][0]["duration_seconds"] = 21.0
    state_path.write_text(json.dumps(state))
    assert idx.staleness()["stale"] is True


# ─── Where it is reached from, and what it must not reach ─────────


def test_ren_search_reaches_the_footage_index():
    """`ren search` / `ren search-index` are how agents and people query it."""
    from ren.commands import VERBS

    by_name = {verb.name: verb for verb in VERBS}
    for name in ("search", "search-index"):
        assert name in by_name, f"ren lost its {name!r} verb"
        assert "footage_query" in " ".join(by_name[name].module_argv), (
            f"ren {name!r} no longer reaches the footage index")


def test_search_does_not_import_the_pipeline():
    """It reads a project's files; it does not join the run.

    Importing a step, the runner or the state writer would make search a
    pipeline stage by the back door, and `ren search` would drag the DAG
    into its process.
    """
    forbidden = re.compile(r"from library\.(steps|processes)|import library\.(steps|processes)"
                           r"|save_pipeline_state|run_pipeline")
    for name in SEARCH_MODULES:
        source = (REPO_ROOT / "library" / "tools" / "analysis" / f"{name}.py").read_text(
            encoding="utf-8")
        assert not forbidden.search(source), f"{name}.py reaches into the pipeline"


# --------------------------------------------------------------------------
# From test_footage_query_person_filter.py
#
# `ren search --person` / `filter --person`: the join between the
# footage index and the person entity store (M3b).
#
# Builds its project and memory under `tmp_path`; no real media, ffmpeg,
# insightface or ECAPA involved - the measured parts live in
# `data/vep-person-entity-store/eval/results.md` and PR #1482, this guards
# the JOIN logic `footage_query.FootageIndex.filter(person=...)` adds.

@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _media(tmp_path: Path, name: str, seed: bytes) -> Path:
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
    return path


@pytest.fixture
def project_2(tmp_path):
    """One project, two clips, each with its own speaking person."""
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"

    media_1 = _media(tmp_path, "IMG_0001.MOV", b"clip-one")
    media_2 = _media(tmp_path, "IMG_0002.MOV", b"clip-two")
    digest_1 = footage_identity.fingerprint(str(media_1))["content_digest"]
    digest_2 = footage_identity.fingerprint(str(media_2))["content_digest"]

    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": media_1.name,
             "source_file": str(media_1), "path": str(media_1),
             "duration_seconds": 20.0},
            {"clip_id": "clip_002", "filename": media_2.name,
             "source_file": str(media_2), "path": str(media_2),
             "duration_seconds": 10.0},
        ]}},
    })

    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
        ],
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0,
        "speech_regions": [
            {"start": 2.0, "end": 4.0, "text": "okay let's begin the podcast",
             "words": [{"word": "okay", "start": 2.0, "end": 2.3}]},
        ],
    })

    return root, digest_1, digest_2


@pytest.fixture
def offline_index_2(project_2, tmp_path, monkeypatch):
    root, digest_1, digest_2 = project_2

    def fake_loader():
        def encode(texts):
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                norm = np.linalg.norm(vector) or 1.0
                rows.append(vector / norm)
            return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
        return encode, "test-stub"

    monkeypatch.setattr(footage_query, "_load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    build_index(root, index_dir=index_dir)
    return FootageIndex(root, index_dir=index_dir), digest_1, digest_2


def _write_identity(root: Path, digest: str, speech_start: float, speech_end: float):
    source_memory.write_json(root / digest / source_memory.SLOT_IDENTITY, {
        "content_digest": digest, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": [1.0] + [0.0] * 511,
                  "spans": [{"start": speech_start, "end": speech_end,
                            "box": [0, 0, 1, 1], "det_score": 0.9}]}],
        "voices": [{"track_id": "voice_001", "embedding": [0.0] * 192,
                   "spans": [[speech_start, speech_end]]}],
        "speech_face_links": [{"t": (speech_start + speech_end) / 2,
                              "face_track": "face_001",
                              "voice_track": "voice_001",
                              "basis": "co-occurrence: voice span + "
                                      "face span overlap"}],
    })


def test_person_filter_keeps_only_that_person_s_clip(offline_index_2, memory_root):
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(memory_root, digest_1, 1.0, 3.0)

    results = idx.filter(person="person_001")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"

    # A span that does not cover the segment's time range does not match
    # it: 10.0-10.2s is nowhere near the 1.0-3.0s speech segment.
    _write_identity(memory_root, digest_1, 10.0, 10.2)
    assert idx.filter(person="person_001") == []


def test_person_filter_unknown_name_raises_rather_than_matching_nothing(
        offline_index_2, memory_root):
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(memory_root, digest_1, 1.0, 3.0)

    with pytest.raises(RenRefusal):
        idx.filter(person="nobody-by-this-name")


def test_search_report_with_person_filter_answers_who_says_okay(
        offline_index_2, memory_root):
    """The captain's example from the scout report: 'who says okay' -
    speaker-attributed text search, through the real `search_report`
    path (text match + person filter), not just `filter` alone."""
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(memory_root, digest_2, 2.0, 4.0)

    report = idx.search_report("okay", mode="lexical",
                               filters={"person": "person_001"})
    assert report["results"]
    assert report["results"][0]["clip_id"] == "clip_002"


# --------------------------------------------------------------------------
# Sound-event search (the M5 lane)
#
# `ren search --sound <label>`: PANNs sound events, measured by step 1.04
# and stored in the M5 slot, joined into search by label.  The defect this
# guards: "where does laughter happen" was unanswerable from search - the
# events were measured but no query could reach them.
#
# --------------------------------------------------------------------------

@pytest.fixture
def sound_index(project_2, memory_root, tmp_path, monkeypatch):
    """An index over a project whose M5 sound lane has measured events."""
    root, digest_1, digest_2 = project_2
    source_memory.write_sound(
        digest_1, str(root / "raw" / "IMG_0001.MOV"), "measured",
        [{"label": "Laughter", "start": 2.0, "end": 4.5, "confidence": 0.87},
         {"label": "Speech", "start": 6.0, "end": 8.0, "confidence": 0.92}],
        "PANNs Cnn14")
    source_memory.write_sound(
        digest_2, str(root / "raw" / "IMG_0002.MOV"), "measured",
        [{"label": "Applause", "start": 1.0, "end": 3.0, "confidence": 0.78}],
        "PANNs Cnn14")

    def fake_loader():
        def encode(texts):
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                norm = np.linalg.norm(vector) or 1.0
                rows.append(vector / norm)
            return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
        return encode, "test-stub"

    monkeypatch.setattr(footage_query, "_load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    build_index(root, index_dir=index_dir)
    return FootageIndex(root, index_dir=index_dir)


def test_sound_search_finds_the_measured_label(sound_index):
    """The defect: 'where does laughter happen' was unanswerable.

    PANNs measured the events and step 1.04 stored them in the M5 slot,
    but no query could reach them - search had no sound lane.  This pins
    that the measured label now returns its spans.
    """
    report = sound_index.sound_search("laughter")
    assert report["abstained"] is False
    assert len(report["hits"]) == 1
    hit = report["hits"][0]
    assert hit["clip_id"] == "clip_001"
    assert hit["label"] == "Laughter"
    assert hit["start"] == 2.0
    assert hit["end"] == 4.5
    assert hit["confidence"] == 0.87


def test_sound_search_matches_across_clips_and_says_what_is_there(
        sound_index):
    """A label in more than one clip returns every span, and a label that
    is not there returns an honest empty naming the labels this footage
    has - never a silent 'nowhere' that reads as 'not measured'."""
    both = sound_index.sound_search("applause")
    assert [h["clip_id"] for h in both["hits"]] == ["clip_002"]

    missing = sound_index.sound_search("dog barking")
    assert missing["abstained"] is True
    assert missing["hits"] == []
    assert "Laughter" in missing["available_labels"]
    assert "Applause" in missing["available_labels"]


def test_sound_search_with_no_events_says_the_lane_was_never_written(
        offline_index):
    """An index whose project has no M5 slot says so, rather than
    answering 'nowhere' to a question that was never measured."""
    idx, _stats = offline_index
    report = idx.sound_search("laughter")
    assert report["abstained"] is True
    assert "no sound events" in report["error"]


def test_sound_search_cli_reaches_the_index(sound_index, project_2, monkeypatch):
    """`ren search --sound <label>` is the person's route to the same
    answer - the flag must reach the module and refuse without a query."""
    root = project_2[0]
    from library.tools.analysis import footage_query as fq

    calls = []

    def fake_sound_search(self, query, top_k=5):
        calls.append((query, top_k))
        return {"query": query, "hits": [], "abstained": True,
                "considered": 0, "matched": 0, "available_labels": [],
                "error": None}

    monkeypatch.setattr(FootageIndex, "sound_search", fake_sound_search)
    monkeypatch.setattr(sys, "argv", [
        "footage_query", "search", str(root), "--sound", "laughter"])
    assert fq.main(["search", str(root), "--sound", "laughter"]) == 0
    assert calls == [("laughter", 5)]

    with pytest.raises(RenRefusal):
        fq.main(["search", str(root), "--sound"])


# --------------------------------------------------------------------------
# From test_footage_frames.py
#
# Frame-level CLIP search: ranges, honesty labels, and scope.
#
# Companion to `test_footage_query.py` (which owns the does-not-import-the-
# pipeline guard - `footage_frames` is in its `SEARCH_MODULES`).  Every test here
# names a defect it would catch; no count, existence or snapshot tests.
#
# All CLIP work and the per-source memory's M2 frame sample are stubbed: a
# test must not download weights, run a model, shell out to ffmpeg, or touch
# `source_memory`'s machine-wide store.  The retrieval arithmetic and the
# scope gates are the things under test.

@pytest.fixture
def project_3(tmp_path) -> Path:
    """A two-clip project whose video files really exist (staleness stats them)."""
    root = tmp_path / "proj"
    catalog = []
    for clip_id, name, duration in (("clip_001", "TAKE_001.MOV", 120.0),
                                    ("other_002", "TAKE_002.MOV", 60.0)):
        raw = root / "raw" / name
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"fake-video-bytes")
        catalog.append({"clip_id": clip_id, "filename": name,
                        "source_file": str(raw), "duration_seconds": duration})
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": catalog}},
    })
    return root


@pytest.fixture
def stub_clip(monkeypatch):
    """Deterministic stand-in for CLIP: image row i is one-hot(i).

    Query vectors are plain rows in the same space, so cosine order is the
    order of the query vector's own entries - enough for "higher ranks
    first" and "merging reads the ranking" to hold.
    """
    calls = {"images": 0}

    def fake_loader():
        def encode_images(paths):
            n = len(paths)
            calls["images"] += 1
            dim = max(n, 1)
            mat = np.zeros((n, dim), dtype="float32")
            for i in range(n):
                mat[i, i % dim] = 1.0
            return mat

        def encode_text(texts):
            # Prefers frame 0, then 1, then 2; frame 3+ score ~0.
            dim = 5
            vec = np.array([0.6, 0.5, 0.4, 0.01, 0.0], dtype="float32")
            vec = vec / np.linalg.norm(vec)
            return np.tile(vec[:dim], (len(list(texts)), 1))

        return encode_images, encode_text, "test-stub"

    monkeypatch.setattr(footage_frames, "_load_clip", fake_loader)
    return calls


@pytest.fixture
def stub_m2(monkeypatch):
    """M2 frame samples with no ffmpeg or `source_memory` store: clip_001
    at t=0/10/20/100, other_002 at t=5 - the per-clip digest IS the clip
    id here, so each clip reads its own fake record.  These are the same
    frame times the old per-clip extractor stubbed, so the retrieval
    tests still exercise the arithmetic they were written for."""
    frame_times = {"clip_001": [0.0, 10.0, 20.0, 100.0],
                  "other_002": [5.0]}

    def fake_digest_for_clip(project_folder, clip, recorded=None):
        return clip["clip_id"], "live"

    def fake_read_m2(content_digest, root=None):
        times = frame_times.get(content_digest)
        if times is None:
            return None
        return {
            "content_digest": content_digest,
            "frame_count": len(times),
            "frames": [{"file": f"frames/frame_{i:06d}.jpg", "t": t}
                      for i, t in enumerate(times)],
        }

    def fake_is_fresh(record, source_file):
        return True

    def fake_frame_abspath(content_digest, frame, root=None):
        return f"/memory/{content_digest}/{frame['file']}"

    monkeypatch.setattr(footage_frames.source_memory, "digest_for_clip",
                        fake_digest_for_clip)
    monkeypatch.setattr(footage_frames.source_memory, "read_m2", fake_read_m2)
    monkeypatch.setattr(footage_frames.source_memory, "is_fresh", fake_is_fresh)
    monkeypatch.setattr(footage_frames.source_memory, "frame_abspath",
                        fake_frame_abspath)


@pytest.fixture
def frame_index(project_3, tmp_path, stub_clip, stub_m2):
    index_dir = tmp_path / "findex"
    footage_frames.build_frame_index(project_3, index_dir=index_dir)
    return FrameIndex(project_3, index_dir=index_dir)


# ─── Ranges: the unit a person scrubs ───────────────────────────────


def test_adjacent_hits_merge_and_distant_ones_do_not(frame_index):
    """t=0/10/20 on one clip are one range; t=100 and the other clip are not."""
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5)
    assert report["refused"] is None
    ranges = report["ranges"]
    assert [(r["clip_id"], r["start"], r["end"]) for r in ranges] == [
        ("clip_001", 0.0, 20.0),
        ("clip_001", 100.0, 100.0),
        ("other_002", 5.0, 5.0),
    ], "adjacent pool hits merge per clip; anything else stays split"
    head = ranges[0]
    assert head["n_frames"] == 3
    assert head["timecode"] == "00:00.000-00:20.000"
    assert head["best_score"] == pytest.approx(max(
        h["score"] for h in report["results"] if h["clip_id"] == "clip_001"
        and h["t"] <= 20.0))

    # A gap wider than the merge gap splits.
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5,
                                       merge_gap_s=5.0)
    clips = [(r["clip_id"], r["start"], r["end"]) for r in report["ranges"]]
    assert ("clip_001", 0.0, 0.0) in clips
    assert ("clip_001", 10.0, 10.0) in clips, (
        "a 10 s gap with a 5 s merger must not merge")


# ─── Honesty: ranked, unverified, never absence ─────────────────────


def test_hits_are_ranked_unverified_and_never_claim_absence(
        frame_index, monkeypatch):
    """A floor here would repeat the failure the text floor fixed: an index
    that cannot say "not here" must rank, not go silent - and say so."""
    report = frame_index.search_ranges("a laptop on a table")
    assert report["notice"] and "cannot be concluded" in report["notice"]
    for hit in report["results"]:
        assert hit["verified"] is False
    for span in report["ranges"]:
        assert span["verified"] is False

    def flat_loader():
        def encode_images(paths):
            return np.zeros((len(paths), 4), dtype="float32")

        def encode_text(texts):
            return np.zeros((len(list(texts)), 5), dtype="float32")

        return encode_images, encode_text, "test-stub"

    monkeypatch.setattr(footage_frames, "_load_clip", flat_loader)
    report = frame_index.search_frames("a birthday cake with candles", top_k=3)
    assert report["refused"] is None
    assert len(report["results"]) == 3, (
        "all-zero scores still return top-k: ranking is the answer")


# ─── Scope: actions belong to the event pipeline ────────────────────


def test_action_queries_refuse_and_name_the_event_pipeline(frame_index):
    """The refusal must name the lane that answers action queries.  The
    event pipeline (M7) measures actions as candidate spans, so a pointer
    to any other lane - the pose/hand lane this used to name - sends the
    caller nowhere: the query is refused and nothing answers it."""
    from library.tools import event_spans

    for query in ("a person covering their mouth with their hand",
                  "a person drinking from a cup",
                  "a hand gesturing in the foreground"):
        report = frame_index.search_frames(query)
        assert report["results"] == [], query
        assert report["refused"] is not None
        hint = report["refused"]["hint"]
        assert "event pipeline" in hint, query
        assert "--predicate" in hint, query
        assert any(predicate in hint
                   for predicate in event_spans.CANDIDATE_PREDICATES), query
    # Object queries pass the gate.
    assert footage_frames.action_refusal_match("a laptop on a table") is None
    assert footage_frames.action_refusal_match("two people at a table") is None


def test_action_override_ranks_when_asked(frame_index):
    report = frame_index.search_frames(
        "a person drinking from a cup", include_actions=True)
    assert report["refused"] is None
    assert len(report["results"]) == 5


# ─── Building: only the index dir, and staleness sees a moved source ──


def test_build_writes_only_into_the_index_dir(project_3, tmp_path, stub_clip,
                                              stub_m2):
    before = sorted(p.relative_to(project_3) for p in project_3.rglob("*")
                    if p.is_file())
    index_dir = tmp_path / "elsewhere"
    stats = footage_frames.build_frame_index(project_3, index_dir=index_dir)
    after = sorted(p.relative_to(project_3) for p in project_3.rglob("*")
                   if p.is_file())
    assert before == after, "building the frame index must not touch the project"
    assert (index_dir / footage_frames.FRAME_INDEX_FILE).exists()
    assert stats["frame_count"] == 5


def test_a_clip_with_no_m2_sample_is_skipped_not_fatal(project_3, tmp_path,
                                                        stub_clip, monkeypatch):
    """This index reads the shared M2 sample; it must never fall back to
    decoding the clip itself when that sample is missing."""
    def fake_digest_for_clip(project_folder, clip, recorded=None):
        return clip["clip_id"], "live"

    monkeypatch.setattr(footage_frames.source_memory, "digest_for_clip",
                        fake_digest_for_clip)
    monkeypatch.setattr(footage_frames.source_memory, "read_m2",
                        lambda content_digest, root=None: None)
    index_dir = tmp_path / "findex"
    stats = footage_frames.build_frame_index(project_3, index_dir=index_dir)
    assert stats["frame_count"] == 0
    assert len(stats["skipped"]) == 2
    assert all("source_memory frames" in s["reason"] for s in stats["skipped"])


def test_a_replaced_source_video_reads_as_stale(frame_index, project_3):
    assert frame_index.staleness()["stale"] is False
    raw = project_3 / "raw" / "TAKE_001.MOV"
    raw.write_bytes(b"fake-video-bytes-CHANGED")
    stale = frame_index.staleness()
    assert stale["stale"] is True
    assert stale["changed"] == ["clip_001"]


# ─── The transformers 5.x boundary ──────────────────────────────────


def test_pooled_unwrap_handles_both_clip_output_shapes():
    """Measured 2026-09-30: `get_*_features` returns BaseModelOutputWithPooling
    from transformers 5.x up, and `.norm` on that raises AttributeError."""

    class _Tensor:
        def norm(self, p=2, dim=-1, keepdim=True):
            return self

        def __truediv__(self, other):
            return "NORMALIZED"

    class _FiveX:
        """5.x shape: no .norm, carries pooler_output."""

    five = _FiveX()
    five.pooler_output = _Tensor()
    assert footage_frames._pooled_clip_features(five) == "NORMALIZED"
    assert footage_frames._pooled_clip_features(_Tensor()) == "NORMALIZED"


# --------------------------------------------------------------------------
# From test_footage_intelligence.py
#
# The analysis-only run (`ren analyze`) and its path-portable export.
#
# Each test names the defect it catches. Every project and memory root is
# built under `tmp_path` (§8): no ffmpeg, no model, no real footage.

def _write_2(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_with_one_source(tmp_path: Path) -> tuple:
    media = tmp_path / "footage" / "CAM_A.MXF"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"frame" * (3 * 1024 * 1024 // 5 + 1))
    fp = footage_identity.fingerprint(str(media))
    project = tmp_path / "collection"
    _write_2(project / "pipeline_data.json", {"step_outputs": {"catalog": {
        "clip_catalog": [{"clip_id": "clip_001", "source_file": str(media),
                          "duration_seconds": 10.0}]}}})
    return str(project), str(media), fp["content_digest"], fp["size_bytes"]


def _transcript(digest: str, media: str, instrument: dict) -> dict:
    return {"content_digest": digest, "source_file": media,
            "status": source_memory.M1_STATUS_TRANSCRIBED,
            "utterances": [{"start": 1.0, "end": 2.0, "text": "hello there",
                            "words": [{"word": "hello", "start": 1.0, "end": 1.4},
                                      {"word": "there", "start": 1.5, "end": 2.0}],
                            "confidence": 0.0, "method": "hybrid-mfa"}],
            "utterance_count": 1, "word_count": 2, "speech_seconds": 1.0,
            "instrument": instrument}


def test_the_analysis_run_executes_no_edit_capability():
    """Defect: an 'analysis-only' run that plans, renders or segments.

    Object segmentation waits for edit plans and OCR has no reader, so
    both stay out by default; nothing downstream of preflight may be
    selected, whatever `--with` names.
    """
    from library.tools import capabilities, footage_intelligence
    default = footage_intelligence.compose(footage_intelligence.select())
    assert {capabilities.get(c).legacy.node_id for c in default} == {
        "scan", "catalog", "semantic_analysis", "temporal_index",
        "prosody_analysis"}
    with pytest.raises(footage_intelligence.RenRefusal):
        footage_intelligence.select(with_=["object_segmentation"])

    # Ordered by the capabilities' derived requires/effects, not by the
    # edit DAG's topology: a shuffled selection comes out producers first.
    shuffled = tuple(reversed(footage_intelligence.ROSTER))
    assert footage_intelligence.compose(shuffled)[:5] == (
        "footage.scan", "footage.catalog", "semantics.analyse",
        "temporal.index", "prosody.analyse")


def test_persons_measured_off_a_rebuilt_frame_sample_are_rebuilt(
        tmp_path, memory_root):
    """Defect: reusing M3 because its digest matches, after the M2 frames
    it was measured from were re-sampled (its faces index other frames)."""
    lane = next(l for l in footage_analysis.LANES if l.name == "persons")
    digest = "ab" * 32
    sdir = source_memory.source_dir(digest)
    _write_2(sdir / source_memory.SLOT_PERSONS, {"content_digest": digest})
    _write_2(sdir / source_memory.SLOT_FRAMES_INDEX, {"content_digest": digest})
    old = os.stat(sdir / source_memory.SLOT_FRAMES_INDEX).st_mtime - 60
    os.utime(sdir / source_memory.SLOT_PERSONS, (old, old))
    assert not footage_analysis.slot_is_fresh(lane, digest, None)

    os.utime(sdir / source_memory.SLOT_PERSONS, None)  # now newer than M2
    assert footage_analysis.slot_is_fresh(lane, digest, None)


def test_export_carries_no_local_path_and_no_default_confidence(
        tmp_path, memory_root):
    """Defects: a shared export that names this machine's folders, and a
    transcript confidence of 0.0 that no transcriber measured."""
    project, media, digest, size = _project_with_one_source(tmp_path)
    sdir = source_memory.source_dir(digest)
    _write_2(sdir / source_memory.SLOT_SOURCE, {
        "content_digest": digest, "size_bytes": size,
        "observed_paths": [media], "duration_seconds": 10.0})
    _write_2(sdir / source_memory.SLOT_TRANSCRIPT,
           _transcript(digest, media, {"arm": "hybrid", "aligner": "mfa"}))

    portable, local = memory_export.build_export(project)
    text = json.dumps(portable)
    assert media not in text and str(tmp_path) not in text
    asset = portable["assets"][0]
    assert asset["asset_id"] == f"sha256:{digest}"
    assert asset["transcript"]["confidence"] is None
    assert asset["transcript"]["utterances"][0]["words"][0] == ["hello", 1.0, 1.4]
    # A slot nobody built is absent, not an empty measurement.
    assert asset["persons"] == {"status": "absent"}
    assert asset["status"] == "partial"
    assert local["assets"][f"sha256:{digest}"]["observed_path"] == media


def test_export_refuses_a_record_that_grows_a_path_field(tmp_path, memory_root):
    """Defect: a lane adds a path-bearing field to its record and the
    export ships it - the check must fail the export, not the reader."""
    project, media, digest, size = _project_with_one_source(tmp_path)
    sdir = source_memory.source_dir(digest)
    _write_2(sdir / source_memory.SLOT_SOURCE, {
        "content_digest": digest, "size_bytes": size, "duration_seconds": 10.0})
    _write_2(sdir / source_memory.SLOT_TRANSCRIPT, _transcript(
        digest, media, {"arm": "hybrid", "wav": media + ".ch1.wav"}))

    with pytest.raises(memory_export.PathLeak, match="instrument"):
        memory_export.build_export(project)


def test_a_clock_measured_after_every_transcript_is_not_rebuilt(memory_root):
    """Defect: re-analysing the bare folder rebuilds M6 from a collection
    with no Resolve timeline, overwriting the recorded cross-check with null.
    A transcript that lands after the clock still forces the rebuild."""
    a, b = "a1" * 32, "b2" * 32
    sources = [{"clip_id": "clip_001", "digest": a},
               {"clip_id": "clip_002", "digest": b}]
    for digest in (a, b):
        sdir = source_memory.source_dir(digest)
        _write_2(sdir / source_memory.SLOT_TRANSCRIPT, {"content_digest": digest})
        then = os.stat(sdir / source_memory.SLOT_TRANSCRIPT).st_mtime - 60
        os.utime(sdir / source_memory.SLOT_TRANSCRIPT, (then, then))
    _write_2(source_memory.source_dir(a) / source_memory.SLOT_CLOCK,
           {"content_digest": a})  # b is in no group: no record, by design
    assert footage_analysis.clock_is_fresh(sources, None)

    os.utime(source_memory.source_dir(b) / source_memory.SLOT_TRANSCRIPT, None)
    later = os.stat(source_memory.source_dir(b)
                    / source_memory.SLOT_TRANSCRIPT).st_mtime + 5
    os.utime(source_memory.source_dir(b) / source_memory.SLOT_TRANSCRIPT,
             (later, later))
    assert not footage_analysis.clock_is_fresh(sources, None)
