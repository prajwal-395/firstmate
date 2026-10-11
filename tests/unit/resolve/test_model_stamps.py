"""Lane A3: model ids stamped onto Resolve markers, generations joined to revisions.

Offline proof, through the Resolve double and isolated stores: a placed
clip's stamp reads back with the model ids from the build's revision,
and the shadow generation row records that revision plus the verified
binding ids. The live half (the same stamp through the broker
qualification session on Ren Qualification) is
`tests/qualification/test_model_stamps_against_resolve.py`.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from library.tools import marker_payload
from library.tools import model_stamps as stamps
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


ASSET = "asset-0123456789abcdef"
USE = "clip_use:0123456789abcdef"


@pytest.fixture
def reel():
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    timeline.AddMarker(10, "Blue", "note", "keep", 1)
    project.SetCurrentTimeline(timeline)
    with assume_sole_writer("model stamps use the Resolve double"):
        yield project, timeline


# ── The record ────────────────────────────────────────────────────


def test_record_carries_everything_merge_record_requires():
    record = stamps.model_link_record(
        asset_id=ASSET, clip_use_id=USE, revision=7,
        timeline="Reel 09", track="V1", label="speech_0")
    for key in ("kind", "writer", "writer_version", "id", "at"):
        assert record[key], key
    assert record["id"] == f"model_link_{USE}"
    assert (record["asset_id"], record["clip_use_id"],
            record["revision"]) == (ASSET, USE, 7)


def test_record_id_is_deterministic_per_clip_use():
    first = stamps.model_link_record(asset_id=ASSET, clip_use_id=USE)
    second = stamps.model_link_record(asset_id="asset-other", clip_use_id=USE)
    assert first["id"] == second["id"]
    assert "revision" not in first


@pytest.mark.parametrize("asset_id,clip_use_id", [
    ("", USE), (ASSET, ""), (None, USE), (ASSET, None),
])
def test_empty_ids_are_refused_not_guessed(asset_id, clip_use_id):
    with pytest.raises(ValueError):
        stamps.model_link_record(
            asset_id=asset_id, clip_use_id=clip_use_id)


@pytest.mark.parametrize("revision", [0, -1, "7", 7.0, True])
def test_non_revision_revisions_are_refused(revision):
    with pytest.raises(ValueError):
        stamps.model_link_record(
            asset_id=ASSET, clip_use_id=USE, revision=revision)


def test_restamping_replaces_and_leaves_other_writers_alone():
    envelope = marker_payload.new_envelope()
    marker_payload.merge_record(envelope, {
        "kind": marker_payload.KIND_STILL, "writer": "capture_frame",
        "writer_version": 1, "id": "still_1", "at": marker_payload.utc_now(),
        "path": "marker_feedback/stills/x.png"})
    stamps.stamp_envelope(envelope, [
        stamps.model_link_record(asset_id=ASSET, clip_use_id=USE,
                                 revision=1)])
    stamps.stamp_envelope(envelope, [
        stamps.model_link_record(asset_id="asset-new", clip_use_id=USE,
                                 revision=2)])
    links = stamps.model_links_of(envelope)
    assert len(links) == 1
    assert (links[0]["asset_id"], links[0]["revision"]) == ("asset-new", 2)
    assert marker_payload.records_of(envelope, marker_payload.KIND_STILL)


def test_stamp_envelope_refuses_foreign_kinds():
    envelope = marker_payload.new_envelope()
    with pytest.raises(ValueError, match="model_link"):
        stamps.stamp_envelope(envelope, [{"kind": "decision"}])


def test_model_link_for_answers_one_use_or_nothing():
    envelope = marker_payload.new_envelope()
    assert stamps.model_link_for(envelope, USE) is None
    stamps.stamp_envelope(envelope, [
        stamps.model_link_record(asset_id=ASSET, clip_use_id=USE)])
    found = stamps.model_link_for(envelope, USE)
    assert found["asset_id"] == ASSET
    assert stamps.model_link_for(envelope, "clip_use:other") is None


# ── Stamping a timeline ───────────────────────────────────────────


def test_a_placed_clips_stamp_reads_back_with_the_builds_ids(reel):
    _, timeline = reel
    report = stamps.stamp_timeline_markers(timeline, [
        {"frame": 10, "asset_id": ASSET, "clip_use_id": USE,
         "revision": 4, "track": "V1", "label": "speech_0"}])
    assert (report.markers_stamped, report.records_written) == (1, 1)
    assert not report.skipped and not report.refused

    envelope = marker_payload.parse(timeline.GetMarkerCustomData(10))
    found = stamps.model_link_for(envelope, USE)
    assert found is not None
    assert (found["asset_id"], found["clip_use_id"],
            found["revision"]) == (ASSET, USE, 4)


def test_a_link_without_a_marker_is_reported_and_creates_none(reel):
    _, timeline = reel
    before = dict(timeline.GetMarkers())
    report = stamps.stamp_timeline_markers(timeline, [
        {"frame": 30, "asset_id": ASSET, "clip_use_id": USE}])
    assert report.markers_stamped == 0
    assert [entry["frame"] for entry in report.skipped] == [30]
    assert timeline.GetMarkers().keys() == before.keys()


def test_stamping_keeps_the_captains_words_and_other_records(reel):
    _, timeline = reel
    stamps.stamp_timeline_markers(timeline, [
        {"frame": 10, "asset_id": ASSET, "clip_use_id": USE}])
    marker = timeline.GetMarkers()[10]
    assert (marker["name"], marker["note"]) == ("note", "keep")
    stamps.stamp_timeline_markers(timeline, [
        {"frame": 10, "asset_id": "asset-second",
         "clip_use_id": "clip_use:second"}])
    envelope = marker_payload.parse(timeline.GetMarkerCustomData(10))
    assert len(stamps.model_links_of(envelope)) == 2


# ── The NLE binding ───────────────────────────────────────────────


def test_binding_has_every_key_the_model_schema_requires():
    binding = stamps.build_nle_binding(
        timeline_id="timeline:abc", timeline_identity="Reel 09",
        project_identity="Ren Qualification", generation=3,
        clip_bindings={USE: "clip:unique-1"})
    assert binding["timeline_id"] == "timeline:abc"
    assert binding["system"] == "resolve"
    assert binding["generation"] == 3
    assert binding["clip_bindings"] == {USE: "clip:unique-1"}
    assert binding["external_bindings"] == {}


def test_binding_id_is_deterministic_over_its_inputs():
    kwargs = {"timeline_id": "timeline:abc",
              "timeline_identity": "Reel 09",
              "project_identity": "Ren Qualification", "generation": 3,
              "clip_bindings": {USE: "clip:unique-1"}}
    assert (stamps.build_nle_binding(**kwargs)["id"]
            == stamps.build_nle_binding(**kwargs)["id"])
    other = dict(kwargs, generation=4)
    assert stamps.build_nle_binding(**other)["id"] != \
        stamps.build_nle_binding(**kwargs)["id"]


@pytest.mark.parametrize("kwargs", [
    {"clip_bindings": {}},
    {"clip_bindings": {USE: ""}},
    {"clip_bindings": {"": "clip:unique-1"}},
    {"generation": 0},
    {"timeline_id": ""},
    {"project_identity": ""},
    {"timeline_identity": ""},
])
def test_binding_refuses_what_it_cannot_bind(kwargs):
    base = {"timeline_id": "timeline:abc",
            "timeline_identity": "Reel 09",
            "project_identity": "Ren Qualification", "generation": 3,
            "clip_bindings": {USE: "clip:unique-1"}}
    with pytest.raises(ValueError):
        stamps.build_nle_binding(**{**base, **kwargs})


# ── Generations joined to revisions ───────────────────────────────


def _linked_store(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    generation = store.record(
        project="Podcast", timeline_id="timeline:09",
        timeline_name="Reel 09", snapshot={"a": 1},
        source=shadow.OBSERVED, expected_head=0)
    assert generation.model_revision is None
    assert generation.nle_binding_ids == ()
    return store


def test_record_carries_linkage_and_reads_it_back(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    generation = store.record(
        project="Podcast", timeline_id="timeline:09",
        timeline_name="Reel 09", snapshot={"a": 1},
        source=shadow.OBSERVED, expected_head=0,
        model_revision=7, nle_binding_ids=["nle:abc"])
    assert generation.model_revision == 7
    assert generation.nle_binding_ids == ("nle:abc",)
    again = store.get("Podcast", "timeline:09", 1)
    assert (again.model_revision, again.nle_binding_ids) == (7, ("nle:abc",))
    assert again.summary()["model_revision"] == 7


def test_ids_without_a_revision_are_refused(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with pytest.raises(ValueError, match="without a revision"):
        store.record(
            project="Podcast", timeline_id="timeline:09",
            timeline_name="Reel 09", snapshot={"a": 1},
            source=shadow.OBSERVED, expected_head=0,
            nle_binding_ids=["nle:abc"])
    with pytest.raises(ValueError):
        store.record(
            project="Podcast", timeline_id="timeline:09",
            timeline_name="Reel 09", snapshot={"a": 1},
            source=shadow.OBSERVED, expected_head=0, model_revision=0)


def test_attach_joins_a_recorded_generation_to_its_revision(tmp_path):
    store = _linked_store(tmp_path)
    joined = store.attach_model_link(
        project="Podcast", timeline_id="timeline:09", generation=1,
        model_revision=7, nle_binding_ids=["nle:abc"])
    assert joined.model_revision == 7
    assert joined.nle_binding_ids == ("nle:abc",)
    assert store.head("Podcast", "timeline:09").model_revision == 7


def test_attach_moves_forward_never_back(tmp_path):
    store = _linked_store(tmp_path)
    store.attach_model_link(
        project="Podcast", timeline_id="timeline:09", generation=1,
        model_revision=7, nle_binding_ids=["nle:abc"])
    with pytest.raises(ValueError, match="older revision"):
        store.attach_model_link(
            project="Podcast", timeline_id="timeline:09", generation=1,
            model_revision=6)
    with pytest.raises(ValueError, match="different bindings"):
        store.attach_model_link(
            project="Podcast", timeline_id="timeline:09", generation=1,
            model_revision=7, nle_binding_ids=["nle:other"])
    moved = store.attach_model_link(
        project="Podcast", timeline_id="timeline:09", generation=1,
        model_revision=8, nle_binding_ids=["nle:def"])
    assert (moved.model_revision, moved.nle_binding_ids) == (8, ("nle:def",))


def test_attach_to_an_unknown_generation_is_refused(tmp_path):
    store = _linked_store(tmp_path)
    with pytest.raises(shadow.ShadowError):
        store.attach_model_link(
            project="Podcast", timeline_id="timeline:09", generation=9,
            model_revision=7)


def test_an_unchanged_reread_keeps_linkage_it_is_given(reel, tmp_path):
    project, timeline = reel
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    first = shadow.observe(project, timeline, store)
    assert first.model_revision is None
    joined = store.record(
        project=first.project, timeline_id=first.timeline_id,
        timeline_name=first.timeline_name,
        snapshot=store.snapshot(first), source=shadow.OBSERVED,
        expected_head=first.generation, model_revision=7,
        nle_binding_ids=["nle:abc"])
    assert joined.generation == first.generation
    assert joined.model_revision == 7


def test_a_store_from_before_this_lane_migrates(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE snapshots (hash TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE generations (
            project TEXT NOT NULL, timeline_id TEXT NOT NULL,
            generation INTEGER NOT NULL, timeline_name TEXT NOT NULL,
            hash TEXT NOT NULL, source TEXT NOT NULL, patch TEXT,
            receipt TEXT, recorded_at REAL NOT NULL,
            verified_at REAL NOT NULL,
            PRIMARY KEY (project, timeline_id, generation));
    """)
    db.execute("INSERT INTO snapshots (hash, body)"
               " VALUES ('h', '{\"a\":1}')")
    db.execute("INSERT INTO generations (project, timeline_id, generation,"
               " timeline_name, hash, source, patch, receipt, recorded_at,"
               " verified_at) VALUES "
               "('P', 't', 1, 'T', 'h', 'observed', NULL, NULL, 1.0, 2.0)")
    db.commit()
    db.close()

    store = shadow.ShadowStore(path)
    old = store.get("P", "t", 1)
    assert old.model_revision is None
    assert old.nle_binding_ids == ()
    joined = store.attach_model_link(
        project="P", timeline_id="t", generation=1, model_revision=3,
        nle_binding_ids=["nle:x"])
    assert (joined.model_revision, joined.nle_binding_ids) == (3, ("nle:x",))


def test_the_mirror_carries_linkage_both_ways(tmp_path):
    source = shadow.ShadowStore(tmp_path / "a.db")
    source.record(
        project="Podcast", timeline_id="timeline:09",
        timeline_name="Reel 09", snapshot={"a": 1},
        source=shadow.OBSERVED, expected_head=0,
        model_revision=7, nle_binding_ids=["nle:abc"])
    document = source.export_document("Podcast")
    entry = document["generations"][0]
    assert entry["model_revision"] == 7
    assert entry["nle_binding_ids"] == ["nle:abc"]

    target = shadow.ShadowStore(tmp_path / "b.db")
    report = target.merge_document(document)
    assert report["generations"] == 1
    merged = target.get("Podcast", "timeline:09", 1)
    assert (merged.model_revision, merged.nle_binding_ids) == (7, ("nle:abc",))


def test_a_mirror_from_before_this_lane_merges_unlinked(tmp_path):
    source = shadow.ShadowStore(tmp_path / "a.db")
    source.record(
        project="Podcast", timeline_id="timeline:09",
        timeline_name="Reel 09", snapshot={"a": 1},
        source=shadow.OBSERVED, expected_head=0)
    document = source.export_document("Podcast")
    del document["generations"][0]["model_revision"]
    del document["generations"][0]["nle_binding_ids"]
    target = shadow.ShadowStore(tmp_path / "b.db")
    assert target.merge_document(document)["generations"] == 1
    merged = target.get("Podcast", "timeline:09", 1)
    assert merged.model_revision is None
    assert merged.nle_binding_ids == ()


# ── End to end on the double ────────────────────────────────────────


def test_stamp_then_generation_then_binding_close_the_loop(
        reel, tmp_path):
    """The lane's own shape, offline: stamp, observe, attach, bind."""
    project, timeline = reel
    stamps.stamp_timeline_markers(timeline, [
        {"frame": 10, "asset_id": ASSET, "clip_use_id": USE, "revision": 7}])

    store = shadow.ShadowStore(tmp_path / "shadow.db")
    observed = shadow.observe(project, timeline, store)
    assert observed.generation == 1
    assert observed.model_revision is None

    item = timeline.GetItemListInTrack("video", 1)[0]
    binding = stamps.build_nle_binding(
        timeline_id=observed.timeline_id,
        timeline_identity=observed.timeline_name,
        project_identity=observed.project,
        generation=observed.generation,
        clip_bindings={USE: item.GetUniqueId()})
    joined = store.attach_model_link(
        project=observed.project, timeline_id=observed.timeline_id,
        generation=observed.generation, model_revision=7,
        nle_binding_ids=[binding["id"]])

    envelope = marker_payload.parse(timeline.GetMarkerCustomData(10))
    found = stamps.model_link_for(envelope, USE)
    assert found is not None
    assert (found["asset_id"], found["revision"]) == (ASSET, 7)
    assert (joined.model_revision, joined.nle_binding_ids) == (
        7, (binding["id"],))
    assert binding["clip_bindings"][USE] == item.GetUniqueId()
    assert binding["generation"] == joined.generation
