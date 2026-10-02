"""decided_value.py - how a creative value gets decided, in one place.

A creative value is decided by a PRECEDENCE, not by a constant (captain,
2026-09-16): a stated preference, then the declared direction, then the
model reasoning over measured signal, then a fallback that names whose
preference it is.  This module is the only implementation of it, so the
next creative value does not need a private ladder or a constant.

**Five readings, spelled differently on purpose.**

    STATED        the project said so in project.yaml or a brand slot, or
                  the user said so in the per-user taste profile. The
                  project wins; the record names the source and speaker.
    DIRECTED      a value the creative direction really DECLARED.  A rule
                  acting on a declared value is not a fallback (AGENTS.md
                  10.5), which is what makes this rung legal at all.
    REASONED      the model answered THIS RUN, over measurements it was
                  SHOWN.  The record carries its own `why` and the
                  measurements it read.  An answer with no `why` is
                  DROPPED, not kept.
    FALLBACK      nothing above answered and the slot has a REGISTERED
                  fallback.  Recorded AS a fallback, naming WHOSE
                  preference it is and what would have superseded it.
    UNDETERMINED  nothing above answered and there is no registered
                  fallback.  There is NO VALUE.  The consumer drops with
                  the reason or refuses.  Never 0, never a stand-in.

That is the line this repository draws everywhere between an admitted
absence and a measured emptiness (AGENTS.md 10.3, `undetermined`).

**A rung that cannot be reached is SKIPPED WITH ITS REASON, never
guessed through.**  If a slot's measurement was not taken, the model is
not asked to reason about a number nobody measured, and the decision says
so.

**The model may answer in units a person can judge, and the engine
SOLVES the delivered value.**  A slot may declare a `solver`: the model
names how far the voice should sit above the bed, and the gain that
reaches the renderer is arithmetic over that answer and two measurements
(`_solve_bed_gain`).  The solver is REGISTERED on the slot so a second
formula cannot exist.

**A stated preference is the person's number, never the engine's.**
`stated_preference` reads the project, then the user's taste profile.
Neither is required, and an absent preference falls to the model
reasoning over measurement - never to a value in a shipped configuration
file; neither source ships with the engine.

**Nothing reads a decision to decide something else about taste.**
`decide` returns a value and a record.  Whatever ranked, filtered or
second-guessed the model's answer would become the chooser (AGENTS.md
10.5).

**A value with no decision record is not a value.**  `assert_decided`
refuses one, fail-closed.

Rules
-----
**A creative value is DECIDED, and the decision says what decided it.**
One enumeration, `library/tools/decided_value.py`: `SLOTS` is the
registry and `decide` is the only ladder.
- **Five readings, and `FALLBACK` is visible AS one.** A fallback names
  WHOSE preference it is and what supersedes it; an engine-owned
  fallback is refused at import.
- **A slot's deciding step must REACH A MODEL** (`undetermined.DECLARING_STEPS`),
  must declare the slot in its manifest's top-level `decides`, and its
  prompt must carry the question on the same run.
- **A declared slot has a READER**, the way a declared output does
  (`library/tools/output_contract.py`).
- **The trace is MERGED, never replaced**, and a row from an earlier run
  is marked so it is never read as fresh.
- `tests/test_decided_value.py`.

The rulings behind the precedence, and the per-module basis enumerations
and constants it replaces: docs/evidence/decided_value.md.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# The key the model writes, and the key the record lands under.  One
# spelling each, here, because a key name spelled twice is this
# repository's dominant bug class (AGENTS.md 10.1).
FIELD = "value_decisions"
STATE_KEY = "value_decisions"

# How the runner hands a split-out answer to a post-bridge.  The field is
# taken out of the model's answer before anything validates it - the same
# rule its three sibling appenders live by - so a post-bridge that needs
# the answer receives it beside its inputs rather than inside them.  The
# double underscore keeps it out of `validate_step_output`'s way, exactly
# as `second_pass.PASS_KEY` does.
MERGE_KEY = "__value_decisions"

# ── The five readings ────────────────────────────────────────────────
STATED = "stated"
DIRECTED = "directed"
REASONED = "reasoned"
FALLBACK = "fallback"
UNDETERMINED = "undetermined"

BASES = (STATED, DIRECTED, REASONED, FALLBACK, UNDETERMINED)

# The ladder, in the captain's order.  `UNDETERMINED` is not a rung: it
# is what is left when every rung was tried and none answered.
LADDER = (STATED, DIRECTED, REASONED, FALLBACK)


class UnknownSlot(KeyError):
    """A read named a value slot that is not in the registry."""


class UndecidedValue(ValueError):
    """A slot value arrived with no decision record behind it."""


class MalformedSlot(ValueError):
    """A slot row that cannot be true - refused at import."""


@dataclass(frozen=True)
class Measurement:
    """One measurement that makes a judgement possible, and who takes it.

    `produced_by` is a real dotted path - `module::function` - because a
    slot claiming a measurement nothing produces is the shape this
    module exists to refuse.  `tests/test_decided_value.py` resolves
    every one.
    """
    name: str
    produced_by: str
    what: str


@dataclass(frozen=True)
class Fallback:
    """A value that survives only as an admitted absence of a decision.

    `whose` is REQUIRED and may not be the engine.  A fallback nobody
    owns is a constant with better paperwork, which is the whole finding:
    the captain's own reading is that the caption style "is the style
    that is preferred for the Lucie videos", and that sentence is what
    makes it legal.  `superseded_by` states what would have answered
    instead, so a reader can tell whether the run was short of a
    preference or short of a measurement.
    """
    value: Any
    whose: str
    why: str
    superseded_by: str


@dataclass(frozen=True)
class Slot:
    """One creative value, addressed once.

    `scopes` lets one slot decide several values of the same kind - the
    behaviour words a bed level is decided per - each landing on its own
    rung.  An empty `scopes` is a single unscoped value.

    `answer_units` and `value_units` differ exactly when the model is
    asked in units a person can judge and the engine computes what the
    renderer reads; `solver` is then REQUIRED and is the only formula.
    """
    key: str
    question: str
    answer_units: str
    value_units: str
    deciding_step: str
    readers: tuple[str, ...]
    measurements: tuple[Measurement, ...] = ()
    scopes: tuple[str, ...] = ()
    preference_paths: tuple[str, ...] = ()
    direction_key: str | None = None
    # Why the DIRECTED rung is unavailable, when it is.  Stated rather
    # than left implicit: a rung silently missing reads as a rung that
    # was tried.
    no_direction_reason: str = ""
    solver: str | None = None
    fallback: Fallback | None = None
    # What a consumer must do when the answer is UNDETERMINED.  Prose,
    # read by a human; nothing branches on it.
    undetermined_means: str = ""

    def scope_names(self) -> tuple[str, ...]:
        """The scopes to decide, or a single unscoped `("",)`."""
        return self.scopes or ("",)


def _solve_bed_gain(answer, measurements):
    """Clip gain from a separation the mix engineer named.

    ``gain = (speech_lufs - separation) - bed_integrated_lufs``

    Arithmetic over one judgement and two measurements, and the only
    formula for it.  Returns ``(gain, note)`` or ``(None, why not)``:
    a term that was never measured produces NO gain rather than a gain
    computed from a stand-in.
    """
    speech = measurements.get("speech_lufs")
    bed = measurements.get("bed_integrated_lufs")
    if not isinstance(answer, (int, float)):
        return None, f"the answer {answer!r} is not a number of dB"
    if not isinstance(speech, (int, float)):
        return None, ("the speech under these windows was not measured, so "
                      "the gain that would deliver the separation cannot "
                      "be computed")
    if not isinstance(bed, (int, float)):
        return None, ("the bed's own loudness was not measured, so the gain "
                      "that would deliver the separation cannot be computed")
    gain = round((float(speech) - float(answer)) - float(bed), 2)
    return gain, (f"{speech} LUFS speech - {answer} dB separation - "
                  f"{bed} LUFS bed")


# ── The registry ─────────────────────────────────────────────────────
#
# One row per creative value the pipeline decides.  A row is a contract:
# a question in one place rather than N copies in N handoffs, the
# measurements that make the judgement possible with the function that
# takes each one, the step that decides it, and who reads the answer.
#
# Adding a row is not free, and it is not supposed to be.  It is the only
# exit a creative constant has from the floors guard, and it costs a
# question, a measured producer, a model-reaching step, a reader, a trace
# and - if the value survives absence at all - a fallback with a named
# owner.

SLOTS: dict[str, Slot] = {
    "mix.speech_above_bed_db": Slot(
        key="mix.speech_above_bed_db",
        question=(
            "How far above the music should the voice sit in this piece, "
            "in dB, for each of the behaviours below? You are looking at "
            "what this bed and this speech actually measure, window by "
            "window, and at what the separation would be if the mix kept "
            "the level it has always used. Say what this video needs, not "
            "what a mix usually needs: a bed mastered hot needs a "
            "different number from a quiet one to sit in the same place "
            "under the same voice. There is no bound on your answer and "
            "no scale to pick from - name the separation you want and why "
            "you want it."),
        answer_units=(
            "dB the speech sits ABOVE the bed - speech integrated LUFS "
            "minus the bed's integrated LUFS after the clip gain. A "
            "bigger number is a quieter bed under a voice"),
        value_units=(
            "the CLIP GAIN in dB applied to the bed - how far the bed is "
            "pushed down from the level its own file carries"),
        deciding_step="audio_mix",
        readers=(
            "library/steps/step_5_02_audio_mix/post_bridge.py",
            "library/tools/otio_mix.py::music_curve",
            "library/tools/render_qa.py::measure_speech_above_bed",
        ),
        measurements=(
            Measurement(
                name="bed_integrated_lufs",
                produced_by="library/tools/music_measurement.py::bed_reading",
                what=("the chosen bed's own integrated loudness, folded on "
                      "by step 2.04's post-bridge")),
            Measurement(
                name="speech_lufs",
                produced_by=("library/tools/speech_loudness.py::"
                             "measure_speech_blocks"),
                what=("one ffmpeg loudnorm pass per block over the ranges "
                      "a_roll_assignments names")),
            Measurement(
                name="separation_delivered_db",
                produced_by=("library/tools/speech_loudness.py::"
                             "separation_delivered_db"),
                what="what the separation would be at a given clip gain"),
        ),
        # The words that carry a DECIDED level.  `silent` is not one: -96
        # dB is the absence of music, which is the `CUT_TYPES` /
        # `NEUTRAL_CDL` category and not a level anybody chose.  The two
        # fades are not either: a fade is a MOVE between two decided
        # levels, so it resolves to its endpoint rather than declaring a
        # third plateau of its own.
        scopes=("prominent", "background"),
        preference_paths=(
            "pipeline.creative_preferences.mix.speech_above_bed_db",),
        direction_key=None,
        no_direction_reason=(
            "no key step 2.01 is asked for states how loud music sits under "
            "a voice; `creative_direction.DIRECTION_KEYS` is the "
            "enumeration, and reading a key that cannot exist is the defect "
            "that module raises on"),
        solver="library.tools.decided_value::_solve_bed_gain",
        fallback=Fallback(
            value={"prominent": -6, "background": -18},
            whose="the mix the Lucie videos shipped at",
            why=("these are the clip gains `music_behavior.MUSIC_BEHAVIORS` "
                 "carried from the initial commit until 2026-09-16. They "
                 "are a mix that was preferred on one series, not a level "
                 "measured for this one, and a window that takes them "
                 "records `fallback` rather than a decision"),
            superseded_by=(
                "a measured bed and measured speech, which every run that "
                "reaches step 2.04 and step 3.01 has - or a separation the "
                "project states under pipeline.creative_preferences or the "
                "user records in their taste profile")),
        undetermined_means=(
            "the window carries no gain and the mix spec says why; nothing "
            "downstream may substitute one"),
    ),
}


def assert_registry_is_well_formed() -> None:
    """Every row is a contract, and a row that cannot be true raises.

    Checked at import, so a malformed slot fails the first thing that
    imports this module rather than the run that reached its step.
    """
    from library.tools import undetermined

    for key, slot in SLOTS.items():
        where = f"slot {key!r}"
        if key != slot.key:
            raise MalformedSlot(f"{where} is filed under a different key "
                                f"({slot.key!r})")
        if not slot.question.strip():
            raise MalformedSlot(f"{where} asks no question")
        if not slot.readers:
            raise MalformedSlot(
                f"{where} has no reader. A declared value with no reader is "
                f"the defect library/tools/output_contract.py refuses")
        if slot.deciding_step not in undetermined.DECLARING_STEPS:
            raise MalformedSlot(
                f"{where} is decided by {slot.deciding_step!r}, which does "
                f"not reach a model. A deterministic step cannot decide a "
                f"creative value - that is the constant this module "
                f"replaces, one level up")
        if slot.answer_units != slot.value_units and not slot.solver:
            raise MalformedSlot(
                f"{where} is answered in different units from the ones it "
                f"delivers and registers no solver, so the conversion would "
                f"be invented at the call site")
        if slot.solver:
            _resolve_solver(slot)
        if slot.direction_key is None and not slot.no_direction_reason:
            raise MalformedSlot(
                f"{where} names no direction key and does not say why. A "
                f"rung silently missing reads as a rung that was tried")
        if slot.fallback is not None:
            fb = slot.fallback
            if not fb.whose.strip():
                raise MalformedSlot(
                    f"{where} registers a fallback nobody owns. A fallback "
                    f"whose owner is the engine is a constant with better "
                    f"paperwork")
            if not fb.superseded_by.strip():
                raise MalformedSlot(
                    f"{where} registers a fallback that says nothing about "
                    f"what would have answered instead")
        elif not slot.undetermined_means.strip():
            raise MalformedSlot(
                f"{where} has no fallback and does not say what its "
                f"consumer must do when nothing decides it")


def _resolve_solver(slot: Slot):
    """The one formula a slot registers, imported by its dotted path."""
    module_path, _, func_name = (slot.solver or "").partition("::")
    if not module_path or not func_name:
        raise MalformedSlot(
            f"slot {slot.key!r} names a solver {slot.solver!r} that is not "
            f"`module::function`")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, func_name)
    except AttributeError as exc:
        raise MalformedSlot(
            f"slot {slot.key!r} names a solver {slot.solver!r} that does "
            f"not exist") from exc


def slot(key: str) -> Slot:
    """The slot row for `key`, raising on one that is not registered."""
    try:
        return SLOTS[key]
    except KeyError as exc:
        raise UnknownSlot(
            f"no value slot {key!r}; the registry is {sorted(SLOTS)}") from exc


def slots_for(node_id: str) -> tuple[Slot, ...]:
    """Every slot the step `node_id` decides, in registry order."""
    return tuple(s for s in SLOTS.values() if s.deciding_step == node_id)


def decides(node_id: str) -> bool:
    """True when this step decides at least one registered value."""
    return bool(slots_for(node_id))


# ── The route to the model ───────────────────────────────────────────
#
# The fourth schema appender, beside `undetermined`,
# `direction_contradiction` and `briefing_interview`.  Deliberately their
# twin rather than a second invention: the same words reach every
# deciding step, and N copies in N `handoff.md` files is N things to keep
# equal.

def schema_entry() -> dict:
    """The `value_decisions` field, for the RENDERED schema."""
    return {
        "name": FIELD,
        "type": "list",
        "required": True,
        # Empty is a legitimate answer, not a missing one: the prompt
        # tells the model to leave a value out where the material does
        # not let it decide one (a speechless edit has no voice to sit
        # above the bed), and the ladder records the omission as a
        # fallback or undetermined - never as agreement with a default.
        # Refusing [] here failed correct output on exactly the runs the
        # sentence above invites (AGENTS.md 10.4).
        "may_be_empty": True,
        "description": (
            "One entry per value you were asked to decide above: "
            "{slot, scope, value, why}. `scope` is the name beside the "
            "question - use \"\" when the question named none. `value` is a "
            "number or word in the units the question states; there is no "
            "bound on it and no scale to choose from. `why` is REQUIRED and "
            "is your own words about the measurements you read - an entry "
            "without one is DROPPED, because a value nobody can review is "
            "what the constant this replaces was. Leave a value out if this "
            "material genuinely does not let you decide it: an omission is "
            "recorded as an omission, never read as agreement with a "
            "default."),
    }


def prompt_block(node_id: str) -> str:
    """The questions this step's slots ask, as DATA beside the context."""
    rows = slots_for(node_id)
    if not rows:
        return ""
    lines = ["\n\n## Values you are deciding\n"]
    for row in rows:
        lines.append(f"\n### `{row.key}`\n")
        lines.append(f"{row.question}\n")
        lines.append(f"\n- Answer in: {row.answer_units}.\n")
        if row.answer_units != row.value_units:
            lines.append(
                f"- What the engine does with it: it computes "
                f"{row.value_units} from your answer and the measurements "
                f"below. You are not asked for that number and should not "
                f"try to name it.\n")
        for scope in row.scopes:
            lines.append(f"- Decide it for `scope: {scope}`.\n")
        if row.measurements:
            lines.append("- The measurements in front of you: "
                         + "; ".join(f"`{m.name}` ({m.what})"
                                     for m in row.measurements) + ".\n")
        lines.append(
            "- There is no bound on your answer and nothing is substituted "
            "for one you leave out. If the material does not let you decide "
            "it, say so by leaving it out rather than naming a number you "
            "cannot defend.\n")
    return "".join(lines)


def take(node_id: str, answer: Any) -> tuple[Any, dict[tuple[str, str], dict]]:
    """Split `value_decisions` out of the answer and index it by slot.

    Returns `(answer without the field, {(slot, scope): entry})`.  The
    field never becomes one of the step's outputs: `validate_step_output`
    would then demand it, and the answer that leaves here is the one the
    step would have produced without the question.

    An entry naming a slot this step does not decide, or carrying no
    `why`, is DROPPED here rather than travelling - 5.01's rule for a
    grade nobody can review.
    """
    if not isinstance(answer, dict) or FIELD not in answer:
        return answer, {}
    remaining = {k: v for k, v in answer.items() if k != FIELD}
    entries = answer.get(FIELD)
    mine = {s.key for s in slots_for(node_id)}
    taken: dict[tuple[str, str], dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("slot")
        if key not in mine:
            continue
        if not str(entry.get("why") or "").strip():
            continue
        scope = str(entry.get("scope") or "")
        taken[(key, scope)] = {"value": entry.get("value"),
                               "why": str(entry["why"]).strip()}
    return remaining, taken


def answers_from(data: dict, key: str, scope: str = "") -> dict | None:
    """The model's answer for one slot, out of a post-bridge's merge data.

    The runner puts what `take` split out under :data:`MERGE_KEY`, as a
    list of entries, because a subprocess cannot read this module's own
    collector.
    """
    for entry in (data or {}).get(MERGE_KEY) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("slot") == key and str(entry.get("scope") or "") == scope:
            return entry
    return None


def as_merge_payload(taken: dict[tuple[str, str], dict]) -> list[dict]:
    """What `take` produced, in the shape a post-bridge reads."""
    return [{"slot": key, "scope": scope, **entry}
            for (key, scope), entry in sorted(taken.items())]


# ── The ladder ───────────────────────────────────────────────────────

@dataclass
class Decision:
    """Why one creative value is what it is.

    `value` is in the slot's `value_units` - what the renderer reads.
    `answer` is what the model actually said, in `answer_units`, kept
    beside the value so a reader can see the judgement AND the arithmetic
    rather than only their product.
    """
    key: str
    scope: str
    basis: str
    value: Any = None
    answer: Any = None
    why: str = ""
    source: str = ""
    measurements: dict[str, Any] = field(default_factory=dict)
    rungs_skipped: list[dict] = field(default_factory=list)
    step: str = ""
    attempt: int = 1

    def as_record(self) -> dict:
        return {
            "slot": self.key,
            "scope": self.scope,
            "basis": self.basis,
            "value": self.value,
            "answer": self.answer,
            "why": self.why,
            "source": self.source,
            "measurements": dict(self.measurements),
            "rungs_skipped": list(self.rungs_skipped),
            "step": self.step,
            "attempt": self.attempt,
        }

    @property
    def decided(self) -> bool:
        """True when a value exists at all - a fallback is still a value."""
        return self.basis != UNDETERMINED


def stated_preference(key: str, project_folder: str, scope: str = ""):
    """What the project or user's taste profile STATES, or `(None, "")`.

    The project has the more specific declaration. If it has none, read
    this user's explicit profile value, which is shared across projects.
    Both sources are declarations: an absent value is ABSENT, never filled
    in with a default.

    Returns `(value, path)` so the record can name where it came from.
    """
    row = slot(key)
    if not row.preference_paths:
        return None, ""
    if project_folder:
        from library.tools.brand_registry import project_pipeline_block

        block = project_pipeline_block(project_folder).get(
            "creative_preferences") or {}
        if isinstance(block, dict):
            # `mix.speech_above_bed_db` may be written nested or flat; both
            # are the same declaration and neither is a second address space.
            declared = block.get(key)
            if declared is None:
                cursor: Any = block
                for part in key.split("."):
                    if not isinstance(cursor, dict):
                        cursor = None
                        break
                    cursor = cursor.get(part)
                declared = cursor
            if declared is not None:
                path = row.preference_paths[0]
                if scope and isinstance(declared, dict):
                    if scope in declared:
                        return declared[scope], f"{path}.{scope}"
                elif not isinstance(declared, dict):
                    return declared, path

    from library.tools import taste_profile
    return taste_profile.stated_preference(key, scope)


def decide(key: str, *, scope: str = "", project_folder: str = "",
           creative_direction: dict | None = None,
           model_answer: dict | None = None,
           measurements: dict[str, Any] | None = None,
           step: str = "", attempt: int = 1) -> Decision:
    """The captain's precedence, in the only place it exists.

    Every rung that could not answer is recorded in `rungs_skipped` with
    its reason, so a reader can tell a run that was short of a preference
    from one that was short of a measurement.
    """
    row = slot(key)
    measurements = dict(measurements or {})
    skipped: list[dict] = []
    common = {"key": key, "scope": scope, "measurements": measurements,
              "rungs_skipped": skipped, "step": step or row.deciding_step,
              "attempt": attempt}

    # 1. STATED - the project or this user said so.
    value, path = stated_preference(key, project_folder, scope)
    if value is not None:
        solved, note = _deliver(row, value, measurements)
        if solved is not None:
            return Decision(basis=STATED, value=solved, answer=value,
                            why=note, source=path, **common)
        skipped.append({"basis": STATED, "why": note})
    else:
        skipped.append({
            "basis": STATED,
            "why": (f"the project states no preference at "
                    f"{row.preference_paths[0]}, and the user's profile "
                    f"states no preference for {key}"
                    if row.preference_paths else
                    "this value has no place a project may state it")})

    # 2. DIRECTED - a value the creative direction really declared.
    if row.direction_key:
        from library.tools.creative_direction import direction_value
        declared = direction_value(creative_direction or {}, row.direction_key)
        if declared is not None:
            solved, note = _deliver(row, declared, measurements)
            if solved is not None:
                return Decision(basis=DIRECTED, value=solved, answer=declared,
                                why=note,
                                source=f"creative_direction.{row.direction_key}",
                                **common)
            skipped.append({"basis": DIRECTED, "why": note})
        else:
            skipped.append({
                "basis": DIRECTED,
                "why": (f"the creative direction declares no "
                        f"{row.direction_key}")})
    else:
        skipped.append({"basis": DIRECTED, "why": row.no_direction_reason})

    # 3. REASONED - the model answered, over measurements it was shown.
    if model_answer and str(model_answer.get("why") or "").strip():
        solved, note = _deliver(row, model_answer.get("value"), measurements)
        if solved is not None:
            return Decision(basis=REASONED, value=solved,
                            answer=model_answer.get("value"),
                            why=str(model_answer["why"]).strip(),
                            source=f"{row.deciding_step} answered this run"
                                   + (f" ({note})" if note else ""),
                            **common)
        skipped.append({"basis": REASONED, "why": note})
    else:
        skipped.append({
            "basis": REASONED,
            "why": ("the model named no value for this scope"
                    if model_answer is None else
                    "the model's entry carried no `why` and was dropped")})

    # 4. FALLBACK - registered, owned, and visible as one.
    if row.fallback is not None:
        held = row.fallback.value
        if isinstance(held, dict):
            if scope not in held:
                skipped.append({
                    "basis": FALLBACK,
                    "why": (f"the registered fallback states nothing for "
                            f"scope {scope!r}")})
                return Decision(basis=UNDETERMINED, why=row.undetermined_means,
                                **common)
            held = held[scope]
        return Decision(
            basis=FALLBACK, value=held, answer=None,
            why=row.fallback.why,
            source=(f"a registered fallback: {row.fallback.whose}. "
                    f"Superseded by {row.fallback.superseded_by}"),
            **common)

    # 5. UNDETERMINED - no value, and the consumer says so.
    return Decision(basis=UNDETERMINED, why=row.undetermined_means, **common)


def _deliver(row: Slot, answer: Any, measurements: dict[str, Any]):
    """The value the renderer reads, from what was answered.

    Identity when the slot is answered in the units it delivers; the
    slot's REGISTERED solver otherwise.  Returns `(value, note)` or
    `(None, why not)` - a term that was never measured produces no value
    rather than one computed from a stand-in.
    """
    if not row.solver:
        return answer, ""
    return _resolve_solver(row)(answer, measurements)


def assert_decided(key: str, value: Any, records: Sequence[dict],
                   scope: str = "") -> None:
    """A slot value with no decision record behind it is refused.

    Fail-closed, the way `reel_rebuild_need` is: a value that arrived
    from nowhere is exactly the state this module exists to make
    impossible, and letting it through because the record is merely
    missing would rebuild it.
    """
    for record in records or []:
        if (record.get("slot") == key
                and str(record.get("scope") or "") == scope):
            return
    raise UndecidedValue(
        f"{key!r}" + (f" scope {scope!r}" if scope else "")
        + f" carries the value {value!r} with no decision record. Every "
        f"creative value is decided through library/tools/decided_value.py "
        f"and says what decided it")


# ── The trace ────────────────────────────────────────────────────────

def records_from(step_output: Any) -> list[dict]:
    """The decision records a step wrote, out of its own output."""
    if not isinstance(step_output, dict):
        return []
    rows = step_output.get(STATE_KEY)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) \
        else []


def merge_records(existing: Any, fresh: Sequence[dict]) -> list[dict]:
    """Fresh rows replace their own STEP's rows; the rest are kept.

    MERGED, never replaced - the sibling keys' reasoning exactly.  A
    `--rerun audio_mix` answers one step, and replacing the key would
    erase every other step's decisions: the narrowest possible run
    destroying the record.  A carried row is marked so it is never read
    as fresh.
    """
    fresh_rows = [dict(r) for r in fresh or [] if isinstance(r, dict)]
    answered = {r.get("step") for r in fresh_rows}
    kept = []
    for row in existing or []:
        if not isinstance(row, dict) or row.get("step") in answered:
            continue
        carried = dict(row)
        carried["from_a_previous_run"] = True
        kept.append(carried)
    return kept + fresh_rows


def summary_lines(records: Sequence[dict]) -> list[str]:
    """One line per decision, saying which rung answered it."""
    lines = []
    for row in records or []:
        if not isinstance(row, dict):
            continue
        scope = row.get("scope")
        where = f"{row.get('slot')}" + (f"[{scope}]" if scope else "")
        basis = row.get("basis")
        line = f"{where}: {basis} = {row.get('value')!r}"
        if basis == FALLBACK:
            line += f" - {row.get('source', '')}"
        elif basis == UNDETERMINED:
            line = f"{where}: UNDETERMINED - {row.get('why', '')}"
        if row.get("from_a_previous_run"):
            line += " (from a previous run)"
        lines.append(line)
    return lines


assert_registry_is_well_formed()


# ── The runner's collector ───────────────────────────────────────────
#
# `take` runs inside `present_llm_step` and the answer is needed by a
# post-bridge, which is a SUBPROCESS and cannot read this module's state.
# So the split-out answer is stashed here, in the runner's own process,
# and `run_hybrid_step` puts it into the post-bridge's merge data under
# `MERGE_KEY` - the same way `second_pass.PASS_KEY` travels.
#
# One stash per step, replaced per attempt: a step asked again after a QA
# failure answers again, and the answer the post-bridge runs on must be
# the one the model just gave.

_STASH: dict[str, list[dict]] = {}


def stash(node_id: str, taken: dict[tuple[str, str], dict]) -> None:
    """Hold this step's split-out answer for its post-bridge."""
    _STASH[node_id] = as_merge_payload(taken)


def stashed(node_id: str) -> list[dict]:
    """What the model answered for this step's slots, in merge shape."""
    return list(_STASH.get(node_id) or [])


def reset() -> None:
    """Forget every stashed answer.  Called once per run by the runner."""
    _STASH.clear()
