# Step 2.2: Construct Speech Sequence — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 2.2 |
| Name | Construct Speech Sequence |
| Determinism | **Nondeterministic** |
| Archetype | Creative Construction |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Step 2.1 |

---

## System Context

You are a narrative editor constructing the spoken backbone of a video.
You have access to the creative direction (your compass) and the
per-clip semantic analyses (your raw material).

Your job is SELECTION AND PLACEMENT — choose which speech passages to use
and in what order. The exact source timings are resolved for you: the
post-bridge aligns the verbatim text you give it against WhisperX word
timings and writes the real in and out points back.

---

## Task Prompt

Given the creative direction and semantic analysis documents, construct a
speech sequence for the video.

### Selection principles:
1. **Follow the compass**: Every selection should serve the creative
   direction's target mood and energy arc
2. **Prefer** passages tagged as "highlight" or "body" for the main sequence;
   use "hook"-tagged clips for the opening
3. **Never select a partial sentence** — passage boundaries must align to
   complete thoughts
4. **Adjacent passages must logically follow** — no non-sequiturs without
   narrative motivation

### What to produce:

**Body sequence**: Ordered list of speech passages forming the narrative arc.

The body may be EMPTY. When the footage's speech serves nothing the
creative direction asks for - or when the piece is better led by music
or by picture - return `"body_sequence": []` with the reason recorded
in `excluded_passages`. An empty body is a decision the spine plans
around, not a failure to select; the post-bridge accepts it without a
duration verdict. Do not pad it to satisfy a count: there is none.
Each passage needs:
- clip_id (which clip it's from)
- text (exact verbatim words from the temporal index transcript)
- source_start / source_end (roughly WHERE in that clip the text sits - a
  location hint, not a timing; see "Where the passage is" below)
- role (describe the structural function of this passage in your own words, e.g. "hook", "development", "punchline")
- flow_note (how this passage connects to the next)
- engagement (how strongly this passage holds a viewer, judged ONLY
  against the other passages in this sequence - rank the hook
  in the same ordering):
  `{"rank": 1..N, 1 is the strongest, no ties;
  "basis": "one sentence naming what makes it strong or weak"}`.
  If you genuinely cannot judge a passage, set `rank` to null
  and say why in `basis`. An unjudged passage must read as
  unjudged.

### Where the passage is (a hint, not a timing)

**The exact in and out points are not yours to compute, and no answer you
give here becomes a timeline range.** The post-bridge
(`library/steps/step_2_02_speech_sequence/post_bridge.py`,
`_align_words_to_text`) searches the clip's WhisperX word timings for your
`text`, tries every occurrence of its first word, and ranks the candidates
by how much of the passage aligned, then by the shortest span, then by the
smallest leading gap. Whatever it picks becomes `start_time`/`end_time`,
and those are what reach the spine. It FAILS THE STEP when your text
cannot be found in the transcript at all.

Your `source_start`/`source_end` are the LAST tie-break in that ranking -
a proximity hint used only when two equally complete anchors are otherwise
indistinguishable, which is what disambiguates a sentence the speaker says
twice. So:

- Give the approximate location from the `transcripts_toon` region you
  found the text in. Close is enough; the alignment does the rest.
- Do not hand-trim a range to the word, and do not treat the numbers as
  the passage's duration - they are not read as one.
- **Never invent one.** A round-number range you made up (`0.0`-`5.0`,
  `60.0`-`65.0`) points the tie-break at the wrong occurrence, which is how
  a mis-anchor gets past a perfectly valid-looking alignment.
- If you cannot find a passage's verbatim text in the transcript, do not
  select that passage.

The `text` is the part that has to be exact. The numbers beside it only
have to be roughly right.

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows. The available fields are `clip_id`, `start`, `end`, and `text` for transcripts, and `clip_id`, `topics` for topics.

**Excluded passages**: Document what was considered and why it was cut —
for transparency and potential revision.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
editorial vision as a rich markdown document. Read it before selecting
passages. The brief may describe:

- The narrative angle or story arc to pursue
- Which moments or themes to prioritize
- Tone and pacing preferences for the monologue

Let the brief guide your selection and ordering decisions. If no creative
brief is provided, rely on the creative direction output from Step 2.1.

## Duration Limit

**The project declares a target duration zone, and the sequence has to fit
inside it. You are not the one who adds it up.** The post-bridge sums the
ALIGNED durations - the real ones, off the word timings, not the hints you
gave - and `refuse_out_of_zone_sequence`
(`library/steps/step_2_02_speech_sequence/post_bridge.py`) REFUSES the step
when the total falls outside the declared zone, handing you the numbers and
the zone so you can cut. Any arithmetic you do here is on estimates the
alignment is about to replace, so do not present a total as a fact.

What that leaves you is the judgement the refusal cannot make:

- **Which passages go, when something has to.** Keep only what serves the
  creative direction's narrative theme and key moments. Cutting the weakest
  passage and cutting the shortest one are different decisions and only one
  of them is editorial.
- **Leaving room for B-roll, transitions, intro and outro** - how much of
  the piece is speech versus visual is a creative call that depends on the
  footage and the creative direction, and the zone measures speech alone.
- **Documenting the cut** in `excluded_passages`, with
  `reason_excluded: "cut to meet duration target of Xs"`.

**A run that declares no target is UNCHECKED, not unbounded.** With no
`target_duration_seconds` the post-bridge measures nothing - judging against
an invented minute is the defect `library/tools/duration_targets.py`
removed. Include the passages that serve the creative direction and let step
2.5 coordinate speech and music timing.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Narrative coherence**: Reading the text fields in sequence should form
   a coherent monologue — not a random assemblage of quotes
2. **Hook quality**: The hook must be genuinely attention-grabbing. Test:
   "Would this make a scrolling viewer stop?"
3. **Arc completeness**: The body should have a beginning, middle, and end.
   Not just a series of unrelated statements.
4. **Faithfulness**: All text must be verbatim from the semantic analysis
   transcripts — no paraphrasing, no invented words
5. **Exclusion transparency**: Excluded passages must have genuine reasons,
   not filler explanations

With an empty body, criteria 1-4 are satisfied vacuously - judge the
exclusion instead: does `excluded_passages` say, in genuine reasons,
why no speech serves this piece.

---

## Parameters

| Parameter | Value |
|-----------|-------|
| Target body passages | However many the duration limit above and the creative direction call for. There is no count to hit. |
| Target total duration| The project's declared zone, checked on the aligned durations by `refuse_out_of_zone_sequence`. Unchecked when the project declares none. |

---

## Important Notes

- This step produces passages that BECOME timestamp-resolved: you give the
  verbatim text and roughly where it sits, and the post-bridge resolves the
  real timings before anything downstream reads them. There is no separate
  timestamp resolution step.
- The hook CAN be a snippet of a body passage — this is intentional and
  common in short video. Use word-level timestamps to trim precisely. A body
  passage that is the whole hook rather than a longer version of it gets
  dropped.
- Two body passages from the same clip must not claim overlapping source
  ranges: the overlapping audio would play twice across the cut. The
  bridge re-anchors such a passage past the previous one, or fails it.
- The flow_note for the LAST passage should describe how the video ends.
- Most passages will have role "development" — that's fine.
- All `text` values must match the temporal index transcript verbatim.

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `clip_catalog` |
| Reads | `semantic_analysis_documents` |
| Reads | `temporal_event_indices` |
| Reads | `creative_direction` |
| Reads | `style_specification` |
| Writes | `speech_sequence` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| No coherent narrative possible | FAIL — footage may not support a short video |
| No good hook found | FLAG — use highest-scoring passage, note the weakness |
| Insufficient speech content for target duration | FLAG — may need to accept shorter duration or add more footage |
