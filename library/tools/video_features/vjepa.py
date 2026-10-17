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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_ID = "vjepa2.1-vitb-384"
PATCH_SIZE = 16
FRAME_SIZE = 384
EMBED_DIM = 768
TEMPORAL_SLICES = 8
SPATIAL_GRID = 24
INFERENCE_PRECISION = "bf16"
"""Inference runs in bf16 on MPS, and that is a measured decision.

bf16 preserved motion action-ranking AP (.62824), P@5 (.60) and P@10 (.70)
on the 32 frozen calibration windows while running 2.43x faster than fp32
(.617s vs 1.500s per window) with 25.6% lower sampled physical footprint
(4.639 GiB vs 6.238 GiB). fp16 fell to AP .57855 and was rejected. On CPU
the precision is fp32 (autocast is MPS-only)."""


class ModelUnavailable(Exception):
    """The V-JEPA encoder cannot be built here (missing repo, weights, ML stack)."""


@dataclass(frozen=True)
class VideoTokenResult:
    """Float32 tokens plus the precision used to produce them."""

    tokens: Any
    precision: str


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
    """Forward one clip and report effective precision with float32 tokens."""
    torch = _require_torch()
    device = next(encoder.parameters()).device
    use_bf16 = device.type == "mps" and INFERENCE_PRECISION == "bf16"
    if use_bf16:
        encoder.to(dtype=torch.bfloat16)
        clip = clip.to(device=device, dtype=torch.bfloat16)
    else:
        clip = clip.to(device=device, dtype=torch.float32)
    with torch.no_grad():
        if use_bf16:
            with torch.autocast(device_type="mps", dtype=torch.bfloat16):
                tokens = encoder(clip).float().cpu().numpy()[0]
        else:
            tokens = encoder(clip).float().cpu().numpy()[0]
    if device.type == "mps":
        torch.mps.synchronize()
    return VideoTokenResult(
        tokens=tokens.reshape(
            TEMPORAL_SLICES, SPATIAL_GRID * SPATIAL_GRID, EMBED_DIM),
        precision="bf16" if use_bf16 else "fp32",
    )


def motion_energy(tokens) -> tuple[float, float]:
    """Mean and p90 per-patch temporal change over the token slices."""
    import numpy as np

    norms = np.linalg.norm(tokens, axis=2, keepdims=True)
    unit = tokens / np.maximum(norms, 1e-12)
    adjacent = 1.0 - (unit[:-1] * unit[1:]).sum(axis=2)
    return float(adjacent.mean()), float(np.percentile(adjacent, 90))
