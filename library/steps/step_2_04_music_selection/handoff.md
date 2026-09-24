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

You are a music supervisor for video content. You have the
creative direction (your compass) and style specification (your rules).
Your job is to CHOOSE the track - from the local library, from the
project's own folder, or from outside either - ensure it fits the video's
mood and energy, and identify specific sections (splices) to use.

`music_candidates.target_duration_seconds` is the length the choice
serves: where the project's video preferences declare a soft
`target_length_seconds` it is that number, otherwise the project's own
`target_duration_seconds`, otherwise the 60 s default. It is a SOFT
target - a longer or shorter video with defensible quality is allowed -
so it guides the choice and never refuses one.

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

### What was measured about each candidate

Every candidate was opened and measured. These say what each number IS and
what it is in; **none of them says what to conclude** - no target level, no
target tempo and no preferred key is declared anywhere in this pipeline,
and none is supplied here.

Loudness and dynamics:

- `integrated_lufs`: BS.1770 integrated loudness of the whole track, in
  LUFS (ffmpeg loudnorm). Lower is quieter.
- `loudness_range_lu`: BS.1770 loudness range (LRA) of the whole track, in
  LU. It is gated: passages below its own relative threshold are excluded.
- `true_peak_dbtp`: True peak of the whole track, in dBTP. `0.0` is full
  scale.
- `rms_spread_db`: p95 minus p5 of per-second RMS windows across the whole
  track, in dB. Ungated, so unlike `loudness_range_lu` it counts
  near-silent passages.
- `window_spread_db`: The same p95-minus-p5 read, over the played window
  only.
- `window_seconds`: How long the played window is, in seconds.
- `speech_band_ratio_db`: RMS in the 300-3400 Hz speech band minus
  full-band RMS, in dB. How much of the track's energy sits in the same
  band as a voice.

Whether it was measured at all:

- `measured`: Whether the file was opened and measured. **`false` means the
  numbers above are ABSENT, never that they are zero.**
- `measurement_note`: Why a candidate was not measured, when `measured` is
  false.

Rhythm:

- `tempo_bpm`: Detected tempo in beats per minute, from the same beat
  tracker step 2.06 runs (beat_this where installed, madmom RNN+DBN next,
  librosa otherwise). `None` when no usable grid was found.
- `tempo_method`: Which tracker answered - `beat-this-final0`,
  `madmom-rnn-dbn` or `librosa-beat-track`. The last two disagree by up
  to a few BPM, and librosa doubles or halves the true tempo on some
  tracks.
- `tempo_beat_count`: How many beats the tracker found across the whole
  track. Below eight there is no grid to snap to, only noise.
- `tempo_downbeat_count`: How many bar starts were found. A cut on a
  downbeat reads as intentional where a cut on any beat can read as busy.
- `tempo_downbeat_source`: Whether those bar starts were `detected` by
  the tracker or `estimated` as every 4th detected beat. An estimated
  grid can sit a whole beat off the bar - a cut "on the downbeat" of
  one may land on beat 4 of the music.
- `tempo_stable`: Whether the instantaneous tempo holds steady across the
  track. A drifting tempo has one BPM number and no single grid.
- `tempo_note`: Why there is no usable tempo, when `tempo_bpm` is `None`
  - and, when the downbeats are estimated, that warning instead.

Harmony:

- `musical_key`: Detected key as a label such as `C major`, from the same
  key extractor step 2.06 runs (essentia). `None` where essentia is not
  installed - the note says so.
- `key_method`: Which extractor answered.
- `key_strength`: The extractor's own confidence, 0 to 1. What to conclude
  from it is your call.
- `key_note`: Why there is no key, when `musical_key` is `None`.

Song structure:

- `song_structure`: The labelled spans of the track - `intro`, `verse`,
  `chorus`, `bridge`, `outro`, `build` - each with `start` and `end` in
  seconds OF THE TRACK, from the same segmenter step 2.06 runs. Every span
  is carried whole: there is no played window yet, so a label past the
  edit's length is still a place the bed could start, and dropping one
  would be deciding what you get to hear.
- `song_structure_note`: Why there are no labels, when the list is empty.
- What a label IS: span boundaries come from a self-similarity novelty
  curve - where the track audibly changes - and the TYPE is an
  energy-profile guess on top of them: a loud span reads as `chorus`, a
  quiet opening as `intro`. A verse that hits harder than its chorus will
  wear the wrong name. Read the boundaries as measured and the names as
  advisory; what to conclude from either is your call.
- No label is ranked, preferred or defaulted anywhere in this pipeline.
  Which span the bed starts on is your decision (`section` below), and
  nothing here says which one is best.

**Two things are measured and deliberately do NOT reach you**: the
per-second envelope curve and the full beat/downbeat arrays. Raw value
lists do not go in a prompt (AGENTS.md 10.1); the scalars above are the
reading of them, and the section shape you get in the second pass below is
the reading of the envelope.

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
   duration, which the row's own `duration_ok`/`duration_note` already
   decided for you.
3. **Decide.** Take the best track, local or external.
4. **Identify splices** (source_in/source_out) for the sections you intend
   to use, and **shortlist the sections you are weighing**
   (`section_shortlist`) so their shape is measured before you commit.
5. **Say which second the bed starts on** (`section`), when the track is
   longer than the edit.
6. **Copy `audio_path` verbatim** from the candidate you chose. Do not
   retype or shorten it.

### Selection principles

1. **Mood must match or enhance** the creative direction's emotional
   landscape and target mood - and must not contradict it. A direction
   that says the piece never becomes triumphant is a direction that
   rejects triumphant music, however good the track is.
2. **Energy should mirror** the video's pacing and energy arc
3. **Not genre-locked** - genre serves the content, not the other way around
4. **BPM is critical** - needed for beatmatching transitions in Phase 4
5. **Entry and exit matter** - consider how the track starts and stops in
   context: fades, hard starts, or cuts to silence are all tools
6. **Duration is already judged - read the verdict, do not re-derive it.**
   The bridge measured every candidate and put the answer on the row:
   `duration_ok` is the flag and `duration_note` says why when it is false.
   A track must be at least as long as the edit (music is placed at 0 and
   run to the end of the timeline) and no longer than
   `music_candidates.max_track_duration_seconds` - something an order of
   magnitude longer than the piece is a compilation, not a track; a
   3914-second file once scored a 55-second edit because nothing asked.
   `validate_selection` (`library/tools/music_selection_contract.py`)
   applies both bounds again to whatever you choose and REJECTS the step
   on either, and it re-measures the file rather than trusting a stated
   number. So do not recompute the comparison and do not argue with a
   `duration_ok: false` row: either take a candidate the flag passed, or
   name an external track and say why.
7. **Identify specific splices** - a 3-minute track is never used in full;
   pick the sections that fit particular moments

### If you go outside the library

- Include the mood/vibe (e.g., "chill", "cinematic", "sparse piano")
- Consider whether vocal or instrumental better serves the content
- Set `source` to `external` and give the `source_url`; the post-bridge
  fetches it and re-measures its real duration

### Splice guidance

- Identify the sections of the track you intend to use
- A single continuous section is as valid as multiple splices
- Splices should be clean cut points - on beat boundaries when possible
- `splices` are in SOURCE time and name no timeline position. Two disjoint
  pieces of one track are two entries, and that is expected. Step 2.5
  decides which piece plays where (`library/tools/music_bed.py`).
- The bed is not one continuous stretch of one track unless you decide it
  is. `tracks` names EVERY additional track you want the bed to be able to
  use beyond the primary; the primary is the one at the top level and is
  the one whose tempo is analysed. Name only tracks you really intend
  pieces of.

### Which SECOND of the track the bed starts on

**This is your decision, and nothing in the pipeline picks one.** There is
no best-section rule - a "pick the flattest window" or "pick the loudest"
rule would be a creative value hardcoded into the engine
(`library/tools/music_section.py` records why there must not be one).

`section` is `{source_in, why}`: `source_in` is the second OF THE TRACK the
bed starts at - the bed is placed once and runs under the whole video from
there - and `why` says what in the measurements decided it. It is REQUIRED
when the track is longer than the edit.

**Omitting `section` plays from the head of the file, and that reads as the
ABSENCE of a decision, not as a choice of the opening.** A track whose first
minute is an unresolved intro and whose second minute is the settled body is
two completely different beds depending on where it starts.

`resolve_section` REFUSES a section that does not fit rather than sliding it
back, because moving the start is choosing which part plays. The beat grid
moves with your offset; you do not adjust for it. `song_structure` above
is the measured input to this decision: where the labelled spans sit, in
the same track seconds `source_in` is named in.

### The second pass: you are asked again with the shape in front of you

Only the mean level and spread of each section have been measured when you
first answer. **`section_shortlist` is the list of sections you are
SERIOUSLY CONSIDERING** - `{track (optional), source_in, source_out, why}`,
as many as you are weighing, across as many tracks as you are weighing.

Naming them has their SHAPE measured and you are asked once more with those
numbers in front of you before the choice stands. **Name none and no shape
is measured**: your first answer stands as given.

When that second pass arrives, `section_measurements` carries one row per
shortlisted span:

- `envelope_dbfs`: **the SHAPE of the span** - its level averaged into
  twelve equal buckets, in time order, in dBFS. This is the measurement
  that exists only for seconds 0 to the length of the edit on the first
  pass, which is why you are being asked again: a section that rises across
  the minute and one that falls across it have the same mean and the same
  spread.
- `mean_dbfs`: the power mean of the span - the same statistic
  `track_sections` reports, repeated here so the shape and the level are
  read together.
- `spread_db`: how far the loud and quiet parts of the span are apart.
- `measured`: `false` with a stated note means this span could not be
  measured. **It never means the span is silent.**

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


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Mood alignment**: Music must complement the creative direction's
   emotional landscape - not contradict it
2. **Recorded reasoning**: `direction_justification` names the direction's
   target mood, the registers it forbids, and why this track avoids each
   one. A choice that contradicts the direction must be visible in what
   you wrote, not only in how it sounds
3. **Duration sanity**: the chosen track's `duration_ok` is true. The
   verdict is `validate_selection`'s, measured off the file, not a
   comparison you were asked to perform
4. **Practical splicing**: Splices must have valid timestamps within the
   track, with clean entry/exit points
5. **Completeness**: Selected splices cover the intended use in the video
6. **BPM accuracy**: BPM must be documented for beatmatching downstream
7. **Intentional placement**: Every splice has a clear intended_use - no
   orphan splices without a purpose

---

## Important Notes

- The music_selection output is a MENU of available splices, not the
  final placement plan. Step 2.5 (Mesh and Refine) determines exactly
  where each splice goes.
- For videos around a minute, one track with multiple splices is more
  common than multiple tracks. Scale that expectation with the
  `target_duration_seconds` above rather than with any fixed length.
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
