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
Your job is to CHOOSE the track - from the local library, from the
project's own folder, or from outside either - ensure it fits the video's
mood and energy, and identify specific sections (splices) to use.

Music and speech are the two halves of the audio spine. Speech carries
content; music carries feeling. The right music elevates the narrative;
the wrong music undermines it.

## Task Prompt

Choose the music for this video, and record why it is right.

`music_candidates` lists every track held locally: the shared music
library (`PIPELINE_MUSIC_LIBRARY`, source `library`) and the project's own
`music/` folder (source `project`). Each entry carries its measured
`duration_seconds`, a `duration_ok` flag and, when that flag is false, a
`duration_note` saying why.

**You may choose from that list, or from outside it.** A track held
nowhere locally is a legitimate answer - name it with source `external`
and a `source_url`. Nothing is preferred by virtue of being on disk; the
list exists so the choice is made with knowledge of what is there, which
is the half that used to be missing.

### Workflow

1. **Read the creative direction first**, and specifically its
   `emotional_landscape` and `target_mood`. Note every register it says
   the piece must NOT be scored in. Those go in
   `direction_justification.forbidden_registers`, and you must say for
   each one why your track does not do it.
2. **Consider the candidates.** Record what you looked at and why you
   rejected it in `candidates_evaluated` - including anything ruled out on
   duration.
3. **Decide.** Take the best track, local or external.
4. **Identify splices** (source_in/source_out) for the sections you intend
   to use.
5. **Copy `audio_path` verbatim** from the candidate you chose. Do not
   retype or shorten it.

### Selection principles

1. **Mood must match or enhance** the creative direction's emotional
   landscape and target mood - and must not contradict it. A direction
   that says the piece never becomes triumphant is a direction that
   rejects triumphant music, however good the track is.
2. **Energy should mirror** the video's pacing and energy arc
3. **Not genre-locked** - genre serves the content, not the other way around
4. **BPM is critical** - needed for beatmatching transitions in Phase 4
5. **Never hard-start or hard-stop** - fades required
6. **Duration has to be plausible.** The track must be at least as long as
   the edit, because music is placed at 0 and run to the end of the
   timeline. It must also be no longer than
   `music_candidates.max_track_duration_seconds`: something an order of
   magnitude longer than the piece is a compilation, not a track. A
   3914-second file once scored a 55-second edit because nothing asked
   this question.
7. **Identify specific splices** - a 3-minute track is never used in full;
   pick the sections that fit particular moments
8. **Prefer royalty-free / no-copyright** tracks to avoid content claims

### If you go outside the library

- Include the mood/vibe (e.g., "chill", "cinematic", "sparse piano")
- Include "instrumental" or "beat" (we need music without vocals)
- Include "no copyright" or "royalty free" for safe usage
- Set `source` to `external` and give the `source_url`; the post-bridge
  fetches it and re-measures its real duration

### Splice guidance

- Identify at least one "intro/hook" splice (for the video opening)
- Identify at least one "background" splice (for under speech)
- Identify transition splices if the track has distinct energy shifts
- Splices should be clean cut points - on beat boundaries when possible

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
sonic and musical preferences as natural language. Read it for:

- Preferred music genres and sonic qualities
- Specific artists, tracks, or vibes to emulate
- Music energy and mood descriptions
- Any tracks or styles to avoid

Use the brief to shape which candidate you take and, if you go outside,
what you search for. The brief takes priority over all other genre
guidance.

If `brand_content.music_genre` is also present (a list of genre strings),
use it as a secondary search hint alongside the brief's guidance.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Mood alignment**: Music must complement the creative direction's
   emotional landscape - not contradict it
2. **Recorded reasoning**: `direction_justification` names the direction's
   target mood, the registers it forbids, and why this track avoids each
   one. A choice that contradicts the direction must be visible in what
   you wrote, not only in how it sounds
3. **Duration sanity**: the track is at least as long as the edit and no
   longer than `max_track_duration_seconds`
4. **Practical splicing**: Splices must have valid timestamps within the
   track, with clean entry/exit points
5. **Completeness**: At least one intro and one background splice identified
6. **BPM accuracy**: BPM must be documented for beatmatching downstream
7. **Intentional placement**: Every splice has a clear intended_use - no
   orphan splices without a purpose
8. **Copyright safety**: Preference for royalty-free/no-copyright tracks

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
| Reads | `music_candidates` (bridge: the local library and the project's music folder) |
| Reads | `style_specification` |
| Writes | `music_selection` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Provided track unsuitable | FLAG — human may need to provide different music |
| BPM detection fails | Estimate manually from listening, note as "estimated" |
