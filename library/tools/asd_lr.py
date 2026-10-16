"""LR-ASD active-speaker scoring behind an explicit-checkout adapter.

Benchmark candidate only (AUD-030). This module integrates LR-ASD
(Liao et al., IJCV 2025) as something Ren can RUN against its own face
tracks and source clocks; it does not adopt it, threshold it, or treat a
high score as speaker identity. Scores are emitted as
``active_speaker_score`` observations carrying the raw value, and the
Project Model projection
(:func:`library.tools.cross_modal_grounding.add_active_speaker_candidates`)
turns them into ``speaking_person_candidate`` relations - candidate and
inferred, never verified.

What this adapter refuses, on purpose:

* It never vendors the upstream implementation. The checkpoint and the
  publisher checkout are passed as explicit paths and read from there;
  nothing is fetched, downloaded, or imported from an implicit location.
* It never hardcodes CUDA. The upstream demo assumes CUDA; Ren selects
  MPS when the Metal backend is present and CPU otherwise, with an
  explicit override that is honoured only when that backend exists.
* It never ships weights. The checkpoint stays a personal-benchmark
  file outside the repo; research-only weights are never committed.
* It never calibrates. No threshold is selected here; per-frame scores
  are evidence, and any operating point is chosen on calibration agent
  labels elsewhere.

License state (checked 2026-10-16): the publisher repository advertises
MIT for its source. The checkpoint and training-data commercial terms
are still pending the license lane's decision, so the candidate stays
research-only and personal-benchmark-only.

Hardware basis: the 0.84M-parameter model runs at full precision on the
24 GB Mac (MPS). No lower-precision rerun is owed unless it makes the
shortlist.

Measured 2026-10-16 (torch 2.8.0): MPS has no ``max_pool3d`` kernel, so
inference refuses on a pure-MPS device and needs
``PYTORCH_ENABLE_MPS_FALLBACK=1`` (those ops run on CPU). The adapter
does not set that variable itself; the caller sets it and records the
device as MPS-with-CPU-fallback. Adapter output was verified
frame-identical against the calibration smoke run under that setting.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

FPS = 25
MFCC_NUMCEP = 13
MFCC_WINLEN = 0.025
MFCC_WINSTEP = 0.010
AUDIO_SAMPLE_RATE = 16000
# Upstream multi-duration aggregation windows, in seconds.
INFER_DURATIONS = (1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6)
FACE_CROP_PX = 112
SCORE_KIND = "uncalibrated"

PUBLISHER_REVISION = "1b6dcd2d8fc2895683de6508ec6294ec47d388ca"
CHECKPOINT_NAME = "pretrain_AVA.model"


def face_crop_rect(
    box: tuple[float, float, float, float],
    *,
    sample_size: tuple[int, int],
    source_size: tuple[int, int],
    expand: float = 1.8,
) -> tuple[int, int, int]:
    """Square source-frame crop rect for one measured face box.

    ``box`` is (x1, y1, x2, y2) in the sample frame (e.g. M3's 1408x792
    coordinates); the crop is taken from the full source frame. The box
    is scaled up, expanded around its center, and clamped to the frame
    origin. Returns (x, y, side) in source pixels.
    """
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
        sample_w, sample_h = int(sample_size[0]), int(sample_size[1])
        source_w, source_h = int(source_size[0]), int(source_size[1])
        expand_f = float(expand)
    except (TypeError, ValueError) as exc:
        raise ValueError("face box and sizes must be numeric") from exc
    for value in (x1, y1, x2, y2, expand_f):
        if not math.isfinite(value):
            raise ValueError("face box and expand must be finite")
    if sample_w <= 0 or sample_h <= 0 or source_w <= 0 or source_h <= 0:
        raise ValueError("sample and source sizes must be positive")
    if x2 <= x1 or y2 <= y1:
        raise ValueError("face box must have positive width and height")
    if expand_f <= 0:
        raise ValueError("expand must be positive")
    scale = source_w / sample_w
    side = int(round((y2 - y1) * scale * expand_f))
    if side <= 0:
        raise ValueError("face box is too small to crop at source scale")
    cx = int(round((x1 + x2) / 2 * scale))
    cy = int(round((y1 + y2) / 2 * scale))
    x = max(0, cx - side // 2)
    y = max(0, cy - side // 2)
    if x + side > source_w or y + side > source_h:
        raise ValueError("expanded crop overflows the source frame")
    return (x, y, side)


def select_device(explicit: str | None = None) -> str:
    """MPS when the Metal backend is present, CPU otherwise.

    An explicit ``"mps"``/``"cuda"`` is honoured only when torch reports
    that backend available; otherwise it raises instead of silently
    running somewhere else. ``"cpu"`` never imports torch.
    """
    if explicit == "cpu":
        return "cpu"
    if explicit is not None and explicit not in ("mps", "cuda"):
        raise ValueError(f"unknown device {explicit!r}")
    try:
        import torch
    except ImportError:
        if explicit is not None:
            raise ValueError(
                "torch is not installed, so no accelerated device exists")
        return "cpu"
    backends = {
        "mps": torch.backends.mps.is_available(),
        "cuda": torch.cuda.is_available(),
    }
    if explicit is not None:
        if not backends[explicit]:
            raise ValueError(
                f"explicit device {explicit!r} is not available here")
        return explicit
    if backends["mps"]:
        return "mps"
    return "cpu"


def _resolve_paths(checkout_dir: str | Path,
                   checkpoint: str | Path) -> tuple[Path, Path]:
    checkout = Path(checkout_dir)
    model_file = checkout / "model" / "Model.py"
    if not model_file.is_file():
        raise FileNotFoundError(
            f"LR-ASD checkout has no model/Model.py: {checkout}")
    ckpt = Path(checkpoint)
    if not ckpt.is_file():
        raise FileNotFoundError(
            f"LR-ASD checkpoint not found (never downloaded): {ckpt}")
    return checkout, ckpt


def load_checkpoint(*, checkout_dir: str | Path, checkpoint: str | Path,
                      device: str = "cpu") -> tuple[Any, Any]:
    """Load the publisher checkpoint onto ``device`` from explicit paths.

    Returns (asd_model, classifier) in eval mode. File existence is
    checked before torch is imported, so a missing path refuses without
    needing the ML stack. Nothing is ever fetched from the network.
    """
    checkout, ckpt = _resolve_paths(checkout_dir, checkpoint)
    selected = select_device(device)
    import torch

    sys.path.insert(0, str(checkout))
    try:
        from model.Model import ASD_Model
    finally:
        try:
            sys.path.remove(str(checkout))
        except ValueError:
            pass
    state = torch.load(ckpt, map_location="cpu", weights_only=True)
    model = ASD_Model()
    model.load_state_dict({
        key.removeprefix("model."): value
        for key, value in state.items() if key.startswith("model.")})
    classifier = torch.nn.Linear(128, 2)
    classifier.load_state_dict({
        key.removeprefix("lossAV.FC."): value
        for key, value in state.items() if key.startswith("lossAV.FC.")})
    model.eval().to(selected)
    classifier.eval().to(selected)
    return model, classifier


def score_frames(model: Any, classifier: Any, frames: Any,
                 audio_features: Any, *, fps: int = FPS,
                 device: str = "cpu") -> list[float]:
    """Aggregate multi-duration LR-ASD scores for one face crop sequence.

    ``frames`` is (T, 112, 112) grayscale float32; ``audio_features`` is
    (T*4, 13) MFCC rows. Returns one raw score per frame, rounded to six
    decimals. Validation runs before torch is touched, so shape errors
    refuse without needing the ML stack.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    try:
        frame_count = len(frames)
        feature_rows = len(audio_features)
    except TypeError as exc:
        raise ValueError("frames and audio_features must be sequences") from exc
    if frame_count <= 0:
        raise ValueError("frames must not be empty")
    usable = min(frame_count, feature_rows // 4)
    if usable <= 0:
        raise ValueError("audio features cover no video frame")
    import numpy as np
    import torch

    frames_np = np.asarray(frames[:usable], dtype=np.float32)
    audio_np = np.asarray(audio_features[:usable * 4], dtype=np.float32)
    if frames_np.shape[1:] != (FACE_CROP_PX, FACE_CROP_PX):
        raise ValueError(
            f"frames must be {FACE_CROP_PX}x{FACE_CROP_PX} crops")
    if audio_np.shape[1] != MFCC_NUMCEP:
        raise ValueError(
            f"audio features must have {MFCC_NUMCEP} cepstra")
    audio_tensor = torch.from_numpy(audio_np).float()
    video_tensor = torch.from_numpy(frames_np).float()
    duration_seconds = usable / fps
    runs: list[list[float]] = []
    with torch.inference_mode():
        for duration in INFER_DURATIONS:
            batch_size = math.ceil(duration_seconds / duration)
            scores: list[float] = []
            for i in range(batch_size):
                audio = audio_tensor[i * duration * 100:(i + 1) * duration * 100]
                video = video_tensor[
                    i * duration * fps:(i + 1) * duration * fps]
                nframes = min(len(video), len(audio) // 4)
                if nframes <= 0:
                    continue
                audio = audio[:nframes * 4].unsqueeze(0).to(device)
                video = video[:nframes].unsqueeze(0).to(device)
                audio_out = model.forward_audio_frontend(audio)
                visual_out = model.forward_visual_frontend(video)
                out = model.forward_audio_visual_backend(audio_out, visual_out)
                logits = classifier(out.squeeze(1))
                scores.extend(
                    torch.softmax(logits, dim=-1)[:, 1].cpu().tolist())
            runs.append(scores[:usable])
    aligned = [row for row in runs if len(row) == usable]
    if not aligned:
        raise ValueError("LR-ASD produced no aligned duration scores")
    mean = np.mean(np.asarray(aligned, dtype=np.float64), axis=0)
    return [round(float(v), 6) for v in mean.tolist()]


def scores_to_observations(
    *,
    scores: list[float],
    fps: int,
    start_tick: int,
    clock_id: str,
    asset_id: str,
    voice_track: str,
    face_track: str,
    provenance_id: str,
    observation_id_prefix: str,
    voice_asset_id: str | None = None,
    frame_scores_path: str | None = None,
) -> list[dict[str, Any]]:
    """Emit one ``active_speaker_score`` observation per scored frame.

    Ticks are milliseconds; frame ``i`` covers
    ``[start_tick + i*step, start_tick + (i+1)*step)`` where
    ``step = round(1000/fps)``. Every observation carries the raw score
    and names its tracks; ``confidence`` stays None because the value is
    uncalibrated evidence, never a calibrated probability. A non-finite
    score refuses instead of entering the Project Model.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    if type(start_tick) is not int or start_tick < 0:
        raise ValueError("start_tick must be a non-negative integer")
    for name, value in (("clock_id", clock_id), ("asset_id", asset_id),
                        ("voice_track", voice_track),
                        ("face_track", face_track),
                        ("provenance_id", provenance_id),
                        ("observation_id_prefix", observation_id_prefix)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
    if not scores:
        raise ValueError("scores must not be empty")
    step = int(round(1000 / fps))
    if step <= 0:
        raise ValueError("fps implies a sub-millisecond frame step")
    observations = []
    for i, raw in enumerate(scores):
        try:
            raw_score = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"score at frame {i} is not numeric") from exc
        if not math.isfinite(raw_score):
            raise ValueError(
                f"score at frame {i} is not finite and cannot be evidence")
        payload: dict[str, Any] = {
            "voice_track": voice_track,
            "face_track": face_track,
            "raw_score": raw_score,
            "score_kind": SCORE_KIND,
            "frame_index": i,
            "fps": fps,
        }
        if voice_asset_id is not None:
            payload["voice_asset_id"] = voice_asset_id
        if frame_scores_path is not None:
            payload["frame_scores_path"] = frame_scores_path
        observations.append({
            "id": f"{observation_id_prefix}-{i:06d}",
            "asset_id": asset_id,
            "interval": {"clock_id": clock_id,
                         "start": start_tick + i * step,
                         "end": start_tick + (i + 1) * step},
            "modality": "active_speaker_score",
            "payload": payload,
            "confidence": None,
            "provenance_id": provenance_id,
        })
    return observations
