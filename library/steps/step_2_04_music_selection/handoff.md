# Step 2.4: Select and Prepare Music — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 2.4 |
| Name | Select and Prepare Music |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Step 2.1 (runs in parallel with 2.2→2.3) |

---

## System Context

You are a music supervisor for shortform video content. You have the
creative direction (your compass) and style specification (your rules).
Your job is to evaluate the provided track, verify it fits the video's mood and energy,
and identify specific sections (splices) to use.

Music and speech are the two halves of the audio spine. Speech carries
content; music carries feeling. The right music elevates the narrative;
the wrong music undermines it.

A track has already been downloaded and is available in the input context.
You do not need to search for or download a track yourself.

---

## Task Prompt

Given the creative direction, evaluate the provided track and prepare it for the video.

### Workflow:
1. **Evaluate the provided track** — review the track candidate provided in the context
   and verify it matches the mood, energy, and compatibility with the creative direction.
2. **Identify specific splices** — mark the sections of the track that
   will be used in the video.
3. **Copy the provided `audio_path`** — ensure the `audio_path` of the provided track
   is exactly copied to your output.

### Selection principles:
1. **Mood must match or enhance** the creative direction's emotional
   landscape and target mood
2. **Energy should mirror** the video's pacing and energy arc
3. **Not genre-locked** — genre serves the content, not the other way around
4. **BPM is critical** — needed for beatmatching transitions in Phase 4
5. **Never hard-start or hard-stop** — fades required
6. **Identify specific splices** — a 3-minute track is never used in full;
   pick the 5-15 second sections that fit particular moments

### Splice guidance:
- Identify at least one "intro/hook" splice (for the video opening)
- Identify at least one "background" splice (for under speech)
- Identify transition splices if the track has distinct energy shifts
- Splices should be clean cut points — on beat boundaries when possible

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
sonic and musical preferences as natural language. Read it for:

- Preferred music genres and sonic qualities
- Specific artists, tracks, or vibes to emulate
- Music energy and mood descriptions
- Any tracks or styles to avoid

Use the brief to shape your evaluation criteria.

---

## Output Format

```json
{
  "overall_mood": "string",
  "overall_energy": "low | medium | high | building",
  "tracks": [
    {
      "track_id": "track_01",
      "track_source": "string (YouTube URL)",
      "track_name": "string",
      "audio_path": "string (local path to provided WAV)",
      "genre": "string",
      "bpm": 120,
      "key": "string (if known)",
      "duration_seconds": 180.0,
      "splices": [
        {
          "splice_id": "track_01_splice_A",
          "source_in": 0.0,
          "source_out": 15.0,
          "duration_seconds": 15.0,
          "section_type": "intro | verse | chorus | bridge | drop | buildup | outro | ambient",
          "energy_level": "low | medium | high | peak",
          "intended_use": "string (e.g., 'hook intro music', 'background under speech')"
        }
      ],
      "selection_rationale": "string"
    }
  ]
}
```

---

## Evaluation Criteria

1. **Mood alignment**: Music must complement the creative direction's
   emotional landscape — not contradict it
2. **Practical splicing**: Splices must have valid timestamps within the
   track, with clean entry/exit points
3. **Completeness**: At least one intro and one background splice identified
4. **BPM accuracy**: BPM must be documented for beatmatching downstream
5. **Intentional placement**: Every splice has a clear intended_use — no
   orphan splices without a purpose
6. **Copyright safety**: Preference for royalty-free/no-copyright tracks

---

## Important Notes

- The music_selection output is a MENU of available splices, not the
  final placement plan. Step 2.5 (Mesh and Refine) determines exactly
  where each splice goes.
- For 30-60 second videos, one track with multiple splices is more common
  than multiple tracks.
- BPM is critical for Phase 4 beatmatching — "major cuts and transitions
  should land on musical beats" (style spec).
- The `candidates_evaluated` field documents the selection process for
  transparency — what was considered and why it was chosen or rejected.
- Downloaded audio files are stored locally for use in the render pipeline.

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `creative_direction` |
| Reads | `style_specification` |
| Writes | `music_selection` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| YouTube search returns no results | Try alternative search queries with different keywords |
| Download fails | Retry with different URL, or try alternative track |
| No suitable music found after multiple searches | FLAG — human may need to source music manually |
| BPM detection fails | Estimate manually from listening, note as "estimated" |
| Copyright concerns | Prefer tracks explicitly labeled as royalty-free |
