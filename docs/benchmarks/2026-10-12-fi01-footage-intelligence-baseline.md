# FI-01 footage-intelligence baseline (2026-10-12)

Plan item FI-01: benchmark corpus manifest + fidelity/efficiency metric
harness. Verdict first: **the harness is built and the baseline is
measured.** `bench_footage_intelligence --corpus <manifest>` over a
scratch memory root reproduces **240/240 metrics** within the
manifest's own tolerance (rel=1e-9, abs=1e-12), and the 5 absent corpus
categories are reported by name on every run.

- Harness: `library/tools/analysis/bench_footage_intelligence.py`
- Corpus manifest: `docs/benchmarks/2026-10-12-fi01-corpus-manifest.json`
- Machine-readable report: `docs/benchmarks/2026-10-12-fi01-baseline-report.json`
- Measured on code revision: `AD27452+` (branch `fm/vep-fi01`; the exact
  commit sha is in the PR description - see "Provenance" below)

## Method (what was and was not run)

- **Read-only over existing lane outputs.** The 7 GEO podcast sources
  already carry M0/M1/M2/M3/M3b/M6/M7 (+M8 on six, +M4 on one) in the
  shared source memory. The harness read those files and computed every
  metric deterministically. **No lane was rebuilt**: a fresh full-lane
  build over 132 GB of 4K MXF (two sources over an hour each) was out
  of scope for this turn with `scripts/full_suite_gate.sh` live on the
  machine and sibling lanes active. Media was read-only throughout.
- **Fresh deterministic measurement (deferred, see section).**
  `picture_quality.sample_sharpness` was NOT run fresh: the
  full-suite gate held the machine for the whole session and the
  brief orders heavy ffmpeg work to stop and report while another
  heavy vep process is live. Double-read reproducibility over the
  stored lane files stands in its place, labelled as such.
- **Scratch-root acceptance.** The 7 digest directories were mirrored
  into a scratch root and the harness re-run with
  `--memory-root <scratch> --check <baseline>`: 240/240 within
  tolerance, exit 0. The scratch mirror used symlinks to the read-only
  lane files (zero copy, identical stat sizes), so the check proves
  path-independence and tolerance conformance, not a rebuild.
- No model was loaded by the harness itself (plan §12F: deterministic
  approximations only). Retrieval recall uses the lexical token-overlap
  half of `ren search`'s hybrid path; probes are derived from each
  source's own utterance vocabulary (document-frequency band computed
  from the transcripts at hand).

## Corpus: 7 sources, 181.58 video minutes

| Clip | Digest | Duration | Size | M6 group |
|---|---|---|---|---|
| LCATL0013 | 15752299 | 4099.6 s (68 min) | 50.2 GB | 82532ce9 |
| LC4932 | dca969b8 | 4941.1 s (82 min) | 60.0 GB | 82532ce9 |
| LCATL0014 | f683a975 | 838.0 s (14 min) | 10.3 GB | 82532ce9 |
| LCATL0012 | 4fa282ce | 289.0 s | 3.5 GB | daa4bb0d |
| LC4931 | c1121815 | 287.6 s | 3.5 GB | daa4bb0d |
| LC4930 | 9b7fd11e | 225.2 s | 2.7 GB | 71933672 |
| LCATL0011 | d19007c3 | 214.5 s | 2.6 GB | 71933672 |

Three measured M6 clock groups cover all 7 sources. All 7 carry
transcripts (2854 utterances total), M3 faces and M7 events.

## Plan §9 categories: 2.5 covered, 5.5 absent

| Category | Status | Basis |
|---|---|---|
| Podcast/interview | covered (7) | M1+M3+M7 on all 7 |
| Multicamera podcast | covered (7) | 3 M6 groups above |
| Noisy or quiet audio | partly (7) | near-silent ISO CH2 at -71..-75 dB vs program CH1 at -29..-34 dB on every source; no noisy-environment material |
| Static classroom | absent | no such footage under the declared roots (scout survey) |
| Times Square/street | absent | no continuous high-motion footage under the declared roots |
| Product footage | absent | only incidental objects in podcast frames |
| Cinematic B-roll | absent | shared assets hold sfx/music/remotion, not footage |
| Complex action | absent | podcast gestures are the closest proxy |

## Fidelity (§10A approximations)

Per-source M7-vs-M8 (candidates = the M7 proposal set M8 judges;
precision = yes/matched, judgment coverage = judged spans/all spans,
boundary error over yes-verdicts matched by any temporal overlap):

| Clip | Cand spans | Verdicts | Precision | Judged coverage | Boundary err (n, mean s) |
|---|---|---|---|---|---|
| LCATL0013 | 213 | 639 | 0.049 | 1.00 | 31, 2.67 |
| LC4932 | 323 | 323 | 0.046 | 1.00 | 15, 3.57 |
| LCATL0014 | 31 | 93 | 0.118 | 1.00 | 11, 0.32 |
| LC4931 | 33 | 33 | 0.061 | 1.00 | 2, 0.38 |
| LC4930 | 26 | 26 | 0.077 | 1.00 | 2, 0.38 |
| LCATL0012 | 13 | 39 | 0.000 | 1.00 | 0, - |
| LCATL0011 | 0 | 0 | unmeasured | unmeasured | 0, - |

Identity consistency vs the M3b roster: 1 face track + 1-2 voice tracks
per source, every face carrying an embedding; M7 face-assignment rate
0.977-1.000; speaking spans with a face 0.35-1.00 (LC4930 lowest at
0.35 over 17 spans); every voice track face-linked except one
LCATL0013 voice (0.50). LCATL0011 has no candidates and no verdicts,
so its event cells are unmeasured, not zero.

Retrieval recall (lexical probes across M6 groups, top-3):
**0.571 over 21 testable probes** (12/21; every probe testable since
all 7 sources have group mates). Approximation of `ren search`'s
lexical half; the embedding half is not measured here.

Coverage (measured / measured-absent / unmeasured, never defaulted):
M0/M1/M2/M3/M3b/M6/M7 measured on all 7; M8 measured on 6,
unmeasured on LCATL0011 (no verdicts file); M4 measured on LCATL0011
only (22 scene windows), unmeasured elsewhere; M1b speakers, M5
sound, M3c expressions, M3d regions, M3e saliency, M3f region stats,
M9 world model and M10 relationships unmeasured on all 7 - zero files
on disk, matching the scout. No recorded refusals
(`untranscribed-*`, `no-faces-detected`) appear in this corpus.

## Efficiency (§10B)

| Metric | Baseline | Basis |
|---|---|---|
| Wall time per source minute | 0.0018 s | harness read pass only (0.3 s over 181.58 min) - NOT a lane-build cost |
| Model calls per source hour | 381 M8 verdict calls; 7.3 M4 windows | 1153 recorded verdicts + 22 scene windows / 3.03 h, counted from files |
| Decode time | unmeasured | lane builds record no decode split; the fresh double-decode pass is deferred (section above) |
| Peak RSS | 56 MB | harness read pass only |
| Cache-hit rate | unmeasured | no analysis_run.json at hand; refused to report zero |
| Bytes per source hour | 247 MB | 748 MB of slot files (M2 JPEGs dominate) / 3.03 h |

## Sharpness reproducibility (fresh double decode - DEFERRED)

Not measured this turn: `scripts/full_suite_gate.sh` held the machine
under the heavy-work lock for the whole session (two consecutive runs,
pids 72930 then 88466), and the brief orders heavy ffmpeg work to stop
and report while another heavy vep process is live. The decode-path
evidence that DID ship:

- transcript word timings re-read twice inside every `run_corpus`
  (2854 utterances, max abs diff 0.0 on real data);
- 240/240 metrics reproduced over the scratch mirror within tolerance;
- `bench.reproducibility` unit-pinned against real drift.

Follow-up: run `picture_quality.sample_sharpness` twice over
LCATL0011.MXF behind `heavy_work_lock run --owner vep-fi01` on a quiet
machine and append the max/mean abs diff, wall seconds and peak RSS
here. No harness change needed - `bench.reproducibility` takes both
payloads directly.

## Open calls touched (decided nothing)

- C1 recall tolerance: the harness reports deltas with no promotion
  bar; the only tolerance is the float-repr bound on the mechanical
  check, stated in the manifest.
- C2 50-80% target: not encoded; cost deltas are reported, never
  judged.
- C3 SQLite vs sidecars: the harness reads the existing JSON sidecars
  and adds no store.
- C4 demand-driven ingest: ingest defaults untouched.
- C5 evaluation thresholds: none shipped; every new facet a later item
  adds must cite this baseline, not a constant.
- C6 missing footage: the 5 absent + 1 partly categories are named in
  the manifest and printed on every run; FI-08's static-vs-busy proxy
  must cite the "podcast gestures" row above.

## Provenance and reproduction

- Code revision measured on: see PR description (branch `fm/vep-fi01`
  head at measurement time; manifest carries `measured_head_sha`).
- Reproduce: `python3 -m
  library.tools.analysis.bench_footage_intelligence --corpus
  docs/benchmarks/2026-10-12-fi01-corpus-manifest.json --memory-root
  <root with the 7 digests> --check
  docs/benchmarks/2026-10-12-fi01-baseline-report.json` exits 0 within
  the manifest tolerance and names every absent category.
- Scratch-root proof this turn: 240/240 within tolerance over a
  7-digest mirror, exit 0.
