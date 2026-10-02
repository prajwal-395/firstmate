"""A step with no creative brief ASKS, rather than planning in silence.

The captain, 2026-09-02: *"if this was like a TUI interface if a user
declines to attach a creative brief, then it should prompt the LLM to
ask some briefing questions for the user"*.

`brief_attachment.py` decides whether a brief is attached.  This is what
happens when it is not.  The eight steps whose manifests declare
`creative_brief` are asked, in the RENDERED schema, for
`briefing_questions`: what they would have wanted the captain to say,
before they decided what they decided.

## Who answers, and on which run

Nobody, on this one, and that is deliberate rather than unfinished.

The pipeline is not interactive.  A step cannot block on a human, and a
run that stopped to wait would turn every brief-less project into a run
that never completes.  So the interview is **collected, not conducted**:
the questions land in the run summary and on
`state["briefing_questions"]`, the captain reads them, and their answers
come back the way every other captain decision does - as a declaration
in the project, which here means writing or extending the brief and
attaching it.  The next run reads it.

That is a real answer channel and it is one turn long.  What it is NOT
is a second mechanism for carrying answers back: an answers file beside
the brief would be a brief under another name, and this module would
then own a document format.  The brief IS the answer format.

**It reports and it never gates.**  A step that asks nothing has not
failed and a step that asks five things has not failed either.  Nothing
reads a question's content or acts on it, because whatever picks which
questions matter becomes the interviewer (AGENTS.md 10.4).

## The twin, and why this is not a third mechanism

This is `undetermined.py`'s and `direction_contradiction.py`'s third
sibling: the same `declares`/`schema_entry`/`prompt_block`/`take`/
`record`/`summary_lines` surface, the same collector, the same route
into the prompt as DATA beside the context - which here is REUSE, not
a freeze workaround: one instruction is asked of every step that
declares a brief, and pasting it into each `handoff.md` would be the
same words in N files with nothing keeping them equal.  The captain's
freeze was lifted 2026-09-09 and this route was kept deliberately.
Same two counting rules - one record per model ATTEMPT,
numbered, with `final_by_step` the per-step reading; and
`state["briefing_questions"]` MERGED rather than replaced, with carried
rows marked `from_a_previous_run`.  Do not build a fourth shape.

It differs from both in one way that matters: **it is CONDITIONAL.**
The other two are asked on every run of a declaring step.  This one is
asked only when no brief was attached, because a step that was handed
the captain's brief and then asked what it wished the captain had said
is being invited to manufacture a gap - and noise here is worse than
silence, the argument `undetermined.prompt_block` already makes.

## Three readings, not two

``ASKED``           the step named one or more questions.
``NOTHING_TO_ASK``  it answered with `[]` - the material was enough
                    without a brief.  A real answer, and a useful one.
``NOT_DECLARED``    the key is absent.  A non-answer, recorded as one
                    and never read as "it had nothing to ask".

The same line `usable_ranges` `[]`/`unmeasured` draws (AGENTS.md 10.3).

`tests/unit/context/test_brief.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: The key the model writes. One spelling, here.
FIELD = "briefing_questions"

_STEPS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "steps")


def _steps_declaring_the_brief() -> frozenset:
    """Every step whose manifest declares `creative_brief`.

    DERIVED, not listed.  `direction_contradiction` derives its flagging
    set off the DAG for the same reason: a second list is a list that
    goes stale, and a step that starts or stops declaring the brief must
    not be able to fall out of the interview without anybody noticing.

    A step is asked because its manifest asked for a brief - not because
    its handoff mentions one.  `mesh_spine` declares the brief with no
    handoff line at all (AGENTS.md 10.1), and it plans the spine from
    the captain's intent as much as any of the other seven.
    """
    declaring = set()
    if not os.path.isdir(_STEPS_DIR):
        return frozenset()
    for entry in sorted(os.listdir(_STEPS_DIR)):
        manifest_path = os.path.join(_STEPS_DIR, entry, "manifest.json")
        if not os.path.exists(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            continue
        inputs = manifest.get("interface", {}).get("inputs", [])
        if any(i.get("name") == "creative_brief" for i in inputs):
            declaring.add(_node_id(entry, manifest))
    return frozenset(declaring)


def _node_id(directory: str, manifest: dict) -> str:
    """The DAG node id for a step directory.

    `library/tools/project_layout.node_id_for` is the ONE translator
    between `step_4_03_plan_vfx` and `plan_vfx` (AGENTS.md 10.1). It is
    imported lazily so this module stays importable from a subprocess
    that has only `library/` on its path.
    """
    try:
        from library.tools.project_layout import node_id_for
        resolved = node_id_for(directory)
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 - fall through to the manifest's own id
        pass
    return manifest.get("id", directory)


#: The steps that may be interviewed. Every step that asked for a brief.
ASKING_STEPS = _steps_declaring_the_brief()

ASKED = "asked"
NOTHING_TO_ASK = "nothing_to_ask"
NOT_DECLARED = "not_declared"

READINGS = (ASKED, NOTHING_TO_ASK, NOT_DECLARED)

#: What one question may say. `question` is the only part that must be
#: there: a question asked without a reason is still a question asked.
ENTRY_KEYS = ("question", "why_it_matters", "what_you_assumed_instead")


@dataclass
class Interview:
    """One step's questions, on one model attempt.

    `attempt` is LAST in the field order deliberately - these are built
    positionally, and a field inserted above `entries` rebinds every
    such call, which is how the sibling's `Flag` first broke.
    """

    step_id: str
    reading: str
    entries: List[Dict[str, str]] = field(default_factory=list)
    malformed: str = ""
    attempt: int = 1

    @property
    def asked_something(self) -> bool:
        return self.reading == ASKED


def asks(step_id: str, brief_attached: bool) -> bool:
    """Whether this step is interviewed on this run.

    Conditional on the brief, which is the one way this differs from its
    two siblings. A step handed the captain's own brief and then asked
    what it wished the captain had said is being invited to invent a
    gap.
    """
    return (not brief_attached) and step_id in ASKING_STEPS


def schema_entry() -> dict:
    """The field, in the shape `generate_output_schema_text` renders.

    `required` is False so a model can leave it empty without being told
    it failed - and an empty list and an absent key are read
    differently, which the prompt block says in as many words.
    """
    return {
        "name": FIELD,
        "type": "array",
        "required": False,
        "description": (
            "No creative brief was attached to this run. What would you "
            "have asked the person commissioning this video, before "
            "deciding what you just decided? Empty list [] if you needed "
            "nothing - that is a complete answer."
        ),
    }


def prompt_block(reading: str = "", basis: str = "") -> str:
    """The instruction, delivered as DATA beside the context.

    The handoffs are frozen, so this takes the route
    `music_measurement.MEASUREMENT_LEGEND` and `CUTS_LEGEND` already
    take: the words travel with the call rather than being edited into
    the prompt file.

    `reading` and `basis` come from `brief_attachment.Attachment`, so
    the model is told WHICH absence this is - a project that declined
    the brief it has, or a project that has none. It is the same
    distinction the run header prints, and a model that knows which one
    it is asks better questions.
    """
    which = {
        "declined": ("This project has a creative brief on file and chose "
                     "not to attach it to this run."),
        "none_declared": ("This project has no creative brief at all."),
    }.get(reading, "No creative brief was attached to this run.")
    return (
        "\n\n## No creative brief was attached\n\n"
        f"{which}"
        + (f" ({basis})" if basis else "")
        + "\n\n"
        "Decide anyway, in full, from the material you were given. Do not "
        "hedge your answer, do not leave a field blank waiting for one, "
        "and do not tell the reader you were missing a brief - the "
        "question below is where that goes.\n\n"
        f"Alongside your answer, return `{FIELD}`: the questions you would "
        "put to the person commissioning this video, if you could.\n\n"
        "Each entry is an object:\n\n"
        "```json\n"
        "{\n"
        '  "question": "what you would ask them, in their words not '
        'ours",\n'
        '  "why_it_matters": "the decision in your answer it would have '
        'changed",\n'
        '  "what_you_assumed_instead": "what you went with, having had to '
        'choose"\n'
        "}\n"
        "```\n\n"
        "Ask what you actually needed and could not get from the footage, "
        "the transcript or the measurements in front of you - who this is "
        "for, what it is meant to do, what the house style refuses. Do "
        "not ask what the material already answers: a question you could "
        "have looked up is noise, and noise here is worse than silence.\n\n"
        "Return `[]` when you needed nothing. An empty list is a complete "
        "answer. Omitting the field entirely is read as a non-answer and "
        "recorded as one, so return `[]` rather than leaving it out.\n"
    )


def _normalise_entry(raw: Any) -> Optional[Dict[str, str]]:
    if isinstance(raw, str):
        text = raw.strip()
        return {"question": text} if text else None
    if isinstance(raw, dict):
        entry = {}
        for key in ENTRY_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        if "question" not in entry:
            # Whatever the model called it, keep the words rather than
            # dropping the entry: a question asked in an unexpected key
            # is still a question asked.
            for value in raw.values():
                if isinstance(value, str) and value.strip():
                    entry["question"] = value.strip()
                    break
        return entry or None
    return None


def take(step_id: str, answer: Any, brief_attached: bool,
         asked: bool | None = None) -> tuple:
    """Split the questions out of a model answer.

    Returns `(answer_without_the_field, Interview)`.

    The field is REMOVED rather than carried with the answer: a step's
    outputs are what its manifest declares, `validate_step_output`
    refuses an unexpected key, and a post-bridge is handed the model's
    answer as its own input.

    `asked` overrides the `asks()` predicate: a project-declared creative
    task is interviewed when its own declaration asks for the brief
    (`creative_tasks.asks_interview`), which `asks()` cannot see - it
    reads step manifests, and a task is not a step. None means "ask the
    predicate", which is every step.
    """
    _asked = asked if asked is not None else asks(step_id, brief_attached)
    if not _asked or not isinstance(answer, dict):
        return answer, Interview(step_id=step_id, reading=NOT_DECLARED)

    if FIELD not in answer:
        return answer, Interview(step_id=step_id, reading=NOT_DECLARED)

    remainder = {k: v for k, v in answer.items() if k != FIELD}
    raw = answer[FIELD]

    if raw is None:
        return remainder, Interview(
            step_id=step_id, reading=NOT_DECLARED,
            malformed="the field was present and null")

    if isinstance(raw, list):
        entries = [e for e in (_normalise_entry(r) for r in raw) if e]
        if not raw:
            return remainder, Interview(
                step_id=step_id, reading=NOTHING_TO_ASK)
        if not entries:
            return remainder, Interview(
                step_id=step_id, reading=NOT_DECLARED,
                malformed=f"{len(raw)} entries, none of them readable")
        return remainder, Interview(
            step_id=step_id, reading=ASKED, entries=entries)

    entry = _normalise_entry(raw)
    if entry:
        return remainder, Interview(
            step_id=step_id, reading=ASKED, entries=[entry],
            malformed="answered with a single value, not a list")
    return remainder, Interview(
        step_id=step_id, reading=NOT_DECLARED,
        malformed=f"unreadable value of type {type(raw).__name__}")


# ── The collector ───────────────────────────────────────────────────
#
# The sibling's, deliberately: `present_llm_step` and the run summary are
# the same process, and the records also go onto the pipeline state so an
# audit reading `pipeline_data.json` months later sees them.

_collected: List[Interview] = []


def record(interview: Interview) -> None:
    """Collect one attempt's questions, NUMBERING them as they land.

    Counting what actually arrived rather than trusting the retry loop's
    index, for the reason `undetermined.record` gives: a call that raises
    before an answer is parsed records nothing.
    """
    interview.attempt = 1 + sum(
        1 for i in _collected if i.step_id == interview.step_id)
    _collected.append(interview)


def collected() -> List[Interview]:
    return list(_collected)


def reset() -> None:
    _collected.clear()


def as_records(interviews=None) -> List[dict]:
    """The interviews in the shape that goes onto the state file."""
    rows = []
    for i in (collected() if interviews is None else interviews):
        row = {"step_id": i.step_id, "reading": i.reading,
               "entries": list(i.entries), "attempt": i.attempt}
        if i.malformed:
            row["malformed"] = i.malformed
        rows.append(row)
    return rows


def final_by_step(interviews=None) -> List[Interview]:
    """One interview per step - the LAST attempt, in first-seen order."""
    rows = collected() if interviews is None else interviews
    latest: Dict[str, Interview] = {}
    for i in rows:
        latest[i.step_id] = i
    return list(latest.values())


def merge_records(previous, current) -> List[dict]:
    """Fold this run's rows onto what a previous run recorded.

    A step this run answered REPLACES every row that step had; a step it
    did not reach keeps its rows, MARKED `from_a_previous_run` - a
    carried row describes a run whose material may since have moved, and
    reading it as fresh is the confident-wrong-answer failure these
    channels exist to avoid. The mark, once set, stays set.
    """
    fresh = {row.get("step_id") for row in current}
    merged = []
    for row in previous or []:
        if not isinstance(row, dict) or row.get("step_id") in fresh:
            continue
        carried = dict(row)
        carried["from_a_previous_run"] = True
        merged.append(carried)
    merged.extend(current)
    return merged


def summary_lines(interviews=None) -> List[str]:
    """What the run summary prints, after `status` is decided.

    This is the READER, and it is the one that closes the loop: the
    captain reads these questions and answers them by writing a brief.
    It prints; it assigns nothing and it fails nothing.
    """
    every_attempt = collected() if interviews is None else interviews
    if not every_attempt:
        return []
    rows = final_by_step(every_attempt)
    lines = ["Briefing questions (no creative brief was attached to this run):"]
    retried = sorted(f"{i.step_id} ({i.attempt} attempts)"
                     for i in rows if i.attempt > 1)
    asked = [i for i in rows if i.reading == ASKED]
    empty = [i for i in rows if i.reading == NOTHING_TO_ASK]
    silent = [i for i in rows if i.reading == NOT_DECLARED]
    for i in asked:
        for entry in i.entries:
            question = entry.get("question", "")
            assumed = entry.get("what_you_assumed_instead")
            lines.append(f"  {i.step_id}: {question}"
                         + (f"  [assumed instead: {assumed}]" if assumed else ""))
    if empty:
        lines.append("  had nothing to ask: "
                     + ", ".join(sorted(i.step_id for i in empty)))
    if silent:
        lines.append("  did not answer the question (recorded as a "
                     "non-answer, not as \"nothing to ask\"): "
                     + ", ".join(sorted(i.step_id for i in silent)))
    if retried:
        lines.append("  answered more than once (the reading above is the "
                     "last attempt): " + ", ".join(retried))
    if asked:
        lines.append("  Answer them by writing or extending this project's "
                     "creative brief and attaching it - see "
                     "library/tools/brief_attachment.py.")
    return lines
