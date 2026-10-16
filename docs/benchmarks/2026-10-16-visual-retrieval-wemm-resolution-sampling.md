# Visual retrieval model upgrade: WeMM-2B, resolution, sampling, rerank (2026-10-16)

Lane: vep-mu-retrieval (RET-001..026). Machine numbers live in
[`2026-10-15-visual-retrieval-model-upgrade.json`](2026-10-15-visual-retrieval-model-upgrade.json);
this note carries the decisions and what is still missing.

## Incumbent and protocol (unchanged)

CLIP ViT-L/14, held-out macro recall 0.1473 / precision 0.20
(`2026-10-14-visual-retrieval.md`). Frozen sets reused read-only; nothing
tuned on held-out. Pilot limit stands: 4 present + 3 negative held-out
queries from one synchronized-camera group (45-frame held-out pool).

## What was measured

| Candidate / test | Held-out Recall@1/5/10 | Decision |
|---|---|---|
| CLIP ViT-L/14 (baseline) | 0.0278 / 0.1389 / 0.2779, P 0.75 | retain baseline |
| PE-Core-S16-384 @384 | 0.0278 / 0.1498 / 0.2996, P 0.75-0.8 | research-only |
| PE-Core-S16-384 @224 | 0.0278 / 0.1498 / 0.3214, P 0.75-0.85 | research-only |
| WeMM-2B 2048d (BF16 MPS) | 0.0273 / 0.1365 / 0.2729, P 0.75 | research-only |
| WeMM-2B 256d (MRL) | identical to 2048d on held-out | research-only |
| Rerank CLIP<->PE (depth 20/50) | converges to stronger single model | no rerank |
| CLIP 5 s stride (88 frames) | 0.0223 / 0.0846 / 0.174 | retain 10 s |
| Pooled 3-still triples (PE-S) | Hit@10 1.0 both ways; Recall not comparable (14 vs 45 units) | no evidence clips beat stills |

WeMM-2B calibration (5 queries): full dim recall 0.003/0.0102/0.017,
256d falls to 0.0008/0.0038/0.0076 - the 256d saving (8.95 MB to
1.12 MB float32, 87.5%) is real, the retention is set-dependent.
Tencent's 98.7% claim is MMEB-v2, not this footage.

WeMM-4B: not run - 2B tied CLIP at 2.7x storage and ~3 s/query text
latency, so the conditional (RET-008) fails.

Thresholds: gaps measured (CLIP 0.0794, PE 0.0322), none shipped -
calibration has no negatives and held-out negatives are eval-only (RET-021).

PE-AV: pinned download + native transformers load verified, but the
upstream `get_*` embed getters are broken and the forward probe OOMed
under parallel-lane contention. Quality eval needs audio-grounded
labels that do not exist yet.

## Adoption (EVAL-032)

- Commercial first-stage: **retain CLIP ViT-L/14**. No candidate shows a
  measured improvement that survives the pilot caveats (ARCH-004/005).
- Personal research candidates: PE-Core S/B/L, WeMM-2B (needs adapter +
  precision work first). No production integration, no default changed.

## Downloads (captain approval 2026-10-15, all deleted after benchmarking)

- tencent/WeMM-Embedding-2B @ bbd6cd4b (5.44 GB bf16) - was at
  `~/.cache/vep-mu-retrieval/models--tencent--WeMM-Embedding-2B/`
- facebook/PE-Core-S16-384 @ aabf3b99 (349 MB) - same cache root
- facebook/pe-av-small @ dd050762 (3.39 GB fp32) - same cache root
- Adapter: facebookresearch/perception_models @ 3e352cc (kept in cache
  for reruns; code, not weights) + timm/ftfy/regex in cache pydeps.

## Still pending (needs firstmate agent labels or calmer machine)

RET-002/003/004/018 expanded set + negatives + opposites + temporal
recall; RET-014 event-aligned intervals; RET-020 calibrated thresholds;
RET-022 hybrid (needs speech-anchored queries); WeMM true video-encoder
run; PE-AV forward numbers. Label request stands at
`data/vep-mu-retrieval/label-request.md` (agent labellers, not human).

## Reproduce (read-only; project and media untouched)

```sh
export PYTHONPATH="$HOME/.cache/vep-mu-retrieval/perception_models:$HOME/.cache/vep-mu-retrieval/pydeps"
bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-retrieval \
  --profile semantics.analyse:inference -- \
  bin/vep library/tools/visual_retrieval_candidate_benchmark.py \
  --project "$HOME/Documents/content_stuff/video_projects/lucie/geo-podcast" \
  --model WeMM-2B --set library/tools/visual_retrieval_heldout_set.json \
  --output /tmp/wemm-2b-heldout.json
```

Pipe stdout to a file, never through `tail` into another process: a
piped heavy run wedged the lock wrapper twice (child gone, wrapper at
0% CPU, `release of a job that is not running` on the next run).
`--save/--load-image-matrix` splits the ~50 min VLM encode from fast
scoring reruns. Do not run 3B+ fp32 forwards while other lanes hold
gigabytes: they OOM (exit 241) at ~60 MB free.
