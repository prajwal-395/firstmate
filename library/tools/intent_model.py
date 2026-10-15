"""The structured intent model - what the video should communicate.

The gap this closes
-------------------
Intent is prose.  `creative_direction` (step 2.01) is one LLM-written
document with free-text fields, and `key_moments` are described "by
content, not timestamps" - so nothing downstream can ask "does this
edit serve the narrative theme?" or "did the key moment land?".  The
theme is a sentence a planner reads once; the key moments are not
entities, so there is nothing to check against the timeline.  This
module is the structured representation beside that prose.

The model
---------
The intent model is a subgraph of the edit graph (Gap 1): its beats are
addressable by the same `beat:` id namespace the graph uses, so a
planner naming `beat:3` is naming a node the graph can link decisions
to.  It is emitted by `creative_direction` beside the prose direction,
and it is the thing the brief writes and the thing verification reads:

    beats          the narrative structure - each with a purpose, a
                   setup/payoff link to another beat, and a span
                   grounded in the footage
    claims         the argument as an ordered set of claims, each
                   naming the claims it supports
    key_moments    the moments that must appear, as addressable
                   entities grounded in a clip and a source span
    pacing_curve   pacing and energy as a curve over beats, not a
                   sentence - one entry per beat

A beat's span is a source-footage range (`clip_id` + `source_start`/
`source_end`), because the model is emitted at step 2.01 before any
timeline exists: the span names the material the beat draws on, and
the spine later realizes it as timeline blocks.  A key moment is
grounded the same way, which is what makes "did the key moment land?"
answerable - the moment has a source address to check against the
cut.

`validate_intent_model` is the contract: a beat with no purpose, a
setup/payoff link naming a beat that does not exist, a key moment
grounded in no clip, a pacing entry naming an unknown beat - each is
a defect the validation names rather than a default it invents.

`validate_serves_beats` is the planner-side half: a planner's output
that serves beats must NAME them, and must name beats that exist.  A
passage that serves the narrative but names no beat is untraceable - a
reader cannot ask what it is for - and a passage naming a beat the
model does not have is a claim with nothing under it.

This module imports nothing from steps or processes: it reads the
model a state carries, the same reader every consumer has.
"""
from __future__ import annotations

INTENT_MODEL = "intent_model"
"""The state key the model is written to, beside `creative_direction`."""

BEAT_PREFIX = "beat:"
CLAIM_PREFIX = "claim:"
MOMENT_PREFIX = "moment:"

BEATS = "beats"
CLAIMS = "claims"
KEY_MOMENTS = "key_moments"
PACING_CURVE = "pacing_curve"

BEAT_ID = "beat_id"
PURPOSE = "purpose"
SETUP_FOR = "setup_for"
PAYOFF_OF = "payoff_of"
SPAN = "span"
CLIP_ID = "clip_id"
SOURCE_START = "source_start"
SOURCE_END = "source_end"

CLAIM_ID = "claim_id"
STATEMENT = "statement"
SUPPORTS = "supports"

MOMENT_ID = "moment_id"
DESCRIPTION = "description"

ENERGY = "energy"
TARGET_ASL = "target_asl"

SERVES_BEATS = "serves_beats"
"""The field a planner's entry carries to name the beats it serves."""

EDITORIAL_SPEC = "editorial_spec"
"""The commission translated into inspectable constraints and preferences."""

EDITORIAL_SPEC_FIELDS = (
    "requirements", "preferences", "target_duration", "platform",
    "audience", "pacing", "subjects", "narrative_goals",
    "desired_content", "prohibited_content", "references",
    "unresolved_decisions", "revision",
)


# ── Small readers over the model ───────────────────────────────────


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def beats(model: dict) -> list:
    """The model's beats, in declaration order."""
    return _as_list(model.get(BEATS))


def claims(model: dict) -> list:
    """The model's claims, in declaration order - the argument's order."""
    return _as_list(model.get(CLAIMS))


def key_moments(model: dict) -> list:
    """The model's key moments, in declaration order."""
    return _as_list(model.get(KEY_MOMENTS))


def pacing_curve(model: dict) -> list:
    """The model's pacing curve, one entry per beat it names."""
    return _as_list(model.get(PACING_CURVE))


def beat_ids(model: dict) -> set:
    """The ids of every beat in the model - what a planner may name."""
    return {beat.get(BEAT_ID) for beat in beats(model) if beat.get(BEAT_ID)}


def beat(model: dict, beat_id: str) -> dict:
    """The beat with this id, or {} - a missing beat is a query miss."""
    for candidate in beats(model):
        if candidate.get(BEAT_ID) == beat_id:
            return candidate
    return {}


def pacing_for_beat(model: dict, beat_id: str) -> dict:
    """The pacing-curve entry for one beat, or {} when it has none."""
    for entry in pacing_curve(model):
        if entry.get(BEAT_ID) == beat_id:
            return entry
    return {}


def setup_payoff_links(model: dict) -> list:
    """Every setup/payoff link as `(beat_id, role, other_beat_id)`.

    A link is a promise between two beats: `setup_for` names the beat
    this one sets up, `payoff_of` the beat this one pays off.  A beat
    with neither is a link in neither direction and carries no row.
    """
    links = []
    for entry in beats(model):
        beat_id = entry.get(BEAT_ID)
        if not beat_id:
            continue
        if entry.get(SETUP_FOR):
            links.append((beat_id, "setup", entry.get(SETUP_FOR)))
        if entry.get(PAYOFF_OF):
            links.append((beat_id, "payoff", entry.get(PAYOFF_OF)))
    return links


def moments_in_beat(model: dict, beat_id: str) -> list:
    """The key moments grounded inside one beat's span.

    A moment is in a beat when its own source range overlaps the beat's
    span on the same clip - the same overlap test the edit graph uses
    to link a block to the passages it plays, so a moment and the beat
    it belongs to are judged by one rule.
    """
    span = beat(model, beat_id).get(SPAN) or {}
    clip_id = span.get(CLIP_ID)
    if not clip_id:
        return []
    start, end = span.get(SOURCE_START), span.get(SOURCE_END)
    if start is None or end is None:
        return []
    inside = []
    for moment in key_moments(model):
        moment_span = _as_dict(moment.get(SPAN))
        if moment_span.get(CLIP_ID) != clip_id:
            continue
        m_start = moment_span.get(SOURCE_START)
        m_end = moment_span.get(SOURCE_END)
        if m_start is None or m_end is None:
            continue
        if m_start < end and start < m_end:
            inside.append(moment)
    return inside


# ── The contract over the model itself ─────────────────────────────


def validate_intent_model(model: dict, catalog_clip_ids=None) -> list:
    """Every defect in the model, as a list of problems. Empty is valid.

    `catalog_clip_ids` is the set of clip ids the catalog carries. When
    it is given, a beat or key moment naming a clip outside it is
    refused - a span grounded in a clip that does not exist is a span
    grounded in nothing. When it is absent (a reader that has no catalog
    in hand) the grounding is checked for shape, not membership.
    """
    model = _as_dict(model)
    problems: list = []

    if EDITORIAL_SPEC in model:
        problems.extend(validate_editorial_spec(model[EDITORIAL_SPEC]))

    seen_beats: set = set()
    for entry in beats(model):
        beat_id = entry.get(BEAT_ID)
        label = beat_id or "<no beat_id>"
        if not beat_id:
            problems.append(f"beat {label}: no beat_id")
        elif beat_id in seen_beats:
            problems.append(f"beat {label}: duplicate beat_id")
        else:
            seen_beats.add(beat_id)
        if not entry.get(PURPOSE):
            problems.append(f"beat {label}: no purpose")
        if "required" in entry and not isinstance(entry["required"], bool):
            problems.append(f"beat {label}: required must be true or false")
        _check_span(entry, f"beat {label}", catalog_clip_ids, problems)

    for beat_id, role, other in setup_payoff_links(model):
        if other == beat_id:
            problems.append(
                f"beat {beat_id}: {role} links to itself")
        elif other not in seen_beats:
            problems.append(
                f"beat {beat_id}: {role} names beat {other!r}, "
                f"which is not in the model")

    seen_claims: set = set()
    for entry in claims(model):
        claim_id = entry.get(CLAIM_ID)
        label = claim_id or "<no claim_id>"
        if not claim_id:
            problems.append(f"claim {label}: no claim_id")
        elif claim_id in seen_claims:
            problems.append(f"claim {label}: duplicate claim_id")
        else:
            seen_claims.add(claim_id)
        if not entry.get(STATEMENT):
            problems.append(f"claim {label}: no statement")
        for supported in _as_list(entry.get(SUPPORTS)):
            if supported == claim_id:
                problems.append(
                    f"claim {label}: supports itself")
            elif supported not in seen_claims and supported not in {
                    c.get(CLAIM_ID) for c in claims(model)}:
                problems.append(
                    f"claim {label}: supports claim {supported!r}, "
                    f"which is not in the model")

    for moment in key_moments(model):
        moment_id = moment.get(MOMENT_ID) or "<no moment_id>"
        if not moment.get(MOMENT_ID):
            problems.append(f"key moment {moment_id}: no moment_id")
        if not moment.get(DESCRIPTION):
            problems.append(f"key moment {moment_id}: no description")
        _check_span(moment, f"key moment {moment_id}",
                    catalog_clip_ids, problems)

    seen_pacing: set = set()
    for entry in pacing_curve(model):
        beat_id = entry.get(BEAT_ID)
        label = beat_id or "<no beat_id>"
        if not beat_id:
            problems.append(f"pacing entry {label}: no beat_id")
        elif beat_id not in seen_beats:
            problems.append(
                f"pacing entry {label}: names a beat not in the model")
        elif beat_id in seen_pacing:
            problems.append(f"pacing entry {label}: duplicate beat_id")
        else:
            seen_pacing.add(beat_id)
        energy = entry.get(ENERGY)
        if energy is not None and not (0.0 <= energy <= 1.0):
            problems.append(
                f"pacing entry {label}: energy {energy} outside 0..1")

    return problems


def validate_editorial_spec(spec: dict) -> list:
    """Validate the commission-level part of an intent model.

    Empty lists and nulls are valid readings: the source request may not
    specify a platform or duration. Missing fields are not, because they
    make an omitted decision indistinguishable from an unasked one.
    """
    spec = _as_dict(spec)
    problems = [f"editorial_spec: missing {key}" for key in EDITORIAL_SPEC_FIELDS
                if key not in spec]
    for field in ("requirements", "preferences", "pacing", "narrative_goals",
                  "desired_content", "prohibited_content", "references",
                  "unresolved_decisions"):
        if field in spec and not isinstance(spec[field], list):
            problems.append(f"editorial_spec.{field}: must be a list")
    for field in ("subjects", "revision"):
        if field in spec and not isinstance(spec[field], dict):
            problems.append(f"editorial_spec.{field}: must be an object")
    for field in ("platform", "audience"):
        if field in spec and spec[field] is not None and not isinstance(spec[field], str):
            problems.append(f"editorial_spec.{field}: must be a string or null")
    if "target_duration" in spec:
        duration = spec["target_duration"]
        if duration is not None and not isinstance(duration, dict):
            problems.append("editorial_spec.target_duration: must be an object or null")
        elif isinstance(duration, dict):
            for key in ("minimum_seconds", "target_seconds", "maximum_seconds"):
                value = duration.get(key)
                if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                    problems.append(f"editorial_spec.target_duration.{key}: must be positive or null")
            low, target, high = (duration.get(k) for k in
                                 ("minimum_seconds", "target_seconds", "maximum_seconds"))
            if low is not None and high is not None and low > high:
                problems.append("editorial_spec.target_duration: minimum exceeds maximum")
            if target is not None and low is not None and target < low:
                problems.append("editorial_spec.target_duration: target is below minimum")
            if target is not None and high is not None and target > high:
                problems.append("editorial_spec.target_duration: target exceeds maximum")
    for group in ("requirements", "preferences"):
        for index, entry in enumerate(spec.get(group, [])):
            if (not isinstance(entry, dict) or not entry.get("id")
                    or not entry.get("statement") or not entry.get("source")):
                problems.append(
                    f"editorial_spec.{group}[{index}]: needs id, statement and source")
    subjects = spec.get("subjects")
    if isinstance(subjects, dict):
        for key in ("include", "exclude"):
            if not isinstance(subjects.get(key), list):
                problems.append(f"editorial_spec.subjects.{key}: must be a list")
    revision = spec.get("revision")
    if isinstance(revision, dict):
        for key in ("revision_of", "preserved", "changed", "removed"):
            if key not in revision:
                problems.append(f"editorial_spec.revision: missing {key}")
        for key in ("preserved", "changed", "removed"):
            if key in revision and not isinstance(revision[key], list):
                problems.append(f"editorial_spec.revision.{key}: must be a list")
    for index, entry in enumerate(spec.get("references", [])):
        if isinstance(entry, dict) and (not entry.get("description") or not entry.get("source")):
            problems.append(
                f"editorial_spec.references[{index}]: needs description and source")
    for index, entry in enumerate(spec.get("unresolved_decisions", [])):
        if isinstance(entry, dict) and (not entry.get("question") or not entry.get("why_it_matters")):
            problems.append(
                f"editorial_spec.unresolved_decisions[{index}]: needs question and why_it_matters")
    for field in ("requirements", "preferences", "pacing", "narrative_goals",
                  "desired_content", "prohibited_content", "references",
                  "unresolved_decisions"):
        # The field itself carries its classification; prevent silent prose blobs.
        if field in spec and any(not isinstance(item, (str, dict)) for item in spec[field]):
            problems.append(f"editorial_spec.{field}: entries must be strings or objects")
    return problems


def _check_span(entry: dict, label: str, catalog_clip_ids, problems: list) -> None:
    """A beat or key moment whose span is not grounded in a measurement."""
    span = entry.get(SPAN)
    if not span:
        problems.append(f"{label}: no span")
        return
    span = _as_dict(span)
    clip_id = span.get(CLIP_ID)
    if not clip_id:
        problems.append(f"{label}: span names no clip_id")
    elif catalog_clip_ids is not None and clip_id not in catalog_clip_ids:
        problems.append(
            f"{label}: span names clip {clip_id!r}, "
            f"which is not in the catalog")
    start, end = span.get(SOURCE_START), span.get(SOURCE_END)
    if start is None or end is None:
        problems.append(f"{label}: span has no source_start/source_end")
    elif end <= start:
        problems.append(f"{label}: span ends before it starts")


# ── The planner-side contract ─────────────────────────────────────


def validate_serves_beats(entries: list, model: dict,
                          field: str = SERVES_BEATS) -> list:
    """Every defect in a planner's output that serves beats.

    A planner's entry - a passage, a block, a transition - serves the
    narrative beats its content belongs to, and it must NAME them: an
    entry that serves the story but names no beat is untraceable, a
    reader cannot ask what it is for, and the graph (Gap 1) cannot link
    it to an intent.  An entry naming a beat the model does not have is
    a claim with nothing under it.  Both are refused here, by name,
    rather than dropped or defaulted.

    An entry naming no beat is refused only when the model HAS beats -
    a run whose intent model carries no beats (or none at all) has
    nothing to name, and the absence is the model's own statement.
    """
    model = _as_dict(model)
    known = beat_ids(model)
    problems: list = []
    for index, entry in enumerate(_as_list(entries)):
        label = _entry_label(entry, index)
        named = [b for b in _as_list(entry.get(field)) if b]
        if known and not named:
            problems.append(
                f"{label}: names no beat, but the intent model has "
                f"{len(known)} to serve")
        for beat_id in named:
            if beat_id not in known:
                problems.append(
                    f"{label}: names beat {beat_id!r}, "
                    f"which is not in the intent model")
    return problems


def validate_narrative_constraints(passages: list, model: dict) -> list:
    """Check the hard beat commitments against the proposed speech order.

    Only beats explicitly marked ``required`` and declared setup/payoff
    links are hard constraints. The remaining beats can be selected and
    ordered to suit the editorial goal.
    """
    model = _as_dict(model)
    positions = {}
    for index, passage in enumerate(_as_list(passages)):
        for beat_id in _as_list(_as_dict(passage).get(SERVES_BEATS)):
            if beat_id:
                positions.setdefault(beat_id, []).append(index)

    problems = []
    for entry in beats(model):
        beat_id = entry.get(BEAT_ID)
        if entry.get("required") is True and beat_id not in positions:
            problems.append(
                f"required beat {beat_id!r} ({entry.get(PURPOSE, '')}) "
                "is missing from the speech sequence")

    checked_links = set()
    for beat_id, role, other_id in setup_payoff_links(model):
        setup_id, payoff_id = ((beat_id, other_id) if role == "setup"
                               else (other_id, beat_id))
        if (setup_id, payoff_id) in checked_links:
            continue
        checked_links.add((setup_id, payoff_id))
        setup_positions = positions.get(setup_id, [])
        payoff_positions = positions.get(payoff_id, [])
        if not setup_positions or not payoff_positions:
            continue
        if max(setup_positions) >= min(payoff_positions):
            problems.append(
                f"setup beat {setup_id!r} must precede payoff beat "
                f"{payoff_id!r}, but their speech passages are reversed "
                "or interleaved")
    return problems


def _entry_label(entry, index: int) -> str:
    if isinstance(entry, dict):
        for key in ("position", "clip_id", "node_id", "beat_id"):
            if entry.get(key) is not None:
                return f"entry {entry.get(key)!r}"
    return f"entry {index}"
