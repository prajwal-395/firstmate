"""Python's conformance to the generated protocol schemas.

The Rust types are the source of truth; the schemas are generated from
them. These tests hold the Python boundaries to that contract: a
payload that does not conform is a finding, not something to paper
over.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools import edit_patch, operations, protocol_conformance
from library.tools.resolved import jobs

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "native" / "ren-protocol" / "schemas"
FIXTURES = REPO_ROOT / "native" / "ren-protocol" / "fixtures"


def _set_mode(monkeypatch, value):
    monkeypatch.setenv("REN_PROTOCOL_MODE", value)


def _valid_patch_dict():
    return {
        "id": "ren-test-0000000000000001",
        "project": "Ren Qualification",
        "timeline": "Timeline 1",
        "base_generation": 7,
        "capability": "reel.touchup",
        "affected_spans": [[4312, 4342]],
        "conflict_domains": ["markers"],
        "operations": [
            {"op": "marker.add", "frame": 4312, "color": "Blue", "name": "anchor"}
        ],
        "preconditions": [],
        "postconditions": [],
        "base_git_sha": "",
    }


# ── The helper's modes ─────────────────────────────────────────────


def test_a_conforming_payload_passes_in_any_mode(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    protocol_conformance.validate(_valid_patch_dict(), "edit-patch")
    _set_mode(monkeypatch, "log")
    protocol_conformance.validate(_valid_patch_dict(), "edit-patch")
    _set_mode(monkeypatch, "off")
    protocol_conformance.validate(_valid_patch_dict(), "edit-patch")


def test_refuse_mode_raises_on_a_non_conforming_payload(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    payload = _valid_patch_dict()
    del payload["id"]
    with pytest.raises(protocol_conformance.ProtocolConformanceError) as excinfo:
        protocol_conformance.validate(payload, "edit-patch")
    assert "edit-patch" in str(excinfo.value)
    assert "id" in str(excinfo.value)


def test_refuse_mode_raises_on_a_wrong_type(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    payload = _valid_patch_dict()
    payload["base_generation"] = "seven"
    with pytest.raises(protocol_conformance.ProtocolConformanceError):
        protocol_conformance.validate(payload, "edit-patch")


def test_log_mode_prints_and_continues(monkeypatch, capsys):
    _set_mode(monkeypatch, "log")
    payload = _valid_patch_dict()
    del payload["id"]
    protocol_conformance.validate(payload, "edit-patch")
    assert "does not conform" in capsys.readouterr().err


def test_off_mode_does_nothing(monkeypatch):
    _set_mode(monkeypatch, "off")
    payload = _valid_patch_dict()
    del payload["id"]
    protocol_conformance.validate(payload, "edit-patch")


def test_a_missing_schema_is_a_finding_not_a_skip(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    with pytest.raises(protocol_conformance.ProtocolConformanceError) as excinfo:
        protocol_conformance.validate({}, "no-such-schema")
    assert "no-such-schema" in str(excinfo.value)


def test_the_mode_defaults_to_refuse_under_pytest(monkeypatch):
    monkeypatch.delenv("REN_PROTOCOL_MODE", raising=False)
    assert protocol_conformance.mode() == "refuse"


# ── The boundaries ────────────────────────────────────────────────


def _non_conforming_patch_dict():
    """A patch that constructs (all required fields present) but does
    not conform to the schema: the operation is not a known EditOp."""
    return {
        "id": "ren-test-0000000000000001",
        "project": "Ren Qualification",
        "timeline": "Timeline 1",
        "base_generation": 7,
        "capability": "reel.touchup",
        "affected_spans": [[4312, 4342]],
        "conflict_domains": ["markers"],
        "operations": [{"op": "no.such_op", "frame": 4312}],
        "preconditions": [],
        "postconditions": [],
        "base_git_sha": "",
    }


def test_from_dict_does_not_validate_in_process(monkeypatch):
    """`from_dict` is in-process construction, not an external boundary:
    compose/carry and test setup build patches through it, and
    validating there costs the scheduler's critical path."""
    _set_mode(monkeypatch, "off")
    # No refusal: the patch is constructed, not read from a socket.
    edit_patch.EditPatch.from_dict(_non_conforming_patch_dict())


def test_the_broker_refuses_a_non_conforming_patch_in_refuse_mode(
        monkeypatch):
    """The external boundary: a patch arriving from the socket is
    validated before Resolve is touched. Refuses in tests/dev."""
    _set_mode(monkeypatch, "refuse")
    payload = _non_conforming_patch_dict()
    job = {"id": "j1", "kind": "timeline.apply_patch",
           "params": {"patch": payload}, "project": "P", "timeline": "T"}

    class ExplodesIfTouched:
        def __getattr__(self, name):
            raise AssertionError(f"Resolve was touched: {name}")

    with pytest.raises(protocol_conformance.ProtocolConformanceError):
        jobs.run(job, ExplodesIfTouched())


def test_the_broker_logs_a_non_conforming_patch_in_production(
        monkeypatch):
    """In production the same non-conforming patch is a log line, not a
    refusal: a hard refusal on the live edit path needs live evidence
    first."""
    _set_mode(monkeypatch, "log")
    payload = _non_conforming_patch_dict()
    job = {"id": "j1", "kind": "timeline.apply_patch",
           "params": {"patch": payload}, "project": "P", "timeline": "T"}

    class ExplodesIfTouched:
        def __getattr__(self, name):
            raise AssertionError(f"Resolve was touched: {name}")

    with pytest.raises(AssertionError, match="Resolve was touched"):
        jobs.run(job, ExplodesIfTouched())


def test_to_dict_refuses_a_non_conforming_patch_under_pytest(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    patch = edit_patch.EditPatch(
        id="ren-x", project="p", timeline="t", base_generation=1,
        capability="reel.touchup", affected_spans=((0, 10),),
        conflict_domains=("markers",),
        operations=({"op": "marker.add", "frame": 1, "color": "Blue",
                     "name": "n"},))
    # A patch whose to_dict output cannot conform is a finding; the
    # valid one passes.
    protocol_conformance.validate(patch.to_dict(), "edit-patch")


def test_the_job_shape_schema_refuses_a_non_conforming_shape(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    # A shape the schema does not accept: priority outside the
    # enumeration. prepare builds the shape itself, so the schema is the
    # guard against the two drifting apart.
    shape = {
        "mode": "exclusive",
        "priority": "urgent",
        "executed": True,
        "project": "P",
        "timeline": "T",
        "locality": "timeline",
        "coalesce_key": None,
        "qualification": False,
    }
    with pytest.raises(protocol_conformance.ProtocolConformanceError):
        protocol_conformance.validate(shape, "job-shape")


def test_prepare_validates_a_real_shape(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    shape = jobs.prepare("timeline.snapshot",
                         {"project": "Ren Qualification",
                          "timeline": "Timeline 1"})
    assert shape["mode"] == "exclusive"


def test_emit_skill_validates_every_capability(monkeypatch):
    _set_mode(monkeypatch, "refuse")
    skill = operations.emit_skill()
    assert "pipeline_operations" in skill


# ── The fixture corpus and the stale check ─────────────────────────


def test_every_fixture_validates_against_the_generated_schemas():
    """The Python conformance half of the round-trip: each captured
    real payload validates against the schema generated from the Rust
    type."""
    import jsonschema

    for path in sorted(FIXTURES.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        for index, row in enumerate(rows):
            if path.name == "job-submission.json":
                schema_name, target = "job-shape", row["shape"]
            else:
                schema_name = path.stem
                target = row
            schema = json.loads(
                (SCHEMAS_DIR / f"{schema_name}.schema.json").read_text(
                    encoding="utf-8"))
            jsonschema.Draft202012Validator(schema).validate(target)


def _cargo() -> str:
    """The cargo binary: PATH first, then the user-level install."""
    from shutil import which
    found = which("cargo")
    if found:
        return found
    candidate = Path.home() / ".cargo" / "bin" / "cargo"
    if candidate.is_file():
        return str(candidate)
    pytest.skip("cargo is not installed")


def test_the_committed_schemas_are_current():
    """A type change without its schema landing beside it is a finding."""
    result = subprocess.run(
        [_cargo(), "run", "--bin", "emit-schemas", "--", "--check"],
        cwd=REPO_ROOT / "native", capture_output=True, encoding="utf-8",
        timeout=300, check=False)
    assert result.returncode == 0, result.stderr + result.stdout


def test_the_schemas_directory_is_committed():
    assert SCHEMAS_DIR.is_dir()
    assert len(list(SCHEMAS_DIR.glob("*.schema.json"))) >= 30


#: A conforming example payload for every algebra op, keyed by op id.
#: Each carries the op's required fields with the types the Rust EditOp
#: variant declares. A new op added to `edit_algebra.ALGEBRA` without a
#: matching Rust variant fails the schema validation below.
_EXAMPLE_OPS = {
    "marker.add": {"frame": 10, "color": "Blue", "name": "c"},
    "marker.delete": {"frame": 10},
    "clip_marker.add": {"unique_id": "clip_1", "frame": 10,
                        "color": "Blue", "name": "c"},
    "clip_marker.delete": {"unique_id": "clip_1", "frame": 10},
    "clip.delete": {"unique_id": "clip_1"},
    "clip.insert": {"unique_id": "clip_1", "track_type": "video",
                    "track_index": 1, "record_in": 0, "record_out": 48,
                    "media_pool_item": "pool_1"},
    "clip.split": {"unique_id": "clip_1", "at_frame": 24},
    "clip.join": {"unique_id": "clip_1", "other_unique_id": "clip_2"},
    "clip.trim": {"unique_id": "clip_1", "in_frame": 0, "out_frame": 48},
    "clip.ripple": {"unique_id": "clip_1", "delta_frames": -5},
    "clip.roll": {"unique_id": "clip_1", "delta_frames": 5},
    "clip.slip": {"unique_id": "clip_1", "delta_frames": 5},
    "clip.slide": {"unique_id": "clip_1", "to_frame": 100},
    "clip.move": {"unique_id": "clip_1", "to_frame": 100},
    "clip.replace": {"unique_id": "clip_1", "source_file": "a.mov"},
    "clip.set_enabled": {"unique_id": "clip_1", "enabled": True},
    "clip.link": {"unique_id": "clip_1",
                  "with_unique_ids": ["clip_2"]},
    "clip.unlink": {"unique_id": "clip_1"},
    "clip.smart_reframe": {"unique_id": "clip_1",
                           "keys": ["Pan", "Tilt", "ZoomX", "ZoomY"],
                           "before": {"Pan": 0.0, "Tilt": 0.0,
                                      "ZoomX": 1.0, "ZoomY": 1.0}},
    "clip.set_property": {"unique_id": "clip_1", "key": "Pan",
                          "value": 0.5},
    "retime": {"unique_id": "clip_1", "percent": 1.5},
    "retime.segments": {"unique_id": "clip_1", "segments": [
        {"record_frame": 0, "percent": 1.0}]},
    "picture.keyframe": {"unique_id": "clip_1", "param": "Pan",
                         "keyframes": [{"frame": 0, "value": 0.5}]},
    "voice_isolation": {"track_index": 1},
    "audio.gain": {"unique_id": "clip_1", "db": -3.0},
    "audio.pan": {"unique_id": "clip_1", "pan": 0.5},
    "audio.fade": {"unique_id": "clip_1", "fade_in": {"duration": 5},
                   "fade_out": {"duration": 5}},
    "audio.automation": {"unique_id": "clip_1", "param": "volume",
                         "keyframes": [{"frame": 0, "value": 0.5}]},
    "clip_lut": {"unique_id": "clip_1", "lut": "rec709"},
    "grade": {"unique_id": "clip_1", "grade": "free_1.13"},
    "grade.node_add": {"unique_id": "clip_1", "node_index": 1},
    "grade.node_remove": {"unique_id": "clip_1", "node_index": 1},
    "grade.node_reorder": {"unique_id": "clip_1", "from_index": 1,
                          "to_index": 2},
    "grade.node_set": {"unique_id": "clip_1", "node_index": 1,
                       "params": {"slope_r": 1.0, "slope_g": 1.0,
                                  "slope_b": 1.0, "offset_r": 0.0,
                                  "offset_g": 0.0, "offset_b": 0.0,
                                  "power_r": 1.0, "power_g": 1.0,
                                  "power_b": 1.0, "saturation": 1.0}},
    "color.window.create": {"unique_id": "clip_1", "node_index": 1,
                            "shape": "circle",
                            "geometry": {"center": [0.5, 0.5]}},
    "color.window.delete": {"unique_id": "clip_1", "node_index": 1},
    "color.qualifier.sample": {"unique_id": "clip_1", "node_index": 1,
                               "point": [0.5, 0.5]},
    "color.qualifier.adjust": {"unique_id": "clip_1", "node_index": 1,
                               "params": {"gain": 1.0}},
    "mask.create": {"unique_id": "clip_1", "matte": "matte_1"},
    "mask.edit": {"unique_id": "clip_1", "matte": "matte_1",
                  "geometry": {"center": [0.5, 0.5]}},
    "mask.delete": {"unique_id": "clip_1", "matte": "matte_1"},
    "mask.track": {"unique_id": "clip_1", "matte": "matte_1",
                   "track": {"keyframes": [{"frame": 0,
                                            "Width": 0.5}]}},
    "multicam.angle": {"unique_id": "clip_1", "angle_index": 0},
    "compound.create": {"unique_id": "clip_1",
                        "child_unique_ids": ["clip_2"]},
    "compound.flatten": {"unique_id": "clip_1"},
    "track.add": {"track_type": "video", "name": "V2"},
    "track.delete": {"track_type": "video", "track_index": 2},
    "track.rename": {"track_type": "video", "track_index": 1,
                     "name": "V1"},
    "track.reorder": {"track_type": "video", "from_index": 1,
                      "to_index": 2, "name": "V1"},
    "overlay.add": {"row": "V2", "record_frame": 100},
    "overlay.remove": {"unique_id": "clip_1"},
    "overlay.swap_pixels": {"unique_id": "clip_1", "file": "a.png"},
    "overlay.resize": {"unique_id": "clip_1", "width": 100,
                       "height": 100},
    "overlay.entry_motion": {"unique_id": "clip_1", "frames": 10},
    "transform_override": {"unique_id": "clip_1", "key": "Pan",
                           "value": 0.5},
    "span_retime": {"unique_id": "clip_1", "percent": 1.5},
    "drop_fragment": {"unique_id": "clip_1"},
    "caption_fix": {"unique_id": "clip_1", "text": "hello"},
    "redraw_closer": {"unique_id": "clip_1", "start": 10.0},
    "angle_plan": {"plan": {"angle": 0}},
    "plan_change": {"node": "step_1_04", "note": "replan"},
    "transition": {"carrier": {"clip": "clip_1"},
                   "transition": {"type": "dissolve"}},
    "shot_selection": {"plan": {"shots": []}},
    "speech_selection": {"plan": {"passages": []}},
    "story_pacing": {"plan": {"blocks": []}},
    "music_selection": {"plan": {"track": "t1"}},
    "audio_mix": {"plan": {"bed": []}},
    "look": {"plan": {"grade": "free_1.13"}},
    "visual_effect": {"plan": {"effect": "drift"}},
    "sound_effect": {"plan": {"sfx": "whoosh"}},
    "subject_effect": {"plan": {"effect": "punch_in"}},
    "motion_graphic": {"plan": {"graphic": "lower_third"}},
    "brand_asset": {"plan": {"asset": "logo"}},
    "end_card": {"plan": {"card": "cta"}},
}


def test_every_algebra_op_has_a_matching_edit_op_variant():
    """Every op in `edit_algebra.ALGEBRA` must have a matching Rust
    `EditOp` variant and a conforming example payload. A new op added
    to the algebra without its ren-protocol type update is caught here,
    in tests, rather than live on the broker path."""
    from library.tools import edit_algebra

    algebra_ids = set(edit_algebra.ALGEBRA)
    example_ids = set(_EXAMPLE_OPS)
    assert algebra_ids == example_ids, (
        f"the example payloads and the algebra disagree: "
        f"only in algebra: {sorted(algebra_ids - example_ids)}, "
        f"only in examples: {sorted(example_ids - algebra_ids)}")

    import jsonschema
    schema = json.loads(
        (SCHEMAS_DIR / "edit-op.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for op_id in sorted(algebra_ids):
        payload = dict(_EXAMPLE_OPS[op_id])
        payload["op"] = op_id
        errors = list(validator.iter_errors(payload))
        assert not errors, (
            f"{op_id} has no matching EditOp variant or its example "
            f"payload does not conform: "
            f"{'; '.join(e.message for e in errors)}")


def test_a_built_tree_holds_the_schemas_and_prepare_accepts_a_job(
    tmp_path, monkeypatch):
    """An installed engine validates broker jobs against the packaged
    schemas: `package_engine` copies them, and a built tree's
    `jobs.prepare` accepts a normal job.

    The `ren-protocol` crate ships whole - the `ren_native` source
    fingerprint is computed over its sources
    (`library/tools/ren_native_source.py`), so an installed tree carries
    them - while the build artifacts (`target/`) stay out.
    """
    from ren.package_engine import build_engine_tree

    dest = build_engine_tree(REPO_ROOT, tmp_path / "engine",
                             channel="test", sha="abc123",
                             built_at="2026-10-04")
    schemas = dest / "native" / "ren-protocol" / "schemas"
    assert (schemas / "edit-patch.schema.json").is_file()
    assert (schemas / "job-shape.schema.json").is_file()
    # The crate ships for the fingerprint; the build artifacts do not.
    assert (dest / "native" / "ren-protocol" / "src").is_dir()
    assert not (dest / "native" / "target").exists()

    # A built tree's prepare, pointed at the packaged schemas, accepts a
    # normal job in refuse mode.
    monkeypatch.setattr(protocol_conformance, "SCHEMAS_DIR", schemas)
    monkeypatch.setattr(protocol_conformance, "_VALIDATORS", {})
    monkeypatch.setenv("REN_PROTOCOL_MODE", "refuse")
    shape = jobs.prepare("timeline.snapshot",
                         {"project": "Ren Qualification",
                          "timeline": "Timeline 1"})
    assert shape["mode"] == "exclusive"


def test_a_missing_checker_is_logged_not_refused_in_production(
    tmp_path, monkeypatch, capsys):
    """Missing tooling is not a non-conforming payload: in production a
    missing schemas dir is a log line and validation is skipped."""
    monkeypatch.setenv("REN_PROTOCOL_MODE", "log")
    monkeypatch.setattr(protocol_conformance, "SCHEMAS_DIR",
                        tmp_path / "no-such-dir")
    monkeypatch.setattr(protocol_conformance, "_VALIDATORS", {})
    # A normal job is accepted despite the checker being absent.
    shape = jobs.prepare("timeline.snapshot",
                         {"project": "Ren Qualification",
                          "timeline": "Timeline 1"})
    assert shape["mode"] == "exclusive"
    assert "unavailable" in capsys.readouterr().err


def test_a_missing_checker_is_a_finding_under_pytest(
    tmp_path, monkeypatch):
    """Under pytest the same missing checker is a finding."""
    monkeypatch.setenv("REN_PROTOCOL_MODE", "refuse")
    monkeypatch.setattr(protocol_conformance, "SCHEMAS_DIR",
                        tmp_path / "no-such-dir")
    monkeypatch.setattr(protocol_conformance, "_VALIDATORS", {})
    with pytest.raises(protocol_conformance.ProtocolConformanceError):
        jobs.prepare("timeline.snapshot",
                     {"project": "Ren Qualification",
                      "timeline": "Timeline 1"})
