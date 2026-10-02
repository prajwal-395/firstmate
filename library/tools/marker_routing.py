"""Route a note the captain typed on the timeline to the step that owns it.

`marker_feedback.py` READS the captain's typed notes off a DaVinci Resolve
timeline and writes them durably to `<project>/marker_feedback/`.  This
module is the other half: it answers two questions about every collected
note, separately, and never lets one answer stand in for the other:

    WHAT IS IT ATTACHED TO - a specific clip, or a moment on the timeline
    WHICH STEP'S DECISION IS IT ABOUT

The first is a MEASUREMENT off the marker itself (`resolve_target`).  The
second is a ROUTING (`route_note`), and it is the one that can be wrong,
so it is allowed to answer "I do not know" and "more than one".


A clip note and a moment note are different things
--------------------------------------------------
A marker typed onto a CLIP (`CLIP_ATTACHED_SOURCES`) is about that clip
and carries `clip`.  A marker typed onto the TIMELINE
(`MOMENT_ATTACHED_SOURCES`) is about a moment and carries `clips_under`,
which is CONTEXT and is explicitly not an attachment.

`marker_feedback.MarkerNote.attached_clip` records the placement.  For a
pull file written before that, the placement is RECOVERED from the frame
arithmetic:

    timeline_frame = clip.timeline_start + (source_frame - clip.source_start)

Only a clip that both plays the source frame and lands on the note's own
timeline frame is a candidate, and the recovery is refused unless the
candidates are one clip - Resolve's linked audio and video items of one
placement count as one (`_same_placement`).  Zero candidates, or two
real ones, is reported as `unresolved`, never broken by picking the first.


Which step
----------
`STEP_DECISIONS` is the whole vocabulary of what a note can be routed to.
A note naming a step outside it is refused BY NAME
(`OUTCOME_UNKNOWN_STEP`) rather than sent to the nearest thing.

A note carrying a typed edit spec is routed by it first: its rows name
their owning steps (`BASIS_EDIT_SPEC`), and a spec still pending
translation or superseded by a later ledger decision holds the note back
from planning (`BASIS_EDIT_SPEC_PENDING`, `BASIS_EDIT_SPEC_SUPERSEDED`).
Otherwise, three bases, in this order:

* `declared` - the note says which step, either as a line
  `step: select_broll` typed into the marker's Name or Notes field, or as
  a `route` record in its `customData` (see `marker_payload`).  A
  declaration is authoritative and stops here.
* `vocabulary` - the note's own words name EXACTLY ONE step's decision.
* `stamped` - the words name nothing, and the ONE clip the note is typed
  on carries the decision that produced it (`stamped_decision`, from its
  `customData` or the decision ledger, `library/tools/timeline_decisions.py`).

and two non-answers, which are outcomes and not failures:

* `ambiguous` - the words name more than one step's decision.  Every
  candidate is reported.  Nothing breaks the tie.
* `unrouted` - the words name none.

THE STAMP RANKS BELOW THE WORDS: a stamp says what PRODUCED the picture,
the captain's words say what the note is ABOUT
(`timeline_decisions.STAMP_RANKS_BELOW_THE_WORDS`).  A MOMENT note never
reaches the stamp; the decisions under a moment are recorded as
`decision_context` and route nothing.

This is a ROUTER, not a chooser: there is no score, no ranking, no
tie-break and no default.  Where a word has two real readings - "zoom" is
a transition in `plan_transitions` and an effect in `plan_vfx` - both
steps declare it and a note using it comes out ambiguous; that is the
correct output.  `WITHDRAWN_ROUTERS` records the shapes considered and
refused.


Reaching the step
-----------------
`STEP_DECISIONS[...].delivery` says how:

* `DELIVERY_PROMPT` - the step has a `handoff.md`, declares
  `timeline_notes` in its manifest, and `gather_step_inputs` hands it
  `prompt_block()` - the captain's words with `PROMPT_LEGEND`, the same
  sentence for every step and so single-sourced here.  Each such step's
  handoff also carries a "Timeline Notes" section of its own.
* `DELIVERY_REPORT` - the step is deterministic and has no prompt.  The
  note is still routed, recorded and reported, stated as not
  prompt-deliverable with the reason.

**Nothing may silently drop a routed note.**  `assert_deliverable` is
called from `gather_step_inputs` and raises `UndeliverableNote` when a
note is routed to a step whose manifest does not declare
`timeline_notes`, naming the note and the step.

    python3 -m library.tools.marker_routing report --project <dir>
    python3 -m library.tools.marker_routing write  --project <dir>
    python3 -m library.tools.marker_routing steps

`tests/test_marker_routing.py`.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

**A collected note is routed to the step that owns the decision it is about, and an
ambiguous one is reported as ambiguous rather than sent somewhere.**
One enumeration, `library/tools/marker_routing.py`.
- **A CLIP note and a MOMENT note are different things and are never flattened together.**
  A `clip_marker`/`media_pool_marker`/`clip_comment` carries `clip`; a `timeline_marker` carries `clips_under` (CONTEXT).
  `marker_feedback.MarkerNote.attached_clip` records the placement; a pull file written
  refused - reported `unresolved` - unless the candidates are one clip. Resolve's linked
  `MarkerNote.attached_clip` records the placement; legacy recovery is refused unless candidates are one clip.
- **`STEP_DECISIONS` is the whole of what a note can be routed to**, each row naming the
  decision that step makes. A step outside it cannot be routed to, and a note naming one is
  refused BY NAME.
- **Two bases, and two non-answers.** `declared` is authoritative. Otherwise the note's words must name EXACTLY ONE step's decision. Two is `ambiguous`; none is `unrouted`. No score, no ranking, no tie-break, no default.
- **Delivery is `prompt` or `report`, declared per step.** A step with a `handoff.md` declares
  the `timeline_notes` input and `gather_step_inputs` hands it `prompt_block()` - the words
  plus a legend, single-sourced here because it is the same sentence for all fifteen, not
  because of the handoff freeze, which was lifted 2026-09-09. A deterministic step has no prompt at all; the note is still routed, recorded
  and reported, with that reason stated. `run_pipeline.project_step_context` restores
  `timeline_notes` BY NAME, so a `context_fields` allow-list neither has to list it nor can
  drop it (§10.1).
- **Nothing may silently drop a routed note.** `assert_deliverable` fails the run when a note
  is routed to a prompt step whose manifest does not declare the input, because a context
  assembled without it reads exactly like a run with no notes. `undelivered` accounts for
  every note that reaches no prompt, by name.
- **A note that reached NOBODY is named in the run summary and recorded on the note.** The summary prints them after `status` is decided. Resolving an ambiguity is the captain's call.
  after `status` is decided and `record_non_delivery` appends them to the same log the
  deliveries go to. **It stops at visibility**: `WITHDRAWN_ROUTERS` records why every tie-break
- The delivery log is APPENDED by the runner, in the `Kind.CAPTURED` area beside the pull
  files: a delivery is a thing that happened, and a later run delivering the same note does
  not unmake the record of the first. `ROUTED-NOTES.md` is generated from the pull files and
  never hand-edited.
- `tests/test_marker_routing.py`, whose note fixtures are the three the captain really typed.

The measurements and rulings behind these rules (the three notes off
001's timeline, the deleted SFX word list, the handoff freeze):
docs/evidence/marker_routing.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_payload, timeline_decisions  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

ROUTING_FORMAT = "marker_routing/1"
ROUTING_FILE_SUFFIX = ".routing.json"
REPORT_FILENAME = "ROUTED-NOTES.md"
DELIVERY_LOG_FILENAME = "deliveries.jsonl"

STEP_INPUT_NAME = "timeline_notes"
"""The ONE input name a step declares to accept the captain's notes.

One name, one injection point, one restore around the projection.  The
alternative surveyed was a bespoke key per step, which is fifteen names to
keep in step with fifteen manifests and nothing that fails when one of
them goes stale."""

KIND_ROUTE = "route"
"""A `customData` record declaring the step a note is about."""


# ── What a note can be attached to ──────────────────────────────────

TARGET_CLIP = "clip"
TARGET_MOMENT = "moment"

CLIP_ATTACHED_SOURCES = ("clip_marker", "media_pool_marker", "clip_comment")
MOMENT_ATTACHED_SOURCES = ("timeline_marker",)


# ── What a note can be routed to ────────────────────────────────────

DELIVERY_PROMPT = "prompt"
DELIVERY_REPORT = "report"


@dataclass(frozen=True)
class StepDecision:
    """One step, and the decision a note about it would be about."""

    node_id: str
    """The DAG's name for the step - what `pipeline_data.json`, both
    ledgers and the runner key everything by.  NOT the directory name;
    `project_layout.node_id_for` is the only translator (AGENTS.md 10.1)."""

    number: str
    """`3.02` - the number the captain and the docs use."""

    owns: str
    """One sentence: the decision this step makes that a note can be about."""

    delivery: str
    """`DELIVERY_PROMPT` or `DELIVERY_REPORT`."""

    delivery_note: str = ""
    """Why, for `DELIVERY_REPORT`."""

    terms: tuple = ()
    """The words that name THIS step's decision.  A word two steps'
    decisions can both be about belongs to both, and a note using it comes
    out ambiguous - that is the point, not a defect."""


_NO_PROMPT = (
    "deterministic step, no handoff.md and no prompt: the note is routed "
    "and reported for investigation, and reaches no model"
)

STEP_DECISIONS = (
    StepDecision(
        "semantic_analysis", "1.03",
        "what the vision pass measured about a clip - scene, camera, "
        "actions, objects, usable ranges",
        DELIVERY_PROMPT,
        terms=("vision pass", "semantic analysis"),
    ),
    StepDecision(
        "creative_direction", "2.01",
        "the stated direction for the piece - its mood, its energy arc "
        "and what it is trying to do",
        DELIVERY_PROMPT,
        terms=("creative direction", "the vibe", "overall tone",
               "the direction"),
    ),
    StepDecision(
        "speech_sequence", "2.02",
        "which spoken passages are used, in what order, and where each "
        "one is cut from",
        DELIVERY_PROMPT,
        terms=("passage", "passages", "script", "what he says",
               "what she says", "what they say", "the order", "speech",
               "spoken", "talking", "narration",
               # The captain's own word for a recorded attempt - four of
               # his 2026-09-19 notes say "two takes", "bad take" and
               # nothing else in this table. A take that must go is a
               # passage that must go, which is this step's decision.
               "take", "takes",
               # His 2026-09-19 words for cutting speech: audio "cut
               # off" mid-word (Reels 09, 29), an answer "cut out"
               # (Reel 10). Where each passage is cut from is this
               # step's own owns-sentence, and no other step's terms
               # name a cut boundary.
               "cut off", "cut out",
               # "says the same things over ... consolidated" (Reel 11)
               # and "repitition of words" (Reel 08) - repetition the
               # reel must lose is passage selection. Both of his
               # misspellings are terms too, verbatim: the table matches
               # his words, and his words include "repitition" and
               # "repition".
               "repetition", "repetitive", "repitition", "repition",
               "consolidate", "consolidated", "consolidation",
               # "fluf to remove where akshita messes up and recovers"
               # (Reel 03) - a stumbled passage she re-says. "fluf" is
               # his spelling, verbatim; "flub" is the general case. The
               # verb forms stay narrow on purpose: "messed up" describes
               # broken OUTPUT ("the subtitles are messed up", Reels 05
               # and 29 - owned by whatever is broken), while "messes
               # up" with a speaker recovering describes the ATTEMPT,
               # which is this step's decision.
               "flub", "fluf", "messes up", "recover", "recovers",
               # "no value add in the reel" (Reel 04), "no value prop
               # given" (Reel 14). A reel's proposition is carried by
               # what is said, so its absence is passage selection -
               # with the Reel 04 demote-or-archive conditional left to
               # the lane's judgement, since reel lifecycle is no DAG
               # step's decision.
               "value add", "value prop", "value proposition"),
    ),
    StepDecision(
        "music_selection", "2.04",
        "which music track plays, and which section of it",
        DELIVERY_PROMPT,
        terms=("music", "song", "track", "soundtrack"),
    ),
    StepDecision(
        "mesh_spine", "2.05",
        "the timeline spine - block order, the gaps between them, and "
        "each block's declared music behaviour",
        DELIVERY_PROMPT,
        terms=("pacing", "pace", "spine", "gap", "gaps", "silence",
               "duck", "ducking", "too long", "too short", "drags",
               # "this whole segment seems to be mistakenly placed here"
               # (Reel 13, 2026-09-19). Block order is this step's own
               # owns-sentence; "segment" alone stays out on purpose,
               # since subtitle and audio notes say it too. "mistakely"
               # is his spelling on that note, verbatim.
               "misplaced", "mistakenly placed", "mistakely placed"),
    ),
    StepDecision(
        "select_broll", "3.02",
        "which b-roll covers which block, and which standalone cutaways "
        "are inserted",
        DELIVERY_PROMPT,
        terms=("b-roll", "broll", "b roll", "cutaway", "cutaways",
               "cut away", "cutting away"),
    ),
    StepDecision(
        "review_rough_cut", "3.03",
        "the review of the rough cut before post-production",
        DELIVERY_PROMPT,
        terms=("rough cut",),
    ),
    StepDecision(
        "select_reels", "3.04",
        "which conversation stretches become reels, where they start and "
        "end, and which closer each reel uses",
        DELIVERY_PROMPT,
        terms=("standalone reel", "reel selection", "reel closer"),
    ),
    StepDecision(
        "plan_subtitles", "4.01",
        "how the words are grouped into caption cards, at the caption "
        "style the project and the template resolve to",
        DELIVERY_REPORT, _NO_PROMPT,
        terms=("subtitle", "subtitles", "caption", "captions",
               "caption card", "caption cards",
               # Seven of his 2026-09-19 notes are about the words on
               # screen - "CEO and CMO should be capitalized", "LA
               # Fitness is a proper noun", a "qu" the transcript should
               # filter out - without ever saying subtitle or caption.
               # The transcript is what the captions are grouped from,
               # so a note about its words is this step's decision.
               "transcript", "capitalized", "capitalised",
               "proper noun", "proper nouns"),
    ),
    StepDecision(
        "plan_transitions", "4.02",
        "which cuts carry a drawn transition, of which type, and how "
        "long it holds",
        DELIVERY_PROMPT,
        terms=("transition", "transitions", "crash zoom", "zoom",
               "flash", "dissolve", "wipe", "whip pan"),
    ),
    StepDecision(
        "plan_vfx", "4.03",
        "which visual effects are drawn on which clip, and how strong",
        DELIVERY_PROMPT,
        terms=("vfx", "effect", "effects", "blur", "blurry", "zoom",
               "shake", "glitch", "defocus", "glow", "grain", "vignette"),
    ),
    StepDecision(
        "plan_sfx", "4.04",
        "which sound effect plays, where, and at what level",
        DELIVERY_PROMPT,
        terms=("sfx", "sound effect", "sound effects", "whoosh", "swoosh",
               "impact", "riser"),
    ),
    StepDecision(
        "render_subtitles", "4.05",
        "how a caption card is drawn - the typeface, the weight, the box",
        DELIVERY_REPORT, _NO_PROMPT,
        terms=("font", "typeface"),
    ),
    StepDecision(
        "render_motion_graphics", "4.06",
        "the motion-graphic overlays, the speaker lower thirds and the "
        "timed text cards",
        DELIVERY_PROMPT,
        # `lower third` and the captain's own three words for it. Their
        # marker of 2026-09-12 asked for "a little label graphic", and
        # none of "lower third", "label graphic" or "name tag" matched
        # anything here - so a follow-up note about the thing they had
        # just asked for would have been reported as ambiguous rather
        # than delivered to the step that draws it
        # (`library/tools/speaker_identity.py` plans it, 4.06 renders
        # it).
        terms=("overlay", "overlays", "motion graphic", "motion graphics",
               "lower third", "lower thirds", "label graphic", "name tag",
               "timed text", "text card", "end card", "intro card",
               "outro card",
               # His 2026-09-19 words for the same layer: "can we use
               # this animation in all of the CTA's", "this animation
               # looks funny", "the bullets are mis-sized" (three reels).
               # No other step's decision is about animations or bullets,
               # so these name this one exactly. The CTA is this layer's
               # element (Reel 09's note asks for its animation), so its
               # name routes here too - including Reel 27's "this CTA
               # doesn't make sense here", which questions the element's
               # fit rather than the reel's words.
               "animation", "animations", "bullet", "bullets",
               "cta", "ctas", "call to action"),
    ),
    StepDecision(
        "color_grade", "5.01",
        "the declared look and the exposure normalisation applied to "
        "each clip",
        DELIVERY_PROMPT,
        terms=("grade", "grading", "colour", "color", "exposure",
               "too dark", "too bright", "saturation", "washed out",
               "contrast"),
    ),
    StepDecision(
        "audio_mix", "5.02",
        "the per-block levels - the music bed's curve and each clip's "
        "own gain",
        DELIVERY_PROMPT,
        terms=("volume", "levels", "the mix", "too loud", "too quiet",
               "inaudible", "clipping",
               # His 2026-09-19 Reel 15 note: "only use the main audio
               # channel" with "the other 3 audio channels bleeding".
               # The bare word "channel" is refused on purpose - the
               # motion-graphics roster owns a `channel_bug`, and a note
               # about that overlay must not land on the mix. The full
               # phrase names this step's decision and nothing else's.
               "audio channel", "audio channels"),
    ),
    StepDecision(
        "compile_manifest", "5.04",
        "the placement geometry every clip is conformed to - the pan, "
        "the fill zoom and the framing backdrop",
        DELIVERY_REPORT, _NO_PROMPT,
        terms=("framing", "letterbox", "letterboxed", "black bars",
               "cropped", "off centre", "off center", "pan"),
    ),
    StepDecision(
        "render", "6.01",
        "what the build did to the picture after placement - "
        "stabilisation, and the render itself",
        DELIVERY_PROMPT,
        terms=("stabilisation", "stabilization", "stabilised",
               "stabilized", "shaky", "the render"),
    ),
)

BY_NODE_ID = {d.node_id: d for d in STEP_DECISIONS}


# ── What was refused ────────────────────────────────────────────────

WITHDRAWN_ROUTERS = {
    "highest_term_count":
        "Score each step by how many of its terms the note contains and "
        "take the highest. This is the shape AGENTS.md section 10.5 "
        "deleted from the SFX chooser: it always produces an answer, so "
        "a note about nothing in the table still lands on a step, and a "
        "note about two steps lands on whichever list happens to be "
        "longer. Ambiguity is information and this discards it.",
    "nearest_step_by_embedding":
        "Embed the note and take the nearest step description. Same "
        "defect with a better disguise - a nearest neighbour is a "
        "chooser, and it cannot say 'not in this table' at all. "
        "`docs/FOOTAGE_INDEX_PROTOTYPE.md` had to establish a measured "
        "score floor before a ranking could answer that, and a routing "
        "with eighteen candidates has no way to measure one.",
    "the_clip_under_the_playhead_decides":
        "Route by what the note sits on - a note over a V2 clip is about "
        "b-roll, one on a cut is about transitions. Every frame of this "
        "pipeline's output has a V1 clip, a caption card, a music bed "
        "and often a cutaway under it, so the structure narrows nothing "
        "and would route by track index. The attachment is recorded as "
        "evidence for the reader and decides nothing.",
    "ask_a_model_which_step":
        "Hand the note to an LLM and let it name the step. It would "
        "answer every time, including for the notes that are genuinely "
        "ambiguous, and the pipeline would have invented a routing "
        "nobody can inspect. The captain declaring `step:` costs one "
        "line and is inspectable.",
}


# ── Declaring a step in the note ────────────────────────────────────

DECLARATION_RE = re.compile(
    r"^[ \t]*(?:step|steps)[ \t]*[:=][ \t]*(?P<targets>[^\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)
DECLARATION_RULE = (
    "A line reading `step: <name>` in a marker's Name or Notes field "
    "names the step the note is about. `step: select_broll` and "
    "`step: 3.02` are the same declaration. Several are separated by "
    "commas. A name that is not in STEP_DECISIONS is refused by name."
)

_SPLIT_TARGETS = re.compile(r"[,;]+")


def resolve_step_name(raw: str) -> Optional[StepDecision]:
    """The step a declaration names, by node id or by number, or None.

    Exact only.  A near match is a chooser, and a note routed to the
    wrong step is the failure this whole module is arranged around.
    """
    token = (raw or "").strip().strip("`'\"[]()").lower()
    if not token:
        return None
    for decision in STEP_DECISIONS:
        if token == decision.node_id.lower() or token == decision.number:
            return decision
    return None


def declared_steps(text: str, custom_data=None) -> tuple:
    """(resolved decisions, unresolved names) declared by this note.

    Two channels, both authoritative and both read: a `step:` line the
    captain typed, and a `route` record in `customData` that a writer -
    a Resolve panel, the capture button - put there where the marker UI
    cannot show it.  Neither outranks the other; a note carrying both is
    read as declaring both, and two different steps is an ambiguity like
    any other.
    """
    resolved, unknown = [], []

    def take(raw):
        decision = resolve_step_name(raw)
        if decision is None:
            if raw.strip():
                unknown.append(raw.strip())
        elif decision not in resolved:
            resolved.append(decision)

    for match in DECLARATION_RE.finditer(text or ""):
        for token in _SPLIT_TARGETS.split(match.group("targets")):
            take(token)
    for record in marker_payload.records_of(custom_data or {}, KIND_ROUTE):
        take(str(record.get("step") or ""))
    return tuple(resolved), tuple(unknown)


# ── The words the captain used ──────────────────────────────────────

def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term).replace(r"\ ", r"[ \t]+")
    left = r"(?<![\w-])" if term[:1].isalnum() else ""
    right = r"(?![\w-])" if term[-1:].isalnum() else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


_TERM_PATTERNS = {
    decision.node_id: tuple((term, _term_pattern(term))
                            for term in decision.terms)
    for decision in STEP_DECISIONS
}


def matched_terms(text: str) -> dict:
    """{node_id: [the terms of that step this note contains]}.

    No count is compared against another count and no list is ranked.
    The caller's rule is on how many STEPS matched, never on how well.
    """
    out = {}
    for node_id, patterns in _TERM_PATTERNS.items():
        hits = [term for term, pattern in patterns if pattern.search(text or "")]
        if hits:
            out[node_id] = hits
    return out


# ── The result ──────────────────────────────────────────────────────

BASIS_DECLARED = "declared"
BASIS_VOCABULARY = "vocabulary"
BASIS_STAMPED = "stamped"
BASIS_EDIT_SPEC = "edit_spec"
BASIS_EDIT_SPEC_PENDING = "edit_spec_pending"
BASIS_EDIT_SPEC_SUPERSEDED = "edit_spec_superseded"
"""The clip this note is attached to carries the decision that produced
it - `library/tools/timeline_decisions.py`, the producer side of this
loop.  It ranks BELOW the words on purpose; `STAMP_RANKS_BELOW_THE_WORDS`
there has the captain's own note that measures why."""

OUTCOME_ROUTED = "routed"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_UNROUTED = "unrouted"
OUTCOME_UNKNOWN_STEP = "unknown_step"


@dataclass
class NoteTarget:
    """What a note is attached to.  A clip, or a moment.  Never both."""

    kind: str
    """`TARGET_CLIP` or `TARGET_MOMENT`."""

    basis: str
    """`recorded` (the reader wrote the placement down), `rederived`
    (recovered from the frame arithmetic), `unresolved`, or `timeline`
    for a moment."""

    clip: Optional[dict] = None
    """The placement, when `kind` is `TARGET_CLIP` and it is known."""

    clips_under: list = field(default_factory=list)
    """For a moment: everything playing at that frame.  CONTEXT.  This is
    deliberately not the same field as `clip`, so a reader cannot mistake
    "what was on screen" for "what the captain selected"."""

    tracks: list = field(default_factory=list)
    """`["video1", "audio1"]` - the tracks one placement was seen on."""

    reason: str = ""


@dataclass
class RoutedNote:
    """One collected note, its attachment and where it is going."""

    note_id: str
    source: str
    name: str
    note: str
    text: str
    frame: Optional[int]
    timecode: Optional[str]
    frame_in_timeline_space: Optional[int]
    unplaced_reason: str
    collected_at: str
    timeline: str
    pull_file: str

    duration_frames: int = 1
    """How long the marker is. A Resolve marker carries a `duration` and
    `marker_feedback` reads it verbatim, so a COLLECTED note is an
    INTERVAL - and this class used to drop it, collapsing every note to a
    point at the one boundary where the interval could still have been
    used. A `clip_comment` carries the clip's whole timeline span here."""

    timeline_fps: float = 0.0
    """The timeline's own frame rate, off the pull file's `timeline_fps`.
    Recorded at pull time and read by nobody until now."""

    edit_link_id: str = ""
    """The id an edit spec and its ledger rows link to (`edit_link_id`):
    the note id plus its words, so two notes on one frame stay apart."""

    timeline_start_frame: int = 0
    """Where the timeline's own frame numbering starts, off the pull
    file. Resolve's default is 01:00:00:00, which is frame 108000 at
    30fps, so this is NOT zero and subtracting it is not optional."""

    target: dict = field(default_factory=dict)
    outcome: str = OUTCOME_UNROUTED
    basis: str = ""
    steps: list = field(default_factory=list)
    """Node ids.  One for `routed`, two or more for `ambiguous`, none
    otherwise."""

    evidence: dict = field(default_factory=dict)
    """{node_id: [terms]} for a vocabulary routing; the declaration for a
    declared one.  What a reader checks the routing against."""

    unknown_names: list = field(default_factory=list)
    reason: str = ""
    attachments: list = field(default_factory=list)

    decision: dict = field(default_factory=dict)
    """The decision that PRODUCED the clip this note is attached to, when
    there is one - `{step, decision_id, basis, locator, routes, source}`.
    `source` is `custom_data` where the marker carries the stamp itself
    and `ledger` where it was looked up in the project's decision ledger.
    A dict carrying only `reason` says why there is none."""

    decision_context: list = field(default_factory=list)
    """For a MOMENT note: the decision behind everything playing at that
    frame.  CONTEXT for a reader, exactly like `clips_under`, and it
    routes nothing - `WITHDRAWN_ROUTERS['the_clip_under_the_playhead_decides']`
    is why."""


def note_span_seconds(note: "RoutedNote"):
    """The note's own interval, in TIMELINE SECONDS, or None.

    THE ONE PLACE this conversion happens, because it is the conversion
    that is easy to get wrong and expensive to notice:

        seconds = (frame - timeline_start_frame) / fps

    `MarkerNote.frame` is an ABSOLUTE timeline frame and a Resolve
    timeline starts at 01:00:00:00 by default - frame 108000 at 30fps -
    so `frame / fps` is an hour out and looks entirely plausible. That is
    the same domain collision `library/tools/region.py` exists to make
    unrepresentable, one level up, and the reason this returns SECONDS
    rather than handing frames to a caller who will divide them.

    None when the note could not be placed on the timeline, or when the
    pull file predates `timeline_fps` being recorded. A span that cannot
    be computed is ABSENT, never zero: a note at 0.0..0.0 would read as
    the first frame of the cut.
    """
    if note.frame is None or not note.timeline_fps:
        return None
    start = (note.frame - note.timeline_start_frame) / note.timeline_fps
    end = start + max(1, note.duration_frames) / note.timeline_fps
    return (round(start, 3), round(end, 3))


def _note_id(raw: dict, timeline: str, pull_file: str) -> str:
    """Stable across re-routings of the same note, and readable.

    The timeline, the kind of marker and the frame - which is what the
    captain would use to find it again in Resolve.  A pull file's name is
    the timeline's, so falling back to it changes nothing.
    """
    stem = timeline or Path(pull_file).name.split(".")[0] or "timeline"
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    frame = raw.get("frame")
    if frame is None:
        from library.tools.marker_feedback import _attachment_identity

        identity = [raw.get("source", ""), raw.get("name", ""),
                    raw.get("note") or raw.get("text", ""),
                    raw.get("frame_in_timeline_space"),
                    _attachment_identity(raw.get("attachments"))]
        digest = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()[:12]
        where = f"unplaced-{digest}"
    else:
        where = str(frame)
    return f"{stem}:{raw.get('source', 'note')}:{where}"


def edit_link_id(raw: dict, timeline: str, pull_file: str) -> str:
    """The id an edit spec and its ledger rows are linked to.

    `_note_id` names a marker by where it sits, so two notes typed on one
    frame share it - harmless for a report, wrong for a translation: the
    second note would inherit the first one's typed operations and never
    be translated. The words join the id here, not in `_note_id`, because
    marker resolutions are already stored under `_note_id`.
    """
    words = raw.get("text") or raw.get("note") or raw.get("name") or ""
    digest = hashlib.sha256(
        words.strip().encode("utf-8")).hexdigest()[:10]
    return f"{_note_id(raw, timeline, pull_file)}~{digest}"


def _same_placement(a: dict, b: dict) -> bool:
    """Resolve's linked audio and video items of ONE placement.

    Same file, same span on the timeline, same span in the source.  A
    pair that agrees on all three is one clip seen twice, not two
    candidates, so recovering the attachment from it is not a guess.
    """
    keys = ("source_file", "timeline_start", "timeline_end",
            "source_start", "source_end")
    return all(a.get(k) == b.get(k) for k in keys)


def resolve_target(raw: dict) -> NoteTarget:
    """What this note is attached to.  See the module docstring."""
    source = raw.get("source", "")
    clips = list(raw.get("clips") or [])
    if source in MOMENT_ATTACHED_SOURCES:
        return NoteTarget(
            kind=TARGET_MOMENT, basis="timeline", clips_under=clips,
            reason="a timeline marker is about the moment; the clips "
                   "under it are context, not what it is attached to",
        )
    if source not in CLIP_ATTACHED_SOURCES:
        return NoteTarget(
            kind=TARGET_MOMENT, basis="unresolved", clips_under=clips,
            reason=f"unknown marker source {source!r}",
        )

    recorded = raw.get("attached_clip")
    if isinstance(recorded, dict) and recorded:
        return NoteTarget(
            kind=TARGET_CLIP, basis="recorded", clip=recorded,
            tracks=[f"{recorded.get('track_type')}{recorded.get('track_index')}"],
        )

    # Recover it.  A pull file written before the reader recorded the
    # placement still names every clip at the frame, and only one of them
    # can be the one whose own arithmetic produced this note's frame.
    frame = raw.get("frame")
    key = raw.get("frame_in_timeline_space")
    if frame is None or key is None:
        return NoteTarget(
            kind=TARGET_CLIP, basis="unresolved", clips_under=clips,
            reason="the note has no placed frame, so no clip's arithmetic "
                   "can be checked against it",
        )
    if source == "clip_comment":
        candidates = [c for c in clips if c.get("timeline_start") == frame]
    else:
        candidates = [
            c for c in clips
            if c.get("source_start") is not None
            and c["source_start"] <= key < c.get("source_end", key)
            and c["timeline_start"] + (key - c["source_start"]) == frame
        ]
    if not candidates:
        return NoteTarget(
            kind=TARGET_CLIP, basis="unresolved", clips_under=clips,
            reason=f"no clip at frame {frame} plays source frame {key}",
        )
    first = candidates[0]
    if not all(_same_placement(first, c) for c in candidates[1:]):
        named = ", ".join(
            f"{c.get('name')} ({c.get('track_type')}{c.get('track_index')})"
            for c in candidates)
        return NoteTarget(
            kind=TARGET_CLIP, basis="unresolved", clips_under=clips,
            reason=f"{len(candidates)} different clips fit the arithmetic "
                   f"and nothing here can choose between them: {named}",
        )
    return NoteTarget(
        kind=TARGET_CLIP, basis="rederived", clip=first,
        tracks=[f"{c.get('track_type')}{c.get('track_index')}"
                for c in candidates],
        reason="recovered from the frame arithmetic; this pull file "
               "predates the reader recording the placement",
    )


# ── The decision that produced the clip ─────────────────────────────

def _record_matches_clip(record: dict, clip: dict) -> bool:
    """Is this stamped record about THIS placement?

    Compared on the two things both sides record - the source file's
    name and where the clip starts on the timeline.  A record that names
    neither is not assumed to match."""
    basename = Path(str(clip.get("source_file") or "")).name
    named = str(record.get("source") or "")
    if named and basename and named != basename:
        return False
    frame = record.get("timeline_in_frame")
    start = clip.get("timeline_start")
    if frame is not None and start is not None and int(frame) != int(start):
        return False
    return bool(named or frame is not None)


def _decision_from(record: dict, source: str) -> dict:
    return {
        "step": str(record.get("step") or ""),
        "decision_id": str(record.get("decision_id") or ""),
        "basis": str(record.get("basis") or ""),
        "locator": str(record.get("locator") or ""),
        "track": str(record.get("track") or ""),
        "label": str(record.get("label") or ""),
        "routes": dict(record.get("routes") or {}),
        "source": source,
    }


def stamped_decision(target: dict, custom_data=None, ledger=None) -> dict:
    """What decided the ONE clip this note is attached to, or why nothing.

    Two sources, in this order and both reported: the marker's own
    `customData`, which is what the timeline was stamped with and travels
    with the Resolve project; and the project's decision ledger, looked
    up by the placement, for a marker nothing stamped.  Never a guess -
    an unresolved attachment, a moment note or a placement no step
    decided all come back as a stated reason.
    """
    if target.get("kind") != TARGET_CLIP:
        return {"reason": "a timeline marker is about a MOMENT, and every "
                          "frame here has a V1 clip, a caption and a music "
                          "bed under it; the stamp of each is recorded as "
                          "context and decides nothing"}
    clip = target.get("clip")
    if not clip:
        return {"reason": target.get("reason")
                or "the clip this note is attached to is unresolved"}
    hits = [r for r in marker_payload.records_of(
                custom_data or {}, timeline_decisions.KIND_DECISION)
            if _record_matches_clip(r, clip)]
    if len(hits) == 1:
        return _decision_from(hits[0], "custom_data")
    if len(hits) > 1:
        return {"reason": f"the marker carries {len(hits)} decision records "
                          f"for this placement and nothing here chooses "
                          f"between them"}
    placement = timeline_decisions.placement_for_clip(ledger or {}, clip)
    if placement:
        return _decision_from(placement, "ledger")
    if not (ledger or {}).get("placements"):
        return {"reason": "the marker carries no decision stamp and this "
                          "project has no decision ledger; build one with "
                          "`python3 -m library.tools.timeline_decisions "
                          "ledger --project <dir>`"}
    return {"reason": "no placement in the decision ledger is this clip - "
                      "the ledger describes a different build, or nothing "
                      "decided this placement (see "
                      "timeline_decisions.UNSTAMPED_PLACEMENTS)"}


def route_note(raw: dict, timeline: str = "", pull_file: str = "",
               ledger=None, timeline_fps: float = 0.0,
               timeline_start_frame: int = 0, edit_rows=None,
               pending_edit_spec=None) -> RoutedNote:
    """Route ONE collected note.  Never raises on the note's content.

    `timeline_fps` and `timeline_start_frame` are the PULL FILE's own
    record of the timeline the note was typed on. They are what turn the
    note's frames into the timeline seconds a region is measured in; both
    default to a reading that produces no span rather than a wrong one.
    """
    text = raw.get("text") or "\n\n".join(
        p for p in (raw.get("name") or "", raw.get("note") or "") if p)
    routed = RoutedNote(
        note_id=_note_id(raw, timeline, pull_file),
        edit_link_id=edit_link_id(raw, timeline, pull_file),
        source=raw.get("source", ""),
        name=raw.get("name", ""),
        note=raw.get("note", ""),
        text=text,
        frame=raw.get("frame"),
        timecode=raw.get("timecode"),
        frame_in_timeline_space=raw.get("frame_in_timeline_space"),
        unplaced_reason=raw.get("unplaced_reason", ""),
        collected_at=raw.get("read_at", ""),
        timeline=timeline,
        pull_file=pull_file,
        duration_frames=int(raw.get("duration_frames") or 1),
        timeline_fps=float(timeline_fps or 0.0),
        timeline_start_frame=int(timeline_start_frame or 0),
        target=asdict(resolve_target(raw)),
        attachments=list(raw.get("attachments") or []),
    )
    routed.decision = stamped_decision(
        routed.target, raw.get("custom_data"), ledger)
    if routed.target.get("kind") == TARGET_MOMENT:
        routed.decision_context = [
            _decision_from(p, "ledger") for p in
            timeline_decisions.placements_at_frame(
                ledger or {}, raw.get("frame"))
        ]

    if pending_edit_spec:
        if pending_edit_spec.get("state") == "superseded":
            routed.basis = BASIS_EDIT_SPEC_SUPERSEDED
            routed.outcome = OUTCOME_UNROUTED
            routed.evidence = {"superseded_edit_spec": pending_edit_spec}
            routed.reason = (
                "this note's typed edit has been superseded by a later "
                "ledger decision; it must not reach planning again")
            return routed
        routed.basis = BASIS_EDIT_SPEC_PENDING
        routed.outcome = OUTCOME_UNROUTED
        routed.evidence = {"pending_edit_spec": pending_edit_spec}
        routed.reason = (
            "this note has an edit spec waiting for translation or editor "
            "clarification; it must not reach a planning step yet. "
            + str(pending_edit_spec.get("reason", "")))
        return routed

    typed_rows = [row for row in (edit_rows or [])
                  if row.get("source_note_id") == routed.edit_link_id]
    if typed_rows:
        from library.tools.edit_operations import owner_for_row
        operations = []
        for row in typed_rows:
            params = row["params"]
            op = (params["operation_type"] if row["op"] == "plan_change"
                  else row["op"])
            owner = owner_for_row(row)
            op_params = (params["values"] if row["op"] == "plan_change"
                         else params)
            operations.append({"op": op, "owner": owner,
                               "ledger_op": row["op"],
                               "anchor": row["anchor"],
                               "params": op_params,
                               "value_units": row.get("value_units", {}),
                               "value_sources": row.get("value_sources", {}),
                               "carry_stated_numbers_in":
                                   _stated_number_fields(row, owner)})
        routed.basis = BASIS_EDIT_SPEC
        routed.evidence = {"edit_spec_ops": operations}
        routed.steps = list(dict.fromkeys(op["owner"] for op in operations))
        unknown = [step for step in routed.steps if step not in BY_NODE_ID]
        if unknown:
            routed.unknown_names = unknown
            routed.outcome = OUTCOME_UNKNOWN_STEP
            routed.reason = (
                "the edit spec names operation owners that are not in this "
                f"pipeline: {', '.join(unknown)}")
        else:
            routed.outcome = OUTCOME_ROUTED
            routed.reason = (
                "the resolved edit spec routes each clause by operation "
                "type: " + ", ".join(
                    f"{op['op']} -> {op['owner']}" for op in operations))
        return routed

    declared, unknown = declared_steps(text, raw.get("custom_data"))
    routed.unknown_names = list(unknown)
    if declared:
        routed.basis = BASIS_DECLARED
        routed.steps = [d.node_id for d in declared]
        routed.evidence = {"declared": list(routed.steps)}
        routed.outcome = (OUTCOME_ROUTED if len(declared) == 1
                          else OUTCOME_AMBIGUOUS)
        routed.reason = (
            "the note declares its step"
            if len(declared) == 1 else
            f"the note declares {len(declared)} steps and nothing here "
            f"chooses between them"
        )
        return routed
    if unknown:
        routed.outcome = OUTCOME_UNKNOWN_STEP
        routed.reason = (
            f"the note declares {', '.join(repr(u) for u in unknown)}, "
            f"which is not a step this pipeline can route to. Run "
            f"`python3 -m library.tools.marker_routing steps` for the "
            f"whole list."
        )
        return routed

    hits = matched_terms(text)
    routed.evidence = hits
    if len(hits) == 1:
        routed.basis = BASIS_VOCABULARY
        routed.outcome = OUTCOME_ROUTED
        routed.steps = list(hits)
        routed.reason = (
            f"the note's own words name one step's decision: "
            f"{', '.join(repr(t) for t in hits[routed.steps[0]])}"
        )
    elif hits:
        routed.basis = BASIS_VOCABULARY
        routed.outcome = OUTCOME_AMBIGUOUS
        routed.steps = sorted(hits)
        routed.reason = (
            "the note's words name more than one step's decision "
            + "; ".join(
                f"{node} ({', '.join(repr(t) for t in hits[node])})"
                for node in routed.steps)
            + f". Nothing here chooses between them - add a line "
              f"`step: <name>` to the marker to decide it."
        )
    else:
        # THE STAMP, and this is the only place it decides anything. The
        # words have named nothing, so the best remaining fact is which
        # decision produced the clip the captain selected and typed on.
        # That is not "the clip under the playhead decides" - a clip
        # marker is the captain's own selection, not the stack at a
        # frame, and a moment note never reaches here.
        step = routed.decision.get("step")
        if step and step in BY_NODE_ID:
            routed.basis = BASIS_STAMPED
            routed.outcome = OUTCOME_ROUTED
            routed.steps = [step]
            routed.evidence = {"stamped": dict(routed.decision)}
            routed.reason = (
                f"the note's words name no step's decision, and the clip "
                f"it is typed on carries the decision that produced it: "
                f"{routed.decision.get('decision_id') or step} "
                f"({routed.decision.get('basis') or 'unstated basis'}, "
                f"from the {routed.decision.get('source')})"
            )
        elif step:
            routed.outcome = OUTCOME_UNKNOWN_STEP
            routed.unknown_names = [step]
            routed.reason = (
                f"the clip is stamped {step!r}, which is not a step this "
                f"pipeline can route to. Run `python3 -m "
                f"library.tools.marker_routing steps` for the whole list."
            )
        else:
            routed.outcome = OUTCOME_UNROUTED
            routed.reason = (
                "the note's words name no step's decision, and "
                + (routed.decision.get("reason")
                   or "the clip carries no decision stamp")
                + ". Add a line `step: <name>` to the marker to route it."
            )
    return routed


# ── Every note this project has collected ───────────────────────────

def _pull_payloads(project_folder) -> list:
    from library.tools import marker_feedback
    return marker_feedback.pulled_files(project_folder)


def route_project(project_folder, timelines=None) -> list:
    """Every collected note of this project, routed, oldest pull first.

    Deduplicated on the same identity `marker_feedback` uses for "have I
    already collected this" - the text, where it was typed and what is
    attached - so a note collected by three pulls is one routed note and
    the LATEST reading of it wins.

    `timelines` scopes the routing to pull files from those timeline
    names (exact match, AGENTS.md 5). Omitted, every pull file ever
    written routes - including ones from staged, backed-up and archived
    containers no timeline carries any more. Measured 2026-09-19: 94
    pull files routed to 63 notes, of which 26 sat on dead containers
    and 37 on the 31 live timelines. A rebuild wave acting on the
    unscoped record works ghosts alongside the captain's current words.
    """
    from library.tools.marker_feedback import _attachment_identity
    from library.tools import edit_ledger, edit_spec

    wanted = set(timelines or [])
    # Read once for the whole project: the ledger is one file and every
    # note is looked up in the same one.
    ledger = timeline_decisions.read_ledger(project_folder)
    edit_rows = edit_ledger.load_rows(project_folder)
    # Keep `ren notes` useful as a routing diagnostic: it shows the old
    # vocabulary guess for an untranslated note, but the run guard refuses
    # that guess before planning. Only an opened/recorded edit spec changes
    # what this report says.
    pending_specs = edit_spec.pending_note_states(
        project_folder, rows=edit_rows, include_untranslated=False)
    seen: dict = {}
    order: list = []
    for path, payload in _pull_payloads(project_folder):
        if wanted and payload.get("timeline", "") not in wanted:
            continue
        for raw in payload.get("notes", []):
            identity = (
                raw.get("source", ""), raw.get("name", ""),
                raw.get("note", ""), raw.get("frame_in_timeline_space"),
                _attachment_identity(raw.get("attachments")),
            )
            routed = route_note(
                raw, payload.get("timeline", ""), str(path), ledger=ledger,
                timeline_fps=payload.get("timeline_fps") or 0.0,
                timeline_start_frame=payload.get("timeline_start_frame") or 0,
                edit_rows=edit_rows,
                pending_edit_spec=pending_specs.get(
                    edit_link_id(raw, payload.get("timeline", ""),
                                 str(path))))
            if identity not in seen:
                order.append(identity)
            seen[identity] = routed
    return [seen[i] for i in order]


def notes_for_step(routed_notes, node_id: str) -> list:
    """The notes routed to ONE step.  Ambiguous ones reach nobody."""
    return [n for n in routed_notes
            if n.outcome == OUTCOME_ROUTED and node_id in n.steps]


# ── The form a step accepts ─────────────────────────────────────────

PROMPT_LEGEND = (
    "The captain reviewed the built timeline in DaVinci Resolve and typed "
    "these notes onto it. They are their own words, verbatim, routed to "
    "this step because this step owns the decision each note is about. "
    "`attached_to` says whether the note was typed on a specific CLIP or "
    "at a MOMENT on the timeline - a clip note is about that clip, a "
    "moment note is about what is happening then. `clip_decided_by` and "
    "`clip_decision_id` name the decision that PRODUCED that clip, and "
    "`clip_decision_written_in` says where in that step's own output it "
    "is written down; they describe the picture, not what the note "
    "means. Read them as context "
    "for the decision you are about to make. They do not replace any "
    "input you were given, and a note you cannot act on is one to leave "
    "alone rather than to guess at. `typed_operations`, when present, "
    "carries the resolved edit-spec clauses for this note. Their operation "
    "type and values are the declared request; do not choose an owner from "
    "keyword matches in the note. A value stated in frames or seconds is "
    "the requester's number: carry it in one of the fields its "
    "`carry_stated_numbers_in` names, never as a feel word."
)


def _stated_number_fields(row: dict, owner: str) -> dict:
    """Per typed value in frames or seconds, the owner's rung 7 fields.

    A plan row carries `{value, unit, stated_by}` per value; a direct row
    carries its units beside its params. Only a timing unit maps.
    """
    from library.tools.edit_operations import stated_number_fields

    params = row["params"]
    if row["op"] == "plan_change":
        units = {key: value["unit"]
                 for key, value in params["values"].items()}
    else:
        units = row.get("value_units", {})
    carried = {}
    for key, unit in units.items():
        fields = stated_number_fields(owner, unit)
        if fields:
            carried[key] = list(fields)
    return carried


def _decision_summary(decision: dict) -> dict:
    """The stamp, as the prompt block carries it.

    Present whether or not it did the routing: a step reading a note
    about its own decision is better off knowing WHICH of its decisions
    the captain was looking at, and that is what `decision_id` is.
    """
    if not decision or not decision.get("step"):
        return {"clip_decided_by": "",
                "clip_decision_absent": decision.get("reason", "")
                if decision else ""}
    return {
        "clip_decided_by": decision.get("step", ""),
        "clip_decision_id": decision.get("decision_id", ""),
        "clip_decision_basis": decision.get("basis", ""),
        "clip_decision_written_in": decision.get("locator", ""),
    }


def _span_summary(note: "RoutedNote") -> dict:
    """The note's own span, as the prompt block carries it.

    A marker the captain DRAGGED to a length is an interval, and until
    now the prompt was told only `at_timecode` - a point. The span is
    what an operation scoped to a region needs, and the note has carried
    it since it was collected.
    """
    span = note_span_seconds(note)
    if span is None:
        return {"at_region": ""}
    return {"at_region": f"{span[0]:.3f}..{span[1]:.3f}",
            "on_timeline": note.timeline or "(the master timeline)"}


def _target_summary(target: dict) -> dict:
    if target.get("kind") == TARGET_CLIP:
        clip = target.get("clip") or {}
        return {
            "attached_to": "clip",
            "clip": clip.get("name", ""),
            "clip_source_file": clip.get("source_file", ""),
            "clip_track": ", ".join(target.get("tracks") or []),
            "clip_timeline_frames": (
                f"{clip.get('timeline_start')}..{clip.get('timeline_end')}"
                if clip else ""),
            "clip_source_frames": (
                f"{clip.get('source_start')}..{clip.get('source_end')}"
                if clip else ""),
            "attachment_basis": target.get("basis", ""),
            "attachment_reason": target.get("reason", ""),
        }
    # A MOMENT note's context carries each clip's timeline span, not just
    # its name. The pull file records `timeline_start`/`timeline_end` for
    # every clip under the frame and this rendered names only, so the one
    # reader who could act on "which of these covers my region" was told
    # the least useful half. Still CONTEXT and still routes nothing -
    # `WITHDRAWN_ROUTERS['the_clip_under_the_playhead_decides']` is
    # unchanged.
    return {
        "attached_to": "moment",
        "clips_under": [
            f"{c.get('name')} ({c.get('track_type')}{c.get('track_index')}"
            f" {c.get('timeline_start')}..{c.get('timeline_end')})"
            for c in (target.get("clips_under") or [])
        ],
        "attachment_basis": target.get("basis", ""),
        "attachment_reason": target.get("reason", ""),
    }


def prompt_block(routed_notes, node_id: str = "") -> dict:
    """What `gather_step_inputs` hands a step that declares the input.

    A legend plus the notes, in the shape `MEASUREMENT_LEGEND` and
    a derived column's definition travels in: the legend is one
    sentence shared by fifteen steps, so
    a new input has to say what it is inside the data itself. When the
    current node is supplied, a multi-clause edit carries only the
    operations owned by that node; another owner must not act on them.
    """
    def operations_for(note):
        operations = note.evidence.get("edit_spec_ops", [])
        if node_id:
            return [operation for operation in operations
                    if operation["owner"] == node_id]
        return operations

    return {
        "legend": PROMPT_LEGEND,
        "note_count": len(routed_notes),
        "notes": [
            dict(
                note_id=n.note_id,
                typed=n.text,
                at_timecode=n.timecode,
                **_span_summary(n),
                collected_at=n.collected_at,
                timeline=n.timeline,
                routed_because=n.reason,
                typed_operations=operations_for(n),
                **_target_summary(n.target),
                **_decision_summary(n.decision),
            )
            for n in routed_notes
        ],
    }


# ── Nothing may silently drop a routed note ─────────────────────────

class UndeliverableNote(RuntimeError):
    """A note was routed to a step that cannot receive it."""


def assert_deliverable(node_id: str, manifest, routed_notes) -> list:
    """The notes for `node_id`, or raise if the step cannot take them.

    Called from `gather_step_inputs`.  A step in `STEP_DECISIONS` with
    `DELIVERY_PROMPT` that does not declare `STEP_INPUT_NAME` would
    assemble a context with the captain's note silently absent, and the
    run would look exactly like a run with no notes at all.  It fails
    instead, naming the note and the one line that fixes it.

    A `DELIVERY_REPORT` step is not a failure: it has no prompt at all,
    which is recorded in the table with its reason, and the note reaches
    the report either way.
    """
    mine = notes_for_step(routed_notes, node_id)
    if not mine:
        return []
    decision = BY_NODE_ID.get(node_id)
    if decision is None:
        raise UndeliverableNote(
            f"{len(mine)} note(s) are routed to {node_id!r}, which is not "
            f"in STEP_DECISIONS. Routing cannot produce a step that is "
            f"not in the table, so this is a corrupted routing record."
        )
    if decision.delivery != DELIVERY_PROMPT:
        return []
    declared = {inp.get("name") for inp in
                ((manifest or {}).get("interface") or {}).get("inputs") or []}
    if STEP_INPUT_NAME not in declared:
        preview = "; ".join(
            f"{n.timecode or '(unplaced)'} {n.text.splitlines()[-1]}"
            for n in mine[:3])
        raise UndeliverableNote(
            f"Step {node_id!r} is carrying {len(mine)} of the captain's "
            f"timeline note(s) and its manifest does not declare the "
            f"{STEP_INPUT_NAME!r} input, so they would reach the prompt "
            f"nowhere and the run would look like a run with no notes.\n"
            f"  {preview}\n"
            f"  Add to library/steps/<dir>/manifest.json, under "
            f"interface.inputs:\n"
            f'    {{"name": "{STEP_INPUT_NAME}", "type": "object", '
            f'"required": false, "description": "..."}}'
        )
    return mine


def undelivered(routed_notes) -> list:
    """Every note that reaches no step's prompt, with why.

    Ambiguous, unrouted, an unknown declared name, and routed-to-a-step-
    with-no-prompt.  All four are reported; none is a silent drop.
    """
    out = []
    for note in routed_notes:
        if note.outcome != OUTCOME_ROUTED:
            out.append((note, note.outcome, note.reason))
            continue
        for step in note.steps:
            decision = BY_NODE_ID.get(step)
            if decision and decision.delivery != DELIVERY_PROMPT:
                out.append((note, DELIVERY_REPORT, decision.delivery_note))
    return out


# ── The durable record, and what the captain reads ──────────────────

def routing_record(project_folder, routed_notes=None, timelines=None) -> dict:
    routed_notes = (route_project(project_folder, timelines=timelines)
                    if routed_notes is None
                    else routed_notes)
    by_step: dict = {}
    for note in routed_notes:
        if note.outcome == OUTCOME_ROUTED:
            for step in note.steps:
                by_step.setdefault(step, []).append(note.note_id)
    counts = {outcome: 0 for outcome in (
        OUTCOME_ROUTED, OUTCOME_AMBIGUOUS, OUTCOME_UNROUTED,
        OUTCOME_UNKNOWN_STEP)}
    for note in routed_notes:
        counts[note.outcome] = counts.get(note.outcome, 0) + 1
    return {
        "format": ROUTING_FORMAT,
        "routed_at": datetime.now(timezone.utc).isoformat(),
        "timelines": sorted(timelines or []),
        "note_count": len(routed_notes),
        "counts": counts,
        "by_step": by_step,
        "notes": [asdict(n) for n in routed_notes],
    }


def write_record(project_folder, routed_notes=None, timelines=None) -> dict:
    """Write the routing record and the report the captain reads.

    Both live beside the pull files in `<project>/marker_feedback/`.  The
    record is timestamped and never overwritten, for the same reason a
    pull file is not; the report is one file, regenerated, and says so.
    `timelines` scopes both to those timeline names - see `route_project`.
    """
    if routed_notes is None:
        routed_notes = route_project(project_folder, timelines=timelines)
    record = routing_record(project_folder, routed_notes,
                            timelines=timelines)
    layout = ProjectLayout(project_folder)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = layout.write_path(Area.MARKER_FEEDBACK,
                             f"routing.{stamp}{ROUTING_FILE_SUFFIX}")
    serial = 1
    while path.exists():
        serial += 1
        path = layout.write_path(
            Area.MARKER_FEEDBACK,
            f"routing.{stamp}-{serial}{ROUTING_FILE_SUFFIX}")
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    report = layout.write_path(Area.MARKER_FEEDBACK, REPORT_FILENAME)
    report.write_text(
        render_report(project_folder, routed_notes, timelines=timelines),
        encoding="utf-8")
    return {"record": str(path), "report": str(report), "payload": record}


def record_delivery(project_folder, node_id: str, note_ids) -> None:
    """Append what reached which step, on the run that delivered it.

    Append-only, for the reason `provenance` is: a delivery is a thing
    that happened, and a later run delivering the same note again does
    not unmake the record of the first.
    """
    if not note_ids:
        return
    layout = ProjectLayout(project_folder)
    path = layout.write_path(Area.MARKER_FEEDBACK, DELIVERY_LOG_FILENAME)
    line = json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "step": node_id,
        "notes": list(note_ids),
    }, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# A run that reaches a note is recorded. A run that reaches NONE of the
# notes it collected has to be recorded too, in the same place and by
# the same rule, or the note's own history says only "not delivered yet"
# where the truth is "this run read it and could not give it to anybody".
NON_DELIVERY_STEP = "(nobody)"


def record_non_delivery(project_folder, undelivered_notes) -> None:
    """Append the notes THIS RUN could give to no prompt, with why.

    Same append-only log as `record_delivery`, and for the same reason:
    a run that could not deliver a note is a thing that happened.

    Three markers were pulled on project 001 on 2026-08-28 and one
    reached a prompt. *"why are the subtitles so big?"* routes to 4.01
    `plan_subtitles`, which is deterministic with no `handoff.md`, so it
    is delivered as a `report` and reaches no model at all. *"why is
    this fully blurry..."* is AMBIGUOUS between `plan_transitions` and
    `plan_vfx` and therefore reaches neither. Both were reported in
    `ROUTED-NOTES.md` and in `manage_project.py notes`, and NOTHING in a
    run said so - the captain thinks they were heard.

    Auto-resolving an ambiguity is NOT done here and is not this
    module's to do: `WITHDRAWN_ROUTERS` records why every tie-break was
    refused, and a note routed to the wrong step is worse than one
    reported as ambiguous. This makes the drop visible; picking a step
    is the captain's call.
    """
    entries = list(undelivered_notes or [])
    if not entries:
        return
    layout = ProjectLayout(project_folder)
    path = layout.write_path(Area.MARKER_FEEDBACK, DELIVERY_LOG_FILENAME)
    line = json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "step": NON_DELIVERY_STEP,
        "notes": [note.note_id for note, _why, _detail in entries],
        "reached_nobody": [
            {"note_id": note.note_id, "outcome": why, "reason": detail}
            for note, why, detail in entries
        ],
    }, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def undelivered_summary_lines(routed_notes) -> list:
    """The run summary's block. Empty when every note reached a prompt."""
    left = undelivered(routed_notes)
    if not routed_notes:
        return []
    reached = len(routed_notes) - len(left)
    if not left:
        return [f"  Captain's notes: {reached} of {len(routed_notes)} "
                f"reached a prompt; none was dropped."]
    lines = [
        f"  Captain's notes: {reached} of {len(routed_notes)} reached a "
        f"prompt. {len(left)} reached NOBODY:"
    ]
    for note, why, detail in left:
        first = next((line for line in note.text.splitlines()
                      if line.strip()), "(no text)")
        lines.append(f"      {note.note_id} [{why}] \"{first[:60]}\"")
        lines.append(f"        {detail}")
    return lines


def deliveries(project_folder) -> list:
    layout = ProjectLayout(project_folder)
    path = layout.read_path(Area.MARKER_FEEDBACK, DELIVERY_LOG_FILENAME)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ── The report ──────────────────────────────────────────────────────

def _where(note: RoutedNote) -> str:
    target = note.target or {}
    if target.get("kind") == TARGET_CLIP:
        clip = target.get("clip") or {}
        if clip:
            tracks = ", ".join(target.get("tracks") or []) or "?"
            return (f"on clip **{clip.get('name', '?')}** ({tracks}), "
                    f"source frames {clip.get('source_start')}.."
                    f"{clip.get('source_end')}")
        return f"on a clip that could not be identified - {target.get('reason')}"
    under = target.get("clips_under") or []
    names = ", ".join(f"{c.get('name')} "
                      f"({c.get('track_type')}{c.get('track_index')})"
                      for c in under) or "nothing"
    return f"at a moment on the timeline, over: {names}"


def render_report(project_folder, routed_notes=None, timelines=None) -> str:
    """The markdown the captain reads.  Generated; never hand-edited."""
    if routed_notes is None:
        routed_notes = route_project(project_folder, timelines=timelines)
    delivered = {}
    reached_nobody = {}
    for entry in deliveries(project_folder):
        # A run that could give a note to nobody is recorded in the same
        # log and must not read as a delivery to a step called
        # "(nobody)".
        if entry.get("step") == NON_DELIVERY_STEP:
            for record in entry.get("reached_nobody") or []:
                reached_nobody.setdefault(record.get("note_id"), []).append(
                    f"{record.get('outcome')} at {entry.get('at')} - "
                    f"{record.get('reason')}")
            continue
        for note_id in entry.get("notes", []):
            delivered.setdefault(note_id, []).append(
                f"{entry.get('step')} at {entry.get('at')}")

    lines = [
        "# The captain's timeline notes, and where each one went",
        "",
        "Generated by `python3 -m library.tools.marker_routing write "
        f"--project {project_folder}`. Never hand-edit it: it is "
        "regenerated from the pull files in this folder.",
        "",
        f"{len(routed_notes)} collected note(s).",
        "",
    ]
    if timelines:
        lines += [
            f"Scoped to {len(timelines)} timeline(s); pulls from any other "
            f"timeline name are not in this report.",
            "",
        ]
    if not routed_notes:
        lines.append("No notes have been collected. Run "
                     "`python3 -m library.tools.marker_feedback pull "
                     f"--project {project_folder}` with the timeline open.")
        return "\n".join(lines) + "\n"

    lines += ["| note | typed on | routed to | basis | delivery |",
              "|---|---|---|---|---|"]
    for note in routed_notes:
        first = next((line for line in note.text.splitlines()
                      if line.strip()), "(no text)")
        if note.outcome == OUTCOME_ROUTED:
            decisions = [BY_NODE_ID[step] for step in note.steps]
            went = ", ".join(
                f"**{decision.number} {decision.node_id}**"
                for decision in decisions)
            how = ("prompt" if any(
                decision.delivery == DELIVERY_PROMPT
                for decision in decisions) else "report only")
        elif note.outcome == OUTCOME_AMBIGUOUS:
            went = "AMBIGUOUS: " + ", ".join(
                f"{BY_NODE_ID[s].number} {s}" for s in note.steps)
            how = "nowhere - reported"
        elif note.outcome == OUTCOME_UNKNOWN_STEP:
            went = "UNKNOWN STEP: " + ", ".join(note.unknown_names)
            how = "nowhere - reported"
        else:
            went = "UNROUTED"
            how = "nowhere - reported"
        lines.append(
            f"| {first[:60]} | {note.target.get('kind', '?')} "
            f"@ {note.timecode or '(unplaced)'} | {went} | "
            f"{note.basis or '-'} | {how} |")

    lines += ["", "## Every note in full", ""]
    for note in routed_notes:
        lines += [
            f"### `{note.note_id}`",
            "",
            f"- **Typed on**: a {note.target.get('kind')} - {_where(note)}",
            f"- **Marker kind**: `{note.source}` at "
            f"{note.timecode or '(unplaced)'} (frame {note.frame})",
            f"- **Collected**: {note.collected_at} off timeline "
            f"`{note.timeline}`",
        ]
        if note.outcome == OUTCOME_ROUTED:
            decisions = [BY_NODE_ID[step] for step in note.steps]
            lines += [
                "- **Routed to**: " + "; ".join(
                    f"{decision.number} `{decision.node_id}` - "
                    f"{decision.owns}" for decision in decisions),
                f"- **Why**: {note.reason}",
            ]
            prompt_decisions = [d for d in decisions
                                if d.delivery == DELIVERY_PROMPT]
            if prompt_decisions:
                lines.append(
                    f"- **Delivery**: reaches each prompt-capable step as "
                    f"`{STEP_INPUT_NAME}`. Re-run it with "
                    + " or ".join(
                        f"`manage_project.py run <project> --rerun "
                        f"{decision.node_id}`"
                        for decision in prompt_decisions) + ".")
            else:
                lines.append(
                    "- **Delivery**: none - " + "; ".join(
                        decision.delivery_note for decision in decisions))
        else:
            lines += [
                f"- **Routed to**: nothing ({note.outcome})",
                f"- **Why**: {note.reason}",
            ]
        if note.decision.get("step"):
            d = note.decision
            lines.append(
                f"- **That clip was produced by**: `{d['step']}` "
                f"- {d.get('decision_id') or '(unnamed decision)'} "
                f"({d.get('basis') or 'unstated basis'}, read from the "
                f"{d.get('source')})")
            routes = d.get("routes") or {}
            if routes:
                lines.append("  - Why: " + ", ".join(
                    f"`{value}`" for value in routes.values()))
        elif note.decision.get("reason"):
            lines.append(f"- **No decision stamp**: {note.decision['reason']}")
        for entry in note.decision_context:
            lines.append(
                f"- **Playing at that moment**: {entry.get('track')} "
                f"`{entry.get('label')}` - decided by `{entry.get('step')}` "
                f"({entry.get('decision_id')}). Context; it routes nothing.")
        if note.note_id in delivered:
            lines.append("- **Delivered**: "
                         + "; ".join(delivered[note.note_id]))
        if note.note_id in reached_nobody:
            lines.append("- **Reached nobody**: "
                         + "; ".join(reached_nobody[note.note_id]))
        for attachment in note.attachments:
            lines.append(
                f"- **Attached**: `{attachment.get('resolved_path') or attachment.get('path')}`"
                + ("" if attachment.get("exists") else "  (NOT ON DISK)"))
        lines += ["", "> " + "\n> ".join(note.text.splitlines()), ""]

    left = undelivered(routed_notes)
    lines += ["## Notes that reached no prompt", ""]
    if not left:
        lines.append("None: every collected note reached a step.")
    else:
        for note, why, detail in left:
            first = next((line for line in note.text.splitlines()
                          if line.strip()), "(no text)")
            lines.append(f"- `{note.note_id}` **{why}** - {first[:70]}")
            lines.append(f"  - {detail}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────

def _print_steps() -> None:
    print(f"{'number':<8}{'step':<26}{'delivery':<10}decision")
    for decision in STEP_DECISIONS:
        print(f"{decision.number:<8}{decision.node_id:<26}"
              f"{decision.delivery:<10}{decision.owns}")
    print()
    print(DECLARATION_RULE)


def _print_report(project_folder, routed_notes) -> None:
    print(f"{len(routed_notes)} collected note(s) in {project_folder}")
    for note in routed_notes:
        print()
        print(f"  {note.note_id}")
        print(f"    typed on : {note.target.get('kind')} "
              f"({note.target.get('basis')}) @ "
              f"{note.timecode or '(unplaced)'}")
        print(f"    {_where(note)}")
        for line in note.text.splitlines():
            print(f"    | {line}")
        if note.outcome == OUTCOME_ROUTED:
            owners = ", ".join(
                f"{BY_NODE_ID[step].number} {step} "
                f"({BY_NODE_ID[step].delivery})" for step in note.steps)
            print(f"    ROUTED   -> {owners} [{note.basis}]")
        else:
            print(f"    {note.outcome.upper()}")
        print(f"    why      : {note.reason}")
    left = undelivered(routed_notes)
    print()
    print(f"  {len(routed_notes) - len(left)} note(s) reach a prompt, "
          f"{len(left)} do not:")
    for note, why, detail in left:
        print(f"    {note.note_id}: {why} - {detail}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.marker_routing",
        description="Route the captain's collected timeline notes to the "
                    "steps that own the decisions they are about.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("steps", help="the steps a note can be routed to")
    p_report = sub.add_parser(
        "report", help="print the routing, writing nothing")
    p_report.add_argument("--project", required=True)
    p_report.add_argument(
        "--timeline", action="append", default=[],
        help="route only pulls from this timeline name (exact match, "
             "repeatable). Left out, every pull file ever written routes, "
             "including ones from timelines that no longer exist.")
    p_write = sub.add_parser(
        "write", help="write the routing record and ROUTED-NOTES.md")
    p_write.add_argument("--project", required=True)
    p_write.add_argument(
        "--timeline", action="append", default=[],
        help="scope the record and the report to these timeline names "
             "(exact match, repeatable).")

    args = parser.parse_args(argv)
    if args.command == "steps":
        _print_steps()
        return 0

    scoped = list(args.timeline or [])
    routed = route_project(args.project, timelines=scoped or None)
    if args.command == "report":
        _print_report(args.project, routed)
        return 0

    result = write_record(args.project, routed,
                          timelines=scoped or None)
    _print_report(args.project, routed)
    print()
    print(f"  -> {result['record']}")
    print(f"  -> {result['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
