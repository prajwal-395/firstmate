"""The footage index prototype: what it cuts, what it finds, and that it is unwired.

`test_footage_index_stays_unwired` is the one that matters most. The
captain's instruction was to prototype a cross-clip footage index and NOT
wire it in, so "nothing imports it" is a property of the repository, not
a promise in a PR description. That test fails the moment a step, the
DAG or a manifest reaches for it.

Every other test builds its project under `tmp_path`. No test reads a
real project (§8).
"""

import json
import re
from pathlib import Path

import numpy as np
import pytest

from library.tools.analysis import footage_query, footage_segments
from library.tools.analysis.footage_query import FootageIndex, build_index
from library.tools.analysis.footage_segments import (
    SEGMENT_KINDS,
    build_segments,
    coverage_report,
    curve_facets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_MODULES = ("footage_query", "footage_segments")


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


def test_a_speech_segment_keeps_its_word_timings(project):
    """Retrieve at the utterance, snap at the word.

    The utterance is what gets embedded; the word timings ride along so a
    caller can trim the hit to the word that matched. Dropping them would
    throw away the pipeline's finest unit to make the index tidy.
    """
    speech = [s for s in build_segments(project) if s.kind == "speech"]
    words = speech[0].words
    assert [w["word"] for w in words] == ["we", "parking", "spot"]
    assert words[1]["start"] == 2.0 and words[1]["end"] == 2.4
    assert speech[0].start <= words[1]["start"] <= speech[0].end


def test_one_object_seen_twice_is_two_segments(project):
    """Two appearances are two answers to "where is the X"."""
    objects = [s for s in build_segments(project) if s.kind == "object"]
    spans = sorted((s.start, s.end) for s in objects)
    assert spans == [(2.0, 5.0), (12.0, 15.0)]
    assert len({s.segment_id for s in objects}) == 2


def test_the_vision_join_goes_through_the_file_path(project):
    """1.03 keys by file stem, the catalog by clip_XXX (§10.1)."""
    segments = build_segments(project)
    scene = next(s for s in segments if s.kind == "scene" and s.clip_id == "clip_001")
    assert "parking lot" in scene.text.lower()
    assert scene.facets["framing"] == "close-up"


def test_unknown_kind_raises_rather_than_matching_nothing(project):
    with pytest.raises(ValueError, match="Unknown segment kind"):
        build_segments(project, kinds=("speech", "vibes"))


# ─── Curves become facets, never segments ─────────────────────────


def test_curves_are_reduced_over_the_span_at_their_own_rate(project):
    doc = footage_segments.load_temporal_index(project)["clip_001"]
    early = curve_facets(doc, 0.0, 10.0)
    late = curve_facets(doc, 10.0, 20.0)
    assert early["speech_ratio"] == 0.0
    assert late["speech_ratio"] == 1.0
    assert early["motion"] == 0.5 and early["brightness"] == 0.4


def test_an_unmeasured_curve_is_absent_not_zero(project):
    """"no face curve" and "no face" are different answers."""
    doc = footage_segments.load_temporal_index(project)["clip_002"]
    facets = curve_facets(doc, 0.0, 10.0)
    assert "face_presence" not in facets
    assert facets["motion"] == 0.9


def test_coverage_reports_what_the_ingest_failed_to_measure(project):
    report = coverage_report(project)
    assert report["clips_in_catalog"] == 2
    assert report["clips_with_vision_profile"] == 2
    assert report["utterances"] == 2 and report["words"] == 4
    assert report["clips_with_measured_prosody"] == 0, (
        "a prosody file that recorded an error is not a measurement (§10.3)"
    )


# ─── Building and querying ────────────────────────────────────────


def test_build_writes_only_into_the_index_dir(project, tmp_path, monkeypatch):
    monkeypatch.setattr(footage_query, "_load_embedder", lambda: (None, "none"))
    before = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    index_dir = tmp_path / "elsewhere"
    build_index(project, index_dir=index_dir)
    after = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file())
    assert before == after, "building the index must not touch the project"
    assert (index_dir / footage_query.SEGMENTS_FILE).exists()


def test_search_finds_the_utterance_and_the_word(offline_index):
    idx, _ = offline_index
    hits = idx.search("parking", top_k=3, mode="lexical")
    top = hits[0]
    assert top["kind"] == "speech" and top["clip_id"] == "clip_001"
    assert top["start"] == 1.0 and top["end"] == 3.0
    assert top["word_hits"] == [{"word": "parking", "start": 2.0, "end": 2.4}]


def test_a_hit_carries_a_timecode_a_person_can_read(offline_index):
    idx, _ = offline_index
    top = idx.search("pollen", top_k=1, mode="lexical")[0]
    assert re.fullmatch(r"\d{2}:\d{2}\.\d{3}-\d{2}:\d{2}\.\d{3}", top["timecode"])
    assert top["timecode"] == "00:06.000-00:07.500"


def test_lexical_search_works_with_no_embedder_at_all(project, tmp_path, monkeypatch):
    """The index must degrade to keyword search, not to nothing.

    `sentence-transformers` is not in requirements.txt and was not
    installed in the environment this was built in, so "the embedder is
    missing" is the normal case, not the exotic one.
    """
    monkeypatch.setattr(footage_query, "_load_embedder", lambda: (None, "none"))
    index_dir = tmp_path / "nodense"
    build_index(project, index_dir=index_dir)
    idx = FootageIndex(project, index_dir=index_dir)
    assert idx.matrix is None
    assert idx.search("pollen", top_k=1, mode="hybrid")[0]["kind"] == "speech"
    assert "error" in idx.search("pollen", mode="dense")[0]


def test_search_mode_is_checked(offline_index):
    idx, _ = offline_index
    with pytest.raises(ValueError, match="Unknown search mode"):
        idx.search("anything", mode="magic")


def test_filter_selects_on_facets_including_reduced_curves(offline_index):
    idx, _ = offline_index
    wide = idx.filter(kind="scene", framing="wide")
    assert [h["clip_id"] for h in wide] == ["clip_002"]

    speaking = idx.filter(has_speech=True)
    assert len(speaking) == 2 and all(h["kind"] == "speech" for h in speaking)

    assert idx.filter(kind="scene", min_face_presence=0.5) != []
    assert idx.filter(kind="object", clip_id="clip_002") == []

    with pytest.raises(ValueError, match="Unknown kind"):
        idx.filter(kind="vibes")


def test_search_and_filter_only_returns_survivors(offline_index):
    idx, _ = offline_index
    hits = idx.search_and_filter("parking", top_k=5, mode="lexical", kind="scene")
    assert hits and all(h["kind"] == "scene" for h in hits)
    assert idx.search_and_filter("parking", kind="speech", clip_id="clip_002") == []


def test_detail_and_transcript_are_the_bulk_reads(offline_index):
    idx, _ = offline_index
    detail = idx.get_detail("clip_001#speech#000")
    assert detail["words"][0]["word"] == "we"
    assert "error" in idx.get_detail("clip_999#speech#000")

    lines = idx.transcript("clip_001")
    assert [line["text"] for line in lines] == [
        "we need to find a parking spot", "the pollen is terrible today",
    ]
    assert idx.transcript("clip_002") == []


def test_summary_reports_the_corpus_and_what_is_missing(offline_index):
    idx, stats = offline_index
    summary = idx.summary()
    assert summary["clips"] == 2
    assert summary["segment_count"] == stats["segment_count"]
    assert summary["kinds"]["speech"] == 2
    assert summary["coverage"]["clips_with_measured_prosody"] == 0


def test_missing_index_says_how_to_build_it(project, tmp_path):
    idx = FootageIndex(project, index_dir=tmp_path / "nope")
    with pytest.raises(FileNotFoundError, match="build"):
        _ = idx.segments


# ─── The LLM-facing half ──────────────────────────────────────────


def test_every_tool_definition_names_a_real_method():
    """A tool an LLM is offered must be one this module can actually run."""
    methods = {
        "search_footage": "search",
        "filter_footage": "filter",
        "search_and_filter_footage": "search_and_filter",
        "get_footage_detail": "get_detail",
        "get_clip_transcript": "transcript",
        "footage_summary": "summary",
    }
    defined = {t["function"]["name"] for t in FootageIndex.get_tool_definitions()}
    assert defined == set(methods)
    for tool, method in methods.items():
        assert callable(getattr(FootageIndex, method)), f"{tool} -> {method}"


def test_tool_definitions_are_valid_json_and_describe_every_parameter():
    for tool in FootageIndex.get_tool_definitions():
        json.dumps(tool)
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        for name, spec in params.get("properties", {}).items():
            assert spec.get("description"), f"{tool['function']['name']}.{name}"
        if "kind" in params.get("properties", {}):
            assert params["properties"]["kind"]["enum"] == list(SEGMENT_KINDS)


def test_the_bridge_answers_on_stdout(project, tmp_path, monkeypatch, capsys):
    """The `sfx_query_bridge.py` precedent: JSON in, JSON out."""
    from library.tools import footage_query_bridge

    monkeypatch.setattr(footage_query, "_load_embedder", lambda: (None, "none"))
    index_dir = tmp_path / "bridge"
    build_index(project, index_dir=index_dir)

    request = json.dumps({
        "project_folder": str(project), "index_dir": str(index_dir),
        "query": "pollen", "top_k": 2, "mode": "lexical",
    })
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(request))
    footage_query_bridge.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["clip_id"] == "clip_001"
    assert payload["results"][0]["kind"] == "speech"


def test_the_bridge_reports_a_failure_as_an_error(monkeypatch, capsys):
    from library.tools import footage_query_bridge

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{not json"))
    footage_query_bridge.main()
    assert "error" in json.loads(capsys.readouterr().out)


# ─── The constraint the captain set ───────────────────────────────


def test_footage_index_stays_unwired():
    """Nothing in the pipeline may reach for the prototype.

    "not actually wiring it in until we are ready for it" is the captain's
    call to reverse. Until then this is enforced here rather than trusted:
    no step, no DAG, no process manifest may name either module.
    """
    searched = [
        REPO_ROOT / "library" / "steps",
        REPO_ROOT / "library" / "processes",
        REPO_ROOT / "library" / "dashboard",
        REPO_ROOT / "manage_project.py",
    ]
    offenders = []
    for root in searched:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix not in (".py", ".json", ".md"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for module in PROTOTYPE_MODULES:
                if module in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} names {module}")
    assert not offenders, (
        "The footage index is a prototype and must stay out of the pipeline:\n  "
        + "\n  ".join(offenders)
    )


def test_the_prototype_does_not_import_the_pipeline_either():
    """It reads a project's files; it does not join the run.

    Importing a step, the runner or the state writer would make it part of
    the pipeline by the back door.
    """
    forbidden = re.compile(r"from library\.(steps|processes)|import library\.(steps|processes)"
                           r"|save_pipeline_state|run_pipeline")
    for name in PROTOTYPE_MODULES:
        source = (REPO_ROOT / "library" / "tools" / "analysis" / f"{name}.py").read_text(
            encoding="utf-8")
        assert not forbidden.search(source), f"{name}.py reaches into the pipeline"
