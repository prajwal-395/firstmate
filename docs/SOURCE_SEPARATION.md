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
