"""MU-08: project M3d region tracks into the Project Model.

Acceptance, on a two-person fixture: ``resolve_person`` returns each
person's regions and none of the other's, and a mask with two faces
inside stays unassigned with its reason. The migration also fills real
confidences where a score exists (ArcFace cosine for face clustering,
diarization silhouette where the voice track carries one) and the
``region_rle`` masks rasterize through the matte writer pixel-identical
to the decoded sidecar.

Every project here is built under ``tmp_path``: hand-written M3b lane
documents plus M3d ``regions.json`` records whose sidecars are written
by the real ``person_regions.write_sidecar`` (pure: no model, no
decode). Nothing reads a real project, a real memory root, or a model.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import person_regions as pr
from library.tools import project_model_effectcraft as ec
from library.tools import project_model_evidence as evidence
from library.tools.project_model import validate_project_model
from library.tools.project_model_masks import (
    REGION_RLE_KIND,
    REGION_RUNS_CODEC,
    decode_region_runs,
)

D1 = "digest-region-001"
D2 = "digest-region-002"

EMB_A = [1.0, 0.0, 0.0, 0.0]
EMB_B = [0.0, 1.0, 0.0, 0.0]
EMB_C = [0.0, 0.0, 1.0, 0.0]

GRID = [8, 6]


@pytest.fixture(autouse=True)
def _hermetic_memory_root(monkeypatch):
    """No host memory root leaks in: every read below names its root."""
    monkeypatch.delenv("PIPELINE_SOURCE_MEMORY_ROOT", raising=False)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mask(*boxes):
    """A bool mask on GRID, on inside the given cell boxes."""
    width, height = GRID
    mask = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        mask[y0:y1, x0:x1] = True
    return mask


def _instance(mask: np.ndarray, track: str, **link) -> tuple[dict, list[int]]:
    """An M3d sample instance plus its sidecar runs, summarised by the
    real geometry function so the interior point is inside the mask."""
    runs = pr.encode_runs(mask)
    return ({"track": track, **pr.summarize(mask),
             "mask": None, **link}, runs)


def _regions_doc(digest: str, tracks: list[dict], samples: list[dict],
                 runs: list[list[int]], memory: Path) -> dict:
    sdir = memory / digest
    ref = pr.write_sidecar(sdir, runs, GRID)
    for sample in samples:
        for inst in sample["instances"]:
            if inst["mask"] is None:
                raise AssertionError("fixture instance claims no mask index")
    return {
        "content_digest": digest,
        "status": "measured",
        "coordinates": "image-normalised, top-left origin",
        "grid": list(GRID),
        "sidecar": ref,
        "tracks": tracks,
        "samples": samples,
    }


@pytest.fixture()
def fixture_project(tmp_path):
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    # D1: one person (face_001/EMB_A), one owned region track.
    left = _mask((0, 0, 4, 6))
    inst0, runs0 = _instance(left, "region_001", face=0,
                             face_track="face_001", containment=0.9)
    inst1, runs1 = _instance(left, "region_001", face=0,
                             face_track="face_001", containment=0.85)
    inst0["mask"], inst1["mask"] = 0, 1
    d1_tracks = [{"track_id": "region_001", "start": 0.0, "end": 0.5,
                  "samples": 2, "face_track": "face_001",
                  "face_track_fraction": 1.0}]
    d1_samples = [{"t": 0.0, "instances": [inst0]},
                  {"t": 0.5, "instances": [inst1]}]
    _write(memory / D1 / "regions.json",
           _regions_doc(D1, d1_tracks, d1_samples, [runs0, runs1], memory))
    # D2: a second person (face_001/EMB_B) with an owned track, plus a
    # whole-frame mask holding two faces that must stay unassigned.
    right = _mask((4, 0, 8, 6))
    whole = _mask((0, 0, 8, 6))
    j0, jruns0 = _instance(right, "region_001", face=0,
                           face_track="face_001", containment=0.95)
    j1, jruns1 = _instance(whole, "region_002",
                           unassigned="several_faces_inside", faces=[0, 1])
    j0["mask"], j1["mask"] = 0, 1
    d2_tracks = [
        {"track_id": "region_001", "start": 0.0, "end": 0.0,
         "samples": 1, "face_track": "face_001",
         "face_track_fraction": 1.0},
        {"track_id": "region_002", "start": 0.25, "end": 0.25,
         "samples": 1, "face_track": None,
         "unassigned": "several_faces_inside"},
    ]
    d2_samples = [{"t": 0.0, "instances": [j0]},
                  {"t": 0.25, "instances": [j1]}]
    _write(memory / D2 / "regions.json",
           _regions_doc(D2, d2_tracks, d2_samples, [jruns0, jruns1], memory))
    for digest, faces, voices in (
        (D1, [{"track_id": "face_001", "embedding": EMB_A,
               "spans": [{"start": 0.0, "end": 5.0,
                          "box": [0, 0, 10, 10], "det_score": 0.9}]}],
         [{"track_id": "voice_001", "embedding": EMB_A,
           "spans": [[1.0, 2.0]], "silhouette": 0.62}]),
        (D2, [{"track_id": "face_001", "embedding": EMB_B,
               "spans": [{"start": 0.0, "end": 5.0,
                          "box": [1, 1, 11, 11], "det_score": 0.8}]},
              {"track_id": "face_002", "embedding": EMB_C,
               "spans": [{"start": 0.0, "end": 5.0,
                          "box": [2, 2, 12, 12], "det_score": 0.7}]}],
         [{"track_id": "voice_001", "embedding": EMB_B,
           "spans": [[3.0, 4.0]]}]),
    ):
        _write(memory / digest / "source.json", {
            "content_digest": digest, "size_bytes": 100,
            "duration_seconds": 10.0, "video_streams": [{"index": 0}],
        })
        _write(memory / digest / "identity.json", {
            "content_digest": digest, "status": "measured",
            "face_match_threshold": 0.25,
            "faces": faces, "voices": voices,
            "speech_face_links": [{"face_track": "face_001",
                                   "voice_track": "voice_001"}],
            "instrument": {},
        })
    catalog = [
        {"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
         "path": "/nonexistent/a.mov"},
        {"clip_id": "clip_002", "source_file": "/nonexistent/b.mov",
         "path": "/nonexistent/b.mov"},
    ]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov",
                         "content_digest": D1},
            "clip_002": {"path": "/nonexistent/b.mov",
                         "content_digest": D2}},
    })
    return {"project": project, "memory": memory,
            "runs": {D1: [runs0, runs1], D2: [jruns0, jruns1]}}


def _migrate(fixture_project):
    model, report = evidence.migrate_project_evidence(
        str(fixture_project["project"]),
        memory_root=str(fixture_project["memory"]))
    validate_project_model(model)
    return model, report


def _persons(model):
    return {e["id"]: e for e in model["entities"] if e["kind"] == "person"}


def test_each_person_resolves_only_their_own_regions(fixture_project):
    """MU-08 acceptance (first half): on a two-person fixture,
    ``resolve_person`` returns each person's regions and none of the
    other's."""
    model, _report = _migrate(fixture_project)
    persons = _persons(model)
    assert len(persons) == 3

    first = evidence.resolve_person(model, "person_001")
    second = evidence.resolve_person(model, "person_002")
    third = evidence.resolve_person(model, "person_003")
    assert first is not None and second is not None and third is not None

    assert [t["id"] for t in first["regions"]] == [f"track-region-{D1}-region_001"]
    assert [t["id"] for t in second["regions"]] == [f"track-region-{D2}-region_001"]
    assert third["regions"] == []
    # No region leaks across: every returned track belongs to the person.
    for resolved in (first, second):
        for track in resolved["regions"]:
            assert track["entity_id"] == resolved["entity"]["id"]
            assert all(s["geometry"]["kind"] == "region"
                       for s in track["samples"])
    # The face_in_region links name the same ownership.
    methods = {(link["entity_id"], link["method"])
               for link in model["identity_links"]}
    assert (first["entity"]["id"], "face_in_region") in methods
    assert (second["entity"]["id"], "face_in_region") in methods


def test_a_two_face_mask_stays_unassigned_with_its_reason(fixture_project):
    """MU-08 acceptance (second half): the whole-frame mask holding two
    faces sits on an explicit unassigned entity with its reason and no
    identity link."""
    model, report = _migrate(fixture_project)

    mask = next(m for m in model["masks"]
                if m["id"] == f"mask-region-{D2}-region_002")
    assert mask["entity_id"] == f"unassigned-{D2}-region_002"
    assert mask["representation"]["kind"] == REGION_RLE_KIND
    assert mask["representation"]["codec"] == REGION_RUNS_CODEC
    assert [link for link in model["identity_links"]
            if link["entity_id"] == mask["entity_id"]] == []
    obs = next(o for o in model["observations"]
               if o["modality"] == "region_track"
               and o["payload"].get("track_id") == "region_002")
    assert obs["payload"]["unassigned"] == "several_faces_inside"
    assert obs["confidence"] is None
    assert any("several_faces_inside" in note for note in report["notes"])
    # And neither person resolves it.
    for person_id in _persons(model):
        resolved = evidence.resolve_person(model, person_id)
        assert mask["id"] not in [m["id"] for m in resolved["masks"]]
        assert f"track-region-{D2}-region_002" not in [
            t["id"] for t in resolved["regions"]]


def test_links_carry_measured_confidences_where_scores_exist(fixture_project):
    """ArcFace cosine for face clustering; the diarization silhouette
    where the voice track carries one, None where it carries none; the
    cooccurrence link (no score) stays None."""
    model, _report = _migrate(fixture_project)

    cluster = [link for link in model["identity_links"]
               if link["method"] == "face_embedding_cluster"]
    assert cluster, "face links must migrate"
    for link in cluster:
        assert link["confidence"] is not None
        assert 0.99 < link["confidence"] <= 1.0

    def _voice_confidence(digest, track):
        obs_ids = {o["id"] for o in model["observations"]
                   if o["modality"] == "voice_turn"
                   and o["provenance_id"] == f"prov-{digest}-m3b"}
        links = [link for link in model["identity_links"]
                 if link["method"] == "diarization"
                 and link["observation_id"] in obs_ids
                 and any(o["id"] == link["observation_id"]
                         and o["payload"].get("voice_track") == track
                         for o in model["observations"])]
        return links

    d1_links = _voice_confidence(D1, "voice_001")
    assert d1_links and all(link["confidence"] == 0.62
                            for link in d1_links)
    d2_links = _voice_confidence(D2, "voice_001")
    assert d2_links and all(link["confidence"] is None
                            for link in d2_links)
    cooccurrence = [link for link in model["identity_links"]
                    if link["method"] == "speech_face_cooccurrence"]
    assert cooccurrence and all(link["confidence"] is None
                                for link in cooccurrence)

    in_region = [link for link in model["identity_links"]
                 if link["method"] == "face_in_region"]
    assert in_region and all(link["confidence"] == 1.0
                             for link in in_region)


def test_region_masks_rasterize_to_the_decoded_sidecar(fixture_project):
    """The migrated ``region_rle`` mask reaches matte frames
    pixel-identical to the sidecar runs: runs -> sidecar -> migrate ->
    resolve -> decode -> PNG alpha, compared against the numpy
    originals, never against another RLE decode."""
    model, _report = _migrate(fixture_project)
    mask = next(m for m in model["masks"]
                if m["id"] == f"mask-region-{D1}-region_001")
    comp = ec.CompSpec(name="Main", width=GRID[0], height=GRID[1],
                       frame_rate=10.0, duration_seconds=2.0)
    sequence = ec.write_matte_sequence(
        mask, model, comp, fixture_project["project"] / "mattes",
        memory_root=str(fixture_project["memory"]))
    # Samples at t=0.0 and t=0.5 (ticks 0 and 500): frames 0..4 sample
    # ticks 0..400, all holding the first sample.
    assert (sequence.first_frame, sequence.count) == (0, 5)
    expected = decode_region_runs(fixture_project["runs"][D1][0],
                                  GRID[0], GRID[1])
    from tests.unit.context.test_project_model_masks import (
        _read_matte_alpha,
    )
    for path in sequence.files:
        assert _read_matte_alpha(path) == expected
    # The inline summary answers geometry without decoding: the
    # interior point of the migrated track sits inside the decoded mask.
    track = next(t for t in model["tracks"]
                 if t["id"] == f"track-region-{D1}-region_001")
    alpha = decode_region_runs(fixture_project["runs"][D1][0],
                               GRID[0], GRID[1])
    for sample in track["samples"]:
        ix, iy = sample["geometry"]["interior_point"]
        assert alpha[int(iy * GRID[1]) * GRID[0]
                     + int(ix * GRID[0])] == 255


def test_an_absent_m3d_migrates_nothing_but_a_note(tmp_path):
    """A digest with no ``regions.json`` gains no tracks or masks, and
    says so in the report rather than failing the migration."""
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    _write(memory / D1 / "source.json", {
        "content_digest": D1, "size_bytes": 100,
        "duration_seconds": 10.0, "video_streams": [{"index": 0}],
    })
    _write(memory / D1 / "identity.json", {
        "content_digest": D1, "status": "measured",
        "face_match_threshold": 0.25,
        "faces": [{"track_id": "face_001", "embedding": EMB_A,
                   "spans": [{"start": 0.0, "end": 5.0,
                              "box": [0, 0, 10, 10]}]}],
        "voices": [], "speech_face_links": [], "instrument": {},
    })
    _write(project / "pipeline_data.json", {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001",
                 "source_file": "/nonexistent/a.mov",
                 "path": "/nonexistent/a.mov"}]}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov",
                         "content_digest": D1}},
    })
    model, report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))
    validate_project_model(model)
    assert [t for t in model["tracks"]
            if any(s["geometry"].get("kind") == "region"
                   for s in t["samples"])] == []
    assert [m for m in model["masks"]
            if m["representation"].get("kind") == REGION_RLE_KIND] == []
    assert any("M3d absent" in note for note in report["notes"])
