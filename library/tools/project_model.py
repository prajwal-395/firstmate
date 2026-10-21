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

from library.tools import evidence_contract, protocol_conformance

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
    # Timeline state is a projection of the same revisioned edit history,
    # with explicit references so readers never infer order from legacy rows.
    clip_use_ids = {row["id"] for row in model["clip_uses"]}
    edit_ids = {row["id"] for row in model["edits"]}
    timeline_ids: set[str] = set()
    for state in model.get("timeline_states", []):
        if not state["id"] or not state["timeline_id"]:
            raise ValueError("timeline state requires stable ids")
        if state["timeline_id"] in timeline_ids:
            raise ValueError("timeline state must have one current row per timeline")
        timeline_ids.add(state["timeline_id"])
        if not set(state["clip_use_ids"]) <= clip_use_ids:
            raise ValueError("timeline state references unknown clip use")
        if not set(state["edit_operation_ids"]) <= edit_ids:
            raise ValueError("timeline state references unknown edit operation")
    from library.tools.project_model_contract import validate_typed_confidence
    clock_rates: dict[str, int] = {}
    for clock in model["clocks"]:
        if clock["ticks_per_second"] <= 0 or (clock["frame_rate"] and clock["frame_rate"]["denominator"] <= 0):
            raise ValueError("clock rates must have positive denominators and ticks_per_second")
        # One clock, one rate: a second row for the same clock under a
        # different rate puts two grids under one id, and every row that
        # names the clock then reads at the wrong time by the ratio.
        prior_rate = clock_rates.get(clock["id"])
        if prior_rate is not None and prior_rate != clock["ticks_per_second"]:
            raise ValueError(
                f"clock {clock['id']!r} carries two tick rates "
                f"({prior_rate} and {clock['ticks_per_second']}); one clock "
                f"keeps one rate")
        clock_rates[clock["id"]] = clock["ticks_per_second"]
    confidence_values = [item["confidence"] for key in ("observations", "identity_links", "relations") for item in model[key]]
    confidence_values.extend(sample["confidence"] for track in model["tracks"] for sample in track["samples"])
    if any(value is not None and not 0 <= value <= 1 for value in confidence_values):
        raise ValueError("confidence must be between 0 and 1")
    observation_ids = {item["id"] for item in model["observations"]}
    # The intents collection is a first-class layer, distinct from the
    # edits that realise it: an intent says what was asked for, an edit
    # says which clip moves.  The links between them are checked here,
    # because an edit pointing at an intent nobody wrote is a decision
    # with nothing behind it.
    intent_ids = {item["id"] for item in model.get("intents", [])}
    if len(intent_ids) != len(model.get("intents", [])):
        raise ValueError("intent ids must be unique")
    for intent in model.get("intents", []):
        if not intent.get("id"):
            raise ValueError("intent requires a stable id")
        if not set(intent.get("evidence_links", [])) <= observation_ids:
            raise ValueError(
                f"intent {intent['id']!r} references unknown observation")
    for use in model.get("clip_uses", []):
        intent_id = use.get("intent_id")
        if intent_id is not None and intent_id not in intent_ids:
            raise ValueError(
                f"clip use {use['id']!r} references unknown intent "
                f"{intent_id!r}")
    for operation in model.get("edits", []):
        intent_id = operation.get("intent_id")
        if intent_id is not None and intent_id not in intent_ids:
            raise ValueError(
                f"edit {operation['id']!r} references unknown intent "
                f"{intent_id!r}")
        evidence_backed = any(operation.get(field) is not None for field in (
            "query_result_id", "evidence_revision", "creative_rationale"))
        if evidence_backed and not operation.get("evidence_ids"):
            raise ValueError(
                f"evidence-backed edit {operation['id']!r} needs evidence_ids")
        if not set(operation.get("evidence_ids", [])) <= observation_ids:
            raise ValueError(
                f"edit {operation['id']!r} references unknown observation")
        if evidence_backed and (not operation.get("query_result_id")
                                or operation.get("evidence_revision") is None):
            raise ValueError(
                f"evidence-backed edit {operation['id']!r} needs query result "
                "and evidence revision")
    entity_ids = {item["id"] for item in model["entities"]}
    track_ids = {item["id"] for item in model.get("tracks", [])}
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
            # Keep objective evidence fields at the project-model boundary,
            # where JSON Schema alone permits empty strings and does not
            # relate the legacy certainty field to its typed evidence.
            if not evidence.get("unit") or not evidence.get("method"):
                raise ValueError("evidence must carry a non-empty unit and method")
            evidence_type = evidence["evidence_type"]
            evidence_certainty = evidence.get("certainty")
            allowed_certainty = {
                "measurement": {"observed", "measured"},
                "inference": {"inferred"},
                "creative_judgment": {"interpreted"},
                "evaluation": set(),
            }[evidence_type]
            if evidence_certainty is not None and evidence_certainty not in allowed_certainty:
                raise ValueError("evidence certainty conflicts with its evidence type")
            if certainty is not None and certainty not in allowed_certainty:
                raise ValueError("observation certainty conflicts with its evidence type")
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
                            or measurement.get("measurement_type") not in required_measurements
                            or measurement.get("evidence_type") != evidence_contract.registry_entry(
                                measurement.get("measurement_type"))["kind"].value):
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
        track_id = link.get("track_id")
        if track_id is not None and track_id not in track_ids:
            raise ValueError("identity link references unknown track")
        if (link.get("method") == "reidentification"
                and link.get("status") == "verified" and not support):
            raise ValueError("verified re-identification requires supporting observations")
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
    for item in model.get("entities", []):
        if item.get("identity_scope") not in (
                None, "asset", "capture_group", "project", "cross_project"):
            raise ValueError("entity identity_scope must use the lane 3 vocabulary")
    for relation in model.get("relations", []):
        if relation["interval"]["start"] >= relation["interval"]["end"]:
            raise ValueError("relation intervals must be non-empty and half-open")
        if relation["subject_id"] not in entity_ids or relation["object_id"] not in entity_ids:
            raise ValueError("relation references unknown participant")
        if relation.get("status") == "verified" and not relation.get("evidence_ids"):
            raise ValueError("verified relations require evidence")
        if not set(relation.get("evidence_ids", [])) <= observation_ids:
            raise ValueError("relation references unknown observations")
        if relation.get("certainty") not in (None, "observed", "measured", "inferred", "interpreted"):
            raise ValueError("relation certainty must use the canonical epistemic vocabulary")
        # Lane 3: each relation class carries its own evidence bar.
        relation_class = relation.get("relation_class")
        if relation_class not in (None, "temporal", "physical", "semantic"):
            raise ValueError("relation relation_class must use the lane 3 vocabulary")
        # The published predicate vocabulary: a relation names a
        # predicate the engine publishes, and the predicate's class is
        # the class the edge carries. An unpublished predicate is not a
        # relation this model may hold.
        predicate = relation.get("predicate")
        if relation_class is not None and isinstance(predicate, str):
            from library.tools import relation_classes
            published = relation_classes.PREDICATES.get(predicate)
            if published is None:
                raise ValueError(
                    f"relation predicate {predicate!r} is not published")
            if published["relation_class"] != relation_class:
                raise ValueError(
                    f"relation predicate {predicate!r} is a "
                    f"{published['relation_class']} relation")
        if relation_class == "temporal":
            if clock_ids and relation["interval"]["clock_id"] not in clock_ids:
                raise ValueError("temporal relations require a known clock")
        elif relation_class == "physical":
            if not relation.get("evidence_ids"):
                raise ValueError("physical relations require spatial or track evidence")
            if relation.get("certainty") == "interpreted":
                raise ValueError("physical relations are measurements or inferences, never interpretations")
        elif relation_class == "semantic":
            if relation.get("certainty") != "interpreted":
                raise ValueError("semantic relations require interpreted certainty")
            if not relation.get("evidence_ids"):
                raise ValueError("semantic relations require interpretation-level support")
            if relation.get("status") == "verified":
                raise ValueError("semantic relations stay candidates, never verified")
    for event in model.get("events", []):
        if event["interval"]["start"] >= event["interval"]["end"]:
            raise ValueError("event intervals must be non-empty and half-open")
        if event.get("status") == "verified" and not event.get("evidence_ids"):
            raise ValueError("verified events require evidence")
        if not set(event.get("evidence_ids", [])) <= observation_ids:
            raise ValueError("event references unknown observations")
        if not set(event.get("participant_ids", [])) <= entity_ids:
            raise ValueError("event references unknown participants")
        for change in event.get("state_changes", []):
            if change.get("entity_id") not in entity_ids:
                raise ValueError("event state change references unknown entity")
            if not str(change.get("property", "")).strip():
                raise ValueError("event state change requires a property")
            if not change.get("evidence_ids"):
                raise ValueError("event state change requires evidence")
            if not set(change["evidence_ids"]) <= observation_ids:
                raise ValueError("event state change references unknown observation")
    event_ids = {event["id"] for event in model.get("events", [])}
    events_by_id = {event["id"]: event for event in model.get("events", [])}
    _validate_occurrences(model, entity_ids)
    # B-05: an estimated boundary carries the bound of the grid that
    # located it. A boundary that no grid located is filed open, which
    # claims no bound - an estimated boundary with no bound would read
    # as a precision nobody measured.
    #
    # The rule applies to a model that carries the B-04 coverage
    # contract (a processing status on its observations). A model
    # migrated before that contract was written down still holds
    # unbounded boundaries from an era when nothing bounded them; it is
    # re-migrated before it can answer a boundary question, and until
    # then it is not evidence of anything.
    bounded_boundaries = _carries_coverage_contract(model)
    for event in model.get("events", []):
        for key in ("start_boundary", "end_boundary"):
            boundary = event.get(key)
            if boundary is None:
                continue
            status = boundary["status"]
            uncertainty = boundary.get("uncertainty_ticks")
            if status == "exact" and uncertainty not in (None, 0):
                raise ValueError("exact event boundaries cannot have non-zero uncertainty")
            if status == "open" and uncertainty is not None:
                raise ValueError("open event boundaries cannot claim bounded uncertainty")
            if (status == "estimated" and bounded_boundaries
                    and not (type(uncertainty) is int and uncertainty >= 0)):
                raise ValueError(
                    "estimated event boundaries must carry a bounded "
                    "uncertainty_ticks")
        for parent_id in event.get("parent_event_ids", []):
            if parent_id == event["id"]:
                raise ValueError("event cannot parent itself")
            parent = events_by_id.get(parent_id)
            if parent is None:
                raise ValueError("event references unknown parent event")
            if (parent["interval"]["clock_id"] != event["interval"]["clock_id"]
                    or parent["interval"]["start"] > event["interval"]["start"]
                    or parent["interval"]["end"] < event["interval"]["end"]):
                raise ValueError("parent event must contain child on the same clock")
    for event in model.get("events", []):
        pending = list(event.get("parent_event_ids", []))
        seen: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in seen:
                raise ValueError("event hierarchy must be acyclic")
            seen.add(parent_id)
            parent = events_by_id.get(parent_id)
            if parent is not None:
                pending.extend(parent.get("parent_event_ids", []))
    for interpretation in model.get("interpretations", []):
        if interpretation["certainty"] != "interpreted":
            raise ValueError("interpretation certainty must be interpreted")
        uncertainty = interpretation.get("uncertainty")
        if uncertainty is not None and not 0 <= uncertainty <= 1:
            raise ValueError("interpretation uncertainty must be between 0 and 1")
        if interpretation["kind"].startswith("narrative_"):
            relation_kind = interpretation["kind"][len("narrative_"):]
            if relation_kind not in {"claim", "topic", "explanation", "demonstration", "context_dependency"}:
                raise ValueError("narrative interpretation must use a supported relation type")
            scope = interpretation["scope"]
            if (not isinstance(scope, dict) or not scope.get("speech_passage_ids")
                    or not isinstance(scope.get("event_ids", []), list)):
                raise ValueError("narrative interpretation requires speech passage and event scope")
            relation = interpretation.get("narrative_relation")
            if (not isinstance(relation, dict)
                    or relation.get("relation_type") != relation_kind
                    or relation.get("status") != "candidate"
                    or relation.get("speech_passage_ids") != scope.get("speech_passage_ids")
                    or relation.get("event_ids") != scope.get("event_ids", [])):
                raise ValueError("narrative interpretation requires a matching typed candidate relation")
            if interpretation["certainty"] != "interpreted" or uncertainty is None:
                raise ValueError("narrative hypotheses require interpreted certainty and explicit uncertainty")
            if not interpretation["evidence_ids"] or not any(
                str(evidence_id).startswith("speech:")
                for evidence_id in interpretation["evidence_ids"]
            ):
                raise ValueError("narrative hypotheses require speech evidence")
            if not set(scope.get("event_ids", [])) <= set(interpretation["evidence_ids"]):
                raise ValueError("narrative event scope must be included in evidence_ids")
            if not set(scope.get("event_ids", [])) <= event_ids:
                raise ValueError("narrative interpretation references an unknown event")
    signal_ids = {signal["id"] for signal in model.get("signals", [])}
    source_links = {}
    for signal in model.get("signals", []):
        if signal["interval"]["start"] >= signal["interval"]["end"]:
            raise ValueError("signal intervals must be non-empty and half-open (start < end)")
        source_signal_id = signal.get("source_signal_id")
        if signal.get("estimate_status") not in (None, "estimated"):
            raise ValueError("signal estimate_status must be 'estimated' when present")
        if (signal.get("estimate_status") == "estimated"
                and (not source_signal_id or not signal.get("samples_ref"))):
            raise ValueError("estimated signals require source signal provenance and samples_ref")
        if source_signal_id is not None and source_signal_id not in signal_ids:
            raise ValueError("signal references unknown source_signal_id")
        if source_signal_id == signal["id"]:
            raise ValueError("signal cannot be its own source_signal_id")
        if source_signal_id is not None:
            source_links[signal["id"]] = source_signal_id
        if clock_ids and (signal["sample_clock_id"] not in clock_ids
                          or signal["interval"]["clock_id"] not in clock_ids):
            raise ValueError("signal references unknown clock")
        if signal.get("processing_status") == "unavailable" and signal.get("samples"):
            raise ValueError("unavailable signal cannot carry samples")
    for signal_id in source_links:
        seen = set()
        current = signal_id
        while current in source_links:
            if current in seen:
                raise ValueError("signal source provenance must be acyclic")
            seen.add(current)
            current = source_links[current]
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


def _validate_occurrences(model: dict[str, Any], entity_ids: set[str]) -> None:
    """Check the occurrence axis: one series, numbered once.

    An occurrence names the class it counts within, the subject it
    counts for and its 1-based position. Two events may not claim the
    same position in the same series of the same source - that is what
    makes "the second time" name ONE event.
    """
    asset_of_clock: dict[str, str] = {}
    for clock in model.get("clocks", []):
        domain = clock.get("domain") or {}
        asset_id = domain.get("asset_id") if isinstance(domain, dict) else None
        if isinstance(asset_id, str) and asset_id:
            asset_of_clock[clock["id"]] = asset_id
    claimed: set[tuple[str, str, str | None, int]] = set()
    for event in model.get("events", []):
        occurrence = event.get("occurrence")
        if occurrence is None:
            continue
        klass = occurrence.get("class")
        index = occurrence.get("index")
        subject = occurrence.get("subject_id")
        if not isinstance(klass, str) or not klass.strip():
            raise ValueError("an event occurrence names its class")
        if type(index) is not int or index < 1:
            raise ValueError("an event occurrence index is 1-based")
        if subject is not None:
            if subject not in entity_ids:
                raise ValueError("event occurrence references unknown subject")
            if subject not in (event.get("participant_ids") or []):
                raise ValueError(
                    "event occurrence names a subject the event does not "
                    "carry as a participant")
        clock_id = (event.get("interval") or {}).get("clock_id")
        asset_id = asset_of_clock.get(clock_id, "")
        key = (asset_id, klass.strip(), subject, index)
        if key in claimed:
            raise ValueError(
                f"two events claim occurrence {index} of "
                f"{klass!r} for the same subject and source")
        claimed.add(key)


def _carries_coverage_contract(model: dict[str, Any]) -> bool:
    """Whether the model carries the B-04 coverage contract.

    A model migrated under B-04 stamps a processing status on every
    observation. A model that predates the contract carries none, and
    its event boundaries were written before anything bounded them.
    """
    from library.tools import processing_status

    for item in model.get("observations", []):
        if (isinstance(item, dict)
                and processing_status.processing_status_of(item) is not None):
            return True
    return False


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
