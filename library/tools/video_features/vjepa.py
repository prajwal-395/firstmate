"""Demand-driven V-JEPA 2.1 ViT-B/16 feature extraction (research-only).

Loads the publisher's own encoder (facebookresearch/vjepa2, 2.1 recipe)
from an explicit repo checkout plus the publisher's own checkpoint file,
and extracts dense clip tokens only for caller-requested intervals -
never whole sources (ARCH-009). torch is imported lazily so the module
imports without the ML stack; anything missing raises ModelUnavailable
instead of falling back to another model.

Weight identity: ema_encoder from
https://dl.fbaipublicfiles.com/vjepa2/vjepa2_1_vitb_dist_vitG_384.pt
(86,833,152 params, fp32).
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_ID = "vjepa2.1-vitb-384"
PATCH_SIZE = 16
FRAME_SIZE = 384
EMBED_DIM = 768
TEMPORAL_SLICES = 8
SPATIAL_GRID = 24


class ModelUnavailable(Exception):
    """The V-JEPA encoder cannot be built here (missing repo, weights, ML stack)."""


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise ModelUnavailable(f"torch is not installed: {exc}") from exc
    return torch


def load_encoder(repo_dir: str | Path, checkpoint_path: str | Path):
    """Build the publisher's vit_base encoder and load ema_encoder strictly."""
    torch = _require_torch()
    repo = Path(repo_dir)
    package = repo / "app" / "vjepa_2_1" / "models" / "vision_transformer.py"
    if not package.is_file():
        raise ModelUnavailable(f"publisher repo not found at {repo}")
    if not Path(checkpoint_path).is_file():
        raise ModelUnavailable(f"checkpoint not found at {checkpoint_path}")
    sys.path.insert(0, str(repo))
    try:
        from app.vjepa_2_1.models import vision_transformer as vit_encoder
    except ImportError as exc:
        raise ModelUnavailable(f"cannot import publisher encoder: {exc}") from exc
    finally:
        try:
            sys.path.remove(str(repo))
        except ValueError:
            pass
    encoder = vit_encoder.vit_base(
        patch_size=PATCH_SIZE, img_size=(FRAME_SIZE, FRAME_SIZE),
        num_frames=64, tubelet_size=2, use_sdpa=True, use_SiLU=False,
        wide_SiLU=True, uniform_power=False, use_rope=True,
        img_temporal_dim_size=1, interpolate_rope=True)
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    try:
        raw = ckpt["ema_encoder"]
    except (KeyError, TypeError) as exc:
        raise ModelUnavailable(
            "checkpoint has no ema_encoder entry") from exc
    cleaned = {key.replace("module.", "").replace("backbone.", ""): value
               for key, value in raw.items()}
    report = encoder.load_state_dict(cleaned, strict=False)
    if report.missing_keys or report.unexpected_keys:
        raise ModelUnavailable(
            f"encoder/weights mismatch: {len(report.missing_keys)} missing, "
            f"{len(report.unexpected_keys)} unexpected")
    encoder.eval()
    return encoder


def preprocess_frames(frames):
    """Normalize RGB crops to the publisher's eval input (B, C, T, H, W)."""
    torch = _require_torch()
    import numpy as np

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    stack = []
    for frame in frames:
        height, width = frame.shape[:2]
        scale = FRAME_SIZE / min(height, width)
        resized = np.asarray(frame)
        if scale != 1.0:
            import cv2

            resized = cv2.resize(
                resized, (int(round(width * scale)), int(round(height * scale))),
                interpolation=cv2.INTER_LINEAR)
        height, width = resized.shape[:2]
        top, left = (height - FRAME_SIZE) // 2, (width - FRAME_SIZE) // 2
        crop = resized[top:top + FRAME_SIZE, left:left + FRAME_SIZE]
        crop = crop.astype(np.float32) / 255.0
        stack.append((crop - mean) / std)
    clip = torch.from_numpy(np.stack(stack)).permute(0, 3, 1, 2)
    return clip.unsqueeze(0).permute(0, 2, 1, 3, 4).contiguous()


def extract_tokens(encoder, clip):
    """Forward one (1, 3, T, 384, 384) clip; returns (8, 576, 768) tokens."""
    torch = _require_torch()
    device = next(encoder.parameters()).device
    with torch.no_grad():
        tokens = encoder(clip.to(device)).float().cpu().numpy()[0]
    if str(device) == "mps":
        torch.mps.synchronize()
    return tokens.reshape(TEMPORAL_SLICES, SPATIAL_GRID * SPATIAL_GRID, EMBED_DIM)


def motion_energy(tokens) -> tuple[float, float]:
    """Mean and p90 per-patch temporal change over the token slices."""
    import numpy as np

    norms = np.linalg.norm(tokens, axis=2, keepdims=True)
    unit = tokens / np.maximum(norms, 1e-12)
    adjacent = 1.0 - (unit[:-1] * unit[1:]).sum(axis=2)
    return float(adjacent.mean()), float(np.percentile(adjacent, 90))
