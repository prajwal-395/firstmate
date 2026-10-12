#!/usr/bin/env python3
"""The Apple Vision helper, shared by every reader that measures people.

Measures faces (boxes, head-pose yaw/pitch/roll, capture quality), face
landmarks, body pose, hand pose and a person-segmentation summary
through a compiled Swift helper (`vision_helper.swift` beside this file)
rather than pyobjc: no new pip dependencies, no import-time cost on
machines without it, and the call path is the harness the eval measured
(34 ms per frame warm for the original five requests, plus ~17-27 ms for
face capture quality). The helper binary is compiled once per source
revision into a temp cache; anything that fails - non-macOS, no swiftc,
compile error, run error - is `VisionUnavailable` or a `(None, reason)`
pair, so Vision is never the reason a run goes red.

Callers, each sampling its own frames:

- M3 `person_measurements` over the shared M2 frame grid (faces, lips,
  hands, pose, capture quality) - THE per-frame person record.
- M3d `person_regions`, which speaks the same `--list` protocol through
  its own helper source.
- The reel punch-in's body-pose aim (`subject_framing`), on the few
  frames of one window.

Step 1.04 no longer runs it (MU-12). It used to sample every clip at
5 Hz and store six `vision_*` keys that nothing read, a second Vision
pass over footage M3 already measures. A reader that needs faces reads
M3 by content digest; one that needs a denser rate raises M2's rate for
that source rather than running a second sampler.

The persistence filter drops single-sample phantom faces (a fist
detected as a face with landmarks is the measured case): a face is kept
iff a box with IoU >= `PERSISTENCE_IOU` exists at the previous or next
sample. Measured on 595 dense-sequence frames + 300 at a 5 Hz grid:
removes the sequence phantom, drops 2 fast-motion faces at 1 Hz
sampling, 0 at 5 Hz, at 6 us/frame.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SWIFT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vision_helper.swift")

#: A face survives iff a box with at least this IoU exists at an
#: adjacent sample. 0.3 is the measured knee (module docstring): it
#: removes the sequence phantom while costing nothing at 5 Hz.
PERSISTENCE_IOU = 0.3

class VisionUnavailable(Exception):
    """The helper cannot run here; the message is the fallback reason."""


def _helper_cache_dir(source: str = SWIFT_SOURCE) -> str:
    """Where the compiled helper lives, keyed by Swift source hash.

    System temp, outside every project and checkout: one compile per
    machine per source revision. A cleaned temp just recompiles.
    `source` names another helper that speaks the same `--list`
    protocol (M3d's `person_regions_helper.swift`); its hash keys its
    own directory, so two helpers never share a cache slot.
    """
    with open(source, "rb") as handle:
        digest = hashlib.sha1(handle.read()).hexdigest()[:12]
    path = os.path.join(tempfile.gettempdir(), "ren-vision", digest)
    os.makedirs(path, exist_ok=True)
    return path


def helper_available(source: str = SWIFT_SOURCE,
                     name: str = "vision_helper") -> tuple:
    """Whether the helper can run on this machine, and why not.

    Returns `(True, None)` or `(False, reason)`. Needs macOS and either
    swiftc (to compile) or an already-compiled binary in the cache.
    """
    if sys.platform != "darwin":
        return False, f"unavailable: platform {sys.platform} has no Vision"
    cached = os.path.join(_helper_cache_dir(source), name)
    if os.path.isfile(cached) and os.access(cached, os.X_OK):
        return True, None
    if shutil.which("swiftc") is None:
        return False, "unavailable: no swiftc to compile the helper"
    return True, None


def ensure_helper(source: str = SWIFT_SOURCE,
                  name: str = "vision_helper") -> tuple:
    """A runnable helper binary path, compiling on first use.

    Returns `(path, None)` or `(None, reason)`. Never raises: a failed
    compile is a fallback, not a failed run.
    """
    try:
        ok, reason = helper_available(source, name)
        if not ok:
            return None, reason
        bindir = _helper_cache_dir(source)
        binary = os.path.join(bindir, name)
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary, None
        if shutil.which("swiftc") is None:
            return None, "unavailable: no swiftc to compile the helper"
        result = subprocess.run(
            ["swiftc", "-O", source, "-o", binary],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, check=False,
        )
        if result.returncode != 0 or not os.path.isfile(binary):
            return None, (
                "unavailable: helper compile failed: "
                f"{result.stderr.strip()[-300:]}"
            )
        return binary, None
    except Exception as e:
        return None, f"unavailable: helper setup failed: {e}"


def measure_frames(image_paths: list, helper_path: str,
                   extra_args: tuple = ()) -> list:
    """One helper process over the whole batch; first frame pays cold.

    Returns the helper's per-image docs in input order. Raises
    `VisionUnavailable` on any failure - the caller converts that into
    its own recorded absence. `extra_args` follow `--list <file>` for a
    helper that takes more (M3d's `--grid w h`).
    """
    if not image_paths:
        raise VisionUnavailable("unavailable: no frames to measure")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8") as handle:
        handle.write("\n".join(image_paths) + "\n")
        list_path = handle.name
    try:
        result = subprocess.run(
            [helper_path, "--list", list_path, *extra_args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace",
            timeout=max(120.0, float(len(image_paths))),
            check=False,
        )
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if result.returncode != 0:
        raise VisionUnavailable(
            f"unavailable: helper exited {result.returncode}: "
            f"{result.stderr.strip()[-300:]}"
        )
    try:
        docs = json.loads(result.stdout)
    except ValueError as e:
        raise VisionUnavailable(
            f"unavailable: helper returned non-JSON: {e}")
    if not isinstance(docs, list) or len(docs) != len(image_paths):
        raise VisionUnavailable(
            f"unavailable: helper returned {len(docs) if isinstance(docs, list) else '?'} "
            f"docs for {len(image_paths)} frames"
        )
    return docs


def _iou(a: list, b: list) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0


def apply_persistence(faces_per_sample: list,
                      iou_thresh: float = PERSISTENCE_IOU) -> tuple:
    """Keep faces with temporal support; drop one-sample phantoms.

    `faces_per_sample` is a list per sample of boxes. Returns
    `(kept, removed_counts)`: `kept[i][j]` says whether face j at sample
    i survives (a box with IoU >= thresh at either neighbor), and
    `removed_counts[i]` is how many sample i lost. Sequence ends consult
    their single neighbor. Pure function of boxes - no thresholds on
    confidence, which the eval proved cannot separate phantoms anyway.
    """
    n = len(faces_per_sample)
    kept = []
    removed = []
    for i, boxes in enumerate(faces_per_sample):
        row = []
        for box in boxes:
            best = 0.0
            for j in (i - 1, i + 1):
                if 0 <= j < n:
                    for other in faces_per_sample[j]:
                        support = _iou(box, other)
                        if support > best:
                            best = support
            row.append(best >= iou_thresh)
        kept.append(row)
        removed.append(sum(1 for keep in row if not keep))
    return kept, removed
