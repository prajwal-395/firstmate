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
from library.tools import footage_analysis, memory_export, person_measurements


REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_MODULES = ("footage_query", "footage_segments", "footage_frames")


# ─── A project on disk, built from nothing ────────────────────────


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _flow_values():
    """A 5 Hz optical-flow series for clip_001: four phases of 5 s each.

    A sample at time t covers the interval ending at t, so the phases
    tile the 20 s clip with no gap: pan right [0,5], static [5,10],
    tilt up [10,15], mixed [15,20].
    """
    values = []
    phases = [("right", 0.0, 5.0, 0.5, 0.0, 0.5),
              ("static", 5.0, 10.0, 0.0, 0.0, 0.0),
              ("up", 10.0, 15.0, 0.0, -0.5, 0.5),
              ("mixed", 15.0, 20.0, 0.0, 0.0, 0.4)]
    for direction, lo, hi, dx, dy, mag in phases:
        t = lo + 0.2
        while t <= hi + 1e-9:
            values.append({"time": round(t, 3), "dx": dx, "dy": dy,
                           "magnitude": mag, "direction": direction,
                           "camera_tx": dx, "camera_ty": dy,
                           "method": "farneback"})
            t += 0.2
    return values


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
        # 1.04 optical flow: the deterministic 5 Hz camera movement
        # classification (Gap 13). clip_002 carries none, so it keeps the
        # VLM description as the only measurement.
        "optical_flow_direction": {
            "sample_rate_hz": 5,
            "values": _flow_values(),
            "dominant_motion": "mixed",
            "dominant_direction": "unknown",
            "method": "farneback",
            "motion_peaks": [],
        },
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
    monkeypatch.setattr(footage_query, "load_embedder", _stub_embedder)
    index_dir = tmp_path / "index"
    stats = build_index(project, index_dir=index_dir)
    return FootageIndex(project, index_dir=index_dir), stats


def _stub_embedder():
    """A deterministic embedder: each text as a normalised bag of characters."""
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


@pytest.fixture
def overlapping_index(project, tmp_path, monkeypatch):
    """An index whose transcript segments SHARE their boundaries.

    The segmenter splits a sentence and lets the two halves overlap by
    a tenth of a second, so a match often touches the segment BEFORE
    the one it sits in.  The passage around the match is measured from
    the segment the match touches, so this shape is what decides where
    the window starts.
    """
    monkeypatch.setattr(footage_query, "load_embedder", _stub_embedder)
    path = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
            / "index" / "clip_001.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["speech_regions"] = [
        {"start": 1.0, "end": 3.0, "text": "the first thing we said",
         "words": [{"word": "first", "start": 1.0, "end": 1.2}]},
        {"start": 2.9, "end": 5.0, "text": "and the second thing",
         "words": [{"word": "second", "start": 3.0, "end": 3.3}]},
        {"start": 6.0, "end": 8.0, "text": "and then the third thing",
         "words": [{"word": "third", "start": 6.0, "end": 6.3}]},
    ]
    path.write_text(json.dumps(doc), encoding="utf-8")
    index_dir = tmp_path / "overlapping"
    build_index(project, index_dir=index_dir)
    return FootageIndex(project, index_dir=index_dir)


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


# ─── Camera movement is the deterministic measurement ───────────────
#
# Gap 13: the search index used the VLM's 10 s window description, so the
# finer-grained 5 Hz optical-flow classification was unqueryable. A query
# for camera movement returned VLM windows, not the measured spans.


def test_camera_movement_is_cut_from_the_deterministic_measurement(project):
    """The 5 Hz optical-flow classification is the primary camera segment.

    The defect this prevents: with only the VLM description indexed, a
    camera-movement query returned one 10 s window per clip no matter how
    often the measured direction changed. The deterministic runs are the
    finest boundary the measurement made, so they are the segments.
    """
    segments = [s for s in build_segments(project)
                if s.kind == "camera" and s.clip_id == "clip_001"]
    # Four runs of constant direction at 5 Hz, not one 20 s VLM window.
    assert [(s.start, s.end) for s in segments] == [
        (0.0, 5.0), (5.0, 10.0), (10.0, 15.0), (15.0, 20.0)]
    assert [s.facets["movement"] for s in segments] == [
        "right", "static", "up", "mixed"]
    assert all(s.facets["movement_method"] == "optical_flow"
               for s in segments)
    # The VLM window enriches each run at the window level.
    assert all(s.facets["vlm_mode"] == "selfie" for s in segments)
    assert all(s.facets["vlm_framing"] == "close-up" for s in segments)
    assert all(s.facets["vlm_movement"] == "walking" for s in segments)
    # The deterministic clip-level summary is queryable too.
    assert all(s.facets["dominant_motion"] == "mixed" for s in segments)
    # The text leads with the deterministic measurement, then the VLM words.
    assert segments[0].text.startswith("panning right shot")
    assert "selfie" in segments[0].text


def test_camera_movement_falls_back_to_vlm_when_unmeasured(project):
    """A clip with no optical flow measurement keeps the VLM description.

    The defect this prevents: a clip whose flow was never measured would
    have had no camera segments at all if the index required the
    deterministic signal. The VLM windows are the only measurement there,
    and ``movement_method`` says which signal produced the facet.
    """
    segments = [s for s in build_segments(project)
                if s.kind == "camera" and s.clip_id == "clip_002"]
    assert len(segments) == 1
    assert segments[0].start == 0.0 and segments[0].end == 10.0
    assert segments[0].facets["movement"] == "static"
    assert segments[0].facets["movement_method"] == "vision_vlm"
    assert "vlm_mode" not in segments[0].facets


def test_a_gap_in_the_flow_series_breaks_the_run(project):
    """An unmeasured pair is not evidence the direction held across it.

    The defect this prevents: treating the series as continuous would
    bridge a gap where nothing was measured and present a span the
    instrument never classified. A time gap starts a new run.
    """
    doc = {
        "optical_flow_direction": {
            "sample_rate_hz": 5,
            "values": [
                {"time": 0.2, "direction": "right", "method": "farneback"},
                {"time": 0.4, "direction": "right", "method": "farneback"},
                # 0.6 and 0.8 unmeasured: a gap, not a continuation.
                {"time": 1.0, "direction": "right", "method": "farneback"},
            ],
        }
    }
    runs = footage_segments._flow_runs(doc)
    assert [(r["start"], r["end"]) for r in runs] == [(0.0, 0.4), (0.8, 1.0)]


# ─── Building and querying ────────────────────────────────────────


def test_search_finds_the_utterance_and_the_word(offline_index):
    idx, _ = offline_index
    hits = idx.search("parking", top_k=3, mode="lexical")
    top = hits[0]
    assert top["kind"] == "speech" and top["clip_id"] == "clip_001"
    # The utterance that MATCHED is named on its own: the row's window
    # is the passage around it (see the neighbour tests below).
    assert top["matched_start"] == 1.0 and top["matched_end"] == 3.0
    assert top["word_hits"] == [{"word": "parking", "start": 2.0, "end": 2.4}]


def test_a_text_hit_carries_the_neighbouring_utterance(offline_index):
    """A passage is what an editor asks for; the index's unit is an utterance.

    The window a text hit offers reaches the utterance immediately after
    the one that matched, and says which one it reached.
    """
    idx, _ = offline_index
    top = idx.search("parking", top_k=3, mode="lexical")[0]
    assert top["start"] == 1.0, "no utterance precedes the first one"
    assert top["end"] == 7.5, "the window must reach the next utterance"
    assert top["matched_end"] == 3.0, "the match itself is the utterance"
    assert top["neighbours"]["before"] == []
    after = top["neighbours"]["after"]
    assert len(after) == 1
    assert after[0]["text"] == "the pollen is terrible today"
    assert after[0]["start"] == 6.0 and after[0]["end"] == 7.5


def test_the_score_stays_on_the_matched_utterance(offline_index):
    """The neighbours are context, not evidence: they carry no score.

    A hit's score describes the segment that matched.  A wider window
    that inherited the neighbour's score would rank a passage by
    something nobody asked about.
    """
    idx, _ = offline_index
    report = idx.search_report("parking", top_k=3, mode="lexical")
    top = report["results"][0]
    position = next(n for n, s in enumerate(idx.segments)
                    if s["segment_id"] == top["segment_id"])
    lexical = idx.bm25.scores("parking")
    assert top["lexical_score"] == round(float(lexical[position]), 4)
    assert top["lexical_score"] > 0
    assert top["matched_start"] == idx.segments[position]["start"]
    assert top["matched_end"] == idx.segments[position]["end"]


def test_a_non_speech_hit_is_not_widened(offline_index):
    """An action, a scene or an object is its own span.

    There is no transcript around it to widen into, and a widened
    picture row would claim a span the picture measurement never made.
    """
    idx, _ = offline_index
    hits = idx.search("bicycle", top_k=5, mode="lexical")
    rows = [hit for hit in hits if hit["kind"] != "speech"]
    assert rows, "the object row must still be findable"
    for row in rows:
        assert row["start"] == row["matched_start"]
        assert row["end"] == row["matched_end"]
        assert "neighbours" not in row


def test_the_passage_is_measured_around_the_segment_the_match_touches(
        overlapping_index):
    """Consecutive transcript segments SHARE their boundary.

    A match that sits at the shared boundary touches the segment
    BEFORE it as well, and the passage around the match starts there:
    that is where the passage an editor names usually lies.  The row
    says which segment it is anchored on, because the anchor is not
    always the matched one.
    """
    idx = overlapping_index
    top = idx.search("second", top_k=3, mode="lexical")[0]
    assert top["kind"] == "speech"
    assert top["matched_start"] == 2.9 and top["matched_end"] == 5.0
    assert top["passage_anchor"]["text"] == "the first thing we said"
    assert top["passage_anchor"]["start"] == 1.0
    assert top["start"] == 1.0, "the window must cover the anchor it names"
    assert top["end"] == 5.0


def test_a_match_that_touches_nothing_earlier_is_anchored_on_itself(
        overlapping_index):
    """The anchor is the matched segment when nothing else touches it.

    A passage is measured around a MATCH, never around a segment that
    merely happens to be near it.
    """
    idx = overlapping_index
    top = idx.search("third", top_k=3, mode="lexical")[0]
    assert top["kind"] == "speech"
    assert top["matched_start"] == 6.0 and top["matched_end"] == 8.0
    assert top["passage_anchor"]["text"] == "and then the third thing"
    before = top["neighbours"]["before"]
    assert len(before) == 1
    assert before[0]["text"] == "and the second thing"
    assert top["start"] == 2.9 and top["end"] == 8.0


def test_the_window_always_covers_the_matched_utterance(overlapping_index):
    """A passage carries the match; it never loses it.

    A window that started after the matched utterance, or ended before
    it, would hand the caller a span the query never matched.
    """
    idx = overlapping_index
    for word in ("first", "second", "third"):
        top = idx.search(word, top_k=3, mode="lexical")[0]
        assert top["kind"] == "speech"
        assert top["start"] <= top["matched_start"]
        assert top["end"] >= top["matched_end"]

def test_a_neighbour_is_never_borrowed_from_another_clip(offline_index):
    """The neighbour lives in the SAME clip as the matched utterance.

    A clip whose only utterance has none has no neighbour, and the
    absence is empty rather than another clip's words.
    """
    idx, _ = offline_index
    for segment in idx.segments:
        if segment["kind"] != "speech":
            continue
        hit = idx.search_report(segment["text"], top_k=5, mode="lexical")
        rows = [r for r in hit["results"]
                if r["segment_id"] == segment["segment_id"]]
        if not rows:
            continue
        row = rows[0]
        for neighbour in row["neighbours"]["before"] + row["neighbours"]["after"]:
            assert neighbour["segment_id"].startswith(
                f"{row['clip_id']}#speech#"), (
                "a neighbour from another clip would put another "
                "recording's words in this window")


def test_lexical_search_works_with_no_embedder_at_all(project, tmp_path, monkeypatch):
    """The index must degrade to keyword search, not to nothing.

    `sentence-transformers` is not in requirements.txt and was not
    installed in the environment this was built in, so "the embedder is
    missing" is the normal case, not the exotic one.
    """
    monkeypatch.setattr(footage_query, "load_embedder", lambda: (None, "none"))
    before = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    index_dir = tmp_path / "nodense"
    stats = build_index(project, index_dir=index_dir)
    after = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    assert before == after, "building the index must not touch the project"
    assert (index_dir / footage_query.SEGMENTS_FILE).exists()
    payload = json.loads((index_dir / footage_query.SEGMENTS_FILE).read_text())
    assert payload["text_lane"]["status"] == "degraded"
    assert stats["text_lane"]["status"] == "degraded"
    idx = FootageIndex(project, index_dir=index_dir)
    assert idx.matrix is None
    report = idx.search_report("pollen", top_k=1, mode="hybrid")
    assert report["results"][0]["kind"] == "speech"
    assert report["answer_status"] == "insufficient_evidence"
    assert report["backend_answered"] == "bm25"
    assert "keyword match only" in report["degraded"]
    assert "error" in idx.search("pollen", mode="dense")[0]


def test_visual_intent_on_transcript_only_index_says_not_analysed(
        project, tmp_path, monkeypatch):
    """The GEO cup/shot distractor cannot turn transcript words into picture evidence."""
    for profile in (project / "pipeline_output" / "steps" /
                    "1_03_semantic_analysis").glob("clip_profile_*.json"):
        profile.unlink()
    monkeypatch.setattr(footage_query, "load_embedder", lambda: (None, "missing"))
    index_dir = tmp_path / "transcript-only"
    build_index(project, index_dir=index_dir)
    idx = FootageIndex(project, index_dir=index_dir)

    report = idx.search_report("find the shot of a cup", mode="hybrid")

    assert report["query_intent"] == "visual"
    assert report["answer_status"] == "not_analysed"
    assert report["results"] == []
    assert "not analysed" in report["error"]
    assert report["coverage"]["per_clip"]["clip_001"]["picture"] is False


def test_visual_intent_returns_measured_picture_rows_not_speech(offline_index):
    idx, _ = offline_index
    report = idx.search_report("find the shot of a bicycle", mode="lexical")
    assert report["answer_status"] == "insufficient_evidence"
    assert report["results"]
    assert all(hit["kind"] != "speech" for hit in report["results"])


def test_index_coverage_records_modalities_per_clip(offline_index):
    idx, _ = offline_index
    coverage = idx.payload["coverage"]["per_clip"]
    assert coverage["clip_001"]["speech"] is True
    assert coverage["clip_001"]["scene"] is True
    assert coverage["clip_001"]["action"] is True
    assert coverage["clip_001"]["object"] is True
    assert coverage["clip_001"]["ocr"] is False
    assert coverage["clip_001"]["expressions"] is False
    assert coverage["clip_001"]["video_embeddings"] is False


def test_visual_query_can_return_ocr_evidence_without_a_transcript_row(
        project, tmp_path, monkeypatch):
    ocr_path = (project / "pipeline_output" / "steps" /
                "1_07_ocr_extraction" / "clip_001" / "ocr_result.json")
    _write(ocr_path, {"detections": [{"text": "BLUE CUP", "confidence": 0.91,
                                      "appearances": [[4.0, 6.0]]}]})
    monkeypatch.setattr(footage_query, "load_embedder", lambda: (None, "missing"))
    index_dir = tmp_path / "ocr-index"
    build_index(project, index_dir=index_dir)
    idx = FootageIndex(project, index_dir=index_dir)

    report = idx.search_report("find the shot of a cup", mode="hybrid")

    assert report["coverage"]["per_clip"]["clip_001"]["ocr"] is True
    assert report["results"][0]["kind"] == "ocr"
    assert report["results"][0]["text"] == "BLUE CUP"
    assert report["results"][0]["start"] == 4.0


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
    # Rank every row, not the top five: the best DENSE score is a max
    # over the whole candidate set, and a top-k cut can hide the row
    # that holds it once chunk rows join the ranking.
    unfloored = idx.search_report("parking", top_k=len(idx.segments), floor=0)
    # The best DENSE score, not the top combined row: the hybrid ranking
    # blends dense with lexical, so the first result is not necessarily the
    # highest cosine. The floor is set above the best cosine any segment
    # actually reached.
    best = max(r["dense_score"] for r in unfloored["results"])

    report = idx.search_report("parking", top_k=5, floor=best + 0.01)
    assert report["results"] == [], "nothing cleared the floor, so nothing comes back"
    assert report["abstained"] is True
    assert report["best_rejected"]["dense_score"] == best, (
        "an abstain that cannot name its near miss reads as a broken search"
    )
    assert report["considered"] == len(idx.segments)
    assert report["retained"] == 0
    assert report["answer_status"] == "not_found"

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


def test_analysis_completion_records_project_model_source_and_result(monkeypatch):
    from library.tools import analysis_state, step_ledger

    captured = {}
    node = {"id": "semantic_analysis", "name": "Semantic Analysis"}

    class Runner:
        @staticmethod
        def _manifest_map(nodes):
            return {"semantic_analysis": {"stage": "preflight"}}

        @staticmethod
        def _clear_step_failure(state, node_id):
            pass

        @staticmethod
        def save_pipeline_state(project, state):
            pass

        @staticmethod
        def _export_step_for_review(*args):
            pass

    monkeypatch.setattr(analysis_state, "_editing_state_services",
                        lambda: Runner)
    monkeypatch.setattr(analysis_state, "_nodes",
                        lambda runner: {"semantic_analysis": node})
    monkeypatch.setattr(step_ledger, "stage_of", lambda manifest, node_id: "preflight")
    monkeypatch.setattr(step_ledger, "ingest_mark", lambda project: None)
    monkeypatch.setattr(step_ledger, "record",
                        lambda state, stage, node_id, completion: captured.update(completion))
    analysis_state.record_completion(
        "fixture-project", {}, "semantics.analyse", "semantic_analysis", {}, 1.0,
        {"revision": 3, "head_hash": "source-head"},
        {"revision": 4, "head_hash": "result-head"},
    )

    assert captured["project_model"] == {
        "source": {"revision": 3, "head_hash": "source-head"},
        "result": {"revision": 4, "head_hash": "result-head"},
    }


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

    monkeypatch.setattr(footage_query, "load_embedder", fake_loader)
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


def _private_root(project) -> Path:
    """Where a project-scoped reader looks for this project's source
    measurements: they are private to the project that measured them."""
    return source_memory.project_memory_root(str(project))


def test_person_filter_keeps_only_that_person_s_clip(offline_index_2, memory_root):
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(_private_root(idx.project_folder), digest_1, 1.0, 3.0)

    results = idx.filter(person="person_001")
    assert {r["segment_id"] for r in results} == {
        "clip_001#speech#000", "clip_001#chunk#000"}, (
        "span overlap selects the fine row and the 8s window covering it")
    assert all(r["clip_id"] == "clip_001" for r in results)

    # A span that does not cover the segment's time range does not match
    # it: 10.0-10.2s is nowhere near the 1.0-3.0s speech segment - but
    # the 8-16s chunk covering 10.0-10.2s still answers, which is the
    # coarse guarantee doing its job where no fine row was cut.
    _write_identity(_private_root(idx.project_folder), digest_1, 10.0, 10.2)
    results = idx.filter(person="person_001")
    assert [r["segment_id"] for r in results] == ["clip_001#chunk#001"]


def test_technical_quality_filters_require_measured_numeric_facets(offline_index_2):
    idx, _, _ = offline_index_2
    measured = idx.segments[0]
    measured["facets"].update({
        "sharpness_laplacian_variance": 240.0,
        "temporal_noise_residual": 0.015,
        "compression_blockiness_proxy": 0.02,
        "underexposed_fraction": 0.001,
        "overexposed_fraction": 0.002,
        "camera_translation_magnitude": 0.05,
        "subject_energy": 0.3,
        "brightness": 0.4,
        "motion": 0.15,
        "subject_center_x": 0.7,
        "subject_width_fraction": 0.2,
    })

    assert [row["segment_id"] for row in idx.filter(
        min_sharpness=200.0, max_temporal_noise=0.02,
        max_blockiness=0.03, min_brightness=0.3, max_motion=0.2,
        max_underexposed_fraction=0.01, max_overexposed_fraction=0.01,
        max_camera_translation=0.1, min_subject_energy=0.1,
        max_subject_energy=0.5,
        min_subject_center_x=0.6, min_subject_width_fraction=0.15
    )] == [measured["segment_id"]]
    # No recorded value is not evidence that a segment passes a bound.
    assert all(row["segment_id"] != idx.segments[1]["segment_id"]
               for row in idx.filter(min_sharpness=1.0))
    assert all(row["segment_id"] != idx.segments[1]["segment_id"]
               for row in idx.filter(max_overexposed_fraction=0.5))
    assert all(row["segment_id"] != idx.segments[1]["segment_id"]
               for row in idx.filter(max_camera_translation=10.0))


def test_person_filter_unknown_name_raises_rather_than_matching_nothing(
        offline_index_2, memory_root):
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(_private_root(idx.project_folder), digest_1, 1.0, 3.0)

    with pytest.raises(RenRefusal):
        idx.filter(person="nobody-by-this-name")


def test_search_report_with_person_filter_answers_who_says_okay(
        offline_index_2, memory_root):
    """The captain's example from the scout report: 'who says okay' -
    speaker-attributed text search, through the real `search_report`
    path (text match + person filter), not just `filter` alone."""
    idx, digest_1, digest_2 = offline_index_2
    _write_identity(_private_root(idx.project_folder), digest_2, 2.0, 4.0)

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
    private = source_memory.project_memory_root(str(root))
    source_memory.write_sound(
        digest_1, str(root / "raw" / "IMG_0001.MOV"), "measured",
        [{"label": "Laughter", "start": 2.0, "end": 4.5, "confidence": 0.87},
         {"label": "Speech", "start": 6.0, "end": 8.0, "confidence": 0.92}],
        "PANNs Cnn14", root=private)
    source_memory.write_sound(
        digest_2, str(root / "raw" / "IMG_0002.MOV"), "measured",
        [{"label": "Applause", "start": 1.0, "end": 3.0, "confidence": 0.78}],
        "PANNs Cnn14", root=private)

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

    monkeypatch.setattr(footage_query, "load_embedder", fake_loader)
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


# On-screen text search (step 1.07's output)
#
# `ren search --ocr <text>`: on-screen strings tracked by step 1.07's
# EasyOCR extraction, joined into search by text.  The defect this
# guards: "where does the SCUFFLEWA sign appear" was unanswerable from
# search - the text was measured and stored on disk but no query could
# reach it, so the 445s extraction bought nothing.
#

def _write_ocr_result(root: Path, clip_id: str, detections: list):
    """Write one clip's `ocr_result.json` where step 1.07 leaves it."""
    out = root / "pipeline_output" / "steps" / "1_07_ocr_extraction" / clip_id
    _write(out / "ocr_result.json", {
        "video_path": str(root / "raw" / f"{clip_id}.MOV"),
        "frame_count": 100,
        "resolution": [1920, 1080],
        "sample_fps": 1.0,
        "detections": detections,
        "raw_frame_detections": {},
    })


@pytest.fixture
def ocr_index(project_2, tmp_path, monkeypatch):
    """An index over a project whose step 1.07 extraction is on disk."""
    root, _digest_1, _digest_2 = project_2
    _write_ocr_result(root, "clip_001", [
        {"text": "SCUFFLEWA BREWING CO", "confidence": 0.91,
         "appearances": [[2.0, 5.5]], "bbox": [100, 200, 300, 40],
         "bbox_normalized": [0.05, 0.18, 0.16, 0.04], "is_static": True},
        {"text": "Chattahoochee Ave NW", "confidence": 0.87,
         "appearances": [[8.0, 12.0]], "bbox": [50, 400, 250, 30],
         "bbox_normalized": [0.03, 0.37, 0.13, 0.03], "is_static": True},
    ])
    _write_ocr_result(root, "clip_002", [
        {"text": "EXIT", "confidence": 0.95,
         "appearances": [[1.0, 4.0]], "bbox": [800, 50, 120, 60],
         "bbox_normalized": [0.42, 0.05, 0.06, 0.06], "is_static": True},
    ])

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

    monkeypatch.setattr(footage_query, "load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    build_index(root, index_dir=index_dir)
    return FootageIndex(root, index_dir=index_dir)


def test_ocr_search_finds_the_measured_text(ocr_index):
    """The defect: 'where does the SCUFFLEWA sign appear' was unanswerable.

    Step 1.07 measured the on-screen text and stored it on disk, but no
    query could reach it - search had no OCR lane.  This pins that the
    measured text now returns its spans.
    """
    report = ocr_index.ocr_search("SCUFFLEWA")
    assert report["abstained"] is False
    assert len(report["hits"]) == 1
    hit = report["hits"][0]
    assert hit["clip_id"] == "clip_001"
    assert hit["text"] == "SCUFFLEWA BREWING CO"
    assert hit["start"] == 2.0
    assert hit["end"] == 5.5
    assert hit["confidence"] == 0.91


def test_hybrid_search_returns_precise_ocr_span_for_exact_text_query(ocr_index):
    """An exact text query should return the measured OCR appearance,
    even when the query does not say "on screen" or "show me".
    """
    baseline = ocr_index.search_report("SCUFFLEWA", mode="hybrid")
    assert all(hit["kind"] != "ocr" for hit in baseline["results"])
    report = ocr_index.search_report("SCUFFLEWA", mode="hybrid",
                                    signal_merge=True)
    assert report["results"][0]["kind"] == "ocr"
    assert report["results"][0]["text"] == "SCUFFLEWA BREWING CO"
    assert report["results"][0]["start"] == 2.0
    assert report["results"][0]["end"] == 5.5


def test_signal_merge_cli_flag_reaches_search(ocr_index, capsys):
    result = footage_query.main([
        "search", ocr_index.project_folder, "SCUFFLEWA", "--signal-merge",
        "--index-dir", str(ocr_index.index_dir),
    ])
    assert result == 0
    assert "SCUFFLEWA BREWING CO" in capsys.readouterr().out


def test_shot_type_lane_matches_measured_camera_facets(tmp_path):
    idx = FootageIndex(tmp_path)
    idx._payload = {"segments": [{
        "segment_id": "clip_001#camera#000", "clip_id": "clip_001",
        "kind": "camera", "text": "medium mounted stationary stable shot",
        "facets": {"framing": "medium", "camera_mode": "mounted",
                   "stability": "stable", "movement": "stationary",
                   "scene_type": "vehicle"},
    }]}
    assert idx._shot_type_scores("medium stationary shot inside the car")[0] > 0
    assert idx._shot_type_scores("aerial night shot")[0] == 0


def test_ocr_match_specificity():
    """Single shared letters are not a text match; exact words are.

    Round-2 bakeoff finding: the both-directions substring rule let
    single-character tracked strings ("a", "N", "D") match any visual
    query sharing the letter ("d" is in "handheld"), flooding "find the
    shaky handheld footage" with OCR rows ahead of the true camera
    window.  Exact words still match at any length; substrings count
    only at OCR_SUBSTRING_MIN or above.
    """
    assert footage_query.ocr_token_matches("handheld", "d") is False
    assert footage_query.ocr_token_matches("handheld", "n") is False
    assert footage_query.ocr_token_matches("shop", "shop") is True
    assert footage_query.ocr_token_matches(
        "chattahoochee", "chattahoochee") is True
    assert footage_query.ocr_token_matches("cup", "cups") is False
    assert FootageIndex._text_matches(
        ["shaky", "handheld", "footage"], "D") is False
    assert FootageIndex._text_matches(
        ["shaky", "handheld", "footage"], "a (") is False
    assert FootageIndex._text_matches(
        ["shaky", "handheld", "footage"], "ITHE") is False
    assert FootageIndex._text_matches(["shop"], "SHOP") is True
    assert FootageIndex._text_matches(
        ["chattahoochee", "ave", "nw"], "Chattahoochee Ave NW") is True


def test_ocr_search_matches_across_clips_and_says_what_is_there(ocr_index):
    """A string in more than one clip returns every span, and a string
    that is not there returns an honest empty naming the text this
    footage carries - never a silent 'nowhere' that reads as 'not
    measured'."""
    both = ocr_index.ocr_search("Chattahoochee")
    assert [h["clip_id"] for h in both["hits"]] == ["clip_001"]

    missing = ocr_index.ocr_search("STOP sign")
    assert missing["abstained"] is True
    assert missing["hits"] == []
    assert "SCUFFLEWA BREWING CO" in missing["available_texts"]
    assert "EXIT" in missing["available_texts"]


def test_ocr_search_with_no_texts_says_the_step_never_ran(offline_index):
    """An index whose project has no OCR extraction says so, rather than
    answering 'nowhere' to a question that was never measured."""
    idx, _stats = offline_index
    report = idx.ocr_search("SCUFFLEWA")
    assert report["abstained"] is True
    assert "no on-screen text" in report["error"]


def test_ocr_search_cli_reaches_the_index(ocr_index, project_2, monkeypatch):
    """`ren search --ocr <text>` is the person's route to the same
    answer - the flag must reach the module and refuse without a query."""
    root = project_2[0]
    from library.tools.analysis import footage_query as fq

    calls = []

    def fake_ocr_search(self, query, top_k=5):
        calls.append((query, top_k))
        return {"query": query, "hits": [], "abstained": True,
                "considered": 0, "matched": 0, "available_texts": [],
                "error": None}

    monkeypatch.setattr(FootageIndex, "ocr_search", fake_ocr_search)
    monkeypatch.setattr(sys, "argv", [
        "footage_query", "search", str(root), "--ocr", "SCUFFLEWA"])
    assert fq.main(["search", str(root), "--ocr", "SCUFFLEWA"]) == 0
    assert calls == [("SCUFFLEWA", 5)]

    with pytest.raises(RenRefusal):
        fq.main(["search", str(root), "--ocr"])


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
    monkeypatch.setattr(footage_frames.source_memory, "m2_frames_present",
                        lambda *_args, **_kwargs: True)


@pytest.fixture
def frame_index(project_3, tmp_path, stub_clip, stub_m2):
    index_dir = tmp_path / "findex"
    footage_frames.build_frame_index(project_3, index_dir=index_dir)
    return FrameIndex(project_3, index_dir=index_dir)


# ─── Ranges: the unit a person scrubs ───────────────────────────────


def test_adjacent_hits_merge_and_distant_ones_do_not(frame_index):
    """t=0/10 on one clip are one range; t=20 is the next sighting, and
    t=100 and the other clip are not in it."""
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5)
    assert report["answer_status"] == "found"
    assert report["refused"] is None
    ranges = report["ranges"]
    assert [(r["clip_id"], r["start"], r["end"]) for r in ranges] == [
        ("clip_001", 0.0, 10.0),
        ("other_002", 5.0, 5.0),
    ], ("adjacent pool hits merge per clip; anything else stays split, and "
        "a clip contributes ONE range, so clip_001's second sighting at "
        "t=100 does not take a second slot")
    head = ranges[0]
    assert head["n_frames"] == 2, (
        "a range holds at most two sampled frames of its source")
    assert head["timecode"] == "00:00.000-00:10.000"
    assert head["best_score"] == pytest.approx(max(
        h["score"] for h in report["results"] if h["clip_id"] == "clip_001"
        and h["t"] <= 10.0))

    # A gap wider than the merge gap splits.
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5,
                                       merge_gap_s=5.0)
    clips = [(r["clip_id"], r["start"], r["end"]) for r in report["ranges"]]
    assert ("clip_001", 0.0, 0.0) in clips
    assert ("other_002", 5.0, 5.0) in clips
    assert not [c for c in clips if c[0] == "clip_001" and c[1] == 10.0], (
        "a 10 s gap with a 5 s merger must not merge, and the second range "
        "of a clip that already has one does not take a slot")


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
    assert report["answer_status"] == "found"
    assert report["refused"] is None
    assert len(report["results"]) == 3, (
        "all-zero scores still return top-k: ranking is the answer")


# ─── The pool, the diversity, and the floor ──────────────────────────
#
# Three defects the frame lane carried, each measured on the editorial
# retrieval corpus (2026-10-20):
#
#   * the candidate pool was 40 frames of a 1092-frame index, and the
#     frames the editor wants sit at a median rank of 396-696 - below
#     the pool, so the lane could not see them at all;
#   * the five windows were the five best ranges of WHICHEVER camera
#     scored well, so one camera could take all five and the other
#     assets were never shown;
#   * the lane could not abstain, so an absent picture was handed to the
#     editor as five wrong windows (10 of the 41 false-positive windows
#     and 44 % of the false-positive seconds of the merged set).


def _hand_written_index(tmp_path, monkeypatch, frames, matrix, vector):
    """A frame index written by hand: no model, no ffmpeg, no project."""
    index_dir = tmp_path / "handwritten"
    index_dir.mkdir()
    _write(index_dir / footage_frames.FRAME_INDEX_FILE, {
        "clip_model": "test-stub", "embed_backend": "test-stub",
        "frames": frames})
    np.save(index_dir / footage_frames.FRAME_EMBEDDINGS_FILE, matrix)
    monkeypatch.setattr(FrameIndex, "_encode_text",
                        lambda self, query: vector)
    return FrameIndex(tmp_path / "proj", index_dir=index_dir)


def _crowded_index(tmp_path, monkeypatch):
    """One busy camera and one quiet one.

    `clip_a` holds 150 frames that all score 0.9 and sit 60 s apart,
    so each is its own range; `clip_b` holds the frame the editor
    wants, at 0.5 - ranked below every one of clip_a's.  The wanted
    frame is at RANK 151 of 151, so a pool of 40 cannot see it.
    """
    frames = []
    matrix = np.zeros((151, 4), dtype="float32")
    for i in range(150):
        frames.append({"clip_id": "clip_a", "filename": "A.MOV",
                       "t": float(i * 60)})
        matrix[i, 0] = 0.9
    frames.append({"clip_id": "clip_b", "filename": "B.MOV", "t": 3000.0})
    matrix[150, 0] = 0.5
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
    return index


def test_the_pool_reaches_a_frame_ranked_below_the_old_forty(
        tmp_path, monkeypatch):
    """The wanted frame was in the index and RANKED, not missing - the
    pool of 40 was what kept it out of the answer."""
    index = _crowded_index(tmp_path, monkeypatch)

    short_pool = index.search_ranges("the wanted thing", top_ranges=5,
                                      pool=40, per_source_pool=0)
    assert "clip_b" not in {r["clip_id"] for r in short_pool["ranges"]}, (
        "a pool of 40 cannot reach a frame ranked 151st")

    report = index.search_ranges("the wanted thing", top_ranges=5)
    assert "clip_b" in {r["clip_id"] for r in report["ranges"]}, (
        "the shipped pool must reach the frames the editor wants")
    assert report["search_policy"]["candidate_pool_size"] == 200


def test_the_pool_never_starves_a_short_camera(tmp_path, monkeypatch):
    """A global cut is a cut BY SOURCE SIZE, and the wanted moment of a
    short camera was out of the pool before anything ranked it.

    Measured on the editorial retrieval corpus (2026-10-21): 99 of the
    167 uncredited wanted items of the held-out set - 59 % - were never
    IN the candidate pool, because the two close-up cameras the editor
    wanted hold 23 and 22 of the corpus's 1092 sampled frames and a
    global pool of 200 was mostly the long cameras'.  So every source
    contributes its own best candidates to the pool, whatever its length.
    """
    index = _crowded_index(tmp_path, monkeypatch)

    report = index.search_ranges("the wanted thing", top_ranges=5, pool=40)
    policy = report["search_policy"]
    assert policy["per_source_pool_size"] == footage_frames.PER_SOURCE_POOL
    assert policy["sources_considered"] == 2, (
        "a source with ONE frame must still be in the pool: the global cut "
        "is what starved it")
    assert policy["candidates_considered"] > 40, (
        "the quota adds candidates on top of the global cut")
    assert "clip_b" in {r["clip_id"] for r in report["ranges"]}, (
        "the wanted frame of the quiet camera must reach the answer")

    # With the quota off, the lane is the one that shipped: a global cut
    # and one source's frames.
    narrow = index.search_ranges("the wanted thing", top_ranges=5, pool=40,
                                 per_source_pool=0)
    assert narrow["search_policy"]["sources_considered"] == 1
    assert narrow["search_policy"]["per_source_pool_size"] == 0


def test_a_query_asking_for_repeated_events_gets_two_windows(
        tmp_path, monkeypatch):
    """One window per source is the rule, and it is the RIGHT rule for a
    query that asks for one shot.

    "Every time the laptop is on the desk" asks for the OCCURRENCES: a
    rule that gives one window per source answers it with a single
    sighting and calls it the whole thing.  So the query decides - not
    the score - and the second window must be a different event.
    """
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": float(t)}
              for t in (0.0, 60.0, 120.0)]
    matrix = np.zeros((3, 4), dtype="float32")
    matrix[:, 0] = 0.5
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    once = index.search_ranges("a shot of the laptop on the desk",
                               top_ranges=5)
    assert len(once["ranges"]) == 1, (
        "one occurrence asked for: one window per source")
    assert once["search_policy"]["windows_per_source"] == 1

    repeated = index.search_ranges(
        "every time the laptop is on the desk", top_ranges=5)
    policy = repeated["search_policy"]
    assert policy["windows_per_source"] == 2, (
        "the query asks for more than one occurrence, so a source may "
        "contribute a second window")
    spans = [(r["start"], r["end"]) for r in repeated["ranges"]]
    assert len(spans) == 2, f"two occurrences, two windows: {spans}"
    gap = abs(spans[1][0] - spans[0][1])
    assert gap > footage_frames.MERGE_GAP_S, (
        f"the second window must be a different event: {spans}")


def test_a_candidate_window_is_not_a_verified_moment(tmp_path, monkeypatch):
    """A deeper pool is more CANDIDATES, not more finds: every window
    stays unverified and the answer says how much was considered."""
    index = _crowded_index(tmp_path, monkeypatch)
    report = index.search_ranges("the wanted thing", top_ranges=5)

    assert "cannot be concluded" in report["notice"]
    for span in report["ranges"]:
        assert span["verified"] is False
    policy = report["search_policy"]
    assert policy["candidates_considered"] >= policy["top_interval_limit"], (
        "the report says how many candidates the ranking read")
    assert policy["sources_considered"] >= 1


def test_five_windows_do_not_all_come_from_one_camera(tmp_path, monkeypatch):
    """One camera must not take every slot: the editor is shown the
    same angle five times and never sees the other assets."""
    frames = []
    matrix = np.zeros((11, 4), dtype="float32")
    for i in range(10):
        frames.append({"clip_id": "clip_a", "filename": "A.MOV",
                       "t": float(i * 60)})
        matrix[i, 0] = 0.9 - i * 0.01
    frames.append({"clip_id": "clip_b", "filename": "B.MOV", "t": 3000.0})
    matrix[10, 0] = 0.5
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    report = index.search_ranges("the wanted thing", top_ranges=5)
    clips = [r["clip_id"] for r in report["ranges"]]
    assert "clip_b" in clips, (
        "the quiet camera's best range must reach the answer")
    assert len(clips) == len(set(clips)), (
        f"a clip must not take two slots: {clips}")


def test_a_query_below_the_floor_abstains_and_names_its_near_miss(
        tmp_path, monkeypatch):
    """An absent picture must not be handed over as five wrong windows.
    The lane abstains below the floor - and an abstain that cannot name
    its near miss reads as a broken search."""
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": 0.0},
              {"clip_id": "clip_a", "filename": "A.MOV", "t": 600.0}]
    matrix = np.zeros((2, 4), dtype="float32")
    matrix[0, 0] = 0.12
    matrix[1, 0] = 0.05
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    report = index.search_ranges("a birthday cake with candles")
    assert report["abstained"] is True
    assert report["ranges"] == []
    assert report["answer_status"] == "abstained"
    assert report["near_miss"] is not None
    assert report["near_miss"]["score"] == pytest.approx(0.12)
    assert "not a claim" in report["notice"], (
        "an abstain must not claim the subject is absent from the footage")


def test_no_floor_ranks_whatever_the_frames_scored(tmp_path, monkeypatch):
    """`floor=None` turns the floor off: the lane ranks rather than goes
    silent, which is what a caller without a negative corpus needs."""
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": 0.0},
              {"clip_id": "clip_a", "filename": "A.MOV", "t": 600.0}]
    matrix = np.zeros((2, 4), dtype="float32")
    matrix[0, 0] = 0.12
    matrix[1, 0] = 0.05
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    report = index.search_ranges("a birthday cake with candles", floor=None)
    assert report["abstained"] is False
    assert len(report["ranges"]) == 1
    assert report["search_policy"]["query_floor"] is None


def test_the_measured_floor_is_the_shipped_default(tmp_path, monkeypatch):
    """The lane abstains at the MEASURED cutoff, and the cutoff is the
    one the LABELLED negative corpus measured (VI-004, 2026-10-22)."""
    assert footage_frames.MEASURED_QUERY_FLOOR == 0.1611
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": 0.0}]
    matrix = np.zeros((1, 4), dtype="float32")
    matrix[0, 0] = 0.1499
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
    report = index.search_ranges("a birthday cake with candles")
    assert report["abstained"] is True
    assert report["search_policy"]["query_floor"] == 0.1611


def test_the_window_floor_drops_a_weak_window_and_keeps_the_query(
        tmp_path, monkeypatch):
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": 0.0},
              {"clip_id": "clip_b", "filename": "B.MOV", "t": 600.0}]
    matrix = np.zeros((2, 4), dtype="float32")
    matrix[0, 0] = 0.30
    matrix[1, 0] = 0.10
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    report = index.search_ranges("a thing", window_floor=0.2)
    assert [r["clip_id"] for r in report["ranges"]] == ["clip_a"]
    assert report["abstained"] is False


def test_a_chain_of_sightings_is_not_one_moment(tmp_path, monkeypatch):
    """A range is ONE sighting, and a sighting is what the samples of a
    source show: two samples ten seconds apart bracket ten seconds of
    footage.

    The chain used to be bounded by the merge gap alone, and at the
    shipped thirty-second cap a run of hits 10 s apart became one range
    of thirty seconds - and, before that, a range of minutes.  A range
    now holds at most TWO sampled frames of its source, so the run is
    three sightings and not one, and a range that claims more time than
    the evidence carries is not offered at all.
    """
    frames = [{"clip_id": "clip_a", "filename": "A.MOV", "t": float(t)}
              for t in (0.0, 10.0, 20.0, 30.0, 40.0, 50.0)]
    matrix = np.zeros((6, 4), dtype="float32")
    for i in range(6):
        matrix[i, 0] = 0.5 - i * 0.01
    index = _hand_written_index(
        tmp_path, monkeypatch, frames, matrix,
        np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    report = index.search_ranges("a thing", top_ranges=5)
    spans = [(r["start"], r["end"]) for r in report["ranges"]]
    assert spans == [(0.0, 10.0)], (
        f"a run may not hold more than two samples of one source: {spans}")

    # The caps are the caller's to lift.
    report = index.search_ranges("a thing", top_ranges=5, max_range_s=None,
                                 max_range_frames=None)
    spans = [(r["start"], r["end"]) for r in report["ranges"]]
    assert spans == [(0.0, 50.0)], (
        f"with the caps off the run is one range again: {spans}")


def test_the_visual_floor_reaches_the_lane_from_the_cli(
        project_2, tmp_path, monkeypatch, capsys):
    """`--visual-floor` names the floor and `--no-visual-floor` turns it
    off: a caller with no negative corpus to measure a cutoff against
    must be able to ask for the ranking anyway."""
    root, _digest_1, _digest_2 = project_2
    index_dir = tmp_path / "vindex"
    index_dir.mkdir()
    _write(index_dir / footage_frames.FRAME_INDEX_FILE, {
        "clip_model": "test-stub", "embed_backend": "test-stub",
        "frames": [{"clip_id": "clip_001", "filename": "IMG_0001.MOV",
                    "t": 2.0}]})
    matrix = np.zeros((1, 4), dtype="float32")
    matrix[0, 0] = 0.12
    np.save(index_dir / footage_frames.FRAME_EMBEDDINGS_FILE, matrix)
    monkeypatch.setattr(
        FrameIndex, "_encode_text",
        lambda self, query: np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))

    assert footage_query.main(
        ["search", str(root), "a birthday cake", "--visual", "--json",
         "--index-dir", str(index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["abstained"] is True, (
        "the measured floor is the shipped default: a query below it "
        "abstains instead of returning a wrong window")

    assert footage_query.main(
        ["search", str(root), "a birthday cake", "--visual", "--json",
         "--no-visual-floor", "--index-dir", str(index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["abstained"] is False
    assert answer["hits"], "with the floor off the lane ranks anyway"


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
    # A current-stamp record: this test names the mtime ordering, not
    # the slot version (a pre-v2 stamp reads stale whatever the mtimes
    # say - see test_m3_pose_capture_quality).
    _write_2(sdir / source_memory.SLOT_PERSONS, {
        "content_digest": digest,
        "instrument": {"method": person_measurements.M3_METHOD}})
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


# --------------------------------------------------------------------------
# Shot colour: the colour track's registration, exposed to search
#
# The colour track measures each shot (exposure, white balance, contrast,
# saturation, skin vector), delivers a per-clip grade and matches cameras
# within a scene; those readings land on the project graph's shot nodes.
# The defect this guards: an agent asking "what contrast does this shot
# have" or "do these two angles match" had no route to the measurement -
# the graph held the registration but no query could reach it.
#

def _colour_project(tmp_path):
    """A two-clip project carrying the colour track's recorded output."""
    root = tmp_path / "proj"
    root.mkdir()
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0},
            {"clip_id": "clip_002", "source_file": "/camB/take1.MXF",
             "duration_seconds": 10.0},
        ]},
        "color_grade.resolve": {
            "clip_exposure": [
                {"clip_id": "clip_001", "luma": 145.5, "contrast": 42.0,
                 "saturation": 0.31, "skin_rgb": [180, 150, 130],
                 "neutral_rb": 1.02, "neutral_gb": 0.99},
                {"clip_id": "clip_002", "luma": 53.1, "contrast": 38.0,
                 "saturation": 0.28, "skin_rgb": [170, 140, 120],
                 "neutral_rb": 0.97, "neutral_gb": 1.01},
            ],
            "color_grade_spec": {"per_clip_adjustments": [
                {"clip_id": "clip_001", "correction_terms": ["exposure_stops"],
                 "cdl_values": {"slope_r": 0.87, "slope_g": 0.9,
                                "slope_b": 0.95, "offset_r": 0.0,
                                "offset_g": 0.0, "offset_b": 0.0,
                                "power_r": 1.0, "power_g": 1.0,
                                "power_b": 1.0, "saturation": 1.0}},
            ]},
            "camera_match": {"matches": [
                {"camera": "camB", "clips": ["clip_002"],
                 "saturation_factor": 1.02,
                 "skin_slope": [1.03, 1.0, 0.97],
                 "contrast_ratio": 1.1, "gap_direction": "cools"},
            ]},
        },
    }}
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    return root


def test_shot_colour_query_returns_the_registered_measurement(tmp_path):
    """The query an agent asks reaches the colour track's three readings:
    the measured row, the delivered grade and the camera match. On
    origin/main the query name is unknown and the answer is an error."""
    root = _colour_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("shot_colour", clip_id="clip_002")

    assert report["error"] is None
    assert len(report["results"]) == 1
    record = report["results"][0]
    assert record["measurement"]["luma"] == 53.1
    assert record["measurement"]["neutral_rb"] == 0.97
    assert record["match"]["gap_direction"] == "cools"
    # The colourist left clip_002 alone: no grade entry exists.
    assert record["grade"] is None


def test_shot_colour_query_returns_nothing_for_an_unregistered_shot(tmp_path):
    """A shot the colour track never measured reads as no results, not
    as a default measurement."""
    root = _colour_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("shot_colour", clip_id="clip_999")

    assert report["error"] is None
    assert report["results"] == []


def test_shot_colour_query_refuses_without_a_clip(tmp_path):
    root = _colour_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("shot_colour")

    assert report["error"] == "shot_colour needs --clip"
    assert report["results"] == []


def test_shot_colour_cli_reaches_the_index(tmp_path, monkeypatch):
    """`ren search-index project-graph shot_colour --clip` is the agent's
    route to the same answer - the query name must be a CLI choice."""
    root = _colour_project(tmp_path)
    from library.tools.analysis import footage_query as fq

    calls = []

    def fake_project_graph(self, query, **kwargs):
        calls.append((query, kwargs))
        return {"query": query, "mode": "project_graph", "node_count": 0,
                "edge_count": 0, "results": [], "error": None}

    monkeypatch.setattr(FootageIndex, "project_graph", fake_project_graph)
    assert fq.main(["project-graph", str(root), "shot_colour",
                    "--clip", "clip_001"]) == 0
    assert calls == [("shot_colour", {"person_id": None, "around_t": None,
                                   "at_time": None,
                                   "clip_id": "clip_001",
                                   "timeline_start": None,
                                   "timeline_end": None,
                                   "segment_id": None,
                                   "final": None,
                                   "kind": None,
                                   "exploration": None,
                                   "timeline": None,
                                   "label": None,
                                   "task": None,
                                   "event_type": None,
                                   "level": None})]


def test_shot_colour_is_in_the_llm_tool_definitions():
    """The tool definitions the LLM is handed carry find_shot_colour with
    a required clip_id - the contract the agent's call depends on. On
    origin/main the agent cannot ask for a shot's colour at all."""
    functions = FootageIndex.get_tool_definitions()
    by_name = {t["function"]["name"]: t["function"] for t in functions}
    assert "find_shot_colour" in by_name
    params = by_name["find_shot_colour"]["parameters"]
    assert params["required"] == ["clip_id"]


# --------------------------------------------------------------------------
# Reel versions, editorial findings, VFX detections: the graph's newer
# nodes, exposed to search
#
# The graph registers reel versions, the compile's report-only findings
# and VFX detections, but the agent's route to the graph only answered
# the five original queries. The defect: asking "which versions has
# this reel held", "what dead sections did the compile find" or "what
# VFX was detected on this clip" answered "unknown query".
#

def _versioned_project(tmp_path):
    """A one-clip project with a two-version ledger."""
    root = tmp_path / "proj"
    root.mkdir()
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0},
        ]},
    }}
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    review_dir = root / "pipeline_output" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "reel_versions.json").write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {
            "reel_001": [
                {"version": 1, "kind": "build",
                 "at": "2026-10-06T10:00:00+00:00",
                 "rows": {}, "rows_digest": "abc123"},
                {"version": 2, "kind": "touch",
                 "at": "2026-10-06T11:00:00+00:00",
                 "rows": {}, "rows_digest": "def456"},
            ],
        },
    }), encoding="utf-8")
    return root


def test_reel_versions_query_lists_versions_for_a_final(tmp_path):
    """The versions the ledger recorded answer through the search
    route, oldest first. On origin/main the query name is unknown."""
    root = _versioned_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("reel_versions", final="reel_001")

    assert report["error"] is None
    assert [r["version"] for r in report["results"]] == [1, 2]
    assert report["results"][1]["kind"] == "touch"


def test_reel_versions_query_reads_an_unversioned_final_as_empty(tmp_path):
    root = _versioned_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("reel_versions", final="reel_999")

    assert report["error"] is None
    assert report["results"] == []


def _findings_project(tmp_path):
    """A one-clip project whose compile reported a dead section and a
    VFX detection through the edit graph."""
    root = tmp_path / "proj"
    root.mkdir()
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 30.0},
        ]},
        "vfx.resolve": {"enhancement_spec": [
            {"position": 1, "clip_id": "clip_001",
             "effect": "stabilization"},
        ]},
    }}
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    manifest_dir = (root / "pipeline_output" / "steps"
                    / "5_04_compile_manifest")
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "assembly_manifest.json").write_text(json.dumps({
        "dead_sections": [
            {"timeline_start": 14.0, "timeline_end": 17.5,
             "duration_seconds": 3.5, "block_position": 2,
             "clip_id": "clip_001",
             "reasons": ["no speech", "static picture"]},
        ],
    }), encoding="utf-8")
    return root


def test_editorial_findings_query_returns_the_compile_findings(tmp_path):
    """The compile's dead section answers through the search route
    with its span and reasons. On origin/main the query name is
    unknown."""
    root = _findings_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("editorial_findings", kind="dead_section")

    assert report["error"] is None
    assert len(report["results"]) == 1
    row = report["results"][0]
    assert row["kind"] == "dead_section"
    assert row["record"]["timeline_start"] == 14.0
    assert row["record"]["reasons"] == ["no speech", "static picture"]


def test_editorial_findings_query_refuses_an_unknown_kind(tmp_path):
    root = _findings_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("editorial_findings", kind="no_such_kind")

    assert report["results"] == []
    assert "no_such_kind" in report["error"]


def test_vfx_detections_query_returns_the_detection_for_a_clip(tmp_path):
    """The edit graph's stabilization detection answers through the
    search route. On origin/main the query name is unknown."""
    root = _findings_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("vfx_detections", clip_id="clip_001")

    assert report["error"] is None
    assert len(report["results"]) == 1
    assert report["results"][0]["effect"] == "stabilization"


def test_vfx_detections_query_reads_a_clean_clip_as_empty(tmp_path):
    root = _findings_project(tmp_path)
    idx = FootageIndex(str(root))

    report = idx.project_graph("vfx_detections", clip_id="clip_999")

    assert report["error"] is None
    assert report["results"] == []


def test_new_graph_queries_are_cli_choices(tmp_path, monkeypatch):
    """`ren search-index project-graph <query>` reaches each new query -
    the query name must be a CLI choice, like shot_colour before it."""
    root = _findings_project(tmp_path)
    from library.tools.analysis import footage_query as fq

    calls = []

    def fake_project_graph(self, query, **kwargs):
        calls.append((query, kwargs))
        return {"query": query, "mode": "project_graph", "node_count": 0,
                "edge_count": 0, "results": [], "error": None}

    monkeypatch.setattr(FootageIndex, "project_graph", fake_project_graph)
    assert fq.main(["project-graph", str(root), "editorial_findings",
                    "--kind", "dead_section"]) == 0
    assert fq.main(["project-graph", str(root), "reel_versions",
                    "--final", "reel_001"]) == 0
    assert fq.main(["project-graph", str(root), "vfx_detections",
                    "--clip", "clip_001"]) == 0
    assert fq.main(["project-graph", str(root), "audio_events",
                    "--clip", "clip_001", "--label", "music"]) == 0
    assert [c[0] for c in calls] == ["editorial_findings", "reel_versions",
                                     "vfx_detections", "audio_events"]
    assert calls[0][1]["kind"] == "dead_section"
    assert calls[1][1]["final"] == "reel_001"
    assert calls[2][1]["clip_id"] == "clip_001"
    assert calls[3][1]["clip_id"] == "clip_001"
    assert calls[3][1]["label"] == "music"


def test_new_graph_queries_are_in_the_llm_tool_definitions():
    """The tool definitions carry the three new functions, and the
    finding-kind enum stays the graph's own vocabulary - a kind the
    graph stops recording disappears from the agent's options."""
    from library.tools import project_graph
    functions = FootageIndex.get_tool_definitions()
    by_name = {t["function"]["name"]: t["function"] for t in functions}
    for name in ("find_reel_versions", "find_editorial_findings",
                 "find_vfx_detections", "find_audio_events"):
        assert name in by_name
    kind_param = by_name["find_editorial_findings"]["parameters"][
        "properties"]["kind"]
    assert kind_param["enum"] == list(project_graph.FINDING_KINDS)
    assert by_name["find_editorial_findings"]["parameters"]["required"] == []
    assert by_name["find_reel_versions"]["parameters"]["required"] == []
    assert by_name["find_vfx_detections"]["parameters"]["required"] == []


# --------------------------------------------------------------------------
# Variant branches: the exploration's comparison surface, exposed to search
#
# The graph registers each explored variant, but without a query the
# agent's route to the graph cannot answer "which variants are on the
# table for this timeline". The defect: asking answers "unknown query".
#

def _explored_project(tmp_path, monkeypatch):
    """A one-clip project with a two-variant exploration off a commit."""
    from library.tools import undo_journal as uj
    from library.tools import variant_exploration as vx

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(locks))

    root = tmp_path / "proj"
    root.mkdir()
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0},
        ]},
    }}
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    base = uj.record_commit(
        str(root), parent=None, intent="first touch", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")["id"]
    record = vx.explore_variants(
        str(root), base=base,
        variants=[{"name": "faster-intro",
                   "direction": "tighten the opening"},
                  {"name": "emotional-intro",
                   "direction": "hold the reaction"}],
        agent="ren-worker-07", timeline="Reel 01 - lab")
    return root, record["id"]


def test_variant_branches_query_returns_the_exploration_variants(tmp_path,
                                                                 monkeypatch):
    """The explored variants answer through the search route with
    branch, base and direction. On origin/main the query name is
    unknown."""
    root, _exploration = _explored_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("variant_branches")

    assert report["error"] is None
    assert [r["name"] for r in report["results"]] == [
        "emotional-intro", "faster-intro"]
    assert report["results"][0]["branch"] == "variant emotional-intro"
    assert report["results"][0]["direction"] == "hold the reaction"


def test_variant_branches_query_filters_by_exploration_and_timeline(
        tmp_path, monkeypatch):
    """The comparison narrows to one exploration or one timeline; a
    timeline with no variants reads as no rows."""
    root, exploration = _explored_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("variant_branches",
                               exploration=exploration)
    assert report["error"] is None
    assert len(report["results"]) == 2

    report = idx.project_graph("variant_branches",
                               timeline="Reel 02 - lab")
    assert report["error"] is None
    assert report["results"] == []

    report = idx.project_graph("variant_branches",
                               exploration="no-such-exploration")
    assert report["error"] is None
    assert report["results"] == []


def test_tool_definitions_carry_find_variant_branches(tmp_path):
    """The agent's tool vocabulary names the new query - a capability
    the tools list never offers is not offered."""
    from library.tools import project_graph
    functions = FootageIndex.get_tool_definitions()
    by_name = {t["function"]["name"]: t["function"] for t in functions}
    assert "find_variant_branches" in by_name
    assert by_name["find_variant_branches"]["parameters"]["required"] == []
    assert by_name["find_audio_events"]["parameters"]["required"] == []
    audio_props = by_name["find_audio_events"]["parameters"]["properties"]
    assert set(audio_props) == {"clip_id", "label"}


# --------------------------------------------------------------------------
# Audio events: what the analysis heard, exposed to search
#
# The graph registers every measured audio event, but the agent's route
# to the graph never answered one. The defect: asking "when does the
# music change" - the spec's own example - answered "unknown query".
#

def _audio_project(tmp_path, monkeypatch):
    """A one-clip project whose world model carries sound events."""
    root = tmp_path / "proj"
    root.mkdir()
    digest = "a" * 64
    state = {"capability_outputs": {
        "footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
             "duration_seconds": 10.0, "content_digest": digest},
        ]},
    },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest},
        },
    }
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))
    model_dir = memory_root / digest
    model_dir.mkdir(parents=True)
    (model_dir / "world_model.json").write_text(json.dumps({
        "timeline": [
            {"t": 5, "sound": {"events": [
                {"label": "music", "start": 0.0, "end": 4.0,
                 "confidence": 0.9},
                {"label": "laughter", "start": 5.0, "end": 6.5,
                 "confidence": 0.8},
            ]}},
        ],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    }), encoding="utf-8")
    (model_dir / "identity.json").write_text(json.dumps({
        "faces": [], "voices": [], "speech_face_links": [],
    }), encoding="utf-8")
    return root


def test_audio_events_query_returns_what_was_heard(tmp_path, monkeypatch):
    """The measured music span answers through the search route with
    its clip, span and confidence. On origin/main the query name is
    unknown and the answer is an error."""
    root = _audio_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("audio_events", clip_id="clip_001",
                               label="music")

    assert report["error"] is None
    assert len(report["results"]) == 1
    row = report["results"][0]
    assert row["clip_id"] == "clip_001"
    assert row["label"] == "music"
    assert row["start"] == 0.0
    assert row["end"] == 4.0


def test_audio_events_query_reads_a_quiet_clip_as_empty(tmp_path, monkeypatch):
    root = _audio_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("audio_events", clip_id="clip_001",
                               label="silence")

    assert report["error"] is None
    assert report["results"] == []


# --------------------------------------------------------------------------
# Commits, operations, decisions, participants, proposals: the newer
# graph queries, exposed to search
#
# The graph registers creative commits, worker operations, decided
# reaction cutaways, roster participants and open proposals, but the
# agent's route answered none of them. The defect, five times over:
# asking "what changed on Reel 1", "what did the workers do", "why
# this cutaway", "who may approve" or "what awaits approval"
# answered "unknown query".
#

def _governance_project(tmp_path, monkeypatch):
    """A one-clip project with a commit, an operation, a roster, a
    proposal and a decided reaction cutaway."""
    from library.tools import event_log
    from library.tools import fabric_permissions as permissions
    from library.tools import undo_journal as uj

    memory_root = tmp_path / "memory"
    monkeypatch.setenv("PIPELINE_SOURCE_MEMORY_ROOT", str(memory_root))

    root = tmp_path / "proj"
    root.mkdir()
    digest = "a" * 64
    (root / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": "/camA/take1.MXF",
                 "duration_seconds": 30.0, "content_digest": digest},
            ]},
        },
        "source_fingerprints": {
            "clip_001": {"content_digest": digest},
        },
    }), encoding="utf-8")
    model_dir = memory_root / digest
    model_dir.mkdir(parents=True)
    (model_dir / "world_model.json").write_text(json.dumps({
        "timeline": [],
        "entities": {"face_tracks": [], "voice_tracks": []},
        "multicam": None,
    }), encoding="utf-8")
    (model_dir / "identity.json").write_text(json.dumps({
        "faces": [], "voices": [], "speech_face_links": [],
    }), encoding="utf-8")

    uj.record_commit(
        str(root), parent=None, intent="tighten opening", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")
    event_log.emit(str(root), "lease_acquired", task="ren-04",
                   resource="resolve")
    permissions.grant(str(root), "client", permissions.SUGGEST)
    permissions.grant(str(root), "director", permissions.APPROVE)
    permissions.record_proposal(
        str(root), "client",
        {"kind": "merge", "timeline": "Reel 01 - lab",
         "branch": "branch-1", "left": "commit-left",
         "right": "commit-right"},
        "hold the reaction longer")

    manifest_dir = (root / "pipeline_output" / "steps"
                    / "5_04_compile_manifest")
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "assembly_manifest.json").write_text(json.dumps({
        "reaction_cutaways": [
            {"speech_block_position": 1, "speech_clip_id": "clip_001",
             "speaking_person_id": "person_001",
             "speech_timeline_start": 2.0, "speech_timeline_end": 8.0,
             "reaction_clip_id": "clip_001",
             "reacting_person_id": "person_001",
             "reaction_source_start": 1.0, "reaction_source_end": 4.0,
             "timing": {"speech_moment": 8.0, "reaction_land": 8.0,
                        "offset_seconds": 0.0},
             "reason": "visible while speaking",
             "decision": {
                 "action": "Use clip_001 as reaction cutaway",
                 "reason": "visible while speaking",
                 "evidence": {},
                 "constraints": {},
                 "style_source": None,
                 "confidence": 0.6,
                 "confidence_source": "measured span overlap",
             }},
        ],
    }), encoding="utf-8")
    return root


def test_commits_query_answers_what_changed_on_a_timeline(
        tmp_path, monkeypatch):
    """The recorded touch answers through the search route with its
    intent. On origin/main the query name is unknown."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("commits", timeline="Reel 01 - lab")

    assert report["error"] is None
    assert [r["intent"] for r in report["results"]] == ["tighten opening"]
    assert idx.project_graph(
        "commits", timeline="Reel 9")["results"] == []


def test_project_events_query_answers_what_the_workers_did(
        tmp_path, monkeypatch):
    """The emitted lease answers with task and type. On origin/main
    the query name is unknown."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("project_events", task="ren-04")

    assert report["error"] is None
    assert len(report["results"]) == 1
    assert report["results"][0]["type"] == "lease_acquired"


def test_reaction_decisions_query_answers_why_this_cutaway(
        tmp_path, monkeypatch):
    """The recorded decision answers with its reason. On origin/main
    the query name is unknown."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("reaction_decisions", clip_id="clip_001")

    assert report["error"] is None
    assert len(report["results"]) == 1
    assert report["results"][0]["decision"]["reason"] == (
        "visible while speaking")


def test_participants_query_answers_who_may_approve(
        tmp_path, monkeypatch):
    """Asking who may approve returns the director, never the
    observe-and-suggest client. On origin/main the query name is
    unknown."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("participants", level="approve")

    assert report["error"] is None
    assert [r["participant"] for r in report["results"]] == ["director"]


def test_participants_query_refuses_without_a_level(tmp_path, monkeypatch):
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("participants")

    assert report["results"] == []
    assert "level" in report["error"]


def test_participants_query_refuses_an_unknown_level(
        tmp_path, monkeypatch):
    """A level nothing grants is refused, not matched against
    nothing."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("participants", level="overlord")

    assert report["results"] == []
    assert "overlord" in report["error"]


def test_proposals_query_answers_what_awaits_approval(
        tmp_path, monkeypatch):
    """The open proposal answers with its rationale. On origin/main
    the query name is unknown."""
    root = _governance_project(tmp_path, monkeypatch)
    idx = FootageIndex(str(root))

    report = idx.project_graph("proposals")

    assert report["error"] is None
    assert len(report["results"]) == 1
    assert report["results"][0]["rationale"] == "hold the reaction longer"


def test_newer_graph_queries_are_cli_choices(tmp_path, monkeypatch):
    """Each new query name is a CLI choice carrying its flags."""
    root = _governance_project(tmp_path, monkeypatch)
    from library.tools.analysis import footage_query as fq

    calls = []

    def fake_project_graph(self, query, **kwargs):
        calls.append((query, kwargs))
        return {"query": query, "mode": "project_graph", "node_count": 0,
                "edge_count": 0, "results": [], "error": None}

    monkeypatch.setattr(FootageIndex, "project_graph", fake_project_graph)
    assert fq.main(["project-graph", str(root), "commits",
                    "--timeline-name", "Reel 01 - lab"]) == 0
    assert fq.main(["project-graph", str(root), "project_events",
                    "--task", "ren-04"]) == 0
    assert fq.main(["project-graph", str(root), "reaction_decisions",
                    "--clip", "clip_001", "--t", "5.0"]) == 0
    assert fq.main(["project-graph", str(root), "participants",
                    "--level", "approve"]) == 0
    assert fq.main(["project-graph", str(root), "proposals"]) == 0
    assert [c[0] for c in calls] == ["commits", "project_events",
                                     "reaction_decisions", "participants",
                                     "proposals"]


def test_newer_graph_queries_are_in_the_llm_tool_definitions():
    """The tool definitions carry each new function under the graph
    function's own name - the contract the agent's call depends on."""
    functions = FootageIndex.get_tool_definitions()
    by_name = {t["function"]["name"]: t["function"] for t in functions}
    for name in ("find_commits", "find_project_events",
                 "find_reaction_decisions", "participants_with",
                 "open_proposals"):
        assert name in by_name
    assert by_name["participants_with"]["parameters"]["required"] == [
        "level"]
    assert by_name["open_proposals"]["parameters"]["required"] == []
# ─── External evidence (Resolve IntelliSearch) ───────────────────
#
# Ren search stays primary: Resolve rows arrive as EXTRA evidence beside
# Ren's answer, never blended into it. These pin the merge - the offline
# half of the lane. The Resolve-side collector needs live Resolve under
# qualification_project.borrowed() and lands in a later slice.


INTELLISEARCH_ROWS = [
    {"clip_id": "clip_002", "start": 2.0, "end": 4.0,
     "text": "empty street", "collected_by": "resolve_intellisearch_ui"},
    {"clip_id": "clip_001", "start": 6.0, "end": 7.5,
     "text": "pollen", "score": 0.99},
]


def test_external_evidence_leaves_the_ren_answer_untouched(offline_index):
    """A naive merge interleaves Resolve rows with Ren's ranking; this one
    must not move a single Ren field."""
    idx, _ = offline_index
    plain = idx.search_report("parking", top_k=5, floor=0)
    merged = idx.search_report("parking", top_k=5, floor=0,
                               external_hits=INTELLISEARCH_ROWS)
    assert "external_evidence" not in plain
    for key in ("results", "weak", "abstained", "considered", "retained"):
        assert merged[key] == plain[key], (
            f"external evidence moved Ren's {key!r}")
    assert [r["clip_id"] for r in merged["external_evidence"]] == [
        "clip_002", "clip_001"]


def test_external_evidence_does_not_clear_a_ren_abstain(offline_index):
    """Ren saying "not here" while Resolve points somewhere is two answers,
    not one "here". A merge that folds external rows into results would
    report Ren as finding what it did not."""
    idx, _ = offline_index
    # Rank every row, not the top five: the best DENSE score is a max
    # over the whole candidate set, and a top-k cut can hide the row
    # that holds it.
    unfloored = idx.search_report("parking", top_k=len(idx.segments),
                                  floor=0)
    best = max(r["dense_score"] for r in unfloored["results"])
    report = idx.search_report("parking", top_k=5, floor=best + 0.01,
                               external_hits=INTELLISEARCH_ROWS)
    assert report["results"] == []
    assert report["abstained"] is True
    assert len(report["external_evidence"]) == 2, (
        "the abstain stands, and the extra evidence stands beside it")


def test_an_external_row_with_no_clip_is_refused_by_name(offline_index):
    """A row that names no clip cannot be placed on any source. It is
    refused with its position and reason - silently dropping it would read
    as "Resolve found one clip" when it returned two rows."""
    idx, _ = offline_index
    hits = [{"clip_id": "clip_001", "text": "pollen"},
            {"text": "nowhere"},
            "not-a-row"]
    report = idx.search_report("parking", top_k=5, floor=0,
                               external_hits=hits)
    assert [r["clip_id"] for r in report["external_evidence"]] == ["clip_001"]
    assert [r["position"] for r in report["external_rejected"]] == [1, 2]
    assert all("clip_id" in r["reason"]
               for r in report["external_rejected"])


def test_no_ren_score_crosses_the_external_merge(offline_index):
    """Every merged row carries its provenance and no Ren score key. A
    reader that cannot tell which engine produced a row reports Resolve's
    guess as Ren's measurement - and the second fixture row smuggling in
    a 0.99 is exactly how that happens."""
    idx, _ = offline_index
    report = idx.search_report("parking", top_k=5, floor=0,
                               external_hits=INTELLISEARCH_ROWS)
    assert report["external_source"] == "resolve_intellisearch"
    for row in report["external_evidence"]:
        assert row["provenance"] == footage_query.EXTERNAL_SOURCE_INTELLISEARCH
        assert not {"score", "dense_score", "lexical_score"} & set(row), (
            "an uncalibrated number crossed the merge")


def test_external_evidence_that_is_not_a_list_is_refused(offline_index):
    idx, _ = offline_index
    report = idx.search_report("parking", top_k=1, floor=0)
    with pytest.raises(RenRefusal):
        footage_query.merge_external_evidence(report, {"clip_id": "clip_001"})


def test_ren_search_merges_an_external_hits_file(offline_index, tmp_path, capsys):
    """`ren search --external-hits` is the caller that ships this slice:
    the flag reaches the merge and the rows print after Ren's answer."""
    idx, _ = offline_index
    hits_path = tmp_path / "ext.json"
    hits_path.write_text(json.dumps(INTELLISEARCH_ROWS), encoding="utf-8")
    assert footage_query.main(
        ["search", str(idx.project_folder), "parking",
         "--index-dir", str(idx.index_dir),
         "--external-hits", str(hits_path)]) == 0
    out = capsys.readouterr().out
    assert "resolve_intellisearch" in out
    assert "NOT Ren-ranked" in out


# ─── Transcript evidence (Resolve transcription) ─────────────────
#
# Same philosophy as footage search: Resolve's transcript arrives as
# EXTRA evidence beside Ren's utterances, provenance-marked, never a
# replacement - and disagreements surface as findings carrying both
# sides. The Resolve-side read (sense transcribe) lands through the
# broker wrapper; this pins the merge, offline.


RESOLVE_TRANSCRIPT = [
    {"text": "we need to find a parking spot", "start": 1.1, "end": 2.9,
     "speaker": "SPEAKER_00"},
    {"text": "the pollen is horrible today", "start": 6.0, "end": 7.5,
     "speaker": "SPEAKER_00"},
    {"text": "thanks for watching", "start": 15.0, "end": 17.0,
     "speaker": "SPEAKER_00"},
]


def test_transcript_merge_leaves_ren_utterances_untouched(offline_index):
    """A merge that rewrites Ren's rows is a replacement wearing extra
    evidence clothes. On origin/main the kwarg does not exist."""
    idx, _ = offline_index
    plain = idx.transcript("clip_001")
    merged = idx.transcript_report("clip_001",
                                   resolve_segments=RESOLVE_TRANSCRIPT)
    assert merged["utterances"] == plain
    assert merged["resolve_source"] == "resolve_transcription"
    assert all(row["provenance"] == "resolve_transcription"
               for row in merged["resolve_segments"])


def test_unmatched_speech_is_a_finding_not_silence(offline_index):
    """Speech one side hears and the other has no span for is a
    finding naming its span - silently dropping either side would read
    as agreement."""
    idx, _ = offline_index
    merged = idx.transcript_report(
        "clip_001",
        resolve_segments=[row for row in RESOLVE_TRANSCRIPT
                          if row["start"] >= 6.0])
    kinds = [finding["kind"] for finding in merged["findings"]]
    assert "ren_only" in kinds
    assert "resolve_only" in kinds
    ren_only = next(finding for finding in merged["findings"]
                    if finding["kind"] == "ren_only")
    assert ren_only["resolve"] is None
    assert "parking" in ren_only["ren"]["text"]
    resolve_only = next(finding for finding in merged["findings"]
                        if finding["kind"] == "resolve_only")
    assert resolve_only["ren"] is None
    assert resolve_only["resolve"]["start"] == 15.0


def test_equal_texts_agree_and_differences_carry_both_sides(
        offline_index):
    """Folded-equal words agree silently; anything else is a finding
    with both texts and both spans for the reader to judge."""
    idx, _ = offline_index
    merged = idx.transcript_report("clip_001", resolve_segments=[
        {"text": "We need to find a parking spot!", "start": 1.1,
         "end": 2.9, "speaker": "SPEAKER_00"},
        {"text": "the pollen is horrible today", "start": 6.0,
         "end": 7.5, "speaker": "SPEAKER_00"},
    ])
    assert len(merged["agreement"]) == 1
    assert len(merged["findings"]) == 1
    finding = merged["findings"][0]
    assert finding["kind"] == "text_differs"
    assert finding["ren"]["text"] == "the pollen is terrible today"
    assert finding["resolve"]["text"] == "the pollen is horrible today"
    assert finding["resolve"]["speaker"] == "SPEAKER_00"


def test_empty_resolve_side_is_stated_not_expanded(offline_index):
    """Resolve transcribing nothing is an answer about Resolve, not a
    finding per utterance - there is nothing to align against."""
    idx, _ = offline_index
    merged = idx.transcript_report("clip_001", resolve_segments=[])
    assert merged["resolve_empty"] is True
    assert merged["findings"] == []
    assert len(merged["utterances"]) > 0


def test_transcript_evidence_that_is_not_a_list_is_refused(offline_index):
    idx, _ = offline_index
    with pytest.raises(RenRefusal):
        footage_query.merge_transcript_evidence([], {"text": "hi"})


def test_ren_transcript_merges_a_resolve_transcript_file(
        offline_index, tmp_path, capsys):
    """`ren search transcript --resolve-transcript` is the caller:
    Ren's rows print, then Resolve's extra evidence with findings."""
    idx, _ = offline_index
    hits_path = tmp_path / "resolve_transcript.json"
    hits_path.write_text(json.dumps(RESOLVE_TRANSCRIPT), encoding="utf-8")
    assert footage_query.main(
        ["transcript", str(idx.project_folder), "clip_001",
         "--index-dir", str(idx.index_dir),
         "--resolve-transcript", str(hits_path)]) == 0
    out = capsys.readouterr().out
    assert "parking spot" in out
    assert "resolve_transcription" in out
    assert "text_differs" in out


def test_best_overlap_wins_regardless_of_order(offline_index):
    """A short utterance barely touching a long segment must not claim
    it ahead of the utterance the segment mostly covers. Measured live
    on clip_004: Ren's opener (1s overlap) stole Resolve's segment from
    the passage holding 10s of it, turning one agreement into a differs
    plus an orphan. Best overlap pairs first, whatever the order."""
    idx, _ = offline_index
    merged = idx.transcript_report("clip_001", resolve_segments=[
        {"text": "the pollen is terrible today", "start": 2.5,
         "end": 10.0},
    ])
    assert len(merged["agreement"]) == 1
    assert merged["agreement"][0]["ren"]["text"] == (
        "the pollen is terrible today")
    assert [f["kind"] for f in merged["findings"]] == ["ren_only"]
    assert "parking" in merged["findings"][0]["ren"]["text"]


def test_pause_markers_are_dropped_and_counted(offline_index):
    """Resolve's literal (...) pause segments carry timing without
    words: aligning them manufactures differs findings against
    punctuation, so they are dropped with the count stated."""
    idx, _ = offline_index
    merged = idx.transcript_report("clip_001", resolve_segments=[
        {"text": "we need to find a parking spot", "start": 1.0,
         "end": 3.0},
        {"text": "(...)", "start": 4.0, "end": 5.0},
    ])
    assert merged["dropped_pause_markers"] == 1
    assert len(merged["agreement"]) == 1
    assert all(f["kind"] != "resolve_only" for f in merged["findings"])


def test_timecode_rows_refuse_without_rate_and_opening(offline_index):
    """The defect: Resolve timecodes silently aligned against nothing -
    every row orphaned on both sides, reading as mass disagreement. On
    origin/main there is no normalization, so this fails there."""
    idx, _ = offline_index
    rows = [{"text": "we need to find a parking spot", "start": "01:00:01:00",
             "end": "01:00:03:00"}]
    with pytest.raises(RenRefusal):
        idx.transcript_report("clip_001", resolve_segments=rows)
    with pytest.raises(RenRefusal):
        idx.transcript_report("clip_001", resolve_segments=rows,
                              resolve_fps="25")
    merged = idx.transcript_report(
        "clip_001", resolve_segments=rows, resolve_fps="25",
        resolve_start_tc="01:00:00:00")
    assert len(merged["agreement"]) == 1
    assert merged["agreement"][0]["ren"]["text"] == (
        "we need to find a parking spot")
    assert [f["kind"] for f in merged["findings"]] == ["ren_only"]


def test_garbage_and_missing_times_are_refused(offline_index):
    """A span that cannot be placed is refused by name, never left to
    read as a finding."""
    idx, _ = offline_index
    with pytest.raises(RenRefusal):
        idx.transcript_report("clip_001", resolve_segments=[
            {"text": "hi", "start": "soon", "end": 3.0}])
    with pytest.raises(RenRefusal):
        idx.transcript_report("clip_001", resolve_segments=[
            {"text": "hi", "end": 3.0}])
    with pytest.raises(RenRefusal):
        footage_query.merge_transcript_evidence([], "not-a-list")


def test_parse_fps_reads_ratios_and_refuses_garbage():
    assert footage_query.parse_fps("24000/1001") == pytest.approx(23.976,
                                                                 abs=0.001)
    assert footage_query.parse_fps("25") == 25.0
    assert footage_query.parse_fps(29.97) == 29.97
    for bad in ("fast", "0", "0/1", "", None):
        with pytest.raises(RenRefusal):
            footage_query.parse_fps(bad)


def test_ren_transcript_converts_timecode_rows(offline_index, tmp_path,
                                               capsys):
    """The CLI takes the rate and opening Resolve timecodes count
    from, end to end."""
    idx, _ = offline_index
    hits_path = tmp_path / "resolve_tc.json"
    hits_path.write_text(json.dumps(
        [{"text": "we need to find a parking spot", "start": "01:00:01:00",
          "end": "01:00:03:00"}]), encoding="utf-8")
    assert footage_query.main(
        ["transcript", str(idx.project_folder), "clip_001",
         "--index-dir", str(idx.index_dir),
         "--resolve-transcript", str(hits_path),
         "--resolve-fps", "25", "--resolve-start-tc", "01:00:00:00"]) == 0
    out = capsys.readouterr().out
    assert "1 in agreement" in out


# --------------------------------------------------------------------------
# LE-06: `ren search --json` on every mode
#
# One stable hit shape - {source_digest, clip_id, file, start_s, end_s,
# kind, score, dense, band, text} - whatever lane answered, so an agent
# parses one schema.  `source_digest` is the clip's content digest
# (stable across renames); `band` is the floor verdict per row.  An
# answer with no strong row carries `abstained: true` plus the floor,
# so "not in this footage" is a field and not prose.
# --------------------------------------------------------------------------

_HIT_KEYS = {"source_digest", "clip_id", "file", "start_s", "end_s",
             "kind", "score", "dense", "band", "text"}


def _stub_loader():
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


@pytest.fixture
def json_text_index(project_2, tmp_path, monkeypatch):
    """A text index over a project whose media files exist, so the live
    content digest resolves and the hit's `source_digest` is checkable."""
    root, digest_1, digest_2 = project_2
    monkeypatch.setattr(footage_query, "load_embedder", _stub_loader)
    index_dir = tmp_path / "json-index"
    build_index(root, index_dir=index_dir)
    return root, index_dir, digest_1, digest_2


def test_search_json_text_hit_matches_the_index(json_text_index, capsys):
    """`ren search <tmp> "<word in fixture>" --json` parses, and the
    first hit's `source_digest` and times match the index."""
    root, index_dir, digest_1, _digest_2 = json_text_index
    assert footage_query.main(
        ["search", str(root), "parking", "--json", "--mode", "lexical",
         "--index-dir", str(index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["abstained"] is False
    assert answer["floor"] is not None
    top = answer["hits"][0]
    assert set(top) == _HIT_KEYS
    assert top["source_digest"] == digest_1
    assert top["clip_id"] == "clip_001"
    assert top["file"] == "IMG_0001.MOV"
    assert (top["start_s"], top["end_s"]) == (1.0, 3.0)
    assert top["band"] == "strong"
    assert "parking" in top["text"]


def test_search_json_nonsense_query_abstains_with_empty_hits(
        json_text_index, capsys):
    """A nonsense query returns `abstained: true` with `hits: []` -
    plus the floor, so the reader knows what judged it absent."""
    root, index_dir, _digest_1, _digest_2 = json_text_index
    assert footage_query.main(
        ["search", str(root), "zxqvkjwobble", "--json", "--mode", "lexical",
         "--index-dir", str(index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["abstained"] is True
    assert answer["hits"] == []
    assert answer["floor"] is not None


def test_search_json_keeps_the_weak_band(json_text_index):
    """Rows below the floor still arrive - with `band: weak` - so an
    abstain names its near miss in the same schema, not in prose."""
    root, index_dir, _digest_1, _digest_2 = json_text_index
    idx = FootageIndex(root, index_dir=index_dir)
    unfloored = idx.search_report("parking", top_k=len(idx.segments), floor=0)
    best = max(r["dense_score"] for r in unfloored["results"])
    answer = footage_query.search_json(
        str(root), "parking", top_k=5, floor=best + 0.01,
        index_dir=index_dir)
    assert answer["abstained"] is True
    assert answer["floor"] == pytest.approx(best + 0.01)
    assert answer["hits"], "the best row sits in the weak band, not nowhere"
    assert all(hit["band"] == "weak" for hit in answer["hits"])
    assert all(answer["weak_floor"] <= hit["dense"] < answer["floor"]
               for hit in answer["hits"])


def test_search_json_sound_lane_shares_the_hit_shape(
        sound_index, project_2, memory_root, capsys):
    """`--sound --json` returns the same hit shape: a measured label is
    strong evidence with no dense cosine and no floor."""
    root, digest_1, _digest_2 = project_2
    assert footage_query.main(
        ["search", str(root), "--sound", "laughter", "--json",
         "--index-dir", str(sound_index.index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["mode"] == "sound"
    assert answer["abstained"] is False
    hit = answer["hits"][0]
    assert set(hit) == _HIT_KEYS
    assert hit["source_digest"] == digest_1
    assert hit["clip_id"] == "clip_001"
    assert hit["kind"] == "sound"
    assert hit["band"] == "strong"
    assert (hit["start_s"], hit["end_s"]) == (2.0, 4.5)
    assert hit["text"] == "Laughter"
    assert hit["score"] == 0.87
    assert hit["dense"] is None


def test_search_json_ocr_lane_shares_the_hit_shape(
        ocr_index, project_2, capsys):
    """`--ocr --json` returns the same hit shape: an exact on-screen
    match is strong, scored by the reader's confidence."""
    root, digest_1, _digest_2 = project_2
    assert footage_query.main(
        ["search", str(root), "--ocr", "scufflewa", "--json",
         "--index-dir", str(ocr_index.index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["mode"] == "ocr"
    assert answer["abstained"] is False
    hit = answer["hits"][0]
    assert set(hit) == _HIT_KEYS
    assert hit["source_digest"] == digest_1
    assert hit["clip_id"] == "clip_001"
    assert hit["kind"] == "ocr"
    assert hit["band"] == "strong"
    assert (hit["start_s"], hit["end_s"]) == (2.0, 5.5)
    assert hit["text"] == "SCUFFLEWA BREWING CO"
    assert hit["score"] == 0.91
    assert hit["dense"] is None


def test_search_json_visual_lane_shares_the_hit_shape(
        project_2, tmp_path, monkeypatch, capsys):
    """`--visual --json` returns the same hit shape.  The frame lane has
    no floor and no abstain - it ranks whatever stills it holds - so
    every range is strong, scored by its best CLIP cosine.  No model is
    loaded: the stills and their vectors are written by hand and only
    the text side is stubbed."""
    root, digest_1, _digest_2 = project_2
    index_dir = tmp_path / "vindex"
    index_dir.mkdir()
    _write(index_dir / footage_frames.FRAME_INDEX_FILE, {
        "clip_model": "test-stub", "embed_backend": "test-stub",
        "frames": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV", "t": 2.0},
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV", "t": 2.5},
            {"clip_id": "clip_002", "filename": "IMG_0002.MOV", "t": 1.0},
        ]})
    matrix = np.zeros((3, 4), dtype="float32")
    matrix[0, 0] = 1.0
    matrix[1, 0] = 0.9
    matrix[2, 1] = 1.0
    np.save(index_dir / footage_frames.FRAME_EMBEDDINGS_FILE, matrix)
    monkeypatch.setattr(
        FrameIndex, "_encode_text",
        lambda self, query: np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
    assert footage_query.main(
        ["search", str(root), "red bicycle", "--visual", "--json",
         "--index-dir", str(index_dir)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["mode"] == "visual"
    assert answer["answer_status"] == "found"
    assert "absence cannot be concluded" in answer["notice"]
    assert answer["abstained"] is False
    top = answer["hits"][0]
    assert set(top) == _HIT_KEYS
    assert top["source_digest"] == digest_1
    assert top["clip_id"] == "clip_001"
    assert top["file"] == "IMG_0001.MOV"
    assert (top["start_s"], top["end_s"]) == (2.0, 2.5)
    assert top["kind"] == "visual"
    assert top["band"] == "unverified"
    assert top["score"] == pytest.approx(1.0)


def test_search_json_chunk_rows_carry_dense_and_band():
    """Video-chunk rows: the score IS the raw cosine, results are
    strong and the floor band is weak."""
    sources = {"clip_001": {"source_digest": "d1", "file": "IMG_0001.MOV"}}
    report = {
        "results": [{"chunk_id": "clip_001#vchunk#000", "clip_id": "clip_001",
                     "filename": "IMG_0001.MOV", "start": 0.0, "end": 8.0,
                     "timecode": "00:00.000-00:08.000", "dense_score": 0.5}],
        "weak": [{"chunk_id": "clip_001#vchunk#001", "clip_id": "clip_001",
                  "filename": "IMG_0001.MOV", "start": 8.0, "end": 16.0,
                  "timecode": "00:08.000-00:16.000", "dense_score": 0.3}],
    }
    hits = footage_query._chunk_json_hits(report, sources)
    assert [hit["band"] for hit in hits] == ["strong", "weak"]
    assert all(set(hit) == _HIT_KEYS for hit in hits)
    assert hits[0]["dense"] == hits[0]["score"] == 0.5
    assert hits[0]["kind"] == "video_chunk"
    assert hits[1]["source_digest"] == "d1"


def test_search_json_unified_rows_carry_band():
    """Unified-anchor rows keep their window kind and description; weak
    rows without a recorded filename fall back to the catalog file."""
    sources = {"clip_001": {"source_digest": "d1", "file": "IMG_0001.MOV"}}
    report = {
        "results": [{"anchor_id": "clip_001#u#000", "clip_id": "clip_001",
                     "filename": "IMG_0001.MOV", "kind": "window",
                     "start": 0.0, "end": 8.0, "description_text": "a lot",
                     "score": 0.7, "dense_score": 0.6}],
        "weak": [{"anchor_id": "clip_001#u#001", "clip_id": "clip_001",
                  "start": 8.0, "end": 16.0, "dense_score": 0.3,
                  "description_text": "a glimpse"}],
    }
    hits = footage_query._unified_json_hits(report, sources)
    assert [hit["band"] for hit in hits] == ["strong", "weak"]
    assert hits[0]["text"] == "a lot"
    assert hits[0]["dense"] == 0.6
    assert hits[1]["file"] == "IMG_0001.MOV"
    assert hits[1]["source_digest"] == "d1"


def test_visual_retrieval_benchmark_reports_uncertainty_and_operating_cost():
    """Acceptance check: this contract is absent on the pre-benchmark main."""
    from library.tools.visual_retrieval_benchmark import enrich_visual_result

    eval_set = {"queries": [{"id": "V", "family": "visual", "query": "open laptop",
                             "category": "object", "positive_frames": {}}]}
    result = {"results": [{"id": "V", "family": "visual", "kind": "present",
                           "recall": 1.0, "precision": 1.0, "returned": 1,
                           "spans_touched": 1, "seconds": 0.01,
                           "rows": [{"hit": True, "ambiguous": False}]}]}
    enrich_visual_result(eval_set, result)
    report = result["visual_benchmark"]
    assert report["macro_recall_ci95"]["n"] == 1
    assert report["baseline"]["status"] == "not measured"
    assert report["useful_interval_cost"]["seconds_per_useful_interval"] == 0.01


def test_the_index_records_the_sources_it_was_cut_from(offline_index_2):
    """An index that cannot say WHICH footage it was cut from cannot say
    which footage changed."""
    idx, digest_1, digest_2 = offline_index_2
    recorded = idx.payload["source_digests"]
    assert recorded["clip_001"] == digest_1
    assert recorded["clip_002"] == digest_2
    assert idx.payload["segment_method"]


def test_staleness_names_the_source_that_moved(offline_index_2, tmp_path,
                                               monkeypatch):
    """Seven sources, one replaced: the verdict names the one.

    "an ingest file changed" sends a reader to re-measure the project.
    "clip_002 changed" sends them to re-measure one clip.
    """
    idx, digest_1, digest_2 = offline_index_2
    assert idx.staleness()["stale"] in (False, None)

    payload = idx.payload
    payload["source_digests"] = dict(payload["source_digests"])
    payload["source_digests"]["clip_002"] = "sha256:replaced"
    idx._payload = payload
    report = idx.staleness()
    assert report["changed_sources"] == ["clip_002"]
    assert "clip_002" in report["reason"]


def test_staleness_notices_the_code_that_cuts_the_segments_changed(
        offline_index_2, monkeypatch):
    """An index is analysis as much as data.

    The rows in it were cut by a particular version of the cutting code.
    Nothing about the media moving says so, and an index serving rows
    the new code would not cut is silently wrong.
    """
    idx, _, _ = offline_index_2
    payload = idx.payload
    payload["segment_method"] = "sha256:the-old-cut"
    idx._payload = payload
    report = idx.staleness()
    assert report["analysis_version"] == "changed"
    assert report["stale"] is True
    assert "cut" in report["analysis_version_reason"]


def test_a_noun_point_is_not_a_gesture(frame_index):
    """A refusal is a false observation, and a noun is not a gesture.

    The action gate matched a bare "point", so two of the sixteen
    committed editorial queries were refused for a word that meant
    something else: "to sit under the pricing point" (E09) and "GEO just
    SEO two point oh" (E08).  The gesture readings still refuse
    (measured 2026-10-20, see
    docs/benchmarks/2026-10-20-editorial-retrieval-benchmark.md).
    """
    for query in ("a shot of the open laptop on the desk, to sit under "
                  "the pricing point",
                  "the pushback from the marketing directors, the one "
                  "calling GEO just SEO two point oh"):
        assert footage_frames.action_refusal_match(query) is None, query
    for query in ("a person pointing at the door",
                  "he points at the screen",
                  "the sign pointed at the crowd"):
        assert footage_frames.action_refusal_match(query) == "pointing", query


# ─── What an empty answer MEANS (VI-004) ───────────────────────────
#
# The defect: a lane's empty answer was whatever that lane felt like
# saying.  The text lane said `not_found`, the frame lane said
# `abstained`, and the frame lane said `insufficient_evidence` on a
# search that returned five windows - a claim that the evidence is
# missing when the lane had five.  These tests name the three answers a
# reader has to tell apart: not observed, insufficient evidence, and
# confirmed absent.


def test_the_frame_lane_says_it_found_the_candidates_it_ranked(frame_index):
    """The defect: the frame lane reported `insufficient_evidence` on a
    search that returned five ranges, which claims the evidence is
    missing when the lane had five."""
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5)
    assert report["ranges"], "this fixture ranks two clips, so it answers"
    assert report["answer_status"] == "found"
    assert report["abstention"]["outcome"] == "found"
    assert report["abstention"]["answer_state"] == "present"


def test_the_frame_lane_never_confirms_an_absence(frame_index):
    """A RANKING is not an assertion.  CLIP ranking a studio frame for
    "a plate of food" says the studio is the closest thing to a plate
    of food; it does not say there is no plate of food."""
    report = frame_index.search_ranges("a birthday cake with candles",
                                       floor=0.9)
    assert report["abstained"] is True
    assert report["abstention"]["outcome"] == "not_observed"
    assert report["abstention"]["answer_state"] == "not_observed", (
        "a ranking lane can only ever reach not_observed; `absent` would "
        "claim a measurement it never made")


def test_the_frame_lane_names_the_scope_it_measured_and_its_near_miss(
        frame_index):
    """An abstain that cannot say WHICH frames it looked at, and what came
    closest, reads as a broken search."""
    report = frame_index.search_ranges("a birthday cake with candles",
                                       floor=0.9)
    abstention = report["abstention"]
    assert abstention["scope"]["note"], "the scope travels with the answer"
    assert abstention["scope"]["complete"] is True
    assert abstention["near_miss"] is not None
    assert report["near_miss"]["score"] == pytest.approx(
        abstention["near_miss"]["score"])


def test_the_text_lane_says_not_observed_over_the_scope_it_measured(
        offline_index):
    """`not found` is a claim about the footage, so it says which
    footage - and the answer state says it is `not_observed` rather than
    `absent`, because a ranking is not an assertion."""
    idx, _ = offline_index
    # The transcript covers every source the catalog names, so the lane
    # searched the whole scope its question was about.
    payload = dict(idx.payload)
    coverage = dict(payload.get("coverage") or {})
    coverage["memory_transcribed_clips"] = coverage.get("clips_in_catalog")
    payload["coverage"] = coverage
    idx._payload = payload
    unfloored = idx.search_report("parking", top_k=len(idx.segments), floor=0)
    best = max(r["dense_score"] for r in unfloored["results"])
    report = idx.search_report("parking", top_k=5, floor=best + 0.01)
    assert report["results"] == []
    assert report["answer_status"] == "not_found"
    assert report["abstention"]["outcome"] == "not_observed"
    assert report["abstention"]["answer_state"] == "not_observed"
    assert report["abstention"]["scope"]["complete"] is True
    assert report["abstention"]["near_miss"]["dense_score"] == pytest.approx(best)


def test_an_empty_answer_over_a_partial_scope_is_insufficient_evidence(
        offline_index):
    """The defect this names: an index that never transcribed one of the
    sources cannot say "there is no such moment in the footage" - it can
    only say the answer is about the index."""
    idx, _ = offline_index
    unfloored = idx.search_report("parking", top_k=len(idx.segments), floor=0)
    best = max(r["dense_score"] for r in unfloored["results"])
    report = idx.search_report("parking", top_k=5, floor=best + 0.01)
    assert report["results"] == []
    assert report["abstention"]["scope"]["complete"] is False
    assert report["abstention"]["outcome"] == "insufficient_evidence"
    assert report["abstention"]["answer_state"] == "inconclusive"


def test_a_text_lane_with_no_embedder_says_insufficient_evidence(
        project, tmp_path, monkeypatch):
    """A keyword-only answer cannot tell an absent subject from different
    wording, so it must not claim it looked and saw nothing."""
    monkeypatch.setattr(footage_query, "load_embedder", lambda: (None, None))
    index_dir = tmp_path / "noembed"
    build_index(project, index_dir=index_dir)
    index = FootageIndex(project, index_dir=index_dir)
    report = index.search_report("parking", top_k=5, mode="lexical")
    assert report["answer_status"] == "insufficient_evidence"
    assert report["abstention"]["outcome"] == "insufficient_evidence"
    assert report["abstention"]["answer_state"] in (
        "inconclusive", "not_analyzed")


def test_the_sound_lane_confirms_an_absence_it_measured(sound_index):
    """The sound lane measured a source and recorded a label vocabulary,
    so "no label of this kind in any measured source" is an assertion -
    the one case a confirmed absence is earned."""
    report = sound_index.sound_search("dog barking")
    assert report["hits"] == []
    assert report["abstention"]["outcome"] == "confirmed_absent"
    assert report["abstention"]["answer_state"] == "absent"
    assert "dog" not in report["available_labels"]


def test_a_sound_lane_that_never_ran_does_not_confirm_anything(
        offline_index):
    """An index with no sound events has measured nothing, so it cannot
    confirm an absence."""
    idx, _ = offline_index
    report = idx.sound_search("dog barking")
    assert report["abstained"] is True
    assert report["abstention"]["outcome"] == "insufficient_evidence"
    assert report["abstention"]["answer_state"] != "absent"
