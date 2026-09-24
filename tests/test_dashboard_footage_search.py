"""Footage search in the dashboard: the person half of the index, wired.

The captain asked to search their own footage in a browser. What that
turns into is three things a search module does not have to care about
and a long-lived server does: an index held warm, a build that is a
decision rather than a side effect, and a hit shaped so it can be carried
into Resolve.

The pipeline half stays forbidden - `tests/test_footage_query_prototype.py`
is where that is enforced, and it still fails if a step imports the index.

Every test builds its project under `tmp_path` (§8).
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from library.dashboard import footage_search, server
from library.tools.analysis import footage_query

# ─── A project, and an embedder that is not a 22M-parameter model ──


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path) -> Path:
    """Two clips of ingest: one that speaks at 30 fps, one silent at 60."""
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"

    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV",
             "source_file": str(root / "raw" / "IMG_0001.MOV"),
             "path": str(root / "raw" / "IMG_0001.MOV"),
             "duration_seconds": 20.0, "frame_rate": 30.0},
            # No frame rate at all: the catalog does not always know one.
            {"clip_id": "clip_002", "filename": "IMG_0002.MOV",
             "source_file": str(root / "raw" / "IMG_0002.MOV"),
             "path": str(root / "raw" / "IMG_0002.MOV"),
             "duration_seconds": 10.0},
        ]}},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
        ],
        "face_presence": {"sample_rate_hz": 1, "values": [1.0] * 20},
        "motion_energy": {"sample_rate_hz": 1, "values": [0.2] * 20},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0, "speech_regions": [],
        "motion_energy": {"sample_rate_hz": 1, "values": [0.9] * 10},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0001_v3.json", {
        "clip_id": "IMG_0001", "file_path": str(root / "raw" / "IMG_0001.MOV"),
        "duration_s": 20.0,
        "scene": [{"start": 0.0, "end": 20.0, "location": "Outdoor parking lot",
                   "type": "outdoor", "lighting": "Daylight", "notable_features": []}],
        "camera": [{"start": 0, "end": 20, "mode": "selfie", "framing": "close-up",
                    "stability": "shaky", "movement": "walking"}],
        "actions": [], "objects": [{"label": "red bicycle", "category": "object",
                                    "role": "background", "readable_text": None,
                                    "appearances": [[2.0, 5.0]]}],
        "assessment": {"content_type": "person_talking_to_camera"},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0002_v3.json", {
        "clip_id": "IMG_0002", "file_path": str(root / "raw" / "IMG_0002.MOV"),
        "duration_s": 10.0,
        "scene": [{"start": 0.0, "end": 10.0, "location": "Empty street",
                   "type": "outdoor", "lighting": "Overcast", "notable_features": []}],
        "camera": [{"start": 0, "end": 10, "mode": "mounted", "framing": "wide",
                    "stability": "steady", "movement": "static"}],
        "actions": [], "objects": [],
        "assessment": {"content_type": "scenery"},
    })
    return root


@pytest.fixture(autouse=True)
def clean_cache():
    """The warm index is process state; no test may inherit another's."""
    footage_search.forget()
    yield
    footage_search.forget()


@pytest.fixture
def stub_embedder(monkeypatch):
    def loader():
        def encode(texts):
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                rows.append(vector / (np.linalg.norm(vector) or 1.0))
            return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
        return encode, "test-stub"
    monkeypatch.setattr(footage_query, "_load_embedder", loader)


# ─── Where the index lives, and when it appears ───────────────────


def test_a_project_with_no_index_says_so_and_names_where_one_would_go(project):
    """The path is stated BEFORE the button, not after the write.

    Building writes about a megabyte into the captain's project. That is
    a decision they make, so the page has to be able to tell them where.
    """
    status = footage_search.status(project, warm=False)
    assert status["exists"] is False
    assert status["index_dir"].endswith("pipeline_output/scratch/footage_index")
    assert "scratch" in status["hint"] and "safe to delete" in status["hint"]
    assert not status["hint"].startswith("No footage index"), (
        "the hint sits under a heading that already says that"
    )
    assert not (project / "pipeline_output" / "scratch").exists(), (
        "asking about the index must not create it"
    )


def test_building_writes_only_into_the_scratch_area(project, stub_embedder):
    before = {p.relative_to(project) for p in project.rglob("*") if p.is_file()}
    stats = footage_search.build(project)
    after = {p.relative_to(project) for p in project.rglob("*") if p.is_file()}

    new = after - before
    assert new, "the build wrote nothing"
    scratch = Path("pipeline_output") / "scratch" / "footage_index"
    assert all(str(p).startswith(str(scratch)) for p in new), sorted(map(str, new))
    assert before <= after, "the build must not remove or rewrite ingest output"
    assert stats["segment_count"] > 0
    assert stats["index_dir"] == footage_search.status(project, warm=False)["index_dir"]


def test_status_reports_the_corpus_the_facets_and_the_staleness(project, stub_embedder):
    footage_search.build(project)
    status = footage_search.status(project, warm=False)

    assert status["exists"] is True
    assert status["summary"]["clips"] == 2
    assert status["staleness"]["stale"] is False
    assert status["floor"]["default"] == footage_query.DENSE_SCORE_FLOOR

    # Only values this footage really has: the dropdowns are built from the
    # index, so a reviewer cannot pick a framing nothing was shot at.
    assert set(status["facets"]["framing"]) == {"close-up", "wide"}
    assert set(status["facets"]["stability"]) == {"shaky", "steady"}
    assert "medium" not in status["facets"].get("framing", [])
    assert set(status["facets"]["clip_id"]) == {"clip_001", "clip_002"}

    # And what the footage contains, which is what an empty state needs.
    assert "red bicycle" in status["corpus"]["objects"]
    assert "Outdoor parking lot" in status["corpus"]["places"]


def test_a_changed_ingest_makes_the_dashboard_report_a_stale_index(project, stub_embedder):
    footage_search.build(project)
    doc = project / "pipeline_output" / "steps" / "1_04_temporal_index" / "index" / "clip_002.json"
    payload = json.loads(doc.read_text())
    payload["speech_regions"] = [{"start": 1.0, "end": 2.0, "text": "newly transcribed"}]
    doc.write_text(json.dumps(payload))

    footage_search.forget(project)
    assert footage_search.status(project, warm=False)["staleness"]["stale"] is True


def test_a_rebuild_is_not_answered_out_of_the_old_cache(project, stub_embedder):
    """A `FootageIndex` caches its payload; a rebuild behind one lies."""
    footage_search.build(project)
    assert footage_search.search(project, "parking")["segment_count"] == \
        len(footage_search.get_index(project).segments)

    before = footage_search.search(project, "parking")["segment_count"]
    doc = project / "pipeline_output" / "steps" / "1_04_temporal_index" / "index" / "clip_002.json"
    payload = json.loads(doc.read_text())
    payload["speech_regions"] = [{"start": 1.0, "end": 2.0, "text": "a second utterance"}]
    doc.write_text(json.dumps(payload))

    footage_search.build(project)
    assert footage_search.search(project, "parking")["segment_count"] == before + 1


# ─── A hit a person can carry into Resolve ────────────────────────


def test_source_timecode_is_non_drop_at_the_clips_own_rate():
    assert footage_search.source_timecode(0.0, 30) == "00:00:00:00"
    assert footage_search.source_timecode(6.0, 30) == "00:00:06:00"
    assert footage_search.source_timecode(2.5, 30) == "00:00:02:15"
    assert footage_search.source_timecode(3661.0, 30) == "01:01:01:00"
    # A fractional rate keeps its own arithmetic - the frame NUMBER comes
    # from the real rate - and counts against a 30-frame second, which is
    # what non-drop means. One second in is one second of timecode.
    assert footage_search.source_timecode(1.0, 29.97) == "00:00:01:00"
    assert footage_search.source_timecode(0.9, 29.97) == "00:00:00:27"


def test_a_hit_carries_the_clip_the_timecode_and_the_frames(project, stub_embedder):
    footage_search.build(project)
    report = footage_search.search(project, "parking", top_k=10, floor=0)
    speech = next(h for h in report["results"] if h["kind"] == "speech")

    assert speech["clip_id"] == "clip_001"
    assert speech["filename"] == "IMG_0001.MOV"
    assert speech["timecode"] == "00:01.000-00:03.000"
    assert speech["source_tc_in"] == "00:00:01:00"
    assert speech["source_tc_out"] == "00:00:03:00"
    assert speech["fps"] == 30.0
    # Retrieve at the utterance, snap at the word.
    assert speech["word_hits"] == [{"word": "parking", "start": 2.0, "end": 2.4}]




# ─── Searching, filtering, and abstaining ─────────────────────────










def test_the_model_is_loaded_once_and_the_state_is_reportable(project, stub_embedder):
    """Cold start is ~2 s and a warm query ~2 ms, so it is paid once.

    What the page needs is not the speed but the STATE: a first load that
    reports nothing is indistinguishable from a hang.
    """
    footage_search.build(project)          # build kicks the warm thread
    for _ in range(200):
        state = footage_search.warm_state(project)["state"]
        if state in ("ready", "unavailable"):
            break
        time.sleep(0.02)
    assert footage_search.warm_state(project)["state"] == "ready"
    assert footage_search.warm_state(project)["backend"] == "test-stub"

    index = footage_search.get_index(project)
    assert footage_search.get_index(project) is index, "one index per project, held"




# ─── Through the HTTP surface the browser really uses ─────────────




def test_switching_project_drops_the_warm_index(project, stub_embedder, monkeypatch):
    """Or the new project is searched against the old one's footage."""
    monkeypatch.setattr(server, "_project_dir", str(project))
    client = TestClient(server.app)
    client.post("/api/footage/search/build")
    assert footage_search._indexes

    client.post("/api/projects/select", json={"project_dir": str(project)})
    assert not footage_search._indexes
