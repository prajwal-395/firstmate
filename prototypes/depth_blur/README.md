# Offline depth blur prototype

This standalone experiment makes one full-resolution video from source footage and a broad relative-depth blur. It does not add a pipeline step, Fusion input, parallax, inpainting, or a subject matte. The depth map only drives a soft foreground/background blend, so hair, motion blur, and hand boundaries remain imperfect.

## Model and limits

- Model: Video Depth Anything Small, `depth-anything/Video-Depth-Anything-Small` at revision `256875362cff76724b920335dfb4b29dd611f66e`.
- Checkpoint SHA-256 is enforced by the script: `13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609`.
- The model is Apache-2.0. The output is source-resolution H.264; depth inference is resized to at most 960 pixels on its long edge, then the depth map is upscaled to the untouched source frames before compositing.
- Inference processes every source frame in the selected interval. `--input-size 518` and pooled 2nd-98th percentile depth normalization match the earlier usability scout.
- The foreground transition defaults to relative depth `0.40` with `0.18` feather after review showed the higher default softened too much of the face and torso. The full-frame hand and hair boundaries remain part of the result.
- The threshold assumes brighter relative depth means nearer, as observed in the selected seated-car window. This does not produce metric depth or a clean subject matte.

## Reproduce the selected 001 window

Use the dedicated `bin/vep` interpreter and the pinned scout checkout containing the VDA model source and checkpoint. The script adds the checkout's sibling `python-deps` directory when present. If a model-only dependency is missing, install it into a task-local directory and pass that directory with `--python-deps`; do not change the shared ML interpreter.

The scout checkout used for this run was missing the source for `easydict`, which Video Depth Anything imports. This task supplied it in an isolated evidence-folder dependency directory with `pip3 install --no-deps --target <model-deps-dir> easydict==1.13`.

```sh
bin/vep prototypes/depth_blur/build_depth_blur.py \
  --input '/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001/raw/IMG_1808.MOV' \
  --start-seconds 5 --duration-seconds 8 \
  --model-repo '/path/to/Video-Depth-Anything' \
  --checkpoint '/path/to/video_depth_anything_vits.pth' \
  --python-deps '/path/to/model-deps' \
  --device mps \
  --output '/path/outside/the/repo/IMG_1808_5-13s_depth-blur.mp4'
```

The output is silent. It has the source width, height, frame rate, and complete frame count for the chosen interval. Its adjacent `.metrics.json` records checkpoint identity, the per-frame and total inference time, total processing time, and the blur parameters. The result is intended to be imported into Resolve as ordinary media.

## Selection and comparison

The 001 scout rejected `IMG_1806.MOV` because its parking-lot view pans. This prototype uses `clip_003_mid`, `IMG_1808.MOV` from 5-13 seconds: the car roofline, window frame, headrest, and hanging device keep the same framing across the scout's 0.0, 2.6, 5.2, and 7.8-second views while the seated subject moves. The full interval is retained, including hands at the hairline and overhead hand poses.

In Resolve, place the original 5-13-second source segment first and this pre-rendered clip second on a new comparison timeline. Keep both full-frame and sequential so the soft hair and hand edges can be judged at native framing. Do not crop out the challenging poses. This experiment does not alter the pipeline DAG.
