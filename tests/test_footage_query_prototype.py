"""The footage index prototype: what it cuts, what it finds, and that it is unwired.

`test_footage_index_stays_unwired` is the one that matters most. The
captain's instruction was to prototype a cross-clip footage index and NOT
wire it in, so "nothing imports it" is a property of the repository, not
a promise in a PR description. That test fails the moment a step, the
DAG or a manifest reaches for it. It was NARROWED on 2026-08-26 when the
captain authorised the dashboard - and only the dashboard - to call it;
its own docstring carries the reasoning, and
`test_the_guard_still_fires_when_a_step_imports_the_index` shows it
still fires.

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


def test_an_unmeasured_curve_is_absent_not_zero(project):
    """"no face curve" and "no face" are different answers."""
    doc = footage_segments.load_temporal_index(project)["clip_002"]
    facets = curve_facets(doc, 0.0, 10.0)
    assert "face_presence" not in facets
    assert facets["motion"] == 0.9


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


# ─── The LLM-facing half ──────────────────────────────────────────


# ─── The constraint the captain set ───────────────────────────────


def _name_the_prototype(roots) -> list:
    """Every file under `roots` that names either prototype module.

    Factored out so the guard can be pointed at a fake tree and shown to
    still fire - a guard nobody has watched fail is a guard nobody knows
    still works.
    """
    offenders = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix not in (".py", ".json", ".md"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for module in PROTOTYPE_MODULES:
                if module in text:
                    offenders.append(f"{path} names {module}")
    return offenders


# The roots the captain's constraint still covers. `library/dashboard/`
# used to be here and is NOT any more - see the docstring below.
PIPELINE_ROOTS = (
    REPO_ROOT / "library" / "steps",
    REPO_ROOT / "library" / "processes",
    REPO_ROOT / "manage_project.py",
)


def test_footage_index_stays_unwired():
    """No STEP may reach for the prototype. The dashboard now may.

    The captain's ruling was "not actually wiring it in until we are ready
    for it", and their reason for wanting the index was two-sided: "to
    speed up both a person's workflow and also help the LLM actually find
    what it is looking for."

    On 2026-08-26 they authorised the FIRST half and only the first half -
    "can you wire this index search into the UI we have for this project" -
    so `library/dashboard/` came out of this scan. A reviewer searching
    their own rushes in a browser is a person using a tool; a step calling
    the same module is the pipeline making an editorial decision out of a
    prototype whose retrieval quality has not been signed off. Those are
    different acts and only the first one is allowed.

    So this test was NARROWED, not weakened. `library/steps/`,
    `library/processes/` and `manage_project.py` are still scanned, which
    is every route by which the index could reach a run: a step module, a
    step's bridge, a `dag.json`, a `manifest.json`, or the CLI that drives
    them. `manage_project.py` reaches the dashboard through
    `library.dashboard.server.start_server` and never needs to name either
    prototype module, so it stays in the scan.

    Widening it back is the captain's call, in the same direction the
    narrowing went: state which half is being authorised.
    """
    offenders = _name_the_prototype(PIPELINE_ROOTS)
    assert not offenders, (
        "The footage index is a prototype and must stay out of the pipeline:\n  "
        + "\n  ".join(o.replace(str(REPO_ROOT) + "/", "") for o in offenders)
    )


def test_the_guard_still_fires_when_a_step_imports_the_index(tmp_path):
    """The narrowed guard is still a guard.

    A test that is quietly relaxed the first time it fires is worse than
    no test, so this drives the same scan over a fake `library/steps/`
    holding exactly what a wiring-in would look like, and asserts it
    reports the offender.
    """
    steps = tmp_path / "library" / "steps" / "step_3_02_select_broll"
    steps.mkdir(parents=True)
    (steps / "step.py").write_text(
        "from library.tools.analysis.footage_query import FootageIndex\n",
        encoding="utf-8")
    (steps / "manifest.json").write_text(
        json.dumps({"interface": {"tools": ["footage_segments"]}}), encoding="utf-8")

    offenders = _name_the_prototype([tmp_path / "library" / "steps"])
    assert len(offenders) == 2, offenders
    assert any("step.py" in o and "footage_query" in o for o in offenders)
    assert any("manifest.json" in o and "footage_segments" in o for o in offenders)

    # ...and stays quiet over a tree that does not name it.
    clean = tmp_path / "clean"
    (clean / "library" / "steps").mkdir(parents=True)
    (clean / "library" / "steps" / "step.py").write_text("import json\n", encoding="utf-8")
    assert _name_the_prototype([clean]) == []


def test_the_dashboard_is_the_one_caller_that_was_carved_out():
    """The carve-out is real and it is exactly one module.

    `library/dashboard/footage_search.py` is where the dashboard reaches
    the prototype. If a future change reaches it from somewhere else in
    the dashboard, that is fine - but the scan above no longer notices, so
    this records where the authorised caller lives.
    """
    caller = REPO_ROOT / "library" / "dashboard" / "footage_search.py"
    assert caller.is_file(), "the dashboard's half of the footage index went missing"
    source = caller.read_text(encoding="utf-8")
    assert "footage_query" in source
    assert "footage_query" not in (REPO_ROOT / "manage_project.py").read_text(encoding="utf-8")


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
