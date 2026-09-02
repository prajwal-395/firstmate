"""undetermined.py - what a creative step could not determine from what it was given.

Across all nine model responses in the 29 Aug 2026 run of project 001
there is exactly ONE hedge, and it is not an "I cannot see" - it is
`select_broll` reasoning about a softness measurement.  The models are
not asking for more, and until this module nothing invited them to.
"Where are the pipeline's bottlenecks" therefore stayed a judgement call
made from outside, with no demand signal from the steps themselves.

This asks the nine steps that reach a model to SAY what the material
they were routed did not let them decide, as a structured field rather
than as prose encouragement.

**Three states, not two, and that is the whole design.**  A field a model
fills with polite noise on every step is worse than no field, and a field
whose emptiness cannot be told from its absence measures nothing.  So:

    DECLARED         the model named one or more gaps.
    NOTHING_MISSING  the model answered the question with an empty list -
                     "the material was sufficient".  A real answer.
    NOT_DECLARED     the key is not in the answer at all.  The model was
                     asked and did not reply, and that is reported as
                     itself rather than read as "nothing was missing".

This is the same line the repository draws everywhere else between an
admitted absence and a measured emptiness: `usable_ranges` `[]` with
method `unmeasured` against `[]` with method `deterministic_v1`
(AGENTS.md 10.3), `primary_subject_visible` None against `[]`,
`speech_present` True-or-None-never-False.

**It reports and it never gates.**  A step that declares nothing is not
failing, and a step that declares five gaps is not failing either - the
declaration is a demand signal for whoever is deciding what to route
next, not a verdict on the answer.  Nothing here reads the content of a
declaration or acts on it: that would be this module deciding which gaps
matter, and whatever picks which findings matter becomes the reviewer
(AGENTS.md 10.4).

**Why all nine steps and not a subset.**  Choosing a subset would be
answering, in advance and from outside, the question this exists to
collect data for.  Every step here makes a judgement from material an
allow-list narrowed, so any of them can be short of something; the point
is to find out which, from them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The key the model writes.  One spelling, here, because a key name
# spelled twice is this repository's dominant bug class.
FIELD = "could_not_determine"

# The nine steps that reach a model on a default run, by DAG node id.
# `semantic_analysis` is not one: its schema is empty and its call is
# skipped.  `render` and `validate` are not creative judgements.
DECLARING_STEPS = frozenset({
    "creative_direction",
    "speech_sequence",
    "music_selection",
    "mesh_spine",
    "select_broll",
    "review_rough_cut",
    "plan_transitions",
    "plan_vfx",
    "plan_sfx",
})

# The three readings.  Spelled differently on purpose.
DECLARED = "declared"
NOTHING_MISSING = "nothing_missing"
NOT_DECLARED = "not_declared"

READINGS = (DECLARED, NOTHING_MISSING, NOT_DECLARED)

# What one entry may say.  `what` is the only part that must be there:
# a gap named without a reason is still a gap named.
ENTRY_KEYS = ("what", "why_it_mattered", "what_would_have_helped")


@dataclass
class Declaration:
    """One step's answer to the question, on one attempt."""

    step_id: str
    reading: str
    entries: List[Dict[str, str]] = field(default_factory=list)
    malformed: str = ""
    """Set when the key was present but not a shape this could read.

    Kept as its own note rather than collapsed into NOT_DECLARED: a model
    that answered in the wrong shape did answer, and that is a different
    thing to fix from a model that stayed silent.
    """

    @property
    def declared_a_gap(self) -> bool:
        return self.reading == DECLARED


def declares(step_id: str) -> bool:
    return step_id in DECLARING_STEPS


def schema_entry() -> dict:
    """The field, in the shape `generate_output_schema_text` renders.

    `required` is False in the RENDERED schema because a model must be
    able to leave it empty without being told it failed - but an empty
    declaration and an absent one are read differently, and the prompt
    block below says so in as many words.
    """
    return {
        "name": FIELD,
        "type": "array",
        "required": False,
        "description": (
            "What you could not determine from the material you were "
            "given. Empty list [] if the material was sufficient - that "
            "is a complete answer and the expected one."
        ),
    }


def prompt_block() -> str:
    """The instruction, delivered as DATA beside the context.

    The handoffs are frozen, so this takes the route
    `music_measurement.MEASUREMENT_LEGEND` and `CUTS_LEGEND` already
    take: the words travel with the call rather than being edited into
    the prompt file.
    """
    return (
        "\n\n## What you could not determine\n\n"
        f"Alongside your answer, return `{FIELD}`: a list of the things "
        "the material above did not let you decide.\n\n"
        "Each entry is an object:\n\n"
        "```json\n"
        "{\n"
        '  "what": "the decision or detail you could not settle",\n'
        '  "why_it_mattered": "what in your answer it would have changed",\n'
        '  "what_would_have_helped": "the measurement, document or field '
        'that would have settled it"\n'
        "}\n"
        "```\n\n"
        "Return `[]` when the material was sufficient. An empty list is a "
        "complete answer and it is the expected one on most calls - it is "
        "read as \"nothing was missing\", not as a failure to reply. Do "
        "not manufacture an entry to fill the field: a gap you did not "
        "actually hit is noise, and noise here is worse than silence. "
        "Name only what genuinely limited the answer you gave.\n\n"
        "Omitting the field entirely is read as a non-answer and recorded "
        "as one, so return `[]` rather than leaving it out.\n"
    )


def _normalise_entry(raw: Any) -> Optional[Dict[str, str]]:
    if isinstance(raw, str):
        text = raw.strip()
        return {"what": text} if text else None
    if isinstance(raw, dict):
        entry = {}
        for key in ENTRY_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        if "what" not in entry:
            # Whatever the model called it, keep the words rather than
            # dropping the entry: a gap named in an unexpected key is
            # still a gap named.
            for key, value in raw.items():
                if isinstance(value, str) and value.strip():
                    entry["what"] = value.strip()
                    break
        return entry or None
    return None


def take(step_id: str, answer: Any) -> tuple:
    """Split the declaration out of a model answer.

    Returns `(answer_without_the_field, Declaration)`.

    The field is REMOVED from the answer rather than carried with it.
    A step's outputs are what its manifest declares, `validate_step_output`
    refuses an unexpected extra key, and a post-bridge is handed the
    model's answer as its own input - so leaving it in would make this
    change visible to three places that have no business seeing it.
    """
    if not declares(step_id) or not isinstance(answer, dict):
        return answer, Declaration(step_id=step_id, reading=NOT_DECLARED)

    if FIELD not in answer:
        return answer, Declaration(step_id=step_id, reading=NOT_DECLARED)

    remainder = {k: v for k, v in answer.items() if k != FIELD}
    raw = answer[FIELD]

    if raw is None:
        return remainder, Declaration(
            step_id=step_id, reading=NOT_DECLARED,
            malformed="the field was present and null")

    if isinstance(raw, list):
        entries = [e for e in (_normalise_entry(r) for r in raw) if e]
        if not raw:
            return remainder, Declaration(
                step_id=step_id, reading=NOTHING_MISSING)
        if not entries:
            return remainder, Declaration(
                step_id=step_id, reading=NOT_DECLARED,
                malformed=f"{len(raw)} entries, none of them readable")
        return remainder, Declaration(
            step_id=step_id, reading=DECLARED, entries=entries)

    entry = _normalise_entry(raw)
    if entry:
        return remainder, Declaration(
            step_id=step_id, reading=DECLARED, entries=[entry],
            malformed="answered with a single value, not a list")
    return remainder, Declaration(
        step_id=step_id, reading=NOT_DECLARED,
        malformed=f"unreadable value of type {type(raw).__name__}")


# ── The collector ───────────────────────────────────────────────────
#
# `present_llm_step` and the run summary are the same process, the same
# way `get_logger()` already is.  The record also goes onto the pipeline
# state, so an audit reading `pipeline_data.json` months later sees it
# without the process that collected it.

_collected: List[Declaration] = []


def record(declaration: Declaration) -> None:
    _collected.append(declaration)


def collected() -> List[Declaration]:
    return list(_collected)


def reset() -> None:
    _collected.clear()


def as_records(declarations=None) -> List[dict]:
    """The declarations in the shape that goes onto the state file."""
    rows = []
    for d in (collected() if declarations is None else declarations):
        row = {"step_id": d.step_id, "reading": d.reading,
               "entries": list(d.entries)}
        if d.malformed:
            row["malformed"] = d.malformed
        rows.append(row)
    return rows


def summary_lines(declarations=None) -> List[str]:
    """What the run summary prints, after `status` is decided.

    This is the READER, and it is the only one.  It prints; it assigns
    nothing and it fails nothing.
    """
    rows = collected() if declarations is None else declarations
    if not rows:
        return []
    lines = ["What the steps could not determine:"]
    declared = [d for d in rows if d.reading == DECLARED]
    empty = [d for d in rows if d.reading == NOTHING_MISSING]
    silent = [d for d in rows if d.reading == NOT_DECLARED]
    for d in declared:
        for entry in d.entries:
            detail = entry.get("what", "")
            helped = entry.get("what_would_have_helped")
            lines.append(f"  {d.step_id}: {detail}"
                         + (f"  [would have helped: {helped}]" if helped else ""))
    if empty:
        lines.append("  declared nothing missing: "
                     + ", ".join(sorted(d.step_id for d in empty)))
    if silent:
        lines.append("  did not answer the question (recorded as a "
                     "non-answer, not as \"nothing missing\"): "
                     + ", ".join(sorted(d.step_id for d in silent)))
    return lines
