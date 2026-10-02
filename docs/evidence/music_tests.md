# Music and mix tests - the incidents behind them

Module docstrings of the music/mix test files as they stood on 2026-10-02,
moved here when each test kept only its invariant. The tests and the code
win where this history disagrees.

## Mix reads the bed

From `tests/unit/audio/test_mix_reads_the_bed.py`.

```text
The mix declared the bed, received it, and never opened it.

Step 5.02 declared `music_selection` and `creative_direction` REQUIRED and
`enhancement_spec` optional, and read none of the three - the reader
discipline guard named all three the moment its deterministic-step blind
spot was fixed (`python3 -m library.tools.input_contract --bad`).

That is not a paperwork defect.  A `music_behavior` word is a RELATIVE dB
applied to whatever level the music file already carries, so whether the
planned offset lands is decided by the bed's own loudness - and on 001
the bed is mastered 8.6 dB hotter than the speech, so -18 dB of clip gain
buys about 12.5 dB of separation and 1 of 11 speech-bearing windows met
the margin the check judged it against.  The step could not see which
track was chosen, let alone how loud it is.

These tests follow the measurements end to end: 2.04 measures every
candidate, the CHOSEN one's scalars travel on `music_selection`, and 5.02
reads them.  They also pin the two honesty properties the wiring is worth
nothing without - an unmeasured bed is an admitted absence and never a
level of 0, and the render-side check says when the margin it judged
against was a clip gain rather than a separation somebody declared.

Nothing here asserts a level, and since 2026-09-16 there is no level to
assert: the captain removed the five clip gains, step 5.02 asks a mix
engineer for the SEPARATION over these same measurements, and the gain is
solved from it (`library/tools/decided_value.py`).  What this file pins is
unchanged by that - the measurements reach the step, an unmeasured bed is
an admitted absence, and the render-side check says which kind of number
it judged against.
```

## Music audit trail

From `tests/unit/audio/test_music_audit_trail.py`.

```text
The music audit trail is out of the spines, and kept in its own file.

Captain's ruling, 2026-09-16: move it out to its own file, then confirm
on a real run. The record - how each track was found and why this one
was chosen - is KEPT; it just stops travelling through every step that
never reads it (~24 kB per run in `pipeline_data.json`, the single
largest remaining payload saving found anywhere in the pipeline).

Both halves are pinned here:

1. ABSENT from the edit data: `mesh_spine`'s post-bridge writes no
   `music_selection` key into `audio_spine` (or `timed_spine`, which
   is the same object).
2. PRESENT and COMPLETE in its own file:
   `pipeline_output/steps/2_04_music_selection/music_audit_trail.json`
   (`library/tools/music_audit_trail.py`), holding the whole resolved
   selection - candidates, justification, splices, section,
   measurements, provenance - with nothing summarised away.
```

## Bed fits each block

From `tests/unit/audio/test_music_bed_fits_each_block.py`.

```text
Finding 25: the music bed has one level per behaviour, fitted to one block.

On the scout's B4 run every `background` block's bed was -29.57 LUFS,
fitted to one block's speech: block speech ranged -39 to -20 LUFS, so
delivered speech-over-bed separation was 2.6-9.2 dB in 10 of 12 blocks
against the step's own 14 dB target. Recorded in `music_automation`,
never reported as a shortfall.

The fix: fit the bed per block to the decided separation, and report any
shortfall on the row that misses (plus the undetermined rows that state
why they carry no gain at all).
```

## Music selection contract

From `tests/unit/audio/test_music_selection_contract.py`.

```text
Music selection has a real schema, and the library is really consulted.

Captain's ruling 2026-08-20, verbatim: "fix schema and let LLM choose from
both library or outside". A hybrid, and neither offered option.

Three things must hold, and each of them failed on the shipped run:

1. The LLM is handed a non-empty output schema. It was handed an EMPTY one,
   because `present_llm_step` subtracts anything the bridge already
   supplied from `interface.outputs`, and the bridge supplied the step's
   only output.
2. `PIPELINE_MUSIC_LIBRARY` is opened. It never was.
3. Selecting from outside the library is still allowed. The captain did
   not restrict it, so a test that forbids `external` would be wrong.

And the failure that prompted all of it - a 3914-second "Inspirational
Motivational Music Video" scoring a 55-second piece whose direction says
it must never be scored as triumphant - must now be rejectable on the
recorded reasoning.
```

## Music selection resolver

From `tests/unit/audio/test_music_selection_resolver.py`.

```text
The main selection resolves title-plus-source to a catalogue path.

At HEAD the model had to hand-copy `audio_path` verbatim because nothing
resolved the track TITLE it chose to the catalogue PATH the verdict
demands - the only title matching in the path was `_shortlist_track`,
which serves shortlist sections, not the main selection.  So a title the
model named and a path it retyped could disagree, and the step could only
refuse the disagreement after the fact.

`resolve_audio_path` is the missing resolver: exact on title AND source,
and a refusal - never a guess - when the title is absent or ambiguous.
An ambiguous title resolved by picking the first match would let sorted
order decide what the viewer hears, which is the defect family AGENTS.md
10.5 has been closing.
```

## Silence under picture

From `tests/unit/audio/test_silence_under_picture.py`.

```text
Picture on screen with nothing at all on any track.

Nothing in this repository looked for it.  "Every frame of the timeline
must show a clip" (AGENTS.md section 10.2) has no audio twin:
`detect_black_frames` asks whether the picture went away,
`verify_audio_streams` asks only whether an audio stream exists, and
`measure_lufs` asks whether the whole master is deliverable, which an
11% hole barely moves.

Measured on project 001's shipped master: **6.312s of its 56.639s is at
digital zero, 11.1%** - 41.643-43.943s (2.301s) and 52.627-56.639s
(4.011s, the last four seconds of the video), of which 6.274s has a
picture on screen.  Measured on the captain's craft reference, twenty
minutes of finished documentary: **0.783s, 0.06%**, at 1219.379s, and
every frame of it is black.  That is the difference this check is for.

The mechanism on 001 is traceable and no part of it is a bug - a
`transition_slot` declaring `music_behavior: silent` and an `outro`
declaring `fade_out`, both covered by V2 cutaways placed `video_only`,
with no A-roll under them.  Silencing the MUSIC is not silencing the
FILM, and the plan has no vocabulary for the second.

The gate is DIGITAL ZERO alone.  How quiet a declared quiet moment may be
is an open captain decision (`craft-silence-under-picture`), so the
ladder above zero is reported at every rung and gates at none.
```
