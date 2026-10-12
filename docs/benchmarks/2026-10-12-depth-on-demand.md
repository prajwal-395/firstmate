# Depth on demand (MU-16), measured 2026-10-12

`library/tools/depth.py` adds monocular relative depth as one median
per person region (M3g, `Observation(modality="depth")`), and
`behind_subject.apply_behind_object` layers a title under a tracked
foreground object that is not a person. This doc is the item's first
acceptance check: what Depth Anything V2 Small costs on the captain's
Mac, and the verdict that follows.

Machine: captain's Mac (M5, 24 GiB), shared with other lanes - these
are loaded-machine numbers. Backend: Depth-Anything-V2 `vits`
(Depth Anything V2 Small), checkpoint
`depth_anything_v2_vits.pth` (99,218,434 bytes) from
`depth-anything/Depth-Anything-V2-Small` @ `03876f8`, run through the
official `DepthAnything/Depth-Anything-V2` repo (cloned 2026-10-12) on
torch MPS. The transformers id of the same name is checkpoint-only
(no config/processor), so there is no transformers route; the
Core ML/MPS distinction in the plan item resolves to torch-on-MPS,
which is what was measured. The Video-Depth-Anything-Small checkout
the `depth_blur` prototype uses was not re-measured: the image model
answers the per-frame maps this lane needs.

## Estimator cost (the model half)

8 synthetic 518 px frames (near box on noise), MPS, `torch.no_grad`:

| input | cold first call | warm median | warm p90 | per played second @24fps |
|---|---|---|---|---|
| 518x518 (from 960x540) | 0.64 s | **70.2 ms** | 73.0 ms | **1.69 s** |
| 518x518 (from 384x216 thumb) | - | 71.8 ms | - | - |

Load: 0.3 s. The model resizes to 518 internally whatever it is fed,
so M2 thumbnails cost the same per frame as 960 px inputs - the lane
reads the thumbnails M2 already decoded and pays no second decode.

## Direction check (the convention half)

`BRIGHTER_IS_NEARER`: larger map values are nearer. Read-only over 5
M3 frames of a real source already in source memory (faces as the
known-foreground anchor):

| t | face median | frame median | face nearer |
|---|---|---|---|
| 0.08 | 1.98 | 1.60 | yes |
| 0.58 | 2.10 | 1.56 | yes |
| 1.08 | 2.01 | 1.60 | yes |
| 1.58 | 1.98 | 1.60 | yes |
| 2.08 | 1.76 | 1.50 | yes |

5/5. A texture-only synthetic probe was confounded (fine noise reads
near) and is not evidence; the faces above are. The prototype's
brighter-is-nearer observation on its car window agrees.

## Deterministic half (the arithmetic after the map)

48 synthetic 1080p frames, numpy only, per played frame:

| step | per frame |
|---|---|
| median over 2 regions | 20.7 ms |
| foreground smoothstep | 16.9 ms |
| precomp punch (shared with the subject path) | 59.1 ms |
| total | **~96.7 ms (~2.3 s per played second)** |

Pooled 2nd-98th percentile normalisation is per source, one-off
(3.35 s for the 48-frame source).

## Verdict: affordable on demand, survey at the M2 cadence

- **Delivery (per played `ClipUse`): yes.** ~1.7 s of estimator plus
  ~2.3 s of arithmetic per played second at 24 fps, 1080p - the same
  shape as the person-matte delivery tier (~1 s per played second).
  A behind-object title over a seconds-long window costs seconds.
- **Survey (whole source): at the M2 grid only (~2 Hz),** where the
  medians live: 8,191 frames x 70 ms is about **9.5 minutes per
  68-minute source**, one source at a time, cached by content digest.
  Full-frame-rate survey (24 fps x 70 ms = 1.7 s per source second,
  ~1.9 h for that source) is not the design: nothing reads depth
  between M2 samples.
- **Stored:** medians plus the normalisation, never the maps. A map
  has no reader; the medians answer "how deep is this region" and the
  foreground mask is recomputed for the delivery window that needs it.

## Reproduce

```sh
mkdir -p /tmp/mu16_depth
curl -sL -o /tmp/mu16_depth/depth_anything_v2_vits.pth \
  "https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth"
git clone --depth 1 https://github.com/DepthAnything/Depth-Anything-V2.git \
  /tmp/mu16_depth/Depth-Anything-V2
bin/vep /tmp/mu16_dav2_bench2.py   # estimator cost (needs the venv torch)
bin/vep /tmp/mu16_numpy_bench.py   # deterministic half
```

Bench scripts are task scratch in `/tmp`, outside the worktree.
The export fixture (second acceptance check) is
`test_the_export_shows_a_foreground_object_occluding_the_title`
in `tests/unit/picture/test_depth.py`: a laptop matte (not a person)
under a white title, flattened by ffmpeg the way Resolve renders
premultiplied Full - the object box reads the plate (128), everywhere
else the title (255).
