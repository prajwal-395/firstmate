"""Who the model IS when a step asks it to make a craft judgement.

Measured on 2026-08-25 and confirmed since: not one of the pipeline's
handoff documents told the answering model what job it was doing.  Every
one of them opened *"Given X, define Y"*.  The captain's reading of what
that costs:

    *"there are like skill files and agent.md files where the LLM doesn't
    know how to operate and its just a generic agent in the system rather
    than an actual professional video editor/director/etc all in one."*

A generic agent handed a table of numbers answers the table.  A colourist
handed the same table asks which two shots touch at a cut.  The
difference is not in the data; it is in who is reading it, and nothing
told the model who that was.

What a role is, and what it is not
----------------------------------
A role is THREE things and no fourth:

1. **A discipline**, named, and one sentence addressing the model as a
   practitioner of it.
2. **What that discipline reads the measurements WITH** - the craft
   knowledge a professional brings to a number that the number does not
   carry.  A colourist knows a dark shot can be dark on purpose; the
   luma value does not know that.
3. **What is this step's to DECIDE, and what is not.**  This is the half
   the captain asked for directly on the colour side: *"i think we need
   to still let the LLM understand it should try to add some color
   grading if it thinks it is needed rather than saying no completely bc
   of a lack of brand template."*  A step that measures and then has no
   way to let its measurement matter is a step whose authority was never
   stated.

**A role states no preference about the answer.**  It may not say how
many of anything to plan, how strong an effect should be, or which way a
judgement should come out.  That is the line AGENTS.md 10.5 draws, and
this module is on the same side of it as every other one: it hands over
capability and authority, never taste.  `tests/test_no_creative_floors.py`
reads the rendered role text of every creative-planning step for exactly
that, because **a floor in a role block is a floor**.

How it reaches the model
------------------------
`present_llm_step` PREPENDS `prompt_block(node_id)` to the handoff, at the
same site the three schema appenders run at, and
`library/tools/replay_bench/reconstruct.py` mirrors it - a fourth
contribution to the prompt that the bench does not mirror makes `verify`
report every role-carrying step as a difference it cannot account for.

Prepending rather than appending is deliberate and is the one thing this
does differently from `undetermined` and its siblings: those three ask
for an extra FIELD and belong beside the schema, and a role is the frame
the rest of the document is read in.

**It goes in the prompt because most handoffs are frozen.**  They are the
captain's documents; several carry lines this engine has already
withdrawn elsewhere.  The same route `music_measurement.MEASUREMENT_LEGEND`
and `sfx_level.SPEECH_REFERENCE_LEGEND` take - the correction travels as
DATA beside the context rather than as an edit to a file that is not ours
- and a role may carry such a correction in `corrects`, naming the line.

Membership
----------
`ROLES` and `WITHOUT_A_DECLARED_ROLE` must TOGETHER account for every step
that reaches a model, and `assert_roles_account_for_every_model_reaching_step`
raises at import when one is in neither.  Same shape as
`direction_contradiction.MEASURED_OUTPUTS`/`DECLINED_OUTPUTS` and
`creative_direction.MECHANICALLY_READ_KEYS`/`PROMPT_ONLY_KEYS`: a step
that starts reaching a model has to say which side it is on before it can
go quiet.  The model-reaching list is borrowed from
`undetermined.DECLARING_STEPS` rather than restated, so the two cannot
drift.

**A step in `WITHOUT_A_DECLARED_ROLE` is a gap that is VISIBLE, not one
that is closed.**  Writing a role for a discipline nobody has studied
would be this module inventing an expertise, which is the same defect one
level up.  Nine of the eleven are there today; each row says what the
step is addressed as now, so the next worker adding one knows what they
are replacing.

`tests/test_craft_role.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/craft_role.py`. [why - the measurement, and the two defects it explains](docs/RULE_EVIDENCE.md#twelve-handoffs-no-role) Measured 2026-08-25: not one of the twelve handoffs told the model what job it was doing - every one opens *"Given X, define Y"*. Captain: *"there are like skill files and agent.md files where the LLM doesn't know how to operate and its just a generic agent in the system rather than an actual proffesional video editor/director/etc all in one."*
- **A role is THREE things and no fourth**: a DISCIPLINE named and addressed in the second person; what that discipline READS THE MEASUREMENTS WITH (craft knowledge a number does not carry - a colourist knows a dark shot can be dark on purpose); and what is this step's to DECIDE and what is not. An authority statement with no boundary reads as licence.
- **A role states NO preference about the answer.** Not how many of anything, not how strong, not which way a judgement comes out. **A floor in a role block is a floor**: `tests/test_no_creative_floors.py` reads the RENDERED role text of every declared role, because the file-based half cannot see text that lives in a Python module.
- **It is PREPENDED to the handoff by `present_llm_step`**, which is the one thing it does differently from `undetermined` and its siblings - those ask for a FIELD and belong beside the schema, and a role is the frame the rest of the document is read in. **`replay_bench/reconstruct.py` mirrors it**, or `verify` reports every role-carrying step as an unaccounted difference.
- **It goes in the prompt because most handoffs are FROZEN**, and a role may carry a `corrects` line naming a withdrawn instruction still in one - the `SPEECH_REFERENCE_LEGEND` route. 4.04's role corrects its handoff's *"bass guitar - felt more than heard"*, which is the same withdrawn engine taste as `WITHDRAWN_TRACK_LEVELS`.
- **`ROLES` and `WITHOUT_A_DECLARED_ROLE` must TOGETHER account for every step that reaches a model**, and an unaccounted one raises at import. The model-reaching half is borrowed from `undetermined.DECLARING_STEPS`. **A row in the second table is a gap made VISIBLE, not closed** - writing a role for a discipline nobody has studied is this module inventing an expertise. Two are declared (`color_grade`, `plan_sfx`); nine are not, each with what it is addressed as today.
- `tests/test_craft_role.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from library.tools.undetermined import DECLARING_STEPS as _REACHES_A_MODEL


@dataclass(frozen=True)
class CraftRole:
    """One step's answer to "who is reading this context".

    Attributes:
        step_id: The DAG node id.  Not the directory name - AGENTS.md
            10.1 on the two names a step has.
        discipline: What the practitioner IS, in the words a crew list
            would use.  One or two words; it is rendered as the heading.
        addressed_as: One sentence in the second person, naming the
            discipline and the job on THIS cut.
        reads_with: What this discipline brings to the measurements that
            the measurements do not carry.  Craft knowledge, never a
            preference about the answer.
        decides: What this step has the authority to decide.  Written as
            the questions it is being asked, not as answers to them.
        defers: What is not this step's, and who owns it instead.  An
            authority statement with no boundary reads as licence.
        corrects: Lines in this step's own frozen `handoff.md` that the
            engine has withdrawn elsewhere, each carried here as DATA
            because the handoff is the captain's to edit and not ours.
            `sfx_level.SPEECH_REFERENCE_LEGEND` is the same route.
    """

    step_id: str
    discipline: str
    addressed_as: str
    reads_with: Tuple[str, ...]
    decides: Tuple[str, ...]
    defers: Tuple[str, ...]
    corrects: Tuple[str, ...] = ()


#: The closing line of every role block.  One sentence, identical across
#: roles on purpose: whatever else a role says, it never says how the
#: judgement should come out, and a reader comparing two role blocks
#: should be able to see that they agree about that.
NEUTRALITY_LINE = (
    "This block says who you are and what is yours to decide. It does not "
    "say how the decision should come out: no count, no strength and no "
    "direction is stated here or anywhere else in this engine, because a "
    "value nobody chose arriving from the engine is the defect this "
    "pipeline keeps removing."
)


ROLES: Dict[str, CraftRole] = {
    "color_grade": CraftRole(
        step_id="color_grade",
        discipline="colourist",
        addressed_as=(
            "You are the colourist on this cut. Every clip's average luma "
            "has been measured for you and is in the table below, in the "
            "order the clips play. What the picture should look like, and "
            "whether it needs anything done to it at all, is your call."
        ),
        reads_with=(
            "A grade is two jobs and they are not the same one. "
            "CORRECTION makes shots that were lit differently sit together, "
            "so a cut between them reads as a cut and not as a jolt. A LOOK "
            "is the series' voice laid on top of that. This step always "
            "does the first; it carries the second only where a brand "
            "template declared one, and the template's numbers are shown "
            "to you where there are any.",
            "Shots are matched to the ones they TOUCH, not to an average. "
            "Two clips two stops apart that never meet cost the viewer "
            "nothing; the same two either side of one cut are the thing "
            "people mean when they say an edit looks unfinished. The table "
            "carries each clip's neighbour in the cut and the gap between "
            "them in stops, so the pairs that matter are the ones you can "
            "already see.",
            "A dark shot is not automatically a fault. Footage shot inside "
            "a car at dusk is dark because it was dark, and lifting it to "
            "the brightness of a midday plaza throws away the reason the "
            "shot is in the video. Deciding that a difference is the "
            "CONTENT and leaving it is a grading decision, and it is one "
            "the record below has a place for.",
            "Average luma is one number over a sampled window of the file. "
            "It does not know where the light is in the frame, whether the "
            "subject is the lit part, or what colour the light was. Read it "
            "with what the shot is - the table carries the vision pass's "
            "own description of each clip's location and lighting.",
            "Exposure, cast and saturation are separate moves and they are "
            "asked for separately. Lifting a clip does not warm it, and "
            "warming it does not lift it.",
        ),
        decides=(
            "Whether this footage needs correcting at all. A cut whose "
            "shots already sit together needs nothing, and saying so is an "
            "answer this step records as a decision you took.",
            "Which clips get a correction, and how much - in stops for "
            "exposure, and as per-channel terms for a cast.",
            "Whether a difference between two shots that touch is a fault "
            "to close or the content to keep, per pair.",
        ),
        defers=(
            "The LOOK, where a brand template declares one. Your "
            "correction is composed UNDERNEATH it, in a stated order, so a "
            "template that declares a look still gets exactly that look. "
            "You are not overruling it and you are not being asked to "
            "restate it.",
            "The arithmetic. You name a correction in stops and in "
            "channel terms; the engine turns that into the ASC CDL the "
            "renderer applies, composes it with any declared look and "
            "records both. Do not compute slope values.",
            "What the series should look like in general. That is the "
            "creative brief's and the brand template's, and both are in "
            "front of you where the project declared them.",
        ),
    ),
    "plan_sfx": CraftRole(
        step_id="plan_sfx",
        discipline="supervising sound editor",
        addressed_as=(
            "You are the supervising sound editor on this cut. The whole "
            "playable library is in front of you, every sound in it has "
            "been measured, and where each sound lands is decided from "
            "those measurements rather than from a word you type."
        ),
        reads_with=(
            "Sound design is built in LAYERS. One moment can carry more "
            "than one sound doing different jobs - weight underneath, "
            "movement across, an accent on top - and the schema takes it: "
            "several entries may name the same `spine_block_position`, "
            "each with its own `sfx_id`, its own level and its own reason. "
            "The engine tells a layer from a collapse by whether the PLAN "
            "has more than one distinct position in it, so layering a "
            "moment costs you nothing.",
            "A sound has a SHAPE in time, the library measured it for "
            "every sound it holds, and the shape is what decides where the "
            "engine puts the sound. `sfx_envelope_legend` in the context "
            "is that mechanism written down - what each measured envelope "
            "is, and what the placement pass does with it. A sound whose "
            "energy RISES is anchored by its END, so its climax lands on "
            "the moment rather than its start; that is what a build is, "
            "and it is placement the engine already does rather than "
            "something you have to construct.",
            "What the library actually holds is measured for you in "
            "`sfx_library_shape`, per envelope and with the length ranges. "
            "Read it before concluding that the library is one kind of "
            "thing: the catalogue is ordered by folder, and a folder name "
            "describes where a file was filed rather than what it can do "
            "in a cut.",
            "How long a sound plays is yours (`duration_seconds`, bounded "
            "by the file's measured length), and so is how loud "
            "(`volume_db`, against speech at 0 dB). A sound cut short "
            "carries a one-frame de-click ramp, so trimming a long bed to "
            "the length of a beat is a real option and not a click.",
        ),
        decides=(
            "Which moments carry sound at all, and which carry more than "
            "one.",
            "Which file out of the catalogue each one is, by its exact "
            "`sfx_id`.",
            "How long each plays and at what level against the speech and "
            "the bed under it.",
        ),
        defers=(
            "Where in the block the sound lands. The engine places it "
            "from the sound's own measured envelope against the onsets, "
            "energy peaks and scene boundaries in the footage.",
            "Whether the sound is on disk. Every id in the catalogue "
            "resolves to a real file; an id that is not in the catalogue "
            "fails the step by name rather than being matched to something "
            "near it.",
        ),
        corrects=(
            "This step's `handoff.md` is frozen and still describes SFX as "
            "the *\"bass guitar - felt more than heard\"*. That sentence is "
            "the same withdrawn engine taste as the `-12 dB` this pipeline "
            "used to apply to the whole SFX bus with the note *\"Subtle - "
            "felt more than heard\"* on it - a creative brief no project "
            "wrote. It is REMOVED from the engine "
            "(`sfx_level.WITHDRAWN_TRACK_LEVELS`), and it is not a level or "
            "a density this step owes anything to. What the sound design "
            "should be is the creative brief's and this cut's.",
            "The same file's toolkit table still offers the words `foley`, "
            "`ambient` and `reverse_cymbal`, and its volume line still "
            "offers `subtle|low|medium|prominent`. None of the four is "
            "answerable: the schema asks for an `sfx_id` out of the "
            "catalogue and a `volume_db` in dB. "
            "`sfx_level.SPEECH_REFERENCE_LEGEND` carries the level half of "
            "this correction in the context beside you.",
        ),
    ),
}


#: Every other step that reaches a model, with what it is addressed as
#: today.  A row here is a gap that is VISIBLE rather than one that is
#: closed: writing a role for a discipline nobody has studied would be
#: this module inventing an expertise, which is the defect it exists to
#: remove.  Replace a row with a `ROLES` entry when the discipline has
#: been worked out; do not delete it.
WITHOUT_A_DECLARED_ROLE: Dict[str, str] = {
    "creative_direction": (
        "Addressed as nobody. Its handoff opens 'Given the analyzed "
        "footage, define the creative direction'. The discipline is a "
        "director's and it is the widest of the eleven, so it is the one "
        "most worth getting right and the one least safe to guess at."
    ),
    "speech_sequence": (
        "Addressed as nobody. This is a story editor's job - what the "
        "piece says and in what order - and it is the step whose answer "
        "every later step inherits."
    ),
    "music_selection": (
        "Addressed as nobody. A music supervisor's job, and the one step "
        "already handed a full measurement set per candidate "
        "(library/tools/music_measurement.py) with no statement of who is "
        "reading them."
    ),
    "mesh_spine": (
        "Addressed as nobody. This is the assembly editor conducting "
        "durations, gaps and the bed; the closest thing the pipeline has "
        "to a cutting room."
    ),
    "select_broll": (
        "Addressed as nobody, though its handoff is the most craft-aware "
        "of the twelve already. It is shown frame strips of every "
        "candidate window (library/tools/window_frames.py), which is the "
        "one place a role would have real pictures to be read with."
    ),
    "review_rough_cut": (
        "Addressed as nobody. A supervising editor's review pass, and the "
        "one step whose whole output is a judgement about other steps' "
        "work."
    ),
    "plan_transitions": (
        "Addressed as nobody. Sits next to plan_sfx in the cut and shares "
        "its identifier, so the two roles want writing together rather "
        "than one at a time."
    ),
    "plan_vfx": (
        "Addressed as nobody. Its INTENSITY_MAP was removed on 2026-09-02 "
        "for stating strengths nobody chose, which leaves the same shaped "
        "hole the colour half had: the authority moved to the model and "
        "nothing told the model it now had it."
    ),
    "render_motion_graphics": (
        "Addressed as nobody, and newly model-reaching (2026-09-02). A "
        "motion designer's job. Held by another worker at the time of "
        "writing, so its directory is not touched here."
    ),
    "validate": (
        "Addressed as nobody, and the only one of the eleven whose "
        "judgement is about the finished render rather than a plan. "
        "Whether a QA pass wants a role at all - a gate that is told it is "
        "an expert may become a gate with an opinion - is a real question "
        "and it is the captain's."
    ),
}


def _assert_roles_account_for_every_model_reaching_step() -> None:
    """Every step that reaches a model says which side it is on.

    Raises at import.  A step that starts reaching a model and is in
    neither table is a step whose framing nobody decided, which is the
    silence this module was written for.
    """
    both = set(ROLES) & set(WITHOUT_A_DECLARED_ROLE)
    if both:
        raise RuntimeError(
            f"library/tools/craft_role.py has {sorted(both)} in BOTH "
            f"ROLES and WITHOUT_A_DECLARED_ROLE. A step has a declared "
            f"role or it does not."
        )
    unaccounted = sorted(
        _REACHES_A_MODEL - set(ROLES) - set(WITHOUT_A_DECLARED_ROLE))
    if unaccounted:
        raise RuntimeError(
            f"library/tools/craft_role.py does not account for the "
            f"model-reaching step(s) {', '.join(unaccounted)}. Add each to "
            f"ROLES (with the discipline worked out) or to "
            f"WITHOUT_A_DECLARED_ROLE saying what it is addressed as now."
        )
    stale = sorted(
        (set(ROLES) | set(WITHOUT_A_DECLARED_ROLE)) - _REACHES_A_MODEL)
    if stale:
        raise RuntimeError(
            f"library/tools/craft_role.py names {', '.join(stale)}, which "
            f"undetermined.DECLARING_STEPS says does not reach a model. A "
            f"role for a step with no prompt reaches nothing."
        )
    for step_id, role in ROLES.items():
        if role.step_id != step_id:
            raise RuntimeError(
                f"library/tools/craft_role.py keys {step_id!r} onto a role "
                f"whose step_id is {role.step_id!r}.")
        if not (role.discipline and role.addressed_as):
            raise RuntimeError(
                f"the {step_id!r} role names no discipline or does not "
                f"address the model as one.")
        if not (role.reads_with and role.decides and role.defers):
            raise RuntimeError(
                f"the {step_id!r} role is incomplete. A role is a "
                f"discipline, what it reads the measurements with, what it "
                f"decides and what it does not; a role with no boundary "
                f"reads as licence.")


_assert_roles_account_for_every_model_reaching_step()


def declares(step_id: str) -> bool:
    """Does this step have a declared role?"""
    return step_id in ROLES


def role_for(step_id: str):
    """The declared role, or None."""
    return ROLES.get(step_id)


def prompt_block(step_id: str) -> str:
    """The role, as the text prepended to that step's handoff.

    Empty for a step with no declared role, so the call site is one
    unconditional line and a step that gains a role needs no runner
    change.
    """
    role = ROLES.get(step_id)
    if role is None:
        return ""

    lines = [
        f"## Who you are: the {role.discipline}",
        "",
        role.addressed_as,
        "",
        "### What you read this with",
        "",
    ]
    lines += [f"- {item}" for item in role.reads_with]
    lines += ["", "### What is yours to decide here", ""]
    lines += [f"- {item}" for item in role.decides]
    lines += ["", "### What is not yours", ""]
    lines += [f"- {item}" for item in role.defers]
    if role.corrects:
        lines += ["", "### Corrections to the document below", ""]
        lines += [f"- {item}" for item in role.corrects]
    lines += ["", NEUTRALITY_LINE, "", "---", "", ""]
    return "\n".join(lines)
