# `library.tools.direction_contradiction` - the history behind its contract

This is the module docstring of `library/tools/direction_contradiction.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
direction_contradiction.py - a step says its measurements disagree with
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
DATA beside the context.  That route is REUSE: the same words go to
every flagging step, and N copies in N `handoff.md` files is N things
to keep equal.  It is not the captain's handoff freeze, lifted
2026-09-09.

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


**The inventory this marking comes from, and what it judged.**
Prose-as-evidence flows found 2026-09-07 by joining every step manifest's
`context_fields` against the producing step's output schema (calibrated:
the sweep was checked against 4.02's `narrative_verdict` prose and
`direction_justification.why_not_forbidden`, both known present, before
any absence was believed).  The line drawn: whole-object prose keyed by
its authoring step (`creative_direction`, `rough_cut_review`,
`speech_sequence`) is SELF-MARKING - the key names the source.  Prose
subfields inside an object this file classifies as a MEASUREMENT are
not, and those are what `VISION_PROSE_PATHS` marks:

* MARKED HERE - vision prose inside `semantic_analysis_documents` /
  `semantic_analysis`, cited as measurement by 9 flagging steps
  (2.02, 2.05, 3.02, 4.02, 4.03, 4.04 and the rest of
  `EVIDENCE_SOURCES`).  A downstream DECISION (contradicted vs not)
  rested on unattributed text: prose-vs-prose read as CONTRADICTED.
* ALREADY MARKED - `cut_decisions` verdict+note reaches 4.02 as
  `narrative_verdict` / `verdict_note`, and step 4.02's `handoff.md`
  states in its own prose that a verdict is the review's JUDGEMENT
  rather than a measurement; the B-roll `description` column is
  vision prose travelling beside measured framing/stability/tags, and
  4.02's `outgoing/incoming_footage` deliberately carries measurements
  only.  Pinned by test, not rebuilt.
* SELF-MARKING, RECORDED - `creative_direction` (all eight fields are
  model prose by construction; `DIRECTION_KEYS` + `direction_value`
  raising on anything else is the source record), `rough_cut_review`
  whole-object to 4.02/4.03/4.04 (the key is the source), 2.02's
  `body_sequence` ordering (a judgement by construction; the spine
  contract already marks its time fields as lookup hints, AGENTS.md 6).
* CORRECTLY EXCLUDED - `music_selection.direction_justification`:
  2.05 explicitly DROPs `why_not_forbidden` with a `-` path. The drop
  IS the marking. Pinned by test. (3.03's old
  `-audio_spine.music_selection.*` drops went with the nested copy
  itself when the audit trail moved to its own file - captain's
  ruling, 2026-09-16; `library/tools/music_audit_trail.py`.)
* GENUINELY JUST PROSE - `topics_toon` / `transcripts_toon`
  (2.02's own prompt tables, consumed only by its own prompt),
  the derived-column definitions each planning handoff now carries in
  its own prose, run-summary lines.  No decision rests on them as
  evidence.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/direction_contradiction.py`. On 001's 29 Aug run "emotion" and "energy" appear ZERO times in the semantic documents and FOUR times each in the `creative_direction` block at stations 2 and 3: step 2.01 reads the footage once and every creative step after it inherits that reading whole, with no way to say what it measured disagrees. Prosody (#417) is the first deterministic measurement that CAN disagree with an inherited affect reading.
- **Captain's ruling, 2026-09-01: a step MAY FLAG and MAY NOT ACT.** Nothing reads a flag's content, nothing gates on one, and the field is SPLIT OUT of the answer before validation - so the output a flagging step produces is byte-for-byte the one it would have produced silently. Escalation to the captain happens OUTSIDE the pipeline.
- **It is `undetermined.py`'s twin carrying different cargo** - same `take`/`record`/`summary_lines` surface, same collector, same route into the prompt as DATA beside the context, and for the same reason: one instruction asked of many steps is single-sourced here rather than copied into each prompt. Do not build a second mechanism.
- **FOUR readings, not the sibling's three.** A gap is named by naming it; a CONTRADICTION is a claim ABOUT a measurement, and a claim with no measurement is a model politely disagreeing with its brief. `contradicted` needs an entry naming a `direction_field` in `DIRECTION_KEYS`, a `measurement`, and a `measured_in` the step was really routed. Everything else is `unevidenced` - kept verbatim, reported as itself, and NEVER counted as a contradiction. `nothing_contradicted` (`[]`) and `not_declared` (key absent) are the other two, and they are not each other.
- **Every step that reaches a model except the one that authors the direction - nine of them.** `color_grade` joined on 2026-09-03 by DERIVATION alone when 5.01 stopped being deterministic. A step needs a prompt to say it in, an inherited direction claim and a routed measurement. The model-reaching half is borrowed from `undetermined.DECLARING_STEPS`; the routed half is DERIVED from `dag.json`, so a new edge cannot leave it stale, and `render_motion_graphics` joined by that derivation alone when 4.06 stopped being deterministic. `creative_cohesion` declares `creative_direction` and is deterministic, so it has nothing to say it in. `validate` reaches a model and holds measurements but is handed no inherited direction, so it is out on the direction half - recorded in `CONSIDERED_AND_EXCLUDED` rather than left as a derivation side-effect.
- **`MEASURED_OUTPUTS` and `DECLINED_OUTPUTS` must together account for every output of every deterministic step**, and an unaccounted one raises at import - a new deterministic output says which side it is on before it can go quiet.
- **Its collector is the sibling's, and so are the sibling's two rules**: one flag per model ATTEMPT, numbered, with `final_by_step` the per-STEP reading the summary prints; and `state["direction_contradictions"]` MERGED rather than replaced, carried rows marked `from_a_previous_run`. The two channels print into the same run summary, so they must count on the same basis.
- **`Flag` and `Declaration` are constructed POSITIONALLY, so a new field goes LAST.** Added above `entries`, it takes the entries and the real entries land in the field after it - no error, just wrong rows, until something compares them.
- `tests/test_direction_contradiction.py`, `tests/test_undetermined_declaration.py`.
```
