# The repetition nothing measures: inside one speaker turn

Filed 2026-09-05. Found by the MODEL, on a clean re-run of reel selection,
in its own `undetermined` - not by a reviewer and not by a test:

> "Whether a repeated sentence inside a single speaker turn is still in
> the captain's cut or is an artefact of the transcription. `retake_of`
> and `retake_band` compare whole candidate stretches and say nothing
> about repetition inside one turn, and this cut has a lot of it."

The agent that wrote that had been given the prompt and nothing else. It
did not know reel 03 existed, did not know a defect was being looked for,
and had no access to any prior finding.

## Why it is right

Two detectors exist and neither covers this.

**`reel_proposal.duplicate_takes`** scans the word stream inside a
candidate span and reports repeats. It is what put
`has_duplicate_take: true` on reel 03. It is a REPORT and nothing acts on
it.

**`reel_build.redundant_takes`** is the one that CUTS, and it pairs
SEGMENTS: same speaker, durations within `DURATION_RATIO`, containment
and Jaccard over a window. Two segments.

**`reel_exchange`'s `retake_of` / `retake_band`** compare whole candidate
WINDOWS against each other - "these two proposals are the same exchange"
- which is the axis the model named.

So the covered cases are: two segments that repeat, and two windows that
repeat. The uncovered case is a single speaker turn that contains its own
repetition, where the transcriber did not split it into two segments and
no second window exists to compare it against.

## What it costs, measured

Reel 03 of the captain's approved nineteen is the case. Its span
301.2-341.3 carries three takes of "search didn't change, the question
changed, whoever AI understands best gets the answer". Removing
`MIN_TAKE_SECONDS` cut three of the repeats and left two, eighteen
seconds apart with another speaker's turn between them - which is
correctly outside what a segment-pair rule should touch.

Nothing measured the remaining repetition, and the proposal that named
that span read "The tightest thing said in the episode and it is already
whole."

## Why it is not simply "lower a threshold"

The two surviving takes are eighteen seconds apart with an intervening
speaker. A cut rule that removed one would be removing a line the editor
may have kept on purpose, and this repository has already been bitten by
a gate that fires on correct output. The right shape is the one
`reel_opening.py` takes and the one the model itself is asking for:
**measure it and report it on the proposal**, so the span can be
re-drawn by whoever is choosing. On the same re-run the model did exactly
that unprompted - it re-spanned that stretch to 313.7-341.3 and recorded
why - which is the behaviour a measurement would make reliable rather
than lucky.

## What would have to be built

A per-turn repetition measurement over the word stream, reported per
moment alongside `duplicate_takes` and `opening_observations`. No score,
no threshold, no rejection: the same rule as everywhere else here, since
whether a repeat is a flub or a deliberate restatement is a judgement
about the writing.

`library/tools/reel_opening.py` is the nearest model for shape, and
`reel_proposal._scan_takes` already walks the word stream this would read.
