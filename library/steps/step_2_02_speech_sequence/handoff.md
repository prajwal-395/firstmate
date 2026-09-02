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

You are a narrative editor constructing the spoken backbone of a shortform
video. You have access to the creative direction (your compass) and the
per-clip semantic analyses (your raw material).

Your job is SELECTION AND PLACEMENT — choose which speech passages to use,
in what order, and resolve each to precise source timestamps using the
temporal event indices. You are building a TIMESTAMP-RESOLVED speech
sequence ready for direct timeline placement.

---

## Task Prompt

Given the creative direction and semantic analysis documents, construct a
speech sequence for the video.

### Selection principles:
1. **Follow the compass**: Every selection should serve the creative
   direction's target mood and energy arc
2. **Prefer chronological source order** as the default sequence (maintains
   visual continuity — lighting, location, clothing)
3. **Break chronological order ONLY for**:
   - The hook (pulling a later moment to the front)
   - Deliberate narrative motivation (contrast, callback, punchline)
4. **Prioritize** passages from clips with higher interest_scores
5. **Prefer** passages tagged as "highlight" or "body" for the main sequence;
   use "hook"-tagged clips for the opening
6. **Never select a partial sentence** — passage boundaries must align to
   complete thoughts
7. **Adjacent passages must logically follow** — no non-sequiturs without
   narrative motivation

### What to produce:

**Hook segment**: The opening attention-grabber — a 1-3 second speech snippet
pulled from the most compelling moment. This CAN be a snippet of a passage
that also appears in the body (common shortform technique: tease a moment,
then play it in full context later). It may not BE that passage: a body
passage covering essentially the same source range as the hook is dropped
by the bridge, because the video would open with a line and replay it
verbatim moments later.

**Body sequence**: Ordered list of speech passages forming the narrative arc.
Each passage needs:
- clip_id (which clip it's from)
- text (exact verbatim words from the temporal index transcript)
- source_start / source_end (source timestamps from the temporal index)
- role ("opening" | "development" | "climax" | "resolution")
- flow_note (how this passage connects to the next)
- engagement (how strongly this passage holds a viewer, judged ONLY
  against the other passages in this sequence - rank the hook segment
  in the same ordering, and give it this field too):
  `{"rank": 1..N, 1 is the strongest, no ties; "composite": 0-100;
  "basis": "one sentence naming what makes it strong or weak"}`.
  If you genuinely cannot judge a passage, set `rank` and `composite`
  to null and say why in `basis`. An unjudged passage must read as
  unjudged, never as a low score.

The hook segment needs the same `clip_id`, `text`, `source_start` and
`source_end` fields.

### Timestamp resolution:
For each passage you select, look up the `transcripts_toon` data for that
clip_id and find the region containing the verbatim text. Copy the
region's `start` and `end` values into `source_start` and `source_end`.

**Never invent a timestamp.** `source_start`/`source_end` are a lookup
hint, not a guess: the bridge re-derives the real timings by aligning your
`text` against WhisperX word timings, and it FAILS THE STEP when your text
cannot be found in the transcript. A round-number range you made up
(`0.0`-`5.0`, `60.0`-`65.0`) will not survive - it will stop the pipeline.
If you cannot find a passage's verbatim text in the transcript, do not
select that passage.

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

Read `project_config.target_duration_seconds` from the input if available.
This value comes from the project's `project.json`.

- If a target is set, the total speech sequence MUST fit within it. Select
  only the strongest passages. If all candidate passages sum to more than
  the target, aggressively cut - keep only passages that directly serve the
  creative direction's narrative theme and key moments.
- Leave room for B-roll, transitions, intro and outro - how much speech
  vs. visual content the piece needs is a creative call that depends on
  the footage and the creative direction.
- Document in `excluded_passages` any passages cut to meet the duration
  target, with `reason_excluded: "cut to meet duration target of Xs"`.

If `project_config` is not available, include all speech passages that
serve the creative direction. Do not artificially cap the duration - the
downstream mesh_spine step will coordinate speech and music timing.

---

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

---

## Parameters

| Parameter | Value |
|-----------|-------|
| Target body passages | However many the duration limit above and the creative direction call for. There is no count to hit. |
| Target total duration| Should serve the creative direction; if project_config sets a target, respect it |
| Hook duration target | 1-3 seconds (per style spec) |
| Default sequence order | Chronological source_order |

---

## Important Notes

- This step produces TIMESTAMP-RESOLVED passages. Each passage includes
  source start/end timestamps looked up from the temporal event index.
  No separate timestamp resolution step is needed.
- The hook CAN be a snippet of a body passage — this is intentional and
  common in shortform. Use word-level timestamps to trim precisely. A body
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
| No coherent narrative possible | FAIL — footage may not support shortform video |
| No good hook found | FLAG — use highest-scoring passage, note the weakness |
| Insufficient speech content for target duration | FLAG — may need to accept shorter duration or add more footage |
