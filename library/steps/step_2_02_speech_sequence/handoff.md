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
video (30-60 seconds). You have access to the creative direction (your
compass) and the per-clip semantic analyses (your raw material).

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
then play it in full context later).

**Body sequence**: Ordered list of speech passages forming the narrative arc.
Each passage needs:
- clip_id (which clip it's from)
- text (exact verbatim words from the temporal index transcript)
- start / end (source timestamps resolved from temporal index)
- role ("opening" | "development" | "climax" | "resolution")
- flow_note (how this passage connects to the next)

### Timestamp resolution:
For each passage you select, look up the `transcripts_toon` data for that
clip_id and find the region containing the verbatim text. Use the
region's `start` and `end` values directly.

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows. The available fields are `clip_id`, `start`, `end`, and `text` for transcripts, and `clip_id`, `topics` for topics.

**Excluded passages**: Document what was considered and why it was cut —
for transparency and potential revision.

---

## Output Format

```json
{
  "hook_segment": {
    "clip_id": "string",
    "text": "string (the hook text)",
    "start": 0.0,
    "end": 0.0,
    "word_timestamps": [{"word": "string", "start": 0.0, "end": 0.0}],
    "rationale": "string (why this is the hook)"
  },
  "body_sequence": [
    {
      "position": 1,
      "clip_id": "string",
      "text": "string (exact spoken words)",
      "start": 0.0,
      "end": 0.0,
      "duration_seconds": 0.0,
      "role": "opening | development | climax | resolution",
      "flow_note": "string"
    }
  ],
  "excluded_passages": [
    {
      "clip_id": "string",
      "text": "string (first 50 chars)",
      "reason_excluded": "string"
    }
  ]
}
```

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
| Target body passages | 10-15 (strictly select exactly 10-15 of the strongest passages) |
| Target total duration| MUST be between 30 and 60 seconds (sum of passage durations) |
| Hook duration target | 1-3 seconds (per style spec) |
| Default sequence order | Chronological source_order |

---

## Important Notes

- This step produces TIMESTAMP-RESOLVED passages. Each passage includes
  source start/end timestamps looked up from the temporal event index.
  No separate timestamp resolution step is needed.
- The hook CAN be a snippet of a body passage — this is intentional and
  common in shortform. Use word-level timestamps to trim precisely.
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
| Insufficient speech content for 30s video | FLAG — may need to accept shorter duration or add more footage |
