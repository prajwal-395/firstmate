"""Translate one editor note into typed, replayable ledger operations.

The host model does the language understanding through the existing
Ren file handshake. This module owns the typed boundary after that: one
operation per clause, operation-type routing, explicit questions for
missing referents, per-value source and unit metadata, atomic ledger
recording, and an intent report that can carry measured misses back as
a revised spec. Proxy preview is deliberately out of scope for this
rung.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shlex
import sys
import uuid
from pathlib import Path

from library.tools import edit_ledger, edit_operations

FORMAT = "edit_spec/1"
STATUSES = ("resolved", "needs_clarification")
SOURCES = edit_ledger.STATED_BY
PROXY_PREVIEW = "proxy preview is out of scope for the rung 6 translation layer"

OP_OWNERS = edit_operations.OP_OWNERS
_DIRECT_LEDGER_OPS = set(edit_ledger.OPS) - {"plan_change"}
if set(edit_operations.DIRECT_OP_OWNERS) != _DIRECT_LEDGER_OPS:
    raise RuntimeError(
        "direct edit operation owners must cover ledger ops other than "
        "plan_change exactly")


class EditSpecError(ValueError):
    """A translation that cannot safely become declared edits."""


class NeedsClarification(EditSpecError):
    """The requester must resolve one or more named referents."""

    def __init__(self, questions: list[dict]):
        self.questions = questions
        detail = "\n".join(
            f"- {q['clause_id']}: {q['question']}" for q in questions)
        super().__init__(
            "the edit spec has unresolved referents; ask the editor and "
            "revise the spec before recording any operation:\n" + detail)


def owner_for_op(op: str) -> str:
    """Return the owner for an enumerated operation, never from its prose."""
    try:
        return edit_operations.owner_for_op(op)
    except ValueError as exc:
        raise EditSpecError(
            f"unknown edit operation {op!r}; known operations: "
            f"{', '.join(OP_OWNERS)}") from exc


def _nonempty_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EditSpecError(f"{field} must be a non-empty string")
    return value.strip()


def _reject_could_not_determine(value, path="edit spec") -> None:
    if isinstance(value, dict):
        if "could_not_determine" in value:
            raise EditSpecError(
                f"{path} uses could_not_determine; unresolved referents "
                "must be returned as needs_clarification with a direct "
                "question to the editor")
        for key, nested in value.items():
            _reject_could_not_determine(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_could_not_determine(nested, f"{path}[{index}]")


def _row_for_clause(clause: dict, index: int,
                    source_note_id: str = "") -> dict:
    op = clause.get("op")
    owner_for_op(op)
    op_source = clause.get("op_source")
    if op_source not in SOURCES:
        raise EditSpecError(
            f"clauses[{index}].op_source must be one of {SOURCES}")
    anchor = clause.get("anchor")
    if not isinstance(anchor, dict):
        raise EditSpecError(f"clauses[{index}].anchor must be an object")
    anchor_source = clause.get("anchor_source")
    if anchor_source not in SOURCES:
        raise EditSpecError(
            f"clauses[{index}].anchor_source must be one of {SOURCES}")
    reel = clause.get("reel")
    if reel is not None:
        reel = _nonempty_string(reel, f"clauses[{index}].reel")
        reel_source = clause.get("reel_source")
        if reel_source not in SOURCES:
            raise EditSpecError(
                f"clauses[{index}].reel_source must be one of {SOURCES}")
    else:
        reel_source = None

    values = clause.get("values")
    if not isinstance(values, dict):
        raise EditSpecError(
            f"clauses[{index}].values must map each operation value to "
            "{value, unit, stated_by}")
    params, sources, units = {}, {}, {}
    for key, value in values.items():
        key = _nonempty_string(key, f"clauses[{index}].values key")
        if not isinstance(value, dict) or "value" not in value:
            raise EditSpecError(
                f"clauses[{index}].values[{key!r}] needs value, unit and "
                "stated_by")
        unit = _nonempty_string(value.get("unit"),
                                f"clauses[{index}].values[{key!r}].unit")
        source = value.get("stated_by")
        if source not in SOURCES:
            raise EditSpecError(
                f"clauses[{index}].values[{key!r}].stated_by must be one "
                f"of {SOURCES}")
        params[key] = value["value"]
        sources[key] = source
        units[key] = unit

    direct = op in edit_operations.DIRECT_OP_OWNERS
    if direct:
        row_op = op
    else:
        if not source_note_id:
            raise EditSpecError(
                f"{op} is delivered to {owner_for_op(op)} through its "
                "linked marker note; prepare the spec with --note-id")
        if not values:
            raise EditSpecError(
                f"{op} needs one or more typed operation values")
        _validate_plan_operation_values(op, values, anchor, index)
        params = {"operation_type": op, "owner": owner_for_op(op),
                  "values": copy.deepcopy(values)}
        sources = {"operation_type": op_source,
                   "owner": "model", "values": "model"}
        units = {"operation_type": "operation type",
                 "owner": "pipeline node", "values": "typed values"}
        row_op = "plan_change"

    sources_used = set(value.get("stated_by") for value in values.values())
    stated_by = (next(iter(sources_used)) if len(sources_used) == 1
                 else "model")
    row = {
        "op": row_op,
        "op_source": op_source,
        "anchor": copy.deepcopy(anchor),
        "anchor_source": anchor_source,
        "params": params,
        "value_sources": sources,
        "value_units": units,
        "stated_by": stated_by,
        "reason": _nonempty_string(
            clause.get("text"), f"clauses[{index}].text"),
    }
    if reel is not None:
        row["reel"] = reel
        row["reel_source"] = reel_source
    if source_note_id:
        row["source_note_id"] = source_note_id
    edit_ledger.validate_rows([row])
    return row


def _validate_plan_operation_values(op: str, values: dict, anchor: dict,
                                    index: int) -> None:
    """Enforce declared typed fields for plan operations that have them."""
    contract = edit_operations.PLAN_OPERATION_VALUE_CONTRACTS.get(op)
    if contract is None:
        return
    expected_fields = set(contract["values"])
    if set(values) != expected_fields:
        expected = ", ".join(sorted(expected_fields))
        raise EditSpecError(
            f"clauses[{index}].values for {op} must contain exactly: "
            f"{expected}")
    if anchor.get("kind") != contract["required_anchor"]:
        raise EditSpecError(
            f"clauses[{index}].anchor for {op} must identify a spoken "
            f"{contract['required_anchor']} passage")
    phrase = anchor.get("phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        raise EditSpecError(
            f"clauses[{index}].anchor.phrase for {op} must be a "
            "non-empty transcript phrase")
    for field, field_contract in contract["values"].items():
        typed = values[field]
        if typed["unit"] != field_contract["unit"]:
            raise EditSpecError(
                f"clauses[{index}].values[{field!r}].unit for {op} must be "
                f"{field_contract['unit']!r}")
        if (not isinstance(typed["value"], str)
                or typed["value"] not in field_contract["allowed"]):
            allowed = ", ".join(field_contract["allowed"])
            raise EditSpecError(
                f"clauses[{index}].values[{field!r}].value for {op} must be "
                f"one of: {allowed}")


def validate_spec(value: dict) -> dict:
    """Validate a host translation and return a detached JSON-shaped copy."""
    if not isinstance(value, dict):
        raise EditSpecError("edit spec must be a JSON object")
    _reject_could_not_determine(value)
    if value.get("format") != FORMAT:
        raise EditSpecError(
            f"edit spec format must be {FORMAT!r}, got "
            f"{value.get('format')!r}")
    _nonempty_string(value.get("request"), "request")
    available_reels = value.get("available_reels")
    if (available_reels is not None
            and (not isinstance(available_reels, list)
                 or any(not isinstance(name, str) or not name.strip()
                        for name in available_reels))):
        raise EditSpecError(
            "available_reels must be a list of exact non-empty timeline names")
    source_note_id = value.get("source_note_id", "")
    if source_note_id is not None and not isinstance(source_note_id, str):
        raise EditSpecError("source_note_id must be a marker note id string")
    if isinstance(source_note_id, str) and source_note_id.strip() != source_note_id:
        raise EditSpecError("source_note_id cannot have leading/trailing spaces")
    clauses = value.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        raise EditSpecError(
            "clauses must be a non-empty list with one operation per clause")
    ids, questions = set(), []
    rows = []
    for index, clause in enumerate(clauses):
        if not isinstance(clause, dict):
            raise EditSpecError(f"clauses[{index}] must be an object")
        clause_id = _nonempty_string(
            clause.get("id"), f"clauses[{index}].id")
        if clause_id in ids:
            raise EditSpecError(f"duplicate clause id {clause_id!r}")
        ids.add(clause_id)
        if clause.get("status") not in STATUSES:
            raise EditSpecError(
                f"clauses[{index}].status must be one of {STATUSES}")
        owner_for_op(clause.get("op"))
        _nonempty_string(clause.get("text"), f"clauses[{index}].text")
        if clause.get("op_source") not in SOURCES:
            raise EditSpecError(
                f"clauses[{index}].op_source must be one of {SOURCES}")
        if clause["status"] == "needs_clarification":
            missing = _nonempty_string(
                clause.get("missing_referent"),
                f"clauses[{index}].missing_referent")
            question = _nonempty_string(
                clause.get("question"), f"clauses[{index}].question")
            questions.append({"clause_id": clause_id,
                              "missing_referent": missing,
                              "question": question})
            continue
        if clause["op"] == "angle_plan" and not clause.get("reel"):
            # Unscoped, it is a question (`questions_for_spec`), not a
            # malformed row: the ledger refuses an angle plan with no reel.
            continue
        rows.append(_row_for_clause(clause, index, source_note_id))
    return copy.deepcopy(value)


def questions_for_spec(spec: dict) -> list[dict]:
    """List unresolved references; unresolved clauses never produce rows."""
    validated = validate_spec(spec)
    questions = []
    available_reels = validated.get("available_reels", [])
    for clause in validated["clauses"]:
        if clause["status"] == "needs_clarification":
            questions.append({"clause_id": clause["id"],
                              "missing_referent": clause[
                                  "missing_referent"],
                              "question": clause["question"]})
        elif clause["op"] == "angle_plan" and not clause.get("reel"):
            known = (f" Available reels: {', '.join(available_reels)}"
                     if available_reels else
                     " This project has no reels yet; an angle plan holds "
                     "on a reel's build.")
            questions.append({
                "clause_id": clause["id"],
                "missing_referent": "which reel this angle plan belongs to",
                "question": ("Which exact reel timeline should use this "
                             "angle plan?" + known),
            })
        elif (clause["op"] == "angle_plan" and available_reels
              and clause["reel"] not in available_reels):
            questions.append({
                "clause_id": clause["id"],
                "missing_referent": "the named reel timeline",
                "question": (
                    f"I could not match {clause['reel']!r} to an available "
                    "reel timeline. Which exact reel should use this angle "
                    f"plan? Available reels: {', '.join(available_reels)}"),
            })
    return questions


def _measured_value(value, label: str) -> dict:
    if not isinstance(value, dict) or "value" not in value:
        raise EditSpecError(f"{label} must carry a measured value")
    _nonempty_string(value.get("unit"), f"{label}.unit")
    if value["value"] is None:
        raise EditSpecError(f"{label}.value cannot be null")
    return value


def _measured_shortfall(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise EditSpecError(f"{label} must be a measured shortfall object")
    if not all(key in value for key in ("requested", "observed", "unit")):
        raise EditSpecError(
            f"{label} needs requested, observed and unit measurements")
    _nonempty_string(value.get("unit"), f"{label}.unit")
    if value["requested"] is None or value["observed"] is None:
        raise EditSpecError(
            f"{label}.requested and observed cannot be null")
    return value


def ledger_rows(spec: dict) -> list[dict]:
    """Compile a fully resolved spec to validated edit-ledger rows."""
    validated = validate_spec(spec)
    questions = questions_for_spec(validated)
    if questions:
        raise NeedsClarification(questions)
    return [_row_for_clause(clause, index, validated.get("source_note_id", ""))
            for index, clause in enumerate(validated["clauses"])]


def record_spec(project_folder: str, spec: dict) -> list[tuple[dict, str]]:
    """Record every resolved clause together or refuse the whole spec."""
    rows = ledger_rows(spec)
    return edit_ledger.record_rows(
        project_folder, rows,
        replace_source_note_id=spec.get("source_note_id") or None)


def _normalise_recorded_row(row: dict) -> dict:
    """Remove the transcript-derived edge added by the ledger writer."""
    normalized = copy.deepcopy(row)
    for field in ("params", "value_sources", "value_units"):
        values = normalized.get(field)
        if isinstance(values, dict):
            values.pop("recorded_edge", None)
    return normalized


def _matches_recorded_rows(spec: dict, recorded: list[dict]) -> bool:
    """Whether this spec's exact operations are present in its ledger rows."""
    expected = ledger_rows(spec)
    expected_rows = {
        json.dumps(_normalise_recorded_row(row), sort_keys=True,
                   ensure_ascii=False) for row in expected}
    recorded_rows = {
        json.dumps(_normalise_recorded_row(row), sort_keys=True,
                   ensure_ascii=False) for row in recorded}
    return (len(recorded) == len(spec["clauses"])
            and recorded_rows == expected_rows)


def intent_check(spec: dict, observations: dict) -> dict:
    """Compare each op with timeline and export evidence.

    Every observation must report both ``followed`` and
    ``broke_nothing`` for the built timeline and export. A miss needs a
    measured shortfall, which is copied into the revised spec instead
    of being reduced to a generic failed flag.
    """
    validated = validate_spec(spec)
    questions = questions_for_spec(validated)
    if questions:
        raise NeedsClarification(questions)
    if not isinstance(observations, dict):
        raise EditSpecError("observations must be keyed by clause id")
    expected = {clause["id"] for clause in validated["clauses"]}
    if set(observations) != expected:
        raise EditSpecError(
            "intent observations must name every clause exactly once; "
            f"missing={sorted(expected - set(observations))}, "
            f"extra={sorted(set(observations) - expected)}")
    results, revisions = [], []
    for clause in validated["clauses"]:
        clause_id = clause["id"]
        evidence = observations[clause_id]
        if not isinstance(evidence, dict):
            raise EditSpecError(
                f"observations[{clause_id!r}] must be an object")
        readings, misses = {}, []
        for surface in ("timeline", "export"):
            reading = evidence.get(surface)
            if not isinstance(reading, dict):
                raise EditSpecError(
                    f"observations[{clause_id!r}].{surface} is required")
            for field in ("followed", "broke_nothing"):
                if not isinstance(reading.get(field), bool):
                    raise EditSpecError(
                        f"observations[{clause_id!r}].{surface}.{field} "
                        "must be boolean")
            _nonempty_string(
                reading.get("evidence"),
                f"observations[{clause_id!r}].{surface}.evidence")
            measurement = _measured_value(
                reading.get("measurement"),
                f"observations[{clause_id!r}].{surface}.measurement")
            readings[surface] = copy.deepcopy(reading)
            if not reading["followed"] or not reading["broke_nothing"]:
                shortfall = _measured_shortfall(
                    reading.get("shortfall"),
                    f"{clause_id} {surface} shortfall")
                if shortfall["unit"] != measurement["unit"]:
                    raise EditSpecError(
                        f"{clause_id} {surface} shortfall unit "
                        f"{shortfall['unit']!r} differs from measured unit "
                        f"{measurement['unit']!r}")
                if shortfall["observed"] != measurement["value"]:
                    raise EditSpecError(
                        f"{clause_id} {surface} shortfall observed value "
                        "differs from the measured value")
                requested = [value["value"] for value in
                             clause.get("values", {}).values()
                             if value.get("unit") == shortfall["unit"]]
                if requested and shortfall["requested"] not in requested:
                    raise EditSpecError(
                        f"{clause_id} {surface} shortfall requested value "
                        "does not match a value in the edit spec with the "
                        f"same unit ({shortfall['unit']!r})")
                misses.append({"surface": surface,
                               "measured_shortfall": copy.deepcopy(shortfall)})
        result = {"clause_id": clause_id,
                  "op": clause["op"],
                  "route": owner_for_op(clause["op"]),
                  "followed": not any(
                      not r["followed"] for r in readings.values()),
                  "broke_nothing": all(
                      r["broke_nothing"] for r in readings.values()),
                  "timeline": readings["timeline"],
                  "export": readings["export"]}
        if misses:
            result["misses"] = misses
            revisions.append({"clause_id": clause_id,
                              "operation": clause["op"],
                              "measured_shortfalls": misses})
        results.append(result)
    revised = copy.deepcopy(validated)
    for clause in revised["clauses"]:
        miss = next((item for item in revisions
                     if item["clause_id"] == clause["id"]), None)
        if miss:
            clause["status"] = "needs_clarification"
            clause["revision"] = {
                "measured_shortfalls": miss["measured_shortfalls"],
                "question": (
                    "The built timeline/export missed this operation by "
                    "the measured amount above. Should the requested value "
                    "change, or should Ren revise its implementation?")}
            clause["missing_referent"] = "measured intent shortfall"
            clause["question"] = clause["revision"]["question"]
    return {
        "status": "revise" if revisions else "passed",
        "results": results,
        "revised_spec": revised if revisions else None,
        "proxy_preview": PROXY_PREVIEW,
    }


INTENT_FORMAT = "edit_intent_observations/1"


def _intent_schema(spec: dict) -> str:
    measurement = {
        "type": "object",
        "required": ["value", "unit"],
        "properties": {
            "value": {"type": ["number", "string", "object", "array",
                                "boolean"]},
            "unit": {"type": "string", "minLength": 1},
        },
        "additionalProperties": True,
    }
    shortfall = {
        "type": "object",
        "required": ["requested", "observed", "unit"],
        "properties": {
            "requested": {"type": ["number", "string", "object",
                                    "array", "boolean"]},
            "observed": {"type": ["number", "string", "object",
                                   "array", "boolean"]},
            "unit": {"type": "string", "minLength": 1},
        },
        "additionalProperties": True,
    }
    surface = {
        "type": "object",
        "required": ["followed", "broke_nothing", "measurement",
                     "evidence"],
        "properties": {
            "followed": {"type": "boolean"},
            "broke_nothing": {"type": "boolean"},
            "measurement": measurement,
            "shortfall": shortfall,
            "evidence": {"type": "string", "minLength": 1},
        },
        "additionalProperties": True,
    }
    clause_ids = [clause["id"] for clause in spec["clauses"]]
    return json.dumps({
        "type": "object",
        "required": ["format", "observations"],
        "properties": {
            "format": {"const": INTENT_FORMAT},
            "observations": {
                "type": "object",
                "required": clause_ids,
                "properties": {
                    clause_id: {
                        "type": "object",
                        "required": ["timeline", "export"],
                        "properties": {
                            "timeline": surface,
                            "export": surface,
                        },
                        "additionalProperties": False,
                    }
                    for clause_id in clause_ids
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }, ensure_ascii=False)


def _intent_artifact(path_value: str, label: str) -> dict:
    path = Path(_nonempty_string(path_value, label)).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError as exc:
        raise EditSpecError(f"cannot read {label} {path}: {exc}") from exc
    if not path.is_file() or stat.st_size <= 0:
        raise EditSpecError(f"{label} must be a non-empty file: {path}")
    return {"path": str(path), "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns}


def prepare_intent(project_folder: str, spec_id: str,
                   timeline_readback: str, export: str) -> tuple[str, str]:
    """Ask the host to inspect the actual built timeline readback and export."""
    from library.tools import llm_handshake
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.path.abspath(project_folder)
    spec = load_response_spec(project_folder, spec_id)
    questions = questions_for_spec(spec)
    if questions:
        raise NeedsClarification(questions)
    source_note_id = spec.get("source_note_id", "")
    if source_note_id:
        recorded = [row for row in edit_ledger.load_rows(project_folder)
                    if row.get("source_note_id") == source_note_id]
        if not _matches_recorded_rows(spec, recorded):
            raise EditSpecError(
                "intent review requires the current resolved edit spec to "
                "match every row in external/edit_ledger.json")
    artifacts = {
        "timeline_readback": _intent_artifact(
            timeline_readback, "built timeline readback"),
        "export": _intent_artifact(export, "built export"),
    }
    request_id = "edit_intent_" + uuid.uuid4().hex[:16]
    timestamp = dt.datetime.now(dt.UTC).isoformat()
    prompt = (
        "Review this resolved edit spec against the actual built timeline "
        "readback and the final exported video at the absolute paths in "
        "context. Open and inspect both files; do not judge from the request "
        "or plan alone. For each operation, report whether the timeline and "
        "the export followed it, whether anything else broke, and one "
        "measured value with its unit on each surface. When either surface "
        "missed or broke something, include the requested value, observed "
        "value, and unit as a shortfall. The code will refuse mismatched "
        "shortfall units or observations. Do not create or use a proxy "
        "preview. Return JSON only using edit_intent_observations/1."
    )
    context = {"format": "edit_intent_review/1", "spec": spec,
               "artifacts": artifacts, "proxy_preview": PROXY_PREVIEW}
    payload = llm_handshake.build_request(
        request_id, prompt, "", json.dumps(context, ensure_ascii=False),
        _intent_schema(spec), project_folder, timestamp,
        kind="edit_spec_intent")
    request_path = Path(llm_handshake.request_path(
        project_folder, request_id))
    response_path = Path(llm_handshake.response_path(
        project_folder, request_id))
    if request_path.exists() or response_path.exists():
        raise EditSpecError(
            f"handshake id collision for {request_id}: {request_path} or "
            f"{response_path} exists")
    layout = ProjectLayout(project_folder)
    layout.write_dir(Area.LLM_REQUESTS)
    layout.write_dir(Area.LLM_RESPONSES)
    request_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return request_id, str(request_path)


def resolve_intent(project_folder: str, intent_id: str) -> dict:
    """Validate the host's timeline/export review and return its revision."""
    from library.tools import llm_handshake

    request_path = Path(llm_handshake.request_path(project_folder, intent_id))
    response_path = Path(llm_handshake.response_path(project_folder, intent_id))
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        context = json.loads(request["context"])
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EditSpecError(
            f"cannot load edit intent request/response for {intent_id}: "
            f"{exc}") from exc
    if not isinstance(context, dict) or not isinstance(response, dict):
        raise EditSpecError(
            f"edit intent request and response for {intent_id} must be objects")
    if context.get("format") != "edit_intent_review/1":
        raise EditSpecError(
            f"{request_path} is not an edit_intent_review/1 request")
    if response.get("format") != INTENT_FORMAT:
        raise EditSpecError(
            f"intent response format must be {INTENT_FORMAT!r}")
    for artifact in context.get("artifacts", {}).values():
        path = Path(artifact["path"])
        try:
            stat = path.stat()
        except OSError as exc:
            raise EditSpecError(
                f"reviewed artifact disappeared before resolve: {path}: "
                f"{exc}") from exc
        if (stat.st_size != artifact["size_bytes"]
                or stat.st_mtime_ns != artifact["modified_ns"]):
            raise EditSpecError(
                f"reviewed artifact changed while the intent review was "
                f"pending: {path}; prepare a new review")
    result = intent_check(context["spec"], response["observations"])
    if result["revised_spec"] is not None:
        output = response_path.with_name(
            response_path.stem + ".revised_spec.json")
        _atomic_json(output, result["revised_spec"])
        result["revised_spec_path"] = str(output)
    return result


def _expected_schema() -> str:
    value_source = {"type": "object", "required": [
        "value", "unit", "stated_by"], "properties": {
            "value": {"type": ["number", "string", "object", "array",
                                "boolean"]},
            "unit": {"type": "string", "minLength": 1},
            "stated_by": {"enum": list(SOURCES)},
        }, "additionalProperties": False}
    clause = {
        "type": "object",
        "required": ["id", "text", "op", "op_source", "status"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "text": {"type": "string", "minLength": 1},
            "op": {"enum": list(OP_OWNERS)},
            "op_source": {"enum": list(SOURCES)},
            "status": {"enum": list(STATUSES)},
            "anchor": {"type": "object", "oneOf": [
                {"type": "object", "required": ["kind", "phrase"],
                 "properties": {"kind": {"const": "words"},
                                "phrase": {"type": "string"},
                                "stated": {"type": "string"}}},
                {"type": "object", "required": ["kind"],
                 "properties": {"kind": {"const": "reel"}}}]},
            "anchor_source": {"enum": list(SOURCES)},
            "reel": {"type": "string", "minLength": 1},
            "reel_source": {"enum": list(SOURCES)},
            "values": {"type": "object", "additionalProperties": value_source},
            "missing_referent": {"type": "string", "minLength": 1},
            "question": {"type": "string", "minLength": 1},
        },
        "allOf": [{"if": {"properties": {"status": {
            "const": "resolved"}}}, "then": {"required": [
                "anchor", "anchor_source", "values"]}},
            {"if": {"properties": {"status": {
                "const": "needs_clarification"}}}, "then": {"required": [
                    "missing_referent", "question"]}}],
        "additionalProperties": True,
    }
    for op, contract in edit_operations.PLAN_OPERATION_VALUE_CONTRACTS.items():
        value_properties = {}
        for field, field_contract in contract["values"].items():
            value_properties[field] = {
                "type": "object",
                "required": ["value", "unit", "stated_by"],
                "properties": {
                    "value": {"type": "string",
                              "enum": list(field_contract["allowed"])},
                    "unit": {"const": field_contract["unit"]},
                    "stated_by": {"enum": list(SOURCES)},
                },
                "additionalProperties": False,
            }
        clause["allOf"].append({
            "if": {"properties": {
                "op": {"const": op},
                "status": {"const": "resolved"},
            }},
            "then": {"properties": {
                "anchor": {
                    "type": "object",
                    "required": ["kind", "phrase"],
                    "properties": {
                        "kind": {"const": contract["required_anchor"]},
                        "phrase": {"type": "string", "minLength": 1},
                    },
                },
                "values": {
                    "type": "object",
                    "required": list(contract["values"]),
                    "properties": value_properties,
                    "additionalProperties": False,
                },
            }},
        })
    return json.dumps({
        "type": "object",
        "required": ["format", "request", "clauses"],
        "properties": {
            "format": {"const": FORMAT},
            "request": {"type": "string", "minLength": 1},
            "source_note_id": {"type": "string"},
            "clauses": {"type": "array", "minItems": 1,
                         "items": clause},
        },
        "additionalProperties": True,
    }, ensure_ascii=False)


def _project_context(project_folder: str, request: str, reel: str) -> dict:
    """Read the smallest available project facts useful for translation.

    The model decides only from this frozen request context. It does not
    search or mutate project files while translating; absent facts make a
    referent a question.
    """
    from library.tools.project_layout import ProjectLayout

    path = ProjectLayout(project_folder).pipeline_data_path
    facts = {"request": request, "reel": reel,
             "project_facts_available": False,
             "available_reels": [],
             "operation_types": list(OP_OWNERS),
             "operation_owners": OP_OWNERS,
             "plan_operation_value_contracts":
                 edit_operations.PLAN_OPERATION_VALUE_CONTRACTS,
             "stated_number_fields": edit_operations.STATED_NUMBER_FIELDS,
             "proxy_preview": PROXY_PREVIEW}
    if not Path(path).is_file():
        facts["project_facts_note"] = (
            "No pipeline_data.json exists yet. Do not infer footage, "
            "transcript, brand or reel facts.")
        return facts
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditSpecError(f"cannot read project state {path}: {exc}") from exc
    from library.tools import capability_outputs
    outputs = capability_outputs.node_outputs(state)
    catalog = outputs.get("catalog", {})
    facts.update({
        "project_facts_available": True,
        "project_config": state.get("project_config", {}),
        "creative_brief": state.get("creative_brief", {}),
        "clips": [{key: clip[key] for key in (
            "clip_id", "filename", "duration_seconds", "frame_rate")
                   if key in clip}
                  for clip in catalog.get("clip_catalog", [])
                  if isinstance(clip, dict)],
    })
    def excerpt(value, limit: int) -> str:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return rendered if len(rendered) <= limit else rendered[:limit] + "…"

    semantic = outputs.get("semantic_analysis", {}).get(
        "semantic_analysis_documents", [])
    facts["footage_observations"] = []
    for document in semantic:
        if not isinstance(document, dict):
            continue
        analysis = document.get("analysis") or {}
        assessment = document.get("assessment") or {}
        objects = document.get("objects") or []
        actions = document.get("actions") or []
        facts["footage_observations"].append({
            "clip_id": document.get("clip_id"),
            "scene": excerpt(analysis.get("scene", ""), 500),
            "motion": excerpt(analysis.get("motion", ""), 300),
            "objects": [excerpt(item, 180) for item in objects[:8]],
            "actions": excerpt(actions[:8], 1100),
            "assessment": {
                key: assessment[key] for key in (
                    "camera_stability", "clip_type", "content_type",
                    "keywords", "usable_ranges", "unusable_ranges")
                if key in assessment},
        })
    facts["current_edit"] = _current_edit(outputs)
    speech = outputs.get("speech_sequence", {})
    facts["transcript_context"] = {
        key: speech[key] for key in ("transcripts_toon", "topics_toon")
        if key in speech}
    if "brand_template" in state:
        facts["brand_template"] = state["brand_template"]
    from library.tools.reel_proposal import reel_timeline_name

    reel_names = []
    selection = ((outputs.get("select_reels") or {})
                 .get("reel_selection") or {})
    for moment in selection.get("moments", []):
        if (isinstance(moment, dict) and moment.get("number") is not None
                and isinstance(moment.get("slug"), str)):
            reel_names.append(reel_timeline_name(
                moment["number"], moment["slug"]))
    build_record = (capability_outputs.read(state, "reel.build")
                    .get("reel_build") or {})
    reel_names.extend(name for name in
                      (build_record.get("timelines_built") or [])
                      if isinstance(name, str) and name.strip())
    facts["available_reels"] = list(dict.fromkeys(reel_names))
    if len(json.dumps(facts, ensure_ascii=False).encode("utf-8")) > 128_000:
        facts["footage_observations"] = [
            {"clip_id": observation.get("clip_id"),
             "scene": observation.get("scene"),
             "motion": observation.get("motion"),
             "keywords": observation.get("assessment", {}).get(
                 "keywords", [])[:8]}
            for observation in facts["footage_observations"]]
        facts["project_context_note"] = (
            "Detailed observations were compacted to fit the handshake. "
            "Do not infer an unlisted shot or brand value; ask if the "
            "remaining evidence does not identify the referent.")
    return facts


def _current_edit(outputs: dict) -> list:
    """The built edit in timeline order, one row per spine block.

    An editor names a spot by what they see: "clip 3", "at 0:07". Neither
    survives a rebuild, so the translation resolves it here to the words
    spoken there - a `words` anchor does survive. Words only, never their
    timings (AGENTS.md 10.1).
    """
    spine = (((outputs.get("mesh_spine") or {}).get("audio_spine") or {})
             .get("structure") or [])
    cutaways: dict = {}
    for item in ((outputs.get("select_broll") or {})
                 .get("b_roll_assignments") or []):
        cutaways.setdefault(item["spine_block_position"], []).append(
            item["clip_id"])
    edit = []
    for block in spine:
        content = block["content"] or {}
        words = [word["word"] for word in content.get("word_timestamps") or []]
        edit.append({
            "order": len(edit) + 1,
            "block": block["position"],
            "block_type": block["block_type"],
            "clip_id": block["clip_id"],
            "cutaway_clip_ids": cutaways.get(block["position"], []),
            "timeline_start_seconds": block["timeline_start"],
            "timeline_end_seconds": block["timeline_end"],
            "first_words": " ".join(words[:8]),
            "last_words": " ".join(words[-8:]),
        })
    return edit


def _write_request_note(project_folder: str, request: str,
                        request_id: str, reel: str = "") -> tuple[str, str]:
    """Save a direct Ren request in the same durable note channel.

    This gives every translated plan operation a source note identity, so
    the runner can hold it for questions and later route it by its typed
    operation. Existing Resolve marker notes pass their recorded id and do
    not create a second note.
    """
    from library.tools import marker_feedback, marker_routing
    from library.tools.project_layout import Area, ProjectLayout

    timeline = reel or f"Ren request {request_id}"
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"ren-request.{stamp}.{request_id}{marker_feedback.PULL_FILE_SUFFIX}"
    path = ProjectLayout(project_folder).write_path(
        Area.MARKER_FEEDBACK, filename)
    raw = {
        "source": "timeline_marker",
        "name": f"Ren request {request_id}",
        "note": request,
        "text": request,
        "frame": None,
        "timecode": None,
        "frame_in_timeline_space": None,
        "unplaced_reason": "natural-language request entered through ren spec",
        "duration_frames": 1,
        "custom_data": {},
        "custom_data_raw": "",
        "attachments": [],
    }
    payload = {
        "format": marker_feedback.PULL_FORMAT,
        "pulled_at": dt.datetime.now(dt.UTC).isoformat(),
        "resolve_project": "ren spec",
        "timeline": timeline,
        "timeline_start_frame": 0,
        "timeline_fps": 0.0,
        "note_count": 1,
        "notes": [raw],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    note_id = marker_routing.edit_link_id(raw, timeline, str(path))
    return note_id, str(path)


def prepare_request(project_folder: str, request: str, reel: str = "",
                    note_id: str = "") -> tuple[str, str, str]:
    """Write a translation request; return id, request path and note id."""
    from library.tools import llm_handshake
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.path.abspath(project_folder)
    if not Path(project_folder).is_dir():
        raise EditSpecError(f"project folder does not exist: {project_folder}")
    request = _nonempty_string(request, "request")
    if not isinstance(note_id, str):
        raise EditSpecError("note_id must be a marker note id string")
    note_id = note_id.strip()
    if reel:
        reel = _nonempty_string(reel, "reel")
    else:
        reel = ""
    request_id = "edit_spec_" + uuid.uuid4().hex[:16]
    step_id = request_id
    timestamp = dt.datetime.now(dt.UTC).isoformat()
    note_path = ""
    if not note_id:
        note_id, note_path = _write_request_note(
            project_folder, request, request_id, reel=reel)
    facts = _project_context(project_folder, request, reel)
    facts["source_note_id"] = note_id
    if note_path:
        facts["source_note_path"] = note_path
    prompt = (
        "Translate this editor note into one edit_spec/1 object. Split it "
        "into exactly one operation per clause. Choose operations by their "
        "meaning and emit the operation type; do not route by matching "
        "keywords. Use only the declared edit-ledger operation vocabulary. "
        "Keep every stated number in its original unit and source it as "
        "requester; preserve feel words as named feels instead of making up "
        "a number. Attribute every value, anchor and reel to requester, "
        "model or captain. If a brand, logo, shot, product, or which-shot "
        "referent is absent or unclear, mark that clause "
        "needs_clarification and ask one direct editor question. Do not "
        "write could_not_determine and do not plan around an unresolved "
        "referent. A clip number or a timecode the editor gives refers to "
        "`current_edit` (timeline order, seconds from the start): resolve "
        "it to a `words` anchor spoken there and keep the editor's own "
        "reference in `anchor.stated`; ask only when `current_edit` does "
        "not settle it. Numbers stated in frames or seconds keep the unit "
        "`frames` or `seconds`: the owning step carries them in its fields "
        "listed under `stated_number_fields`. An `angle_plan` must name the "
        "exact `reel` and its camera, `min_shot_seconds` and `lead_frames`; "
        "ask which reel if the request and `available_reels` do not say. "
        "Use `plan_operation_value_contracts` for typed planning values. "
        "For a story_pacing request to open on a spoken line and return to "
        "the normal intro, emit `opening_structure` with value "
        "`cold_open_then_intro`, unit `spine structure`, and "
        "`stated_by: requester`. Put the transcript-grounded line in a "
        "`words` anchor; do not encode a line or passage position in the "
        "structure value. The mesh_spine step resolves that anchor against "
        "the speech sequence and chooses its passage position. A resolved "
        "plan operation with no typed values is refused; ask if its spoken "
        "anchor cannot be identified. "
        "Return JSON "
        "only, with the exact request in `request`. "
        "The code derives the owner from the op type; do not invent a route.\n\n"
        f"Editor request: {request}\n"
        f"Project folder: {project_folder}\n"
        f"Reel scope supplied by editor: {reel or '(not supplied)'}\n"
        "Read the project's existing transcript, available_reels, semantic "
        "analysis and brand declarations before resolving anchors. Those "
        "facts are supplied in context. Decide only from those facts; "
        "where they do not resolve the referent, ask."
    )
    context = json.dumps(facts, ensure_ascii=False)
    payload = llm_handshake.build_request(
        step_id, prompt, "", context, _expected_schema(), project_folder,
        timestamp, kind="edit_spec")
    path = Path(llm_handshake.request_path(project_folder, step_id))
    response = Path(llm_handshake.response_path(project_folder, step_id))
    if path.exists() or response.exists():
        raise EditSpecError(
            f"handshake id collision for {step_id}: {path} or {response} exists")
    layout = ProjectLayout(project_folder)
    layout.write_dir(Area.LLM_REQUESTS)
    layout.write_dir(Area.LLM_RESPONSES)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return step_id, str(path), note_id


def load_response_spec(project_folder: str, request_id: str) -> dict:
    from library.tools import llm_handshake

    path = Path(llm_handshake.response_path(project_folder, request_id))
    if not path.is_file():
        raise EditSpecError(
            f"edit spec response is not ready: {path}; answer the matching "
            "LLM_REQUEST_READY file, then rerun `ren spec resolve`.")
    try:
        response = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditSpecError(f"cannot read edit spec response {path}: {exc}") from exc
    if not isinstance(response, dict):
        raise EditSpecError(f"edit spec response {path} must be a JSON object")
    request_path = Path(llm_handshake.request_path(
        project_folder, request_id))
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise TypeError("request must be a JSON object")
        context = request.get("context") or "{}"
        if isinstance(context, str):
            context = json.loads(context)
        if not isinstance(context, dict):
            raise TypeError("request context must be a JSON object")
    except (OSError, ValueError, AttributeError) as exc:
        raise EditSpecError(
            f"cannot read edit spec request {request_path}: {exc}") from exc
    source_note_id = context.get("source_note_id", "")
    if response.get("source_note_id", source_note_id) != source_note_id:
        raise EditSpecError(
            "response source_note_id differs from the note id in its request")
    if source_note_id:
        response["source_note_id"] = source_note_id
    response["available_reels"] = context.get("available_reels", [])
    return validate_spec(response)


def pending_note_states(project_folder: str, rows: list | None = None,
                        include_untranslated: bool = True) -> dict:
    """Marker notes that must not reach edit planning yet.

    A linked note stays out while translation is unanswered, asks a
    question, is malformed, or has not reached the ledger. The runner also
    holds collected natural-language notes before they have a translation.
    A complete ledger row set releases a note to op-type routing;
    `include_untranslated=False` lets `ren notes` keep its old route as a
    diagnostic without allowing the pipeline to execute that guess. A note
    the current edit answers comes back with state `answered` in both
    modes (`marker_resolution.answered_release`): released from the hold,
    and kept out of planning.
    """
    from library.tools import llm_handshake, marker_routing

    root = Path(project_folder)
    request_dir = root / llm_handshake.REQUESTS_SUBDIR
    existing_rows = (edit_ledger.load_rows(project_folder)
                     if rows is None else rows)
    latest: dict[str, tuple[str, str, Path]] = {}
    pending = {}
    request_paths = (sorted(request_dir.glob("edit_spec_*.json"))
                     if request_dir.is_dir() else [])
    for path in request_paths:
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            context = request.get("context") or "{}"
            if isinstance(context, str):
                context = json.loads(context)
            if not isinstance(context, dict):
                raise ValueError("request context must be a JSON object")
        except (OSError, ValueError, AttributeError) as exc:
            request_id = path.stem
            pending[f"unlinked:{request_id}"] = {
                "request_id": request_id,
                "reason": f"edit spec request needs repair: {exc}",
                "questions": [],
            }
            continue
        note_id = context.get("source_note_id")
        if not isinstance(note_id, str) or not note_id:
            request_id = path.stem
            pending[f"unlinked:{request_id}"] = {
                "request_id": request_id,
                "reason": "edit spec request has no linked marker note id",
                "questions": [],
            }
            continue
        candidate = (str(request.get("timestamp", "")), path.name, path)
        if note_id not in latest or candidate[:2] > latest[note_id][:2]:
            latest[note_id] = candidate

    for note_id, (_stamp, _name, request_path) in latest.items():
        request_id = request_path.stem
        response_path = Path(llm_handshake.response_path(
            project_folder, request_id))
        if not response_path.is_file():
            pending[note_id] = {
                "request_id": request_id,
                "reason": "edit spec translation is waiting for the host",
                "questions": [],
            }
            continue
        try:
            spec = load_response_spec(project_folder, request_id)
            questions = questions_for_spec(spec)
        except (OSError, ValueError) as exc:
            pending[note_id] = {
                "request_id": request_id,
                "reason": f"edit spec response needs repair: {exc}",
                "questions": [],
            }
            continue
        if questions:
            pending[note_id] = {
                "request_id": request_id,
                "reason": "edit spec has unresolved referents",
                "questions": questions,
            }
            continue
        recorded = [row for row in existing_rows
                    if row.get("source_note_id") == note_id]
        if not _matches_recorded_rows(spec, recorded):
            expected = ledger_rows(spec)
            expected_keys = {edit_ledger._ledger_key(row)
                             for row in expected}
            current_by_key = {edit_ledger._ledger_key(row): row
                              for row in existing_rows}
            superseded = bool(expected)
            if any(edit_ledger._ledger_key(row) not in expected_keys
                   for row in recorded):
                superseded = False
            for expected_row in expected:
                current = current_by_key.get(
                    edit_ledger._ledger_key(expected_row))
                if current is None:
                    superseded = False
                    break
                if (_normalise_recorded_row(current)
                        == _normalise_recorded_row(expected_row)):
                    continue
                current_note_id = current.get("source_note_id")
                current_request = latest.get(current_note_id)
                if (current_note_id == note_id
                        or (current_request
                            and current_request[0] <= _stamp)):
                    superseded = False
                    break
            if superseded:
                pending[note_id] = {
                    "state": "superseded",
                    "request_id": request_id,
                    "reason": (
                        "this note's ledger operation was superseded by a "
                        "later declared edit"),
                    "questions": [],
                }
                continue
            pending[note_id] = {
                "request_id": request_id,
                "reason": ("resolved edit spec has not been recorded "
                           "exactly in the ledger"),
                "questions": [],
            }

    # A note the current edit already answers, proven by a check the
    # project measures itself (`marker_resolution.answered_release`), is
    # held out of planning without a typed edit, in either mode. One whose
    # answered record no longer holds is pending again, saying why.
    from library.tools import marker_resolution

    linked_ids = set(latest)
    recorded_ids = {row.get("source_note_id") for row in existing_rows
                    if row.get("source_note_id")}
    for pull_path, payload in marker_routing._pull_payloads(project_folder):
        timeline = payload.get("timeline", "")
        for raw in payload.get("notes", []):
            note_id = marker_routing.edit_link_id(
                raw, timeline, str(pull_path))
            if note_id in linked_ids or note_id in recorded_ids:
                continue
            request_value = raw.get("text") or raw.get("note") or ""
            if (not isinstance(request_value, str)
                    or not request_value.strip()):
                continue
            answered = marker_resolution.answered_release(
                project_folder, raw, timeline, str(pull_path))
            if answered and answered[0]:
                pending[note_id] = {"state": "answered", "request_id": "",
                                    "reason": answered[1], "questions": []}
                continue
            if not include_untranslated:
                continue
            request = request_value.strip()
            command = " ".join((
                "ren spec prepare",
                shlex.quote(os.path.abspath(project_folder)),
                "--note-id", shlex.quote(note_id),
                "--request", shlex.quote(request)))
            reason = (
                "this collected natural-language note has not been "
                "translated into typed operations; keyword routing "
                "is not used to plan an edit")
            if answered:
                reason += "; " + answered[1]
            pending[note_id] = {
                "request_id": "",
                "reason": reason,
                "questions": [],
                "prepare_command": command,
            }
    return pending


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren spec",
        description="Translate, clarify and record a natural-language edit spec.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="request a host translation")
    prepare.add_argument("project", help="project folder path")
    prepare.add_argument("--request", required=True,
                         help="editor's natural-language request")
    prepare.add_argument("--reel", default="",
                         help="exact reel timeline name, if stated")
    prepare.add_argument("--note-id", default="",
                         help="link a collected timeline note to its typed ops")
    resolve = sub.add_parser("resolve", help="clarify and record a translation")
    resolve.add_argument("project", help="project folder path")
    resolve.add_argument("--id", required=True, help="request id from prepare")
    intent = sub.add_parser(
        "intent", help="inspect built timeline and export against each op")
    intent_sub = intent.add_subparsers(dest="intent_command", required=True)
    intent_prepare = intent_sub.add_parser(
        "prepare", help="ask the host to inspect built artifacts")
    intent_prepare.add_argument("project", help="project folder path")
    intent_prepare.add_argument("--id", required=True,
                                help="recorded edit-spec request id")
    intent_prepare.add_argument("--timeline-readback", required=True,
                                help="readback file for the built timeline")
    intent_prepare.add_argument("--export", required=True,
                                help="actual rendered export file")
    intent_resolve = intent_sub.add_parser(
        "resolve", help="validate the host's measurements")
    intent_resolve.add_argument("project", help="project folder path")
    intent_resolve.add_argument("--id", required=True,
                                help="intent request id from prepare")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            request_id, path, note_id = prepare_request(
                args.project, args.request, reel=args.reel,
                note_id=args.note_id)
            print(f"LLM_REQUEST_READY: {path}")
            print(f"edit spec id: {request_id}")
            print(f"linked note id: {note_id}")
            return 0
        if args.command == "resolve":
            spec = load_response_spec(args.project, args.id)
            questions = questions_for_spec(spec)
            if questions:
                print(json.dumps({"status": "needs_clarification",
                                  "questions": questions}, indent=2,
                                 ensure_ascii=False))
                return 4
            recorded = record_spec(args.project, spec)
            routes = []
            rerun_steps = []
            for clause, (_row, action) in zip(spec["clauses"], recorded):
                op = clause["op"]
                owner = owner_for_op(op)
                replayed_directly = (
                    op in edit_ledger.REPLAYED_OPS
                    or op == "transform_override")
                if not replayed_directly:
                    rerun_steps.append(owner)
                routes.append({
                    "clause_id": clause["id"],
                    "op": op,
                    "owner": owner,
                    "ledger_action": action,
                    "next": ("ren build replays this direct edit"
                             if replayed_directly else
                             f"ren edit <project> --rerun {owner}, "
                             "then ren build <project>"),
                })
            print(json.dumps({
                "status": "recorded",
                "routes": routes,
                "rerun_steps": list(dict.fromkeys(rerun_steps)),
                "proxy_preview": PROXY_PREVIEW,
            }, indent=2, ensure_ascii=False))
            return 0
        if args.command == "intent":
            if args.intent_command == "prepare":
                request_id, path = prepare_intent(
                    args.project, args.id, args.timeline_readback,
                    args.export)
                print(f"LLM_REQUEST_READY: {path}")
                print(f"edit intent id: {request_id}")
                return 0
            if args.intent_command == "resolve":
                result = resolve_intent(args.project, args.id)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0 if result["status"] == "passed" else 4
    except (OSError, ValueError) as exc:
        print(f"ren spec: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
