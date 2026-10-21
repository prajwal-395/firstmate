"""PLAN-010: the QUERY that tells an agent what it may do, and what it costs.

The punch list asks (PLAN-010) to make the available ACTIONS discoverable:
"let the agent query the currently supported operations and their
constraints", "present the smallest relevant schema and examples", "show
known preservation limits and expected verification", "return actionable
refusal categories and recovery options", and "do not require the model to
memorize the full 84-operation inventory".

WHAT WAS ALREADY THERE, AND WHAT WAS MISSING.

The registry is not the problem.  `edit_algebra.OP_STATUS` holds every
operation with its semantics, its backends, its qualification and its
preservation limits; `operation_map` publishes the canonical record;
`capability_coverage` (BASE-002) answers "may this run here"; and
`resolve-axi capabilities` answers "what does this operation mean" and
"search the registry by name".  A second catalog would be a defect, so
this module declares NO operation, NO backend, NO qualification and NO
route: every fact in an action card is read from those registries.

What was missing is the QUERY AN AGENT MAKES.  Two things were true and
both of them cost a brief:

* the only searchable surface matched NAMES (`capabilities --search`
  scores the operation id, its domain and its note), so an agent asking
  in the language of the EDIT - "hold the name card until she finishes",
  "take the soft focus card out" - got a ranking over technical
  vocabulary and no way to tell a routable operation from a declared
  one;
* the answers that existed were WHOLE-OPERATION answers (the 141 KB of
  generated reference tables, the operation map, the coverage report).
  Nothing answered "what does this action PRESERVE, how is it VERIFIED,
  and what does it refuse with" in one place.

So this module is ONE query surface over the registries:

    actions("<effect>")   the SUPPORTED operations that match an effect,
                          each with its smallest relevant card
    actions()             the compact menu of every supported operation

and the card carries the five things PLAN-010 names: the schema and a
worked example, the preservation limits, the expected verification, the
refusal categories with their recovery options, and how to request it.

THE SUPPORTED MENU IS THE POINT.

An operation that ordinary routing may not use is not a choice.  The
menu therefore holds the production-enabled operations - about a fifth of
the inventory - and the operations that are declared but not routable are
reported beside it, each with the blocker that keeps it out and the
supported alternatives in its own domain.  A model reading this no longer
has to remember 84 ids to avoid naming one that cannot run.

EVERYTHING HERE IS DERIVED.  The effect vocabulary is the registry's own
text (the operation id, its domain, its note, its parameter names) plus
the AXI CLI's own help text for the commands that perform it - both
written by the engine, neither written here.  The card's facts are the
operation map's, the coverage report's, the refusal taxonomy's and the
recovery instruction's.  This module joins them and ranks them; it
declares none of them.

    `tests/unit/tools/test_action_discovery.py`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import cache

from library.tools import (
    capability_coverage,
    edit_algebra,
    operation_map,
    plan_feasibility,
    recovery_instruction,
    refusal_taxonomy,
)

#: The schema the query is published under.
SCHEMA = "ren-action-discovery/v1"

#: The support band of an operation ordinary routing may use.
SUPPORTED = "supported"

#: The band of an operation that is declared but not routable in
#: production: it exists, it is implemented, and it may not be used.
DECLARED_NOT_ROUTABLE = "declared_not_routable"

#: Words that carry no editorial meaning, and are therefore evidence of
#: nothing.  English function words, plus the words every brief is
#: wrapped in ("video", "timeline", "clip", "shot") - those name the
#: MEDIUM, not the EFFECT, and every operation lives on a timeline.
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "do", "does", "for", "from", "had", "has", "have", "he", "her", "his",
    "i", "if", "in", "into", "is", "it", "its", "just", "me", "more",
    "my", "no", "not", "of", "on", "one", "or", "our", "out", "over",
    "own", "please", "she", "should", "so", "some", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "too", "up",
    "us", "want", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "will", "with", "would", "you", "your",
    # the medium, not the effect
    "clip", "clips", "shot", "shots", "video", "timeline", "footage",
    "reel", "reels", "thing", "things",
})

#: The shortest term the matcher will look at.  Two letters match too
#: much English to be evidence of anything.
MIN_TERM = 3

#: The number of terms a query keeps.  A brief's instruction is prose; a
#: query is an effect, and the effect is a few words.
MAX_TERMS = 24

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    """The lowercase words of a piece of text."""
    return _WORD.findall(str(text or "").casefold())


def _content_words(text: str) -> list[str]:
    """The words of a text that carry meaning."""
    return [word for word in _words(text)
            if len(word) >= MIN_TERM and word not in STOP_WORDS]


# ── The effect vocabulary, derived from the registry ─────────────────


@cache
def _cli_help() -> dict[str, str]:
    """The AXI CLI's own help text, by command path.

    This is the engine's own description of what each command DOES, in
    the words a person uses ("remove an item and close the gap on its
    track", "narrow an item's source range in place").  It is read from
    the parser the CLI already builds, so it cannot drift from the
    commands - and it is the vocabulary that lets an effect phrased in
    editorial words reach the operation that performs it.
    """
    from library.tools import resolve_axi_core_commands

    parser = resolve_axi_core_commands.build_parser()
    rows: dict[str, str] = {}

    def walk(subparser, prefix: tuple[str, ...]) -> None:
        for action in subparser._actions:
            mapping = getattr(action, "_name_parser_map", None)
            if not mapping:
                continue
            helps = {entry.dest: entry.help
                     for entry in getattr(action, "_choices_actions", ())}
            for name, child in mapping.items():
                path = prefix + (name,)
                rows[" ".join(path)] = str(helps.get(name) or "")
                walk(child, path)

    walk(parser, ())
    return rows


@cache
def _effect_terms() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Every operation's effect vocabulary, read off the registry.

    Two halves, because a NAME and a PROSE are different kinds of
    evidence.  The STRONG half is the operation's own name, its domain
    and the AXI command that performs it: "caption", "style", "trim",
    "position".  The WEAK half is the note, the parameter names and the
    command's help text: prose that mentions the effect, in words that
    also occur in prose about something else.
    """
    help_text = _cli_help()
    verbs = operation_map.axi_verbs()
    terms: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for op_id, spec in edit_algebra.ALGEBRA.items():
        strong: set[str] = set()
        weak: set[str] = set()
        strong.update(_content_words(op_id.replace(".", " ").replace("_", " ")))
        strong.update(_content_words(spec.domain))
        for path in verbs.get(op_id, {}):
            strong.update(_content_words(path))
            weak.update(_content_words(help_text.get(path, "")))
        weak.update(_content_words(spec.note))
        for param in spec.required:
            weak.update(_content_words(param.replace("_", " ")))
        terms[op_id] = (frozenset(strong), frozenset(weak))
    return terms


def effect_terms(op_id: str) -> frozenset[str]:
    """The effect vocabulary of one operation, strong half first."""
    strong, weak = _effect_terms().get(str(op_id), (frozenset(), frozenset()))
    return strong | weak


@cache
def _idf() -> dict[str, float]:
    """How rare a term is across the registry's effect vocabularies.

    A term that every operation's vocabulary carries ("picture", "clip")
    says nothing about which operation an effect names; a term only one
    carries ("caption", "matte") says a great deal.  The weights are
    computed from the registry, so they cannot be a hand-set preference.
    """
    vocabularies = _effect_terms()
    total = max(len(vocabularies), 1)
    document_frequency: dict[str, int] = {}
    for strong, weak in vocabularies.values():
        for word in strong | weak:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    return {word: math.log((total + 1) / (count + 0.5))
            for word, count in document_frequency.items()}


def _matches(term: str, words: Iterable[str]) -> str:
    """The vocabulary word a query term names, if it names one.

    Prefix matching, in both directions, because the registry writes
    `caption` and a brief writes `captions`, and the registry writes
    `trim` and a brief writes `trimmed`.  The short direction is
    guarded: a three-letter query term is not evidence against a long
    vocabulary word, but a long query term may match a short one.

    The LONGEST match is returned: "open" names "opening", and the
    weight of the answer is the weight of the word that was matched,
    not of the word the caller typed.
    """
    best = ""
    for word in words:
        if term == word:
            matched = True
        elif len(word) >= 4 and term.startswith(word):
            matched = True
        elif len(term) >= 4 and word.startswith(term):
            matched = True
        else:
            matched = False
        if matched and len(word) > len(best):
            best = word
    return best


# ── The card ─────────────────────────────────────────────────────────


def _example_value(param: str) -> object:
    """A placeholder value for one required parameter.

    Derived from the PARAMETER'S NAME, not from the operation: the point
    is to show the shape of a request, and a shape with a placeholder in
    it is a shape the caller can fill.
    """
    name = str(param)
    if name.endswith("_id") or name == "id":
        return "<the item's unique id>"
    if name.endswith("_frame") or name == "frame":
        return 0
    if name.endswith("_file") or name.endswith("_path"):
        return "<the pool item>"
    if name.endswith("_name"):
        return "name"
    if name.endswith("_in") or name.endswith("_out"):
        return 0
    if name.endswith("index") or name.endswith("_count"):
        return 0
    if name in ("text",):
        return "the corrected words"
    if name in ("style",):
        return "default"
    if name in ("db", "pan", "percent", "value"):
        return 0
    if name in ("key", "param"):
        return "Tilt"
    if name in ("enabled",):
        return True
    return f"<{name}>"


def example_request(op_id: str) -> dict:
    """A worked example of the request one operation takes."""
    spec = edit_algebra.ALGEBRA.get(str(op_id))
    if spec is None:
        return {}
    request: dict = {"op": op_id}
    for param in spec.required:
        request[param] = _example_value(param)
    return request


def _refusal_rows(op_id: str, *, routable: bool) -> list[dict]:
    """The refusal categories that can apply to one operation.

    The blockers are the taxonomy's own six; the next action each one
    demands is the taxonomy's own table; and the checks that raise them
    are the feasibility gate's own checks.  Nothing here is written per
    operation: the operation decides WHICH of them apply by what it
    needs, and the gate decides whether they fired.
    """
    rows: list[dict] = []
    for blocker in refusal_taxonomy.BLOCKERS:
        checks = sorted(
            check for check, (blocker_name, _) in
            plan_feasibility.CHECK_BLOCKERS.items()
            if blocker_name == blocker)
        rows.append({
            "blocker": blocker,
            "plain": refusal_taxonomy.BLOCKER_PLAIN[blocker],
            "next_action": refusal_taxonomy.BLOCKER_NEXT_ACTION[blocker],
            "raised_by": checks,
            "applies_now": (blocker == "unsupported_operation"
                            and not routable),
        })
    return rows


def _recovery_rows() -> list[dict]:
    """The recovery options, one per execution failure category."""
    rows: list[dict] = []
    for category in sorted(recovery_instruction.CATEGORY_RECOVERY_TYPE):
        instruction = recovery_instruction.instruction(category)
        rows.append({
            "category": category,
            "recovery_class": instruction["recovery_class"],
            "action": instruction["action"],
            "reason": instruction["reason"],
        })
    return rows


@cache
def _routable_ids() -> tuple[str, ...]:
    """The operations ordinary production routing may use."""
    return tuple(sorted(op_id for op_id, status in edit_algebra.OP_STATUS.items()
                        if status.production_enabled))


@cache
def _priority() -> dict[str, float]:
    """How often the shipped pipeline asks for each operation.

    Read from the coverage report's own priority measure, which counts
    the production call sites that construct the operation.  It is the
    tie-breaker that puts the operations the editorial loop actually uses
    first, and it is measured rather than asserted.
    """
    report = capability_coverage.coverage_report()
    scores: dict[str, float] = {}
    for row in report.get("operations") or ():
        if not isinstance(row, Mapping):
            continue
        priority = row.get("priority") or {}
        scores[str(row.get("id"))] = float(priority.get("priority_score") or 0)
    return scores


def card(op_id: str) -> dict:
    """The action card of one operation.

    The five things PLAN-010 names, in one record: the smallest
    relevant schema and a worked example, the known preservation limits,
    the expected verification, the refusal categories with their
    recovery options, and how the operation is requested.
    """
    status = edit_algebra.status(str(op_id))
    spec = edit_algebra.ALGEBRA.get(str(op_id))
    if status is None or spec is None:
        raise KeyError(f"unknown operation {op_id!r}")
    routable = status.production_enabled
    entry = operation_map.operation_entry(
        op_id,
        declared=operation_map.declared_coverage(),
        inventory=operation_map.implemented_coverage(),
        verbs=operation_map.axi_verbs(),
        exposure=operation_map.backend_exposure())
    preservation_limits = sorted({
        limit
        for backend in status.backends
        for limit in backend.preservation_limits})
    limitations = sorted({
        limit
        for backend in status.backends
        for limit in backend.limitations})
    visual = sorted({
        item
        for backend in status.backends
        for item in backend.visual_verification})
    audio = sorted({
        item
        for backend in status.backends
        for item in backend.audio_verification})
    domain = status.state_domains[0] if status.state_domains else spec.domain
    alternatives = sorted(
        other for other in _routable_ids()
        if other != op_id
        and edit_algebra.status(other).state_domains
        and edit_algebra.status(other).state_domains[0] == domain)
    return {
        "id": op_id,
        "effect": spec.note or f"{op_id} operation.",
        "domain": domain,
        "support_level": operation_map.support_level(status),
        "routable": routable,
        "schema": {
            "required": list(status.param_schema.get("required") or ()),
            "optional": sorted(
                name for name in (status.param_schema.get("properties") or {})
                if name not in set(status.param_schema.get("required") or ())),
        },
        "example": example_request(op_id),
        "preserves": {
            "safety_class": status.safety_class,
            "limits": preservation_limits,
            "limitations": limitations,
            "unsupported_native_state": list(status.unsupported_native_state),
        },
        "verifies": {
            "contract": status.verification_contract,
            "visual": visual,
            "audio": audio,
        },
        "refusals": _refusal_rows(op_id, routable=routable),
        "recovery": {
            "inverse": dict(status.inverse_recovery),
            "proven_backends": list(
                entry["how_to_request"].get("routable_backends") or ()),
        },
        "how_to_request": {
            "edit_request_routable": bool(
                entry["how_to_request"].get("edit_request_routable")),
            "commands": list(entry["how_to_request"].get("commands") or ()),
            "supported_states": {
                "media_types": list(status.media_types),
                "timeline_conditions": list(status.timeline_conditions),
            },
        },
        "alternatives": alternatives,
    }


#: How much a weak (prose) match is worth against a strong (named) one.
WEAK_WEIGHT = 0.35


def _score(query_terms: Sequence[str], op_id: str) -> tuple[float, bool, float, list[str]]:
    """How well an operation names the requested effect.

    Four things come back: the combined score, whether the operation's
    NAME named the effect at all, the weak (prose) part of the score on
    its own, and the terms that matched.  Prose that mentions an effect
    is evidence for the ORDER of the candidates, never for their
    membership: a note that happens to contain the word "everything" does
    not make an operation the answer to a brief that says "everything".
    """
    strong, weak = _effect_terms().get(op_id, (frozenset(), frozenset()))
    weights = _idf()
    matched: list[str] = []
    named = False
    score = 0.0
    weak_score = 0.0
    for term in query_terms:
        word = _matches(term, strong)
        if word:
            matched.append(term)
            named = True
            score += weights.get(word, 0.0)
            continue
        word = _matches(term, weak)
        if word:
            matched.append(term)
            weak_score += weights.get(word, 0.0)
            score += WEAK_WEIGHT * weights.get(word, 0.0)
    return score, named, weak_score, matched


def discover(request: str, *, limit: int = 5) -> dict:
    """The supported operations that answer a requested effect.

    The menu holds the operations ordinary routing may use.  Those whose
    NAME names the effect come first; the rest follow in the order the
    editorial loop asks for them, so a caller is never left with an
    empty answer.  The operations that are declared but not routable are
    reported beside the menu, each with the blocker that keeps it out
    and the supported operations in its own domain, so a caller learns
    the gap rather than naming an operation that cannot run.
    """
    terms = _content_words(request)[:MAX_TERMS]
    priority = _priority()
    scored: list[tuple[float, float, float, float, str, list[str]]] = []
    for op_id in _routable_ids():
        score, named, weak_score, matched = _score(terms, op_id)
        scored.append((score, 1.0 if named else 0.0, weak_score,
                       priority.get(op_id, 0.0), op_id, matched))
    named_rows = [row for row in scored if row[1] > 0.0]
    named_rows.sort(key=lambda row: (-row[0], -row[3], row[4]))
    rest = sorted((row for row in scored if row[1] <= 0.0),
                  key=lambda row: (-row[2], -row[3], row[4]))
    menu = (named_rows + rest)[:max(int(limit), 1)]

    not_routable: list[dict] = []
    for op_id in sorted(edit_algebra.OP_STATUS):
        status = edit_algebra.status(op_id)
        if status.production_enabled:
            continue
        score, _named, _weak, matched = _score(terms, op_id)
        if score <= 0.0:
            continue
        domain = (status.state_domains[0] if status.state_domains
                  else edit_algebra.ALGEBRA[op_id].domain)
        alternatives = sorted(
            other for other in _routable_ids()
            if other != op_id
            and edit_algebra.status(other).state_domains
            and edit_algebra.status(other).state_domains[0] == domain)
        not_routable.append({
            "id": op_id,
            "effect": edit_algebra.ALGEBRA[op_id].note or f"{op_id} operation.",
            "support_level": operation_map.support_level(status),
            "blocker": "unsupported_operation",
            "next_action": refusal_taxonomy.BLOCKER_NEXT_ACTION[
                "unsupported_operation"],
            "matched_terms": matched,
            "score": round(score, 4),
            "supported_alternatives": alternatives,
        })
    not_routable.sort(key=lambda row: (-row["score"], row["id"]))
    not_routable = not_routable[:max(int(limit), 1)]

    return {
        "schema": SCHEMA,
        "query": str(request),
        "terms": terms,
        "effect_match": bool(named_rows),
        "supported_count": len(_routable_ids()),
        "total_operations": len(edit_algebra.OP_STATUS),
        "matches": [{
            "id": op_id,
            "score": round(score, 4),
            "matched_terms": matched,
            "card": card(op_id),
        } for score, _named, _weak, _priority_score, op_id, matched in menu],
        "not_routable": not_routable,
        "refusal_catalog": [
            {
                "blocker": blocker,
                "plain": refusal_taxonomy.BLOCKER_PLAIN[blocker],
                "next_action": refusal_taxonomy.BLOCKER_NEXT_ACTION[blocker],
            }
            for blocker in refusal_taxonomy.BLOCKERS
        ],
        "recovery_options": _recovery_rows(),
    }


def catalog() -> dict:
    """The compact menu of every supported operation.

    This is the answer to "do not require the model to memorize the full
    84-operation inventory": the supported operations, one line each,
    with the parameters they take and whether they can be requested as a
    semantic edit.
    """
    rows = []
    for op_id in _routable_ids():
        status = edit_algebra.status(op_id)
        spec = edit_algebra.ALGEBRA[op_id]
        rows.append({
            "id": op_id,
            "effect": spec.note or f"{op_id} operation.",
            "domain": status.state_domains[0] if status.state_domains
            else spec.domain,
            "required": list(status.param_schema.get("required") or ()),
            "verified_by": status.verification_contract,
            "preserves": sorted({
                limit
                for backend in status.backends
                for limit in backend.preservation_limits}),
        })
    return {
        "schema": SCHEMA,
        "supported_count": len(rows),
        "total_operations": len(edit_algebra.OP_STATUS),
        "operations": rows,
    }
