"""Structural edit plans name only the work their evidence supports."""

import hashlib
import json
from types import SimpleNamespace

from library.tools.structural_patch import plan_structural_patch


def _manifest():
    names = ("aroll", "captions", "motion_graphics", "audio", "fusion",
             "timeline")
    components = {}
    for name in names:
        component = {"id": f"reel-01:{name}", "status": "recorded",
                     "dependencies": {"input": f"fingerprint-{name}"},
                     "assets": []}
        component["digest"] = hashlib.sha256(json.dumps(
            {key: component[key]
             for key in ("id", "dependencies", "assets")},
            sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        components[name] = component
    manifest_digest = hashlib.sha256(json.dumps(
        {name: components[name]["digest"] for name in sorted(components)},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"format": "reel_component_manifest/1",
            "components": components, "manifest_digest": manifest_digest}


def _tracks():
    return [{"type": "video", "index": 1, "clips": [
        {"record_in": 0, "record_out": 24, "source_in_frame": 0,
         "source_out_frame": 24, "media_pool_item_id": "media-1"},
        {"record_in": 24, "record_out": 48, "source_in_frame": 24,
         "source_out_frame": 48, "media_pool_item_id": "media-1"},
    ]}]


def _change(**overrides):
    values = {"row": "V1", "item_index": 0, "previous_record": 0,
              "record_frame": 2, "previous_duration": 24, "duration": 24,
              "played_length_changes": False, "how": "move"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_local_move_names_direct_targets_and_track_interval():
    plan = plan_structural_patch(_tracks(), [_change()],
                                 component_manifest=_manifest())

    assert plan["direct_targets"][0]["stable_id"]
    assert plan["dependent_targets"] == []
    assert plan["affected_by_track"]["V1"] == {
        "start_frame": 0, "end_frame": 48}
    assert not plan["full_reconstruction_required"]
    assert plan["stages"][-1] == "commit-staged-edit"


def test_ripple_omission_escalates_with_exact_unplanned_target():
    plan = plan_structural_patch(_tracks(), [_change(how="shift")],
                                 component_manifest=_manifest())

    assert plan["full_reconstruction_required"]
    assert "ripple omitted downstream items: V1[1]@24" in plan[
        "escalation_reason"]


def test_audio_presence_requires_declared_picture_sync_mapping():
    tracks = _tracks() + [{"type": "audio", "index": 1, "clips": [
        {"record_in": 0, "record_out": 48, "source_in_frame": 0,
         "source_out_frame": 48, "media_pool_item_id": "audio-1"},
    ]}]
    plan = plan_structural_patch(tracks, [_change()],
                                 component_manifest=_manifest())

    assert not plan["sync_proven"]
    assert "no declared audio sync mapping" in plan["escalation_reason"]


def test_rendered_asset_reuse_checks_recorded_bytes(tmp_path):
    asset = tmp_path / "captions" / "caption.mov"
    asset.parent.mkdir()
    asset.write_bytes(b"caption-render")
    manifest = _manifest()
    manifest["components"]["captions"]["assets"] = [{
        "path": "captions/caption.mov",
        "sha256": hashlib.sha256(b"caption-render").hexdigest(),
        "bytes": len(b"caption-render"),
    }]
    caption = manifest["components"]["captions"]
    caption["digest"] = hashlib.sha256(json.dumps(
        {key: caption[key] for key in ("id", "dependencies", "assets")},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest["manifest_digest"] = hashlib.sha256(json.dumps(
        {name: manifest["components"][name]["digest"]
         for name in sorted(manifest["components"])},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    change = _change(how="move", record_frame=0)

    plan = plan_structural_patch(_tracks(), [change],
                                 component_manifest=manifest,
                                 asset_root=str(tmp_path))

    assert plan["render_asset_identity_proven"]
    asset.write_bytes(b"different caption")
    changed = plan_structural_patch(_tracks(), [change],
                                    component_manifest=manifest,
                                    asset_root=str(tmp_path))
    assert not changed["render_asset_identity_proven"]
    assert not changed["reuse_proven"]
