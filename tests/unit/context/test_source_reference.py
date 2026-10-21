"""Lane A-04: the universal source reference, and what it refuses.

Every test here names the defect the lane names:

* a measurement that says a value but not which bytes it was measured
  from is accepted (it must be refused);
* a reference that cannot be turned into a place in a file is answered
  with a guess (it must be refused, with a named reason);
* a mask whose representation kind nobody can read is in the model (it
  must be refused);
* a singular matrix, a dangling space join and a space with no
  calibration are all in the model (each must be refused);
* the spatial tables are collections nothing writes (they must be
  produced, from a measurement).

Hermetic: every project, file and model here is built under
``tmp_path``. Nothing reads a captain project, a real source or
Resolve.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import evidence_contract
from library.tools import project_model_evidence as evidence
from library.tools import reference_resolver
from library.tools import source_reference
from library.tools import spatial_tables
from library.tools.project_model import validate_project_model
from tests.unit.context import test_project_model_evidence as evidence_tests

DIGEST = "digest-aaa-001"
CLOCK = "clock-digest-aaa-001"
STREAM = "stream-digest-aaa-001"
ASSET = "asset-digest-aaa-001"


# ─── The reference itself ─────────────────────────────────────────


def test_the_reference_vocabulary_names_the_source_and_nothing_else():
    """One vocabulary, and it names the source: an asset, a stream, an
    element, an interval, a region, and one optional level of detail."""
    assert source_reference.REFERENCE_KINDS == (
        "asset", "stream", "element", "interval", "region", "sub_element")
    assert source_reference.names_source(
        source_reference.stream(STREAM)) is True
    assert source_reference.names_source(
        source_reference.interval(STREAM, CLOCK, 0, 10)) is True
    assert source_reference.names_source(
        source_reference.region("space-x", {"box": [0, 0, 1, 1]})) is True
    # An asset names the FILE, and a file is not a place in it.
    assert source_reference.names_source(
        source_reference.asset(ASSET)) is False
    # A sub-element is exactly as good as the parent it hangs from.
    assert source_reference.names_source(
        source_reference.sub_element(
            source_reference.stream(STREAM), "mask", "mask-1")) is True
    assert source_reference.names_source(
        source_reference.sub_element(
            source_reference.asset(ASSET), "mask", "mask-1")) is False


def test_a_malformed_reference_is_refused_by_name():
    """A reference with an unknown kind, a missing id, an element that
    names two indices or an empty interval is refused here rather than
    half-read by whoever follows it."""
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.parse({"kind": "vibes", "asset_id": ASSET})
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.parse({"kind": "stream"})
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.parse({"kind": "element", "stream_id": STREAM,
                                "clock_id": CLOCK,
                                "element": {"kind": "frame"}})
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.element(STREAM, CLOCK, frame_index=1,
                                 sample_index=1)
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.interval(STREAM, CLOCK, 10, 10)
    # The pre-contract spelling is read, never written.
    with pytest.raises(source_reference.SourceReferenceError):
        source_reference.parse({"kind": "source_frame", "stream_id": STREAM,
                                "frame_index": 3})


# ─── The completeness the lane names ──────────────────────────────


def test_a_measurement_with_no_reference_is_refused():
    """A measurement with nothing to point at is a number about
    nothing: the record says a value was measured but not which bytes
    it was measured from."""
    with pytest.raises(evidence_contract.EvidenceContractError,
                       match="must carry at least one reference"):
        evidence_contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="image.sharpness.laplacian_variance",
            value=142.6, method="laplacian-variance-180px-v1",
            provenance_id="prov-x", sample_resolution=[320, 180])
    # An asset reference names the file, not a place in it: it does not
    # make the measurement complete either.
    with pytest.raises(evidence_contract.EvidenceContractError,
                       match="must carry at least one reference"):
        evidence_contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="image.sharpness.laplacian_variance",
            value=142.6, method="laplacian-variance-180px-v1",
            provenance_id="prov-x", sample_resolution=[320, 180],
            references=(source_reference.asset(ASSET),))


def _migrated(tmp_path):
    """The migrated model of a project built under ``tmp_path``.

    The fixture lives with the migration it belongs to; it is called
    here directly so this file does not shadow its name.
    """
    built = evidence_tests.fixture_project.__wrapped__(tmp_path)
    model, _report = evidence.migrate_project_evidence(
        str(built["project"]), memory_root=str(built["memory"]))
    return model


def test_every_measurement_a_migration_writes_carries_a_source_reference(
        tmp_path):
    """The benchmark: 100% of the measurement rows carry at least one
    reference that reaches the source."""
    model = _migrated(tmp_path)
    measurements = [
        observation["payload"]["evidence_contract"]
        for observation in model["observations"]
        if (observation.get("payload", {}).get("evidence_contract", {})
            .get("evidence_type")) == "measurement"]
    assert measurements, "the fixture measures something"
    for contract in measurements:
        assert contract["references"], contract["measurement_type"]
        assert any(source_reference.names_source(reference)
                   for reference in contract["references"]), contract


# ─── The resolver and its four refusals ───────────────────────────


def _media_model(tmp_path: Path, *, frames: int = 4) -> tuple[dict, Path]:
    """A model over one real file, with the measured tables that say
    where its frames are in the file."""
    media = tmp_path / "clip.mov"
    media.write_bytes(b"0123456789" * 64)
    from library.tools import footage_identity
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    frame_table = [
        {"media_type": "video", "pkt_pos": 100 + index * 50,
         "pkt_size": 50, "best_effort_timestamp_time": index / 25.0}
        for index in range(frames)]
    packet_table = [
        {"stream_index": 0, "pos": 100 + index * 50, "size": 50}
        for index in range(frames)]
    model = {
        "id": "project-demo",
        "schema_version": 1,
        "timebase_policy": "integer_ticks",
        "clocks": [{"id": "clock-x", "ticks_per_second": 1000,
                     "frame_rate": {"numerator": 25, "denominator": 1},
                     "domain": {"kind": "source", "asset_id": "asset-x",
                                "stream_id": "stream-x"}}],
        "assets": [{"id": "asset-x", "kind": "video", "uri": str(media),
                    "content_digest": digest, "metadata": {},
                    "provenance": ["prov-1"]}],
        "streams": [{"id": "stream-x", "asset_id": "asset-x",
                     "stream_index": 0, "clock_id": "clock-x",
                     "metadata": {"codec_type": "video"},
                     "source_digest": digest}],
        "coordinate_spaces": [{
            "id": "space-x-pixels", "kind": "source_pixels",
            "units": "pixel", "origin": "top_left",
            "axes": "x_right_y_down", "calibration": None,
            "asset_id": "asset-x"}],
        "space_transforms": [],
        "masks": [],
    }
    model["assets"][0]["metadata"] = {"packet_table": packet_table,
                                      "frame_table": frame_table}
    return model, media


def test_a_valid_reference_resolves_to_the_artefact_and_its_bytes(tmp_path):
    """The resolver answers with the path and the byte range, not with
    a description of where the thing probably is."""
    model, media = _media_model(tmp_path)
    resolved = reference_resolver.resolve(
        model, source_reference.element("stream-x", "clock-x",
                                        frame_index=2))
    assert resolved.path == media
    assert resolved.byte_range == (200, 250)
    assert resolved.kind == "element"

    stream = reference_resolver.resolve(model, source_reference.stream("stream-x"))
    assert stream.path == media
    assert stream.byte_range == (100, 300)

    interval = reference_resolver.resolve(
        model, source_reference.interval("stream-x", "clock-x", 0, 100))
    assert interval.path == media
    assert interval.byte_range == (100, 250)


def test_a_reference_to_something_that_is_not_there_is_refused_missing(tmp_path):
    """The file is gone: the answer is that it is gone, by name."""
    model, media = _media_model(tmp_path)
    media.unlink()
    with pytest.raises(reference_resolver.ReferenceResolutionError) as refused:
        reference_resolver.resolve(model, source_reference.stream("stream-x"))
    assert refused.value.reason == "missing"


def test_a_reference_to_bytes_that_changed_is_refused_stale(tmp_path):
    """The file is there, but it is not the file the model measured."""
    model, media = _media_model(tmp_path)
    media.write_bytes(b"a different file entirely" * 8)
    with pytest.raises(reference_resolver.ReferenceResolutionError) as refused:
        reference_resolver.resolve(model, source_reference.stream("stream-x"))
    assert refused.value.reason == "stale"


def test_a_reference_that_names_two_things_is_refused_ambiguous(tmp_path):
    """Two masks with one name: choosing one would be a guess."""
    model, _media = _media_model(tmp_path)
    model["masks"] = [
        {"id": "mask-1", "entity_id": "person-1",
         "interval": {"clock_id": "clock-x", "start": 0, "end": 1},
         "representation": {"kind": "ellipse",
                            "normalized_bbox": [0, 0, 1, 1]},
         "source_model": "fixture", "provenance_id": "prov-1"},
        {"id": "mask-1", "entity_id": "person-2",
         "interval": {"clock_id": "clock-x", "start": 0, "end": 1},
         "representation": {"kind": "ellipse",
                            "normalized_bbox": [0, 0, 1, 1]},
         "source_model": "fixture", "provenance_id": "prov-1"},
    ]
    with pytest.raises(reference_resolver.ReferenceResolutionError) as refused:
        reference_resolver.resolve(
            model, source_reference.sub_element(
                source_reference.stream("stream-x"), "mask", "mask-1"))
    assert refused.value.reason == "ambiguous"


def test_a_reference_the_source_cannot_express_is_refused_incompatible(tmp_path):
    """The model carries no measured frame table, so it cannot say
    where frame 12 is in the file. It says so instead of guessing."""
    model, _media = _media_model(tmp_path)
    model["assets"][0]["metadata"] = {}
    with pytest.raises(reference_resolver.ReferenceResolutionError) as refused:
        reference_resolver.resolve(
            model, source_reference.element("stream-x", "clock-x",
                                            frame_index=2))
    assert refused.value.reason == "incompatible"


def test_a_reference_to_a_stream_the_model_does_not_carry_is_refused(tmp_path):
    model, _media = _media_model(tmp_path)
    with pytest.raises(reference_resolver.ReferenceResolutionError) as refused:
        reference_resolver.resolve(
            model, source_reference.stream("stream-nobody"))
    assert refused.value.reason == "missing"


# ─── The mask typing ─────────────────────────────────────────────


def test_a_mask_nobody_can_read_is_refused_by_the_model(tmp_path):
    """The representation kind is typed against the registry that
    resolves the bytes, so a kind nobody can read never reaches the
    model."""
    from library.tools import project_model_masks
    model = _migrated(tmp_path)
    model["masks"][0]["representation"] = {"kind": "magic_mask"}
    with pytest.raises(ValueError, match="mask registry"):
        validate_project_model(model)
    assert "magic_mask" not in project_model_masks.MASK_KINDS


# ─── The spatial tables ───────────────────────────────────────────


def test_the_spatial_tables_are_produced_from_a_measurement(tmp_path):
    """The two collections nothing wrote now have a producer: the lane
    that measured the source's frame grid."""
    model = _migrated(tmp_path)
    spaces = {space["id"]: space for space in model["coordinate_spaces"]}
    assert spaces, "the spatial tables are produced"
    pixels = spaces[f"space-{DIGEST}-pixels"]
    assert pixels["kind"] == "source_pixels"
    assert pixels["asset_id"] == ASSET
    normalized = spaces[f"space-{DIGEST}-normalized"]
    assert normalized["kind"] == "normalized_image"
    assert normalized["calibration"]
    transforms = {transform["id"]: transform
                  for transform in model["space_transforms"]}
    join = transforms[f"space-transform-{DIGEST}-pixels-to-normalized"]
    assert join["from_space"] == pixels["id"]
    assert join["to_space"] == normalized["id"]
    # The join is the measured scale, and it is invertible.
    assert join["matrix"][0] == pytest.approx(1 / 1920)
    assert join["matrix"][4] == pytest.approx(1 / 1080)


def test_a_singular_matrix_is_refused():
    """A singular matrix maps a whole plane onto a line: every point
    asked about lands on the same place."""
    model = _spatial_model()
    model["space_transforms"][0]["matrix"] = [1.0] * 9
    with pytest.raises(ValueError, match="invertible"):
        validate_project_model(model)


def test_a_dangling_space_join_is_refused():
    """A transform whose endpoint is not declared is a pointer into
    nothing."""
    model = _spatial_model()
    model["space_transforms"][0]["to_space"] = "space-nobody"
    with pytest.raises(ValueError, match="unknown coordinate space"):
        validate_project_model(model)


def test_a_space_with_no_calibration_is_refused():
    """The source's own pixel grid describes itself. Every other space
    is derived, and a derived space that does not say how it was
    established is a number with nothing behind it."""
    model = _spatial_model()
    model["coordinate_spaces"][1]["calibration"] = None
    with pytest.raises(ValueError, match="requires a calibration"):
        validate_project_model(model)


def _spatial_model() -> dict:
    """A model with one source's two grids and the join between them."""
    spaces, transforms = spatial_tables.spatial_tables("digest-x", 1920, 1080)
    for space in spaces:
        space["asset_id"] = "asset-x"
    transforms[0]["provenance_id"] = "prov-1"
    return {
        "id": "project-demo", "schema_version": 1,
        "timebase_policy": "integer_ticks",
        "clocks": [], "assets": [], "streams": [], "entities": [],
        "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": [], "edits": [], "effects": [], "renders": [],
        "frame_maps": [], "nle_bindings": [], "receipts": [],
        "provenance": [], "coordinate_spaces": spaces,
        "space_transforms": transforms,
    }


# ─── The vocabulary stays portable ────────────────────────────────


def test_the_reference_vocabulary_names_no_nle_and_no_model_provider():
    """A reference stays meaningful when the analysis that made it came
    from one tool and the edit that uses it is executed by another."""
    schema = (Path(__file__).resolve().parents[3]
              / "native/ren-protocol/schemas/project-source-reference.schema.json")
    text = schema.read_text(encoding="utf-8").lower()
    # The transcription vendors are named by another contract, which
    # owns their spelling; this one owns the reference vocabulary, so
    # it checks the applications and the analysis providers.
    for token in ("resolve", "gemini", "gemma", "sam2", "whisper",
                  "ffmpeg", "davinci", "premiere", "final cut"):
        assert token not in text, token
