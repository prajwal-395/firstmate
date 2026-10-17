# ASR and alignment lane: current implementation and qualification state

Date: 2026-10-15. Scope: AUD-001..015. Media scope for the eventual
qualification remains the cleared GEO podcast and Apple 001 originals in
`docs/benchmarks/2026-10-15-editorial-eval-corpus.json`; originals are
read-only. Existing test fixtures remain synthetic/unit evidence, not footage
claims.

## Current implementation

Ren already retains Voz as the ASR baseline and uses MFA for forced alignment.
The transcriber is contained by `library/tools/heard_speech.py` (`da voz`),
then `library/tools/hybrid_transcription.py` passes Voz's words to
`library/tools/mfa_align.py`. The transcript seam is
`library/tools/timeline_transcript.py`; source memory and temporal indexing
also use the canonical `library/tools/transcript_measurement.py` path. The
reported Voz spans are not authoritative cut boundaries: MFA re-places them
and the transcript keeps the source-audio timebase.

The local machine reports `da` 0.1.1, Desert Ant Core 3.1.0, arm64 macOS,
Core ML available. Voz cache is `desert-ant-labs/voz/v0.1.0`, whose publisher
tag resolves to Hub commit `8e7ab8ca342c6bb00f9347a25e53fe44543b2a40`.
The cached artifact is the compiled Core ML build; the upstream model card
identifies Voz as converted and compressed Parakeet TDT 0.6B v3 with otherwise
unchanged weights. Voz's runtime license is the Desert Ant Labs
Source-Available License, which is free for most apps and requires a
commercial license at scale. The upstream Parakeet weights carry CC BY 4.0.
Treat these as separate artifacts and obligations.

MFA is pinned in code and the model registry: Montreal Forced Aligner 3.4.2,
English US ARPA acoustic and dictionary v3.0.0, G2P v2.0.0a. Its executable
is isolated in the shared micromamba environment. The existing comparison
against the former wav2vec2 aligner measured speed and timing agreement, not
an accuracy win: `docs/MFA_ADOPTION_VERIFIED.md` and
`docs/evidence/transcription.md` contain the record. Do not treat that
historical WhisperX disagreement as ground truth.

## Capability audit

| Item | Existing state | Remaining qualification |
|---|---|---|
| AUD-001 | Voz is the baseline and current implementation. | Preserve it while comparing candidates. |
| AUD-002 | Local CLI/runtime and publisher Voz tag are now recorded above. | Model registry has no Voz row yet; that schema and registry are owned by `vep-mu-registry`, so this lane will not edit them. |
| AUD-003 | Original NVIDIA Parakeet TDT 0.6B v3 is CC BY 4.0 and has an upstream NeMo runtime path. It is a separately runnable distribution, but it is not an independent set of model weights: Voz is a conversion of the same checkpoint. | Compare original F32 inference to Voz's shipped Core ML precision/runtime and disclose that shared base. |
| AUD-004 | Existing speech scorer groups samples by condition and split. | Need blind agent labels on clean, naturally noisy, multilingual and overlapped audio; do not infer absent conditions or use upstream leaderboard scores as local results. |
| AUD-005 | WER/CER, insertions, deletions and substitutions are scored. Punctuation was stripped for WER and previously had no separate metric. | New scorer reports punctuation precision/recall/F1 over exactly matched words. Agent-labelled comparison is pending. |
| AUD-006 | Voz timestamps are followed by MFA placement; evaluation already measures absolute start/end errors against a reference and the alignment tests preserve window offsets. | Candidate boundary-error report against blind agent labels is pending. |
| AUD-007..009 | MFA is installed and the only production aligner. Its executable and model pins are documented. | Desert Ant Align now supports arbitrary supplied transcripts and cross-platform Node/Linux runtimes in current upstream docs, with model revision v1.1.0; it is no longer limited to Apple's SpeechAnalyzer. Compare it on the same words/audio windows and include setup, runtime dependencies, and full end-to-end latency. |
| AUD-010..011 | `da ear` already runs on speech windows selected from Voz's word timings. The hybrid rejects unsupported MFA languages; it does not silently force English alignment. | Ear's model output and abstention behavior need blind evaluation on these two collections, including whether a language mix is present. |
| AUD-012..014 | `speech_take.disfluencies_in` emits filler/partial records with start/end seconds. `project_model_evidence` writes `speech.disfluency` measurements. This is an observed transcript projection; the `speech_take` producer does not delete words. The local `da info uhm --json` pins Uhm weights to `612592c10ad7b2a51f3237725448a1aad212480b` and reports Apple/Core ML runnable, not downloaded. | Uhm could add audio-grounded detections independent of ASR, but its precision/recall and boundaries require labels. Any removal remains a separate editing policy. |
| AUD-015 | Existing code already keeps the incumbent behind a contained transcriber and an injected aligner interface. | No replacement/default change without a local scorecard against Voz + MFA. |

The scorer changes in this lane are isolated to the benchmark tool and its
unit tests; no canonical transcript, project model, or capability schema was
changed.

## Candidate evidence and preliminary decisions

Decision vocabulary follows EVAL-032. Until local blind-label quality and
runtime results exist, all candidates remain research-only for qualification;
this describes their current integration status and does not mean their
licenses are research-only.

| Candidate | Published evidence (not a Ren measurement) | Decision |
|---|---|---|
| Voz v0.1.0 + MFA | Existing Ren run history and reports; no clean, paired agent-label bakeoff yet. | **retain baseline** |
| Original Parakeet TDT 0.6B v3 | NVIDIA describes multilingual ASR, timestamps and CC BY 4.0. It shares the base weights used by Voz. | **research-only** pending paired quality and resource measurement |
| Desert Ant Align v1.1.0 | Publisher reports a 0.7 MB model package, 9 languages and boundary comparisons against machine estimates; number timing is a stated weak case. Its current docs say arbitrary transcript refinement and Node/LiteRT support. The installed CLI says Align is not runnable here and reports a floating `main` revision, so a reproducible test must use the separate current SDK/package pinned to a commit. | **research-only** pending comparison with MFA on Ren source speech |
| Desert Ant Ear v0.1.0 | Already present on this machine. Publisher reports 12/12 ordinary recordings and 8/10 podcast rebuilds confident/correct among confident cases; loud music can still mislead the model. | **research-only** pending local language/abstention evaluation |
| Desert Ant Uhm | The public product page claims filler spans within 20 ms and one hour per 12 seconds; this is vendor-reported and not independently reproduced here. | **research-only** pending label-based event precision/recall and timing |

## Scorecard status

Machine measurements for newly downloaded candidate weights: none. No Parakeet
or Align files were downloaded during this initial audit. Existing cached models
were not changed. A detailed JSON scorecard with explicit unmeasured fields is
at `docs/benchmarks/2026-10-15-asr-candidate-scorecards.json`.

Agent labels are required before the held-out comparison. The label request is
in the lane data directory. It asks for two independent blind labelers from
different model families plus a third tie-breaker, with agreement reported;
no candidate output is to be shown to them. Calibration results choose settings;
held-out results are to be reported once.

## Source references

- Voz checkpoint/runtime, published language and accuracy claims, model
  ancestry, and license: <https://huggingface.co/desert-ant-labs/voz> and
  <https://github.com/Desert-Ant-Labs/desert-ant-core/blob/main/docs/models/voz.md>.
- Voz publisher tag to commit mapping: Hub refs API at
  <https://huggingface.co/api/models/desert-ant-labs/voz/refs>.
- Local Desert Ant CLI model metadata: `da info <model> --json` (read-only).
- Original Parakeet checkpoint card, language coverage and CC BY 4.0 terms:
  <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>.
- Desert Ant Align current API, versions and model behavior:
  <https://github.com/Desert-Ant-Labs/desert-ant-core/blob/main/docs/models/align.md>.
- Desert Ant Ear model and limitations:
  <https://github.com/Desert-Ant-Labs/desert-ant-core/blob/main/docs/models/ear.md>.
- Desert Ant audio product claims:
  <https://desertant.com/audio/>.
- Existing Ren baseline evidence: `docs/benchmarks/SPEECH_EVAL.md`,
  `docs/MFA_ADOPTION_VERIFIED.md`, `docs/evidence/transcription.md`.
