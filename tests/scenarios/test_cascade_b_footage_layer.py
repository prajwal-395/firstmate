"""Lane B (cascade report section 5): the footage layer's foreign keys.

The captain's constraint: a unifying thread connects everything so a
change cascades. The footage layer links into the Project Model by
foreign key while its bytes stay where they are:

- B1: the scan writes Asset/Stream/Clock through the one revision API,
  the catalog stamps `model_revision`, the step ledger calls
  `ingest_fingerprint` (R10).
- B2: the footage index carries `{asset_id, clock interval}` per
  segment; transcript segments gain `obs_id` (span-cache key
  untouched).
- B3: 1.06 mask files gain `{mask_id, track_id}` sidecars at stamp
  time.

Every fixture is built under ``tmp_path``; no test reaches a real
project. Each test names the dangle it would leave: a test that
cannot fail is worse than no test.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.steps.step_1_01_scan_project import step as scan_step
from library.capabilities.footage import catalog as catalog_impl
from library.steps.step_1_02_catalog_footage import step as catalog_step
from library.tools import footage_model_link as link
from library.tools import project_model_evidence as evidence
from library.tools import step_ledger
from library.tools.analysis import footage_segments as segments


def _media(project: Path, name: str = "clip_a.mp4") -> Path:
    raw = project / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    path.write_bytes(os.urandom(3 * 1024 * 1024))
    return path


def test_scan_writes_assets_streams_clocks_through_the_revision_api(tmp_path):
    """B1: one revision per scan with changes, through the store - no second writer."""
    project = tmp_path / "proj"
    project.mkdir()
    _media(project)

    out = scan_step.scan_project_folder(str(project))
    assert out["model_link"]["status"] == "committed", out["model_link"]
    assert out["model_revision"] == 1

    from library.tools import project_model_store as store
    from library.tools.project_model import validate_project_model
    revision, model = store.load_project_model(project)
    assert revision == 1
    validate_project_model(model)

    asset_ids = out["model_link"]["asset_ids"]
    assert len(asset_ids) == 1
    digest = asset_ids[0].removeprefix("asset-")
    assert [a["id"] for a in model["assets"]] == asset_ids
    assert len(model["streams"]) == 1
    assert len(model["clocks"]) == 1

    # A second identical scan is an idempotent no-op (#2816 writes
    # record-level changes, so an unchanged re-scan commits no empty
    # revision): the revision stays put and nothing duplicates - the
    # deterministic ids resolve to each other, they do not pile up.
    again = scan_step.scan_project_folder(str(project))
    assert again["model_link"]["status"] == "committed"
    assert again["model_revision"] == 1
    revision, model = store.load_project_model(project)
    assert revision == 1
    assert [a["id"] for a in model["assets"]] == asset_ids
    assert len(model["observations"]) == len(
        {o["id"] for o in model["observations"]})


def test_link_ids_match_the_migration_scheme(tmp_path):
    """B1/B3: the link module spells ids the way the migration mints.

    Two spellings of one id is the dangle this lane exists to close:
    if either side drifts, the sidecar and the model stop resolving.
    """
    digest = "b46635029cb74fdf68fa5c6da4e102c495e04028a84f136d1dec4443e92283e8"
    assert link.asset_id_for_digest(digest) == evidence._asset_id(digest)
    assert link.clock_id_for_digest(digest) == evidence._clock_id(digest)
    assert link.stream_id_for_digest(digest) == evidence._stream_id(digest)
    assert link.utterance_obs_id(digest, 3) == f"obs-{digest}-m1u-0003"
    assert link.utterance_obs_id(digest, 3) != link.utterance_obs_id(
        digest, 4)
    entries = link.mask_sidecar_entries(
        "clip_001", {"objects": [{"object_id": "person-1"}, {}]})
    assert entries[0] == {"object_id": "person-1",
                          "mask_id": "mask-clip_001-person-1",
                          "track_id": "track-clip_001-person-1"}
    # An object with no id is still linkable, never dropped silently.
    assert entries[1]["mask_id"] == "mask-clip_001-obj"
    assert link.sidecar_name("clip_001") == \
        "clip_001_segmentation.model_ids.json"


def test_utterance_observation_resolves_index_and_transcript_ids(
        tmp_path, monkeypatch):
    """B2: the migration mints the Observation both readers resolve to.

    The word-level rows keep their counter ids (no reader can
    recompute those); the utterance row carries the deterministic lane
    key the index and the transcript mint identically.
    """
    doc = {"instrument": {"arm": "fixture", "aligner": "fixture"},
           "utterances": [
        {"text": "hello world", "start": 1.0, "end": 2.5,
         "words": [{"word": "hello", "start": 1.0, "end": 1.5,
                    "confidence": 0.9}]},
        {"text": "", "start": 3.0, "end": 3.5, "words": []},
    ]}
    monkeypatch.setattr(evidence.source_memory, "read_m1",
                        lambda digest, root=None: (doc, "fresh"))
    builder = evidence._Builder("project-probe")
    evidence._migrate_transcript(builder, "abc123", "asset-abc123",
                                 "clock-abc123", None)
    rows = [o for o in builder.model["observations"]
            if o["id"] == link.utterance_obs_id("abc123", 0)]
    assert len(rows) == 1
    assert rows[0]["interval"] == {"clock_id": "clock-abc123",
                                   "start": 1000, "end": 2500}
    assert rows[0]["modality"] == "speech"
    assert rows[0]["payload"]["evidence_contract"]["method"] == (
        '{"aligner":"fixture","arm":"fixture"}')
    # An empty utterance mints no Observation: honestly absent, never
    # an empty row a transcript segment could resolve to.
    assert not [o for o in builder.model["observations"]
                if o["id"] == link.utterance_obs_id("abc123", 1)]
    from library.tools.project_model import validate_project_model
    validate_project_model(builder.model)


def test_index_segments_carry_asset_clock_interval():
    """B2: every speech segment knows its model span; unresolvable is
    all-None, never a guessed id."""
    clip = {"clip_id": "clip_001", "filename": "a.mp4"}
    temporal = {"speech_regions": [
        {"text": "hello world", "start": 1.0, "end": 2.5,
         "words": [{"word": "hello", "start": 1.0, "end": 1.5}]}]}
    (region,) = segments._speech_segments(clip, temporal, {}, "abc123")
    assert (region.asset_id, region.clock_id,
            region.start_tick, region.end_tick) == (
        "asset-abc123", "clock-abc123", 1000, 2500)
    # 1.04 regions are not M1 utterances: no utterance Observation
    # exists for them, so no obs_id is minted.
    assert region.obs_id is None
    assert region.to_dict()["asset_id"] == "asset-abc123"

    memory = {"utterances": [
        {"text": "hello world", "start": 1.0, "end": 2.5,
         "words": [{"word": "hello", "start": 1.0, "end": 1.5}]}]}
    (row,) = segments._memory_speech_segments(
        clip, memory, {}, {}, "abc123")
    assert row.obs_id == link.utterance_obs_id("abc123", 0)
    assert row.to_dict()["obs_id"] == link.utterance_obs_id("abc123", 0)

    (bare,) = segments._speech_segments(clip, temporal, {}, None)
    assert (bare.asset_id, bare.clock_id, bare.start_tick,
            bare.end_tick, bare.obs_id) == (None,) * 5


def test_transcript_segments_gain_obs_id_leaving_the_cache_key_alone(
        tmp_path, monkeypatch):
    """B2: bound rows resolve to the M1 utterance they overlap most.

    The span-cache key is untouched: this adds a field, it changes no
    key. Rows with no source span, no digest, or no overlapping fresh
    utterance carry no obs_id.
    """
    monkeypatch.setattr(evidence.source_memory, "load_catalog",
                        lambda pf: [{"clip_id": "clip_001",
                                     "source_file": "/m/a.mp4",
                                     "path": "/m/a.mp4"}])
    monkeypatch.setattr(evidence.source_memory,
                        "load_recorded_fingerprints", lambda pf: {})
    monkeypatch.setattr(evidence.source_memory, "digest_for_clip",
                        lambda pf, clip, rec: ("abc123", "live"))
    monkeypatch.setattr(evidence.source_memory, "read_m1",
                        lambda digest, root=None: (
                            {"utterances": [
                                {"text": "hi", "start": 10.0, "end": 12.0,
                                 "words": []}]}, "fresh"))
    document = {"segments": [
        {"speaker": "A", "text": "hi", "source_file": "/m/a.mp4",
         "source_start": 10.5, "source_end": 11.5},
        {"speaker": "A", "text": "unbound"},
        {"speaker": "A", "text": "elsewhere", "source_file": "/m/a.mp4",
         "source_start": 50.0, "source_end": 51.0},
    ]}
    counts = link.attach_transcript_obs_ids(document, str(tmp_path))
    assert counts == {"linked": 1, "unlinked": 2}
    assert document["segments"][0]["obs_id"] == \
        link.utterance_obs_id("abc123", 0)
    assert "obs_id" not in document["segments"][1]
    assert "obs_id" not in document["segments"][2]
    # Nothing else on the row moved: the cache key reads the same span.
    assert document["segments"][0]["source_start"] == 10.5


def test_mask_sidecar_lands_beside_the_matte(tmp_path):
    """B3: the sidecar's ids are the migration's ids, beside the file
    whose bytes stay the authority."""
    from library.steps.step_1_06_object_segmentation import step as seg
    matte = tmp_path / "clip_001_segmentation.json"
    matte.write_text(json.dumps(
        {"objects": [{"object_id": "person-1"}]}), encoding="utf-8")
    seg._stamp_segmentation_model_ids(matte, "clip_001")
    sidecar = tmp_path / "clip_001_segmentation.model_ids.json"
    assert sidecar.is_file(), "the matte without a sidecar is the dangle"
    body = json.loads(sidecar.read_text(encoding="utf-8"))
    assert body["objects"] == [{"object_id": "person-1",
                                "mask_id": "mask-clip_001-person-1",
                                "track_id": "track-clip_001-person-1"}]
    # A matte that fails to parse leaves no sidecar and raises nothing:
    # the bytes stay usable, the next migration reads them directly.
    bad = tmp_path / "clip_002_segmentation.json"
    bad.write_text("{not json", encoding="utf-8")
    seg._stamp_segmentation_model_ids(bad, "clip_002")
    assert not (tmp_path / "clip_002_segmentation.model_ids.json").exists()


def test_catalog_stamps_model_revision(tmp_path, monkeypatch):
    """B1: every catalog row resolves into the scan's revision.

    ffprobe is mocked (identity, not measurement, is under test); the
    stamping is what would otherwise dangle.
    """
    project = tmp_path / "proj"
    project.mkdir()
    first = project / "a.mp4"
    first.write_bytes(b"\x00" * 1024)
    second = project / "b.mp4"
    second.write_bytes(b"\x01" * 1024)
    monkeypatch.setattr(
        catalog_impl, "extract_metadata",
        lambda filepath, **kwargs: {"duration_seconds": 10.0,
                                    "width": 1920, "height": 1080,
                                    "frame_rate": 30.0,
                                    "has_audio": False})
    files = [{"path": str(first), "filename": "a.mp4",
              "extension": ".mp4", "size_bytes": 1024,
              "clip_id": "clip_001"},
             {"path": str(second), "filename": "b.mp4",
              "extension": ".mp4", "size_bytes": 1024,
              "clip_id": "clip_002"}]
    out = catalog_step.catalog_footage(files, model_revision=7)
    assert out["model_revision"] == 7
    assert [e["model_revision"] for e in out["clip_catalog"]] == [7, 7]
    # A run whose scan predates Lane B stamps None: unlinked, never
    # revision zero.
    bare = catalog_step.catalog_footage(files)
    assert bare["model_revision"] is None
    assert {e["model_revision"] for e in bare["clip_catalog"]} == {None}
    assert "model_revision" in inspect.signature(
        catalog_step.catalog_footage).parameters
    # The stamp has one owner, the link module - the step only routes
    # the value (the input-contract survey reads a step's own
    # subscript as a refusal, a shared tool's as a row statement).
    rows = [{"clip_id": "clip_001"}, "not-a-row"]
    link.stamp_catalog_revision(rows, 9)
    assert rows[0]["model_revision"] == 9
    assert rows[1] == "not-a-row"


def test_step_ledger_calls_the_one_fingerprint(tmp_path):
    """B1 R10: the ledger calls ingest_fingerprint, not a reimplementation."""
    mark = step_ledger.ingest_mark(str(tmp_path))
    assert mark is not None and mark["digest"]
    from library.tools.analysis import footage_segments
    assert mark == footage_segments.ingest_fingerprint(str(tmp_path))
    # A project with no ingest at all fingerprints to zero files, which
    # is itself a stable answer - absent ingest reads as empty, never
    # as a failure.
    assert step_ledger.ingest_mark("/nonexistent-lane-b") == mark


def test_dag_routes_model_revision_scan_to_catalog():
    """B1: the edge, the scan output, and the catalog input agree.

    A route to an undeclared input (or from an undeclared output) is
    the declaration drift AGENTS.md 10.1 refuses.
    """
    dag = json.loads((REPO / "library" / "processes" / "edit_video"
                      / "dag.json").read_text(encoding="utf-8"))
    edge = next(e for e in dag["edges"]
                if e["from"] == "scan" and e["to"] == "catalog")
    assert edge["data_mapping"]["model_revision"] == "model_revision"
    scan_manifest = json.loads(
        (REPO / "library" / "steps" / "step_1_01_scan_project"
         / "manifest.json").read_text(encoding="utf-8"))
    assert "model_revision" in {o["name"] for o in
                                scan_manifest["interface"]["outputs"]}
    catalog_manifest = json.loads(
        (REPO / "library" / "steps" / "step_1_02_catalog_footage"
         / "manifest.json").read_text(encoding="utf-8"))
    catalog_input = next(i for i in catalog_manifest["interface"]["inputs"]
                         if i["name"] == "model_revision")
    assert catalog_input.get("required", True) is False
