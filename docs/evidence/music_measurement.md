# `library.tools.music_measurement` - the history behind its contract

This is the module docstring of `library/tools/music_measurement.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
What a bed sounds like, measured - so the choice is not made on filenames.

Step 2.04 picks the music that plays under the whole video.  Until now the
model was handed ``title, audio_path, duration_seconds, source,
duration_ok, duration_note`` and asked to score seven candidates against
the creative direction's emotional landscape.  There is nothing musical in
that list.  The run of record says so in its own words:

    "I am being asked to match an emotional landscape against seven
    filenames. ... on the filenames alone the 'rise' track reads as
    forbidden and Sickick reads as neutral - i.e. the context as supplied
    points at the wrong answer."

That run got the right answer only because the agent had a shell and
measured the files itself with ffmpeg.  A model without one cannot.  What
it measured, and what decided it, was four numbers:

======================  ==============  ====================
                        rise (chosen)   Sickick (rejected)
======================  ==============  ====================
integrated loudness     -13.9 LUFS      -15.1 LUFS
loudness range          6.0 LU          9.1 LU
RMS spread              5.7 dB          29.9 dB
envelope over 0-60s     flat at -16.3   -43 -> -13 dBFS
======================  ==============  ====================

A 29.9 dB swing under a voice that sometimes whispers has no single clip
gain that works.  That is the whole basis of the decision, and none of it
was in the context.  This module puts it there.

**It measures.  It does not classify.**  No mood, no genre, no "energy"
word, no ranking, no recommendation - those are the model's to write and
inventing one here is the taste-fabrication AGENTS.md 10.5 forbids.  What
comes out is numbers and one curve.  What each one IS, in what unit, is
stated in step 2.04's own ``handoff.md`` - never what to conclude from
it - and :data:`MEASURED_KEYS` is this module's inventory of them, which
the import-time guard below is asked of.

What is measured, and why each one
----------------------------------
``integrated_lufs``
    Perceived loudness of the whole track (BS.1770, ffmpeg ``loudnorm``).
    The mix's ``music_behavior`` words are RELATIVE dB offsets applied to
    whatever the file already is, so the file's own level decides whether
    the planned offset lands.  AGENTS.md 10.4 records 001's music arriving
    8.6 dB hotter than its speech and the separation target becoming
    unreachable by any mix setting; this is that number, before the choice.

``loudness_range_lu``
    How much that perceived loudness varies across the track (LRA).  A bed
    is placed once, at one gain, and run to the end of the timeline.

``rms_spread_db``
    p95 minus p5 of per-second RMS windows over the whole track.  The
    ungated companion to LRA: LRA discards everything under its own
    relative gate, so a track that opens near silence and lands loud can
    read narrow.  This is the number the run of record used, and on 001's
    Sickick instrumental it reproduces it - 30.7 dB measured here against
    the 29.9 dB the agent got by hand.

``window_envelope_dbfs`` and ``window_spread_db``
    The same per-second RMS, over one ``target_duration_seconds`` span
    from the head of the file - the section that plays when the model
    declares none.  A viewer hears one span that long and no more, so this
    is the shape of what is heard, not of the track.  The curve is
    bucketed to :data:`ENVELOPE_BUCKETS` points so its size does not
    depend on the target duration; the spread is the same p95-p5 read, as
    one scannable column.  ``track_sections`` below is how every other
    span compares.

``true_peak_dbtp``
    Free from the same ``loudnorm`` JSON, no extra pass.  Headroom before
    the master limiter that step 6.02 measures and fails a build on.

``speech_band_ratio_db``
    Speech-band (:data:`SPEECH_BAND_HZ`, the telephony voice band) RMS
    minus full-band RMS: how much of the bed's energy sits where the voice
    does.  On 001 it separates the candidates by 5.8 dB - the piano is
    -2.8, the Sickick instrumental -8.6 - which is a real fact about
    whether a bed and a voice occupy the same place, and one no filename
    carries.

``track_sections``
    The same per-second RMS, reduced to one row per *playable section* of
    the track: successive non-overlapping spans of ``window_seconds``,
    plus the last span that still fits, each with its mean level and its
    spread.  A track is longer than the video and only part of it plays;
    which part is the model's decision
    (:mod:`library.tools.music_section`), and this is what it has to
    decide from.  The rows are a description of the track at the
    granularity of what plays - **not a menu**: a section may start
    anywhere, and nothing here says which one is best.

Nothing here is a threshold and nothing here is a verdict.  The one
judgement in the module is which candidates are worth opening at all, and
it is mechanical: see :func:`should_measure`.

Considered and declined
-----------------------
See :data:`DECLINED_MEASUREMENTS`.  Widening the table is not a way to make
the prompt better; every column is paid on every candidate, and the reason
there is room for these ones is that #295 stopped copying the channel brief
into the prompt.  Measured on 001's own snapshot, this step's context:

    b10833d  57,539 B  candidates 2,204 B   3.8%  (84.3% creative brief)
    #295     14,260 B  candidates 2,204 B  15.5%
    + this   16,747 B  candidates 4,691 B  28.0%


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**The bed's own measurements reach the mix, because a step that cannot see the music cannot act on any answer about it.**
Step 2.04 measures every candidate (§10.5); its post-bridge folds the CHOSEN track's SCALARS onto `music_selection.measurements` through `music_measurement.selection_measurements`, and step 5.02 reads them and records `bed` plus a per-window `bed_level_after_gain_lufs` - the bed's integrated loudness plus the clip gain, which is arithmetic and not a decision.
- **Only scalars travel.** `music_selection` is declared whole by `plan_transitions` and `mesh_spine`, so the envelope curve and the section table would land in two prompts (§10.1). `WITHHELD_FROM_THE_SELECTION` records both with the reason, and an unaccounted measurement key raises at import.
- **An unmeasured bed is an admitted absence**: `measured: false` with its reason, no level at all, and `bed_level_after_gain_lufs` None - never 0.
- **The separation a window will DELIVER is predicted, and the separation it OUGHT to deliver is not supplied.** `library/tools/speech_loudness.py` measures the speech with one ffmpeg `loudnorm` pass per block over the ranges `a_roll_assignments` names - 0.23 s a block, measured, so 1.2 s for 001's eight - and 5.02 records `speech_lufs` and `separation_delivered_db` per window. **Measure and expose; never choose.** `SEPARATION_TARGETS_DB` is still empty and the master loudness target is still the captain's, so nothing compares the delivered number with anything. A block whose speech could not be measured records the reason and `None`, never 0.
- Step 5.02 declares `audio_spine` and `music_selection` and nothing else. Re-declaring `creative_direction` or `enhancement_spec` needs a reader in the same commit.
- `tests/test_mix_reads_the_bed.py`.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**Every candidate is MEASURED, and nothing about it is classified.**
`library/tools/music_measurement.py` is that half: integrated loudness, loudness range, RMS spread, the envelope over the played window, true peak and the share of energy in the speech band.
- **What each measurement IS is defined in step 2.04's `handoff.md`**, where the model reads it - the unit and nothing about what to conclude. `MEASURED_KEYS` here is the inventory the import-time guard is asked of, not a second copy of the definitions: a key measured and neither travelling with the selection nor recorded as withheld fails at import.
- **A candidate the duration check already rejected is not opened**, and says so rather than leaving a blank column. That is mechanical - it cannot be selected either way.
- `DECLINED_MEASUREMENTS` records what was left out and why. Tempo and key used to be declined there (2.06 measures them after the choice); the captain's decision of 2026-09-07 reversed that, so they are measured per candidate at choice time instead. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)
- The bed's own level is what decides whether a planned `music_behavior` offset lands - see §10.4.
- The played window's envelope is the section starting at 0; `track_sections` is how every other span compares.
- **Where the candidates come from**: [`docs/MUSIC_SOURCING.md`](docs/MUSIC_SOURCING.md) §5.
- `tests/test_music_measurement.py`.
```
