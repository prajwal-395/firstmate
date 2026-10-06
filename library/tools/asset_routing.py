"""Generative media and tool routing: the contract and its decision record.

Spec section 9 (capability 14): the edit constantly needs things that
do not exist as placed - a missing establishing shot, a transition
frame, room tone, a sound effect, a texture, a graphic, B-roll, a few
frames at a clip boundary. The routing layer tries, in order:

    existing assets -> transformation -> generative media

and records the decision: what was needed, which tier answered, which
handler or provider, and the provenance of the inserted asset.

This module is the CONTRACT and the decision record. Tiers register
handlers here - the transformation tier, then the generator provider
interface - so the router imports no provider and no provider is
hardcoded. A need no tier can solve is recorded as ``answered_by: None``
with every tier's reason on the record: an asset inserted without a
routing record is an asset whose provenance nobody can state, which is
the defect this module exists to remove.

A handler is a callable ``(need, context) -> outcome`` where ``context``
carries ``project_folder`` and ``step_id``. An outcome is a dict:

    {"handler": name, "solved": bool,
     "artifact": {"path", "media_type"} | None,
     "provenance": {"producer", "method", "derived_from", "detail"} | None,
     "reason": str | None, "refusal": dict | None}

``artifact`` and ``provenance`` are set when ``solved`` is true;
``reason`` says why a tier could not answer. A handler that refuses
raises ``RenRefusal`` and the router records it as the tier's outcome
with ``refusal`` carrying ``to_dict()`` - the refusal reaches the
decision record by name instead of aborting the routing.
"""
from __future__ import annotations

import hashlib
import json
import os

from library.tools.ren_refusal import RenRefusal

#: The needs the routing layer answers, one enumeration. Spec section 9:
#: "missing establishing shot, transition frame, room tone, sound
#: effect, texture, graphic, B-roll, or a few frames at a clip
#: boundary", plus the two the edit reaches for constantly.
NEED_KINDS = frozenset({
    "b_roll",
    "establishing_shot",
    "graphic",
    "image",
    "missing_frames",
    "music",
    "room_tone",
    "sound_effect",
    "texture",
    "transition_frame",
    "voiceover",
})

#: The tiers, in the order the router tries them. Existing assets
#: first: an asset the project already holds is measured, free and
#: rights-clear. Transformation second: the need is met by changing
#: what exists. Generative media last: nothing held or transformable
#: can answer, so a provider is asked to make it.
ROUTE_TIERS = ("existing_asset", "transformation", "generative")

#: Where a decision record's ledger lives, relative to the project's
#: output root. One JSON object per line, appended in routing order.
LEDGER_RELPATH = os.path.join("routing", "decisions.jsonl")

#: Provenance methods, aligned with the provenance ledger's ArtifactRecord.
METHOD_OBSERVED = "OBSERVED"
METHOD_DECLARED = "DECLARED"


class NeedRefused(RenRefusal):
    """A need naming a kind outside the routing vocabulary."""


class Need:
    """One thing the edit requires that is not placed.

    ``kind`` names the vocabulary entry; ``params`` carries what the
    tier handlers need to attempt the need (a fill length, a source
    file, a span). Params are kind-specific and unvalidated here - a
    handler that cannot use them says so in its outcome.
    """

    def __init__(self, kind: str, params: dict | None = None) -> None:
        if kind not in NEED_KINDS:
            raise NeedRefused(
                f"the routing layer was asked for {kind!r}, which is not "
                f"a need it can route",
                "the need vocabulary is the contract every tier handler "
                "is written against; an unlisted kind would route to "
                "handlers that were never told it exists.",
                f"use one of: {', '.join(sorted(NEED_KINDS))}",
            )
        self.kind = kind
        self.params = dict(params or {})

    def to_dict(self) -> dict:
        return {"kind": self.kind, "params": self.params}


#: tier name -> {handler name -> callable}. Handlers register here so the
#: router stays free of any tier's imports.
_HANDLERS: dict[str, dict] = {tier: {} for tier in ROUTE_TIERS}


def register_handler(tier: str, name: str, handler) -> None:
    """Register ``handler`` under ``name`` for ``tier``.

    Registering a second handler under the same name replaces the first:
    a tier with two handlers of one name is a bug the registry would
    otherwise hide.
    """
    if tier not in _HANDLERS:
        raise NeedRefused(
            f"a handler was registered for tier {tier!r}, which is not "
            f"a routing tier",
            "the tier order is the contract - a handler on an unlisted "
            "tier would never be consulted and would look wired.",
            f"register on one of: {', '.join(ROUTE_TIERS)}",
        )
    _HANDLERS[tier][name] = handler


def registered_handlers(tier: str) -> dict:
    """The handlers registered for ``tier``, name -> callable."""
    return dict(_HANDLERS.get(tier, {}))


def decision_id(need: Need) -> str:
    """A stable id for a need: the same need routes to the same id.

    Derived from the kind and the canonical params, so a rerun of the
    same need matches the earlier decision record instead of looking
    like a new decision.
    """
    canonical = json.dumps(need.params, sort_keys=True,
                           separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
    return f"route:{need.kind}:{digest}"


def route_need(need: Need, context: dict | None = None) -> dict:
    """Walk the tiers in order; the first solution wins.

    Every consulted tier's outcome is recorded, solved or not, so the
    record states what each tier said - a record that only named the
    winner would not show the routing happened. Tiers below the winner
    are marked not consulted, never silently skipped. A need no tier
    can solve returns ``answered_by: None`` with every reason on the
    record; the router does not raise for an unsolvable need, because
    "nothing could answer" is a routing outcome the caller must see.
    """
    context = dict(context or {})
    record = {
        "decision_id": decision_id(need),
        "need": need.to_dict(),
        "context": {
            "project_folder": context.get("project_folder"),
            "step_id": context.get("step_id"),
        },
        "tiers": [],
        "answered_by": None,
        "artifact": None,
        "provenance": None,
    }
    for tier in ROUTE_TIERS:
        if record["answered_by"] is not None:
            record["tiers"].append({
                "tier": tier,
                "consulted": False,
                "outcomes": [],
                "reason": f"not consulted: answered by "
                          f"{record['answered_by']}, a lower tier",
            })
            continue
        tier_record = {"tier": tier, "consulted": True, "outcomes": []}
        for name, handler in _HANDLERS[tier].items():
            tier_record["outcomes"].append(
                _consult(name, handler, need, context))
        solved = next((o for o in tier_record["outcomes"] if o["solved"]),
                      None)
        tier_record["answered"] = solved is not None
        record["tiers"].append(tier_record)
        if solved is not None:
            record["answered_by"] = tier
            record["artifact"] = solved["artifact"]
            record["provenance"] = solved["provenance"]
    return record


def _consult(name: str, handler, need: Need, context: dict) -> dict:
    """Ask one handler, recording its outcome or its refusal."""
    try:
        outcome = handler(need, context)
    except RenRefusal as refusal:
        return {"handler": name, "solved": False, "artifact": None,
                "provenance": None, "reason": refusal.what,
                "refusal": refusal.to_dict()}
    if (not isinstance(outcome, dict)
            or not isinstance(outcome.get("solved"), bool)
            or not isinstance(outcome.get("handler"), str)):
        raise NeedRefused(
            f"the {name!r} handler returned an outcome that is not the "
            f"outcome contract",
            "every handler returns {handler, solved, artifact, "
            "provenance, reason, refusal}; an outcome of another shape "
            "would be recorded as something it is not.",
            "fix the handler to return the outcome contract.",
        )
    return outcome


def ledger_path(project_folder: str) -> str:
    """The decision ledger's path for a project."""
    return os.path.join(project_folder, "pipeline_output", LEDGER_RELPATH)


def append_decision(project_folder: str, record: dict) -> str:
    """Append one decision record to the project's routing ledger.

    Returns the record's path. The ledger is append-only: a decision
    once routed is part of the project's history, and rewriting it
    would destroy the provenance of every asset inserted since.
    """
    path = ledger_path(project_folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def read_decisions(project_folder: str) -> list:
    """Read the project's routing ledger, oldest first.

    An absent ledger is an empty list, not an error: a project that has
    never routed a need has no decisions to read.
    """
    path = ledger_path(project_folder)
    if not os.path.isfile(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
