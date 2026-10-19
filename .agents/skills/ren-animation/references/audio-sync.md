# Audio sync

Time sound and motion from the same declared or measured source. Use
transcript timings for speech and `tempo.beats` / `tempo.downbeats` from
`library/tools/beat_grid.py` for music. The grid may not begin at zero.

## Choosing timing

- Anchor speech illustration to the word or phrase it serves. Whether
  picture leads or follows speech is an editorial choice; use the brief
  or ask when it changes meaning.
- Place sound effects in relation to the visual event. Do not apply a
  fixed lead or lag.
- When an animation changes timing, derive its linked sound timing from
  the same source so the relationship remains intact.

## Mix

Use the project's declared mix and measured audio. Do not invent generic
track gains, ducking values or voice-gap thresholds. Use the pipeline's
mix and render checks to measure the delivered audio, then inspect
whether the sound supports the picture and speech.

## Verification

Listen to the relevant passage and compare it with the visual event at
the same timeline time. Structural timing alone cannot establish that
the mix is clear or that the sound belongs to the motion.
