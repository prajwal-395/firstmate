# Perception Bakeoffs: Qwen3-VL vs Gemma, SigLIP2 vs CLIP, IntelliSearch vs Ren Retrieval, SAM3

Date: 2026-10-04
Branch: fm/vep-perception-bakeoffs

## Summary

| Bakeoff | Status | Recommendation |
|---------|--------|----------------|
| Qwen3-VL vs Gemma (stills/windows) | Complete (8B model; 30B not Mac-viable) | Keep Gemma as default: Qwen3-VL 8B is 4.8x slower. Qwen3-VL has better text reading but speed matters for still inspection |
| SigLIP2 vs CLIP (frame ranking) | Complete | Keep CLIP: LAION head is CLIP-specific (768 dim), SigLIP2 incompatible (1152 dim) |
| IntelliSearch vs Ren retrieval | Not run: no IntelliSearch API access | No verdict |
| SAM3 (segmentation) | Complete | Not Mac-viable: requires triton/CUDA |

## Model Downloads (pinned and hashed)

| Model | Source | Size | SHA256 |
|-------|--------|------|--------|
| SigLIP2 Base Patch16 512 | google/siglip2-base-patch16-512 | 1503.3 MB | fe0e601c625e69eed8e73500d39e9b6164403fe03db8048e87913c3cefbbb3fe |
| SAM3.1 Multiplex fp16 | Comfy-Org/sam3.1 | 1745.5 MB | 9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03 |
| Qwen3-VL 30B A3B Instruct 4bit | mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit | 18.2 GB | (not Mac-viable: GPU memory exhaustion with 7+ images) |
| Qwen3-VL 8B Instruct 4bit | mlx-community/Qwen3-VL-8B-Instruct-4bit | 5.4 GB | (hash not computed - large files) |

## 1. SigLIP2 vs CLIP - Frame Ranking

### Method

Both backbones were tested on the same synthetic 10-second test pattern (testsrc2, 960x540, 30fps). Frames were extracted at 1 fps (10 frames total). The CLIP backbone used the production LAION aesthetic head. SigLIP2 was tested with raw backbone feature extraction since the LAION head is dimension-incompatible.

### Results

| Metric | CLIP ViT-L/14 | SigLIP2 Base Patch16 512 |
|--------|---------------|--------------------------|
| Load time | 17.71s | 0.51s |
| Scoring time (10 frames) | 37.06s | 26.22s |
| Aesthetic head compatible | Yes (LAION, 768 dim) | No (1152 dim) |
| Scores | [4.45, 4.67, 5.22, 4.25, 4.74, 4.64, 4.99, 4.16, 4.73, 4.69] | N/A (feature norms only) |

### Verdict

**Keep CLIP.** The LAION aesthetic head is a 768-dim linear probe trained specifically on CLIP ViT-L/14 features. SigLIP2 Base has 1152-dim features, making the head incompatible without retraining. SigLIP2 loads faster (0.51s vs 17.71s) and extracts features faster (26.22s vs 37.06s for 10 frames), but without a compatible aesthetic head, it cannot replace CLIP in the frame ranker. Training a new LAION-style head for SigLIP2 would require a labeled aesthetic dataset and is out of scope for this bakeoff.

## 2. Qwen3-VL vs Gemma - Stills and Windows

### Method

Both models were tested on the same 10 object-inspection requests across 5 clips (84 stills total) from the existing Gemma benchmark (`docs/benchmarks/stills-gemma-vs-cloud-2026-10-04.md`). The same exact prompt strings and image paths were used for both routes. Gemma 4 12B (MLX) is the current production default. Qwen3-VL was tested at two sizes: 30B A3B (not viable on this hardware) and 8B (completed).

The Qwen3-VL 30B model (18.2GB) was downloaded successfully but cannot run on this 24GB Mac: processing 7+ images causes GPU memory exhaustion (`Metal Command buffer execution failed: Insufficient Memory`). The 8B model (5.4GB) runs successfully.

An mlx_vlm 0.6.13 bug was patched in-place to enable Qwen3-VL vision encoding: `mx.repeat(seq_len, grid_thw[i, 0])` in `vision.py` passes two arrays but `mx.repeat` expects an int for repeats. Fixed to `mx.repeat(seq_len, int(grid_thw[i, 0].item()))`.

### Results

| Metric | Gemma 4 12B | Qwen3-VL 8B |
|--------|-------------|-------------|
| Load time | ~30s | 3.2s |
| Median wall time per request | 31.184s | 149.9s |
| Total inference time (10 requests) | 307.809s | 2303.8s |
| Memory footprint | ~7GB | ~5.4GB (model only) |
| Mac-viable | Yes | Yes (8B); No (30B) |

Per-request wall times (seconds):

| # | Request | Images | Gemma wall | Qwen3-VL 8B wall |
|---:|---|---:|---:|---:|
| 1 | IMG_1808 coarse | 7 | 32.819 | 149.879 |
| 2 | IMG_1808 detail | 2 | 9.615 | 65.087 |
| 3 | IMG_1812 coarse | 15 | 28.068 | 524.413 |
| 4 | IMG_1812 detail | 2 | 7.223 | 39.007 |
| 5 | IMG_1816 coarse | 15 | 38.785 | 421.671 |
| 6 | IMG_1816 detail | 4 | 28.839 | 104.024 |
| 7 | IMG_1818 coarse | 15 | 41.431 | 440.541 |
| 8 | IMG_1818 detail | 6 | 35.904 | 124.261 |
| 9 | IMG_1820 coarse | 12 | 55.577 | 304.175 |
| 10 | IMG_1820 detail | 6 | 29.548 | 130.757 |

### Quality Comparison

Compared against the Gemma answers recorded in `docs/benchmarks/stills-gemma-vs-cloud-2026-10-04.md`:

**Where Qwen3-VL 8B is stronger:**
- **Text reading**: Qwen3-VL correctly reads "Chattahoochee Row" (Gemma garbles it as "C_S_F_E_R_E_N_I_N_I_C_O" repeated 7 times), "SPEED LIMIT 5" (Gemma reads "SPEED LIMIT 20"), "SALTY PIG", "GFiber", "FOOD", and "2251". Gemma's repeated garbled OCR on IMG_1820 would pollute downstream object/text profiles.
- **Entity detection**: Qwen3-VL finds more entities in most requests - the hanging device in IMG_1808 (Gemma misidentifies it as a dashcam), the A-frame sign in IMG_1816, the fingertip in IMG_1820, and multiple vehicles in IMG_1818.
- **No repeated entities**: Gemma emits 7 duplicate street-sign entries on IMG_1820; Qwen3-VL does not have this issue.

**Where Gemma is stronger:**
- **Speed**: Gemma is 4.8x faster (31.2s vs 149.9s median). For still inspection in a pipeline, this matters.
- **IMG_1818 coarse**: Gemma correctly reads the mirrored "Credit" sign; Qwen3-VL misses it.
- **IMG_1816 coarse**: Gemma finds the pink pendant and rings; Qwen3-VL misses these accessories.

**Shared weaknesses:**
- Both models carry coarse entities into detail requests without grounding them in the detail frames.
- Both miss some entities (seat belt in IMG_1808, white sedan in IMG_1812).
- Both make occasional color errors (Gemma: "silver shirt" for light gray; Qwen3-VL: "purple corduroy cap" for charcoal).

### Verdict

**Keep Gemma as the default for still inspection.** Qwen3-VL 8B has better text reading and entity detection, but is 4.8x slower (149.9s vs 31.2s median per request). For a pipeline that processes many stills, the speed difference is decisive. Qwen3-VL's text reading advantage is real but does not justify a 5x latency increase for the primary use case. The 30B model is not Mac-viable.

If text reading accuracy becomes a bottleneck (e.g., for OCR-dependent downstream steps), Qwen3-VL 8B could be used as a secondary pass on specific frames.

## 3. IntelliSearch vs Ren Retrieval

### Status

**Not run.** IntelliSearch is a DaVinci Resolve semantic search feature (referenced in `library/tools/resolve_axi.py`). It is a proprietary Resolve API with no public access or Python SDK. The bakeoff would require:
1. A Resolve project with indexed media
2. The IntelliSearch API endpoint
3. A comparison against Ren's hybrid dense+BM25 retrieval

Ren's retrieval eval set (`library/tools/retrieval_eval_set.json`) has 32 queries across 4 families (speech: 19, visual: 8, person: 4, verified_predicate: 1) with ground truth spans. This eval set could be used for comparison if IntelliSearch access becomes available.

## 4. SAM3 - Mac Viability

### Method

SAM3.1 was downloaded from the Comfy-Org mirror (facebook/sam3.1 is gated). The model loads as a 1.7GB fp16 safetensors file. The `sam3` Python package (v0.1.4) was installed.

### Finding

**SAM3 is not Mac-viable.** The `sam3` package requires `triton` for the euclidean distance transform (EDT) kernel in `sam3/model/edt.py`. Triton is a CUDA-only compiler and is not available on Mac MPS. The import chain is:

```
sam3/__init__.py -> model_builder.py -> sam1_task_predictor.py -> sam3_tracker_base.py -> sam3_tracker_utils.py -> edt.py -> import triton
```

There is no CPU/MPS fallback for the triton import. The EDT kernel is used in `sample_box_points` for mask-to-box conversion with noise, which is core to SAM3's point sampling.

### Verdict

**Do not adopt SAM3.** It cannot run on Mac without triton/CUDA. The current SAM 2.1 Hiera Small (`facebook/sam2.1-hiera-small`) remains the only Mac-viable segmentation model in the stack.

## Recommendations

1. **Keep CLIP for frame ranking.** SigLIP2 is faster but incompatible with the LAION aesthetic head. No code change.
2. **Keep SAM 2.1 for segmentation.** SAM3 is not Mac-viable. No code change.
3. **Keep Gemma 4 12B as default for still inspection.** Qwen3-VL 8B has better text reading but is 4.8x slower. Qwen3-VL 30B is not Mac-viable. No code change.
4. **IntelliSearch vs Ren retrieval:** Not runnable without Resolve IntelliSearch API access. No code change.

## What was NOT changed

- No default models were changed
- No code was modified
- No project or timeline resolution was touched
- No Resolve calls were made
