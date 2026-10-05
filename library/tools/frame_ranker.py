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
   ``tests/unit/picture/test_picture_quality.py`` pins that.

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

Status: exercised on real frames 2026-10-04
-------------------------------------------
``extract_ranked_clip_thumbnail`` drives the whole path - probe, extract,
score, sharpness-gate, choose - and the real pass over the geo-podcast
clips is recorded in ``docs/evidence/frame_ranker.md``: the ranker beats
the legacy timestamp on the thumbnail clips with BOTH the shipped
openai/clip-vit-large-patch14 backbone and the laion2b backbone the head
was trained on, and the blind re-judge confirms the win is visible, not
just numerical.  The unit tests still pin the two bounds with stub
scorers; ``tests/qualification/test_frame_ranker_real.py`` runs the real
weights over a synthetic clip.
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
#: CLOSED 2026-10-04: the real pass ran BOTH this backbone and the exact
#: laion2b (laion/CLIP-ViT-L-14-laion2B-s32B-b82K); the ranker beats the
#: legacy timestamp on the thumbnail clips with either, and the blind
#: re-judge confirms the win on both.  Evidence:
#: ``docs/evidence/frame_ranker.md``.
CLIP_MODEL_ID = "openai/clip-vit-large-patch14"

#: The 4 KB linear head, fetched over HTTPS 200 from the LAION repo.
HEAD_URL = (
    "https://github.com/LAION-AI/aesthetic-predictor/raw/main/"
    "sa_0_4_vit_l_14_linear.pth"
)
HEAD_FILENAME = "sa_0_4_vit_l_14_linear.pth"

#: The sha256 of the head file. PINNED.
#:
#: Measured 2026-10-04 by downloading `HEAD_URL` and hashing it (4071
#: bytes). `download_head` refuses bytes that do not match, before the
#: weights are ever loaded - an unverified 4 KB of linear weights is a
#: silent re-ranking of every thumbnail. Upstream publishes no checksum
#: file, so this measured hash IS the pin - re-pin by the same
#: procedure if the head ever moves (it has not since 2022).
HEAD_SHA256 = "2cd4e60f4f24ae3bcd57b847b13c1f3ba27edc28cc1a7f9ce74ee9f421243cba"

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
    the shared HF cache at load time, nothing is vendored.

    The bytes are checked against the pinned `HEAD_SHA256`, on fetch
    AND on reuse - a cached head that does not match refuses with
    `FrameRankerUnavailable` rather than re-ranking on unknown
    weights. A fresh mismatch is deleted, never loaded.
    """
    import hashlib
    from pathlib import Path as _Path

    def _sha256(path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    _Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest = str(_Path(dest_dir) / HEAD_FILENAME)
    if _Path(dest).is_file() and _sha256(dest) == HEAD_SHA256:
        return dest
    if _Path(dest).is_file():
        _Path(dest).unlink()
    urllib.request.urlretrieve(HEAD_URL, dest)
    if _sha256(dest) != HEAD_SHA256:
        _Path(dest).unlink()
        raise FrameRankerUnavailable(
            f"LAION head sha256 mismatch (expected {HEAD_SHA256}): "
            f"the download was deleted, nothing was loaded.")
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


def _probe_duration(path: str) -> float:
    """Container duration in seconds, or raise - an unprobeable clip has no
    candidates to rank."""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=_pq.FFPROBE_TIMEOUT_S, check=False,
    )
    if probe.returncode != 0:
        raise FrameRankerUnavailable(f"ffprobe could not read {path}")
    import json as _json

    try:
        return float(_json.loads(probe.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise FrameRankerUnavailable(f"no duration for {path}") from exc


def extract_ranked_clip_thumbnail(
    clip_path: str,
    scorer: LaionAestheticScorer,
    work_dir: str | None = None,
    n_candidates: int | None = None,
    width: int = 960,
) -> dict:
    """Extract a clip's thumbnail by ranking candidate frames.

    Probes the clip, extracts ``candidate_timestamps`` interior frames (the
    legacy pick among them, so the old rule stays a candidate), scores each
    with the loaded ``scorer``, measures each one's sharpness, and applies the
    fail-closed ``choose`` gate.  Returns the winner, or ``None`` with
    ``fallback`` naming the legacy timestamp when nothing passes - the caller
    then uses exactly the frame it would have used before this module
    existed, never a soft one.

    Frames are written under ``work_dir`` (a fresh temp dir when omitted) and
    left there: the caller builds pairs or previews from them and removes the
    directory.  Raises ``FrameRankerUnavailable`` rather than returning a
    partial result when the clip cannot be probed or decoded.
    """
    import shutil as _shutil
    import tempfile as _tempfile
    from pathlib import Path as _Path

    duration = _probe_duration(clip_path)
    count = int(n_candidates) if n_candidates else N_THUMBNAIL_CANDIDATES
    timestamps = candidate_timestamps(duration, count)
    legacy_ts = round(legacy_timestamp(duration), 3)

    own_dir = work_dir is None
    work = _tempfile.mkdtemp(prefix="frame-ranker-") if own_dir else work_dir
    _Path(work).mkdir(parents=True, exist_ok=True)
    try:
        paths = []
        for ts in timestamps:
            frame_path = str(_Path(work) / f"t{ts:07.3f}.jpg")
            extracted = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-ss", str(ts), "-i", str(clip_path),
                 "-frames:v", "1", "-vf", f"scale={width}:-1",
                 "-q:v", "2", frame_path],
                capture_output=True, timeout=_pq.FFMPEG_TIMEOUT_S, check=False,
            )
            if extracted.returncode != 0 or not _Path(frame_path).is_file():
                raise FrameRankerUnavailable(
                    f"ffmpeg could not extract t={ts} from {clip_path}")
            paths.append(frame_path)

        scores = scorer.score_files(paths)
        candidates = []
        for ts, path, score in zip(timestamps, paths, scores):
            candidates.append({
                "id": f"t{ts:07.3f}",
                "timestamp": ts,
                "path": path,
                "aesthetic_score": round(float(score), 4),
                "sharpness": round(sharpness_of_image(path), 2),
            })
        winner, report = choose(candidates)
        return {
            "clip_path": str(clip_path),
            "duration": round(duration, 3),
            "legacy_timestamp": legacy_ts,
            "candidates": candidates,
            "winner": winner,
            "report": report,
            "fallback": None if winner else "legacy_timestamp",
        }
    finally:
        if own_dir:
            _shutil.rmtree(work, ignore_errors=True)


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
    # a writable copy: torch.from_numpy warns on the read-only array
    # frombuffer returns, and the warning is noise at scoring time
    return _np.frombuffer(raw[: width * height * 3],
                          dtype=_np.uint8).reshape(height, width, 3).copy()
