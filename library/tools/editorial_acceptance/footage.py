"""The footage a brief runs against, and whether the brief applies to it.

An acceptance set is only meaningful if the footage it runs against
carries the briefs' premises. Four of the eight briefs in the shipped
set name something the substrate footage does not contain - a statement
of what it costs, a moment of overlapping speech, a caption that names
the wrong word, a music bed - and the run was then scored for failing
to find what was never there. The remedy is to state the premise and
CHECK it against measurement.

Three objects, each with one job:

- a FEATURE is a fact about a piece of footage: "this reel carries a
  music bed", "this reel carries no caption that misstates its speech".
  The enumeration is closed, so a premise cannot be spelled in words
  nobody can check.
- a REQUIREMENT is what a brief's request presupposes of the footage it
  runs against: a feature that must be present, or a feature that must
  be absent. It belongs to the brief's answer key, because it is what
  the request implies rather than what the request says.
- a SUBSTRATE is one reel, measured once: the features it carries, and
  the features it was CHECKED for and does not carry. A feature nobody
  looked for is neither present nor absent - it is UNKNOWN, and a
  requirement resting on it cannot be settled.

The measurement is the point. A substrate that says a feature is absent
because somebody looked and did not find it is evidence; a substrate
that says nothing about it has not earned an answer, and the harness
reports the premise as UNCHECKED rather than guessing either way.

What the harness does with this:

- A brief whose premise the substrate does not carry is SCORED, and the
  score is the headline: the engine still had to do the work the
  request asks for. Beside that score, the report names the premise
  the footage does not carry.
- A brief whose premise cannot be checked is reported as unchecked,
  and says so. It is neither a pass nor a failure.
- A run picks, per brief, the substrate whose profile matches the
  brief and whose measured content carries the brief's premise. When
  more than one substrate is available the choice is made here, once,
  and reported - not left to whichever reel the driver opened.

The pairing is the point of the last one: a brief with no substrate
that carries its premise is not a brief to argue about, it is a brief
waiting for footage that satisfies it, and the report says exactly
which premises such a substrate has to carry.

Nothing here touches Resolve, a model or a network: it is data in, data
out.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "ren-editorial-substrates/1"

#: Every fact about footage a premise may rest on, with what it means.
#: Closed on purpose: a premise stated in words nobody can check is a
#: premise nobody can enforce.
FEATURES: dict[str, str] = {
    "cost_statement": "the footage states what something costs",
    "overlapping_speech": "two speakers talk over each other",
    "caption_mismatch": "a caption names a word or number the speech does not",
    "repeated_passage": "the same statement is made more than once",
    "second_camera_angle": "a second camera angle covers a moment already shot",
    "matching_broll": "a cutaway matching the spoken words exists",
    "music_bed": "a music bed runs under the dialogue",
    "lower_third_overlay": "a lower third carries a name",
    "workshop_bench_coverage": "the workshop bench was filmed",
    "named_logo_asset": "the new logo is in the project",
    # Added for the representative validation corpus (BASE-003), whose
    # briefs are written against footage that CARRIES the event they ask
    # for. Each name is a fact a person can look for and settle.
    "dense_caption_cards": "a caption card sits over nearly every "
                           "spoken line",
    "hand_made_adjustments": "a human changed the timeline outside Ren",
    "several_number_passages": "the speaker reads numbers out in more "
                               "than one place",
    "long_pause": "the speech pauses long enough to be worth cutting out",
}

#: The shape of footage a brief is written against. A profile is what a
#: SUBSTRATE is; it selects the substrate, and it says what kind of
#: recording the brief assumes.
PROFILES: dict[str, str] = {
    "two_camera_interview": "one conversation recorded on two cameras",
    "single_camera_interview": "one conversation recorded on one camera",
    "talking_head_with_graphics": "one speaker on camera with graphics over it",
    "interview_with_broll": "an interview with cutaway footage cut in",
    "product_demo": "a demonstration of a product or a workshop",
    # Added for the representative validation corpus (BASE-003): the
    # shapes of footage that corpus measures against. A profile is what
    # a SUBSTRATE is, so a brief written against one shape is never
    # answered by footage of another.
    "caption_heavy_reel": "a short cut carrying a caption card over "
                          "nearly every spoken line",
    "single_camera_talking_head": "one speaker recorded on one camera",
}

#: What a requirement asks of the footage. A premise that must hold
#: ("present"), or a premise that must not ("absent") - the second is
#: how a brief whose correct answer is "that footage does not exist"
#: states itself.
PRESENCE: tuple[str, ...] = ("present", "absent")

#: The three states of a brief's premise against a substrate. The
#: premise is checked ALONGSIDE the scoring: a brief whose premise the
#: substrate does not carry is still scored, because the engine still
#: has to do the work the brief asks for - but the report says, next to
#: that score, that the footage could not carry the premise.
PREMISE_CARRIED = "premise_carried"
PREMISE_NOT_CARRIED = "premise_not_carried"
PREMISE_UNCHECKED = "premise_unchecked"
PREMISE_PROFILE_MISMATCH = "premise_profile_mismatch"
"""The substrate is a DIFFERENT SHAPE of footage from the one the brief
is written against.  The premise was never checked on it, and it could
not have been: a brief written against a product demo is not answered by
a two-camera interview, whatever that interview happens to carry."""

#: The states of one requirement against one substrate.
SATISFIED = "satisfied"
MISSING = "missing"
VIOLATED = "violated"
#: Nobody looked: the requirement rests on a fact that was never
#: measured, so it cannot be settled either way.
REQUIREMENT_UNKNOWN = "unknown"

CATALOGUE_PATH = Path(__file__).resolve().parent / "substrates.json"


class SubstrateError(ValueError):
    """The substrate catalogue does not satisfy its own contract."""


def _normalise(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def catalogue_path() -> Path:
    """Where the shipped substrate catalogue lives."""
    return CATALOGUE_PATH


def load_catalogue(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the catalogue; raise on any contract breach.

    The catalogue is DATA, like the brief set: a substrate is added by
    measuring a reel, not by editing code.
    """
    target = Path(path) if path is not None else CATALOGUE_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    problems = validate_catalogue(raw)
    if problems:
        raise SubstrateError(
            "the substrate catalogue violates its contract:\n  "
            + "\n  ".join(problems))
    raw["path"] = str(target)
    raw["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    return raw


def validate_catalogue(raw: Mapping[str, Any]) -> list[str]:
    """Every contract breach in the catalogue, named. Empty means it holds."""
    problems: list[str] = []
    if not isinstance(raw, Mapping):
        return ["the substrate catalogue is not an object"]
    if raw.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}, not {raw.get('schema')!r}")
    substrates = raw.get("substrates")
    if not isinstance(substrates, list) or not substrates:
        return problems + ["the catalogue declares no substrates"]
    seen: set[str] = set()
    for index, entry in enumerate(substrates):
        label = f"substrates[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{label} is not an object")
            continue
        substrate_id = entry.get("id")
        if not isinstance(substrate_id, str) or not substrate_id.strip():
            problems.append(f"{label} has no id")
        else:
            label = f"substrate {substrate_id!r}"
            if substrate_id in seen:
                problems.append(f"{label} repeats an id")
            seen.add(substrate_id)
        if entry.get("profile") not in PROFILES:
            problems.append(
                f"{label} names a profile outside the enumeration: "
                f"{entry.get('profile')!r}")
        measured = set()
        for field in ("features", "checked_features"):
            values = entry.get(field, [])
            if not isinstance(values, list):
                problems.append(f"{label} {field} is not a list")
                continue
            for value in values:
                if value not in FEATURES:
                    problems.append(
                        f"{label} {field} names a feature outside the "
                        f"enumeration: {value!r}")
            measured.add(field)
        carried = set(entry.get("features") or [])
        checked = set(entry.get("checked_features") or [])
        both = sorted(carried & checked)
        if both:
            problems.append(
                f"{label} carries a feature it also checked for and did not "
                f"find: {both}")
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            problems.append(f"{label} aliases is not a list")
        elif any(not isinstance(alias, str) or not alias.strip()
                 for alias in aliases):
            problems.append(f"{label} carries an empty alias")
    return problems


def substrates(catalogue: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every substrate in the catalogue, in file order."""
    return [dict(entry) for entry in (catalogue.get("substrates") or [])
            if isinstance(entry, Mapping)]


def declared_profile(brief: Mapping[str, Any]) -> str:
    """The footage profile the brief is written against."""
    state = brief.get("project_state")
    state = state if isinstance(state, Mapping) else {}
    return str(state.get("footage_profile") or "")


def requirements_of(brief: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What the brief's request presupposes of the footage.

    The requirements live in the brief's answer key: they are what the
    request implies about the footage, not what the request says.
    """
    key = brief.get("oracle")
    key = key if isinstance(key, Mapping) else {}
    raw = key.get("footage_requirements")
    if not isinstance(raw, list):
        return []
    return [dict(entry) for entry in raw if isinstance(entry, Mapping)]


def requirement_state(requirement: Mapping[str, Any],
                      substrate: Mapping[str, Any]) -> str:
    """How one requirement stands against one substrate.

    ``satisfied`` the substrate carries what the premise needs (or
    lacks what the premise forbids). ``missing`` the substrate was
    checked for it and does not carry it. ``violated`` the substrate
    carries what the premise says is not there. ``unknown`` nobody
    looked.
    """
    feature = str(requirement.get("feature") or "")
    presence = str(requirement.get("presence") or "present")
    carried = set(substrate.get("features") or [])
    checked = set(substrate.get("checked_features") or [])
    if presence == "absent":
        if feature in carried:
            return VIOLATED
        if feature in checked:
            return SATISFIED
        return REQUIREMENT_UNKNOWN
    if feature in carried:
        return SATISFIED
    if feature in checked:
        return MISSING
    return REQUIREMENT_UNKNOWN


def _requirement_rows(brief: Mapping[str, Any],
                      substrate: Mapping[str, Any]
                      ) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for requirement in requirements_of(brief):
        row = _requirement_row(requirement)
        row["state"] = requirement_state(requirement, substrate)
        rows.append(row)
    return rows


def _requirement_row(requirement: Mapping[str, Any]) -> dict[str, Any]:
    """One premise, as the pairing reports it: what it needs, in words."""
    feature = str(requirement.get("feature") or "")
    return {
        "feature": feature,
        "presence": str(requirement.get("presence") or "present"),
        "summary": FEATURES.get(feature, "a premise outside the "
                                   "enumeration"),
    }


def applicability(brief: Mapping[str, Any],
                  substrate: Mapping[str, Any] | None) -> dict[str, Any]:
    """Whether the substrate carries what this brief presupposes.

    ``premise_not_carried`` means the substrate was MEASURED and does
    not carry the premise. ``premise_unchecked`` means the substrate
    was never measured for it, or no substrate was named at all.
    Neither is a failure of the run: the run is scored either way, and
    this is reported beside the score.

    **The PROFILE is enforced here.**  A brief declares the shape of
    footage it is written against, and a substrate declares the shape it
    is.  A brief written against a product demo and run against a
    two-camera interview is not a brief that failed: it is a brief
    measured against footage it does not apply to, and it says so
    instead of carrying the substrate's profile silently.
    """
    profile = declared_profile(brief)
    required = requirements_of(brief)
    if substrate is None:
        return {
            "status": PREMISE_UNCHECKED,
            "reason": "the record names no substrate the catalogue carries",
            "brief_profile": profile,
            "substrate_id": None,
            "substrate_profile": None,
            "profile_matches": None,
            "required": [_requirement_row(requirement) for requirement
                         in required],
            "missing_premises": [],
            "unchecked_premises": [],
        }
    rows = _requirement_rows(brief, substrate)
    missing = [row for row in rows if row["state"] in (MISSING, VIOLATED)]
    unchecked = [row for row in rows
                   if row["state"] == REQUIREMENT_UNKNOWN]
    substrate_profile = str(substrate.get("profile") or "")
    profile_matches = bool(profile) and substrate_profile == profile
    detail = {
        "brief_profile": profile,
        "substrate_id": str(substrate.get("id") or ""),
        "substrate_profile": substrate_profile,
        "profile_matches": profile_matches,
        "required": [_requirement_row(requirement) for requirement
                     in required],
        "requirements": rows,
        "missing_premises": missing,
        "unchecked_premises": unchecked,
    }
    if profile and not profile_matches:
        detail["status"] = PREMISE_PROFILE_MISMATCH
        detail["reason"] = (
            f"the substrate is a {substrate_profile} and this brief is "
            f"written against a {profile}; the footage is the wrong shape "
            "for the brief")
        return detail
    if not rows:
        detail["reason"] = ("the brief states no premise about the "
                            "footage, so any substrate carries it")
        detail["status"] = PREMISE_CARRIED
        return detail
    if missing:
        names = ", ".join(row["summary"] for row in missing)
        detail["status"] = PREMISE_NOT_CARRIED
        detail["reason"] = (f"the substrate does not carry what the brief "
                             f"presupposes: {names}")
        return detail
    if unchecked:
        names = ", ".join(row["summary"] for row in unchecked)
        detail["status"] = PREMISE_UNCHECKED
        detail["reason"] = (f"the substrate was never measured for what the "
                             f"brief presupposes: {names}")
        return detail
    detail["status"] = PREMISE_CARRIED
    detail["reason"] = "the substrate carries what the brief presupposes"
    return detail


def resolve_substrate(catalogue: Mapping[str, Any],
                      record: Mapping[str, Any]
                      ) -> tuple[dict[str, Any] | None, list[str]]:
    """The substrate a record ran against, and the names that were tried.

    A record names its substrate; the live lane writes it in the
    record's project section, next to the project and the timeline it
    worked in. Aliases are honoured, because the timeline's own name is
    the name a reel is known by.
    """
    names: list[str] = []
    top = record.get("substrate")
    if isinstance(top, str) and top.strip():
        names.append(top)
    project = record.get("project")
    if isinstance(project, Mapping):
        for field in ("substrate", "timeline", "footage"):
            value = project.get(field)
            if isinstance(value, str) and value.strip():
                names.append(value)
    entries = substrates(catalogue)
    for name in names:
        wanted = _normalise(name)
        for entry in entries:
            if wanted == _normalise(entry.get("id")):
                return entry, names
        for entry in entries:
            aliases = entry.get("aliases") or []
            if any(wanted == _normalise(alias) for alias in aliases):
                return entry, names
    return None, names


def select_substrate(brief: Mapping[str, Any],
                     catalogue: Mapping[str, Any]) -> dict[str, Any]:
    """Which substrate a run should work this brief against.

    **The profile is what pairs a brief with a substrate.**  A substrate
    that carries the brief's premise comes first; among those, one whose
    PROFILE matches the profile the brief declares is preferred, because
    the profile is what the brief was written against. When nothing
    carries the premise, that is said plainly: the brief needs a
    substrate nobody has measured yet, and no measurement is invented to
    fill the gap.

    A substrate whose profile does NOT match is never proposed: a brief
    written against a product demo is not served by a two-camera
    interview, however well that interview carries the premise.
    """
    requirements = requirements_of(brief)
    profile = declared_profile(brief)
    entries = substrates(catalogue)
    of_profile = [entry for entry in entries
                  if profile and str(entry.get("profile") or "") == profile]
    settled: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    carrying: list[dict[str, Any]] = []
    carrying_any_profile: list[dict[str, Any]] = []
    for entry in entries:
        states = [requirement_state(requirement, entry)
                  for requirement in requirements]
        if any(state in (MISSING, VIOLATED) for state in states):
            continue
        carrying_any_profile.append(entry)
        if entry not in of_profile:
            continue
        carrying.append(entry)
        if any(state == REQUIREMENT_UNKNOWN for state in states):
            unconfirmed.append(entry)
        else:
            settled.append(entry)
    pool = settled + unconfirmed
    matches = pool
    chosen: dict[str, Any] | None = None
    reason = ""
    if matches:
        chosen = min(matches, key=lambda entry: str(entry.get("id")))
        reason = ("its profile matches the profile the brief declares, and "
                  "it carries what the brief presupposes")
    elif of_profile:
        reason = ("a substrate of the right profile exists, but it does "
                  "not carry what the brief presupposes")
    elif profile:
        reason = (f"no substrate declares the profile this brief is written "
                  f"against ({profile}); the brief needs footage of that "
                  "shape")
    else:
        reason = "the brief declares no profile, and nothing carries it"
    selection = {
        "brief_id": str(brief.get("id") or ""),
        "declared_profile": profile,
        "requires": [_requirement_row(requirement)
                     for requirement in requirements],
        "selected": str(chosen.get("id")) if chosen else None,
        "selected_profile": str(chosen.get("profile")) if chosen else None,
        "reason": reason,
        "carrying_premise": [str(entry.get("id")) for entry in carrying],
        "carrying_premise_any_profile": [
            str(entry.get("id")) for entry in carrying_any_profile],
        "profile_matches": [str(entry.get("id")) for entry in matches],
        "settled": [str(entry.get("id")) for entry in settled],
        "unconfirmed": [str(entry.get("id")) for entry in unconfirmed],
    }
    if chosen is None and requirements:
        missing: list[dict[str, Any]] = []
        unconfirmed_rows: list[dict[str, Any]] = []
        for requirement in requirements:
            states = [requirement_state(requirement, entry)
                      for entry in entries]
            feature = str(requirement.get("feature") or "")
            row = {
                "feature": feature,
                "presence": str(requirement.get("presence") or "present"),
                "summary": FEATURES.get(feature, "a premise outside the "
                                               "enumeration"),
            }
            if any(state == SATISFIED for state in states):
                continue
            if any(state == REQUIREMENT_UNKNOWN for state in states):
                unconfirmed_rows.append(row)
            else:
                missing.append(row)
        selection["missing_premises"] = missing
        selection["unconfirmed_premises"] = unconfirmed_rows
    return selection


def select_for_set(brief_set: Mapping[str, Any],
                    catalogue: Mapping[str, Any]) -> dict[str, Any]:
    """The substrate each brief in the set would run against.

    This is the PAIRING the footage lane works from: for each brief,
    the profile it is written against, the premises it presupposes, the
    substrate that carries them, and - where nothing does - the exact
    premises a substrate has to carry for the brief to be doable.
    """
    selections = {}
    for brief in brief_set.get("briefs") or []:
        if not isinstance(brief, Mapping):
            continue
        selections[str(brief.get("id") or "")] = select_substrate(
            brief, catalogue)
    return {
        "catalogue_path": catalogue.get("path"),
        "catalogue_sha256": catalogue.get("sha256"),
        "substrate_count": len(substrates(catalogue)),
        "briefs": selections,
    }
