"""TMP-033..038 benchmark: reusable video features on calibration windows.

Reads the frozen window fixture (mu33_video_features_windows.py) and runs,
per subcommand, one producer over read-only calibration footage:

  incumbent  Farneback regional motion via library.tools.regional_motion
             (exact incumbent pipeline; face_presence=None recorded).
  vjepa      Official V-JEPA 2.1 ViT-B/16 384px encoder (publisher repo +
             publisher checkpoint; fp32; MPS).
  pe         Official PE-Core-S16-384 vision tower (publisher repo +
             publisher checkpoint; fp32; MPS).
  clip       Cached openai/clip-vit-large-patch14 (transformers; incumbent
             visual-similarity baseline).
  metrics    Aggregate per-window rows into frozen ren_evaluation_report/1
             files (one per version+modality, calibration split).

DINOv3 is NOT run here: its weights are HF-gated (manual approval, no
token on this machine). PE-Core-S is the available spatial-correspondence
reference; TMP-038 stays pending gated access.

Heavy steps run under heavy_work_lock by the caller. Outputs go to
--out-dir (scratch, outside the worktree).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # bench-only lazy ML imports; names for annotations only
    import numpy as np
    import torch

from library.tools.video_features.metrics import (
    average_precision as _average_precision,
)
from library.tools.video_features.metrics import (
    cosine as _cosine,
)
from library.tools.video_features.metrics import (
    precision_at_k as _precision_at_k,
)
from library.tools.video_features.metrics import (
    rank_auc as _auc,
)
from library.tools.video_features.metrics import (
    spearman as _spearman,
)

VJEPA_REPO = "/tmp/vep-mu-features-models/vjepa2-repo"
VJEPA_CKPT = "/tmp/vep-mu-features-models/vjepa2_1_vitb_384.pt"
PE_REPO = "/tmp/vep-mu-features-models/perception_models"
PE_CKPT = "/tmp/vep-mu-features-models/pe-core-s/PE-Core-S16-384.pt"
DEPS = "/tmp/vep-mu-features-deps"

CALIB_TAKES = ("IMG_1806", "IMG_1807", "IMG_1808", "IMG_1809", "IMG_1810",
               "IMG_1811", "IMG_1814", "IMG_1815", "IMG_1816",
               "LCATL0012", "LC4931")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def source_path(take: str) -> Path:
    home = Path.home() / "Documents"
    podcast = (home / "work_stuff/Lucie consulting/Social Media/podcast media")
    apple = home / "content_stuff/post a day keeps the apple away/001/raw"
    direct = podcast / f"{take}.MXF"
    if direct.is_file():
        return direct
    return apple / f"{take}.MOV"


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)


def _match_score(a: "np.ndarray", b: "np.ndarray") -> float:
    """Mean top-1 cosine match between two 576-patch token sets."""
    import numpy as np

    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    sim = a @ b.T
    return float(sim.max(axis=1).mean())


def _trace_score(a: "np.ndarray", b: "np.ndarray") -> float:
    """Mean same-position cosine: shuffling positions destroys it."""
    import numpy as np

    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    return float((a * b).sum(axis=1).mean())


def load_windows(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------- incumbent: Farneback regional motion ----------------

def run_incumbent(windows_path: Path, out_dir: Path) -> Path:
    sys.path.insert(0, str(repo_root()))
    from library.tools.regional_motion import (
        _decode_frames, analyze_frames, _display_aspect_ratio)

    fixture = load_windows(windows_path)
    rows = []
    for window in fixture["windows"]:
        path = source_path(window["take"])
        span = {"start": window["start"], "end": window["end"],
                "selected_by": "mu33-bench", "action_labels": [window["label"]]}
        t0 = time.perf_counter()
        frames = _decode_frames(str(path), window["start"], window["end"])
        t1 = time.perf_counter()
        aspect = _display_aspect_ratio(str(path))
        result = analyze_frames(frames, window["start"], None, aspect, span)
        t2 = time.perf_counter()
        tracks = result["motion_tracks"]
        peak = max((track["peak_p90_motion_px"] for track in tracks),
                   default=0.0)
        rows.append({
            "window_id": window["window_id"], "take": window["take"],
            "start": window["start"], "end": window["end"],
            "label": window["label"],
            "peak_p90_motion_px": peak,
            "n_motion_tracks": len(tracks),
            "decode_s": round(t1 - t0, 3),
            "infer_s": round(t2 - t1, 3),
            "frame_count": len(frames),
        })
    out = out_dir / "incumbent_rows.json"
    out.write_text(json.dumps({
        "model": "farneback-regional-motion",
        "peak_rss_mb": peak_rss_mb(),
        "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"incumbent: {len(rows)} windows peak_rss_mb={peak_rss_mb()}")
    return out


# ---------------- V-JEPA 2.1 ViT-B ----------------

def _decode_rgb_16(path: Path, start: float, end: float) -> "list":
    import numpy as np

    duration = end - start
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-i", str(path),
         "-t", f"{duration:.3f}",
         "-vf", "fps=8,scale=512:-2", "-an", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=max(120, int(duration * 10)), check=False)
    if result.returncode != 0:
        raise RuntimeError("vjepa decode failed: "
                           + result.stderr.decode("utf-8", errors="replace")[:300])
    # Recover frame geometry from the first bytes via ffprobe-free reshape:
    # re-decode one probe frame for dimensions.
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=512:-2",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=60, check=False)
    # Derive h from byte counts: total // (w*3) must be integral.
    import cv2 as _cv  # noqa: F401  (ensures cv2 present for later steps)
    total = len(result.stdout)
    # ffprobe the scaled size instead of guessing.
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False)
    width, height = (int(v) for v in info.stdout.strip().split(",") if v.strip())
    scale = 512.0 / width
    width, height = 512, int(round(height * scale / 2) * 2)
    frame_size = width * height * 3
    count = total // frame_size
    raw = np.frombuffer(result.stdout[:count * frame_size], dtype=np.uint8)
    frames = list(raw.reshape(count, height, width, 3))
    picks = [frames[min(len(frames) - 1, int(round(i * (len(frames) - 1) / 15)))]
             for i in range(16)]
    return picks


def _vjepa_preprocess(frames: list) -> "torch.Tensor":
    import cv2
    import numpy as np
    import torch

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    stack = []
    for frame in frames:
        height, width = frame.shape[:2]
        short = min(height, width)
        scale = 384.0 / short
        resized = cv2.resize(frame, (int(round(width * scale)),
                                    int(round(height * scale))),
                             interpolation=cv2.INTER_LINEAR)
        height, width = resized.shape[:2]
        top, left = (height - 384) // 2, (width - 384) // 2
        crop = resized[top:top + 384, left:left + 384].astype(np.float32) / 255.0
        stack.append((crop - mean) / std)
    clip = torch.from_numpy(np.stack(stack)).permute(0, 3, 1, 2)
    return clip.unsqueeze(0).permute(0, 2, 1, 3, 4).contiguous()


def run_vjepa(windows_path: Path, out_dir: Path) -> Path:
    sys.path.insert(0, DEPS)
    sys.path.insert(0, VJEPA_REPO)
    import numpy as np
    import torch
    from app.vjepa_2_1.models import vision_transformer as vit_encoder

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.perf_counter()
    encoder = vit_encoder.vit_base(
        patch_size=16, img_size=(384, 384), num_frames=64, tubelet_size=2,
        use_sdpa=True, use_SiLU=False, wide_SiLU=True, uniform_power=False,
        use_rope=True, img_temporal_dim_size=1, interpolate_rope=True)
    ckpt = torch.load(VJEPA_CKPT, map_location="cpu", weights_only=False)
    sd = {k.replace("module.", "").replace("backbone.", ""): v
          for k, v in ckpt["ema_encoder"].items()}
    missing = encoder.load_state_dict(sd, strict=False)
    assert not missing.missing_keys and not missing.unexpected_keys
    encoder.to(device).eval()
    t_load = time.perf_counter() - t0

    fixture = load_windows(windows_path)
    subset_ids = set()
    for label in ("motion", "static"):
        ids = sorted(row["window_id"] for row in fixture["windows"]
                     if row["label"] == label)
        subset_ids.update(ids[::2])
    rows, first_forward = [], True
    for window in fixture["windows"]:
        path = source_path(window["take"])
        t0 = time.perf_counter()
        frames = _decode_rgb_16(path, window["start"], window["end"])
        clip = _vjepa_preprocess(frames)
        t1 = time.perf_counter()
        with torch.no_grad():
            tokens = encoder(clip.to(device)).float().cpu().numpy()[0]
        if device == "mps":
            torch.mps.synchronize()
        t2 = time.perf_counter()
        # tokens: (8 temporal, 24, 24 spatial, 768). d_half contrasts the two
        # clip halves; global is the probe-style mean-pooled feature.
        # motion_energy_* measure per-patch temporal change instead of the
        # washed-out global mean: the dense-feature motion analogue.
        temporal = tokens.reshape(8, -1, 768)
        norms = np.linalg.norm(temporal, axis=2, keepdims=True)
        unit = temporal / np.maximum(norms, 1e-12)
        adjacent = 1.0 - (unit[:-1] * unit[1:]).sum(axis=2)
        motion_energy_mean = float(adjacent.mean())
        motion_energy_p90 = float(np.percentile(adjacent, 90))
        pooled = temporal.mean(axis=1)
        first, second = pooled[:4].mean(axis=0), pooled[4:].mean(axis=0)
        d_half = float(1.0 - first @ second
                       / (np.linalg.norm(first) * np.linalg.norm(second)))
        row = {
            "window_id": window["window_id"], "take": window["take"],
            "start": window["start"], "end": window["end"],
            "label": window["label"],
            "d_half": round(d_half, 6),
            "motion_energy_mean": round(motion_energy_mean, 6),
            "motion_energy_p90": round(motion_energy_p90, 6),
            "global_norm": round(float(np.linalg.norm(pooled.mean(axis=0))), 4),
            "decode_s": round(t1 - t0, 3),
            "infer_s": round(t2 - t1, 3),
            "cold": first_forward,
        }
        if window["window_id"] in subset_ids:
            # TMP-035: patch correspondence near (adjacent slices) vs far
            # (first vs last slice). Top-1 measures match strength; the
            # same-position trace is what spatial shuffling destroys, so the
            # shuffled control applies to the trace only.
            spatial = unit.reshape(8, 576, 768)
            far_shuffled = spatial[7][np.random.RandomState(7).permutation(576)]
            row["correspondence"] = {
                "near": round(_match_score(spatial[3], spatial[4]), 6),
                "far": round(_match_score(spatial[0], spatial[7]), 6),
                "trace_near": round(_trace_score(spatial[3], spatial[4]), 6),
                "trace_far": round(_trace_score(spatial[0], spatial[7]), 6),
                "trace_shuffled": round(_trace_score(spatial[0], far_shuffled), 6),
            }
        rows.append(row)
        first_forward = False
    out = out_dir / "vjepa_rows.json"
    out.write_text(json.dumps({
        "model": "vjepa2.1-vitb-384", "precision": "fp32", "device": device,
        "cold_load_s": round(t_load, 3),
        "tokens_per_window": [8, 24, 24, 768],
        "rows": rows} | {"peak_rss_mb": peak_rss_mb()}, indent=2) + "\n",
        encoding="utf-8")
    print(f"vjepa: {len(rows)} windows cold_load_s={t_load:.1f} "
          f"peak_rss_mb={peak_rss_mb()}")
    return out


# ---------------- PE-Core-S + CLIP ----------------

def _center_frame_rgb(path: Path, start: float, end: float, width: int = 512):
    import subprocess

    import numpy as np

    mid = (start + end) / 2.0
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False)
    src_w, src_h = (int(v) for v in info.stdout.strip().split(",") if v.strip())
    height = int(round(src_h * width / src_w / 2) * 2)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{mid:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={width}:{height}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=60, check=False)
    if result.returncode != 0:
        raise RuntimeError("frame decode failed")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


def _frame_at(path: Path, at: float, width: int = 512):
    """Decode one RGB frame at an exact source time."""
    import subprocess

    import numpy as np

    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False)
    src_w, src_h = (int(v) for v in info.stdout.strip().split(",") if v.strip())
    height = int(round(src_h * width / src_w / 2) * 2)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={width}:{height}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=60, check=False)
    if result.returncode != 0:
        raise RuntimeError("frame decode failed")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


def run_pe(windows_path: Path, out_dir: Path) -> Path:
    sys.path.insert(0, DEPS)
    sys.path.insert(0, PE_REPO)
    import cv2
    import numpy as np
    import torch
    from core.vision_encoder import pe

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.perf_counter()
    model = pe.CLIP.from_config("PE-Core-S16-384", pretrained=True,
                                checkpoint_path=PE_CKPT)
    model.visual.to(device).eval()
    t_load = time.perf_counter() - t0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def preprocess(frame) -> "torch.Tensor":
        height, width = frame.shape[:2]
        scale = 384.0 / min(height, width)
        resized = cv2.resize(frame, (int(round(width * scale)),
                                     int(round(height * scale))),
                             interpolation=cv2.INTER_LINEAR)
        height, width = resized.shape[:2]
        top, left = (height - 384) // 2, (width - 384) // 2
        crop = resized[top:top + 384, left:left + 384].astype(np.float32) / 255.0
        return torch.from_numpy(((crop - mean) / std).transpose(2, 0, 1))

    fixture = load_windows(windows_path)
    subset_ids = set()
    for label in ("motion", "static"):
        ids = sorted(row["window_id"] for row in fixture["windows"]
                     if row["label"] == label)
        subset_ids.update(ids[::2])
    rows, first = [], True
    with torch.no_grad():
        for window in fixture["windows"]:
            path = source_path(window["take"])
            t0 = time.perf_counter()
            frame = _center_frame_rgb(path, window["start"], window["end"])
            x = preprocess(frame).unsqueeze(0).to(device)
            t1 = time.perf_counter()
            embedding = model.visual(x).float().cpu().numpy()[0]
            if device == "mps":
                torch.mps.synchronize()
            t2 = time.perf_counter()
            row = {
                "window_id": window["window_id"], "take": window["take"],
                "label": window["label"],
                "embedding": [round(float(v), 6) for v in embedding],
                "decode_s": round(t1 - t0, 3),
                "infer_s": round(t2 - t1, 3),
                "cold": first,
            }
            if window["window_id"] in subset_ids:
                # TMP-035/038-ref: patch correspondence between a near frame
                # pair (0.5 s apart) and a far pair, at mid and final layers.
                duration = window["end"] - window["start"]
                t_near = min(window["start"] + 1.0, window["end"] - 0.6)
                frames = [_frame_at(path, t_near), _frame_at(path, t_near + 0.5),
                          _frame_at(path, window["end"] - 0.25)]
                batch = torch.stack([preprocess(frame) for frame in frames]).to(device)
                correspondence = {}
                for layer in (7, -1):
                    tokens = model.visual.forward_features(
                        batch, norm=True, layer_idx=layer,
                        strip_cls_token=True).float().cpu().numpy()
                    far_shuffled = tokens[2][np.random.RandomState(7).permutation(576)]
                    correspondence[f"layer_{layer}"] = {
                        "near": round(_match_score(tokens[0], tokens[1]), 6),
                        "far": round(_match_score(tokens[0], tokens[2]), 6),
                        "trace_near": round(_trace_score(tokens[0], tokens[1]), 6),
                        "trace_far": round(_trace_score(tokens[0], tokens[2]), 6),
                        "trace_shuffled": round(_trace_score(tokens[0], far_shuffled), 6),
                    }
                row["correspondence"] = correspondence
            rows.append(row)
            first = False
    out = out_dir / "pe_rows.json"
    out.write_text(json.dumps({
        "model": "pe-core-s16-384", "precision": "fp32", "device": device,
        "cold_load_s": round(t_load, 3), "embedding_dim": 512,
        "rows": rows} | {"peak_rss_mb": peak_rss_mb()}, indent=2) + "\n",
        encoding="utf-8")
    print(f"pe: {len(rows)} windows cold_load_s={t_load:.1f} "
          f"peak_rss_mb={peak_rss_mb()}")
    return out


def run_clip(windows_path: Path, out_dir: Path) -> Path:
    sys.path.insert(0, DEPS)
    import numpy as np
    import torch
    from transformers import CLIPModel, CLIPProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.perf_counter()
    model_id = "openai/clip-vit-large-patch14"
    model = CLIPModel.from_pretrained(model_id).to(device).eval()
    processor = CLIPProcessor.from_pretrained(model_id)
    t_load = time.perf_counter() - t0

    from PIL import Image
    fixture = load_windows(windows_path)
    rows, first = [], True
    with torch.no_grad():
        for window in fixture["windows"]:
            path = source_path(window["take"])
            t0 = time.perf_counter()
            frame = _center_frame_rgb(path, window["start"], window["end"])
            inputs = processor(images=Image.fromarray(frame),
                               return_tensors="pt").to(device)
            t1 = time.perf_counter()
            embedding = model.get_image_features(**inputs).pooler_output.float().cpu().numpy()[0]
            embedding = embedding / np.linalg.norm(embedding)
            if device == "mps":
                torch.mps.synchronize()
            t2 = time.perf_counter()
            rows.append({
                "window_id": window["window_id"], "take": window["take"],
                "label": window["label"],
                "embedding": [round(float(v), 6) for v in embedding],
                "decode_s": round(t1 - t0, 3),
                "infer_s": round(t2 - t1, 3),
                "cold": first,
            })
            first = False
    out = out_dir / "clip_rows.json"
    out.write_text(json.dumps({
        "model": model_id, "precision": "fp32", "device": device,
        "cold_load_s": round(t_load, 3),
        "embedding_dim": int(embedding.shape[0]),
        "rows": rows} | {"peak_rss_mb": peak_rss_mb()}, indent=2) + "\n",
        encoding="utf-8")
    print(f"clip: {len(rows)} windows cold_load_s={t_load:.1f} "
          f"peak_rss_mb={peak_rss_mb()}")
    return out


# ---------------- metrics + frozen reports ----------------

# Rank and similarity math lives in library.tools.video_features.metrics
# (unit-tested); this script only aggregates per-window rows.


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _revision() -> str:
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8",
                            check=False)
    return result.stdout.strip()


def _write_report(out_dir: Path, name: str, version_id: str, role: str,
                  models: dict[str, str], modality: str, metrics: dict,
                  evidence: list[tuple[str, Path]]) -> Path:
    path = out_dir / name
    path.write_text(json.dumps({
        "format": "ren_evaluation_report/1",
        "revision": _revision(),
        "version": {"id": version_id, "role": role, "models": models},
        "modality": modality,
        "split": "calibration",
        "metrics": metrics,
        "evidence": [{"id": label, "path": src.name, "sha256": _sha(src)}
                     for label, src in evidence],
        "selection_basis": ["calibration"],
    }, indent=2) + "\n", encoding="utf-8")
    return path


def run_metrics(windows_path: Path, out_dir: Path) -> list[Path]:
    fixture = load_windows(windows_path)
    incumbent_doc = json.loads(
        (out_dir / "incumbent_rows.json").read_text(encoding="utf-8"))
    incumbent = {row["window_id"]: row for row in incumbent_doc["rows"]}
    vjepa_doc = json.loads((out_dir / "vjepa_rows.json").read_text(encoding="utf-8"))
    vjepa = {row["window_id"]: row for row in vjepa_doc["rows"]}
    pe_doc = json.loads((out_dir / "pe_rows.json").read_text(encoding="utf-8"))
    pe = {row["window_id"]: row for row in pe_doc["rows"]}
    clip_doc = json.loads((out_dir / "clip_rows.json").read_text(encoding="utf-8"))
    clip = {row["window_id"]: row for row in clip_doc["rows"]}

    windows = fixture["windows"]
    sep = [row for row in windows if row["label"] in ("motion", "static")]
    labels = [1 if row["label"] == "motion" else 0 for row in sep]
    evidence = [("mu33-calibration-windows", windows_path)]

    def quality(name: str, value: float, unit: str) -> tuple[str, dict]:
        return name, {"value": value, "unit": unit, "dimension": "quality"}

    def latency(name: str, value: float, unit: str) -> tuple[str, dict]:
        return name, {"value": value, "unit": unit, "dimension": "latency"}

    def memory(name: str, value: float, unit: str) -> tuple[str, dict]:
        return name, {"value": value, "unit": unit, "dimension": "memory"}

    def cost(name: str, value: float, unit: str) -> tuple[str, dict]:
        return name, {"value": value, "unit": unit, "dimension": "cost"}

    vjepa_scores = [vjepa[row["window_id"]]["d_half"] for row in sep]
    vjepa_energy = [vjepa[row["window_id"]]["motion_energy_p90"] for row in sep]
    inc_scores = [incumbent[row["window_id"]]["peak_p90_motion_px"] for row in sep]

    reports = []
    # TMP-033: extraction cost per candidate and incumbent.
    vjepa_rows = list(vjepa.values())
    warm = [row for row in vjepa_rows if not row["cold"]]
    reports.append(_write_report(
        out_dir, "2026-10-16-vjepa-cost.report.json", "vjepa2.1-vitb-384",
        "candidate", {"video_features": "vjepa2_1_vitb_dist_vitG_384@dl.fbaipublicfiles.com"},
        "feature_extraction_cost", dict([
            latency("cold_load_s", vjepa_doc["cold_load_s"], "seconds"),
            latency("warm_infer_s_per_window",
                    sum(row["infer_s"] for row in warm) / len(warm),
                    "seconds per <=8s window (16 frames at 384px)"),
            latency("decode_s_per_window",
                    sum(row["decode_s"] for row in vjepa_rows) / len(vjepa_rows),
                    "seconds per window"),
            memory("process_peak_rss_gb", vjepa_doc["peak_rss_mb"] / 1024.0,
                   "GB (does not include all Metal memory)"),
            cost("wall_s_per_window",
                 sum(row["decode_s"] + row["infer_s"] for row in vjepa_rows) / len(vjepa_rows),
                 "seconds per window"),
        ]), evidence))
    pe_rows = list(pe.values())
    pe_warm = [row for row in pe_rows if not row["cold"]]
    reports.append(_write_report(
        out_dir, "2026-10-16-pe-cost.report.json", "pe-core-s16-384",
        "candidate", {"visual_embed": "facebook/PE-Core-S16-384@aabf3b990573d8114ae6e501b4697106beac8f19"},
        "feature_extraction_cost", dict([
            latency("cold_load_s", pe_doc["cold_load_s"], "seconds"),
            latency("warm_infer_s_per_frame",
                    sum(row["infer_s"] for row in pe_warm) / len(pe_warm),
                    "seconds per 384px frame"),
            memory("process_peak_rss_gb", pe_doc["peak_rss_mb"] / 1024.0,
                   "GB (does not include all Metal memory)"),
            cost("wall_s_per_frame",
                 sum(row["decode_s"] + row["infer_s"] for row in pe_rows) / len(pe_rows),
                 "seconds per frame"),
        ]), evidence))
    inc_rows = list(incumbent.values())
    reports.append(_write_report(
        out_dir, "2026-10-16-farneback-cost.report.json",
        "farneback-regional-motion", "baseline",
        {"motion": "library.tools.regional_motion@repo"},
        "feature_extraction_cost", dict([
            latency("mean_infer_s_per_window",
                    sum(row["infer_s"] for row in inc_rows) / len(inc_rows),
                    "seconds per window (10Hz 640x360 Farneback)"),
            latency("mean_decode_s_per_window",
                    sum(row["decode_s"] for row in inc_rows) / len(inc_rows),
                    "seconds per window"),
            memory("process_peak_rss_gb",
                   incumbent_doc["peak_rss_mb"] / 1024.0,
                   "GB (does not include all Metal memory)"),
            cost("wall_s_per_window",
                 sum(row["decode_s"] + row["infer_s"] for row in inc_rows) / len(inc_rows),
                 "seconds per window"),
        ]), evidence))
    clip_rows = list(clip.values())
    clip_warm = [row for row in clip_rows if not row["cold"]]
    reports.append(_write_report(
        out_dir, "2026-10-16-clip-cost.report.json",
        "clip-vit-large-patch14", "baseline",
        {"visual_embed": "openai/clip-vit-large-patch14@cached"},
        "feature_extraction_cost", dict([
            latency("cold_load_s", clip_doc["cold_load_s"], "seconds"),
            latency("warm_infer_s_per_frame",
                    sum(row["infer_s"] for row in clip_warm) / len(clip_warm),
                    "seconds per frame"),
            memory("process_peak_rss_gb", clip_doc["peak_rss_mb"] / 1024.0,
                   "GB (does not include all Metal memory)"),
            cost("wall_s_per_frame",
                 sum(row["decode_s"] + row["infer_s"] for row in clip_rows) / len(clip_rows),
                 "seconds per frame"),
        ]), evidence))

    # TMP-034: motion/state-change sensitivity (motion vs static separation).
    # motion_energy_p90 is the dense-feature motion formulation; d_half is
    # the washed-out global-mean control recorded alongside it.
    reports.append(_write_report(
        out_dir, "2026-10-16-vjepa-motion-sensitivity.report.json",
        "vjepa2.1-vitb-384", "candidate",
        {"video_features": "vjepa2_1_vitb_dist_vitG_384@dl.fbaipublicfiles.com"},
        "motion_sensitivity", dict([
            quality("motion_static_auc_energy_p90", _auc(labels, vjepa_energy),
                    "rank AUC over 9 motion + 11 static agent-labeled windows"),
            quality("motion_static_auc_d_half", _auc(labels, vjepa_scores),
                    "rank AUC, global-mean control"),
            quality("mean_energy_p90_motion",
                    sum(s for lab, s in zip(labels, vjepa_energy) if lab) / 9,
                    "mean per-patch p90 temporal change"),
            quality("mean_energy_p90_static",
                    sum(s for lab, s in zip(labels, vjepa_energy) if not lab) / 11,
                    "mean per-patch p90 temporal change"),
        ]), evidence))
    reports.append(_write_report(
        out_dir, "2026-10-16-farneback-motion-sensitivity.report.json",
        "farneback-regional-motion", "baseline",
        {"motion": "library.tools.regional_motion@repo"},
        "motion_sensitivity", dict([
            quality("motion_static_auc", _auc(labels, inc_scores),
                    "rank AUC over 9 motion + 11 static agent-labeled windows"),
        ]), evidence))

    # TMP-036: agreement between V-JEPA distances and incumbent motion.
    all_ids = [row["window_id"] for row in windows]
    reports.append(_write_report(
        out_dir, "2026-10-16-vjepa-motion-agreement.report.json",
        "vjepa2.1-vitb-384", "candidate",
        {"video_features": "vjepa2_1_vitb_dist_vitG_384@dl.fbaipublicfiles.com"},
        "motion_agreement", dict([
            quality("spearman_energy_p90_vs_farneback_peak",
                    _spearman([vjepa[i]["motion_energy_p90"] for i in all_ids],
                              [incumbent[i]["peak_p90_motion_px"] for i in all_ids]),
                    "rank correlation over all 32 calibration windows"),
            quality("spearman_d_half_vs_farneback_peak",
                    _spearman([vjepa[i]["d_half"] for i in all_ids],
                              [incumbent[i]["peak_p90_motion_px"] for i in all_ids]),
                    "rank correlation, global-mean control"),
        ]), evidence))

    # TMP-035: V-JEPA temporal correspondence on the 11-window subset.
    def _corr(rows: dict, key: str) -> list[float]:
        return [rows[row["window_id"]]["correspondence"][key]
                for row in windows if "correspondence" in rows[row["window_id"]]]
    near = _corr(vjepa, "near")
    far = _corr(vjepa, "far")
    trace_near = _corr(vjepa, "trace_near")
    trace_far = _corr(vjepa, "trace_far")
    trace_shuffled = _corr(vjepa, "trace_shuffled")
    reports.append(_write_report(
        out_dir, "2026-10-16-vjepa-correspondence.report.json",
        "vjepa2.1-vitb-384", "candidate",
        {"video_features": "vjepa2_1_vitb_dist_vitG_384@dl.fbaipublicfiles.com"},
        "temporal_correspondence", dict([
            quality("near_minus_far_margin",
                    sum(n - f for n, f in zip(near, far)) / len(near),
                    "mean top-1 patch cosine, adjacent vs first-last slices"),
            quality("trace_near_minus_trace_far_margin",
                    sum(n - f for n, f in zip(trace_near, trace_far)) / len(trace_near),
                    "same-position cosine margin"),
            quality("trace_far_minus_trace_shuffled_margin",
                    sum(f - s for f, s in zip(trace_far, trace_shuffled)) / len(trace_far),
                    "same-position cosine vs spatial-shuffle control"),
            quality("mean_near_score", sum(near) / len(near), "cosine"),
            quality("mean_far_score", sum(far) / len(far), "cosine"),
            quality("subset_windows", float(len(near)), "windows"),
        ]), evidence))

    # TMP-038 reference: PE-Core-S spatial correspondence, mid and final layer.
    for layer in (7, -1):
        key = f"layer_{layer}"
        subset = [row for row in windows if "correspondence" in pe[row["window_id"]]]
        pe_near = [pe[row["window_id"]]["correspondence"][key]["near"] for row in subset]
        pe_far = [pe[row["window_id"]]["correspondence"][key]["far"] for row in subset]
        pe_trace_near = [pe[row["window_id"]]["correspondence"][key]["trace_near"] for row in subset]
        pe_trace_far = [pe[row["window_id"]]["correspondence"][key]["trace_far"] for row in subset]
        pe_trace_shuffled = [pe[row["window_id"]]["correspondence"][key]["trace_shuffled"] for row in subset]
        reports.append(_write_report(
            out_dir, f"2026-10-16-pe-correspondence-l{layer if layer > 0 else 'final'}.report.json",
            "pe-core-s16-384", "candidate",
            {"visual_embed": "facebook/PE-Core-S16-384@aabf3b990573d8114ae6e501b4697106beac8f19"},
            f"spatial_correspondence_l{layer if layer > 0 else 'final'}", dict([
                quality("near_minus_far_margin",
                        sum(n - f for n, f in zip(pe_near, pe_far)) / len(pe_near),
                        "mean top-1 patch cosine, 0.5s-apart vs span-apart frames"),
                quality("trace_near_minus_trace_far_margin",
                        sum(n - f for n, f in zip(pe_trace_near, pe_trace_far)) / len(pe_trace_near),
                        "same-position cosine margin"),
                quality("trace_far_minus_trace_shuffled_margin",
                        sum(f - s for f, s in zip(pe_trace_far, pe_trace_shuffled)) / len(pe_trace_far),
                        "same-position cosine vs spatial-shuffle control"),
                quality("mean_near_score", sum(pe_near) / len(pe_near), "cosine"),
                quality("mean_far_score", sum(pe_far) / len(pe_far), "cosine"),
            ]), evidence))

    # TMP-037: action-candidate ranking vs the same 9 motion labels.
    # V-JEPA ranks by motion_energy_p90 (its fair motion formulation).
    for version_id, role, models, scores, stem in [
            ("vjepa2.1-vitb-384", "candidate",
             {"video_features": "vjepa2_1_vitb_dist_vitG_384@dl.fbaipublicfiles.com"},
             vjepa_energy, "vjepa"),
            ("farneback-regional-motion", "baseline",
             {"motion": "library.tools.regional_motion@repo"},
             inc_scores, "farneback")]:
        reports.append(_write_report(
            out_dir, f"2026-10-16-{stem}-action-ranking.report.json",
            version_id, role, models, "action_candidate_ranking", dict([
                quality("average_precision", _average_precision(labels, scores),
                        "9 motion positives over 20 labeled windows"),
                quality("precision_at_5", _precision_at_k(labels, scores, 5),
                        "fraction"),
                quality("precision_at_10", _precision_at_k(labels, scores, 10),
                        "fraction"),
            ]), evidence))

    # TMP-038 reference: frame visual similarity separation (PE vs CLIP).
    # Same-take window pairs should sit closer than cross-take pairs: the
    # protocol measures whether an embedding sees shared place and moment,
    # not agent action classes (which share no visual content across takes).
    def similarity_separation(embeddings: dict) -> tuple[float, float]:
        takes: dict[str, list] = {}
        for row in windows:
            takes.setdefault(row["take"], []).append(
                embeddings[row["window_id"]]["embedding"])
        within = [_cosine(a, b)
                  for frames in takes.values() for i, a in enumerate(frames)
                  for b in frames[i + 1:] if len(frames) > 1]
        keys = sorted(takes)
        across = [_cosine(a, b)
                  for i, ka in enumerate(keys) for kb in keys[i + 1:]
                  for a in takes[ka] for b in takes[kb]]
        mean_within = sum(within) / len(within)
        mean_across = sum(across) / len(across)
        scored = [1] * len(within) + [0] * len(across)
        return mean_within - mean_across, _auc(scored, within + across)
    pe_gap, pe_auc = similarity_separation(pe)
    clip_gap, clip_auc = similarity_separation(clip)
    reports.append(_write_report(
        out_dir, "2026-10-16-pe-similarity.report.json", "pe-core-s16-384",
        "candidate",
        {"visual_embed": "facebook/PE-Core-S16-384@aabf3b990573d8114ae6e501b4697106beac8f19"},
        "visual_similarity", dict([
            quality("within_minus_across_cosine", pe_gap, "cosine gap"),
            quality("pair_separation_auc", pe_auc, "rank AUC"),
        ]), evidence))
    reports.append(_write_report(
        out_dir, "2026-10-16-clip-similarity.report.json",
        "clip-vit-large-patch14", "baseline",
        {"visual_embed": "openai/clip-vit-large-patch14@cached"},
        "visual_similarity", dict([
            quality("within_minus_across_cosine", clip_gap, "cosine gap"),
            quality("pair_separation_auc", clip_auc, "rank AUC"),
        ]), evidence))
    print(f"metrics: {len(reports)} frozen reports")
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", required=True,
                        choices=["incumbent", "vjepa", "pe", "clip", "metrics"])
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    {"incumbent": run_incumbent, "vjepa": run_vjepa, "pe": run_pe,
     "clip": run_clip, "metrics": run_metrics}[args.only](args.windows, args.out_dir)


if __name__ == "__main__":
    main()
