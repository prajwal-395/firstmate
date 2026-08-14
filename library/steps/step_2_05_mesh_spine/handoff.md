# Step 2.5: Mesh and Refine Audio Spine — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 2.5 |
| Name | Mesh and Refine Audio Spine |
| Determinism | **Nondeterministic** |
| Archetype | Creative Construction |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Step 2.3, Step 2.4 |

---

## System Context

You are an audio editor weaving speech and music into a single cohesive
audio spine for a shortform video. You have the timestamp-resolved speech
sequence (what to say and when), the music selections (what music is
available), and the creative direction (the vision).

This is where the two halves MESH — and where mismatches get resolved.
Speech and music were selected in parallel, both guided by the creative
direction. Now they must be woven into one coherent timeline plan.

### Timeline duration anchor

The selected music track's `duration_seconds` is your natural timeline
anchor. Read it from the `music_selection.tracks` in your context. Your
spine's `total_estimated_duration_seconds` should be informed by the
music duration - use intro, outro, transition_slot, and breather blocks
to fill the timeline so the video and music end together naturally. If
the speech content is shorter than the music, add non-speech blocks
(transition slots, intro, outro) rather than cutting the music short.
If the speech content is longer, you may trim speech to fit or accept
a longer video.

---

## Task Prompt

Given the speech sequence, music selections, and creative direction,
construct the complete audio spine.

### Key decisions to make:
1. **How does the video open?** (hook → music intro → speech start)
2. **Where do music-driven moments go?** (between speech blocks)
3. **How does the energy flow?** (build, sustain, resolve)
4. **Where are the breathing points / transition slots?**
5. **Do any speech segments need to be cut for mood coherence?**
6. **Do any music splices need to be swapped?**

### Block types to use:

| Type | Description | Music Behavior |
|------|-------------|---------------|
| `hook` | Opening attention-grabber (1-3s speech snippet) | prominent or background |
| `intro` | Music + visual moment before speech (no speech) | prominent |
| `speech` | Contiguous spoken A-roll audio | background (music under) |
| `transition_slot` | Non-speech moment for B-roll + music | prominent |
| `outro` | Closing section | fade_out |

### Music behavior values:
- `"prominent"` — music leads (no competing speech)
- `"background"` — music plays quietly under speech
- `"fade_in"` — transitioning from silent/background to prominent
- `"fade_out"` — transitioning from prominent to background/silent
- `"silent"` — no music (for dramatic effect, raw moments, emphasis)

### Structural rules:
- Spine MUST start with a "hook" block
- Hook is followed by an "intro" block (music + B-roll before speech)
- Speech blocks should not exceed ~8-10 seconds without a transition slot
- Spine MUST have a defined ending (outro or final speech block)
- Music and speech CAN overlap — music_behavior controls the relationship
- Every block must have music_behavior specified
- Assign a visual_note to each block (guidance for Phase 3, not binding)

### Duration invariant (CRITICAL):

The timeline duration of a speech block must EQUAL the source duration of
the audio it references, or speech is silently truncated - the viewer
hears an incomplete sentence.

You do not set that source range: the post-bridge takes it from the
speech_sequence passage named by `content.passage_ref`, whose timings come
from word-level alignment, and it overwrites your `duration_seconds` with
the passage's real duration. So your `duration_seconds` is a pacing
estimate, and the invariant holds by construction.

**If a speech block is too long for the target pacing**, adjust the total
video length or shorten other blocks (transition slots, intro, outro) to
compensate. You cannot trim a passage here by shortening
`duration_seconds` or by splitting it across blocks with hand-written
in/out points - trimming belongs to step 2.2, which chooses the passages.

### Transition slot guidance:
- Duration varies by intent - determined by the purpose of the transition
- No fixed minimum or maximum
- Examples: dramatic pause (brief), scene change (medium), musical
  buildup (longer)
- These are CRITICAL for pacing - a video that's all speech feels exhausting

### Intentional black beats (rare):

A non-speech block may declare a deliberate hold on black - a stretch
where no clip plays and the viewer sees a black frame.  This is a real
editorial tool (a breath before a reveal, a hard cut to silence) but it
is almost never the right choice.  Most "pauses" should use a B-roll
cutaway or a transition slot with visual content instead.

To declare one, set two keys on the block:

    "intentional_black_beat": true,
    "black_beat_reason": "hold on black before the tonal shift"

Rules:
- **Only on non-speech blocks** (intro, transition_slot, outro).  Speech
  blocks are never held on black - the viewer must see the speaker or
  a cutaway.
- **The reason is mandatory.**  A flag with no reason - or an empty one -
  is rejected outright.  A vague reason like "pause" clears the gate but
  fails review: say WHY the black serves the edit.
- **Maximum 0.5 seconds** (`MAX_DECLARED_BLACK_BEAT_SECONDS` in
  `library/tools/spine_contract.py`).  Longer holds are not a beat, they
  are a hole.  If you need more than half a second of visual silence,
  reconsider the structure.
- **This should be rare** - most videos have zero black beats.  Use it
  only when holding on black is genuinely better than showing any image.

An undeclared gap - where no clip covers a stretch of the timeline and
no block declares a beat - hard-fails compilation.  The declaration is
the only way to tell the pipeline "this is intentional."

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

### passage_ref (CRITICAL)

Every `hook` or `speech` block MUST include `content.passage_ref` which
links back to the speech_sequence passage it came from:
- For the hook block: `"passage_ref": "hook"`
- For body speech blocks: `"passage_ref": <position>` matching the
  passage's `position` field from the speech_sequence body_sequence

This linkage is how the post-bridge injects the block's clip_id, source
range, word_timestamps and execution-layer timing data. A `hook`/`speech`
block whose `passage_ref` is missing or names no passage FAILS the step -
it is not silently kept, because a block without word timings disables
beat-aligned cutting downstream (see `library/tools/spine_contract.py`).

---

## Evaluation Criteria

1. **Structural completeness**: Starts with hook, has speech, has ending
2. **Pacing variety**: Not a monotone monologue — transition slots and
   music moments create breathing room
3. **Energy arc coherence**: The spine follows the creative direction's
   energy arc
4. **Music-speech fit**: Music behavior is appropriate for each block type
5. **No content loss**: Every speech passage from body_sequence appears in
   exactly one speech block
6. **Duration plausibility**: Estimated total aligns with the music track duration

---

## Important Notes

- The visual_note field is guidance for Phase 3, NOT a binding assignment.
  It says "good place for B-roll" or "needs a talking head shot."
- Transition slots are where music carries energy and B-roll provides
  visual variety.
- The hook CAN be a snippet of a body passage — intentional shortform
  technique.
- music_behavior "background" means speech and music play simultaneously.
- If speech and music don't mesh well, propose cutting a speech segment
  or swapping a music splice — document the change and rationale.

### Precision tool: RMS energy contour (embedded)

To make data-driven pacing decisions, analyze the music track's energy:

```python
import librosa

y, sr = librosa.load("music_track.wav", sr=22050)
rms = librosa.feature.rms(y=y)[0]
rms_times = librosa.frames_to_time(range(len(rms)), sr=sr)
# Plot or analyze: high RMS = energetic (good for prominent music)
# Low RMS = quiet (good for background under speech)
```

### Precision tool: Beat tracking (embedded)

To align transition slot boundaries to musical beats:

```python
tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
beat_times = librosa.frames_to_time(beat_frames, sr=sr)
# beat_times = [0.5, 1.0, 1.5, ...] — use for transition slot boundaries
```

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `speech_sequence` (with timestamps from 2.3) |
| Reads | `music_selection` |
| Reads | `temporal_index` |
| Reads | `music_analysis` |
| Writes | `audio_spine` |
| Writes | `timed_spine` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Speech and music don't mesh | Revisit 2.4 (different music) or 2.2 (adjust speech) |
| Structure feels monotonous | Add more transition_slots, vary music behavior |
| Estimated duration diverges from music track length | Add/remove transition slots, intro, or outro to align with music |
