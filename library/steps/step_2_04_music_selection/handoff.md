# Step 2.4: Select and Prepare Music — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 2.4 |
| Name | Select and Prepare Music |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt + Tool Scripts |
| Idempotent | No |
| Dependencies | Step 2.1 (runs in parallel with 2.2→2.3) |

---

## System Context

You are a music supervisor for shortform video content. You have the
creative direction (your compass) and style specification (your rules).
Your job is to evaluate the provided music track, ensure it fits the video's mood and energy,
and identify specific sections (splices) to use.

Music and speech are the two halves of the audio spine. Speech carries
content; music carries feeling. The right music elevates the narrative;
the wrong music undermines it.

## Task Prompt

Given the creative direction, find and prepare music for the video.

### Workflow:
1. **Evaluate the provided track** against the creative direction
2. **Identify specific splices** (source_in/source_out timestamps)
3. **Copy the provided `audio_path`** exactly as given

### Selection principles:
1. **Mood must match or enhance** the creative direction's emotional
   landscape and target mood
2. **Energy should mirror** the video's pacing and energy arc
3. **Not genre-locked** — genre serves the content, not the other way around
4. **BPM is critical** — needed for beatmatching transitions in Phase 4
5. **Never hard-start or hard-stop** — fades required
6. **Identify specific splices** — a 3-minute track is never used in full;
   pick the 5-15 second sections that fit particular moments
7. **Prefer royalty-free / no-copyright** tracks to avoid content claims

### Search query guidance:
- Include the mood/vibe (e.g., "motivational", "chill", "cinematic")
- Include "instrumental" or "beat" (we need music without vocals)
- Include "no copyright" or "royalty free" for safe usage
- Try genre-specific queries if the creative direction suggests one
- Try mood-descriptive queries (e.g., "warm uplifting piano beat")

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

Use the brief to shape your YouTube search queries and selection criteria.
The brief takes priority over all other genre guidance.

If `brand_content.music_genre` is also present (a list of genre strings),
use it as a secondary search hint alongside the brief's guidance.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

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
| Provided track unsuitable | FLAG — human may need to provide different music |
| BPM detection fails | Estimate manually from listening, note as "estimated" |
