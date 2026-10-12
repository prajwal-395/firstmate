"""MU-12 benchmark: time step 1.04's index_clip on one clip, under one code tree.

Run once per clip per code tree (a `git archive <rev> library` export),
interleaving the trees; `2026-10-12-mu12-temporal-index-vision.md` has
the driver loop and the result.

argv: tree_root clip_path clip_id cached_index wav out_json
Speech is supplied from the clip's cached 1.04 index (transcription is
batched outside index_clip and untouched by MU-12). The audio models (PANNs,
audio classification) are stubbed: identical in both trees, and noise. Everything on the picture side runs for real.
"""
import json, os, sys, time
tree, clip, clip_id, cached, wav, out = sys.argv[1:7]
sys.path.insert(0, tree)
os.chdir(tree)
from library.steps.step_1_04_temporal_index import step as ti
from library.tools.project_layout import ProjectLayout
timings = {"decode_s": 0.0, "vision_s": 0.0}
real_decode = ti.decode_temporal_media
def timed_decode(*a, **k):
    t = time.perf_counter(); r = real_decode(*a, **k)
    timings["decode_s"] += time.perf_counter() - t; return r
ti.decode_temporal_media = timed_decode
vm = getattr(ti, "vision_measure", None)
if vm is not None and hasattr(vm, "measure_clip_vision"):
    real_vision = vm.measure_clip_vision
    def timed_vision(*a, **k):
        t = time.perf_counter(); r = real_vision(*a, **k)
        timings["vision_s"] += time.perf_counter() - t; return r
    vm.measure_clip_vision = timed_vision
ti.measure_clip_sound_events = lambda _a: ([], "unmeasured: bench stub")
ti.measure_audio_classification = lambda *a, **k: {
    "categories_measured": [], "method": "unmeasured: bench stub"}
idx = json.load(open(cached))
pre = (wav, idx.get("onset_times") or [], [], idx["speech_regions"],
       {"aligner": "cached", "arm": "cached", "detected_language": "en"})
project = os.path.join(os.path.dirname(out), "proj_" + clip_id + "_" + os.path.basename(tree))
os.makedirs(project, exist_ok=True)
t0 = time.perf_counter()
index = ti.index_clip(clip, clip_id, ProjectLayout(project), pretranscribed=pre)
total = time.perf_counter() - t0
json.dump({"clip_id": clip_id, "tree": os.path.basename(tree), "total_s": total,
           **timings, "vision_engine": (index.get("vision_method") or {}).get("engine"),
           "vision_samples": (index.get("vision_method") or {}).get("samples"),
           "vision_keys": sorted(k for k in index if k.startswith("vision_")),
           "face_samples": len(index["face_presence"]["values"])},
          open(out, "w"))
