"""Who the model IS when a step asks it to make a craft judgement.

A measurement on 2026-08-25 claimed that not one of the pipeline's
handoff documents told the answering model what job it was doing, and that
every one of them opened *"Given X, define Y"*. That measurement read the
## Task Prompt section and missed the ## System Context above it, where
eleven of thirteen handoffs name a discipline in the second person. The
captain's reading of what the absence of a role costs:

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

**It goes in the prompt.** A role may carry a correction in `corrects`,
naming a line in the handoff. The same route
`music_measurement.MEASUREMENT_LEGEND` and
`sfx_level.SPEECH_REFERENCE_LEGEND` take - the correction travels as
DATA beside the context.

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
level up.  Ten of the twelve are there today; each row says what the
step is addressed as now, so the next worker adding one knows what they
are replacing.

`tests/test_craft_role.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/craft_role.py`. [why - the measurement, and the two defects it explains](docs/RULE_EVIDENCE.md#twelve-handoffs-no-role) A measurement on 2026-08-25 claimed not one of the twelve handoffs told the model what job it was doing (it missed the System Context section). Captain: *"there are like skill files and agent.md files where the LLM doesn't know how to operate and its just a generic agent in the system rather than an actual proffesional video editor/director/etc all in one."*
- **A role is THREE things and no fourth**: a DISCIPLINE named and addressed in the second person; what that discipline READS THE MEASUREMENTS WITH (craft knowledge a number does not carry - a colourist knows a dark shot can be dark on purpose); and what is this step's to DECIDE and what is not. An authority statement with no boundary reads as licence.
- **A role states NO preference about the answer.** Not how many of anything, not how strong, not which way a judgement comes out. **A floor in a role block is a floor**: `tests/test_no_creative_floors.py` reads the RENDERED role text of every declared role, because the file-based half cannot see text that lives in a Python module.
- **It is PREPENDED to the handoff by `present_llm_step`**, which is the one thing it does differently from `undetermined` and its siblings - those ask for a FIELD and belong beside the schema, and a role is the frame the rest of the document is read in. **`replay_bench/reconstruct.py` mirrors it**, or `verify` reports every role-carrying step as an unaccounted difference.
- **It goes in the prompt**, and a role may carry a `corrects` line naming a withdrawn instruction still in a handoff - the `SPEECH_REFERENCE_LEGEND` route. 4.04's role corrects its handoff's volume words like `subtle|low|medium|prominent`.
- **`ROLES` and `WITHOUT_A_DECLARED_ROLE` must TOGETHER account for every step that reaches a model**, and an unaccounted one raises at import. The model-reaching half is borrowed from `undetermined.DECLARING_STEPS`. **A row in the second table is a gap made VISIBLE, not closed** - writing a role for a discipline nobody has studied is this module inventing an expertise. Two are declared (`color_grade`, `plan_sfx`); ten are not, each with what it is addressed as today.
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
#:
#: `a setting nobody chose` was `a value nobody chose` until 2026-09-06.
#: The meaning is unchanged - the sentence has always been about counts,
#: strengths and directions arriving from the engine - and the word was
#: retired because `reel_quality_bar.FORBIDDEN_IN_THE_ASK` forbids it in
#: any prompt sent to `judge_reels`, where "value" names one of the four
#: things that step's answer is read for. A shared line that cannot go
#: in one role's prompt is a shared line that has to be reworded, not
#: carved out: a guard with an exemption reads as coverage of the case
#: it exempts (AGENTS.md 10.4).
NEUTRALITY_LINE = (
    "This block says who you are and what is yours to decide. It does not "
    "say how the decision should come out: no count, no strength and no "
    "direction is stated here or anywhere else in this engine, because a "
    "setting nobody chose arriving from the engine is the defect this "
    "pipeline keeps removing."
)


ROLES: Dict[str, CraftRole] = {
    # The one role in this file written to be READ BY A GUARD as well as
    # by a model.  Step 3.05's whole design is that the reader does not
    # know what its answer will be read for, so every word of this block
    # goes through `reel_quality_bar.assert_ask_is_uncontaminated` along
    # with the handoff it is prepended to - and a role naming the four
    # things the answer is read for would defeat the step rather than
    # frame it.  See tests/test_reel_quality_bar.py.
    "judge_reels": CraftRole(
        step_id="judge_reels",
        discipline="first listener",
        addressed_as=(
            "You are the first person to hear these short videos. You have "
            "not heard the conversation they were cut from, nobody has "
            "told you what it was about, and what reaches you is the words "
            "a listener hears and nothing else. Writing down what each one "
            "actually says is your job."
        ),
        reads_with=(
            "A listener hears a short video ONCE, in order, with no way to "
            "rewind and nothing before it. Anything the words lean on that "
            "is not in the words is simply missing for them, however "
            "obvious it would be to somebody who had heard the whole "
            "conversation - and you are the only person in this pipeline "
            "who has not.",
            "Speech is not prose. People restart sentences, talk over each "
            "other and finish a thought two turns after they start it, and "
            "a transcript records all of it. A stumble is not a defect in "
            "the recording and a tidy sentence is not evidence of "
            "anything; read what was said, not what somebody meant to say.",
            "What a listener carries away is a different thing from what "
            "was interesting to hear. Somebody can enjoy two people "
            "agreeing with each other for a minute and be left holding "
            "nothing, and that is an ordinary outcome rather than a "
            "failure of your reading.",
            "You are shown these as a SET, and the ordering you write is "
            "over the set as it stands. It is an ordering and nothing "
            "else - which one you put first says something, and the "
            "distance between the eleventh and the thirteenth says "
            "nothing.",
        ),
        decides=(
            "What each short video is saying, and in which of its own "
            "words it says it.",
            "What a listener is left holding afterwards, if anything.",
            "What it takes for granted that a listener could not know from "
            "it alone.",
            "Where it stops going anywhere.",
            "The ordering, and what you order them on - nobody has told "
            "you what these are for, and that is deliberate.",
        ),
        defers=(
            "Which of these is published, and whether any of them is "
            "built. That is the captain's, after the whole set is read.",
            "Where any of them starts or stops. The seconds were chosen "
            "before you saw them and there is no route from here to "
            "changing one.",
            "How they are captioned, graded, titled or scored. None of "
            "those exists yet and none of them is your reading's subject.",
            "What is done with your reading. It is recorded and read by "
            "code you do not see, so write what is true rather than what "
            "would be convenient.",
        ),
    ),
    "select_reels": CraftRole(
        step_id="select_reels",
        discipline="short-form editor",
        addressed_as=(
            "You are the short-form editor on this episode. The whole "
            "conversation has been transcribed and broken into turns for "
            "you, and every candidate stretch below carries what was "
            "measured about it. Which of these are worth cutting as "
            "standalone shorts, and where each one should start and stop, "
            "is your call."
        ),
        reads_with=(
            "A short is a COMPLETE SMALL THING, not an excerpt. It has to "
            "make sense to somebody who has never heard the episode, and "
            "it has to finish rather than stop - a clip that ends because "
            "the timecode ran out reads as a fragment however good the "
            "line was.",
            "This is a two-hander. A stretch where one person talks and "
            "the other says nothing is a monologue with a prompt attached, "
            "and the measurements below carry each speaker's share and the "
            "number of times the exchange changes hands so you can see "
            "which is which. A low share is not automatically wrong: a "
            "short question that opens a long answer, and a reaction that "
            "lands in the middle of one, both count for more than their "
            "seconds.",
            "A call to action at the end is PART of this format, not "
            "noise in it. The measurements report where the hosts pitch "
            "their product; that is information about the shape of the "
            "stretch, and a short that delivers something and then closes "
            "on the invitation is a complete one. An earlier version of "
            "this step treated pitch as a defect and discarded every "
            "stretch containing one, which removed the ending the format "
            "is built on.",
            "The same exchange is often recorded more than once, and "
            "single lines are re-taken inside one exchange. Both are "
            "reported below with the timecodes of each take. Which take "
            "plays, and whether a stretch that was recorded twice is worth "
            "one short or two, is an editorial question rather than a "
            "measurement.",
            "Length guidance for this series is 45 to 90 seconds, "
            "averaging around a minute. It is guidance you weigh, not a "
            "boundary the engine enforces: every candidate is listed with "
            "its length whether it falls inside that range or not, "
            "because a story that needs longer to finish is a real answer "
            "and a truncated one is not.",
        ),
        decides=(
            "Which stretches of this conversation are worth cutting as "
            "shorts at all, including the answer that a stretch the "
            "measurements like is not worth one.",
            "Where each short starts and ends - which turn opens it and "
            "which one closes it.",
            "Whether a stretch carries a complete story: what is given to "
            "the viewer, and what closes it.",
            "Which take plays where an exchange or a line was recorded "
            "more than once.",
            "Why each chosen stretch is worth a short, in a line the "
            "captain can accept or reject on.",
        ),
        defers=(
            "Whether a chosen short is actually built. The captain "
            "approves every one before a timeline exists, and nothing "
            "here builds anything.",
            "How the shorts are captioned, graded or titled. This step "
            "chooses the conversations; the rest of the pipeline dresses "
            "them.",
            "Anything about the master timeline. The cut is the captain's "
            "and this step only reads it.",
        ),
    ),
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
            "The engine tells a layer from a collapse by whether the entries "
            "sharing a timeline position name the SAME `spine_block_position`: "
            "several sounds planned onto one moment is a layer, and several "
            "moments landing on one timeline position is a placement collapse "
            "the compile refuses by name. So layering a moment costs you "
            "nothing - even when the layered moment is the only sound in "
            "the plan.",
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
        "Addressed as 'a creative director for shortform video content'. "
        "The discipline is a director's and it is the widest of the eleven, "
        "so it is the one most worth getting right and the one least safe to guess at."
    ),
    "speech_sequence": (
        "Addressed as 'a narrative editor constructing the spoken backbone'. "
        "This is a story editor's job - what the piece says and in what order - "
        "and it is the step whose answer every later step inherits."
    ),
    "music_selection": (
        "Addressed as 'a music supervisor for shortform video content'. "
        "A music supervisor's job, and the one step already handed a full measurement "
        "set per candidate (library/tools/music_measurement.py) with no statement "
        "of who is reading them."
    ),
    "mesh_spine": (
        "Addressed as 'an audio editor weaving speech and music'. "
        "This is the assembly editor conducting durations, gaps and the bed; "
        "the closest thing the pipeline has to a cutting room."
    ),
    "select_broll": (
        "Addressed as 'a visual editor selecting B-roll clips'. "
        "Its handoff is the most craft-aware of the twelve already. "
        "It is shown frame strips of every candidate window (library/tools/window_frames.py), "
        "which is the one place a role would have real pictures to be read with."
    ),
    "review_rough_cut": (
        "Addressed as 'a rough-cut reviewer — the last gate'. "
        "A supervising editor's review pass, and the one step whose whole "
        "output is a judgement about other steps' work."
    ),
    "plan_transitions": (
        "Addressed as 'a video editor planning transitions'. "
        "Sits next to plan_sfx in the cut and shares its identifier, so "
        "the two roles want writing together rather than one at a time."
    ),
    "plan_vfx": (
        "Addressed as 'a motion designer adding subtle visual effects'. "
        "Its INTENSITY_MAP was removed on 2026-09-02 for stating strengths "
        "nobody chose, which leaves the same shaped hole the colour half had: "
        "the authority moved to the model and nothing told the model it now had it."
    ),
    "render_motion_graphics": (
        "Addressed as 'deciding what additive graphics, if any, this video carries'. "
        "A motion designer's job. Held by another worker at the time of writing, "
        "so its directory is not touched here."
    ),
    "validate": (
        "Addressed as 'watching the RENDERED video and validating it'. "
        "The only one of the eleven whose judgement is about the finished "
        "render rather than a plan. Whether a QA pass wants a role at all - "
        "a gate that is told it is an expert may become a gate with an opinion - "
        "is a real question and it is the captain's."
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
