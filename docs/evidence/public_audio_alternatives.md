# Public audio alternatives: qualification evidence

Status: neither candidate qualified as a full public-edition replacement. No runtime or dependency changes were made.

## Audit claims checked on `origin/main`

Checked against `origin/main` at `df9e9434` on 2026-10-04.

- The Audio Flamingo Next captioner is released under NVIDIA's OneWay Noncommercial License and its model card says it is for non-commercial research only. The audit claim holds. This model is used by `library/tools/analysis/sfx_pipeline.py` for Ren's SFX profiles. [NVIDIA model card](https://huggingface.co/nvidia/audio-flamingo-next-captioner-hf)
- `praat-parselmouth` is GPL version 3 or later. The license claim holds. GPL is a distribution and copyleft question, rather than a blanket prohibition on commercial use. [Parselmouth license](https://github.com/YannickJadoul/Parselmouth)
- Prosody is currently wired into the edit-video process: `prosody_analysis` feeds `creative_direction` and `speech_sequence` in `library/processes/edit_video/dag.json`. The prosody step declares the `prosody_analysis` output. Therefore, any statement that this capability is currently unwired does not hold on this `origin/main`. `docs/PROSODY_MEASURED.md` still describes the earlier unwired state and is stale on this point.

## Sound-effect captions

### Candidate and method

Candidate: `mlx-community/Qwen2-Audio-7B-Instruct-4bit` at revision `c65570002626f41b4dc08b7b54f42f99f3e82e7f`, run with MLX-Audio on Apple Silicon. The upstream Qwen2-Audio model and this quantized snapshot declare Apache-2.0; MLX-Audio declares MIT. These are commercially permissive component licenses, subject to normal provenance and notice review for the exact converted snapshot. [Pinned quantized model card](https://huggingface.co/mlx-community/Qwen2-Audio-7B-Instruct-4bit/blob/c65570002626f41b4dc08b7b54f42f99f3e82e7f/README.md), [upstream Qwen2-Audio model card](https://huggingface.co/Qwen/Qwen2-Audio-7B-Instruct), [MLX-Audio license](https://github.com/Blaizzy/mlx-audio/blob/main/pyproject.toml)

Compared eight existing SFX-library assets against their saved Audio Flamingo profile descriptions. The assets cover impacts, a riser, camera sounds, ambience, a mechanical sound, and foley; durations range from 0.46 to 76.14 seconds. Qwen used the exact prompt from `sfx_pipeline.py`, mono 16 kHz audio, the first 30 seconds at most, no padding for shorter clips, temperature 0, and a 200-token limit. Audio Flamingo's saved descriptions were not regenerated for this comparison; its current path pads clips to 30 seconds. A separate Qwen pass padded every clip to 30 seconds, matching Audio Flamingo preprocessing, and produced the same key misidentifications. Padding does not explain those disagreements. This is a small, hand-selected sample, not a benchmark or blinded listening study; no independent source labels or listening-based ratings were collected. Table assessments describe agreement and differences from the saved Audio Flamingo descriptions.

The quantized model loaded in 23.5 seconds and generated the eight captions in 100.2 seconds total (12.2 seconds median). Total elapsed time, including model load, was 126 seconds after weights were already cached. A separate full-precision Transformers attempt on this Mac did not produce a first caption after more than seven minutes, so the quantized MLX build was the practical candidate.

| Asset | Saved Audio Flamingo description | Qwen 4-bit description | Assessment |
|---|---|---|---|
| `Burst_3.wav` | Metallic ping with a short resonant decay | Burning flame with a long release | Major sound-source mismatch; Qwen adds a phone-recording claim absent from the saved baseline. |
| `Bass_riser.wav` | Deep swell followed by metallic impact and boom | Slow-down sweep, silence, then electronic effect | Less detail and a materially different event sequence. |
| `camera flash.wav` | Repeated camera clicks and mechanical whir | Camera shutter being opened and closed | Both identify a camera; Qwen is less specific about the sequence. |
| `camera soft click.wav` | Sustained low-frequency electronic tone | Kick, snare, open hat, and crash cymbal | Strong disagreement: Qwen supplies several musical elements where the saved baseline reports one sustained tone. |
| `Freeway_overpass.wav` | Ominous drone, metallic clank, rumble, electronic tone | Rising screech, described as an abandoned house sound | Shares a horror mood, but replaces the layers in the saved baseline with a different event and setting. |
| `Eject Vhs.wav` | Printer mechanism and motor hum | Printer press and moving paper | Qwen repeats the printer interpretation and does not clarify this ambiguous mechanical clip. |
| `Plastic_01.wav` | Thin plastic wrapper crinkling | Paper or plastic crumpling | Broadly aligned; Qwen adds a phone-recording claim absent from the saved baseline. |
| `Paper_crinkle_04.wav` | Glitch burst followed by electronic hum | Quiet paper handling and folding | Qwen is closer to the filename hint, while Audio Flamingo describes a different sound. No independent ground truth was available. |

**Decision:** Qwen is not good enough as a drop-in replacement. It matches or improves a few broad categories but makes conspicuous source and event errors on short clips, and its captions are less detailed on the riser and ambience examples. Keep the candidate as an evaluated option, not a public-edition selection. The gap is reliable, grounded identification across short and ambiguous SFX.

## Prosody measurements

### Candidate and method

Candidate: `librosa.pyin`, already available through Ren's declared `librosa` dependency. Librosa is under the ISC license; pYIN estimates fundamental frequency and voiced frames. [librosa license](https://github.com/librosa/librosa/blob/main/LICENSE.md), [pYIN API](https://librosa.org/doc/0.11.0/generated/librosa.pyin.html)

Both methods analyzed the same first 30 seconds of `clip_011.wav` from Ren project `001`, using 10 ms hops and an F0 range of 75 to 600 Hz. Praat values came from Ren's current `analyze_prosody` path. This one-clip comparison measures method agreement, not independent ground-truth accuracy.

| Measurement | Current Praat/Parselmouth | librosa pYIN | Comparison |
|---|---:|---:|---|
| Mean F0 | 110.25 Hz | 101.93 Hz | Different summary pitch. |
| Median F0 | 106.70 Hz | 101.86 Hz | 40 cents median absolute error on jointly voiced frames; 182.7 cents at p90. |
| Voiced frames | 40.0% (1,199 frames) | 74.7% (2,240 frames) | Voiced-mask Jaccard agreement: 46.5%. |
| Relative intensity contour | Praat scale | centered RMS | Pearson correlation 0.962; absolute scales are not comparable. |
| Jitter / shimmer / HNR | 0.06324 / 0.22736 / 7.0 dB | Not measured | No replacement for three voice-quality fields. |

Ren's `view:prosody` keeps the voice-quality measurements and the pitch statistics in the context used by planning steps; it filters the raw pitch contour out of the prompt. pYIN can produce analogous F0 summaries and voicing, and RMS can track relative intensity, but this clip's voiced-frame disagreement does not support substituting its pitch statistics. pYIN also does not cover jitter, shimmer, or HNR.

**Decision:** librosa pYIN is not a complete or sufficiently agreeing replacement for the public path. The gap is voice-quality feature coverage plus closer voiced-frame agreement. Do not remove or route around Praat based on this evaluation alone.
