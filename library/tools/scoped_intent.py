"""Resolve persistent creative declarations into a reproducible prompt view.

Declarations are immutable facts with an explicit scope and provenance. Explicit
creator direction outranks brand direction, which outranks project direction;
inferred observations are evidence only and never silently become preferences.
Only explicit taste is eligible for cross-project use.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

VERSION = 1
PRECEDENCE = {"creator": 0, "brand": 1, "project": 2, "inference": 3}


class IntentViewError(ValueError):
    """A declaration cannot safely participate in an intent view."""


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise IntentViewError(f"invalid intent expiry {value!r}") from exc
    if parsed.tzinfo is None:
        raise IntentViewError("intent expiry must include a timezone")
    return parsed


def declaration(
    key,
    value,
    *,
    source,
    scope,
    provenance,
    confidence=1.0,
    expires_at=None,
    explicit=False,
    corrected_from=None,
):
    """Create the canonical declaration record used by stores and fixtures."""
    if source not in PRECEDENCE:
        raise IntentViewError(f"unknown intent source {source!r}")
    if not key or not scope or not provenance:
        raise IntentViewError("intent key, scope and provenance are required")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise IntentViewError("intent confidence must be between 0 and 1")
    if source == "inference" and explicit:
        raise IntentViewError("an inference cannot be explicit creator preference")
    return {
        "key": str(key),
        "value": value,
        "source": source,
        "scope": str(scope),
        "provenance": str(provenance),
        "confidence": float(confidence),
        "expires_at": expires_at,
        "explicit": bool(explicit),
        "corrected_from": corrected_from,
    }


def resolve(declarations, *, project_id, now=None):
    """Resolve active declarations; return winners and a complete audit trail.

    `project_id` scopes project and inferred observations. Explicit creator
    taste may use `*` and therefore travel across projects. Expired and
    superseded declarations remain in the audit trail but cannot win.
    """
    instant = now or datetime.now(timezone.utc)
    if isinstance(instant, str):
        instant = _date(instant)
    if instant.tzinfo is None:
        raise IntentViewError("resolution time must include a timezone")
    candidates, audit = {}, []
    for row in declarations:
        required = {"key", "value", "source", "scope", "provenance", "confidence"}
        missing = required - row.keys()
        if missing:
            raise IntentViewError(f"intent declaration missing {sorted(missing)}")
        if row["source"] not in PRECEDENCE:
            raise IntentViewError(f"unknown intent source {row['source']!r}")
        if row["source"] == "inference" and row.get("explicit"):
            raise IntentViewError("inference cannot be marked explicit")
        expiry = _date(row.get("expires_at"))
        reason = "eligible"
        scope = row["scope"]
        cross_project = (
            row["source"] == "creator" and row.get("explicit") is True and scope == "*"
        )
        if scope != project_id and not cross_project:
            reason = "out_of_scope"
        elif expiry and expiry <= instant:
            reason = "expired"
        elif row.get("superseded_by"):
            reason = "corrected"
        item = dict(row, resolution=reason)
        audit.append(item)
        if reason == "eligible":
            candidates.setdefault(row["key"], []).append(row)
    winners, conflicts = {}, {}
    for key, rows in sorted(candidates.items()):
        rows.sort(
            key=lambda row: (
                PRECEDENCE[row["source"]],
                -float(row["confidence"]),
                row["provenance"],
            )
        )
        top_rank = PRECEDENCE[rows[0]["source"]]
        top_confidence = float(rows[0]["confidence"])
        top = [
            r
            for r in rows
            if PRECEDENCE[r["source"]] == top_rank
            and float(r["confidence"]) == top_confidence
        ]
        distinct = {
            json.dumps(r["value"], sort_keys=True, ensure_ascii=False) for r in top
        }
        if len(distinct) > 1:
            conflicts[key] = [dict(r) for r in top]
            continue
        winners[key] = dict(rows[0])
    projection = {
        "version": VERSION,
        "project_id": project_id,
        "precedence": ["creator", "brand", "project", "inference"],
        "values": {key: row["value"] for key, row in winners.items()},
        "influenced_by": {
            key: {
                "source": row["source"],
                "provenance": row["provenance"],
                "confidence": row["confidence"],
            }
            for key, row in winners.items()
        },
        "conflicts": conflicts,
        "declarations": audit,
    }
    projection["digest"] = hashlib.sha256(
        json.dumps(
            projection, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return projection


def correct(old, new_value, *, provenance, confidence=1.0):
    """Return a replacement declaration linked to the declaration corrected."""
    revised = declaration(
        old["key"],
        new_value,
        source=old["source"],
        scope=old["scope"],
        provenance=provenance,
        confidence=confidence,
        explicit=old.get("explicit", False),
        corrected_from=old.get("provenance"),
    )
    old_copy = dict(old, superseded_by=provenance)
    return old_copy, revised


def migrate(value):
    """Migrate the pre-view representation to the versioned view envelope."""
    if isinstance(value, list):
        # Version zero was the early flat declaration list. Preserve its
        # contents and attach the fields the resolver now requires.
        return {"version": VERSION, "declarations": [dict(row) for row in value]}
    if not isinstance(value, dict):
        raise IntentViewError(
            "intent view migration requires an object or declaration list"
        )
    version = value.get("version", 0)
    if version == VERSION:
        return dict(value)
    if version != 0:
        raise IntentViewError(f"unsupported intent view version {version!r}")
    migrated = dict(value)
    migrated["version"] = VERSION
    migrated.setdefault("declarations", [])
    return migrated


def from_sources(
    *,
    intent_model=None,
    creative_brief=None,
    learned_context=None,
    taste_profile=None,
    brand=None,
    project_id="",
    now=None,
):
    """Adapt existing representations without changing their stores.

    Structured declarations can be supplied under `declarations`; legacy
    intent_model values are deliberately marked as inference. Brief text is
    provenance and is not guessed into a structured preference.
    """
    declarations = []

    def add_rows(value, source, scope, prefix, explicit=False):
        for row in (value or {}).get("declarations", []):
            declarations.append(
                declaration(
                    row["key"],
                    row["value"],
                    source=source,
                    scope=row.get("scope", scope),
                    provenance=row.get("provenance", prefix),
                    confidence=row.get("confidence", 1),
                    expires_at=row.get("expires_at"),
                    explicit=explicit,
                    corrected_from=row.get("corrected_from"),
                )
            )

    add_rows(intent_model, "inference", project_id, "intent_model")
    add_rows(brand, "brand", project_id, "brand_template")
    add_rows(creative_brief, "creator", project_id, "creative_brief", True)
    add_rows(learned_context, "project", project_id, "learned_context")
    for key, scopes in (taste_profile or {}).get("preferences", {}).items():
        entry = scopes.get("*") or scopes.get(project_id)
        if entry is not None:
            declarations.append(
                declaration(
                    key,
                    entry["value"],
                    source="creator",
                    scope="*" if "*" in scopes else project_id,
                    provenance=f"taste_profile:{key}",
                    confidence=1,
                    explicit=True,
                )
            )
    return resolve(declarations, project_id=project_id, now=now)


def _flatten(mapping, prefix=""):
    """Flatten declared configuration values without interpreting prose."""
    if not isinstance(mapping, dict):
        return
    for key, value in sorted(mapping.items()):
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            yield from _flatten(value, name)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            yield name, value


def for_project(project_folder, node_id, *, state=None, now=None):
    """Read existing project declarations and build the prompt's intent view.

    This is read-only: it adapts the four existing stores and records a stable
    view; corrections continue through their owning store APIs.
    """
    from pathlib import Path
    from library.tools import learned_context, taste_profile

    state = state or {}
    root = Path(project_folder).resolve()
    declarations = []
    for key, value in sorted((state.get("intent_model") or {}).items()):
        if key in ("version", "declarations"):
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            declarations.append(
                declaration(
                    f"intent_model.{key}",
                    value,
                    source="inference",
                    scope=str(root),
                    provenance="pipeline_output/steps/creative_direction/intent_model",
                    confidence=0.6,
                )
            )
    for key, value in _flatten(state.get("project_config") or {}):
        declarations.append(
            declaration(
                key,
                value,
                source="project",
                scope=str(root),
                provenance="project.yaml",
                confidence=1.0,
            )
        )
    brand = state.get("brand_template")
    if isinstance(brand, str) and brand:
        from library.tools.brand_registry import resolve_template_reference
        from dataclasses import asdict

        brand = asdict(resolve_template_reference(brand, project_folder=str(root)))
    if isinstance(brand, dict):
        for key, value in _flatten(brand):
            declarations.append(
                declaration(
                    key,
                    value,
                    source="brand",
                    scope=str(root),
                    provenance="brand template",
                    confidence=1.0,
                )
            )
    brief_path = state.get("creative_brief")
    if brief_path:
        declarations.append(
            declaration(
                "attached_brief",
                {"attached": True, "path": str(brief_path)},
                source="creator",
                scope=str(root),
                provenance="attached creative brief",
                confidence=1.0,
                explicit=True,
            )
        )
    for learning in learned_context._load(str(root)):
        if learning.get("status") != learned_context.ACTIVE:
            continue
        if node_id not in learning.get(
            "read_by", []
        ) and learned_context.GLOBAL not in learning.get("read_by", []):
            continue
        declarations.append(
            declaration(
                f"learning:{learning['id']}",
                learning["statement"],
                source="project",
                scope=str(root),
                provenance=f"learned_context/{learning['id']} ({learning['kind']})",
                confidence=1.0,
            )
        )
    profile = taste_profile._read(taste_profile.profile_path())
    for key, scopes in profile["preferences"].items():
        entry = scopes.get("*")
        if entry is None:
            # Project-specific taste is stored in the existing scoped slots.
            entry = scopes.get(str(root))
        if entry is not None:
            declarations.append(
                declaration(
                    key,
                    entry["value"],
                    source="creator",
                    scope="*" if scopes.get("*") is entry else str(root),
                    provenance=f"taste_profile:{key} ({entry['stated_by']}: {entry['reason']})",
                    confidence=1.0,
                    explicit=True,
                )
            )
    return resolve(declarations, project_id=str(root), now=now)


def prompt_section(view):
    """Stable, compact serialization identifying the declarations in play."""
    body = {
        key: view[key]
        for key in (
            "version",
            "project_id",
            "precedence",
            "values",
            "influenced_by",
            "conflicts",
            "declarations",
            "digest",
        )
    }
    return (
        "--- scoped intent view (declarations and provenance) ---\n"
        + json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2)
        + "\n"
    )
