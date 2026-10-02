# `library.tools.marker_routing` - the history behind its contract

This is the module docstring of `library/tools/marker_routing.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Route a note the captain typed on the timeline to the step that owns it.

`marker_feedback.py` READS the captain's typed notes off a DaVinci Resolve
timeline and writes them durably to `<project>/marker_feedback/`.  Nothing
consumed them: a detailed note about a b-roll choice reached a file and
stopped there, and the step that made that choice never heard about it.

This module is the other half.  It answers two questions about every
collected note, separately, and never lets one answer stand in for the
other:

    WHAT IS IT ATTACHED TO - a specific clip, or a moment on the timeline
    WHICH STEP'S DECISION IS IT ABOUT

The first is a MEASUREMENT off the marker itself.  The second is a
ROUTING, and it is the one that can be wrong, so it is allowed to answer
"I do not know" and it is allowed to answer "more than one".


A clip note and a moment note are different things
--------------------------------------------------
A marker typed onto a CLIP is about that clip: the captain selected it,
put the playhead on it and typed.  A marker typed onto the TIMELINE is
about a moment - what is happening then, which may involve every clip
stacked under that frame and the cut on either side of it.

These are never flattened together.  A clip note carries `clip`; a
moment note carries `clips_under`, which is CONTEXT and is explicitly not
an attachment.  Reading "the clips at this frame" as "the clip this note
is about" would hand a note typed on a V2 cutaway to whatever happened to
be on V1 underneath it.

`marker_feedback.MarkerNote.attached_clip` records the placement directly
for anything read after this module existed.  A pull file written before
that has the placement RECOVERABLE rather than recorded, from the frame
arithmetic PR 276 established:

    timeline_frame = clip.timeline_start + (source_frame - clip.source_start)

Only a clip that both plays the source frame and lands on the note's own
timeline frame is a candidate, and the recovery is refused unless the
candidates are one clip - Resolve's linked audio and video items of one
placement agreeing on file, timeline range and source range count as the
one clip they are.  Zero candidates, or two real ones, is reported as
`unresolved`, never broken by picking the first.


Which step - declared, or by the words the captain used
-------------------------------------------------------
`STEP_DECISIONS` is the whole vocabulary of what a note can be routed to.
A step that is not in it cannot be routed to, and a note naming one is
refused BY NAME rather than sent to the nearest thing.

Two bases, in this order:

* `declared` - the note says which step, either as a line
  `step: select_broll` typed into the marker's Name or Notes field, or as
  a `route` record in its `customData` (a writer's channel; see
  `marker_payload`).  A declaration is authoritative and stops here.
* `vocabulary` - the note's own words name EXACTLY ONE step's decision.

* `stamped` - the words name nothing, and the ONE clip the note is
  typed on carries the decision that produced it, written into its
  `customData` at build time or looked up in the project's decision
  ledger (`library/tools/timeline_decisions.py`).  This is the producer
  side of the loop: where it answers, nothing is inferred at all.

and two non-answers, which are outcomes and not failures:

* `ambiguous` - the words name more than one step's decision.  Every
  candidate is reported.  Nothing breaks the tie.
* `unrouted` - the words name none.

THE STAMP RANKS BELOW THE WORDS, and this is the one ordering worth
arguing about.  A stamp says what PRODUCED the picture; the captain's
words say what the note is ABOUT.  Measured on their own three notes off
001's timeline: *"why is this fully blurry, is it the zoom blur applied
wrong?"* is typed on a V1 A-roll clip whose stamp is `speech_sequence`,
while its words are ambiguous between `plan_transitions` and `plan_vfx` -
and a blur that held for a whole clip is decided in one of those two.  A
stamp that outranked the words would have sent that note to the step that
chose the passage.  So the stamp answers only where the words answer
nothing, which on those three notes changes none of them.  That is the
point: it closes the UNROUTED case without touching the routed ones.

It is also not `WITHDRAWN_ROUTERS['the_clip_under_the_playhead_decides']`
coming back.  That one read the STACK at a frame, which on this pipeline
is always a V1 clip, a caption card and a music bed.  The stamp reads the
ONE clip the captain selected and typed on, and a MOMENT note never
reaches it - the decisions under a moment are recorded as
`decision_context` and route nothing.

This is a ROUTER, not a chooser, and the difference from the SFX word
list AGENTS.md section 10.5 deleted is the refusal rule.  That one scored
each candidate by substring hits and took the HIGHEST COUNT, so it always
produced an answer and the answer was frequently the library's first
entry.  Here there is no score, no ranking, no tie-break and no default:
two candidates is a reported ambiguity and zero is a reported miss.
`WITHDRAWN_ROUTERS` records the shapes that were considered and refused.

A note routed to the wrong step is worse than one reported as ambiguous,
so where the two readings of a word are both real - "zoom" is a
transition in `plan_transitions` and an effect in `plan_vfx` - both steps
declare it and a note using it comes out ambiguous.  That is the correct
output, not a gap to be closed.


Reaching the step
-----------------
`STEP_DECISIONS[...].delivery` says how, and there are two ways because
the pipeline has two kinds of step:

* `DELIVERY_PROMPT` - the step has a `handoff.md` and reads a prompt.  It
  declares `timeline_notes` in its manifest and `gather_step_inputs`
  hands it `prompt_block()` - the captain's words with a legend saying
  what they are.  The NOTES are per-run data and can only travel as
  data; the legend beside them is the same sentence for every step, so
  it is single-sourced here rather than copied into fifteen prompts.
  Not the handoff freeze, lifted 2026-09-09 - every one of the fifteen
  now carries a "Timeline Notes" section of its own.
* `DELIVERY_REPORT` - the step is deterministic and has no prompt at all.
  The note is still routed, still recorded and still reported; it is
  stated as not prompt-deliverable, with the reason, rather than being
  quietly dropped on the floor.  The primary consumer of a routed note is
  a human investigating it, and that consumer is served either way.

**Nothing may silently drop a routed note.**  `assert_deliverable` is
called from `gather_step_inputs`: a note routed to a step whose manifest
does not declare `timeline_notes` FAILS the run, naming the note and the
step, rather than being assembled into a context that does not carry it.

    python3 -m library.tools.marker_routing report --project <dir>
    python3 -m library.tools.marker_routing write  --project <dir>
    python3 -m library.tools.marker_routing steps

`tests/unit/resolve/test_marker_routing.py`.


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
- `tests/unit/resolve/test_marker_routing.py`, whose note fixtures are the three the captain really typed.
```
