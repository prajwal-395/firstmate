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

## What is not yours here

The three other words are not levels and are not asked for.

- `silent` is the absence of music, not a quiet level.
- `fade_in` and `fade_out` are a move between two levels, so a fade takes the
  level of the block it moves to. There is no third number.

Which track plays, where it sits, and what the bed does under each block were
all decided upstream. You are being asked how loud those decisions are.
