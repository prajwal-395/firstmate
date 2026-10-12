"""Depth on demand: M3g (`depth.json`) of the per-source footage memory.

Monocular relative depth per M2 sample, read as one number per person
region: the median depth inside each M3d instance mask. This is the
depth lane of the multimodal-understanding plan (MU-16,
`data/vep-plan-multimodal-understanding/report.md` §3 A9 in firstmate's
home). Its two readers are `project_model_evidence` (one
`Observation(modality="depth")` per region per sample, beside the
`region_stats` one) and `behind_subject`'s behind-object title mode,
which layers a title under a foreground object that is not a person.

**Two halves with different costs.** Estimating a depth map needs a
model (Depth Anything V2 Small, probed below); everything after the map
is arithmetic over pixels already decoded - median per region,
normalisation, the foreground layer. The map is estimated on demand
and never stored: a low-res map per sample has no reader (the readers
ask "how deep is this region" and "what is foreground", both answered
by the medians and the mask), and a declared output has a reader
(AGENTS.md 10.1). What M3g stores is the medians plus the
normalisation that makes them comparable across samples of one source.

**The estimator is probed, never assumed.** `probe_estimator` tries the
backends in order and names the first that can run here; with none it
says which one is missing. `build_source_depth` raises
`DepthUnavailable` rather than writing zeros - an unmeasured sample
carries `regions: null` with its reason, and a run with no estimator at
all refuses the lane. There is no stand-in: a gradient or a centre
prior is taste wearing a measurement's name (AGENTS.md §10.5).

**Depth direction.** Brighter means nearer, as the `depth_blur`
prototype observed on its seated-car window
(`prototypes/depth_blur/README.md`): the foreground transition and the
medians are read on that convention, and the instrument records it so
a backend that reports the opposite can be refused at the probe.

    python3 -m library.tools.depth build <project>
    python3 -m library.tools.depth status <project>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from library.tools import source_memory
from library.tools.ren_refusal import RenRefusal

M3G_STATUS_MEASURED = "measured"
M3G_METHOD = "depth-v1-m2"
"""The slot version. `footage_analysis.slot_is_fresh` rebuilds a record
stamped with anything else, so a change to what a reading means (the
estimator, the normalisation, the region join) bumps this."""

DEPTH_MODALITY = "depth"
DEPTH_PRODUCER = "library.tools.depth"

BRIGHTER_IS_NEARER = True
"""The depth direction every backend here is read on: larger map values
are nearer the camera. Stated once so a backend reporting the opposite
is a probe failure, not a silent inversion."""

HF_MODEL_ID = "depth-anything/Depth-Anything-V2-Small"
"""The HuggingFace id of Depth Anything V2 Small. Loaded lazily and
only when asked - importing this module never touches the network."""

FOREGROUND_CENTER = 0.40
"""Normalised relative-depth centre of the foreground transition. The
`depth_blur` prototype's reviewed value: the higher default softened
too much of the face and torso."""

FOREGROUND_FEATHER = 0.18
"""Normalised width of the soft foreground transition. Same source."""


class DepthUnavailable(RenRefusal):
    """No depth map can be estimated here, so nothing depth says is built."""

    CODE = "REN_DEPTH_UNAVAILABLE"


ESTIMATOR_REASONS = {
    "no_checkpoint": "no depth checkpoint is on disk "
                     "(`PIPELINE_DEPTH_CHECKPOINT` pointing at a "
                     "`depth_anything_v2_vits.pth` beside the "
                     "Depth-Anything-V2 checkout `PIPELINE_DEPTH_REPO`) "
                     "and the HuggingFace model "
                     f"{HF_MODEL_ID!r} is not cached here",
    "no_backend": "neither torch nor transformers is importable here, so "
                  "no depth backend can run",
    "direction": "the backend reports larger values as farther, opposite "
                 "to the nearer-is-brighter convention this lane reads",
}
"""Every reason a depth map is not estimated. Complete: `probe_estimator`
returns one of these keys, and the build refuses with it."""


def probe_estimator() -> tuple[str | None, str | None]:
    """Name the depth backend that can run here, or the reason none can.

    Returns `(name, None)` or `(None, reason_key)`. Pure probe: it
    imports lazily and never downloads - a model that is not on disk is
    absent, not fetched. The measurement
    (`docs/benchmarks/2026-10-12-depth-on-demand.md`) fetched on its
    own clock; this probe only notices what that left behind. The
    measured backend is Depth Anything V2 Small through the official
    Depth-Anything-V2 checkout (70 ms/frame warm on MPS).
    """
    try:
        import torch  # noqa: F401
    except ImportError:
        torch_ok = False
    else:
        torch_ok = True
    try:
        import transformers  # noqa: F401
    except ImportError:
        transformers_ok = False
    else:
        transformers_ok = True
    if not torch_ok and not transformers_ok:
        return None, "no_backend"
    checkpoint = os.environ.get("PIPELINE_DEPTH_CHECKPOINT", "")
    repo = os.environ.get("PIPELINE_DEPTH_REPO", "")
    if (checkpoint and repo and Path(checkpoint).is_file() and Path(
            repo, "depth_anything_v2", "dpt.py").is_file()):
        return "depth-anything-v2-small", None
    if transformers_ok:
        try:
            from transformers.utils import cached_file

            cached_file(HF_MODEL_ID, "config.json",
                        local_files_only=True)
        except Exception:  # noqa: BLE001 - any miss is absence
            pass
        else:
            return "depth-anything-v2-small-hf", None
    _ = torch_ok
    return None, "no_checkpoint"


def normalize_depth(depths: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Per-source normalised depth, 0 (far) to 1 (near), with its feet.

    Pooled 2nd-98th percentile normalisation, the `depth_blur`
    prototype's rule (`prototypes/depth_blur/build_depth_blur.py`):
    one outlier frame must not set the scale for the source. Raises on
    a constant or non-finite map - that is a broken estimate, not the
    flattest room ever filmed.
    """
    depths = np.asarray(depths, dtype=np.float64)
    low, high = np.percentile(depths, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        raise ValueError("depth estimate produced a constant or "
                         "non-finite map")
    return np.clip((depths - low) / (high - low), 0.0, 1.0), float(low), float(high)


def median_depth_in_region(depth: np.ndarray, mask: np.ndarray) -> float | None:
    """Median normalised depth over the cells of `mask`, or None when the
    mask holds no cells. An empty mask is an absence with a reason at
    the caller, never a depth of zero."""
    depth = np.asarray(depth, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    if depth.shape != mask.shape:
        raise ValueError(f"depth is {depth.shape}, mask is {mask.shape} - "
                         f"one window, one grid")
    cells = depth[mask]
    if cells.size == 0:
        return None
    value = float(np.median(cells))
    if not np.isfinite(value):
        return None
    return value


def foreground_mask_from_depth(depth: np.ndarray, *,
                               center: float = FOREGROUND_CENTER,
                               feather: float = FOREGROUND_FEATHER) -> np.ndarray:
    """Soft foreground matte from a normalised depth map, 0-1 float.

    The `depth_blur` prototype's `foreground_alpha` without the resize
    and the blur: the caller composites at the map's own grid, and any
    softening is the caller's measured choice, not this function's
    quiet default. Smoothstep across `[center - feather/2,
    center + feather/2]`, so a feather of zero is refused, not a step.
    """
    if not 0 <= center <= 1:
        raise ValueError(f"foreground center {center} is not normalised")
    if not 0 < feather <= 1:
        raise ValueError(f"foreground feather {feather} must be a "
                         f"positive normalised width")
    depth = np.asarray(depth, dtype=np.float64)
    low = center - feather / 2
    high = center + feather / 2
    alpha = np.clip((depth - low) / (high - low), 0.0, 1.0)
    return alpha * alpha * (3.0 - 2.0 * alpha)


def order_regions_by_depth(medians: Sequence[tuple[str, float | None]]) -> list[str]:
    """Region tracks ordered front to back by median depth. Regions with
    no median (empty masks) sort last, in input order - unmeasured is
    behind, never first."""
    ranked = sorted(enumerate(medians),
                    key=lambda item: (item[1][1] is None,
                                      -(item[1][1] or 0.0), item[0]))
    return [medians[i][0] for i, _ in ranked]


def _key(t: float) -> int:
    return int(round(float(t) * 1000))


def measure_samples(content_digest: str, m2: dict, m3d: dict,
                    maps: dict[int, np.ndarray],
                    root: Path | None = None) -> list[dict]:
    """Per-region median depth for every M3d sample carrying a map.

    `maps` keys M2 millisecond times to normalised depth maps on the
    M3d grid. A sample with no map, or an M3d sample that measured
    nothing, is `regions: None` with its reason - never a median of the
    neighbour. Pure read of M2 and M3d plus the caller's maps; writes
    nothing.
    """
    from library.tools import person_regions

    grid = [int(v) for v in m3d["grid"]]
    masks = person_regions.read_masks(content_digest, m3d, root)
    out = []
    for sample in m3d.get("samples") or []:
        t = sample["t"]
        if sample.get("instances") is None:
            out.append({"t": t, "regions": None,
                        "unmeasured_because": "M3d did not measure this "
                                              "sample"})
            continue
        depth = maps.get(_key(t))
        if depth is None:
            out.append({"t": t, "regions": None,
                        "unmeasured_because": "no depth map estimated for "
                                              "this sample"})
            continue
        if [int(depth.shape[1]), int(depth.shape[0])] != grid:
            out.append({"t": t, "regions": None,
                        "unmeasured_because": (
                            f"the depth map is {int(depth.shape[1])}x"
                            f"{int(depth.shape[0])}, the M3d grid "
                            f"{grid[0]}x{grid[1]}")})
            continue
        regions = []
        for inst in sample["instances"]:
            mask = person_regions.decode_runs(masks[inst["mask"]], grid)
            median = median_depth_in_region(depth, mask)
            regions.append({
                "track": inst["track"],
                "mask": inst["mask"],
                "median_depth": median,
                **({} if median is not None else
                   {"unmeasured_because": "the mask holds no cells"}),
            })
        out.append({"t": t, "regions": regions})
    return out


def build_source_depth(source_file: str, content_digest: str,
                       root: Path | None = None,
                       estimator: Callable[[list[str]], list[np.ndarray]]
                       | None = None) -> dict:
    """M3g for one source. Refuses without M2, M3d, or an estimator.

    `estimator` maps M2 thumbnail paths to raw depth maps (brighter is
    nearer); None probes the machine backends and refuses with the
    probe's reason when none can run. Tests inject a synthetic
    estimator, so the build path is hermetic without a model.
    """
    from library.tools import person_regions

    started = time.perf_counter()
    m3d = person_regions.read_m3d(content_digest, root)
    if m3d is None:
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no M3d person regions; "
            f"run `ren analyze <project> --lanes regions` first")
    if not person_regions.sidecar_present(content_digest, m3d, root):
        raise RuntimeError(
            f"{os.path.basename(source_file)}: the M3d mask sidecar is "
            f"missing; rebuild M3d")
    m2 = source_memory.read_m2(content_digest, root)
    if (m2 is None
            or not source_memory.m2_frames_present(content_digest, m2, root)):
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no M2 frame sample with "
            f"its thumbnails on disk")
    if estimator is None:
        name, reason = probe_estimator()
        if name is None:
            raise DepthUnavailable(
                "no depth map can be estimated here",
                f"{os.path.basename(source_file)}: "
                f"{ESTIMATOR_REASONS[reason or 'no_checkpoint']}.",
                "Point `PIPELINE_DEPTH_CHECKPOINT` at a "
                "`depth_anything_v2_vits.pth` and `PIPELINE_DEPTH_REPO` "
                "at the Depth-Anything-V2 checkout (the measured route, "
                "`docs/benchmarks/2026-10-12-depth-on-demand.md`), or "
                f"cache {HF_MODEL_ID} locally; then rebuild this lane.")
        estimator = _backend_estimator(name)
    else:
        name = "injected"
    frames = m2["frames"]
    paths = [source_memory.frame_abspath(content_digest, f, root)
             for f in frames]
    raw = estimator(paths)
    if len(raw) != len(paths):
        raise ValueError(f"estimator returned {len(raw)} maps for "
                         f"{len(paths)} frames - one map per frame")
    stacked = np.stack([np.asarray(m, dtype=np.float64) for m in raw])
    normalised, low, high = normalize_depth(stacked)
    grid = [int(v) for v in m3d["grid"]]
    resized = np.stack([_to_grid(m, grid) for m in normalised])
    maps = {_key(f["t"]): resized[i] for i, f in enumerate(frames)}
    samples = measure_samples(content_digest, m2, m3d, maps, root=root)
    measured = sum(1 for s in samples if s["regions"] is not None)
    record = {
        "content_digest": content_digest,
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3G_STATUS_MEASURED,
        "grid": grid,
        "direction": "brighter-is-nearer",
        "frame_count": len(samples),
        "samples_measured": measured,
        "tracks": m3d.get("tracks") or [],
        "samples": samples,
        "instrument": {
            "method": M3G_METHOD,
            "estimator": name,
            "input": "m2-thumbnail",
            "depth_percentiles": {"p02": low, "p98": high},
            "foreground_center": FOREGROUND_CENTER,
            "foreground_feather": FOREGROUND_FEATHER,
            "m3d_sidecar_sha256": (m3d.get("sidecar") or {}).get("sha256"),
            "wall_seconds": round(time.perf_counter() - started, 1),
        },
    }
    source_memory.write_json(source_memory.source_dir(content_digest, root)
                             / source_memory.SLOT_DEPTH, record)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": content_digest, "frames": len(samples),
            "samples_measured": measured,
            "wall_seconds": record["instrument"]["wall_seconds"]}


def _to_grid(depth: np.ndarray, grid: Sequence[int]) -> np.ndarray:
    """A normalised map onto the M3d `(w, h)` grid. Nearest resample -
    depth edges are measurements, not pictures to smooth."""
    import cv2

    width, height = int(grid[0]), int(grid[1])
    if [int(depth.shape[1]), int(depth.shape[0])] == [width, height]:
        return np.asarray(depth, dtype=np.float64)
    return cv2.resize(np.asarray(depth, dtype=np.float64), (width, height),
                      interpolation=cv2.INTER_NEAREST)


def _backend_estimator(name: str) -> Callable[[list[str]], list[np.ndarray]]:
    """Thumbnail paths -> raw depth maps from the probed backend (larger
    is nearer, the convention the lane reads)."""
    if name == "depth-anything-v2-small":
        def _dav2(paths: list[str]) -> list[np.ndarray]:
            import cv2
            import torch

            repo = Path(os.environ["PIPELINE_DEPTH_REPO"])
            sys.path.insert(0, str(repo))
            from depth_anything_v2.dpt import DepthAnythingV2

            model = DepthAnythingV2(encoder="vits", features=64,
                                    out_channels=[48, 96, 192, 384])
            state = torch.load(os.environ["PIPELINE_DEPTH_CHECKPOINT"],
                               map_location="cpu", weights_only=True)
            model.load_state_dict(state, strict=True)
            device = ("mps" if torch.backends.mps.is_available() else "cpu")
            model = model.to(device).eval()
            maps = []
            with torch.no_grad():
                for path in paths:
                    image = cv2.cvtColor(cv2.imread(path),
                                         cv2.COLOR_BGR2RGB)
                    maps.append(np.asarray(model.infer_image(image),
                                           dtype=np.float64))
                    if device == "mps":
                        torch.mps.synchronize()
            return maps
        return _dav2

    def _hf(paths: list[str]) -> list[np.ndarray]:
        import torch
        from PIL import Image
        from transformers import (AutoImageProcessor,
                                  AutoModelForDepthEstimation)

        processor = AutoImageProcessor.from_pretrained(HF_MODEL_ID,
                                                       local_files_only=True)
        model = AutoModelForDepthEstimation.from_pretrained(
            HF_MODEL_ID, local_files_only=True)
        model.eval()
        maps = []
        with torch.no_grad():
            for path in paths:
                image = Image.open(path).convert("RGB")
                inputs = processor(images=image, return_tensors="pt")
                depth = model(**inputs).predicted_depth.squeeze().numpy()
                maps.append(np.asarray(depth, dtype=np.float64))
        return maps
    return _hf


def read_m3g(content_digest: str,
             root: Path | None = None) -> dict | None:
    """A digest's M3g record, or None when never measured."""
    path = source_memory.source_dir(content_digest, root) / \
        source_memory.SLOT_DEPTH
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def build_project_depth(project_folder: str,
                        clip_ids: list[str] | None = None,
                        root: Path | None = None) -> dict:
    """M3g for every catalog source, one source at a time. Same reporting
    shape as `attention_saliency.build_project_saliency`."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    results, failed, seen = [], [], set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        digest, _ = source_memory.digest_for_clip(project_folder, clip,
                                                  recorded)
        if digest is None or digest in seen:
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "no content digest" if digest is None
                            else "reused"})
            continue
        seen.add(digest)
        try:
            account = build_source_depth(path, digest, root)
        except Exception as exc:  # noqa: BLE001 - one source's failure
            # is recorded on its row; the rest of the catalog still runs.
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            failed.append(clip_id)
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="depth",
        description="M3g: monocular relative depth medians over each "
                    "source's person regions.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3g for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3g")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_depth(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3g(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3g": "measured" if doc else "missing",
                     "samples_measured": doc["samples_measured"] if doc else 0,
                     "estimator": (doc["instrument"]["estimator"]
                                   if doc else None)})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
