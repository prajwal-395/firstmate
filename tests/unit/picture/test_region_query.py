"""MU-09: `region_at` / `regions_overlapping` and region-carrying reads.

Every project here is built under `tmp_path`: a hand-made Project Model
dict plus hand-made M3d lane documents in a scratch memory root. Nothing
reads a real project, a real memory root, or a model (§8). The memory
root is passed explicitly and `MEMORY_ROOT_ENV` is pinned to it, so a
host or agent environment cannot leak in.
"""
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import person_regions, project_model_evidence as evidence
from library.tools import region_query
from library.tools.analysis import footage_query
from library.tools.ren_refusal import RenRefusal

D = "digest-region-001"
GRID = [10, 10]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _l_mask() -> np.ndarray:
    """An L: left column plus bottom row. The bbox centre (the notch)
    is background, so only a true interior point is inside."""
    mask = np.zeros((10, 10), dtype=bool)
    mask[:, 0:2] = True
    mask[8:10, :] = True
    return mask


def _rect_mask() -> np.ndarray:
    mask = np.zeros((10, 10), dtype=bool)
    mask[1:4, 5:9] = True
    return mask


def _geometry(mask: np.ndarray) -> dict:
    summary = person_regions.summarize(mask)
    return {"kind": "region", **summary}


def _model() -> dict:
    """One person, one region track of two samples (t=1.0, t=2.0)."""
    samples = []
    for tick, mask in ((1000, _l_mask()), (2000, _rect_mask())):
        samples.append({"tick": tick, "geometry": _geometry(mask),
                        "confidence": 0.9,
                        "coordinate_space": "source-normalised-tl"})
    return {
        "id": "project-fixture", "schema_version": 1,
        "clocks": [{"id": f"clock-{D}",
                    "domain": {"kind": "source", "asset_id": f"asset-{D}"},
                    "ticks_per_second": 1000, "frame_rate": None}],
        "assets": [{"id": f"asset-{D}", "content_digest": D}],
        "entities": [{"id": "person_001", "kind": "person",
                      "label": "Craig", "object_class_id": None}],
        "observations": [], "identity_links": [], "masks": [],
        "tracks": [{"id": "track-region_001", "entity_id": "person_001",
                    "clock_id": f"clock-{D}", "samples": samples,
                    "provenance_id": f"prov-{D}-m3d"}],
        "provenance": [{"id": f"prov-{D}-m3d", "producer": "fixture"}],
        "clip_uses": [], "renders": [], "frame_maps": [],
    }


def _bbox_centre_pixel(bbox, grid) -> tuple[int, int]:
    x0, y0, x1, y1 = bbox
    width, height = grid
    return (int((x0 + x1) / 2.0 * width), int((y0 + y1) / 2.0 * height))


# ── Acceptance: the interior point is inside the decoded mask ──


def test_region_at_returns_an_interior_point_inside_the_mask():
    model = _model()
    answer = region_query.region_at(model, "person_001", 1.0)
    assert answer["present"] is True
    decoded = person_regions.decode_runs(
        person_regions.encode_runs(_l_mask()), GRID)
    width, height = GRID
    px = int(answer["interior_point"][0] * width)
    py = int(answer["interior_point"][1] * height)
    assert decoded[py, px], (
        "the interior point must lie inside the decoded mask")
    # And this fixture proves why the point matters: the L's bbox
    # centre is background, so a bbox-centre answer would fail it.
    cx, cy = _bbox_centre_pixel(answer["bbox"], GRID)
    assert not decoded[cy, cx], (
        "the L-mask's bbox centre falls outside it - the test would not "
        "discriminate a bbox-centre implementation without this")


def test_region_at_holds_a_track_but_never_invents_across_a_gap():
    model = _model()
    held = region_query.region_at(model, "person_001", 1.5)
    assert held["present"] is True
    assert held["sample_tick"] == 1000
    absent = region_query.region_at(model, "person_001", 5.0)
    assert absent["present"] is False
    assert absent["reason"] == "absent_at_time"
    assert "bbox" not in absent, (
        "an absence carries its reason, never the nearest sample's geometry")


def test_region_at_names_why_it_is_absent():
    model = _model()
    assert region_query.region_at(
        model, "nobody-here", 1.0)["reason"] == "unknown_person"
    assert region_query.region_at(
        model, "", 1.0)["reason"] == "unknown_person"
    lone = json.loads(json.dumps(model))
    lone["entities"].append({"id": "person_002", "kind": "person",
                             "label": "Nobody", "object_class_id": None})
    assert region_query.region_at(
        lone, "person_002", 1.0)["reason"] == "no_region_tracks"


def test_regions_overlapping_names_the_entity_and_nothing_else():
    model = _model()
    # The L covers the left columns; the rect lives upper-right.
    left = region_query.regions_overlapping(model, [0.0, 0.0, 0.3, 1.0],
                                            0.0, 3.0)
    assert [row["entity_id"] for row in left] == ["person_001"]
    nowhere = region_query.regions_overlapping(model, [0.4, 0.4, 0.6, 0.6],
                                               0.0, 0.5)
    assert nowhere == []
    with pytest.raises(ValueError, match="unit square"):
        region_query.regions_overlapping(model, [0.0, 0.0, 2.0, 1.0],
                                         0.0, 3.0)


def test_resolve_person_gains_regions():
    resolved = evidence.resolve_person(_model(), "person_001")
    assert resolved is not None
    assert len(resolved["regions"]) == 1
    assert resolved["regions"][0]["id"] == "track-region_001"
    bare = json.loads(json.dumps(_model()))
    bare["tracks"] = []
    assert evidence.resolve_person(bare, "person_001")["regions"] == []
    assert evidence.resolve_person(_model(), "nobody-here") is None


# ── M3d path: `ren where` and region-carrying search hits ──


def _m3d_memory(tmp_path: Path) -> tuple[Path, Path]:
    """A scratch memory root holding one digest's identity + regions."""
    memory = tmp_path / "memory"
    _write(memory / D / "identity.json", {
        "content_digest": D, "status": "measured",
        "face_match_threshold": 0.25,
        "faces": [{"track_id": "face_001", "embedding": [1.0, 0.0],
                   "spans": [{"start": 0.5, "end": 2.5,
                              "box": [0, 0, 10, 10], "det_score": 0.9}]}],
        "voices": [], "speech_face_links": [], "instrument": {}})
    masks = [person_regions.encode_runs(_l_mask()),
             person_regions.encode_runs(_rect_mask())]
    sidecar = person_regions.write_sidecar(memory / D, masks, GRID)
    instances = []
    for index, moment, mask in ((0, 1.0, _l_mask()), (1, 2.0, _rect_mask())):
        summary = person_regions.summarize(mask)
        instances.append((moment, {"track": "region_001", **summary,
                                   "mask": index, "face": 0,
                                   "containment": 1.0,
                                   "face_track": "face_001"}))
    _write(memory / D / "regions.json", {
        "content_digest": D, "status": "measured",
        "coordinates": "image-normalised, top-left origin",
        "grid": GRID, "sidecar": sidecar,
        "frame_count": 2, "samples_measured": 2,
        "tracks": [{"track_id": "region_001", "start": 1.0, "end": 2.0,
                    "samples": 2, "face_track": "face_001",
                    "face_track_fraction": 1.0}],
        "samples": [{"t": moment, "instances": [inst]}
                    for moment, inst in instances],
        "instrument": {"method": "fixture"}})
    project = tmp_path / "proj"
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
             "path": "/nonexistent/a.mov"}]}},
        "source_fingerprints": {"clip_001": {
            "path": "/nonexistent/a.mov", "content_digest": D}},
    })
    (project / "project.yaml").write_text(
        "source:\n  person_names:\n    person_001: Craig\n",
        encoding="utf-8")
    return project, memory


def test_m3d_sidecar_decodes_to_the_summaries_region_at_returns(monkeypatch):
    """The stored interior point is inside the sidecar bytes `ren where`
    would render from - summary and mask cannot drift apart."""
    import os

    from library.tools import source_memory

    # This test is hermetic: the memory root travels explicitly and the
    # host's pointer at it is cleared, since the full-suite run executes
    # inside an agent session.
    monkeypatch.delenv(source_memory.MEMORY_ROOT_ENV, raising=False)
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        _m3d_memory(root)
        doc = person_regions.read_m3d(D, root / "memory")
        assert doc is not None
        mask = person_regions.read_mask(D, doc, 0, root / "memory")
        assert mask.sum() > 0
        inst = doc["samples"][0]["instances"][0]
        width, height = doc["grid"]
        px = int(inst["interior_point"][0] * width)
        py = int(inst["interior_point"][1] * height)
        assert mask[py, px]
        raw = gzip.decompress(
            (root / "memory" / D / doc["sidecar"]["file"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == doc["sidecar"]["sha256"]
        assert os.environ.get(source_memory.MEMORY_ROOT_ENV) is None


def test_where_lists_tracks_and_answers_at_a_moment(tmp_path, monkeypatch):
    from library.tools import source_memory

    project, memory = _m3d_memory(tmp_path)
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(memory))
    tracks = footage_query.where_answer(
        str(project), "person_001", memory_root=str(memory))
    assert tracks["person_id"] == "person_001"
    assert len(tracks["tracks"]) == 1
    assert tracks["tracks"][0]["track_id"] == "region_001"
    at = footage_query.where_answer(
        str(project), "Craig", at=1.0, memory_root=str(memory))
    place = at["places"][0]
    assert place["region"]["present"] is True
    assert place["region"]["interior_point"] == \
        person_regions.summarize(_l_mask())["interior_point"]
    gone = footage_query.where_answer(
        str(project), "person_001", at=9.0, memory_root=str(memory))
    assert gone["places"][0]["region"]["present"] is False
    assert gone["places"][0]["region"]["reason"] == "absent_at_time"
    with pytest.raises(RenRefusal, match="no person matching"):
        footage_query.where_answer(
            str(project), "nobody-here", memory_root=str(memory))


def _search_index_double(monkeypatch, roster, docs_by_digest):
    """A FootageIndex standing in for search: roster, sources and M3d
    pinned, no disk behind it."""
    idx = footage_query.FootageIndex.__new__(footage_query.FootageIndex)
    idx.project_folder = "/nonexistent/proj"
    idx._person_roster_cache = roster
    monkeypatch.setattr(footage_query, "clip_sources",
                        lambda _project: {"clip_001": {
                            "source_digest": D, "file": "a.mov"}})
    monkeypatch.setattr("library.tools.region_query.load_m3d_for_digests",
                        lambda digests, root=None: {
                            d: docs_by_digest[d] for d in digests
                            if d in docs_by_digest})
    return idx


def test_search_person_hits_carry_the_region_at_the_hit_tick(monkeypatch):
    _project, memory_holder = _m3d_memory(Path(__import__(
        "tempfile").mkdtemp()))
    doc = person_regions.read_m3d(D, memory_holder)
    roster = [{"person_id": "person_001", "name": "Craig",
               "face_tracks": [{"content_digest": D,
                                "track_id": "face_001"}]}]
    idx = _search_index_double(monkeypatch, roster, {D: doc})
    hit = {"clip_id": "clip_001", "start": 0.5, "end": 1.5}
    idx._attach_person_region("where is craig", hit)
    assert hit["region_person"] == "person_001"
    assert hit["region"]["present"] is True
    assert hit["region"]["interior_point"] == \
        person_regions.summarize(_l_mask())["interior_point"]
    # A hit where the person is absent carries the absence, not silence.
    far = {"clip_id": "clip_001", "start": 8.0, "end": 9.0}
    idx._attach_person_region("where is craig", far)
    assert far["region"] == {"present": False, "reason": "absent_at_time",
                             "detail": {"t": 8.5},
                             "entity_id": "person_001",
                             "content_digest": D}
    # A query naming nobody leaves the hit alone.
    plain = {"clip_id": "clip_001", "start": 0.5, "end": 1.5}
    idx._attach_person_region("parking lot", plain)
    assert "region" not in plain


def test_the_where_verb_is_registered_in_both_front_doors():
    import ren.commands as ren_commands

    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert "where" in by_name, "ren lost its 'where' verb"
    assert by_name["where"].module_argv == (
        "library.tools.analysis.footage_query", "where")
    repo = Path(__file__).resolve().parents[3]
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "where"' in native
