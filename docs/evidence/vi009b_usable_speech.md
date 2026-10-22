# VI-009b: the selection carries a usable-speech reading

Code: `library/tools/audio_quality.py` (`stored_record`, `ensure_measured`,
`ensure_readings`), `library/steps/step_3_02_select_broll/bridge.py`.
Tests: `tests/unit/audio/test_audio_quality.py`,
`tests/unit/context/test_bridges.py`.
Proof: `docs/benchmarks/vi009b_usable_speech_proof.py`.

## The defect

PR 3140 put the technical leg in the candidate table the planner reads:
seven indicators per candidate, each with its coverage and its confidence,
and none of them a preference. One of the seven is `usable_speech`, read
from M11 (`audio_quality.json`).

M11 was measured OUT OF BAND. Nothing in a pipeline run called it: the
only callers were the lane's own command line and a benchmark script. So
a run that never measured audio left `usable_speech` absent on EVERY
candidate, and the planner could not compare two candidates acoustically
at all - the column said `-`, which is honest and useless.

Reading a stored result already worked: `source_quality.profile_for_clip`
reads M11 out of the source memory when it is there. What was missing was
anything that PRODUCED the reading inside the run.

## The change

The selection now measures the audio lane for the sources it offers, and
files the reading in the source memory, where every other reader of M11
already looks.

1. **`library/tools/audio_quality.py`.** Three functions:
   `stored_record` (the reading this lane already holds for a source),
   `ensure_measured` (one source's reading, measured now when it has
   none), and `ensure_readings` (the sources of a set of candidates, with
   a report naming every one of them). A source that cannot be measured
   is reported with its reason and stays absent - an absent reading is
   never a zero.
2. **`library/steps/step_3_02_select_broll/bridge.py`.** The candidate
   table's technical legs are built after the audio readings are taken,
   so `usable_speech` reaches the planner with its coverage and its
   confidence. The run log says what was measured, what was already
   measured, what could not be measured and why.

The lane decodes a WHOLE source, so the run bounds its own work and says
which bound each source ran into: at most 8 sources, at most 1800 s of
source each, and at most 900 s of measuring in one call. The readings
that were taken are stored, so the next run continues with the ones it
did not reach.

The leg is still not a ranking, and the technical reading still does not
move the editorial choice: `compare_legs` and the prompt block are
untouched, and the column is still ONE LEG of the choice.

## Measured, on a Ren-owned scratch copy

The podcast's own cameras, the captain's media read-only, measured into
`fm-vi009b-scratch` (a Ren-owned scratch copy) and its private source
memory. The candidate table was built by the REAL step 3.02 bridge.

Four candidates offered; the denominator is the four.

| run | candidates with a usable-speech reading |
|---|---|
| BEFORE - PR 3140's bridge, source memory empty | **0 / 4** |
| AFTER - this branch, source memory empty | **3 / 4** (3 measured in the run) |
| AFTER - this branch, second run | **3 / 4** (0 measured, 3 read from the stored readings) |
| PR 3140's bridge, with the readings stored | **3 / 4** (it reads a stored result; it never produces one) |

The candidate table the planner reads, after the change:

```
LC4931.MXF    ... usable_speech=0.5578|22.71 dB[288 full]
LC4932.MXF    ... not measured
LCATL0011.MXF ... usable_speech=0.3593|28.95 dB[215 full]
LC4930.MXF    ... usable_speech=0.515|21.2 dB[226 full]
```

and the run log says, for the one candidate that carries no reading:

```
Usable-speech readings: 3 measured, 0 already measured, 0 unmeasurable, 1 not reached
  LC4932.MXF: not reached - the source is 4941s long and this call measures at most 1800s of source
```

Measuring those three sources took 7m42s of wall clock (four audio
streams each, decoded whole at 48 kHz). That is the cost the bounds
exist for: at most 8 sources, at most 1800 s of source each and at most
900 s of measuring in one call.

Two cameras of the same conversation, the same line, and the numbers
differ: 0.3593 against 0.515 of the speech band carrying energy, 28.95 dB
against 21.2 of SNR. The planner can now say which of two candidates is
acoustically the better one - and the leg still says what it is not: a
frequency-coverage proxy, never intelligibility.

## What this lane did NOT do

- It did not rank the candidates, and it did not move any editorial
  choice. The comparison carries no recommendation field and did not
  change.
- It did not measure the whole source. The lane reads M11's per-interval
  rows over the candidate's interval and M11's whole-source SNR, exactly
  as PR 3140's leg already did; what changed is that the reading exists.
- It did not measure a source longer than 1800 s. `LC4932.MXF` is 82
  minutes, and decoding it whole would cost about half an hour of the
  run's wall clock, so the run names it instead of measuring it and the
  candidate carries no reading. A reading over the candidate's INTERVAL
  rather than over the whole source would cover it, and would need its
  own slot: the M11 slot holds a whole-source reading, and a partial one
  filed there would read as the source's own reading to the search
  index. That is the next piece of work, not this one's.
- It did not touch the captain's project, timelines or media. The proof
  ran on `fm-vi009b-scratch`, and the measurements landed in that
  project's private source memory.
