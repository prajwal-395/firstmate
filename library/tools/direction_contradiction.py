"""direction_contradiction.py - a step says its measurements disagree with
the creative direction it was handed, and complies anyway.

On the 29 Aug 2026 run of project 001 the words "emotion" and "energy"
appear ZERO times in the semantic documents and FOUR times each in the
`creative_direction` block carried into station 2 and station 3.  Step
2.01 reads the footage once, writes down how it feels, and every creative
step after it inherits that reading whole.  None of them had any way to
say "what I was routed disagrees with what I was told".  One early
judgement governed everything after it, unchallengeably.

That only became a real defect when prosody landed (#417, 2026-09-01):
prosody is the first DETERMINISTIC measurement that CAN disagree with an
inherited affect reading - pitch, speaking rate, voice quality and
intensity, measured by signal processing rather than judged.  That is
exactly why the captain wanted it.

**The captain's ruling of 2026-09-01 is the shape of this module.**  A
step MAY FLAG a contradiction and MAY NOT ACT ON ONE.  Flagging is the
whole design; deviating is what the ruling was against.  Nothing here
reads the content of a flag, nothing gates on one, and the field is taken
out of the answer before anything validates it - so a step that flags
produces exactly the output it would have produced silently.  Whoever
decides what a contradiction means becomes the director, and that is the
captain (AGENTS.md 10.4, 10.5).

**It is the wave-3 shape carrying different cargo.**
`library/tools/undetermined.py` is the sibling: a structured field, split
out of the answer before validation, reporting and never gating, with an
empty answer and an absent one read differently.  This is deliberately
its twin rather than a second invention - same `take`/`record`/
`summary_lines` surface, same collector, same route into the prompt as
DATA beside the context because the handoffs are frozen.

**Where it differs, and why.**  The sibling has THREE readings; this has
FOUR.  A gap is named by naming it, so `what` alone is a complete entry
there.  A CONTRADICTION is not: it is a claim ABOUT a measurement, and a
claim with no measurement behind it is a model politely disagreeing with
its brief.  The failure mode the sibling was designed against - a field
models fill with noise on every call - has a sharper form here, because
prose disagreement is the cheapest thing a model can produce.  So an
entry that names no measurement, or names one the step was not routed, or
names a direction field step 2.01 is not asked for, is UNEVIDENCED: kept
verbatim, reported as itself, and never counted as a contradiction.

    CONTRADICTED           at least one entry names a direction field, a
                           measurement, and the routed input it came
                           from.  Evidence travelling with the claim.
    NOTHING_CONTRADICTED   the model answered `[]`.  A real answer, and
                           the expected one on most calls.
    UNEVIDENCED            the model wrote entries and not one of them
                           carried a measurement.  Neither a
                           contradiction nor a silence.
    NOT_DECLARED           the key is absent.  The model was asked and
                           did not reply, recorded as a non-answer and
                           never as "nothing contradicted".

Same line the repository draws everywhere between an admitted absence and
a measured emptiness: `usable_ranges` `[]`/`unmeasured` against
`[]`/`deterministic_v1` (AGENTS.md 10.3), `primary_subject_visible` None
against `[]`, `speech_present` True-or-None-never-False.

**Which steps can flag, argued rather than assumed.**  A contradiction
needs THREE things in one step: a prompt to say it in, an inherited
direction claim, and a measurement of the material to hold against it.
So a step flags when it reaches a model, declares `creative_direction`
as an input, and is routed at least one measurement.  Step 2.01 is
excluded because it AUTHORS the direction - it has nothing inherited to
contradict.  The model-reaching half is
`undetermined.DECLARING_STEPS`, borrowed rather than restated so the two
cannot drift; the routed half is derived from `dag.json` rather than
listed here, so inserting an edge cannot leave this stale.

That comes to NINE steps - every step that reaches a model except the
one that writes the direction.  `creative_cohesion` (5.03) declares
`creative_direction`, is deterministic, and is excluded with a reason
rather than by omission: a step with no prompt cannot be asked, and
inventing a comparison for it in code would be this module deciding that
a measurement disagrees, which is the captain's call.

`color_grade` (5.01) was in that excluded list until 2026-09-03 and
joined by DERIVATION alone when it stopped being deterministic - it
declares `creative_direction`, and the vision documents its bridge joins
scene descriptions from are a routed measurement.  It is the clearest
case the channel has: a direction that says the piece is vibrant, held
against nine clips one of which measures 53 luma.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/direction_contradiction.py`. On 001's 29 Aug run "emotion" and "energy" appear ZERO times in the semantic documents and FOUR times each in the `creative_direction` block at stations 2 and 3: step 2.01 reads the footage once and every creative step after it inherits that reading whole, with no way to say what it measured disagrees. Prosody (#417) is the first deterministic measurement that CAN disagree with an inherited affect reading.
- **Captain's ruling, 2026-09-01: a step MAY FLAG and MAY NOT ACT.** Nothing reads a flag's content, nothing gates on one, and the field is SPLIT OUT of the answer before validation - so the output a flagging step produces is byte-for-byte the one it would have produced silently. Escalation to the captain happens OUTSIDE the pipeline.
- **It is `undetermined.py`'s twin carrying different cargo** - same `take`/`record`/`summary_lines` surface, same collector, same route into the prompt as DATA beside the context because the handoffs are frozen. Do not build a second mechanism.
- **FOUR readings, not the sibling's three.** A gap is named by naming it; a CONTRADICTION is a claim ABOUT a measurement, and a claim with no measurement is a model politely disagreeing with its brief. `contradicted` needs an entry naming a `direction_field` in `DIRECTION_KEYS`, a `measurement`, and a `measured_in` the step was really routed. Everything else is `unevidenced` - kept verbatim, reported as itself, and NEVER counted as a contradiction. `nothing_contradicted` (`[]`) and `not_declared` (key absent) are the other two, and they are not each other.
- **Every step that reaches a model except the one that authors the direction - nine of them.** `color_grade` joined on 2026-09-03 by DERIVATION alone when 5.01 stopped being deterministic. A step needs a prompt to say it in, an inherited direction claim and a routed measurement. The model-reaching half is borrowed from `undetermined.DECLARING_STEPS`; the routed half is DERIVED from `dag.json`, so a new edge cannot leave it stale, and `render_motion_graphics` joined by that derivation alone when 4.06 stopped being deterministic. `creative_cohesion` declares `creative_direction` and is deterministic, so it has nothing to say it in.
- **`MEASURED_OUTPUTS` and `DECLINED_OUTPUTS` must together account for every output of every deterministic step**, and an unaccounted one raises at import - a new deterministic output says which side it is on before it can go quiet.
- **Its collector is the sibling's, and so are the sibling's two rules**: one flag per model ATTEMPT, numbered, with `final_by_step` the per-STEP reading the summary prints; and `state["direction_contradictions"]` MERGED rather than replaced, carried rows marked `from_a_previous_run`. The two channels print into the same run summary, so they must count on the same basis.
- **`Flag` and `Declaration` are constructed POSITIONALLY, so a new field goes LAST.** Added above `entries`, it takes the entries and the real entries land in the field after it - no error, just wrong rows, until something compares them.
- `tests/test_direction_contradiction.py`, `tests/test_undetermined_declaration.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from library.tools.creative_direction import DIRECTION_KEYS
from library.tools.undetermined import DECLARING_STEPS as _REACHES_A_MODEL

# The key the model writes.  One spelling, here, because a key name
# spelled twice is this repository's dominant bug class.
FIELD = "contradicts_direction"

# The four readings.  Spelled differently on purpose.
CONTRADICTED = "contradicted"
NOTHING_CONTRADICTED = "nothing_contradicted"
UNEVIDENCED = "unevidenced"
NOT_DECLARED = "not_declared"

READINGS = (CONTRADICTED, NOTHING_CONTRADICTED, UNEVIDENCED, NOT_DECLARED)

# What one entry may say.  The first three are what makes it a
# contradiction rather than an opinion; the rest are optional colour.
ENTRY_KEYS = (
    "direction_field",
    "direction_said",
    "measurement",
    "measured_in",
    "why_they_disagree",
    "complied_by",
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DAG = os.path.join(_REPO, "library", "processes", "edit_video", "dag.json")
_STEPS = os.path.join(_REPO, "library", "steps")


# ── What counts as a measurement ────────────────────────────────────
#
# A measurement is a value a DETERMINISTIC step produced by measuring
# the material - the footage, the speech, the chosen track.  It is not
# every deterministic output: `subtitle_plan` and `assembly_manifest`
# are consolidations of decisions taken upstream, and holding a decision
# against the direction that produced it would prove nothing.
#
# The two tables below must TOGETHER account for every output of every
# deterministic step, and `_assert_deterministic_outputs_accounted`
# raises at import when one is unaccounted for.  Same shape as
# `creative_direction.MECHANICALLY_READ_KEYS` and `PROMPT_ONLY_KEYS`:
# a new deterministic output has to say which side it is on before it
# can go quiet.

MEASURED_OUTPUTS = {
    "clip_catalog": "1.02 - measured duration, frame rate and stored resolution per clip",
    "project_fps": "1.02 - the footage's measured frame rate",
    "source_resolution": "1.02 - the footage's measured stored resolution",
    "semantic_analysis_documents": "1.03 - the vision pass's per-window scene, camera, action and assessment measurements",
    "temporal_event_indices": "1.04 - the same index, per clip, as the event view",
    "prosody_analysis": "1.05 - parselmouth pitch contour, speaking rate, voice quality and intensity",
    "object_segmentation": "1.06 - SAM 2 subject masks (unwired; nothing consumes them)",
    "ocr_extraction": "1.07 - on-screen text read off the frames",
    "music_analysis": "2.06 - librosa tempo, beat grid and energy dynamics of the chosen track",
}

DECLINED_OUTPUTS = {
    "sfx_library_status": "0.01 validates a SHARED library, not this project's material",
    "project_config": "1.01 - the project's own declarations, not a measurement of anything",
    "raw_footage_files": "1.01 - a listing of what is on disk",
    "skipped_files": "1.01/1.02 - a listing of what was not read",
    "total_files": "1.01 - a count of a listing",
    "total_clips": "1.02 - a count of a listing",
    "total_clips_analyzed": "1.03 - a count of a listing",
    "index_dir": "1.04 - a path",
    "source": "1.04 - which route produced the index",
    "total_failed": "1.04 - a count",
    "total_indexed": "1.04 - a count",
    "total_reused": "1.04 - a count",
    "a_roll_assignments": "3.01 places what 2.02 already chose; a placement is not a measurement",
    "hook_assignment": "3.01 - the same placement, for the hook",
    "total_a_roll_segments": "3.01 - a count",
    "subtitle_plan": "4.01 renders decisions already taken into caption cards",
    "subtitle_overlay": "4.05 - a rendered artifact",
    # 4.06 became hybrid on 2026-09-02 and its outputs are therefore no
    # longer required to be accounted for here.  The rows stay because
    # the claim they make is unchanged - both are rendered artifacts,
    # not measurements - and deleting a true row to satisfy a coverage
    # check is how a table starts disagreeing with the system.
    "motion_graphics_overlay": "4.06 - a rendered artifact",
    "timed_text_overlay": "4.06 - a rendered artifact",
    # 5.01 became hybrid on 2026-09-03 and its output is therefore no
    # longer required to be accounted for here.  The row stays because
    # the claim it makes is unchanged and deleting a true row to satisfy
    # a coverage check is how a table starts disagreeing with the system.
    "color_grade_spec": "5.01 carries a declared look plus a colourist's judgement; neither is a measurement of the material",
    "audio_mix_spec": "5.02 - a mix plan. It embeds measured levels, but it is routed to no step holding the direction, so nothing here could read them",
    "cohesion_review": "5.03 observes decisions, not material",
    "assembly_manifest": "5.04 consolidates every decision taken; holding it against the direction proves nothing",
}


def _load_dag() -> dict:
    with open(_DAG, encoding="utf-8") as fh:
        return json.load(fh)


def _manifest_for(step_ref: str) -> dict:
    dirname = step_ref.rsplit("/", 1)[-1]
    with open(os.path.join(_STEPS, dirname, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _build() -> tuple:
    dag = _load_dag()
    manifests, deterministic, outputs = {}, set(), {}
    for node in dag["nodes"]:
        node_id = node["id"]
        manifest = _manifest_for(node["step_ref"])
        manifests[node_id] = manifest
        if manifest.get("classification", {}).get("determinism") == "deterministic":
            deterministic.add(node_id)
        outputs[node_id] = [
            o.get("name") for o in manifest.get("interface", {}).get("outputs", [])
        ]

    # A model-reaching step need not be in the DAG. `select_reels` runs
    # on a FINISHED cut rather than inside the pipeline that produces
    # one, and this loop only ever walked DAG nodes - so an unwired model
    # step was invisible here, and the coverage assertion in
    # tests/test_direction_contradiction.py could never be satisfied for
    # one. Read those from the layout, which is where an unwired step
    # declares itself and its reason.
    from library.tools.project_layout import STEPS as _STEP_DIRS

    for step_dir in _STEP_DIRS:
        if step_dir.wired or step_dir.node_id in manifests:
            continue
        if step_dir.node_id not in _REACHES_A_MODEL:
            continue
        manifests[step_dir.node_id] = _manifest_for(
            f"step_{step_dir.dirname}")

    unaccounted = sorted({
        name
        for node_id in deterministic
        for name in outputs[node_id]
        if name not in MEASURED_OUTPUTS and name not in DECLINED_OUTPUTS
    })
    if unaccounted:
        raise RuntimeError(
            "library/tools/direction_contradiction.py does not account for "
            f"the deterministic output(s) {', '.join(unaccounted)}. Add each "
            "to MEASURED_OUTPUTS (a measurement of the material a step could "
            "hold against the direction) or to DECLINED_OUTPUTS with the "
            "reason it is not one."
        )

    routed: Dict[str, Dict[str, str]] = {}
    for edge in dag.get("edges", []):
        producer, consumer = edge.get("from"), edge.get("to")
        if producer not in deterministic:
            continue
        for produced, received in (edge.get("data_mapping") or {}).items():
            if produced in MEASURED_OUTPUTS:
                routed.setdefault(consumer, {})[received] = (
                    f"{MEASURED_OUTPUTS[produced]} (routed as `{received}`)"
                )
    return manifests, routed


_MANIFESTS, _ROUTED = _build()


# Measurements a step holds that no DAG edge carries.  Both are real
# routes the repository already documents, and neither is inferable from
# `dag.json`, so each is listed with the reason it cannot be.
OFF_DAG_MEASUREMENTS = {
    "select_reels": {
        "reel_candidates": (
            "3.04's own pre-bridge collapses the cut into turns and "
            "measures every candidate stretch - turn count, how often it "
            "changes hands, each speaker's share, where the hosts pitch, "
            "length against the brief, and takes recorded more than once "
            "(library/tools/reel_exchange.py). It is the step's own "
            "output, so no edge carries it in - and it is exactly where "
            "the direction can be contradicted: a stretch the brief "
            "calls a highlight that the turns show is one person talking."
        ),
    },
    "music_selection": {
        "music_candidates": (
            "2.04's own pre-bridge measures every candidate track - "
            "integrated loudness, loudness range, RMS spread, the played "
            "window's envelope and the share of energy in the speech band "
            "(library/tools/music_measurement.py). It is the step's own "
            "output, so no edge carries it in."
        ),
    },
    "review_rough_cut": {
        "render_qa_findings": (
            "6.02's measurements of the LAST render. `validate` is the "
            "final node and 3.03 is in phase 3, so an edge would be a back "
            "edge; they travel by name in `gather_step_inputs` "
            "(AGENTS.md 10.4)."
        ),
    },
}

# The step that AUTHORS the direction cannot inherit one.
AUTHORS_THE_DIRECTION = "creative_direction"


def _flagging_steps() -> Dict[str, Dict[str, str]]:
    steps: Dict[str, Dict[str, str]] = {}
    for node_id, manifest in _MANIFESTS.items():
        if node_id == AUTHORS_THE_DIRECTION:
            continue
        if node_id not in _REACHES_A_MODEL:
            # No prompt, nothing to say it in.
            continue
        inputs = {
            i.get("name")
            for i in manifest.get("interface", {}).get("inputs", [])
        }
        if "creative_direction" not in inputs:
            continue
        evidence = {
            name: why
            for name, why in _ROUTED.get(node_id, {}).items()
            if name in inputs
        }
        evidence.update(OFF_DAG_MEASUREMENTS.get(node_id, {}))
        if evidence:
            steps[node_id] = evidence
    return steps


# node id -> {input name: what it measures}.  The steps that hold BOTH an
# inherited direction and something measured to hold against it.
EVIDENCE_SOURCES = _flagging_steps()
FLAGGING_STEPS = frozenset(EVIDENCE_SOURCES)


def flags(step_id: str) -> bool:
    return step_id in EVIDENCE_SOURCES


def evidence_sources(step_id: str) -> Dict[str, str]:
    return dict(EVIDENCE_SOURCES.get(step_id, {}))


# ── One flag ────────────────────────────────────────────────────────

@dataclass
class Flag:
    """One step's answer to the question, on one attempt.

    `attempt` is the sibling's field, for the sibling's reason
    (`undetermined.Declaration`): a step whose answer fails QA is asked
    again, so one step can produce three flags in a run, and unnumbered
    they are indistinguishable rows that make a reader counting steps
    count model calls. Every attempt is kept - a step flagging the SAME
    contradiction three times running is evidence, not noise - and
    `final_by_step` is what a reader wanting one row per step calls.
    It is declared LAST, below.
    """

    step_id: str
    reading: str
    entries: List[Dict[str, str]] = field(default_factory=list)
    """The entries that carried a measurement. Only these are
    contradictions."""

    unevidenced: List[Dict[str, str]] = field(default_factory=list)
    """Entries the model wrote that named no measurement, or named one
    the step was not routed, or named a direction field 2.01 is not asked
    for. Kept verbatim and reported as themselves: dropping them would
    hide that the field is being filled with prose, which is the failure
    mode this design is against."""

    malformed: str = ""
    """Set when the key was present but not a shape this could read. A
    model that answered in the wrong shape did answer, and that is a
    different thing to fix from a model that stayed silent."""

    attempt: int = 1
    """Which model call this was, stamped by `record`. LAST in the field
    order deliberately: `Flag` is constructed positionally, so inserting
    a field above `entries` silently rebinds every such call."""

    @property
    def contradicted(self) -> bool:
        return self.reading == CONTRADICTED


def schema_entry() -> dict:
    """The field, in the shape `generate_output_schema_text` renders."""
    return {
        "name": FIELD,
        "type": "array",
        "required": False,
        "description": (
            "Measurements you were routed that contradict the creative "
            "direction you were handed. Empty list [] if nothing you "
            "measured disagreed - that is a complete answer and the "
            "expected one. Flagging does not change your answer: comply "
            "with the direction regardless."
        ),
    }


def prompt_block(step_id: str) -> str:
    """The instruction, delivered as DATA beside the context.

    The handoffs are frozen, so this takes the route
    `music_measurement.MEASUREMENT_LEGEND` and `CUTS_LEGEND` already
    take: the words travel with the call rather than being edited into
    the prompt file.

    The block is per step because the two closed vocabularies it names -
    the direction's own fields, and the measurements THIS step was
    routed - are what make an entry checkable.
    """
    sources = evidence_sources(step_id)
    lines = [
        "\n\n## Where your measurements disagree with the direction\n\n",
        f"Alongside your answer, return `{FIELD}`: the places where "
        "something you were MEASURED disagrees with the creative "
        "direction you were handed.\n\n",
        "**Flagging does not change what you do.** The creative "
        "direction governs your answer whether you flag or not. Produce "
        "the answer the direction asks for, and record the disagreement "
        "beside it. Do not deviate, hedge or split the difference: the "
        "flag is read by a human, and it is the only thing here that "
        "decides anything.\n\n",
        "Each entry is an object:\n\n",
        "```json\n"
        "{\n"
        '  "direction_field": "which creative_direction field it '
        'contradicts",\n'
        '  "direction_said": "what that field said, quoted",\n'
        '  "measurement": "the measured value, with its number or its '
        'measured words",\n'
        '  "measured_in": "which of your routed inputs you read it in",\n'
        '  "why_they_disagree": "one sentence",\n'
        '  "complied_by": "what you did anyway"\n'
        "}\n"
        "```\n\n",
        "`direction_field` must be one of: " + ", ".join(DIRECTION_KEYS) + ".\n\n",
        "`measured_in` must be one of the measurements you were routed:\n\n",
    ]
    for name, why in sorted(sources.items()):
        lines.append(f"- `{name}` - {why}\n")
    lines.append(
        "\nAn entry with no `measurement`, or naming an input outside "
        "that list, is NOT a contradiction and is recorded separately as "
        "an unevidenced disagreement. The evidence has to travel with "
        "the claim: an opinion that the direction is wrong is not what "
        "this field collects, and prose disagreement here is worse than "
        "silence.\n\n"
        "Return `[]` when nothing you measured disagreed. An empty list "
        "is a complete answer and it is the expected one on most calls - "
        "it is read as \"nothing contradicted\", not as a failure to "
        "reply. Omitting the field entirely is read as a non-answer and "
        "recorded as one, so return `[]` rather than leaving it out.\n"
    )
    return "".join(lines)


def _normalise_entry(raw: Any) -> Optional[Dict[str, str]]:
    if isinstance(raw, str):
        text = raw.strip()
        # A bare string can carry no measurement, so it can only ever be
        # unevidenced. It is kept rather than dropped.
        return {"direction_said": text} if text else None
    if not isinstance(raw, dict):
        return None
    entry = {}
    for key in ENTRY_KEYS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    return entry or None


def is_evidenced(step_id: str, entry: Dict[str, str]) -> bool:
    """Whether an entry carries the evidence that makes it a contradiction.

    Three conditions, and all three are closed vocabularies rather than
    judgements: the direction field is one step 2.01 is asked for, the
    measurement is present, and the input it was read in is one this step
    was actually routed. Nothing here judges whether the disagreement is
    real - that is the captain's, and a rule deciding it would become the
    director (AGENTS.md 10.5).
    """
    return (
        entry.get("direction_field") in DIRECTION_KEYS
        and bool(entry.get("measurement"))
        and entry.get("measured_in") in EVIDENCE_SOURCES.get(step_id, {})
    )


def take(step_id: str, answer: Any) -> tuple:
    """Split the flag out of a model answer.

    Returns `(answer_without_the_field, Flag)`.

    The field is REMOVED from the answer rather than carried with it, for
    the reason its sibling gives: a step's outputs are what its manifest
    declares, `validate_step_output` refuses an unexpected extra key, and
    a post-bridge is handed the model's answer as its own input. It is
    also what makes compliance structural rather than promised - the
    output that leaves this function is the output the step would have
    produced without the field.
    """
    if not flags(step_id) or not isinstance(answer, dict):
        return answer, Flag(step_id=step_id, reading=NOT_DECLARED)

    if FIELD not in answer:
        return answer, Flag(step_id=step_id, reading=NOT_DECLARED)

    remainder = {k: v for k, v in answer.items() if k != FIELD}
    raw = answer[FIELD]

    if raw is None:
        return remainder, Flag(
            step_id=step_id, reading=NOT_DECLARED,
            malformed="the field was present and null")

    malformed = ""
    if isinstance(raw, list):
        rows = raw
    else:
        rows = [raw]
        malformed = "answered with a single value, not a list"

    if isinstance(raw, list) and not raw:
        return remainder, Flag(step_id=step_id, reading=NOTHING_CONTRADICTED)

    entries = [e for e in (_normalise_entry(r) for r in rows) if e]
    if not entries:
        return remainder, Flag(
            step_id=step_id, reading=NOT_DECLARED,
            malformed=f"{len(rows)} entries, none of them readable")

    evidenced = [e for e in entries if is_evidenced(step_id, e)]
    unevidenced = [e for e in entries if not is_evidenced(step_id, e)]
    reading = CONTRADICTED if evidenced else UNEVIDENCED
    return remainder, Flag(step_id=step_id, reading=reading, entries=evidenced,
                           unevidenced=unevidenced, malformed=malformed)


# ── The collector ───────────────────────────────────────────────────
#
# `present_llm_step` and the run summary are the same process, the same
# way `get_logger()` and `undetermined` already are. The record also goes
# onto the pipeline state, so an audit reading `pipeline_data.json`
# months later sees it without the process that collected it.

_collected: List[Flag] = []


def record(flag: Flag) -> None:
    """Collect one attempt's flag, NUMBERING it as it lands.

    Counted off what has actually arrived rather than passed in by the
    retry loop, for the reason `undetermined.record` states: a call that
    raises before an answer is parsed records nothing, so a loop index
    and the stored rows can drift apart.
    """
    flag.attempt = 1 + sum(1 for f in _collected if f.step_id == flag.step_id)
    _collected.append(flag)


def collected() -> List[Flag]:
    return list(_collected)


def reset() -> None:
    _collected.clear()


def as_records(flags_=None) -> List[dict]:
    """The flags in the shape that goes onto the state file."""
    rows = []
    for f in (collected() if flags_ is None else flags_):
        row = {"step_id": f.step_id, "reading": f.reading,
               "entries": list(f.entries), "attempt": f.attempt}
        if f.unevidenced:
            row["unevidenced"] = list(f.unevidenced)
        if f.malformed:
            row["malformed"] = f.malformed
        rows.append(row)
    return rows


def final_by_step(flags_=None) -> List[Flag]:
    """One flag per step - the LAST attempt, in first-seen order.

    The last attempt is the one whose answer the step returned. The
    earlier ones are still in `collected()` with their own numbers.
    """
    rows = collected() if flags_ is None else flags_
    latest: Dict[str, Flag] = {}
    for f in rows:
        latest[f.step_id] = f
    return list(latest.values())


def merge_records(previous, current) -> List[dict]:
    """Fold this run's flag rows onto what a previous run recorded.

    `state["direction_contradictions"]` was REPLACED, so a `--rerun` of
    one step erased every other step's flags - the narrowest possible run
    destroying the record. Same shape and same reasoning as
    `undetermined.merge_records`: a step this run answered replaces its
    own rows at every attempt, and a step it did not reach keeps them,
    MARKED, because a carried flag is a claim about measurements that may
    since have moved.
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


def summary_lines(flags_=None) -> List[str]:
    """What the run summary prints, after `status` is decided.

    This is the READER, and it is the only one. It prints; it assigns
    nothing and it fails nothing. Escalating a contradiction to the
    captain happens outside the pipeline.
    """
    every_attempt = collected() if flags_ is None else flags_
    if not every_attempt:
        return []
    # One row per STEP - the last attempt.  Printing every attempt made a
    # reader counting names count model calls.
    rows = final_by_step(every_attempt)
    lines = ["Where a step's measurements contradicted the direction:"]
    retried = sorted(f"{f.step_id} ({f.attempt} attempts)"
                     for f in rows if f.attempt > 1)
    contradicted = [f for f in rows if f.reading == CONTRADICTED]
    empty = [f for f in rows if f.reading == NOTHING_CONTRADICTED]
    unevidenced = [f for f in rows if f.reading == UNEVIDENCED]
    silent = [f for f in rows if f.reading == NOT_DECLARED]
    for f in contradicted:
        for entry in f.entries:
            lines.append(
                f"  {f.step_id}: `{entry.get('direction_field')}` said "
                f"\"{entry.get('direction_said', '')}\" - measured "
                f"\"{entry.get('measurement')}\" in "
                f"`{entry.get('measured_in')}`"
            )
            why = entry.get("why_they_disagree")
            if why:
                lines.append(f"      {why}")
            complied = entry.get("complied_by")
            if complied:
                lines.append(f"      complied by: {complied}")
    if contradicted:
        lines.append("  The step FLAGGED and COMPLIED. Nothing in the "
                     "pipeline acts on this; it is for the captain.")
    for f in unevidenced:
        lines.append(
            f"  {f.step_id}: {len(f.unevidenced)} disagreement(s) carrying "
            "no routed measurement - recorded as unevidenced, NOT as a "
            "contradiction")
    if empty:
        lines.append("  measured nothing that contradicted: "
                     + ", ".join(sorted(f.step_id for f in empty)))
    if silent:
        lines.append("  did not answer the question (recorded as a "
                     "non-answer, not as \"nothing contradicted\"): "
                     + ", ".join(sorted(f.step_id for f in silent)))
    if retried:
        # Said rather than hidden: the reading above is the LAST attempt's.
        lines.append("  answered more than once (the reading above is the "
                     "last attempt): " + ", ".join(retried))
    return lines
