"""Python boundary for Ren's versioned canonical project model contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from library.tools import protocol_conformance

SCHEMA_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_MODEL_SCHEMA = REPOSITORY_ROOT / "native/ren-protocol/schemas/project-model.schema.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/project_model_e2e"


def validate_project_model(model: dict[str, Any]) -> None:
    """Validate the Rust-generated schema and Python semantic invariants."""
    version = model.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported project model schema_version {version!r}; expected {SCHEMA_VERSION}")
    protocol_conformance.validate(model, "project-model")
    from library.tools.project_model_contract import validate_typed_confidence
    for clock in model["clocks"]:
        if clock["ticks_per_second"] <= 0 or (clock["frame_rate"] and clock["frame_rate"]["denominator"] <= 0):
            raise ValueError("clock rates must have positive denominators and ticks_per_second")
    confidence_values = [item["confidence"] for key in ("observations", "identity_links", "relations") for item in model[key]]
    confidence_values.extend(sample["confidence"] for track in model["tracks"] for sample in track["samples"])
    if any(value is not None and not 0 <= value <= 1 for value in confidence_values):
        raise ValueError("confidence must be between 0 and 1")
    observation_ids = {item["id"] for item in model["observations"]}
    entity_ids = {item["id"] for item in model["entities"]}
    clock_ids = {clock["id"] for clock in model["clocks"]}
    stream_ids = {stream["id"] for stream in model.get("streams", [])}
    policy_rules: dict[str, dict[str, str]] = {}
    for policy in model.get("evaluation_policies", []):
        if (policy.get("format") != "evaluation_policy/1"
                or not policy.get("id") or type(policy.get("version")) is not int
                or policy["version"] < 1 or policy.get("project_id") != model["id"]):
            raise ValueError("evaluation policy needs a supported format, positive version, id and matching project")
        policy_ref = f"{policy['id']}@v{policy['version']}"
        if policy_ref in policy_rules:
            raise ValueError(f"duplicate evaluation policy {policy_ref}")
        seen_rules: dict[str, str] = {}
        for kind in ("requirements", "preferences"):
            expected_kind = "requirement" if kind == "requirements" else "preference"
            for rule in policy.get(kind, []):
                if (rule.get("kind") != expected_kind or not rule.get("id")
                        or not rule.get("measurement_type") or not rule.get("origin")
                        or not rule.get("origin_ref")
                        or rule.get("operator") not in ("eq", "ne", "lt", "lte", "gt", "gte")
                        or rule["id"] in seen_rules):
                    raise ValueError(f"invalid or duplicate {expected_kind} rule in {policy_ref}")
                seen_rules[rule["id"]] = rule["measurement_type"]
        policy_rules[policy_ref] = seen_rules
    for item in model.get("observations", []):
        detail = item.get("confidence_detail")
        if detail is not None:
            validate_typed_confidence(detail["value"], detail["kind"], detail["producer"],
                                      detail.get("calibration_version"))
        certainty = item.get("certainty")
        if certainty not in (None, "observed", "measured", "inferred", "interpreted"):
            raise ValueError("observation certainty must use the canonical epistemic vocabulary")
        if clock_ids and item["interval"]["clock_id"] not in clock_ids:
            raise ValueError("observation references unknown clock")
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            if evidence.get("evidence_type") == "evaluation":
                if not evidence.get("policy_id"):
                    raise ValueError("evaluation must name its versioned policy")
                if not evidence.get("basis_measurement_ids"):
                    raise ValueError("evaluation must cite its basis measurements")
                if not evidence.get("basis_rule_ids"):
                    raise ValueError("evaluation must cite its policy rules")
                rules = policy_rules.get(evidence["policy_id"])
                if rules is None:
                    raise ValueError(f"evaluation references missing policy {evidence['policy_id']}")
                rule_ids = set(evidence["basis_rule_ids"])
                if not rule_ids <= rules.keys():
                    raise ValueError("evaluation references unknown policy rule")
                required_measurements = {rules[rule_id] for rule_id in rule_ids}
                for measurement_id in evidence["basis_measurement_ids"]:
                    measurement = next(
                        (row.get("evidence") for row in model["observations"]
                         if row["id"] == measurement_id), None)
                    if (not isinstance(measurement, dict)
                            or measurement.get("evidence_type") != "measurement"
                            or measurement.get("measurement_type") not in required_measurements):
                        raise ValueError(
                            "evaluation cites a measurement unrelated to its policy rules")
            if not set(evidence.get("basis_measurement_ids", [])) <= observation_ids:
                raise ValueError("evidence references unknown basis observations")
            for reference in evidence.get("references", []):
                kind = reference.get("kind")
                if kind in ("source_sample", "source_frame"):
                    if stream_ids and reference.get("stream_id") not in stream_ids:
                        raise ValueError("evidence references unknown source stream")
                elif kind == "observation":
                    if observation_ids and reference.get("observation_id") not in observation_ids:
                        raise ValueError("evidence references unknown input observation")
                else:
                    raise ValueError(f"evidence reference has unknown kind {kind!r}")
    for item in model.get("identity_links", []):
        detail = item.get("confidence_detail")
        if detail is not None:
            validate_typed_confidence(detail["value"], detail["kind"], detail["producer"],
                                      detail.get("calibration_version"))
    for item in model.get("events", []):
        detail = item.get("confidence")
        if detail is not None:
            validate_typed_confidence(detail["value"], detail["kind"], detail["producer"],
                                      detail.get("calibration_version"))
    for link in model.get("identity_links", []):
        support = link.get("supporting_observation_ids", [])
        contradicting = link.get("contradicting_observation_ids", [])
        if link.get("verification_status") == "verified" and not (support or link.get("observation_id")):
            raise ValueError("verified identity links require supporting observations")
        if support and not set(support) <= observation_ids:
            raise ValueError("identity link references unknown supporting observations")
        if contradicting and not set(contradicting) <= observation_ids:
            raise ValueError("identity link references unknown contradicting observations")
        uncertainty = link.get("uncertainty")
        if uncertainty is not None and not 0 <= uncertainty <= 1:
            raise ValueError("identity link uncertainty must be between 0 and 1")
        scope = link.get("scope")
        if scope is not None and scope["start"] >= scope["end"]:
            raise ValueError("identity link scope must be a non-empty half-open interval")
    for relation in model.get("relations", []):
        if relation.get("status") == "verified" and not relation.get("evidence_ids"):
            raise ValueError("verified relations require evidence")
        if not set(relation.get("evidence_ids", [])) <= observation_ids:
            raise ValueError("relation references unknown observations")
        if relation.get("certainty") not in (None, "observed", "measured", "inferred", "interpreted"):
            raise ValueError("relation certainty must use the canonical epistemic vocabulary")
    for event in model.get("events", []):
        if event["interval"]["start"] >= event["interval"]["end"]:
            raise ValueError("event intervals must be non-empty and half-open")
        if event.get("status") == "verified" and not event.get("evidence_ids"):
            raise ValueError("verified events require evidence")
        if not set(event.get("evidence_ids", [])) <= observation_ids:
            raise ValueError("event references unknown observations")
        if not set(event.get("participant_ids", [])) <= entity_ids:
            raise ValueError("event references unknown participants")
    for interpretation in model.get("interpretations", []):
        if interpretation["certainty"] != "interpreted":
            raise ValueError("interpretation certainty must be interpreted")
        uncertainty = interpretation.get("uncertainty")
        if uncertainty is not None and not 0 <= uncertainty <= 1:
            raise ValueError("interpretation uncertainty must be between 0 and 1")
    for signal in model.get("signals", []):
        if signal["interval"]["start"] >= signal["interval"]["end"]:
            raise ValueError("signal intervals must be non-empty and half-open (start < end)")
        if clock_ids and (signal["sample_clock_id"] not in clock_ids
                          or signal["interval"]["clock_id"] not in clock_ids):
            raise ValueError("signal references unknown clock")
        if signal.get("processing_status") == "unavailable" and signal.get("samples"):
            raise ValueError("unavailable signal cannot carry samples")
    coordinate_ids = {space["id"] for space in model.get("coordinate_spaces", [])}
    space_transform_ids = {transform["id"] for transform in model.get("space_transforms", [])}
    for track in model.get("tracks", []):
        if clock_ids and track["clock_id"] not in clock_ids:
            raise ValueError("track references unknown clock")
        ticks = [sample["tick"] for sample in track["samples"]]
        if any(first >= second for first, second in pairwise(ticks)):
            raise ValueError("track samples must be strictly ordered; discontinuity must be explicit")
        for sample in track["samples"]:
            detail = sample.get("confidence_detail")
            if detail is not None:
                validate_typed_confidence(detail["value"], detail["kind"], detail["producer"],
                                          detail.get("calibration_version"))
            interval = sample.get("interval")
            if interval is not None and (interval["start"] >= interval["end"]
                                         or interval["clock_id"] != track["clock_id"]):
                raise ValueError("track sample interval must be valid on the track clock")
            if not set(sample.get("evidence_ids", [])) <= observation_ids:
                raise ValueError("track sample references unknown observation")
            if sample.get("space_transform_id") is not None \
                    and sample["space_transform_id"] not in space_transform_ids:
                raise ValueError("track sample references unknown space transform")
            if sample.get("coordinate_space_id") is not None \
                    and sample["coordinate_space_id"] not in coordinate_ids:
                raise ValueError("track sample references unknown coordinate space")
    for result in model.get("query_results", []):
        if result["status"] == "confidently_absent" and not result["completeness_basis"]:
            raise ValueError("confidently absent requires a completeness basis")
        if result["status"] == "confidently_absent" and any(
            basis["coverage"] != "complete"
            or basis["freshness"] != "fresh"
            or not basis["producer_version"]
            for basis in result["completeness_basis"]
        ):
            raise ValueError("confidently absent requires fresh complete versioned coverage")
        if result["status"] == "not_yet_analysed" and not result["required_missing_lanes"]:
            raise ValueError("not_yet_analysed requires missing lanes")
    for collection in ("observations", "masks", "relations", "effects"):
        for item in model[collection]:
            interval = item["interval"]
            if interval["start"] >= interval["end"]:
                raise ValueError(f"{collection} intervals must be non-empty and half-open (start < end)")
    for use in model["clip_uses"]:
        for interval in (use["source_interval"], use["timeline_interval"]):
            if interval["start"] >= interval["end"]:
                raise ValueError("clip use intervals must be non-empty and half-open (start < end)")
    for frame_map in model["frame_maps"]:
        for segment in frame_map["segments"]:
            if segment["from"]["start"] >= segment["from"]["end"] or segment["to"]["start"] >= segment["to"]["end"]:
                raise ValueError("frame map intervals must be non-empty and half-open (start < end)")
    for stream in model.get("streams", []):
        if clock_ids and stream["clock_id"] not in clock_ids:
            raise ValueError("source stream references unknown clock")
        if stream.get("source_digest") == "":
            raise ValueError("source stream digest must not be empty")
    for transform in model.get("time_transforms", []):
        if clock_ids and not {transform["from_clock"], transform["to_clock"]} <= clock_ids:
            raise ValueError("time transform references unknown clock")
        for segment in transform["segments"]:
            if segment["slope"]["denominator"] <= 0:
                raise ValueError("time transform slope denominator must be positive")
            if segment["from"]["start"] >= segment["from"]["end"] or segment["to"]["start"] >= segment["to"]["end"]:
                raise ValueError("time transform intervals must be non-empty and half-open")
            if segment["from"]["clock_id"] != transform["from_clock"] \
                    or segment["to"]["clock_id"] != transform["to_clock"]:
                raise ValueError("time transform segment clock mismatch")
    for transform in model.get("space_transforms", []):
        if coordinate_ids and not {transform["from_space"], transform["to_space"]} <= coordinate_ids:
            raise ValueError("space transform references unknown coordinate space")
        matrix = transform.get("matrix", [])
        if len(matrix) not in (9, 16) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in matrix):
            raise ValueError("space transform matrix must be a finite 3x3 or 4x4")


def read_project_model(path: Path) -> dict[str, Any]:
    """Read a model document, refusing unknown versions and schema drift."""
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read project model {path}: {exc}") from exc
    validate_project_model(model)
    return model


def write_project_model(path: Path, model: dict[str, Any]) -> None:
    """Validate then write the shared Rust/Python JSON representation."""
    validate_project_model(model)
    path.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_project_model(path: str | Path) -> dict[str, Any]:
    """Load JSON and check it directly against the generated JSON Schema."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        schema = json.loads(PROJECT_MODEL_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read project model {source}: {exc}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(document),
                    key=lambda error: list(map(str, error.absolute_path)))
    if errors:
        rendered = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors)
        raise ValueError(rendered)
    return document


def validate_fixture(root: str | Path = FIXTURE_ROOT) -> dict[str, Any]:
    """Validate schema, referenced media, and fixture-specific assertions."""
    fixture = Path(root)
    model = load_project_model(fixture / "expected-project-model.json")
    for asset in model["assets"]:
        if asset["kind"] == "render":
            continue
        media = fixture / asset["uri"]
        if not media.is_file() or media.stat().st_size == 0:
            raise ValueError(f"fixture media is missing or empty: {media}")
        digest = "sha256:" + hashlib.sha256(media.read_bytes()).hexdigest()
        if asset["content_digest"] != digest:
            raise ValueError(f"fixture media digest mismatch: {media}")
    validate_project_model(model)
    people = {entity["id"] for entity in model["entities"]
              if entity["kind"] == "person"}
    for person_id in people:
        linked_observations = {link["observation_id"]
                               for link in model["identity_links"]
                               if link["entity_id"] == person_id}
        modalities = {observation["modality"] for observation in model["observations"]
                      if observation["id"] in linked_observations}
        if not {"face", "speech"} <= modalities:
            raise ValueError(f"{person_id} must link to face and speech observations")
        if not any(mask["entity_id"] == person_id for mask in model["masks"]):
            raise ValueError(f"{person_id} must have an associated mask")
    roles = {use["role"] for use in model["clip_uses"]}
    if not {"a_roll", "b_roll"} <= roles:
        raise ValueError("fixture needs A-roll and B-roll uses")
    trim = next((edit for edit in model["edits"]
                 if edit["operation"] == "clip.trim"), None)
    if not trim or trim["before"]["duration"] <= trim["after"]["duration"]:
        raise ValueError("fixture needs an edit that changes clip length")
    render = model["renders"][0]
    frame_map = next(item for item in model["frame_maps"]
                     if item["id"] == render["frame_map_id"])
    if frame_map["segments"][-1]["to"]["end"] != render["settings"]["expected_frames"]:
        raise ValueError("render frame map must end at the expected frame count")
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "validate-fixture"))
    parser.add_argument("path", nargs="?", help="Project Model JSON for validate")
    args = parser.parse_args()
    try:
        if args.command == "validate-fixture":
            model = validate_fixture()
            print(f"valid Project Model fixture: {model['id']}")
        else:
            if not args.path:
                parser.error("validate requires a JSON path")
            model = load_project_model(args.path)
            print(f"valid Project Model: {model['id']}")
    except ValueError as exc:
        parser.exit(1, f"invalid Project Model: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
