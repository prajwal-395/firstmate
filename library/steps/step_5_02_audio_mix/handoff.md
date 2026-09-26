# Step 5.02 - The mix

You are deciding how far above the music the voice sits in this video.

Until 2026-09-16 this step did not ask anybody. Five numbers lived in the
engine - the bed played 18 dB down under speech and 6 dB down when it led -
and every project got them whatever its music or its voice measured. The
captain removed them:

> "these all stand as things that should be covered by creative reasoning by
> the LLM to see if music is sitting too loud, too soft, or just right. there
> is no hard coded number that hits this. perhaps a formula or some kind of
> audio anaysis that allows that judgement to be made is what im referring to"

So the analysis is done and the judgement is yours.

## What has been measured for you

`bed_measurements` - the track that will play, measured whole:

- `integrated_lufs` - its own loudness, before anything is done to it.
- `loudness_range_lu` - how much it moves between its quiet and loud parts.
- `speech_band_ratio_db` - how much of its energy sits in the 300-3400 Hz
  band, which is where a voice lives. A bed with a lot there competes with
  speech at the same separation that a bed with little there would not.
- `true_peak_dbtp`, `rms_spread_db`, `window_spread_db`.

`mix_windows` - one row per block of the edit, in the order it plays:

- `music_behavior` - what the spine planned the bed to do here. Five words:
  `prominent` (music leads), `background` (music under speech), `fade_in`,
  `fade_out`, `silent`.
- `speech_lufs` - the measured loudness of the speech in this window. `null`
  means nothing measured it - it does NOT mean silence.
- `separation_at_the_fallback_db` - what the gap would be if this run kept
  the level the engine used to hold. It is the status quo, shown so you can
  say whether it is right for this material. It is not a recommendation.

`mix_decision_legend` - what each column is, in one place.

## What you are deciding

Two numbers, in `value_decisions`, one per scope:

- `scope: "background"` - how far above the bed the voice should sit where
  music plays under speech.
- `scope: "prominent"` - where the bed sits when music LEADS.

Both are a SEPARATION in dB: how far the speech sits above the bed once the
mix is done. A bigger number is a quieter bed under a voice.

For `prominent` there is usually no speech in the window at all - that is what
the word means - so the reference is the loudest speech in the piece: where
the bed should sit when it leads, against the voice the rest of the video is
carried by. A small number puts the bed near the voice's level; a negative one
puts it above. The basis the reference was read on is recorded with your
decision.

**You are not naming a gain.** The engine computes the clip gain that
delivers your separation, from your answer and the two measurements:

    gain = (speech_lufs - your separation) - bed_integrated_lufs

Do not try to work that out or name it. If you name a gain instead of a
separation the number will be wrong by however far the bed was mastered from
the voice, which on the last project measured was 8.6 dB.

**There is no bound and no scale.** No range is offered because none exists.
If this bed and this voice need a separation a general rule would not have
reached, that is the answer - the captain's words are that "it could very
well be possible that we need to use values outside of these bounds".

**`why` is required** on every entry: what you read in the measurements that
made the number what it is. An entry without one is dropped, because a mix
level nobody can review is exactly what this step used to ship.

**Leave a scope out if this material genuinely does not let you decide it** -
if nothing measured the bed, or no window carrying that behaviour has
measured speech under it. An omission is recorded as an omission. It is
never read as agreement with what the engine used to do.

## Dialogue cleanup (a second, separate decision)

`cleanup_context.sources` - one row per played source, measured whole:

- `floor.level_dbfs` - the measured quiet of this source, the room tone
  rung 5a stages fills from. The noisiest played source on the last
  measured project sat at -29.7 dBFS against -44 to -54 elsewhere.
- `floor_unmeasured_reason` - why no floor could be measured (dense
  speech with no 0.3 s pause, a missing file). A reason, never a level.
- `speech` - the loudest played range's own loudness.

`cleanup_context.tools` - what the build can actually do on this run:
`deepfilternet` (a processed stem placed natively) is probed in the
build interpreter - the binary method needs the binary on the machine
(`ren doctor` reports it), not the model in the interpreter -
`voice_isolation` (Resolve's per-track Voice Isolation, Studio-only)
applies at build with read-back, and `audio_ops` is the local FFmpeg plus
offline WPE chain for EQ, de-essing, and dereverberation.

In `cleanup_plan`, one entry per source that earns it:

- `source` - the source_file basename from the table. Nothing else
  names a file the build can stage.
- `tool` - `voice_isolation`, `deepfilternet`, or `audio_ops`. An
  unknown name refuses the step rather than shipping an uncleaned
  source reported clean.
- `amount` - REQUIRED for `voice_isolation`: Resolve's own 0..100. The
  rung-3a probe read back 60; how strong yours should be is what the
  floor is for. FORBIDDEN for `deepfilternet`.
- `operations` - OPTIONAL chain of effects, also accepted with either
  cleanup tool; REQUIRED and non-empty for `audio_ops`. Each object has
  exactly these keys:
  - `{"type":"high_pass","frequency_hz":80,"why":"..."}`
  - `{"type":"equalizer","frequency_hz":300,"gain_db":-3,"q":1,"why":"..."}`
  - `{"type":"de_ess","frequency_hz":6000,"reduction_db":3,"threshold_dbfs":-30,"q":2,"attack_ms":5,"release_ms":80,"why":"..."}`
  - `{"type":"dereverb","strength":0.5,"why":"..."}`
  Numeric controls must be explicit and in the supported range. The
  example numbers show field shape only - use the request's numbers
  exactly where present, and choose from its feel words where absent.
  The effect chain is applied to the played source range and placed as an
  OTIO stem; a plan value that cannot be read or applied refuses by name.
- `span_start`/`span_end` - an optional pair in source seconds. Leave
  both out and the whole played range is cleaned.
- `why` - REQUIRED: what in the measured floor made this tool (and this
  amount) the answer.

There is no default cleanup. Leave the list empty when no source earns
it - the build cleans nothing it was not asked for, and a floor with no
entry is a measurement, not a request.

## What is not yours here

The three other words are not levels and are not asked for.

- `silent` is the absence of music, not a quiet level.
- `fade_in` and `fade_out` are a move between two levels, so a fade takes the
  level of the block it moves to. There is no third number.

Which track plays, where it sits, and what the bed does under each block were
all decided upstream. You are being asked how loud those decisions are.

## Word-gap ducking

Return one `music_ducking_plan` object with exactly these keys:

- `enabled` - true when the request calls for the bed to sit lower while
  words are spoken and recover between words.
- `duck_db` - how far the bed rises in a gap relative to its per-block
  speech level. The declared per-block level remains the ducked level.
- `release_ms` - how long the bed takes to recover after a word. If a
  gap is shorter than the release, the bed stays ducked through that gap.
- `why` - name the request or creative reading behind both values.

When enabled, the build derives word intervals privately from the timed
spine after this prompt returns. Word timings do not belong in your
answer. Keep stated numbers exactly; translate feel words to a numeric
release and duck depth with the basis in `why`. When no speech/music pair
can use this, set `enabled` false and both numeric keys to null.

## Delivery loudness and peak

Return one `audio_delivery_plan` object with exactly these keys:

- `dialogue_target_lufs` - preserve an explicit dialogue integrated
  loudness number from the request. The build applies this as the final
  exported program loudness target and measures the export against it.
  Use null when no numeric dialogue target was requested; the delivery
  target remains -14 LUFS.
- `true_peak_ceiling_dbtp` - preserve an explicit true-peak ceiling from
  the request. Use null when none was requested; the export ceiling
  remains -1 dBTP.
- `why` - name the request that supplied the numbers, or why the
  declared delivery targets apply.

The final file is re-measured after mastering. A normalization write
does not count as proof; the measured LUFS and dBTP are recorded on the
render report and checked before delivery.
