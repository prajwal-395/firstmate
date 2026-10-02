# MFA adoption: which aligner actually runs, retry, and digit handling

Finding, 2026-09-22. **No pipeline code changed, no transcription, no renders,
no Resolve contact.** Readings of checked-in code plus the transcript record of
one real post-adoption build. Prompted by an apparent gap: `ALIGNER_MFA` is
defined and referenced, but `library/tools/hybrid_transcription.py` writes
`ALIGNER_WAV2VEC2` at both places a value is recorded.

## Answer up front

**MFA runs.** It is the preferred aligner on the reels transcript path and it
timed both speakers of the real build on disk. The two `ALIGNER_WAV2VEC2`
writes are a default and a true fallback account, not the selection - the
selection overrides both via a stamp. Retry for transient empty chunks is
**present** in the shipped path; whether it has ever fired is **unverifiable**
from the records. Digit/symbol spelling-out is **present** in code **and** in
effect in the record. The WhisperX full arm is standby, not load-bearing, on
the reels path - but the edit-pipeline ingest is still WhisperX-only, which is
a different arrangement from "MFA primary everywhere" and is stated as item 4.

## 1. Which aligner runs on the reels path: MFA (code + record agree)

Code path (`library/tools/timeline_transcript.py:585-614`): `_aligner()` is a
composite. `_covers` is MFA-covers OR wav2vec2-covers; `_align` tries
`mfa_align.align` first and runs wav2vec2 `align_segments` only on
`FallbackRequired`. Both arms stamp their own document - `"aligner": "mfa"`
(`library/tools/mfa_align.py:550`) and `"aligner": "wav2vec2"`
(`library/tools/timeline_transcript.py:581`) - and
`hybrid_transcription.transcribe_and_align` copies the stamp onto the record
(`library/tools/hybrid_transcription.py:444`:
`aligned.get("aligner", ALIGNER_WAV2VEC2)`). The second `ALIGNER_WAV2VEC2`
write (`hybrid_transcription.py:465`, `fallback_record`) is the full-WhisperX
arm's account, and that arm genuinely is wav2vec2-timed. So neither write can
override an MFA stamp: one is a default for unstamped (pre-MFA) documents,
the other describes a different arm.

Record (the stronger evidence, because it says what happened): the
`lucie/geo-podcast` timeline transcript
(`pipeline_output/scratch/timeline_transcript/transcript.json`, written
2026-09-20, after the 2026-09-17 adoption, timeline "GEO Podcast - Synced")
carries `transcription.arms = {Akshita: hybrid, Craig: hybrid}` and
`transcription.aligners = {Akshita: mfa, Craig: mfa}`. Corroborated twice
over by MFA's documented cost: `words_with_alignment_score` is 0,
`segments_with_asr_confidence` is 0, and every sampled word carries
`alignment_score: null` - exactly what "MFA emits no per-word score"
(`library/tools/mfa_align.py:61-68`,
`library/tools/hybrid_transcription.py:99-108`) predicts. A wav2vec2-timed
run would carry scores.

## 2. Retry for transient empty chunks: PRESENT in code, firing UNVERIFIABLE

Present: `mfa_align.align` (`library/tools/mfa_align.py:147-180`) collects
chunk indices with no TextGrid, re-runs exactly those in a retry corpus, and
declines the whole run to wav2vec2 only if chunks are still empty after the
retry. Covered by
`tests/test_mfa_align.py::test_a_transiently_empty_chunk_is_retried` (flaky-first-run
recovery). From the record: 8,590 words, 0 untimed, so no window was lost -
consistent with retry working OR with no transient failure occurring. No
retry accounting surfaces in the document (the return is
`{"segments", "aligner"}` only) and no run logs are kept beside it, so a
firing leaves no trace to read back. If a future dispute needs firings
counted, the count must be added to the record; it cannot be recovered now.

## 3. Digit/symbol spelling-out: PRESENT in code and in effect

Present: `normalization_map` / `normalize_for_aligner`
(`library/tools/mfa_align.py:446-461`) spell digit/symbol tokens out for the
`.lab` line only, and `merge_window` (`:471-522`) walks both sequences in
order and writes the ORIGINAL token back, degrading an unplaceable token to
an untimed word rather than dropping or mis-timing it. In effect: the same
geo-podcast record holds 19 digit/symbol tokens in original form - `20%`
twice (the exact class the measurement named), `2.0.`, `90s,`, `2000s,`,
`10`, `2023,`, `10-man`, `50-person`, `10-person` (x2), `50`, `100`, `3D`
(x3), `H1` (x2) - every one carrying a start and end, with 0 untimed words
in the whole 8,590-word transcript. The quiet state the brief feared (reels
silently losing numbers) is not what the record shows.

## 4. Is the WhisperX path load-bearing or standby? Standby on reels, primary on ingest - by design

On the reels path it is standby: this build never entered it (both arms
hybrid, both aligners MFA), and in code it fires only on measured
`FallbackRequired` triggers (`hybrid_transcription.TRIGGERS`) or an MFA
decline. Nothing here rewrites that. But scope matters and PR 1180 says
it plainly in its own message: "step 1.04/ingest untouched (reels path
only)". Verified: zero occurrences of "mfa" in
`library/steps/step_1_04_temporal_index/step.py`, which calls
`whisperx.load_model` + `load_align_model` (wav2vec2), and ingest records
carry no `aligner` field at all. So the edit pipeline's V1 in-point path
(`compile_manifest` from 2.02 blocks from 1.04 words) is still wav2vec2-timed
with the measured 40-55 ms late starts, while reel builds read the MFA-timed
`transcript.json` (`library/tools/reel_proposal.py:1847-1848`). "Every reel
built since 09-17 still carries the clipping" does not follow: reels flow
from the MFA document. What does follow is that any edit-pipeline render
does - until ingest is adopted separately.

## 5. Landing condition for whoever flips ingest: migrate the caption pins

`lucie/geo-podcast/external/caption_timing.json` holds the captain's
recorded Reel 13 pins: a +7-frame offset over the source scope
500.78-512.759 s plus a head-trim pin at source 512.759 s, both scoped to
"Reel 13 - the-accounting-firm-ai-called-healthcare". Pins address cards by
source audio at ~1 ms tolerance against a ~47 ms move, so any ingest timing
change detaches them. Migrate by the measured per-file delta; never leave
them to report stale.
