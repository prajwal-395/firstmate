"""Depth Anything 3 backends behind the M3g estimator protocol.

The incumbent (`library/tools/depth.py`) reads Depth Anything V2 Small
through the official Depth-Anything-V2 checkout. This module reaches the
three Depth Anything 3 candidates the GEO lane benchmarks (GEO-001..003)
through the official ByteDance-Seed/Depth-Anything-3 checkout, kept
beside its weights exactly like the V2 checkout - never vendored into
this repo, never downloaded by this module.

**What each variant is.** DA3-Small and DA3-Base are main-series
foundation models (relative depth plus camera pose from one or more
views); DA3Metric-Large is the metric fine-tune (metres). All three
are Apache-2.0 at their pinned revisions (`VARIANTS`). Anything bigger
(Giant, Large, nested, mono) is research-only: `load_variant` refuses
it for any purpose but `permitted_research` (GEO-012).

**Direction.** DA3 natively reports SMALLER-is-nearer (a depth-like
scale: the seated face medians 0.77 against a frame median 0.93 on the
calibration podcast frames, `docs/benchmarks/2026-10-16-da3-depth.md`),
opposite the brighter-is-nearer convention `depth.py` reads. The
estimator this module hands out negates the native map, which preserves
the ordering the percentile normalisation downstream reads. A variant
whose native direction was never measured refuses (`DIRECTION`).

**Import shim.** The official package's `api` drags an export stack
(moviepy, trimesh, pycolmap, open3d) none of which inference touches
and none of which is installed here. The loader inserts the checkout's
`src` on `sys.path` and satisfies those import-time-only references
with inert placeholders that raise on USE, plus a functional minimal
`addict.Dict` (attribute access over a dict, the only part of addict
the model core touches). Nothing is installed into the shared venv.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import numpy as np

from library.tools.ren_refusal import RenRefusal

DEFAULT_REPO = Path.home() / ".cache" / "vep" / "depth" / "Depth-Anything-3"
DEFAULT_WEIGHTS = Path.home() / ".cache" / "vep" / "depth"
CODE_REPO = "https://github.com/ByteDance-Seed/Depth-Anything-3"
CODE_REVISION = "3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"
"""Pinned `main` of the official inference code, measured 2026-10-16."""

VARIANTS = {
    "da3-small": {
        "hf_repo": "depth-anything/DA3-SMALL",
        "revision": "e08cab65ca0ec38e7826075418411ab90cab4da3",
        "license": "Apache-2.0",
        "retrieved_at": "2026-10-16",
        "preset": "da3-small",
        "weights": "da3-small/model.safetensors",
        "series": "main",
        "depth_kind": "relative",
    },
    "da3-base": {
        "hf_repo": "depth-anything/DA3-BASE",
        "revision": "f4a6c9b3c95e41c82048423d3493a81ec3fa810e",
        "license": "Apache-2.0",
        "retrieved_at": "2026-10-16",
        "preset": "da3-base",
        "weights": "da3-base/model.safetensors",
        "series": "main",
        "depth_kind": "relative",
    },
    "da3metric-large": {
        "hf_repo": "depth-anything/DA3METRIC-LARGE",
        "revision": "4010e39f3634a45bc60553321fb49fb760bd594e",
        "license": "Apache-2.0",
        "retrieved_at": "2026-10-16",
        "preset": "da3metric-large",
        "weights": "da3metric-large/model.safetensors",
        "series": "metric",
        "depth_kind": "metric",
    },
}
"""Every candidate this lane benchmarks: publisher id, pinned revision,
license class from the publisher's own card, and what it claims to be."""

RESEARCH_ONLY = ("da3-giant", "da3-large", "da3mono-large",
                 "da3nested-giant-large")
"""Bigger DA3 variants: never downloaded, never benchmarked on client
work. `load_variant` refuses them outside `permitted_research`."""

DIRECTION = {
    "da3-small": "smaller-is-nearer",
    "da3-base": "smaller-is-nearer",
    "da3metric-large": "smaller-is-nearer",
}
"""Native depth direction per benchmarked variant, measured on the
calibration podcast frames (faces as the known-foreground anchor).
Absent means unmeasured, and the estimator refuses."""

AUX_MISSING_OK_PREFIXES = ("model.head.scratch.output_conv2_aux",)
"""State-dict keys allowed to be absent after loading: the auxiliary
training head, which inference never reads. Anything else missing is
a wrong-weights refusal, not a silent partial load."""


class Da3Unavailable(RenRefusal):
    """No DA3 variant can be estimated here, so nothing it says is built."""

    CODE = "REN_DA3_UNAVAILABLE"


class Da3ResearchOnly(RenRefusal):
    """A research-only variant was asked for outside permitted research."""

    CODE = "REN_DA3_RESEARCH_ONLY"


class _AttrDict(dict):
    """The one addict behaviour the model core touches: attribute
    read/write/delete over a dict."""

    def __getattr__(self, key):
        try:
            value = self[key]
        except KeyError:
            raise AttributeError(key) from None
        if isinstance(value, dict) and not isinstance(value, _AttrDict):
            value = _AttrDict(value)
            self[key] = value
        return value

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key) from None


class _UseError:
    """Import-time placeholder: importing succeeds, any USE raises."""

    def __init__(self, name: str):
        self._name = name

    def __call__(self, *args, **kwargs):
        raise ImportError(f"stubbed optional dependency {self._name} "
                          f"was actually called")

    def __getattr__(self, attr):
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        raise ImportError(f"stubbed optional dependency "
                          f"{self._name}.{attr} was actually used")


_EXPORT_ONLY_STUBS = ("moviepy", "moviepy.editor", "trimesh", "pycolmap",
                      "open3d", "evo", "evo.core", "evo.core.trajectory",
                      "plyfile", "pillow_heif", "e3nn")
"""Official-package imports inference never reaches (export, demo and
visualisation stack). Inert placeholders: present for the import, loud
on use."""


def _install_shim() -> None:
    """Make the official checkout importable without installing anything.

    Idempotent: a second call is a no-op. Real modules win - nothing is
    overwritten when the dependency is genuinely installed.
    """
    repo = Path(os.environ.get("PIPELINE_DA3_REPO", str(DEFAULT_REPO)))
    src = repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if "addict" not in sys.modules:
        try:
            import addict  # noqa: F401
        except ImportError:
            stub = types.ModuleType("addict")
            stub.Dict = _AttrDict  # type: ignore[attr-defined]
            sys.modules["addict"] = stub
    for name in _EXPORT_ONLY_STUBS:
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.__file__ = f"<da3 export-only stub for {name}>"
            stub.__path__ = []  # type: ignore[attr-defined]

            def _get(attr, _name=name):
                if attr.startswith("__") and attr.endswith("__"):
                    raise AttributeError(attr)
                return _UseError(f"{_name}.{attr}")

            stub.__getattr__ = _get  # type: ignore[attr-defined]
            sys.modules[name] = stub
    for parent, child in (("moviepy", "moviepy.editor"),
                          ("evo", "evo.core"),
                          ("evo.core", "evo.core.trajectory")):
        if parent in sys.modules and child in sys.modules:
            setattr(sys.modules[parent], child.split(".")[-1],
                    sys.modules[child])


def _repo_weights(name: str) -> tuple[Path, Path]:
    """The official checkout plus this variant's local safetensors."""
    repo = Path(os.environ.get("PIPELINE_DA3_REPO", str(DEFAULT_REPO)))
    root = Path(os.environ.get("PIPELINE_DA3_WEIGHTS", str(DEFAULT_WEIGHTS)))
    override = os.environ.get(f"PIPELINE_DA3_WEIGHTS_{name.upper().replace('-', '_')}")
    weights = Path(override) if override else root / VARIANTS[name]["weights"]
    return repo, weights


def probe_variant(name: str) -> tuple[bool, str]:
    """Whether `name` can run here. Pure probe: never downloads.

    Returns `(True, preset)` or `(False, reason)`. Unknown names and
    research-only variants are absent, not errors - the caller reports
    them with its own reason.
    """
    if name in RESEARCH_ONLY:
        return False, "research-only"
    if name not in VARIANTS:
        return False, "unknown-variant"
    repo, weights = _repo_weights(name)
    if not (repo / "src" / "depth_anything_3" / "api.py").is_file():
        return False, "no-code-checkout"
    if not weights.is_file():
        return False, "no-weights"
    if name not in DIRECTION:
        return False, "direction-unmeasured"
    return True, VARIANTS[name]["preset"]


def load_variant(name: str, purpose: str = "personal_noncommercial"):
    """The official estimator for `name`, on MPS when available.

    Refuses research-only variants outside `permitted_research`
    (GEO-012), unknown names, missing assets, unmeasured direction and
    partial weight loads beyond the auxiliary head.
    """
    if name in RESEARCH_ONLY and purpose != "permitted_research":
        raise Da3ResearchOnly(
            f"{name} is a research-only DA3 variant",
            "benchmark it only under a permitted-research purpose; "
            "it never ships and never touches client work.",
            "Use da3-small, da3-base or da3metric-large instead.")
    ok, reason = probe_variant(name)
    if not ok:
        if name in RESEARCH_ONLY:
            raise Da3ResearchOnly(
                f"{name} is a research-only DA3 variant",
                f"probe says {reason}.",
                "Rerun with purpose='permitted_research'.")
        raise Da3Unavailable(
            f"DA3 variant {name} cannot run here",
            f"probe says {reason}.",
            "Download only the lane's benchmarked variants at their "
            "pinned revisions beside the V2 assets "
            f"({DEFAULT_WEIGHTS}), with the official code checkout at "
            f"{CODE_REVISION} in {DEFAULT_REPO}; then retry.")
    _install_shim()
    import torch
    from depth_anything_3.api import DepthAnything3
    from safetensors.torch import load_file

    _, weights = _repo_weights(name)
    model = DepthAnything3(model_name=VARIANTS[name]["preset"])
    state = load_file(str(weights), device="cpu")
    missing, unexpected = model.load_state_dict(state, strict=False)
    outside_aux = [k for k in missing
                   if not k.startswith(AUX_MISSING_OK_PREFIXES)]
    if outside_aux or unexpected:
        raise Da3Unavailable(
            f"DA3 variant {name} weights do not match its code",
            f"{len(outside_aux)} non-auxiliary keys missing, "
            f"{len(unexpected)} unexpected.",
            "Re-fetch the pinned revision "
            f"{VARIANTS[name]['revision']} and retry; never run a "
            "partially loaded model.")
    device = torch.device("mps" if torch.backends.mps.is_available()
                          else "cpu")
    return model.to(device).eval()


def native_to_brighter_nearer(name: str, depth: np.ndarray) -> np.ndarray:
    """The lane convention (larger is nearer) from this variant's
    native scale. Refuses unmeasured variants rather than guessing."""
    if DIRECTION.get(name) != "smaller-is-nearer":
        raise Da3Unavailable(
            f"DA3 variant {name} has no measured native direction",
            "the faces-anchor check never ran for it.",
            "Measure it on the calibration frames before estimating.")
    return np.asarray(-np.asarray(depth, dtype=np.float64))


def estimator_for(name: str, process_res: int = 518):
    """A `depth.py`-protocol estimator (paths -> brighter-nearer maps)
    for the adopted variant `name`. Loads the model once."""

    def _estimate(paths: list[str]) -> list[np.ndarray]:
        import torch
        from PIL import Image

        model = _estimate.model  # type: ignore[attr-defined]
        maps = []
        with torch.no_grad():
            for path in paths:
                with Image.open(path) as handle:
                    image = handle.convert("RGB")
                pred = model.inference([np.asarray(image)],
                                       process_res=process_res)
                native = np.asarray(pred.depth[0], dtype=np.float64)
                maps.append(native_to_brighter_nearer(name, native))
                if str(getattr(model, "device", "")).startswith("mps"):
                    torch.mps.synchronize()
        return maps

    _estimate.model = load_variant(name)  # type: ignore[attr-defined]
    _estimate.variant = name  # type: ignore[attr-defined]
    _estimate.depth_kind = VARIANTS[name]["depth_kind"]  # type: ignore[attr-defined]
    return _estimate


def license_evidence(name: str) -> dict:
    """The scorecard's license class, from the publisher's own card."""
    spec = VARIANTS[name]
    return {"upstream_url": f"https://huggingface.co/{spec['hf_repo']}",
            "exact_version": spec["revision"],
            "retrieved_at": spec["retrieved_at"],
            "license": spec["license"],
            "code_upstream_url": CODE_REPO,
            "code_revision": CODE_REVISION}
