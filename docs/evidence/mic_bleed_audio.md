# Mic bleed belongs to the losing audio angle

`tests/unit/reels/test_reel_build_mic_bleed.py` is the scenario table for the
audio mute contract. `library/tools/reel_build.py` owns
`suppress_mic_bleed_audio`; `library/tools/timeline_transcript.py` owns
the measured speaker decisions that feed it.

## The incident

On Reel 09, master audio rows were named `Akshita CH1` and `Craig CH1`,
while the transcript identified speakers as `Akshita` and `Craig`. The
build treated each stream label as a person, so it muted both
microphones during turns and left short audible scraps at several
boundaries. The correct speaker identity comes from the audio row's
picture angle, joined by track index.

## Preserved cases

The parameterized table covers four outcomes: split only the losing
mic around another speaker's line, keep both mics for a genuine overlap,
leave picture placements untouched, and resolve a channel-named speech
row through its picture angle. The split case checks frame-rounded
mute edges and the source-time mapping on both surviving pieces.
