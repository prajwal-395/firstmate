"""Reusable video-feature extraction behind the existing capability interface.

TMP-033..041: demand-driven, incrementally rebuildable adapters over
publisher-official video and image encoders. Nothing here changes a
capability default or removes an incumbent: V-JEPA 2.1 ViT-B and
PE-Core-S16-384 measured as research-only representations (see
docs/benchmarks/2026-10-16-video-features.md), so these adapters are the
integration path that keeps the incumbents (Farneback regional motion,
CLIP visual similarity) exactly where they are.

Publisher code is never vendored: each loader imports the publisher's
own package from an explicit checkout directory and the publisher's own
checkpoint file, so checkpoint identity stays with the publisher.
Model availability is a structured failure (ModelUnavailable), never a
silent fallback, per the evaluation failure taxonomy.
"""

from __future__ import annotations

MODEL_IDS = {
    "vjepa2.1-vitb-384": {
        "publisher": "Meta FAIR",
        "code": "facebookresearch/vjepa2",
        "checkpoint_url": "https://dl.fbaipublicfiles.com/vjepa2/vjepa2_1_vitb_dist_vitG_384.pt",
        "params": 86833152,
    },
    "pe-core-s16-384": {
        "publisher": "Meta FAIR",
        "code": "facebookresearch/perception_models",
        "checkpoint": "facebook/PE-Core-S16-384",
        "params": 23782656,
    },
}
