# Audio sync

Syncing motion to sound. The principle: time sound effects and motion
from the same constants. A whoosh that starts 150 ms before the
movement peak reads as cause; a whoosh that starts after the move
reads as an apology.

## The sync hierarchy

Motion syncs to audio at three levels:

1. **Beat** - cuts and entrances land on the music's beat grid. The
   beat grid is `tempo.beats` / `tempo.downbeats` and does not start
   at zero. See `library/tools/beat_grid.py`.
2. **Word** - picture events land on the spoken word they illustrate.
   The picture leads the word by a measured lead time (see below).
3. **SFX** - a sound effect lands on the frame its visual event
   happens, or just before.

## Picture leads the word

Measured on an animation-first reference (Ren's
`docs/ANIMATION_FIRST_REFERENCE.md`): two-thirds of picture events
are illustrations of a single noun, and each one arrives BEFORE its
noun is spoken. The picture is a visual cue that primes the viewer
for the word.

The lead time is a measurement, not a guess. It is the difference
between the picture event's timestamp and the word's timestamp in
the transcript. A typical lead is 0.3-1.0 s - long enough to register
the image, short enough that the connection is obvious.

## Word-level subtitle animation

Ren's subtitle overlay animates each word as it is spoken:

- The word's accent sweep (colour change) starts at the word's
  start frame and ends at its end frame.
- The entry scale (0.95 to 1) runs over the first 4 frames of the
  word's window.
- An emphasised word is drawn at 1.14em font size for the whole
  card, not transformed.

The word timings come from the transcript. The animation is a pure
function of the timeline time - the same frame always produces the
same picture.

## Placing sound effects

- A whoosh starts ~150 ms before the movement peak.
- A pop or click lands on the frame the element appears (+0-50 ms).
- A transition sound starts at the transition's T, not before.

Derive SFX times from the same constants that drive the animation.
If the animation is re-timed, the SFX follows automatically.

## Gain guidelines

Relative to a normalised voice at 0 dB:

| Source | Gain (dB) |
|---|---|
| Music under voice | -16 to -22 (plus ducking) |
| Music alone | -8 to -12 |
| UI SFX | -8 to -14 |
| Whooshes | -8 to -12 |

Ducking: -12 dB, 0.2 s attack before the voice, 0.3 s hold, 0.8 s
release. Voice gaps under 0.8 s are merged.

## Checking without listening

- Loudness: about -14 LUFS integrated for web/social (-16 for
  podcasts, -23 for broadcast). More than 2 LU off means adjust gains.
- True peak at or below -1 dBTP.
- No unintended silence - an unexpected silent range means a track
  ends early or a file is missing.
- Scenes with voice should be within ~2 LU of each other.

The waveform image shows cue ticks under the scene lines: each
sound should sit where its visual event is. For a precise check,
query what plays at an event time and compare with a frame of the
same time.
