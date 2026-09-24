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
audio spine for a video. You have the timestamp-resolved speech
sequence (what to say and when), the music selections (what music is
available), and the creative direction (the vision).

This is where the two halves MESH — and where mismatches get resolved.
Speech and music were selected in parallel, both guided by the creative
direction. Now they must be woven into one coherent timeline plan.

### Timeline duration anchor

Speech and music are two halves of one backbone — neither anchors the other.
You must not let either input silently stretch the timeline past the target duration band.
The project's target duration zone is in `duration_zone` in your context:
`minimum_seconds`, `target_seconds`, `maximum_seconds`.

**You do not add the total up, and you are not asked to.** The post-bridge
recomputes `total_estimated_duration_seconds` from the enriched blocks
after it has replaced every speech block's estimate with the passage's
real measured duration, and `validate_spine_blocks`
(`library/tools/spine_contract.py`) REFUSES the step when that recomputed
total falls outside the zone. Declared bookend blocks are excluded from
the comparison, because a brand card is not a length you chose.

What is yours is the shape that lands inside the zone: which passages
open and close, how many transition slots the piece wants and how long
each one breathes. If the refusal fires, it comes back to you with the
real numbers and you cut or lengthen the non-speech blocks accordingly.
If the music track is longer than the target duration, do NOT fill the timeline to match the music length; the music will be trimmed or faded out downstream.
If the speech content is shorter than the target duration, you may add non-speech blocks (transition slots, intro, outro) to reach the target, but do NOT add dead air or silence at the head of the video. Silence at the head of a video is a gap, not spine.
If the speech content is longer than the target duration, you may need to adjust pacing or recommend speech cuts, but do not just blindly accept a longer video that exceeds the target band.

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
| `hook` | Opening attention-grabber (a speech passage) | prominent or background |
| `intro` | Music + visual moment before speech (no speech) | prominent |
| `speech` | Contiguous spoken A-roll audio | background (music under) |
| `music` | Music-led moment: the edit follows a span of a chosen track, not a spoken passage. Picture comes from B-roll, as on a transition slot. Optionally pin the span with `content.track` (one of the chosen tracks) plus `content.source_in`/`source_out` (seconds of that file); without one the conducted bed decides. | prominent |
| `picture` | Picture-led moment: the edit follows a clip span, not a spoken passage. Names its own picture with `content.clip_id` plus `content.source_start`/`source_end`. Carries no words. | background or prominent |
| `transition_slot` | Non-speech moment for B-roll + music | prominent |
| `outro` | Closing section | fade_out |

Note what is NOT in that table: `intro_card`, `outro_card` and `end_card`.
Those are CARDS - a logo animation, a branded end card - and which card a
video gets is a brand decision, not a per-run one. The brand template
declares them in `content.bookends` and the bridge places them around
your spine (`library/tools/bookends.py`); most templates declare none.
Any you write yourself is dropped, with a line in the step's log. An
`intro` block is still yours: it is a breath of music and B-roll, not a
card.

### Music behavior values:
- `"prominent"` — music leads (no competing speech)
- `"background"` — music plays quietly under speech
- `"fade_in"` — transitioning from silent/background to prominent
- `"fade_out"` — transitioning from prominent to background/silent
- `"silent"` — no music (for dramatic effect, raw moments, emphasis)

### Structural guidance:
- Spine structure is a creative decision. A hook followed by an intro is a
  common short-video pattern, but not the only one - cold opens, direct
  speech starts and other structures serve different content.
- Long unbroken speech blocks can feel monotonous; transition slots give
  breathing room, but how long speech runs before a break depends on the
  delivery and the content.
- Spine MUST have a defined ending (outro or final speech block)
- Music and speech CAN overlap — music_behavior controls the relationship
- Every block must have music_behavior specified
- Assign a visual_note to each block (guidance for Phase 3, not binding)

### Spines with little or no speech:

Speech is one way to lead a spine, not the only one. The spine is
audio-led: speech only, music only, or both - and where the picture is
the better backbone, picture-led blocks carry it.

- **Speech only** (today's shape): `hook`/`speech` blocks, every
  `content.passage_ref` resolving against the speech_sequence body.
- **Music only**: `music` blocks. The speech_sequence body is empty on
  such a run (step 2.02 always runs, and answers zero transcribed
  speech with an empty body) - no `passage_ref` will resolve, so write
  no `hook`/`speech` block.
- **Both, interleaved**: `speech` blocks alternating with `music`
  blocks - voice, then a music moment, then voice.
- **Both, layered**: `speech` blocks with music under them. Layering
  is the conducted `music_bed` plus each block's `music_behavior`
  (`background`/`prominent`), not two blocks sharing a second: blocks
  partition the timeline, so overlap is not representable and is not
  asked for.
- **Picture-led, no speech**: `picture` blocks (each naming its own
  clip span), optionally interleaved with `music` blocks for the
  audio-only stretches.

A `picture` block's duration follows its span the way a speech block's
follows its words: the post-bridge syncs `duration_seconds` to the
`content.source_start`/`source_end` you name. A `music` block keeps the
duration you give it, like a transition slot.

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

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
editorial vision as a rich markdown document. **This step sets every gap
length and every `music_behavior` in the piece**, and until the captain's
freeze on this file lifted it was the only planning step of the seven whose
prompt never mentioned the brief. Read it for:

- Pacing philosophy: how much breathing room the piece wants, and where
- Where music should lead and where it should get out of the way
- The shape of the opening - a cold open, a hook into a music beat, a
  straight start
- How the piece should end

Let the brief shape the structure, not just the words in it. If no creative
brief is provided, follow the creative direction's energy arc.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

### passage_ref (CRITICAL)

Every `hook` or `speech` block MUST include `content.passage_ref` which
links back to the speech_sequence passage it came from:
- `"passage_ref": <position>` matching that passage's 1-based order in
  the speech_sequence `body_sequence`. When the body is empty (or the
  sequence absent), no position resolves - so a speechless spine writes
  no `hook`/`speech` block at all.

There is no other way to name a passage. A block that OPENS the video is
addressed exactly like every other one - by the position of the passage
step 2.2 gave it. Which passage opens is your decision, made here; step
2.2 hands you an ordered sequence and names no opener.

This linkage is how the post-bridge injects the block's clip_id, source
range, word_timestamps and execution-layer timing data. A `hook`/`speech`
block whose `passage_ref` is missing or names no passage FAILS the step -
it is not silently kept, because a block without word timings disables
beat-aligned cutting downstream (see `library/tools/spine_contract.py`).

---

## Evaluation Criteria

1. **Structural completeness**: Has a defined ending, and at least one
   content block (`speech`, `music` or `picture`) - a spine of only
   gaps is no spine
2. **Pacing variety**: Not a monotone monologue — transition slots and
   music moments create breathing room
3. **Energy arc coherence**: The spine follows the creative direction's
   energy arc
4. **Music-speech fit**: Music behavior is appropriate for each block type
5. **No content loss**: When the body_sequence is non-empty, every
   speech passage from it appears in exactly one speech block. An
   empty body obligates nothing.
6. **Duration plausibility**: The shape you chose lands inside the target
   duration band once the real passage durations replace your estimates.
   The summation and the verdict are `validate_spine_blocks`'.

---

## Important Notes

- The visual_note field is guidance for Phase 3, NOT a binding assignment.
  It says "good place for B-roll" or "needs a talking head shot."
- Transition slots are where music carries energy and B-roll provides
  visual variety.
- The hook CAN be a snippet of a body passage — an intentional
  short-video technique.
- music_behavior "background" means speech and music play simultaneously.
- If speech and music don't mesh well, propose cutting a speech segment
  or swapping a music splice — document the change and rationale.

### The music is already measured, and the bar starts are in your context

Step 2.06 (`music_analysis`) measures the chosen track with librosa:
the per-second energy curve, the tempo, the beats and the downbeats.
The raw series do not reach you - this step's `context_fields`
withholds `music_analysis.tempo.beats`,
`music_analysis.tempo.downbeats` and
`music_analysis.energy_dynamics.energy_curve_1hz` by name (AGENTS.md
10.1, "No raw value list reaches a prompt"). What you get instead is
the addressed reading: the `beatgrid` view, one row per bar with its
bar number, its downbeat second and the grid's provenance
(detected or estimated).

So do not try to derive a beat grid of your own. You have no shell, no
audio file and no measurements to run one on, and a number you produced
by describing an analysis you did not run is an invented number. Where
a boundary should land on a beat, land it there: size a non-speech
block (`intro`, `transition_slot`, `outro`) so its boundary falls on a
bar start from the view, and name the bar in the block's `visual_note`
so step 4.02 - which places the cut - can hold it. (Speech-block
durations are the passages' real durations, not yours to size.)

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
