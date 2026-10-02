# Transcription and alignment - the incidents behind the tests

Incident history moved out of the transcript/alignment test modules
(`tests/unit/audio/test_transcript_confidence.py`, `tests/unit/audio/test_aligner_leading_gap.py`,
`tests/unit/audio/test_transcript_duration_anomaly.py`) when the suite was consolidated
on 2026-10-02. The tests keep the invariant; this file keeps the story.

## Transcriber confidence and the script check (`test_transcript_confidence.py`)

A clean selector run over the whole 45-minute episode named the one
thing it needed and did not have, and priced it in its own
`could_not_determine`: it could not tell a garbled READING from garbled
AUDIO, so it ended reel 22 at 284.07 rather than 299.41 - giving up
Craig's 290.73-299.41 pickup and about 15 seconds of length to keep the
damaged line outside the span.

What it asked for was "a per-line transcription confidence beside the
text - the ASR already produces one - or a flag saying a line's
characters fall outside the language the rest of the transcript is in".

Both now exist, and the test file holds all three directions at once,
because a gate that can only fail one way is not a gate (AGENTS.md 10.4):

- the transcriber's own confidence REACHES the prompt, through the
  declaration at the manifest's top level that #583 built;
- the word timings, source paths and item ids #583 removed still do NOT
  come back with it; and
- NO threshold fires on any of it. The number is published and the model
  judges - the captain's standing ruling (AGENTS.md 10.5).

Measured on the field-test transcript, 2026-09-06: it carries no
confidence at all: 940 segments whose keys are exactly `speaker, text,
timeline_start, timeline_end, source_file, source_start, source_end,
resolve_item_id, words, read_from_words`, and 8,509 words whose keys are
exactly `word, start, end, timed`. `faster_whisper` emitted `avg_logprob`
per segment and `whisperx.align` carried it; both write sites in
`timeline_transcript` rebuilt the dict without it.

Its letters are 38,008 LATIN and 8 HANGUL, and all 8 sit on one line -
284.13-288.06, exactly the line the model named. One flagged row in 940.
The same transcript carries `kalabrahat Correct.` at 299.999 - the same
failure in LATIN characters, which the script check cannot see; the
legend says so.

Since 2026-09-24 no arm publishes segment confidence (Voz emits nothing
like it, MFA emits no per-word score), so "no `avg_logprob`" has three
readings - written before the number was kept, a row the aligner produced
without one, and a hybrid transcriber that emits none - and an MFA-timed
transcript additionally carries no `alignment_score`. Each case gets its
own sentence in the view.

## The aligner's leading gap (`test_aligner_leading_gap.py`)

Project 001's finished 59.437s export carried **6.901 seconds of audio
nobody chose, with no caption over it** - 11.6% of the video - because
`_align_words_to_text` anchored body_3 on the wrong occurrence of the
word *"i"*. The passage the model wrote begins *"i get caught up in all
the numbers..."*; the aligner opened it seconds before that, and the
two-pointer then walked forward over everything in between with no
bound on the time it may skip.

Both existing guards were the right guards for a different failure.
`MAX_HINT_DRIFT` fired and re-anchored - onto a second wrong *"i"*.
`MIN_TEXT_OVERLAP` compares SETS, so a span that is 63% unmatched audio
still scores a perfect 1.0.

The fix is an anchor SEARCH, not a threshold: every occurrence of the
passage's first word is tried, and the alignments are ranked by words
aligned, then by the leading gap, and only then by proximity to the
model's time hint. A leading gap survives exactly when no equally
complete anchor removes it - which is what protects body_0's dramatic
pause ("and ... i have an announcement to make"), where the silence IS
the line. The fixture is 001's own clip_011 data from the 2026-08-26 run
that produced the shipped export.

## Duration anomalies (`test_transcript_duration_anomaly.py`)

Reel 12 (field test, 2026-09-19) is the specimen: the large-v3 arm
dropped "pull from there" and stuttered "probably" into two in a single
46-word row, MFA stretched "hallucinate" across the gap to 1.55s, and
the coverage gate reported CLEAN - every leg reads the transcript. The
two duration outliers in that row WERE the two captain complaints
("hallucinate" 1.55s the row maximum, "probably" 0.72s the runner-up,
against a next-longest content word of 0.58s), so the row's own words
scored against its own local rate must warn, while an ordinary row
carrying a naturally long word must stay silent: the same absolute
seconds warn in fast speech and pass in slow speech, because the
threshold is derived per row, never constant. Durations are from
`data/vep-ft-r12-missing-words-unexplained/report.md`.
