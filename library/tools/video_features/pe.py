"""Demand-driven PE-Core-S16-384 visual embeddings (research-only).

Loads the publisher's own vision tower (facebookresearch/perception_models)
from an explicit repo checkout plus the publisher's own checkpoint file,
and embeds only caller-requested frames - never whole sources (ARCH-009).
torch is imported lazily; anything missing raises ModelUnavailable
instead of falling back to another model.

Weight identity: facebook/PE-Core-S16-384 (23,782,656 vision params, fp32,
Apache 2.0). The paper's finding that mid-network features transfer best
is honored by exposing forward_features layer selection; the benchmark
selected layer 7 on calibration (see docs/benchmarks/2026-10-16-video-features.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_ID = "pe-core-s16-384"
FRAME_SIZE = 384
EMBED_DIM = 512
PATCH_GRID = 24
SELECTED_LAYER = 7


class ModelUnavailable(Exception):
    """The PE tower cannot be built here (missing repo, weights, ML stack)."""


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise ModelUnavailable(f"torch is not installed: {exc}") from exc
    return torch


def load_tower(repo_dir: str | Path, checkpoint_path: str | Path):
    """Build the publisher's PE-Core-S16-384 tower with official weights."""
    _require_torch()
    repo = Path(repo_dir)
    package = repo / "core" / "vision_encoder" / "pe.py"
    if not package.is_file():
        raise ModelUnavailable(f"publisher repo not found at {repo}")
    if not Path(checkpoint_path).is_file():
        raise ModelUnavailable(f"checkpoint not found at {checkpoint_path}")
    sys.path.insert(0, str(repo))
    try:
        from core.vision_encoder import pe
    except ImportError as exc:
        raise ModelUnavailable(
            f"cannot import publisher tower: {exc}") from exc
    finally:
        try:
            sys.path.remove(str(repo))
        except ValueError:
            pass
    try:
        model = pe.CLIP.from_config(
            "PE-Core-S16-384", pretrained=True,
            checkpoint_path=str(checkpoint_path))
    except RuntimeError as exc:
        raise ModelUnavailable(f"PE tower build failed: {exc}") from exc
    model.visual.eval()
    return model.visual


def preprocess_frame(frame):
    """Normalize one RGB frame to the publisher's 384px input."""
    _require_torch()
    import numpy as np

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    height, width = frame.shape[:2]
    scale = FRAME_SIZE / min(height, width)
    if scale != 1.0:
        import cv2

        frame = cv2.resize(
            np.asarray(frame),
            (int(round(width * scale)), int(round(height * scale))),
            interpolation=cv2.INTER_LINEAR)
    height, width = frame.shape[:2]
    top, left = (height - FRAME_SIZE) // 2, (width - FRAME_SIZE) // 2
    crop = np.asarray(frame)[top:top + FRAME_SIZE, left:left + FRAME_SIZE]
    crop = crop.astype(np.float32) / 255.0
    return (crop - mean) / std


def embed_frame(tower, frame):
    """Global 512-d embedding for one frame."""
    torch = _require_torch()
    import numpy as np

    tensor = torch.from_numpy(
        preprocess_frame(frame).transpose(2, 0, 1)).unsqueeze(0)
    device = next(tower.parameters()).device
    with torch.no_grad():
        embedding = tower(tensor.to(device)).float().cpu().numpy()[0]
    if str(device) == "mps":
        torch.mps.synchronize()
    return embedding / max(1e-12, float(np.linalg.norm(embedding)))


def patch_tokens(tower, frame, layer_idx: int = SELECTED_LAYER):
    """(576, width) patch tokens at one transformer layer, CLS stripped."""
    torch = _require_torch()
    import numpy as np

    tensor = torch.from_numpy(
        preprocess_frame(frame).transpose(2, 0, 1)).unsqueeze(0)
    device = next(tower.parameters()).device
    with torch.no_grad():
        tokens = tower.forward_features(
            tensor.to(device), norm=True, layer_idx=layer_idx,
            strip_cls_token=True).float().cpu().numpy()[0]
    if str(device) == "mps":
        torch.mps.synchronize()
    norms = np.linalg.norm(tokens, axis=1, keepdims=True)
    return tokens / np.maximum(norms, 1e-12)
