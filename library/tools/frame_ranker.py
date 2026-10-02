"""Aesthetic frame ranking for still selection, gated on sharpness.

FIRSTMATE VERDICT 2026-09-18, overturning the spike's "do not adopt" on the
one test the spike could not run - it cannot see pictures, and the question
was visual.  The spike's measurements all stand; its recommendation did not.
Spike report: ``data/vep-evaluate-open-video-models/report.md`` (firstmate
data dir, outside this repo) plus ``pairs/``.

What it replaces
----------------
The legacy thumbnail timestamp rule - ``min(1.0, duration * 0.1)``,
about one second into a clip - which is reliably mid-movement: a person
settling, glancing away, still gesturing.  Pairs 1 and 3 show the ranker's
pick square to the lens and settled where the timestamp frame is mid-gesture.
The ranker is not being clever; it is avoiding a systematically bad rule.

Two bounds, both from the spike's own measurements, both non-negotiable
------------------------------------------------------------------------
1. GATED ON SHARPNESS.  Blur-blindness is measured, not feared: a frame at
   Laplacian variance 65 outscored a sharp pipeline pick 4.55 to 4.09.  The
   head scores COMPOSITION, not focus, so composition alone must never
   choose a frame.  ``choose`` below is fail-closed: a candidate whose
   sharpness is unknown or below the floor cannot win, and when nothing
   passes the caller falls back to the legacy timestamp rather than
   promoting a soft frame.
2. NOT A HOLD-POINT INSTRUMENT.  On the Reel 30 closing-freeze drift the
   ranker beats the current hold by 0.04 - noise - and prefers the softer
   frame.  Nothing in the ending-freeze path
   (``library/tools/reel_ending.py``, ``reel_build._with_freeze``) or the
   named-frame grabs (``gate_stills.py``, step 7.02) may import this module;
   ``tests/unit/picture/test_frame_ranker.py`` pins that.

What it costs (measured on the captain's machine, not re-derived)
-----------------------------------------------------------------
0.48 s per frame on CPU, 1.6 GB CLIP weights, MIT licence checked against
the LAION repo's own LICENSE file.  Plain torch CPU - no ONNX needed, so
the survey's "ONNX export unverified" caveat does not apply to this pick.

Scope: no caller today
----------------------
Its one caller was ``thumbnail_extractor``, which served thumbnails to the
retired dashboard and was removed with it.  The ranker is kept as the
measured selection for whatever next shows a single representative frame.

Deliberately untouched: the rough-cut review STRIPS (``window_frames``,
step 3.03).  Those show every placed window - both ends plus middle,
unranked by architectural rule (``DECLINED_TO_RANK``; AGENTS.md 10.5:
whatever selects a shortlist becomes the chooser).  There is no
single-frame selection there to replace, and ranking strips would
contradict the rule that every candidate window gets one.

Status: unwired, not yet exercised on real frames
-------------------------------------------------
The selection and the gate run against injected scorers in tests.  The
real head has not scored a production frame.
"""

from __future__ import annotations

import subprocess
import urllib.request

# The sharpness floor is picture_quality's own absolute floor, not a new
# magic number: it is calibrated at SAMPLE_SHORT_SIDE_PX, which is why
# sharpness_of_image scales a still to that geometry before measuring.
# The spike's blurred frame read 65 against that scale - below the floor -
# while the sharp picks read 200-365.  One implementation of the Laplacian
# lives in picture_quality; this module reuses it rather than carrying a
# second one that could drift.
from library.tools.analysis import picture_quality as _pq

#: What the LAION head is: a linear probe on CLIP image embeddings (MIT,
#: Copyright (c) 2022 LAION AI).  Verified 2026-09-18 against the repo's
#: own LICENSE file; no S-Lab/NC taint on this pick.
LAION_HEAD_LICENCE = "MIT (LAION-AI/aesthetic-predictor, 2022)"

#: The CLIP backbone the spike actually ran: HuggingFace
#: openai/clip-vit-large-patch14 (same architecture and 768 dim as the
#: OpenCLIP laion2b backbone LAION trained the head on, different training
#: data).  Spike report section 1: the right fidelity for "does ranking
#: beat timestamp picking", NOT the exact LAION number.  The 200-frame
#: retest with the exact backbone is still open (report section 6).
CLIP_MODEL_ID = "openai/clip-vit-large-patch14"

#: The 4 KB linear head, fetched over HTTPS 200 from the LAION repo.
HEAD_URL = (
    "https://github.com/LAION-AI/aesthetic-predictor/raw/main/"
    "sa_0_4_vit_l_14_linear.pth"
)
HEAD_FILENAME = "sa_0_4_vit_l_14_linear.pth"

#: The spike scored 8 evenly spaced alternatives plus the pipeline pick.
N_THUMBNAIL_CANDIDATES = 9


class FrameRankerUnavailable(RuntimeError):
    """The real scorer cannot run here, so nothing was ranked.

    Raised - never swallowed into a silent timestamp pick - so the caller
    falls back to the legacy rule KNOWING it fell back.
    """


def legacy_timestamp(duration_s: float) -> float:
    """The rule this adoption replaces, stated once.

    ``min(1.0, duration * 0.1)`` - about one second into a clip, reliably
    mid-movement.  Kept as the fallback and as a guaranteed candidate, so
    a run the ranker cannot judge gets exactly what it got before.
    """
    duration_s = float(duration_s or 0.0)
    if duration_s <= 0:
        return 1.0
    return min(1.0, duration_s * 0.1)


def candidate_timestamps(
    duration_s: float, count: int = N_THUMBNAIL_CANDIDATES
) -> list:
    """Interior instants to judge, legacy pick always among them.

    Evenly spaced across the clip inside a small margin (frames at the
    exact edges can fail to decode), with the legacy timestamp swapped in
    for its nearest neighbour so the old pick stays a candidate.  Sorted,
    deterministic, all inside ``[0, duration]``.
    """
    duration_s = float(duration_s or 0.0)
    if duration_s <= 0:
        return [1.0]
    count = max(1, int(count))
    margin = min(0.5, duration_s * 0.02)
    lo, hi = margin, max(margin, duration_s - margin)
    if count == 1 or hi <= lo:
        times = [round((lo + hi) / 2.0, 3)]
    else:
        times = [round(lo + i * (hi - lo) / (count - 1), 3) for i in range(count)]
    legacy = round(legacy_timestamp(duration_s), 3)
    nearest = min(range(len(times)), key=lambda i: abs(times[i] - legacy))
    times[nearest] = legacy
    return sorted(times)


def sharpness_of_image(path: str) -> float:
    """Laplacian variance of a still, on the floor's own geometry.

    Decodes through ffmpeg to greyscale at picture_quality's short-side
    scale and measures with its Laplacian, so SHARPNESS_ABSOLUTE_FLOOR
    applies as calibrated.  Raises (never returns a forgiving default):
    an unmeasurable still is UNKNOWN sharpness, and unknown sharpness
    cannot pass the gate.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", str(path)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=_pq.FFPROBE_TIMEOUT_S, check=False,
    )
    if probe.returncode != 0:
        raise FrameRankerUnavailable(f"ffprobe could not read {path}")
    import json as _json

    try:
        streams = _json.loads(probe.stdout).get("streams") or []
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
    except (ValueError, IndexError, AttributeError):
        width, height = 0, 0
    if width <= 0 or height <= 0:
        raise FrameRankerUnavailable(f"no frame size for {path}")
    out_w, out_h = _pq._sample_dimensions(
        width, height, _pq.SAMPLE_SHORT_SIDE_PX)

    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vf", f"scale={out_w}:{out_h}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=_pq.FFMPEG_TIMEOUT_S, check=False,
    )
    raw = decoded.stdout
    if decoded.returncode != 0 or len(raw) < out_w * out_h:
        raise FrameRankerUnavailable(f"ffmpeg could not decode {path}")
    import numpy as _np

    frame = _np.frombuffer(raw[: out_w * out_h], dtype=_np.uint8).reshape(
        out_h, out_w).astype(_np.float32)
    return _pq._laplacian_variance(frame)


def choose(candidates: list, sharpness_floor: float = None) -> tuple:
    """Pick a frame by composition among frames in focus.

    ``candidates`` are ``{"id", "timestamp", "aesthetic_score",
    "sharpness"}`` - ``sharpness`` a Laplacian variance on the floor's
    geometry, or ``None`` when unmeasured.  Returns ``(winner, report)``;
    ``winner`` is ``None`` when nothing passes, and the caller falls back
    to the legacy timestamp.  Ties break to the earliest timestamp, so the
    pick is deterministic.

    The gate is fail-closed twice over: below-floor sharpness is
    ineligible, and UNKNOWN sharpness is ineligible too.  Composition
    alone never chooses a frame - that is the spike's finding 2, wired in.
    """
    floor = float(sharpness_floor if sharpness_floor is not None
                  else _pq.SHARPNESS_ABSOLUTE_FLOOR)
    eligible, rejected = [], []
    for cand in candidates:
        sharp = cand.get("sharpness")
        if sharp is None:
            rejected.append({**cand, "reason": "sharpness_unknown"})
        elif float(sharp) < floor:
            rejected.append({**cand, "reason": "below_sharpness_floor"})
        else:
            eligible.append(cand)
    if not eligible:
        return None, {
            "winner": None,
            "reason": "no_candidate_passed_sharpness_gate",
            "sharpness_floor": floor,
            "eligible": [],
            "rejected": rejected,
        }
    winner = min(eligible, key=lambda c: (-float(c["aesthetic_score"]),
                                          float(c.get("timestamp") or 0.0)))
    return winner, {
        "winner": winner.get("id"),
        "reason": "highest_aesthetic_score_above_sharpness_floor",
        "sharpness_floor": floor,
        "eligible": [c.get("id") for c in eligible],
        "rejected": [{k: r.get(k) for k in ("id", "reason")} for r in rejected],
    }


def _normalized_clip_features(feats):
    """L2-normalised CLIP features across the transformers 5.x boundary.

    `get_image_features` / `get_text_features` return
    `BaseModelOutputWithPooling` from transformers 5.x up - calling `.norm`
    on that raises AttributeError (measured 2026-09-30 against 5.15.0) -
    and a bare tensor before.  `pooler_output` IS the feature vector in
    both shapes, so read that and normalise it.
    """
    tensor = getattr(feats, "pooler_output", feats)
    return tensor / tensor.norm(p=2, dim=-1, keepdim=True)


def download_head(dest_dir: str) -> str:
    """Fetch the 4 KB LAION head.  Network only; the CLIP weights come from
    the shared HF cache at load time, nothing is vendored."""
    from pathlib import Path as _Path

    _Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest = str(_Path(dest_dir) / HEAD_FILENAME)
    urllib.request.urlretrieve(HEAD_URL, dest)
    return dest


class LaionAestheticScorer:
    """The spike's ranker, loadable where the weights live.

    torch/transformers import INSIDE ``load`` so merely importing this
    module never drags 1.6 GB into a pipeline process.  ``score_files``
    before ``load`` raises: an unloaded scorer is not a zero scorer.
    """

    def __init__(self, head_path: str, model_id: str = CLIP_MODEL_ID):
        self.head_path = head_path
        self.model_id = model_id
        self._model = None
        self._processor = None
        self._head = None

    def load(self) -> "LaionAestheticScorer":
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
        except ImportError as exc:
            raise FrameRankerUnavailable(
                f"torch/transformers not installed: {exc}") from exc
        try:
            self._model = CLIPModel.from_pretrained(self.model_id)
            self._processor = CLIPProcessor.from_pretrained(self.model_id)
            self._model.eval()
            head = torch.nn.Linear(768, 1)
            state = torch.load(self.head_path, map_location="cpu",
                               weights_only=True)
            head.load_state_dict(state)
            head.eval()
            self._head = head
            self._torch = torch
        except Exception as exc:  # noqa: BLE001 - any load failure is "no ranker"
            raise FrameRankerUnavailable(
                f"could not load aesthetic head: {exc}") from exc
        return self

    def score_files(self, paths: list) -> list:
        """Aesthetic scores, higher-is-better, in input order."""
        if self._model is None or self._head is None:
            raise FrameRankerUnavailable(
                "scorer used before load - call load() first")
        torch = self._torch
        scores = []
        with torch.no_grad():
            for path in paths:
                arr = _decode_rgb(path)
                inputs = self._processor(images=arr, return_tensors="pt")
                feats = _normalized_clip_features(
                    self._model.get_image_features(**inputs))
                scores.append(float(self._head(feats).squeeze().item()))
        return scores


def _decode_rgb(path: str):
    """A still as an RGB numpy array, via the ffmpeg this pipeline
    already shells out to - no new imaging dependency at the boundary."""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", str(path)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=_pq.FFPROBE_TIMEOUT_S, check=False,
    )
    if probe.returncode != 0:
        raise FrameRankerUnavailable(f"ffprobe could not read {path}")
    import json as _json

    import numpy as _np

    try:
        streams = _json.loads(probe.stdout).get("streams") or []
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
    except (ValueError, IndexError, AttributeError):
        width, height = 0, 0
    if width <= 0 or height <= 0:
        raise FrameRankerUnavailable(f"no frame size for {path}")
    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=_pq.FFMPEG_TIMEOUT_S, check=False,
    )
    raw = decoded.stdout
    if decoded.returncode != 0 or len(raw) < width * height * 3:
        raise FrameRankerUnavailable(f"ffmpeg could not decode {path}")
    return _np.frombuffer(raw[: width * height * 3],
                          dtype=_np.uint8).reshape(height, width, 3)
