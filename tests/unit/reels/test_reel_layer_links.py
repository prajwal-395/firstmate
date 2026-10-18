"""Cascade Lane C: the reel layer's foreign keys into the Project Model.

Covers ``library/tools/reel_layer_links.py`` (C1 manifest/overlay/
sidecar links, C2 hearing QA, C3 anchor resolution) plus its wiring:
``marker_routing.route_note(..., model=)`` resolves the anchor, and
``review_channel.queue_note(..., note_id=)`` stores the stable id.

No Resolve, no real project: the model is minted in-memory through
``reel_project_model.build_reel_fragment`` and every store lands under
``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import reel_layer_links as links
from library.tools import reel_project_model as rpm
from library.tools.project_model import validate_project_model


def _placements():
    return [
        {"source_file": "/m/a.MXF", "source_in": 10.0, "source_out": 15.0,
         "record_start": 0.0, "record_frame": 0,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "speaker": "Ada", "master_start": 10.0, "master_end": 15.0},
        {"source_file": "/m/b.MXF", "source_in": 3.0, "source_out": 7.0,
         "record_start": 5.0, "record_frame": 120,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "speaker": "Ben", "master_start": 20.0, "master_end": 24.0},
    ]


def _track_plan():
    return {"video_tracks": [
        {"index": 1, "media_type": "video", "role": "a_roll",
         "name": "A-Roll", "occupant": "main"}],
        "audio_tracks": []}


def _model(reel="Reel 01"):
    model = rpm.blank_model("project-x")
    rpm.build_reel_fragment(model, reel, _placements(), _track_plan())
    validate_project_model(model)
    return model


def _manifest_row(source_file, source_in, source_out,
                  timeline_in, timeline_out, label="clip"):
    return {"source_file": source_file, "source_in": source_in,
            "source_out": source_out, "timeline_in": timeline_in,
            "timeline_out": timeline_out, "label": label}


# ── C1: manifest rows ─────────────────────────────────────────────


def test_link_manifest_resolves_rows_by_source():
    model = _model()
    manifest = {"tracks": {"V1": {"label": "A-Roll", "clips": [
        _manifest_row("/m/a.MXF", 10.0, 15.0, 0.0, 5.0, label="a1"),
        _manifest_row("/m/b.MXF", 3.0, 7.0, 5.0, 9.0, label="b1"),
    ]}}}
    linked, report = links.link_manifest(manifest, model)
    assert report["linked"] == 2
    assert report["skipped"] == []
    slugs = links.slug_for_timeline("Reel 01")
    first, second = linked["tracks"]["V1"]["clips"]
    assert first["clip_use_id"] == f"use:{slugs}:0000"
    assert second["clip_use_id"] == f"use:{slugs}:0001"
    assert first["edit_ids"] == [f"edit:{slugs}:assemble"]
    assert first["effect_ids"] == []
    # The input manifest is never mutated.
    assert "clip_use_id" not in manifest["tracks"]["V1"]["clips"][0]
    assert linked["_model_links"]["version"] == links.LINK_VERSION


def test_link_manifest_reports_what_it_cannot_resolve():
    model = _model()
    manifest = {"tracks": {"V1": {"label": "A-Roll", "clips": [
        _manifest_row("/m/ghost.MXF", 0.0, 2.0, 0.0, 2.0, label="ghost"),
        _manifest_row("/m/a.MXF", 40.0, 42.0, 2.0, 4.0, label="elsewhere"),
    ]}}}
    linked, report = links.link_manifest(manifest, model)
    assert report["linked"] == 0
    assert len(report["skipped"]) == 2
    ghost, elsewhere = linked["tracks"]["V1"]["clips"]
    assert ghost["clip_use_id"] is None
    assert ghost["edit_ids"] == [] and ghost["effect_ids"] == []
    assert elsewhere["clip_use_id"] is None


def test_link_manifest_without_a_model_links_nothing_and_says_so():
    manifest = {"tracks": {"V1": {"label": "A-Roll", "clips": [
        _manifest_row("/m/a.MXF", 10.0, 15.0, 0.0, 5.0, label="a1"),
    ]}}}
    linked, report = links.link_manifest(manifest, None)
    assert report["model_absent"] is True
    assert linked["tracks"]["V1"]["clips"][0]["clip_use_id"] is None
    assert len(report["skipped"]) == 1


def test_match_clip_use_refuses_what_cannot_be_a_span():
    model = _model()
    use_id, _ = links.match_clip_use(
        source_file="/m/a.MXF", source_start=11.0, source_end=12.0,
        model=model)
    assert use_id is not None
    assert links.match_clip_use(
        source_file="", source_start=11.0, source_end=12.0,
        model=model)[0] is None
    assert links.match_clip_use(
        source_file="/m/a.MXF", source_start=2.0, source_end=2.0,
        model=model)[0] is None
    assert links.match_clip_use(
        source_file="/m/a.MXF", source_start=11.0, source_end=12.0,
        model="not-a-model")[0] is None


def test_effect_ids_follow_the_effect_target():
    model = _model()
    slugs = links.slug_for_timeline("Reel 01")
    use_id = f"use:{slugs}:0000"
    model["effects"].append({
        "id": f"effect:{slugs}:grade000", "class_id": "color.grade",
        "target_id": use_id,
        "interval": {"clock_id": f"clock:reel:{slugs}",
                     "start": 0, "end": 600_000},
        "parameters": {"exposure": 0.1}, "keyframes": []})
    manifest = {"tracks": {"V1": {"label": "A-Roll", "clips": [
        _manifest_row("/m/a.MXF", 10.0, 15.0, 0.0, 5.0, label="a1"),
    ]}}}
    linked, _ = links.link_manifest(manifest, model)
    assert linked["tracks"]["V1"]["clips"][0]["effect_ids"] == [
        f"effect:{slugs}:grade000"]
    assert links.effects_on(model, "use:reel-01:9999") == []
    assert links.edits_targeting(model, "use:reel-01:9999") == []


# ── C1: overlay entries and subtitle sidecars ─────────────────────


def test_link_overlay_entries_match_title_effects_by_master_span():
    model = _model()
    slugs = links.slug_for_timeline("Reel 01")
    entries = [{"timeline_start": 10.0, "timeline_end": 15.0,
                "copy": "hello"}]
    # Titles are minted from overlay segments in the fragment builder;
    # replicate one segment here so the effect exists to match.
    placements = _placements()
    use_ids = [row["id"] for row in model["clip_uses"]
               if row["role"] == "a_roll"]
    assert use_ids
    titles = rpm.titles_from_segments(
        model, slugs, f"clock:reel:{slugs}",
        [{"overlay_path": "/t/one.mov", "timeline_start": 10.0,
          "timeline_end": 15.0, "copy": "hello"}],
        placements,
        {id(place): use_ids[0] for place in placements})
    assert titles["minted"]
    linked, report = links.link_overlay_entries(entries, model)
    assert report["linked"] == 1
    assert linked[0]["effect_id"] == titles["minted"][0]


def test_link_overlay_entries_report_unmatched_and_unlisted():
    model = _model()
    linked, report = links.link_overlay_entries(
        [{"timeline_start": 90.0, "timeline_end": 95.0}], model)
    assert linked[0]["effect_id"] is None
    assert len(report["skipped"]) == 1
    assert links.link_overlay_entries("not-a-list", model)[0] == []
    assert links.link_overlay_entries(
        [{"timeline_start": 1.0, "timeline_end": 2.0}], None
    )[0][0]["effect_id"] is None


def test_subtitle_sidecar_link_reads_the_content_key():
    sidecar = {"segment_id": "sub_x", "content_key": "abc123"}
    out = links.subtitle_sidecar_link(sidecar, clip_use_id="use:reel-01:0000")
    assert out["clip_use_id"] == "use:reel-01:0000"
    assert out["caption_binding"] == "abc123"
    assert "caption_binding" not in sidecar
    explicit = links.subtitle_sidecar_link(
        sidecar, clip_use_id=None, caption_binding="bind-v1")
    assert explicit["caption_binding"] == "bind-v1"
    assert explicit["clip_use_id"] is None
    bare = links.subtitle_sidecar_link({})
    assert bare == {"clip_use_id": None, "caption_binding": None}


# ── C2: hearing verdicts as receipt QA ────────────────────────────


def test_attach_hearing_qa_writes_receipt_qa_beside_details(tmp_path):
    from library.tools import project_model_store as store
    repo_root = Path(__file__).resolve().parents[3]
    media = sorted((repo_root / "tests/fixtures/project_model_e2e/media").glob("*.mp4"))
    assert media, "the synthetic fixture ships renderable media"
    project = tmp_path / "proj"
    project.mkdir()
    record = {"reel_placements": {"Reel 01": _placements()},
              "track_plans": {"Reel 01": _track_plan()},
              "reel_fps": 24.0}
    kept = rpm.maintain_from_build(str(project), record)
    assert kept["status"] == "committed"
    hearing = {"passed": True, "refusal": None,
               "script_divergence": {"substitutions": 0},
               "coverage": {"uncovered": 0}, "hearing_id": "hearing:abc"}
    # C2: the deliver records the render, and the hearing record beside
    # it becomes the receipt's QA fields in the same commit.
    out = rpm.note_deliver(
        str(project), 1, "Reel 01", str(media[0]), hearing=hearing)
    assert out["status"] == "committed"
    assert out["hearing_qa"]["attached"] is True
    _, model = store.load_project_model(project)
    slug = links.slug_for_timeline("Reel 01")
    receipt = next(r for r in model["receipts"] if r["id"] == f"receipt:{slug}")
    assert receipt["details"]["qa"]["hearing"]["passed"] is True
    assert receipt["details"]["qa"]["hearing"]["hearing_id"] == "hearing:abc"
    assert receipt["details"]["expected_frames"] > 0
    validate_project_model(model)
    # The pure attach reads the same receipt without a store, and never
    # mutates its input: the returned copy carries the new verdict while
    # the model passed in keeps the delivered one.
    import copy as _copy
    before = _copy.deepcopy(model["receipts"])
    same, report = links.attach_hearing_qa(model, slug, {"passed": False})
    assert report["attached"] is True
    assert model["receipts"] == before
    assert same["receipts"] != before


def test_attach_hearing_qa_refuses_without_a_receipt_or_hearing():
    model = rpm.blank_model("project-x")
    _, report = links.attach_hearing_qa(model, "reel-01", {"passed": True})
    assert report["attached"] is False
    assert "deliver" in report["reason"]
    fields, _ = links.hearing_qa_fields(None)
    assert fields == {}
    _, report = links.attach_hearing_qa(
        _model(), links.slug_for_timeline("Reel 01"), None)
    assert report["attached"] is False


def test_ingest_receipt_carries_optional_qa():
    from library.tools import project_model_effectcraft as ec
    fixtures = (Path(__file__).resolve().parents[2]
                / "fixtures/effectcraft_fframes_conformance")

    def _fixture(name):
        return json.loads((fixtures / name).read_text(encoding="utf-8"))

    comp = ec.CompSpec(name="Main", width=32, height=32,
                       frame_rate=10.0, duration_seconds=0.2)
    renders = [ec.RenderSpec(format="rgba_png_sequence",
                             output="effect_#####.png")]

    def _receipt(projection):
        return {
            "version": ec.RECEIPT_VERSION,
            "project_id": projection.project_id,
            "upstream_revision": ec.PINNED_EFFECTCRAFT_REV,
            "engine_version": ec.PINNED_EFFECTCRAFT_VERSION,
            "project_file": "Main.ecproj",
            "renders": [{
                "format": "rgba_png_sequence",
                "path": "effect_00000.png",
                "width": projection.comp.width,
                "height": projection.comp.height,
                "frames": projection.comp.expected_frames(),
                "has_alpha": True,
            }],
        }

    model = _fixture("model-masked.json")
    projection = ec.project_to_request(
        model, comp, renders, project_id="ec-conformance-masked")
    updated = ec.ingest_receipt(model, projection, _receipt(projection),
                                input_revision="rev-1",
                                qa={"hearing": {"passed": True}})
    receipt = next(r for r in updated["receipts"]
                   if r["id"] == "receipt-ec-main")
    assert receipt["details"]["qa"] == {"hearing": {"passed": True}}
    plain = ec.ingest_receipt(model, projection, _receipt(projection),
                              input_revision="rev-1")
    assert "qa" not in next(
        r for r in plain["receipts"] if r["id"] == "receipt-ec-main")["details"]
    with pytest.raises(ec.ReceiptMismatch, match="qa must be a mapping"):
        ec.ingest_receipt(model, projection, _receipt(projection),
                          input_revision="rev-1", qa=["not-a-mapping"])
    assert model == _fixture("model-masked.json")


# ── C3: anchor resolution ─────────────────────────────────────────


def test_resolve_clip_use_at_seconds_walks_the_frame_map():
    model = _model()
    slug = links.slug_for_timeline("Reel 01")
    use_id, basis = links.resolve_clip_use_at_seconds(model, slug, 1.0)
    assert use_id == f"use:{slug}:0000"
    assert "map:" in basis
    use_id, _ = links.resolve_clip_use_at_seconds(model, slug, 6.0)
    assert use_id == f"use:{slug}:0001"
    assert links.resolve_clip_use_at_seconds(
        model, slug, 99.0)[0] is None
    assert links.resolve_clip_use_at_seconds(
        model, "reel-that-was-never-built", 1.0)[0] is None
    assert links.resolve_clip_use_at_seconds(None, slug, 1.0)[0] is None
    assert links.resolve_clip_use_at_seconds(
        model, slug, "soon")[0] is None


def test_anchor_resolution_reads_only_projection_collections():
    """Lane C3's model reads must stay inside the routing sidecar.

    The sidecar carries clocks, frame maps and clip uses; if anchor
    resolution ever reaches another collection, routing silently
    degrades to clip_use_reason instead of answering. This test fails
    first, before the sidecar does.
    """
    from collections.abc import Mapping

    model = _model()
    allowed = {"clocks", "frame_maps", "clip_uses"}
    read: set[str] = set()

    class _Strict(Mapping):
        def __getitem__(self, key):
            read.add(key)
            if key not in allowed:
                raise AssertionError(
                    f"anchor resolution reached {key!r}, outside the "
                    f"routing sidecar {sorted(allowed)}")
            return model[key]

        def __iter__(self):
            return iter(model)

        def __len__(self):
            return len(model)

    slug = links.slug_for_timeline("Reel 01")
    use_id, _ = links.resolve_clip_use_at_seconds(_Strict(), slug, 1.0)
    assert use_id == f"use:{slug}:0000"
    assert read <= allowed


def test_annotate_pulled_note_resolves_and_reports():
    model = _model()
    note = {"timeline": "Reel 01", "frame": 108030,
            "note_id": "nt_abc", "text": "fix this"}
    out = links.annotate_pulled_note(
        note, model, timeline_fps=30.0, timeline_start_frame=108000)
    slug = links.slug_for_timeline("Reel 01")
    assert out["clip_use_id"] == f"use:{slug}:0000"
    assert out["note_id"] == "nt_abc"
    assert "clip_use_id" not in note
    unplaced = links.annotate_pulled_note({"timeline": "Reel 01"}, model)
    assert unplaced["clip_use_id"] is None
    assert "no placed frame" in unplaced["clip_use_reason"]


def test_route_note_with_model_resolves_the_anchor():
    from library.tools import marker_routing
    model = _model()
    raw = {"source": "timeline_marker", "name": "fix", "note": "this",
           "text": "fix\n\nthis", "frame": 108030,
           "frame_in_timeline_space": 30, "attachments": []}
    routed = marker_routing.route_note(
        raw, timeline="Reel 01", pull_file="Reel 01.pull.json",
        timeline_fps=30.0, timeline_start_frame=108000, model=model)
    slug = links.slug_for_timeline("Reel 01")
    assert routed.clip_use_id == f"use:{slug}:0000"
    assert routed.clip_use_reason != ""
    plain = marker_routing.route_note(
        raw, timeline="Reel 01", pull_file="Reel 01.pull.json",
        timeline_fps=30.0, timeline_start_frame=108000)
    assert plain.clip_use_id == ""
    assert "no model" in plain.clip_use_reason


def test_queue_note_stores_the_stable_note_id(tmp_path):
    from library.dashboard import review_channel
    anchor = {"selector": "[data-step-id=\"x\"]", "tag": "div",
              "text": "", "label": "x", "view": "pipeline", "step_id": "x"}
    note = review_channel.queue_note(
        tmp_path, "look at this", anchor, note_id="nt_stable_1")
    assert note["note_id"] == "nt_stable_1"
    assert note["id"].startswith("note_")
    bare = review_channel.queue_note(tmp_path, "another", anchor)
    assert bare["note_id"] == ""
    with pytest.raises(ValueError, match="note_id must be a string"):
        review_channel.queue_note(tmp_path, "bad", anchor, note_id=7)
