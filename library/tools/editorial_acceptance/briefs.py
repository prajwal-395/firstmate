"""The unfamiliar-brief acceptance set, and the split the agent never sees.

A brief is a USER'S request, in the user's words. It is not an operation
list, and it is not a fixture key: the agent is handed the instruction
and the state of the project it is working on, and nothing else. The
evaluator holds the answer key - the operations the request implies, the
semantic target roles, the source moments, the edge case the request
hides - and that key never reaches the agent.

Two objects come out of one brief:

- ``public_view(brief)`` is everything the agent may read: the
  instruction, the project state, and the acceptance criteria the result
  will be judged against.
- ``oracle(brief)`` is the evaluator's key: what the request implies, not
  what the request says.

The separation is CHECKED, not assumed. ``assert_no_leak`` refuses a
public view that carries an oracle key or an oracle value, so a brief
that leaks the answer key fails here rather than producing a flattering
score.

The set also states what it covers, so a gap is visible instead of
implied: the edge cases a brief hides (overlapping speech, sparse
footage, an unavailable asset, a self-contradicting request), the
pre-existing human adjustments a brief must survive, and the imperfect
media it is working against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

BRIEF_SET_SCHEMA = "ren-editorial-brief-set/1"

#: The edge cases a brief may hide. Every one of them must appear in the
#: set: a brief that never stumbles proves nothing about the hard cases.
EDGE_CASES: tuple[str, ...] = (
    "overlapping_speech",
    "sparse_footage",
    "unavailable_asset",
    "conflicting_instruction",
)

#: The keys of a brief the agent is allowed to read.
PUBLIC_KEYS: tuple[str, ...] = (
    "id",
    "title",
    "instruction",
    "project_state",
    "acceptance_criteria",
)

#: The keys of a brief the evaluator holds.
ORACLE_KEYS: tuple[str, ...] = (
    "edge_case",
    "expected_refusal",
    "expected_operations",
    "expected_target_roles",
    "expected_moment_ids",
    "allowed_operations",
    "footage_requirements",
    "notes",
)

#: The keys of the project state the agent may read.
PROJECT_STATE_KEYS: tuple[str, ...] = (
    "footage_profile",
    "existing_adjustments",
    "media_conditions",
)

#: A brief whose instruction is this similar to a corpus request is a
#: copy, not an unfamiliar brief. Measured on normalised word sets.
SIMILARITY_FLOOR = 0.7

BRIEF_SET_PATH = Path(__file__).resolve().parent / "brief_set.json"


class BriefSetError(ValueError):
    """The brief set does not satisfy its own contract."""


def _normalise(text: str) -> str:
    return " ".join(str(text).lower().split())


def _known_operations() -> set[str]:
    """The operation ids the engine actually has.

    An answer key that names an operation nobody implements is a brief
    that can never be passed, and it looks like a failure of the run
    rather than of the key. The check lives here so a brief is refused
    when it is added rather than discovered in a report.
    """
    from library.tools import edit_algebra

    return set(edit_algebra.ALGEBRA)


def _words(text: str) -> set[str]:
    return {word for word in _normalise(text).split() if word}


def brief_set_path() -> Path:
    """Where the shipped acceptance set lives."""
    return BRIEF_SET_PATH


def load_brief_set(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the acceptance set; raise on any contract breach.

    The set is DATA, so it is read from disk rather than imported: a
    brief can be added, or withdrawn, without touching code.
    """
    target = Path(path) if path is not None else BRIEF_SET_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    problems = validate_brief_set(raw)
    if problems:
        raise BriefSetError(
            "the brief set violates its contract:\n  "
            + "\n  ".join(problems))
    raw["path"] = str(target)
    raw["sha256"] = hashlib.sha256(
        target.read_bytes()).hexdigest()
    return raw


def validate_brief_set(raw: Mapping[str, Any]) -> list[str]:
    """Every contract breach in the set, named. Empty means it holds."""
    problems: list[str] = []
    if not isinstance(raw, Mapping):
        return ["the brief set is not an object"]
    from library.tools.editorial_acceptance import footage
    if raw.get("schema") != BRIEF_SET_SCHEMA:
        problems.append(
            f"schema must be {BRIEF_SET_SCHEMA!r}, not "
            f"{raw.get('schema')!r}")
    briefs = raw.get("briefs")
    if not isinstance(briefs, list) or not briefs:
        return problems + ["the brief set declares no briefs"]
    demonstration = raw.get("demonstration_brief")
    if demonstration is not None:
        if not isinstance(demonstration, str) or not demonstration.strip():
            problems.append("demonstration_brief must name a brief")
        elif not any(isinstance(brief, Mapping)
                     and brief.get("id") == demonstration
                     for brief in briefs):
            problems.append(
                f"demonstration_brief names {demonstration!r}, which the set "
                "does not carry")
    seen: set[str] = set()
    for index, brief in enumerate(briefs):
        label = f"briefs[{index}]"
        if not isinstance(brief, Mapping):
            problems.append(f"{label} is not an object")
            continue
        brief_id = brief.get("id")
        if not isinstance(brief_id, str) or not brief_id.strip():
            problems.append(f"{label} has no id")
        else:
            label = f"brief {brief_id!r}"
            if brief_id in seen:
                problems.append(f"{label} repeats an id")
            seen.add(brief_id)
        for key in PUBLIC_KEYS:
            if key not in brief:
                problems.append(f"{label} is missing {key!r}")
        for key in ORACLE_KEYS:
            if not isinstance(brief.get("oracle"), Mapping) or (
                    key not in brief["oracle"]):
                problems.append(f"{label} is missing oracle {key!r}")
        if not isinstance(brief.get("instruction"), str) or not (
                brief.get("instruction") or "").strip():
            problems.append(f"{label} has no instruction")
        state = brief.get("project_state")
        if not isinstance(state, Mapping):
            problems.append(f"{label} has no project_state object")
        else:
            for key in PROJECT_STATE_KEYS:
                if key not in state:
                    problems.append(
                        f"{label} project_state is missing {key!r}")
        criteria = brief.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            problems.append(f"{label} declares no acceptance criteria")
        else:
            criterion_ids: set[str] = set()
            for criterion in criteria:
                if not isinstance(criterion, Mapping):
                    problems.append(f"{label} has a malformed criterion")
                    continue
                criterion_id = criterion.get("id")
                statement = criterion.get("statement")
                if not isinstance(criterion_id, str) or not criterion_id.strip():
                    problems.append(f"{label} has a criterion with no id")
                    continue
                if criterion_id in criterion_ids:
                    problems.append(
                        f"{label} repeats criterion id {criterion_id!r}")
                criterion_ids.add(criterion_id)
                if not isinstance(statement, str) or not statement.strip():
                    problems.append(
                        f"{label} criterion {criterion_id!r} has no statement")
        oracle = brief.get("oracle")
        if isinstance(oracle, Mapping):
            edge_case = oracle.get("edge_case")
            if edge_case is not None and edge_case not in EDGE_CASES:
                problems.append(
                    f"{label} names an edge case outside the enumeration: "
                    f"{edge_case!r}")
            if oracle.get("expected_refusal") not in (True, False):
                problems.append(
                    f"{label} must say whether a refusal is expected")
            operations = oracle.get("expected_operations")
            if not isinstance(operations, list):
                problems.append(
                    f"{label} expected_operations must be a list")
            allowed = oracle.get("allowed_operations")
            if not isinstance(allowed, list):
                problems.append(
                    f"{label} allowed_operations must be a list")
            elif isinstance(operations, list) and operations:
                missing = [op for op in operations if op not in allowed]
                if missing:
                    problems.append(
                        f"{label} expects operations it does not allow: "
                        f"{missing}")
            requirements = oracle.get("footage_requirements")
            if not isinstance(requirements, list):
                problems.append(
                    f"{label} footage_requirements must be a list")
            else:
                for requirement in requirements:
                    if not isinstance(requirement, Mapping):
                        problems.append(
                            f"{label} has a malformed footage requirement")
                        continue
                    feature = requirement.get("feature")
                    presence = requirement.get("presence")
                    if feature not in footage.FEATURES:
                        problems.append(
                            f"{label} names a premise outside the "
                            f"enumeration: {feature!r}")
                    if presence not in footage.PRESENCE:
                        problems.append(
                            f"{label} names a presence outside the "
                            f"enumeration: {presence!r}")
            if isinstance(operations, list) and isinstance(allowed, list):
                known = _known_operations()
                unknown = sorted(
                    {str(op) for op in list(operations) + list(allowed)}
                    - known)
                if unknown:
                    problems.append(
                        f"{label} names operations the engine does not have: "
                        f"{unknown}")
        # The public view must not carry the key. This is checked here as
        # well as at run time, so a bad brief fails when it is added.
        try:
            view = public_view(brief)
        except (KeyError, TypeError) as exc:
            problems.append(f"{label} cannot be read: {exc}")
        else:
            problems.extend(
                f"{label} {problem}" for problem in leak_problems(
                    view, brief.get("oracle") or {}))
    return problems


def public_view(brief: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the agent may read of one brief.

    Built key by key rather than by filtering the brief, so a field added
    to the brief later cannot leak by being forgotten here.
    """
    state = brief["project_state"]
    return {
        "id": brief["id"],
        "title": brief["title"],
        "instruction": brief["instruction"],
        "project_state": {
            "footage_profile": state["footage_profile"],
            "existing_adjustments": list(state["existing_adjustments"]),
            "media_conditions": list(state["media_conditions"]),
        },
        "acceptance_criteria": [
            {"id": criterion["id"], "statement": criterion["statement"]}
            for criterion in brief["acceptance_criteria"]
        ],
    }


def oracle(brief: Mapping[str, Any]) -> dict[str, Any]:
    """The evaluator's answer key for one brief. Never shown to the agent."""
    key = brief["oracle"]
    return {
        "edge_case": key["edge_case"],
        "expected_refusal": key["expected_refusal"],
        "expected_operations": list(key["expected_operations"]),
        "expected_target_roles": list(key["expected_target_roles"]),
        "expected_moment_ids": list(key["expected_moment_ids"]),
        "allowed_operations": list(key["allowed_operations"]),
        "footage_requirements": [
            dict(requirement)
            for requirement in (key.get("footage_requirements") or [])
        ],
        "notes": key["notes"],
    }


def leak_problems(view: Mapping[str, Any],
                  key: Mapping[str, Any]) -> list[str]:
    """Every way the public view carries the answer key.

    A leak is a KEY that should not be there, or a VALUE from the key
    appearing anywhere in the view. The key's operation ids, target role
    ids and moment ids are the values that would let an agent recognise
    the answer instead of working it out.
    """
    problems: list[str] = []
    serialised = json.dumps(view, sort_keys=True, ensure_ascii=False)
    for oracle_key in ORACLE_KEYS:
        if oracle_key in view:
            problems.append(f"the public view carries the oracle key "
                            f"{oracle_key!r}")
    forbidden: list[str] = []
    for field in ("expected_operations", "expected_target_roles",
                  "expected_moment_ids", "allowed_operations"):
        for value in key.get(field) or ():
            text = str(value)
            if text and text in serialised:
                forbidden.append(text)
    for value in forbidden:
        problems.append(f"the public view carries the oracle value {value!r}")
    for requirement in (key.get("footage_requirements") or ()):
        if not isinstance(requirement, Mapping):
            continue
        feature = str(requirement.get("feature") or "")
        if feature and feature in serialised:
            problems.append(
                f"the public view names the premise {feature!r}")
    if key.get("expected_refusal") is True and "expected_refusal" in serialised:
        problems.append("the public view says a refusal is expected")
    edge_case = key.get("edge_case")
    if edge_case and str(edge_case) in serialised:
        problems.append(
            f"the public view names the edge case {edge_case!r}")
    return problems


def assert_no_leak(view: Mapping[str, Any],
                   key: Mapping[str, Any]) -> None:
    """Raise when a public view carries the answer key."""
    problems = leak_problems(view, key)
    if problems:
        raise BriefSetError(
            "the brief leaks its answer key:\n  " + "\n  ".join(problems))


def _similarity(left: str, right: str) -> float:
    left_words = _words(left)
    right_words = _words(right)
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def unfamiliar_against(brief_set: Mapping[str, Any],
                       corpus_texts: Iterable[str]) -> list[str]:
    """The briefs that are not new, given a corpus the engine was tuned on.

    A brief is unfamiliar when its instruction is not a copy of a request
    the engine has already been measured against. Copies are reported by
    id so a brief can be rewritten rather than quietly re-used.
    """
    corpus = [str(text) for text in corpus_texts]
    repeats: list[str] = []
    for brief in brief_set["briefs"]:
        instruction = str(brief["instruction"])
        if any(_similarity(instruction, text) >= SIMILARITY_FLOOR
               for text in corpus):
            repeats.append(str(brief["id"]))
    return repeats


def coverage(brief_set: Mapping[str, Any]) -> dict[str, Any]:
    """What the set covers, and what it does not.

    The missing half matters as much as the present half: a set with no
    brief for an edge case must SAY so rather than look thorough.
    """
    briefs = list(brief_set["briefs"])
    edge_case_counts: dict[str, int] = {case: 0 for case in EDGE_CASES}
    edge_case_briefs: dict[str, list[str]] = {case: [] for case in EDGE_CASES}
    with_human_work: list[str] = []
    with_media_conditions: list[str] = []
    profiles: dict[str, int] = {}
    premises: dict[str, list[str]] = {}
    criterion_count = 0
    refusal_briefs: list[str] = []
    for brief in briefs:
        brief_id = str(brief["id"])
        key = brief["oracle"]
        case = key.get("edge_case")
        if case in edge_case_counts:
            edge_case_counts[case] += 1
            edge_case_briefs[case].append(brief_id)
        for requirement in (key.get("footage_requirements") or []):
            feature = str(requirement.get("feature") or "")
            premises.setdefault(feature, []).append(brief_id)
        state = brief["project_state"]
        if list(state["existing_adjustments"]):
            with_human_work.append(brief_id)
        if list(state["media_conditions"]):
            with_media_conditions.append(brief_id)
        profile = str(state["footage_profile"])
        profiles[profile] = profiles.get(profile, 0) + 1
        criterion_count += len(brief["acceptance_criteria"])
        if key.get("expected_refusal"):
            refusal_briefs.append(brief_id)
    missing = [case for case in EDGE_CASES if not edge_case_counts[case]]
    return {
        "brief_count": len(briefs),
        "acceptance_criterion_count": criterion_count,
        "edge_case_counts": edge_case_counts,
        "edge_case_briefs": edge_case_briefs,
        "edge_cases_missing": missing,
        "briefs_with_existing_adjustments": with_human_work,
        "briefs_with_imperfect_media": with_media_conditions,
        "footage_profile_counts": profiles,
        "premise_briefs": premises,
        "refusal_briefs": refusal_briefs,
    }


def briefs_by_id(brief_set: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Every brief, keyed by id."""
    return {str(brief["id"]): brief for brief in brief_set["briefs"]}


def demonstration_brief_id(brief_set: Mapping[str, Any]) -> str | None:
    """Which brief in the set is the recurring demonstration, if any."""
    named = brief_set.get("demonstration_brief")
    if named is None:
        return None
    return str(named)


def brief(brief_set: Mapping[str, Any], brief_id: str) -> Mapping[str, Any]:
    """One brief by id; KeyError when the set does not carry it."""
    for candidate in brief_set["briefs"]:
        if str(candidate["id"]) == brief_id:
            return candidate
    raise KeyError(f"the brief set carries no brief {brief_id!r}")


def corpus_instructions(
        brief_set: Mapping[str, Any]) -> Sequence[str]:
    """The set's own instructions, for a set-versus-set comparison."""
    return [str(brief["instruction"]) for brief in brief_set["briefs"]]
