# ASR lane qualification - 2026-10-16

Scope: AUD-001..015. This report follows the earlier inventory in
[`2026-10-15-asr-alternatives.md`](2026-10-15-asr-alternatives.md) and records
the calibration runs in [`asr-qual-2026-10-16/`](asr-qual-2026-10-16/).
Only the cleared GEO podcast and Apple 001 originals were used. Their files
were read-only. The five selected intervals total 248.81 seconds; all are
calibration windows. Held-out source audio and labels were not accessed.

## Implementation and decisions

Ren already uses Voz as its contained ASR implementation and MFA as the
transcript aligner. Those remain the baselines. The exact locally reported
Voz identity is `da` 0.1.1 / Desert Ant Core 3.1.0, Apple Core ML, Voz weights
tag `v0.1.0` resolving to publisher commit
`8e7ab8ca342c6bb00f9347a25e53fe44543b2a40`. The model uses Desert Ant Labs
Source-Available License 1.0; its commercial threshold and redistribution
terms remain unresolved in Ren's license evidence. The underlying Parakeet
weights retain their separate CC BY 4.0 terms. MFA remains 3.4.2 with English
US ARPA acoustic/dictionary v3.0.0 and G2P v2.0.0a.

The original NVIDIA Parakeet TDT 0.6B v3 checkpoint was run through NeMo as a
separate distribution and runtime. The exact publisher revision was
`541d1f99c6b0c3cd0b11a95167540bb8edefd82b`. The F32 `.nemo` file was
2,509,332,480 bytes, SHA-256
`3cbdc85877e668ca7b82d0d56770eb1fac76691f55d6b97545e8d61ca588d10d`,
downloaded to `/tmp/vep-mu-asr/parakeet-tdt-0.6b-v3.nemo`, and removed after
the benchmark. No MLX runtime was present in the scratch NeMo environment;
F32 was used under the small-model full-precision rule. This is an independent
runtime and file distribution, not an independent set of trained weights:
Voz is derived from the same Parakeet checkpoint. NVIDIA identifies the
checkpoint as CC BY 4.0 and permits commercial use subject to its terms and
attribution. See the [pinned NVIDIA model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/tree/541d1f99c6b0c3cd0b11a95167540bb8edefd82b).

No model default or canonical schema changed. EVAL-032 decisions are in
[`scorecard.asr.json`](asr-qual-2026-10-16/scorecard.asr.json): retain Voz and
MFA as baselines; keep Parakeet, Align, Ear, and Uhm research-only until the
local quality evidence is measured. Research-only here is an adoption
decision, not a license class.

## Measured calibration results

| Candidate | Cold seconds | Warm seconds | Warm seconds per audio minute | Peak memory |
|---|---:|---:|---:|---:|
| Voz | 5.245 | 4.268 | 5.146 | 84,017,152 B RSS |
| MFA | 73.221 | 82.907 | 99.962 | 1,583,775,744 B RSS |
| Desert Ant Align | 2.205 | 2.062 | 2.486 | 136,282,112 B RSS |
| Desert Ant Ear | 0.892 | 0.778 | 0.938 | 1,583,775,744 B RSS |
| Desert Ant Uhm | 2.629 | 3.220 | 3.882 | 1,583,775,744 B RSS |
| Original Parakeet TDT 0.6B v3 | 34.136 first load and inference; 12.152 in the full run after the probe warmed the file cache | 1.718 mean second inference per window | 2.071 | 5.307 GiB process-tree physical footprint |

Voz, MFA, Align, Ear, and Uhm values are from their standalone candidate
reports; the recorded memory value is each process's RSS high-water mark.
Parakeet's physical footprint comes from Ren's scheduler. Its short-window
probe used a 7.5 GB reservation and measured 5.307 GiB physical footprint.
The five-window run used a 6.25 GB reservation and also measured 5.307 GiB.
Both runs stayed within their declared reservations and below 8 GB. The
Parakeet cold timing distinguishes the initial checkpoint load from the
full-run process start, whose OS file cache had just been warmed by the
probe. Window extraction and JSON serialization were not separately timed;
these limits are recorded in the scorecard. The NeMo process loaded one
resident model for all five windows.

Align and MFA received the same Voz words on the same five WAV windows.
Their complete warm costs were 2.486 versus 99.962 seconds per audio minute.
Align used the `@desert-ant-labs/align` Node 26.11.1 package, SDK 3.6.0, with the
SDK-managed model; MFA uses the isolated micromamba runtime and its pinned
acoustic, dictionary, and G2P artifacts. This is measured runtime and setup
evidence, not a boundary-accuracy result. Alignment quality still needs the
blind source-time references.

## Language, timing, and filler behavior

The five already-recorded Ear results all reported English. The lowest
confidence was 0.544 on `IMG_1814_0_45`; that low confidence is retained as a
measurement. Parakeet's benchmark now requires a prior language-id result
and refuses an unknown or unsupported code before loading the checkpoint.
The supported-code set is the publisher's 25-language list. This prevents a
silent attempt outside the supported set; it does not claim local language
accuracy. The existing MFA/Align paths also report unsupported-language
passthrough or refusal explicitly.

Word and filler times retain both the WAV-window clock (`start`/`end`) and the
original source-audio clock (`source_start`/`source_end`). Each result carries
the source interval and ID. Uhm detections are emitted as timed events with
source IDs and source-clock boundaries. No code removes a detected filler;
any removal remains an editorial policy decision.

## Quality evidence and remaining gates

The agent labels have a material caveat: Gemini's words are usable as a
provisional text sequence, but its timestamps do not match the audio in 7 of 8
samples. The second pass is from Anthropic, but it was transcript-assisted and
did not transcribe words independently. It found timing errors, not an
independent word sequence. These are agent labels, not gold labels; text
agreement between independent labelers is therefore unmeasured. The full caveat
and its evidence are recorded in the audio check report at
`/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data/vep-labels-audio-check/report.md`.

One calibration interval can be compared to existing hypotheses: `IMG_1814`
source seconds 15-45. Against its 30-word Gemini sequence, Voz, Parakeet, MFA,
and Align each produce 1 substitution and 2 insertions (3/30 =
10.0% WER); each also matches the only labeled filler (`um`, 1/1). The
comparison uses the complete word sequences and reads no timestamps. These are
provisional word-sequence measurements from one interval. The label has no
punctuation, so punctuation quality is not scored. `LC4931`/`LCATL0012` labels
cover source seconds 0-25 while the benchmark hypotheses cover later source
intervals, so they are not paired. The remaining quality measures - CER,
punctuation, boundary errors, Ear accuracy, unsupported-language abstention,
and Uhm event precision/recall - remain unscored. Timing metrics are explicitly
not scored because the reference timing is unreliable.

Calibration is now frozen before held-out evaluation: retain Voz and MFA as the baselines, make no default or model change, and use case-insensitive lexical word-sequence WER plus filler-token precision/recall as the text scoring rule. Punctuation is scored only when the reference carries punctuation; source-time metrics are excluded because the label timing is unreliable. No candidate is adopted from the provisional single-interval result. The original
request for blind independent labels and tie-break is in
`/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data/vep-mu-asr/label-request.md`;
the delivered text evidence does not satisfy its independent-agreement
requirement. No candidate output was shown to either labeler by this lane.

The five calibration windows did not provide independent labels for noise,
multilingual speech, or overlap. The held-out label manifest marks its two
Apple intervals as naturally noisy and its two podcast intervals as clean, but
the single-family, transcript-assisted reference limits condition claims.
Ear returned English on each held-out interval; language accuracy remains
unscored because an independent language reference is absent. The held-out
report above is the one requested report after the calibration freeze.

## Single held-out report - 2026-10-17

Calibration was frozen before any held-out audio or label payload was opened:
Voz and MFA remain the baselines, no candidate or default changed, WER is
case-insensitive lexical word-sequence Levenshtein, and filler scoring uses
`uh`, `um`, `erm`, `er`, `mm`, `mhm`, and `hmm`. Punctuation is scored only
when present in the reference. The held-out report was then run exactly once on
the four held-out intervals (110 seconds total) through Ren's scheduler, with a
6.25 GB reservation and Parakeet F32. The pinned checkpoint's revision, size,
SHA-256, and both downloads/deletions are in
[`heldout/download-record.parakeet.json`](asr-qual-2026-10-16/heldout/download-record.parakeet.json).
The scheduler run completed in 679.371 seconds.
Candidate-by-candidate held-out memory is not scored: the runner records `ru_maxrss`, a cumulative child-process high-water mark across sequential candidates. The Parakeet measured footprint and reservation are documented from scheduler evidence; other held-out RSS values are not attributable per candidate.

| Candidate | Held-out word-sequence WER | Fillers P/R | Reference words |
|---|---:|---:|---:|
| Voz | 9.57% (31/324 edits) | 100% / 83.3% (5/5, 5/6) | 324 |
| MFA over Voz | 9.57% (31/324 edits) | 100% / 83.3% (5/5, 5/6) | 324 |
| Align over Voz | 9.57% (31/324 edits) | 100% / 83.3% (5/5, 5/6) | 324 |
| Original Parakeet TDT 0.6B v3 | 23.15% (75/324 edits) | 100% / 83.3% (5/5, 5/6) | 324 |

The detailed per-sample results and source intervals are in
[`heldout/quality.asr.json`](asr-qual-2026-10-16/heldout/quality.asr.json);
raw hypotheses, language observations, and execution reports are alongside it.
All four references contain no punctuation, so punctuation quality is not
scored. All boundary and event-timing metrics are not scored because 7 of 8
label samples have unreliable timestamps. Ear predicted English for all four
held-out intervals, but language accuracy is not scored without independent
language references. These are provisional agent-label results: Gemini supplied
the word sequences and the Anthropic check was transcript-assisted, so text
agreement between independent labelers is unavailable. The report does not
claim gold-label quality or change any adoption decision.

## Item status

| Item | Result |
|---|---|
| AUD-001 | Voz remains the baseline. |
| AUD-002 | Voz tag, runtime versions, Core ML build, and Source-Available License 1.0 revision are recorded above and in the scorecard. |
| AUD-003 | Original Parakeet is qualified as a separately runnable, conditionally distributable alternative with exact checkpoint identity; provisional held-out WER is worse than Voz and it remains research-only. |
| AUD-004..005 | Calibration and the single held-out transcription report include provisional text-only WER and filler measures. Punctuation is unscored because references contain no punctuation; independent text agreement remains unavailable. |
| AUD-006 | Source-clock word times are preserved alongside window-relative times. Boundary metrics are not scored because labels' reference timing is unreliable. |
| AUD-007..009 | Align and MFA were compared on identical words/audio, with complete runtime and dependency evidence. Boundary metrics are not scored because labels' reference timing is unreliable. |
| AUD-010 | Ear was run on all five calibration windows; the five results and confidences are preserved. Language accuracy remains unscored. |
| AUD-011 | Parakeet refuses missing and unsupported language IDs before model load. |
| AUD-012..013 | Uhm was run; its predicted fillers are recorded as timed events with source-clock boundaries. Event precision/recall and boundary quality remain unscored. |
| AUD-014 | Filler outputs remain observational; there is no automatic deletion. |
| AUD-015 | The incumbent remains available and no candidate is adopted on upstream claims. |
