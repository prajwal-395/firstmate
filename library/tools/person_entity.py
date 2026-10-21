"""The person entity store: M3b (`identity.json`) and M1b
(`speakers.json`) of the per-source footage memory
(`docs/SOURCE_MEMORY.md`).

Scout basis: `data/vep-video-intelligence-entities-and-search/report.md`
section 2 (in firstmate's home). Rigor-gate basis:
`data/vep-person-entity-store/eval/{plan,results}.md` - a false-accept
study on 83 real faces (the captain's own footage, 2 cameras, profile
and eyes-closed frames included) measured FAR=0/FRR=0 at
`FACE_MATCH_THRESHOLD`. The study is rerun on the frames this module
actually decodes by `library/tools/face_identity_study.py`.

**Identity comes from measured embeddings, never from attributes.**
buffalo_l also reports sex/age; the scout measured those WRONG (Akshita
read as M 34-43) and this module never reads them. A face's identity is
its 512-d ArcFace embedding and nothing else.

**Who a voice belongs to is the MEASURED voice, not whoever is on
screen.** The person on screen is not always the person talking: a
camera picks up the other speaker, and the roster used to file that
speech under the person in shot, so one speaker resolved to two people
depending on the angle. The rules and the measurement are in
`docs/IDENTITY_CONTINUITY.md`; the measurement itself is
`library/tools/identity_continuity.py`.

**What this module measures, per source file:**

- Face tracks: sampled frames decoded from the SOURCE - at the shared M2
  I-frame times (`source_memory.read_m2`) when a fresh sample exists,
  else at this module's own planned times. The decoded frame is capped
  at `FACE_INPUT_MAX_WIDTH` before insightface `buffalo_l` detects and
  embeds; faces are clustered within one source by cosine >=
  `FACE_MATCH_THRESHOLD`. Never the 384 px M2 thumbnails themselves:
  see `FRAME_SOURCE_M2_TIMES`.
- Voice tracks: the source's own program-track audio, diarized with offline
  Sortformer by default and ECAPA embeddings per turn for identity; the
  existing ECAPA diarizer remains the fallback (`single_track_diarization`)
  - reused as-is, not re-measured here. Model license conditions travel
  with each voice track. The same tracks are
  written to M1b (`speakers.json`), the diarization lane's reserved slot,
  beside their M3b home.
- Speech-face links: a face span and a voice turn that overlap in time,
  recorded with the basis that produced them. Never invented past what
  overlaps.

**Cross-source voice matching:** measured and on.
`match_voices_across_sources` matches voice tracks across sources by
ECAPA cosine >= `VOICE_MATCH_THRESHOLD`, a threshold set by
`library/tools/voice_identity_study.py` (FAR=0/FRR=0 on the captain's
cameras, lavs and phone takes - `docs/CROSS_SOURCE_VOICE_MEASUREMENT.md`).
Cross-source PERSON identity is still face-only: a voice track travels
with whichever person its speech-face link (within its own source)
attaches it to, and a voice no face in its own source claims JOINS the
person whose linked voice it matches (`join_voices_across_sources`). A
voice match never merges two persons.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from library.tools import (footage_identity, project_ref, shared_environment,
                               source_memory)
    from library.tools.ren_refusal import RenRefusal
    from library.tools import single_track_diarization as diarization
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity, project_ref, shared_environment, source_memory
    from tools.ren_refusal import RenRefusal
    from tools import single_track_diarization as diarization

FACE_MATCH_THRESHOLD = 0.25
"""Cosine floor for 'same person' on a 512-d ArcFace embedding.

Measured, not chosen, by `face_identity_study` (2026-10-02): the 83-face
hard-case set passes at 512 px (closest different-person 0.110, weakest
same-person 0.259), while the combined 508-frame set fails at 512 px
(157 false rejects) and 1280 px (2 false rejects). At 1408 px the same
set has FAR=0/FRR=0 at this threshold: closest different-person 0.1452,
weakest same-person 0.2647. This is the smallest passing width in the
measured sweep. Biased toward FAR over FRR: a false merge silently
corrupts a person's whole span history; a missed match leaves two
person_ids for one person, visible and recoverable in search results.
"""

VOICE_MATCH_THRESHOLD = 0.45
"""Cosine floor for 'same voice' on a 192-d ECAPA embedding, across sources.

Measured, not chosen, by `voice_identity_study` (2026-10-12) on 12 of the
captain's sources - the geo-podcast's two cameras (each speaker's own
lav, and the other speaker's voice bleeding into it) and five phone
takes - labelled by physical track: tracks of at least
`VOICE_MATCH_MIN_SECONDS` have FAR=0/FRR=0 from 0.30 to 0.65 (closest
different-person 0.2925, weakest same-person 0.6954). 0.45 is the
FAR-biased point of that band: FAR is 0 at 0.40 and above even for 3 s
chunks (closest different-person 0.3874), so a short track that slips
the gate still cannot false-merge. Full record:
`docs/CROSS_SOURCE_VOICE_MEASUREMENT.md`.
"""

VOICE_MATCH_MIN_SECONDS = 21.0
"""Speech a voice track must carry before it is matched across sources.
Every same-person false reject in the study involved a diarizer fragment
of 4-6 s; 40-window chunks (>= 21 s of speech) pass FAR=0/FRR=0 from 0.30
to 0.50, and tracks this long from 0.30 to 0.65. A shorter track stays
where its own source's speech-face link put it."""

FRAME_SAMPLE_INTERVAL_S = 10.0
"""How often a source is sampled for faces. `resolve_sample_frames`
snaps each planned timestamp to the nearest shared M2 I-frame
(`source_memory.read_m2`) when a fresh sample exists for this digest;
either way the frame is this module's own sparse `ffmpeg -ss` decode at
source resolution, capped to `FACE_INPUT_MAX_WIDTH` on output."""

MAX_FRAME_SAMPLES = 120
"""Upper bound on frames sampled per source, so an hours-long file costs
minutes, not the file's own runtime. Repeated builds reuse the stored face
measurement while its source, sample plan, threshold, input size and model
pack stay the same."""

FACE_INPUT_MAX_WIDTH = 1408
"""Width cap for ArcFace input. Smallest width in the measured sweep
that passes the combined production and hard-case study."""

FACE_MEASUREMENT_REVISION = 1
"""Bump when face preprocessing or clustering semantics change so cached
M3b face tracks are measured again."""

FACE_SPAN_RADIUS_S = 0.1
"""A face observation is a point sample, not a tracked interval - the
span recorded is the sampled instant +/- this radius, matching the
report's own example span width (60.0-60.2). Never claims presence
between samples that was not measured."""

STATUS_MEASURED = "measured"
STATUS_NO_VIDEO = "no-video-stream"
STATUS_NO_FACES = "no-faces-detected"
STATUS_INSIGHTFACE_UNAVAILABLE = "face-identity-unavailable"


class FaceIdentityUnavailable(RuntimeError):
    """insightface/buffalo_l could not be reached or loaded."""


@dataclass
class FaceObservation:
    timestamp: float
    bbox: Tuple[float, float, float, float]
    det_score: float
    embedding: Tuple[float, ...]


# ── face measurement ───────────────────────────────────────────────


def sample_timestamps(duration_seconds: float,
                      interval_s: float = FRAME_SAMPLE_INTERVAL_S,
                      max_samples: int = MAX_FRAME_SAMPLES) -> List[float]:
    """Evenly spaced timestamps across a source, margin-trimmed.

    Pure: a duration in, a plan out, so a test can drive it without
    ffmpeg. Empty for a source too short to carry a safe margin.
    """
    if not duration_seconds or duration_seconds <= 1.0:
        return []
    margin = min(1.0, duration_seconds * 0.02)
    start, end = margin, duration_seconds - margin
    if end <= start:
        return [round(duration_seconds / 2.0, 3)]
    count = min(max_samples, max(1, int((end - start) // interval_s) + 1))
    if count == 1:
        return [round((start + end) / 2.0, 3)]
    step = (end - start) / (count - 1)
    return [round(start + i * step, 3) for i in range(count)]


_FACE_APP = None


def _face_app():
    """The insightface `FaceAnalysis` app, loaded once per process.

    Detection and recognition only: ArcFace aligns on the detector's own
    five keypoints, so the pack's 2D/3D landmark and sex/age models feed
    nothing this module reads - they were 22 of 96 ms per frame (measured
    over the 427 M2 frames of the geo-podcast sources). Embeddings are
    byte-identical with or without them.

    Not replaced by M3's Apple Vision faces: aligning ArcFace on
    Vision-landmark keypoints instead of SCRFD's raised FRR at
    `FACE_MATCH_THRESHOLD` from 0 to 0.074 on the 83-face study (profile
    frames misplace the keypoints by more than an inter-ocular distance).
    """
    global _FACE_APP
    if _FACE_APP is not None:
        return _FACE_APP
    from ren.edition import EditionError, select_component

    try:
        provider = select_component(
            "face identity",
            personal_component="model.insightface_buffalo_l",
            public_component=None)
    except EditionError as exc:
        raise FaceIdentityUnavailable(str(exc)) from exc
    if provider != "model.insightface_buffalo_l":
        raise FaceIdentityUnavailable(
            f"face identity provider {provider!r} has no loader registered")
    model_dir = shared_environment.require_insightface()
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name=shared_environment.INSIGHTFACE_PACK_NAME,
                       root=str(model_dir),
                       allowed_modules=["detection", "recognition"],
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    _FACE_APP = app
    return app


FRAME_SOURCE_M2_TIMES = "M2-times-source-decode"
"""Frames are decoded from the source at the shared M2 I-frame times -
the same instants M3 measured, so `event_spans` can join the two. The
decoded frame is scaled to `FACE_INPUT_MAX_WIDTH` before it is written;
the 384x216 M2 thumbnail itself is never used for ArcFace."""

FRAME_DECODE_WORKERS = 10
"""Parallel `ffmpeg -ss` seeks in `frames_at`. Measured 2026-10-02 on
LCATL0013.MXF across 24 production timestamps: 436 ms/frame at 1 worker,
246 at 2, 162 at 4, 130 at 8 and 138 at 10. A same-timestamp repeat
measured medians of 142 ms at 8 and 139.5 at 10; a 12-worker sweep was
146 ms. Ten is marginally fastest here and matches this machine's CPU
count. Capped by CPU count on smaller machines."""

FRAME_SOURCE_M2 = "M2-shared-sample"
"""Records written before `FRAME_SOURCE_M2_TIMES`: measured ON the 384 px
M2 thumbnails. Read for the join, never written."""

FRAME_SOURCE_OWN_DECODE = "own-decode"


def extract_frame(source_file: str, timestamp: float, out_path: str) -> bool:
    """One scaled JPEG frame at `timestamp`, fast-seek. True on success.

    FFmpeg still decodes the source frame at native resolution. Scaling
    the output reduces the JPEG and model input without changing seek or
    long-GOP decode time.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", f"{timestamp:.3f}",
             "-i", source_file, "-vf",
             f"scale='min({FACE_INPUT_MAX_WIDTH},iw)':-2",
             "-frames:v", "1", "-q:v", "2", out_path,
             "-loglevel", "error"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=120, check=False)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    return out.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def _nearest_m2_times(m2_frames: List[dict], timestamps: List[float]) -> List[float]:
    """The M2 I-frame time nearest each wanted timestamp, deduplicated.

    M2 samples at its own I-frame cadence (~2 Hz), denser than this
    module needs; this picks the closest I-frame to each planned
    timestamp rather than running insightface over every I-frame M2
    kept. Two planned timestamps landing on the same nearest frame
    contribute it once.
    """
    if not m2_frames:
        return []
    frame_times = [f["t"] for f in m2_frames]
    chosen = {min(range(len(frame_times)), key=lambda i: abs(frame_times[i] - ts))
              for ts in timestamps}
    return [frame_times[idx] for idx in sorted(chosen)]


def _resolved_frame_times(source_file: str, digest: str,
                          timestamps: List[float], root: Optional[Path]
                          ) -> Tuple[List[float], str]:
    """Resolve planned times to fresh M2 timestamps, or keep the plan."""
    m2 = source_memory.read_m2(digest, root)
    if (timestamps and m2 is not None and source_memory.is_fresh(m2, source_file)
            and m2.get("frames")):
        return _nearest_m2_times(m2["frames"], timestamps), FRAME_SOURCE_M2_TIMES
    return timestamps, FRAME_SOURCE_OWN_DECODE


def _voice_cache_key(program_pcm_sha256: Optional[str],
                     declared_speaker_count: Optional[int],
                     device: Optional[str]) -> Optional[str]:
    """Stable key for the voice tracks saved in one M3b record, or None
    when nothing may be cached.

    Keyed on the exact PCM the diarizer hears (not the container digest:
    the program-track decision can change under one digest), the speaker
    count, the source of the reduction below, and
    `single_track_diarization.measurement_identity` - its code, every
    parameter, the model revision and checkpoint bytes, the device and
    the measuring libraries' versions. Any of them changing re-measures.
    """
    if program_pcm_sha256 is None or device is None:
        return None
    payload = {
        "program_pcm_sha256": program_pcm_sha256,
        "declared_speaker_count": declared_speaker_count,
        "reduction_sha256": hashlib.sha256(
            inspect.getsource(measure_voice_tracks).encode("utf-8")
        ).hexdigest(),
        "diarization": diarization.measurement_identity(device),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cached_voice_record(identity: Optional[dict],
                         cache_key: Optional[str]) -> bool:
    """Whether a prior voice measurement exactly fits this one. A
    refusal is never reused: a missing model or library is fixed by an
    install, and serving the old reason would hide that fix."""
    if cache_key is None or not identity:
        return False
    instrument = identity.get("instrument") or {}
    return (instrument.get("voice_cache_key") == cache_key
            and instrument.get("voice_unavailable_reason") is None
            and isinstance(identity.get("voices"), list))


def _face_cache_key(digest: str, frame_source: str,
                    timestamps: List[float], interval_s: float,
                    max_samples: int) -> str:
    """Stable key for the face tracks saved in one M3b record."""
    payload = {
        "revision": FACE_MEASUREMENT_REVISION,
        "content_digest": digest,
        "frame_source": frame_source,
        "timestamps": [round(ts, 3) for ts in sorted(timestamps)],
        "sample_interval_s": interval_s,
        "max_samples": max_samples,
        "input_max_width": FACE_INPUT_MAX_WIDTH,
        "model_pack": shared_environment.INSIGHTFACE_PACK_NAME,
        "threshold": FACE_MATCH_THRESHOLD,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cached_face_record(identity: Optional[dict], cache_key: str,
                        expected_sample_count: int) -> bool:
    """Whether a prior successful face measurement exactly fits this plan."""
    if not identity or identity.get("status") not in (STATUS_MEASURED,
                                                       STATUS_NO_FACES):
        return False
    instrument = identity.get("instrument") or {}
    pixels = instrument.get("frame_pixels")
    frame_size_matches = (
        expected_sample_count == 0
        or (isinstance(pixels, list) and len(pixels) == 2
            and isinstance(pixels[0], (int, float))
            and isinstance(pixels[1], (int, float))
            and 0 < pixels[0] <= FACE_INPUT_MAX_WIDTH and pixels[1] > 0)
    )
    return (
        identity.get("face_match_threshold") == FACE_MATCH_THRESHOLD
        and instrument.get("face_cache_key") == cache_key
        and instrument.get("sample_count") == expected_sample_count
        and instrument.get("face_input_max_width") == FACE_INPUT_MAX_WIDTH
        and instrument.get("face_model_pack") == shared_environment.INSIGHTFACE_PACK_NAME
        and frame_size_matches
    )


def frames_at(source_file: str, digest: str, timestamps: List[float],
              root: Optional[Path], scratch_dir: str
              ) -> Tuple[List[Tuple[float, str, bool]], str]:
    """Source-resolution frames at `timestamps`, and which times they are.

    The ONE path a face reaches ArcFace by, shared with
    `face_identity_study` so the study measures what production decodes.
    With a fresh M2 sample (`source_memory.read_m2`) each timestamp
    snaps to its nearest M2 I-frame, so M3 and M3b describe the same
    instants; otherwise the planned times are decoded as given.

    The seeks run in parallel (`FRAME_DECODE_WORKERS`); the returned
    list is sorted by timestamp regardless of completion order, and a
    frame that fails to decode is omitted, exactly as the sequential
    loop did. Returns `[(timestamp, path, owned)]`; every path is
    extracted into `scratch_dir` and is the caller's to delete.
    """
    timestamps, frame_source = _resolved_frame_times(
        source_file, digest, timestamps, root)

    def decode_one(ts: float) -> Optional[Tuple[float, str, bool]]:
        frame_path = os.path.join(scratch_dir, f"f_{ts:.3f}.jpg")
        if extract_frame(source_file, ts, frame_path):
            return (ts, frame_path, True)
        return None

    workers = min(FRAME_DECODE_WORKERS, os.cpu_count() or FRAME_DECODE_WORKERS,
                  len(timestamps))
    if workers <= 1:
        frames = [row for row in (decode_one(ts) for ts in timestamps)
                  if row is not None]
    else:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="face-decode") as pool:
            frames = [row for row in pool.map(decode_one, timestamps)
                      if row is not None]
    frames.sort(key=lambda row: row[0])
    return frames, frame_source


def resolve_sample_frames(source_file: str, digest: str, duration_seconds: float,
                          root: Optional[Path], scratch_dir: str,
                          interval_s: float = FRAME_SAMPLE_INTERVAL_S,
                          max_samples: int = MAX_FRAME_SAMPLES
                          ) -> Tuple[List[Tuple[float, str, bool]], str]:
    """Frames to run face detection over, and which times they are.

    The planned `sample_timestamps`, decoded by `frames_at`.
    """
    timestamps = sample_timestamps(duration_seconds, interval_s, max_samples)
    return frames_at(source_file, digest, timestamps, root, scratch_dir)


def measure_face_observations(frames: List[Tuple[float, str, bool]]
                              ) -> Tuple[List[FaceObservation], Optional[List[int]]]:
    """Every face insightface finds across the resolved frames, and the
    `[width, height]` its boxes are drawn on (None when no frame read).

    A frame insightface finds no face in contributes nothing - absence,
    never a synthesized observation. `frames` is `[(timestamp, path,
    owned)]` from `frames_at`; an owned (scratch) file is removed after
    reading.
    """
    import cv2

    app = _face_app()
    observations: List[FaceObservation] = []
    frame_pixels = None
    for ts, path, owned in frames:
        image = cv2.imread(path)
        if owned:
            os.remove(path)
        if image is None:
            continue
        frame_pixels = frame_pixels or [int(image.shape[1]), int(image.shape[0])]
        for face in app.get(image):
            observations.append(FaceObservation(
                timestamp=ts,
                bbox=tuple(round(float(v), 1) for v in face.bbox),
                det_score=round(float(face.det_score), 4),
                embedding=tuple(round(float(v), 6)
                                for v in face.normed_embedding)))
    return observations, frame_pixels


def _cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a), np.asarray(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


AMBIGUITY_MARGIN = 0.05
"""How far a face match must beat the runner-up candidate to be taken.

Two candidates closer together than this are the same measurement to the
face study (`face_identity_study.THRESHOLD_GRID` steps by 0.02-0.05), so
a match that does not clear the runner-up by this margin is NOT decidable
by what was measured. Such a track starts its own group and is recorded as
uncertain - never merged into the nearest candidate, because a wrong merge
corrupts both people's history while an unresolved track stays visible and
recoverable."""


def cluster_face_tracks(embeddings: List[list],
                        threshold: float = FACE_MATCH_THRESHOLD,
                        ambiguity_margin: float = AMBIGUITY_MARGIN) -> dict:
    """Group face embeddings into people, refusing an undecidable match.

    **Complete linkage.** A track joins a group only when its similarity to
    EVERY member of that group clears `threshold`. The running centroid this
    replaced drifts as members join it, so a face whose appearance changes
    falls out of its own group (one person, two ids) and a second person can
    be pulled into it (two people, one id). Complete linkage cannot do
    either: a group is only as strong as its weakest pair.

    **The margin.** The group a track joins must beat the runner-up group by
    `ambiguity_margin`. When it does not, the placement is not decidable: the
    track starts its own group and is reported in `uncertain` with both
    candidate similarities, so a reader sees the doubt instead of a merge.

    Returns `{"groups": [[index, ...], ...], "uncertain": [row, ...]}`.
    `uncertain` rows carry `index`, `reason`, `best` and `runner_up`.
    """
    count = len(embeddings)
    if count == 0:
        return {"groups": [], "uncertain": []}

    sims = [[1.0] * count for _ in range(count)]
    for i in range(count):
        for j in range(i + 1, count):
            value = _cosine(embeddings[i], embeddings[j])
            sims[i][j] = sims[j][i] = value

    groups: List[List[int]] = []
    uncertain: List[dict] = []
    for index in range(count):
        scored = []
        for position, group in enumerate(groups):
            scored.append((min(sims[index][member] for member in group),
                           position))
        scored.sort(key=lambda row: (-row[0], row[1]))
        best_value, best_position = scored[0] if scored else (-2.0, -1)
        runner_up = scored[1][0] if len(scored) > 1 else None
        if (best_position >= 0 and best_value >= threshold
                and (runner_up is None
                     or best_value - runner_up >= ambiguity_margin)):
            groups[best_position].append(index)
            continue
        if best_position >= 0 and best_value >= threshold:
            # It WOULD have merged, and the runner-up is too close to
            # tell the candidates apart. It stays on its own.
            uncertain.append({
                "index": index,
                "reason": "ambiguous-between-persons",
                "best": round(best_value, 6),
                "runner_up": round(runner_up, 6),
            })
        groups.append([index])
    return {"groups": groups, "uncertain": uncertain}


def cluster_face_observations(observations: List[FaceObservation],
                              threshold: float = FACE_MATCH_THRESHOLD
                              ) -> List[dict]:
    """Group observations into within-source face tracks.

    Incremental: each observation joins the track whose running centroid
    it is closest to, when that similarity clears `threshold`; otherwise
    it starts a new track. Order is timestamp, so the result is
    deterministic. This is the SAME threshold the eval measured FAR=0/
    FRR=0 at - cross-frame grouping within one source is exactly what
    the study tested (same-person pairs are frames of one person across
    the source's own camera and lighting range).
    """
    import numpy as np

    tracks: List[dict] = []
    for obs in sorted(observations, key=lambda o: o.timestamp):
        best_idx, best_sim = None, -2.0
        for idx, track in enumerate(tracks):
            sim = _cosine(obs.embedding, track["_centroid"])
            if sim > best_sim:
                best_idx, best_sim = idx, sim
        if best_idx is not None and best_sim >= threshold:
            track_idx = best_idx
            track = tracks[track_idx]
            track["_members"].append(obs.embedding)
            mean = np.mean(np.asarray(track["_members"]), axis=0)
            track["_centroid"] = tuple(
                round(float(v), 6) for v in mean / np.linalg.norm(mean))
        else:
            tracks.append({"_members": [obs.embedding],
                           "_centroid": obs.embedding, "spans": []})
            track_idx = len(tracks) - 1
        tracks[track_idx]["spans"].append({
            "start": round(obs.timestamp - FACE_SPAN_RADIUS_S, 3),
            "end": round(obs.timestamp + FACE_SPAN_RADIUS_S, 3),
            "box": list(obs.bbox), "det_score": obs.det_score})
    out = []
    for i, track in enumerate(tracks):
        out.append({"track_id": f"face_{i + 1:03d}",
                   "embedding": list(track["_centroid"]),
                   "spans": track["spans"]})
    return out


# ── voice measurement (reuses the landed diarization, PR #1482) ──────


def measure_voice_tracks(wav_path: str,
                         num_speakers: Optional[int] = None,
                         device: Optional[str] = None
                         ) -> Tuple[List[dict], Optional[str]]:
    """Per-cluster voice tracks for one source's program-track audio,
    and why there are none when there are none.

    Reuses `single_track_diarization.diarize_track` outright - the current
    offline Sortformer turns and ECAPA identity embeddings (or ECAPA fallback)
    are not re-measured here, only consumed.
    Returns `([], reason)` when the ECAPA weights are unreachable or the
    VAD gate keeps nothing to cluster (silence). The reason is recorded
    as `instrument.voice_unavailable_reason`: every geo-podcast source
    once built with `voices: []` and nothing saying speechbrain was
    missing, which reads exactly like "nobody spoke". `device` None
    lets the diarizer choose (`single_track_diarization.embedding_device`).
    """
    try:
        result = diarization.diarize_track(
            wav_path, num_speakers=num_speakers, device=device)
    except diarization.DiarizationUnavailable as refused:
        return [], str(refused)
    # ECAPA fallback carries a silhouette when it estimated speaker count;
    # Sortformer has fixed slots and does not claim a silhouette score.
    k_scores = result.k_scores or {}
    silhouette = k_scores.get(result.speakers_estimated)
    try:
        silhouette = (round(float(silhouette), 4)
                      if silhouette is not None else None)
    except (TypeError, ValueError):
        silhouette = None
    out = []
    for i, cluster in enumerate(result.clusters):
        out.append({"track_id": f"voice_{i + 1:03d}",
                    "embedding": list(cluster.centroid),
                    "spans": [list(turn) for turn in cluster.turns],
                    "silhouette": silhouette,
                    "model_provenance": result.provenance or []})
    return out, None


# ── cross-source voice matching (measured: voice_identity_study) ──────


def measure_cross_source_voice_separation(labelled_pairs: list) -> dict:
    """Same-person vs different-person ECAPA distance distributions.

    `labelled_pairs` is a list of `(embedding_a, embedding_b,
    same_person)` tuples, where `same_person` is True when both
    embeddings are the same person's voice measured across different
    sources. Returns the distributions and whether a threshold separates
    them at the target precision >= 0.90 and recall >= 0.80.

    Voice matching needs high precision: a false accept merges two
    people's speech into one track; a false reject leaves two tracks
    for one person, visible and recoverable. The asymmetry matches the
    face-identity study's bias toward FAR over FRR.
    """
    same_dists = []
    diff_dists = []
    for emb_a, emb_b, same_person in labelled_pairs:
        sim = _cosine(emb_a, emb_b)
        if same_person:
            same_dists.append(sim)
        else:
            diff_dists.append(sim)

    result = {
        "same_person_distances": same_dists,
        "different_person_distances": diff_dists,
        "separates": False,
        "threshold": None,
        "precision_at_threshold": None,
        "recall_at_threshold": None,
    }

    if not same_dists or not diff_dists:
        result["reason"] = "need both same-person and different-person pairs"
        return result

    import numpy as np
    same_arr = np.asarray(same_dists)
    diff_arr = np.asarray(diff_dists)

    candidates = np.unique(np.concatenate([same_arr, diff_arr]))
    best = None
    for t in candidates:
        tp = int(np.sum(same_arr >= t))
        fp = int(np.sum(diff_arr >= t))
        fn = int(np.sum(same_arr < t))
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision >= 0.90 and (best is None or recall > best[1]):
            best = (float(t), recall, precision)

    if best is not None:
        result["threshold"] = best[0]
        result["recall_at_threshold"] = best[1]
        result["precision_at_threshold"] = best[2]
        if best[1] >= 0.80:
            result["separates"] = True
        else:
            result["reason"] = (
                f"best recall {best[1]:.3f} below 0.80 target")
    else:
        result["reason"] = "no threshold achieves precision >= 0.90"
    return result


def match_voices_across_sources(voice_tracks_by_source: dict,
                                threshold: float) -> list:
    """Match voice tracks across sources by ECAPA cosine similarity.

    `voice_tracks_by_source` maps a source identifier to its voice
    tracks (each with `track_id`, `embedding`, `spans`). Returns
    cross-source matches: pairs of voice tracks from different sources
    whose embedding cosine >= `threshold`. Never matches two tracks
    from the same source - within-source diarization already did that.

    The threshold is measured, not chosen - see
    `measure_cross_source_voice_separation` and
    `docs/CROSS_SOURCE_VOICE_MEASUREMENT.md`.
    """
    matches = []
    source_ids = sorted(voice_tracks_by_source.keys())
    for i, src_a in enumerate(source_ids):
        for src_b in source_ids[i + 1:]:
            for voice_a in voice_tracks_by_source[src_a]:
                for voice_b in voice_tracks_by_source[src_b]:
                    sim = _cosine(voice_a["embedding"], voice_b["embedding"])
                    if sim >= threshold:
                        matches.append({
                            "source_a": src_a,
                            "voice_a": voice_a["track_id"],
                            "source_b": src_b,
                            "voice_b": voice_b["track_id"],
                            "similarity": round(sim, 6),
                        })
    return matches


def _speech_seconds(voice: dict) -> float:
    """How much speech a voice track carries, in seconds."""
    return sum(end - start for start, end in voice.get("spans") or [])


def voice_components(voices_by_digest: Dict[str, List[dict]],
                     threshold: float) -> List[List[Tuple[str, str]]]:
    """The cross-source voice components: one entry per measured voice.

    A component is a set of voice tracks from DIFFERENT sources whose ECAPA
    cosine clears `threshold`, grouped transitively. A component is one
    speaker's voice as the measurement sees it - across cameras, across
    takes, across a cut. Only tracks carrying at least
    `VOICE_MATCH_MIN_SECONDS` of speech take part: the study's every false
    reject was a diarizer fragment of 4-6 s.
    """
    eligible = {digest: [v for v in voices
                         if _speech_seconds(v) >= VOICE_MATCH_MIN_SECONDS]
                for digest, voices in voices_by_digest.items()}
    matches = match_voices_across_sources(eligible, threshold)
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for match in matches:
        a = (match["source_a"], match["voice_a"])
        b = (match["source_b"], match["voice_b"])
        parent[find(a)] = find(b)
    groups: Dict[Tuple[str, str], list] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)
    return [sorted(members) for members in groups.values()]


def attribute_voice_components(voices_by_digest: Dict[str, List[dict]],
                               attached: Dict[Tuple[str, str], set],
                               threshold: Optional[float] = None) -> dict:
    """Which person each voice track belongs to, by measured voice identity.

    `attached` maps `(digest, track_id)` to the persons a within-source
    speech-face link already gave that voice.

    **A component's owner.** A component (see `voice_components`) is one
    speaker's voice as measured. Its owner is the person holding a MAJORITY
    of the component's speech duration among the members whose
    co-occurrence names exactly one person. A member whose co-occurrence
    names a DIFFERENT person is RE-ATTRIBUTED to the owner and the
    contradiction is recorded: the person on screen is not always the one
    talking, and the measured voice outranks the co-occurrence. A member
    whose co-occurrence names nobody joins the owner.

    **A contested component.** A component with no majority is a doubt: two
    people each claiming a voice the measurement cannot separate. Nothing in
    it is joined and nothing in it is moved; it is returned as a conflict.

    **A contradicted voice.** Two voice tracks of ONE source that the
    co-occurrence gave to the same person, and whose ECAPA similarity is
    below the threshold, cannot both be that person's voice. The one that
    matches that person's other voices keeps the person; the other is left
    UNRESOLVED, with the measurement that refutes it recorded. A voice the
    measurement contradicts is never force-attached.

    Returns `{"owner", "matched", "contradictions", "contested",
    "unresolved"}`. `owner` maps `(digest, track_id)` to the person index.
    """
    if threshold is None:
        threshold = VOICE_MATCH_THRESHOLD
    voices: Dict[Tuple[str, str], dict] = {}
    for digest, rows in voices_by_digest.items():
        for voice in rows:
            voices[(digest, voice["track_id"])] = voice

    owner: Dict[Tuple[str, str], int] = {}
    matched: Dict[Tuple[str, str], list] = {}
    contradictions: List[dict] = []
    contested: List[dict] = []

    components = voice_components(voices_by_digest, threshold)
    for members in components:
        votes: Dict[int, float] = {}
        for member in members:
            holders = attached.get(member) or set()
            if len(holders) != 1:
                continue
            holder = next(iter(holders))
            votes[holder] = votes.get(holder, 0.0) + _speech_seconds(
                voices[member])
        total = sum(votes.values())
        decided = None
        if total > 0:
            for holder, weight in sorted(votes.items()):
                if weight * 2 > total:
                    decided = holder
                    break
        if decided is None:
            contested.append({
                "voice_tracks": [{"content_digest": d, "voice_track": t}
                                 for d, t in members],
                "persons": sorted({owner_index
                                   for member in members
                                   for owner_index in (attached.get(member)
                                                       or set())}),
                "votes": {str(holder): round(weight, 3)
                          for holder, weight in sorted(votes.items())},
                "reason": "no-majority-owner",
            })
            continue
        for member in members:
            owner[member] = decided
            matched[member] = [
                {"content_digest": other[0], "voice_track": other[1],
                 "similarity": round(_cosine(voices[member]["embedding"],
                                             voices[other]["embedding"]), 6)}
                for other in members if other != member]
            for holder in (attached.get(member) or set()):
                if holder == decided:
                    continue
                contradictions.append({
                    "content_digest": member[0],
                    "voice_track": member[1],
                    "from_person": holder,
                    "to_person": decided,
                    "basis": "cross-source voice identity: this voice's "
                             "measured component is owned by another person",
                    "similarity": round(max(
                        (row["similarity"] for row in matched[member]),
                        default=0.0), 6),
                })

    # A voice the measurement CONTRADICTS: one source, two voices, one
    # claimed owner, and the two are not the same voice.
    resolved: Dict[Tuple[str, str], set] = {key: {owner_index}
                                            for key, owner_index
                                            in owner.items()}
    for key in voices:
        if key in resolved:
            continue
        holders = attached.get(key) or set()
        if holders:
            resolved[key] = set(holders)
    unresolved: List[dict] = []
    for digest, rows in voices_by_digest.items():
        for index, voice in enumerate(rows):
            for other in rows[index + 1:]:
                key = (digest, voice["track_id"])
                other_key = (digest, other["track_id"])
                holders = resolved.get(key, set())
                if len(holders) != 1 or resolved.get(other_key) != holders:
                    continue
                holder = next(iter(holders))
                similarity = _cosine(voice["embedding"], other["embedding"])
                if similarity >= threshold:
                    continue
                elsewhere = [
                    candidate for candidate_key, candidate in voices.items()
                    if candidate_key[0] != digest
                    and resolved.get(candidate_key) == holders]
                if not elsewhere:
                    continue
                support = {
                    voice["track_id"]: max(
                        _cosine(voice["embedding"], candidate["embedding"])
                        for candidate in elsewhere),
                    other["track_id"]: max(
                        _cosine(other["embedding"], candidate["embedding"])
                        for candidate in elsewhere),
                }
                if max(support.values()) < threshold:
                    continue
                loser_name = min(support, key=lambda name: support[name])
                loser = (digest, loser_name)
                keeper = other_key if loser_name == voice["track_id"] else key
                resolved.pop(loser, None)
                owner.pop(loser, None)
                matched.pop(loser, None)
                unresolved.append({
                    "content_digest": digest,
                    "voice_track": loser_name,
                    "claimed_person": holder,
                    "basis": "measured voice identity contradicts the "
                             "co-occurrence: this voice does not match "
                             "that person's other voices",
                    "similarity_to_claimed_person": round(
                        support[loser_name], 6),
                    "kept_voice_track": keeper[1],
                })

    return {"owner": owner, "matched": matched,
            "contradictions": contradictions, "contested": contested,
            "unresolved": unresolved, "resolved": resolved}


def join_voices_across_sources(voices_by_digest: Dict[str, List[dict]],
                               attached: Dict[Tuple[str, str], set],
                               threshold: Optional[float] = None,
                               attribution: Optional[dict] = None
                               ) -> Tuple[dict, List[dict]]:
    """Which unlinked voice tracks join which person, by voice alone.

    `attached` maps `(digest, track_id)` to the persons a within-source
    speech-face link already gave that voice. Matches at `threshold`
    (default `VOICE_MATCH_THRESHOLD`) are grouped transitively; a component
    whose linked members all belong to ONE person brings its unlinked
    members to that person. Returns `({(digest, track_id): (person,
    matched)}, conflicts)`.

    A voice match never MERGES two persons. A component whose members were
    given to TWO persons is a conflict and joins nobody. What the measured
    identity may DO is move a voice that the co-occurrence gave to the wrong
    person - see `attribute_voice_components`, which is the whole account;
    this function is the join-only view of it. `attribution` is that
    account, when the caller already has it.
    """
    if threshold is None:
        threshold = VOICE_MATCH_THRESHOLD
    if attribution is None:
        attribution = attribute_voice_components(voices_by_digest, attached,
                                                 threshold)
    joins = {}
    for member, owner_index in attribution["owner"].items():
        if member in attached:
            continue
        joins[member] = (owner_index, attribution["matched"][member])
    return joins, attribution["contested"]


# ── speech-face linking ───────────────────────────────────────────────


def link_speech_to_face(face_tracks: List[dict],
                        voice_tracks: List[dict]) -> List[dict]:
    """Every face span whose midpoint falls inside a voice turn.

    Pure interval arithmetic over two already-measured signals - no new
    embedding comparison, no invented overlap past what the spans
    actually say. A face span with no covering voice turn contributes no
    link; the absence is the honest answer when nobody is speaking (or
    the voice side has no tracks at all - e.g. `voice_tracks == []`).
    """
    links = []
    for face in face_tracks:
        for span in face["spans"]:
            mid = (span["start"] + span["end"]) / 2.0
            for voice in voice_tracks:
                for turn_start, turn_end in voice["spans"]:
                    if turn_start <= mid <= turn_end:
                        links.append({
                            "t": round(mid, 3),
                            "face_track": face["track_id"],
                            "voice_track": voice["track_id"],
                            "basis": "co-occurrence: voice span + "
                                    "face span overlap",
                        })
    return links


# ── building M3b ───────────────────────────────────────────────────────


def build_source_identity(source_file: str,
                          root: Optional[Path] = None,
                          declared_speaker_count: Optional[int] = None,
                          scratch_parent: Optional[str] = None,
                          interval_s: float = FRAME_SAMPLE_INTERVAL_S,
                          max_samples: int = MAX_FRAME_SAMPLES,
                          program_declaration: Optional[int] = None) -> dict:
    """M3b for one source file. Returns the per-file account.

    Heavy on a cache miss: sparse frame extraction + face embedding.
    Voice tracks are rebuilt from the already-decoded program WAV. Callers
    run this under the heavy-work lock; source media is read-only.
    The probe and the program track are the source's primitives
    (`library/tools/source_primitives.py`) - the same track M1
    transcribed, decided under the same declaration - so the voices
    are diarized off the WAV already on disk, not a fresh demux.
    """
    from library.tools import source_primitives

    primitives = source_primitives.ensure(
        source_file, program_declaration, root, scratch_parent)
    digest = primitives["content_digest"]
    target = source_memory.source_dir(digest, root)
    m0 = primitives["m0"]
    video_streams = m0.get("video_streams") or []
    duration = m0.get("duration_seconds") or 0.0

    if not video_streams:
        record = {"content_digest": digest,
                  "source_file": os.path.abspath(source_file),
                  "status": STATUS_NO_VIDEO, "face_match_threshold": FACE_MATCH_THRESHOLD,
                  "faces": [], "voices": [], "speech_face_links": [],
                  "instrument": {}}
        source_memory.write_json(target / source_memory.SLOT_IDENTITY, record)
        source_memory.write_speakers(
            digest, source_file, STATUS_NO_VIDEO, [], instrument={},
            root=root)
        return {"source_file": os.path.abspath(source_file),
               "content_digest": digest, "status": STATUS_NO_VIDEO,
               "faces": 0, "voices": 0, "links": 0}

    planned = sample_timestamps(duration, interval_s, max_samples)
    resolved, frame_source = _resolved_frame_times(
        source_file, digest, planned, root)
    face_cache_key = _face_cache_key(
        digest, frame_source, resolved, interval_s, max_samples)
    previous = read_identity(digest, root)
    face_measurement_reused = _cached_face_record(
        previous, face_cache_key, len(resolved))

    face_unavailable_reason = None
    frame_pixels = None
    sample_count = 0
    if face_measurement_reused:
        previous_instrument = previous["instrument"]
        face_tracks = previous["faces"]
        frame_pixels = previous_instrument["frame_pixels"]
        sample_count = previous_instrument["sample_count"]
    else:
        try:
            shared_environment.require_insightface()
        except shared_environment.InsightfaceEnvironmentMissing as missing:
            face_unavailable_reason = str(missing)

        if face_unavailable_reason is None:
            with tempfile.TemporaryDirectory(prefix="person-entity-",
                                             dir=scratch_parent) as scratch:
                frames, frame_source = resolve_sample_frames(
                    source_file, digest, duration, root, scratch,
                    interval_s, max_samples)
                sample_count = len(frames)
                observations, frame_pixels = measure_face_observations(frames)
                face_tracks = cluster_face_observations(observations)
        else:
            face_tracks = []

    voice_device = voice_cache_key = None
    if primitives["program_wav"] is not None:
        try:
            voice_device = diarization.embedding_device()
        except ImportError:
            pass  # measure_voice_tracks records the refusal below
        voice_cache_key = _voice_cache_key(
            primitives["program_pcm_sha256"], declared_speaker_count,
            voice_device)
    voice_measurement_reused = _cached_voice_record(previous, voice_cache_key)

    voice_unavailable_reason = None
    if voice_measurement_reused:
        voice_tracks = previous["voices"]
    elif primitives["program_wav"] is not None:
        voice_tracks, voice_unavailable_reason = measure_voice_tracks(
            primitives["program_wav"], declared_speaker_count, voice_device)
    else:
        voice_tracks = []
        voice_unavailable_reason = (m0["program_track"].get("basis")
                                    or "no-live-track")

    links = link_speech_to_face(face_tracks, voice_tracks)

    if face_unavailable_reason is not None:
        status = STATUS_INSIGHTFACE_UNAVAILABLE
    elif not face_tracks:
        status = STATUS_NO_FACES
    else:
        status = STATUS_MEASURED

    record = {
        "content_digest": digest,
        "source_file": os.path.abspath(source_file),
        "status": status,
        "face_match_threshold": FACE_MATCH_THRESHOLD,
        "faces": face_tracks,
        "voices": voice_tracks,
        "speech_face_links": links,
        "instrument": {
            "face": ("insightface buffalo_l (ArcFace, 512-d)"
                     if face_unavailable_reason is None else None),
            "face_unavailable_reason": face_unavailable_reason,
            "voice": (
                "offline Sortformer turns + speechbrain ECAPA "
                "(192-d identity embeddings)"
                if voice_tracks and any(
                    row.get("implementation_id") == "sortformer.offline_coreml"
                    for row in voice_tracks[0].get("model_provenance", []))
                else ("speechbrain ECAPA diarization fallback (192-d identity)"
                      if voice_tracks else None)),
            "voice_model_provenance": (
                voice_tracks[0].get("model_provenance", [])
                if voice_tracks else []),
            "voice_unavailable_reason": voice_unavailable_reason,
            "frame_source": frame_source if face_unavailable_reason is None else None,
            "frame_pixels": frame_pixels,
            "sample_count": sample_count,
            "sample_interval_s": interval_s,
            "max_samples": max_samples,
            "face_input_max_width": (FACE_INPUT_MAX_WIDTH
                                     if face_unavailable_reason is None
                                     else None),
            "face_model_pack": (shared_environment.INSIGHTFACE_PACK_NAME
                                if face_unavailable_reason is None else None),
            "face_cache_key": (face_cache_key
                               if face_unavailable_reason is None else None),
            "voice_device": (voice_device
                             if voice_unavailable_reason is None else None),
            "voice_cache_key": (voice_cache_key
                                if voice_unavailable_reason is None else None),
        },
    }
    source_memory.write_json(target / source_memory.SLOT_IDENTITY, record)
    source_memory.write_speakers(
        digest, source_file, status, voice_tracks,
        instrument={key: record["instrument"].get(key)
                    for key in ("voice", "voice_unavailable_reason",
                                "voice_device", "voice_cache_key")},
        root=root)
    return {"source_file": os.path.abspath(source_file),
           "content_digest": digest, "status": status,
           "faces": len(face_tracks), "voices": len(voice_tracks),
           "links": len(links),
           "face_measurement_reused": face_measurement_reused,
           "voice_measurement_reused": voice_measurement_reused}


@source_memory._project_scoped
def build_project_identity(project_folder: str,
                           clip_ids: Optional[List[str]] = None,
                           root: Optional[Path] = None) -> dict:
    """M3b for every unique source digest in a project's catalog.

    Two clips sharing a digest (the same camera file cut into more than
    one project) are measured once; the second is reported `reused`.
    Clips whose media is offline are reported, not failed.
    """
    declared = footage_identity.declared_speakers(project_folder)
    speaker_count = len(declared) if declared else None
    try:
        program_declaration = footage_identity.declared_program_stream(
            project_folder)
    except Exception:
        program_declaration = None
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    seen_digests: set = set()
    results, failed = [], []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "media-offline"})
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest in seen_digests:
            results.append({"clip_id": clip_id, "source_file": path,
                            "content_digest": digest, "reused": True})
            continue
        try:
            account = build_source_identity(
                path, root, declared_speaker_count=speaker_count,
                program_declaration=program_declaration)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        seen_digests.add(account["content_digest"])
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
           "clips": results, "failed": failed}


# ── reading: per-source and cross-project person resolution ──────────


def read_identity(content_digest: str,
                  root: Optional[Path] = None) -> Optional[dict]:
    """A digest's M3b record, or None when never built."""
    path = source_memory.source_dir(content_digest, root) / source_memory.SLOT_IDENTITY
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


@source_memory._project_scoped
def declared_person_names(project_folder: str) -> Dict[str, str]:
    """`{person_id: declared name}` from `source.person_names` in
    project.yaml, or `{}` undeclared. A name is a human label on a
    measured cluster (the report's `"name": "Akshita (declared)"`), never
    a substitute for the embedding match that assigns the person_id.
    """
    raw = footage_identity.source_block(project_folder).get("person_names")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()
           if isinstance(v, str) and v.strip()}


@source_memory._project_scoped
def resolve_person_tracks(project_folder: str,
                          root: Optional[Path] = None) -> dict:
    """The project's person roster: face tracks unified across every
    source digest its catalog references.

    **Faces.** Tracks are grouped by COMPLETE LINKAGE on the measured
    ArcFace cosine (`FACE_MATCH_THRESHOLD`): a track joins a person only
    when it matches every track already in that person, and only when it
    beats the runner-up candidate by `AMBIGUITY_MARGIN`. A placement that
    is not decidable starts its own person and is reported in
    `uncertain_persons` - never a forced merge.

    **Voices.** A voice track belongs to the person its MEASURED voice
    says it is. The cross-source ECAPA components are the measurement
    (`attribute_voice_components`): a component is one speaker's voice
    across cameras and takes, its owner is the person holding a majority
    of its speech duration, and a voice the co-occurrence gave to somebody
    else is re-attributed to that owner. A voice the measurement
    CONTRADICTS is left unresolved (`voice_unresolved`), never attached.

    Every person's spans are clip-scoped for the project doing the asking.
    The roster is PROJECT-LOCAL: it is built from this project's own
    sources, and nothing in it links to another project.
    """
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    names = declared_person_names(project_folder)

    nodes = []  # one per (clip, face_track)
    voices_by_digest: Dict[str, List[dict]] = {}
    clips_by_digest: Dict[str, List[str]] = {}
    unmeasured: List[dict] = []
    for clip in catalog:
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        identity = read_identity(digest, root)
        if not identity:
            unmeasured.append({"clip_id": clip.get("clip_id"),
                               "content_digest": digest,
                               "reason": "no-identity-measurement"})
            continue
        clips_by_digest.setdefault(digest, []).append(clip.get("clip_id"))
        voices_by_digest.setdefault(digest, identity.get("voices", []))
        voice_by_id = {v["track_id"]: v for v in identity.get("voices", [])}
        linked_voice = {}
        for link in identity.get("speech_face_links", []):
            linked_voice.setdefault(link["face_track"], []).append(link)
        for face in identity.get("faces", []):
            nodes.append({
                "clip_id": clip.get("clip_id"),
                "content_digest": digest,
                "face_track": face,
                "links": linked_voice.get(face["track_id"], []),
                "voice_by_id": voice_by_id,
            })

    clustering = cluster_face_tracks(
        [node["face_track"]["embedding"] for node in nodes])
    uncertain_rows = {row["index"]: row for row in clustering["uncertain"]}

    persons: List[dict] = []
    attached: Dict[Tuple[str, str], set] = {}
    person_of_node: Dict[int, int] = {}
    for group in clustering["groups"]:
        index = len(persons)
        for member in group:
            person_of_node[member] = index
        persons.append({"_members": [], "face_spans": [],
                        "face_tracks": [], "voice_spans": [],
                        "speech_face_links": [], "voice_matches": [],
                        "voice_reassignments": [], "uncertain": []})

    for position, node in enumerate(nodes):
        index = person_of_node[position]
        person = persons[index]
        emb = node["face_track"]["embedding"]
        person["_members"].append(emb)
        import numpy as np
        mean = np.mean(np.asarray(person["_members"]), axis=0)
        person["_centroid"] = tuple(
            round(float(v), 6) for v in mean / np.linalg.norm(mean))
        person["face_tracks"].append({
            "clip_id": node["clip_id"],
            "content_digest": node["content_digest"],
            "track_id": node["face_track"]["track_id"]})
        for span in node["face_track"]["spans"]:
            person["face_spans"].append({
                "clip_id": node["clip_id"],
                "content_digest": node["content_digest"],
                **span})
        if position in uncertain_rows:
            person["uncertain"].append(dict(
                uncertain_rows[position],
                clip_id=node["clip_id"],
                content_digest=node["content_digest"],
                track_id=node["face_track"]["track_id"]))
        for link in node["links"]:
            attached.setdefault((node["content_digest"], link["voice_track"]),
                                set()).add(index)
            person["speech_face_links"].append({
                "clip_id": node["clip_id"], **link})

    attribution = attribute_voice_components(voices_by_digest, attached)
    joins, _conflicts = join_voices_across_sources(
        voices_by_digest, attached, attribution=attribution)
    for (digest, track_id), (index, matched) in sorted(joins.items()):
        voice = next((v for v in voices_by_digest.get(digest, [])
                      if v["track_id"] == track_id), None)
        if voice is None:
            continue
        persons[index]["voice_matches"].append({
            "content_digest": digest, "voice_track": track_id,
            "matched": matched,
            "basis": "cross-source voice match: ECAPA cosine >= "
                     "VOICE_MATCH_THRESHOLD to a voice this person's "
                     "face is linked to"})
    for (digest, track_id), holders in sorted(attribution["resolved"].items()):
        voice = next((v for v in voices_by_digest.get(digest, [])
                      if v["track_id"] == track_id), None)
        if voice is None:
            continue
        for index in sorted(holders):
            person = persons[index]
            for clip_id in clips_by_digest.get(digest, []):
                for start, end in voice["spans"]:
                    entry = {"clip_id": clip_id, "content_digest": digest,
                             "start": start, "end": end}
                    if entry not in person["voice_spans"]:
                        person["voice_spans"].append(entry)

    for contradiction in attribution["contradictions"]:
        index = contradiction["to_person"]
        if index < 0 or index >= len(persons):
            continue
        persons[index]["voice_reassignments"].append({
            "content_digest": contradiction["content_digest"],
            "voice_track": contradiction["voice_track"],
            "from_person": contradiction["from_person"],
            "similarity": contradiction["similarity"],
            "basis": contradiction["basis"]})

    def person_id_of(index: int) -> str:
        return f"person_{index + 1:03d}"

    out = []
    for i, person in enumerate(persons):
        person_id = person_id_of(i)
        out.append({
            "person_id": person_id,
            "name": names.get(person_id),
            "face_embedding": list(person["_centroid"]),
            "face_spans": person["face_spans"],
            "face_tracks": person["face_tracks"],
            "voice_spans": person["voice_spans"],
            "speech_face_links": person["speech_face_links"],
            "voice_matches": person["voice_matches"],
            "voice_reassignments": [
                {**row, "from_person": person_id_of(row["from_person"])}
                for row in person["voice_reassignments"]],
            "uncertain": person["uncertain"],
        })
    conflicts = list(attribution["contested"])
    conflicts.extend({
        "kind": "within-source-contradiction",
        "content_digest": row["content_digest"],
        "voice_track": row["voice_track"],
        "persons": [row["claimed_person"]],
        "basis": row["basis"],
    } for row in attribution["unresolved"])
    return {"project_folder": os.path.abspath(project_folder),
           "identity_scope": "project",
           "face_match_threshold": FACE_MATCH_THRESHOLD,
           "voice_match_threshold": VOICE_MATCH_THRESHOLD,
           "ambiguity_margin": AMBIGUITY_MARGIN,
           "uncertain_persons": [
               {"person_id": person_id_of(i),
                "rows": person["uncertain"]}
               for i, person in enumerate(persons) if person["uncertain"]],
           "voice_unresolved": [
               {**row, "claimed_person": person_id_of(row["claimed_person"])}
               for row in attribution["unresolved"]],
           "sources_without_identity_measurement": unmeasured,
           "voice_match_conflicts": [
               {**conflict, "persons": [person_id_of(i)
                                        for i in conflict["persons"]]}
               for conflict in conflicts],
           "persons": out}


@source_memory._project_scoped
def find_person(project_folder: str, query: str,
                root: Optional[Path] = None) -> Optional[dict]:
    """One person from `resolve_person_tracks`, by person_id or declared
    name (case-insensitive). None when the project's roster has no
    match - never a best-effort guess.
    """
    roster = resolve_person_tracks(project_folder, root)
    query_l = query.strip().lower()
    for person in roster["persons"]:
        if person["person_id"].lower() == query_l:
            return person
        if person.get("name") and person["name"].strip().lower() == query_l:
            return person
    return None


# ── CLI ────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="person_entity",
        description="Person entity store (M3b): face ArcFace + voice "
                    "ECAPA identity, clip-local spans, speech-face "
                    "links. Heavy commands run under the heavy-work "
                    "lock.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="M3b for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None,
                         help="one clip id (repeatable; default: all)")

    p_roster = sub.add_parser("roster", help="Resolved person roster "
                              "(face-matched across the project's sources)")
    p_roster.add_argument("project")

    p_find = sub.add_parser("find", help="One person by id or declared name")
    p_find.add_argument("project")
    p_find.add_argument("query")

    args = parser.parse_args(argv)
    project = project_ref.require_project_state(
        project_ref.resolve_project_folder(args.project))
    if args.command == "build":
        result = build_project_identity(project, args.clip)
    elif args.command == "roster":
        result = resolve_person_tracks(project)
    elif args.command == "find":
        result = find_person(project, args.query)
        if result is None:
            print(f"no person matching {args.query!r}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
