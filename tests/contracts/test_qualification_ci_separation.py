"""Qualification stays out of the ordinary test and pipeline run.

OPS-016/017/018: fast contract and unit tests run on every code change, while
real-model qualification runs only when a bakeoff lane explicitly requests it.
This contract fails any change that would drag the heavy path into either:

1. Harness unit tests must stay lightweight: no ``real_model`` marker and no
   heavy model-runtime imports.
2. Harness gate modules must import nothing a headless unit runner lacks:
   stdlib plus the named lightweight in-repo helpers.
3. Pipeline runtime (processes, steps, the ``ren`` front door) must never
   import the qualification gate, so ordinary runs cannot trigger it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

HARNESS_TESTS = (
    "tests/unit/tools/test_model_adapter_contract.py",
    "tests/unit/tools/test_model_qualification.py",
    "tests/unit/tools/test_multimodal_eval_dataset.py",
    "tests/unit/tools/test_qualification_resources.py",
)

HARNESS_MODULES = (
    "library/tools/model_adapter_contract.py",
    "library/tools/model_qualification.py",
    "library/tools/multimodal_eval_dataset.py",
    "library/tools/qualification_resources.py",
)

# Model runtimes and media decoders a headless unit runner must not need.
HEAVY_THIRD_PARTY = {
    "torch", "torchvision", "torchaudio", "tensorflow", "jax", "mlx",
    "transformers", "diffusers", "accelerate", "optimum", "onnxruntime",
    "onnx", "openvino", "coremltools", "whisper", "faster_whisper",
    "librosa", "essentia", "ultralytics", "segment_anything", "supervision",
    "mediapipe", "insightface", "sentence_transformers", "cv2",
}

# In-repo helpers the harness is allowed to reuse (lightweight, no models).
ALLOWED_IN_REPO = {
    "library.tools.evaluation_scorecard",
    "library.tools.editorial_eval_corpus",
    "library.tools.project_model_contract",
    "library.tools.qualification_resources",
    "library.tools.model_qualification",
    "library.tools.model_adapter_contract",
    "library.tools.multimodal_eval_dataset",
}

PIPELINE_ROOTS = ("library/processes", "library/steps", "ren")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if node.module == "library.tools":
                    names.extend(f"library.tools.{alias.name}"
                                 for alias in node.names)
                else:
                    names.append(node.module)
    return names


def test_harness_unit_tests_have_no_real_model_marker():
    offenders = []
    for relative in HARNESS_TESTS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        if "real_model" in text:
            offenders.append(relative)
    assert not offenders, (
        "harness unit tests must stay in the ordinary run: "
        + ", ".join(offenders))


def test_harness_modules_import_no_heavy_runtime():
    offenders = []
    for relative in HARNESS_MODULES:
        for name in _imports(REPO_ROOT / relative):
            root = name.split(".")[0]
            if root in HEAVY_THIRD_PARTY:
                offenders.append(f"{relative} imports {name}")
    assert not offenders, (
        "qualification harness must not require model runtimes: "
        + "; ".join(offenders))


def test_harness_modules_use_only_lightweight_in_repo_helpers():
    offenders = []
    for relative in HARNESS_MODULES:
        for name in _imports(REPO_ROOT / relative):
            if name.startswith("library.") and name not in ALLOWED_IN_REPO:
                offenders.append(f"{relative} imports {name}")
    assert not offenders, (
        "harness must reuse only lightweight helpers: " + "; ".join(offenders))


def test_pipeline_runtime_never_imports_the_qualification_gate():
    gate_names = {"model_qualification", "multimodal_eval_dataset",
                  "qualification_resources"}
    offenders = []
    for root in PIPELINE_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            for name in _imports(path):
                if name.split(".")[-1] in gate_names or name in ALLOWED_IN_REPO:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
    assert not offenders, (
        "pipeline runtime must not reach the qualification gate: "
        + "; ".join(offenders))
