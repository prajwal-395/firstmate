# Estimated audio source signals

`library/tools/analysis/source_separation.py` exposes cached HTDemucs
separation, a deterministic synthetic C6 proxy fixture, SI-SDR and bleed
measurements, and project-model signal metadata. It does not overwrite the
source recording. Derived artifacts are floating-point WAV files; each
Project Model Signal carries its source signal id, artifact reference, and
`estimate_status: "estimated"` separately from `processing_status`.

HTDemucs is music-trained. Its `vocals` stem is surfaced as
`dialogue_candidate`, not verified dialogue; its non-vocal stems are summed
as an estimated music accompaniment. The model cannot isolate ambience or
identify individual speakers, so those signals are reported unsupported and
are not synthesized from a coarse stem. Neither output is evidence for an
edit-critical choice without the required source-specific validation.

Model inference must use the existing checkpoint and the 8 GB model-memory
profile. The C6 proxy compares each separated candidate with the original
mixture. It passes only when both SI-SDR improves and bleed falls for each
candidate.

Inventory for this run: `demucs-infer==4.2.2` and the HTDemucs
`955717e8` checkpoint were already installed (`80 MB` in the Torch Hub
cache, with a matching cached Hugging Face snapshot). Inference points at
the local Torch Hub directory, so it does not fetch weights. The cached
ECAPA model is for speaker identity embeddings, and the larger generic
Torchaudio `model.pt` checkpoint is a Wav2Vec model; neither separates
audio sources.

The fixture is a known, generated three-stem mix (harmonic dialogue proxy,
tonal music proxy, seeded noise ambience proxy). SI-SDR is measured against
each clean stem. Bleed is the least-squares projected energy of the labeled
non-target stems divided by the target-stem energy in the estimate. This is
a synthetic proxy only: no licensed annotated multitrack mixtures were
available, so real-footage accuracy remains unmeasured.

## C6 result (2026-10-13)

The cached model ran on CPU under `music.analyse:model_memory` (8 GB
ceiling). Checkpoint size was 84,141,911 bytes and observed process peak RSS
was 1,887,698,944 bytes (about 1.76 GiB). Before means the original mixture;
after means HTDemucs' estimated vocals candidate or the sum of its non-vocal
stems.

| Candidate | Before SI-SDR | After SI-SDR | Before bleed | After bleed |
|---|---:|---:|---:|---:|
| Dialogue candidate | -0.572 dB | -18.112 dB | 0.569 dB | 6.943 dB |
| Music candidate | 0.425 dB | 0.357 dB | -0.420 dB | -0.363 dB |

**C6 failed:** both candidates regressed on SI-SDR and bleed. The outputs
remain explicitly estimated candidates, not usable edit-critical separation.
The model cannot separate ambience or individual speakers, and accuracy on
real footage remains unmeasured. The machine-readable record is
[`2026-10-13-source-separation-c6.json`](benchmarks/2026-10-13-source-separation-c6.json).

Run the model-backed proxy only through the scheduler:

```sh
bin/vep -m library.tools.heavy_work_lock run \
  --owner vep-vi16 --profile music.analyse:model_memory -- \
  bin/vep -m library.tools.analysis.source_separation \
  --out /tmp/vi16-c6.json
```

## Query-triggered separation and candidate runtimes (2026-10-15)

Separation remains an explicit user-selected capability. `music.analyse`
skips stems unless requested; there is no measured routine-processing benefit
that justifies changing that default. HTDemucs is music-trained, and its
`vocals` output is still only a dialogue candidate.

The matched-runtime comparison used the same cached HTDemucs weights
(`955717e8`), six-second C6 fixture, 44.1 kHz input, FP32 weights, zero
shifts, 0.25 overlap, four named stems, and float WAV serialization. The
candidate package versions were `demucs-mlx==1.4.8` and
`mlx-audio-separator==0.1.20`; the latter runs the demucs-mlx implementation.
The current machine's baseline CPU pass was much slower than the earlier run,
so timings are specific to these process conditions. Warm medians and measured
differences were:

| Runtime | Warm inference | Complete separation | Peak process RSS | Peak MLX memory | Dialogue SI-SDR delta vs baseline | Dialogue bleed delta vs baseline |
|---|---:|---:|---:|---:|---:|---:|
| `demucs-infer==4.2.2` CPU baseline | 73.11 s | 73.18 s | 1.54 GiB | - | - | - |
| `demucs-mlx==1.4.8` | 1.93 s | 1.96 s | 207 MiB | 1.94 GiB | +0.21 dB | -0.09 dB |
| `mlx-audio-separator==0.1.20` | 0.67 s | 0.76 s | 257 MiB | 2.84 GiB | +0.02 dB | +0.04 dB |

Stem waveforms from both MLX paths were close to the CPU output. The package
wrapper was faster on this one calibration fixture, with higher recorded MLX
peak memory. Neither candidate repaired the synthetic dialogue proxy: all
three vocal SI-SDR values were about -18 dB. These are technical comparisons,
not editorial-quality judgments. No held-out footage or blind agent labels
are included yet, so candidate decisions await that validation. See the
[direct MLX comparison](benchmarks/2026-10-15-demucs-mlx-c6.json),
[package comparison](benchmarks/2026-10-15-mlx-audio-separator-c6.json),
and the modality-specific evaluation reports.

### Model eligibility and alternatives

The implementation license does not establish the license of each downloaded
checkpoint. Demucs and demucs-mlx code are MIT licensed and the compared
HTDemucs checkpoint is publisher-provided under the Demucs MIT license.
`mlx-audio-separator` is MIT licensed, but its MDX, MDXC, VR, and Roformer
catalog entries are not cleared by that code license. No such weights were
downloaded or benchmarked in this lane. Treat each exact checkpoint as
**unconfirmed / ineligible for shipped use** until its model card, license,
training-data terms, and attribution requirements are reviewed. Catalog
presence or community SDR scores do not establish commercial rights. The
audio-separator project itself warns that model licenses vary.

| Family | Candidate status | Reason |
|---|---|---|
| MDX / MDXC | Hold | Exact checkpoint and training-data terms not reviewed; no matched mixture results. |
| VR | Hold | Exact checkpoint and training-data terms not reviewed; no matched mixture results. |
| Roformer | Hold | Exact checkpoint and training-data terms not reviewed; no matched mixture results. |
| HTDemucs | Eligible under its published license | Same publisher checkpoint used by the existing runtime; runtime parity and real-footage utility still require qualification. |

`mlx-audio-separator` demonstrated that a single MLX package can expose all
four families, but its HTDemucs run added no demonstrated quality advantage
over direct `demucs-mlx`. Its package decision is research-only. No alternate
MDX-family overlap or difficult-mixture benchmark is claimed here because no
eligible exact checkpoint was established and the C6 fixture is not such a
mixture.

### demucs-mlx 1.1.0 calibration on Apple 001

On 2026-10-17, the PyPI 1.1.0 release was compared with the incumbent on the
same 20-second IMG_1808 calibration span, using the official HTDemucs
checkpoint signature `955717e8`, FP32 weights, 44.1 kHz stereo, zero shifts,
0.25 overlap, and the same four stems. The candidate completed separation in
1.43 seconds versus 11.20 seconds for the CPU incumbent (7.83x faster on this
span). Warm inference was 1.38 versus 11.17 seconds. Aligned float32 output comparisons yield difference SNR of 28.26 dB for bass,
28.89 dB for drums, 17.29 dB for `other`, and 38.64 dB for vocals. Maximum
absolute sample differences range from 0.01015 to 0.01260. None meets the
requested 40 dB per-stem numerical-equivalence threshold, so the incumbent is
retained and 1.1.0 is not adopted as the default. The `other` stem diverges
most. These are waveform-parity measurements, not separation-quality
judgments.

A scheduled calibration diagnostic applied the incumbent wrapper's waveform
normalization before MLX inference and restored its mean and scale afterward.
Vocal parity rose to 48.11 dB, while the summed bass/drums/other residual
measured 35.07 dB; `other` remained at 17.96 dB. Both measured runs used FP32,
zero shifts, and 0.25 overlap. The two implementations use matching triangular
weighted overlap-add formulas, so precision and overlap-add settings do not
explain the remaining `other` difference. The exact MLX operator responsible
has not been isolated. The reference `other` stem is quiet (0.00244 RMS), and
its aligned error is 0.000333 RMS (12.65% relative L2).

Caller inspection found that `music.analyse` calls `music_pipeline.separate_stems`
and exposes all four stem files. No in-repository downstream reader of those
files was found. The separate `source_separation.separate_waveform` helper
returns only vocals plus the summed residual, but is called by the synthetic
C6 benchmark and tests only. The active four-stem operation therefore retains
the incumbent while `other` still diverges; the two-output helper has no
production caller to receive a runtime change.

The scheduler recorded an 8.286 GB peak physical footprint for the candidate
under an 8.5 GB reservation; MLX reported 2.77 GB peak memory and process RSS
was 0.27 GB. The 1.1.0 runtime code is MIT at its attested PyPI release commit.
The exact converted checkpoint artifact remains quarantined in the license
registry pending the license lane's reconciliation. See the [calibration
scorecard](benchmarks/2026-10-17-demucs-mlx-1.1.0-calibration.json) and [span
provenance](benchmarks/2026-10-17-demucs-mlx-1.1.0-calibration-span.json).

### Dialogue enhancement and transient calibration

Desert Ant Clear and DeepFilterNet were run on one generated five-second
speech/music/noise mixture with a 4 kHz, 10 ms click. The target was locally
generated TTS, not podcast or Apple 001 footage. SI-SDR is aligned to the
speech target; DeepFilterNet's 30 ms output delay is included in that
alignment. Clear improved SI-SDR from 5.11 to 7.94 dB (+2.83 dB), with click
projection retention of 0.832 (-1.60 dB). DeepFilterNet improved it to 15.31
dB (+10.20 dB), with click retention of 0.018 (-34.71 dB). This fixture
shows a speech-versus-transient trade-off, not general source separation or a
real-footage preference. Clear's publisher describes it as speech
enhancement and explicitly treats music and effects as noise. Both tools
remain query-triggered dialogue cleanup options, not replacements for source
separation. The generated Clear model used `clear-studio` revision `v0.3.0`
(8.7 MB installed model bundle); it was removed after the run.

DeepFilterNet's code/model is MIT or Apache-2.0. Clear's model license is
source-available, with commercial terms depending on monthly active users and
attribution requirements; it is not an OSI-approved open-source license.
The [speech enhancement evaluation report](benchmarks/2026-10-15-dialogue-enhancement-c6.json)
records the measurements and limits of this fixture.

### Prompt and visual conditioning

SAM Audio supports text, visual, and temporal prompts in its published API,
but its published inference path is CUDA-first and its checkpoint requires
gated Hugging Face access. This Apple Silicon machine has no CUDA runtime; no
checkpoint was downloaded and no prompt-separation measurement is claimed.
Visual prompting (identify a sound source from video, then isolate its audio)
remains unmeasured. The source-separated audio capability currently has no
video-conditioned selector. Revisit with an approved, runnable checkpoint and
blind agent labels on the bounded podcast and Apple 001 footage.
