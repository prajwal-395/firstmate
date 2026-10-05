# Public audio alternatives: qualification evidence

Status: LAION-CLAP qualified as the public-edition SFX profiler; praat-parselmouth qualified as public-optional for prosody. Both are wired behind the edition seam.

## Audit claims checked on `origin/main`

Checked against `origin/main` at `de9bf4d8` on 2026-10-04.

- The Audio Flamingo Next captioner is released under NVIDIA's OneWay Noncommercial License and its model card says it is for non-commercial research only. The audit claim holds. This model is used by `library/tools/analysis/sfx_pipeline.py` for Ren's SFX profiles. [NVIDIA model card](https://huggingface.co/nvidia/audio-flamingo-next-captioner-hf)
- `praat-parselmouth` is GPL version 3 or later. The license claim holds. GPL is a distribution and copyleft question, rather than a blanket prohibition on commercial use. [Parselmouth license](https://github.com/YannickJadoul/Parselmouth)
- Prosody is currently wired into the edit-video process: `prosody_analysis` feeds `creative_direction` and `speech_sequence` in `library/processes/edit_video/dag.json`. The prosody step declares the `prosody_analysis` output. Therefore, any statement that this capability is currently unwired does not hold on this `origin/main`. `docs/PROSODY_MEASURED.md` still describes the earlier unwired state and is stale on this point.

## Sound-effect captions

### Qwen2-Audio (previous evaluation, kept for context)

Candidate: `mlx-community/Qwen2-Audio-7B-Instruct-4bit` at revision `c65570002626f41b4dc08b7b54f42f99f3e82e7f`, run with MLX-Audio on Apache-2.0. Compared eight SFX-library assets against their saved Audio Flamingo profile descriptions. Qwen made conspicuous source and event errors on short clips and was less detailed on the riser and ambience examples. **Decision:** not good enough as a drop-in replacement. The gap is reliable, grounded identification across short and ambiguous SFX.

### AST and CLAP taggers (this evaluation)

Candidates: `MIT/ast-finetuned-audioset-10-10-0.4593` (BSD-3-Clause) and `laion/clap-htsat-unfused` (Apache-2.0). Both are audio taggers: they output AudioSet-style labels rather than free-text captions. AST is a spectrogram transformer fine-tuned on AudioSet; CLAP is a contrastive audio-text model used here for zero-shot classification over the 527 AudioSet labels. [AST model card](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593), [CLAP model card](https://huggingface.co/laion/clap-htsat-unfused)

Same eight SFX-library assets, same saved Audio Flamingo descriptions as baseline. AST ran at 16 kHz with its native feature extractor; CLAP ran at 48 kHz with zero-shot text prompts "This is a sound of {label}." over all 527 AudioSet labels. Both output top-5 labels with confidence scores. This is a small, hand-selected sample, not a benchmark; no independent ground truth was collected.

| Asset | Saved Audio Flamingo description (gist) | AST top labels | CLAP top labels | Assessment |
|---|---|---|---|---|
| `Burst_3.wav` | Metallic ping, short resonant decay | Explosion, Sound effect, Burst/pop | Fire, Splinter, Boom, Breaking | Both wrong: a burst/ping is not an explosion or fire. CLAP is closer (Boom/Breaking share the percussive character). |
| `Bass_riser.wav` | Deep swell, metallic impact, boom | Whoosh/swoosh, Music, Silence | Boom, Effects unit, Whoosh, Eruption | Both partial: they capture the swell/boom character but miss the metallic impact and the three-event sequence. |
| `camera flash.wav` | Repeated camera clicks, mechanical whir | Camera, SLR camera, Drill, Tools | Camera, SLR camera, Drill, Dental drill | Both correct: camera is the top label for both. |
| `camera soft click.wav` | Sustained low-frequency electronic tone | SLR camera, Camera, Sound effect | Finger snapping, Bicycle bell, Camera, Tick | Both wrong: a sustained tone is not a camera click or snapping. This is the hardest clip for taggers. |
| `Freeway_overpass.wav` | Ominous drone, metallic clank, rumble | Field recording, Vehicle, Whir, Car, Rumble | Traffic noise, Truck, Vehicle, Outside urban | CLAP correct: traffic/vehicle matches the freeway overpass. AST partial: vehicle/rumble are close but "field recording" is too generic. |
| `Eject Vhs.wav` | Printer mechanism and motor hum | Power windows, Printer, Door | Power windows, Printer, Car, Engine starting | Both partial: Printer appears in both top-5 but Power windows is the top label for both. The VHS eject mechanism is ambiguous. |
| `Plastic_01.wav` | Thin plastic wrapper crinkling | Crunch, Crack, Crumpling/crinkling | Crumpling/crinkling, Splinter, Crunch, Crushing | Both correct: Crumpling/crinkling appears in both. CLAP has it as top label. |
| `Paper_crinkle_04.wav` | Glitch burst, electronic hum | Crumpling/crinkling, Crunch, Tearing | Crumpling/crinkling, Rustle, Shuffle, Tearing | Both partial: they capture the crinkle but miss the electronic hum that dominates the second half. |

Summary: CLAP correctly identifies the sound source in 3/8 cases (camera flash, freeway overpass, plastic wrapper), partial in 3/8 (bass riser, VHS eject, paper crinkle), wrong in 2/8 (both short ambiguous clips: burst and camera soft click). AST correctly identifies 2/8, partial in 4/8, wrong in 2/8. CLAP is the stronger candidate. Both are significantly better than Qwen2-Audio, which had major source mismatches on 4/8 clips.

**Decision:** LAION-CLAP qualifies as the public-edition SFX profiler. It is commercially permissive (Apache-2.0), correctly identifies most sound sources, and provides a working capability where the public edition would otherwise have none. The gap vs Audio Flamingo is detail and nuance: CLAP outputs labels rather than descriptive captions, and it makes errors on short ambiguous clips. The public edition gets a functional but less detailed SFX profiler. Wired as `model.laion_clap` in `THIRD_PARTY_NOTICES` and dispatched in `sfx_pipeline.load_afnext_model`.

## Prosody measurements

### Permissive stack evaluation

Candidate: `librosa.pyin` (ISC) and `librosa.yin` (ISC) for F0, with manual jitter/shimmer/HNR implemented from first principles using `scipy` (BSD-3-Clause). Both librosa and scipy are already Ren dependencies.

Analyzed the same first 30 seconds of `clip_011.wav` from Ren project `001`. Praat values came from Ren's current `analyze_prosody` path. Jitter is mean absolute period difference / mean period over consecutive voiced frames; shimmer is mean absolute RMS difference / mean RMS over voiced frames; HNR is 10*log10(R(T0)/(R(0)-R(T0))) from the autocorrelation of a stable voiced segment.

| Measurement | Praat/Parselmouth | librosa pYIN | librosa YIN |
|---|---:|---:|---:|
| Mean F0 | 110.25 Hz | 101.93 Hz | 104.32 Hz |
| Median F0 | 106.69 Hz | 101.86 Hz | 101.14 Hz |
| Voiced frames | 40.0% (1,199) | 74.7% (2,241) | 100.0% (3,000) |
| Jitter (local) | 0.06324 | 0.01590 | 0.03068 |
| Shimmer (local) | 0.22736 | 0.03794 | 0.03721 |
| HNR | 7.0 dB | -4.6 dB | -1.1 dB |

The permissive stack does not clear the bar. Jitter disagrees by 52-75%, shimmer by 83%, and HNR by 116-166% (both HNR values are negative, indicating the autocorrelation method does not match Praat's harmonicity algorithm on this clip). The voiced-frame disagreement is fundamental: pYIN marks 74.7% as voiced where Praat marks 40.0%, and YIN marks 100%. The pitch statistics inherit this disagreement.

**Decision:** No permissive stack covers jitter, shimmer, and HNR at acceptable agreement with Praat. The gap is algorithmic: Praat's voice-quality measurements are based on its specific point-process and harmonicity implementations, which are not reproducible with permissive libraries at sufficient accuracy.

### Public-optional distribution model

Since no permissive alternative qualifies, praat-parselmouth is retained as a `public-optional` component:

- The public build does not bundle `praat-parselmouth` in its requirements (its `requirement-ref` rows are filtered from public builds, same as personal-only).
- The engine code that imports parselmouth (`library/tools/analysis/speech_advanced_pipeline.py`) is shipped in both editions.
- The user fetches and installs parselmouth separately (`pip install praat-parselmouth`).
- When parselmouth is absent, the prosody step raises `ProsodyUnavailable` and the pipeline continues without prosody measurements (already handled by the existing `except ImportError` path).

This distribution model resolves the GPLv3 concern: Ren does not distribute parselmouth in the public product; the user installs it under GPLv3 terms themselves. The edition mechanism is extended with a `public-optional` category that filters the dependency from public builds while allowing the code to load it when present.

Wired as `public-optional` in `THIRD_PARTY_NOTICES` and registered as the public provider in `speech_advanced_pipeline.analyze_prosody`.

## Wiring summary

| Capability | Personal edition | Public edition | Mechanism |
|---|---|---|---|
| SFX profiling | Audio Flamingo Next (personal-only) | LAION-CLAP (public) | `select_component` in `sfx_pipeline.load_afnext_model` |
| Prosody | praat-parselmouth (bundled) | praat-parselmouth (user-fetched) | `select_component` in `speech_advanced_pipeline.analyze_prosody`; `public-optional` filters the pip dependency from public builds |

The edition mechanism (`ren/edition.py`) is extended with `PUBLIC_OPTIONAL = "public-optional"`. Public-optional components are allowed to load in both editions but their `requirement-ref` packages are filtered from public builds. The packaging policy (`ren/package_engine.py`) treats public-optional like personal-only for requirement filtering.
