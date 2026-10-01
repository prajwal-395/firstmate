"""The person entity store: M3b (`identity.json`) of the per-source
footage memory (`docs/SOURCE_MEMORY.md`).

Scout basis: `data/vep-video-intelligence-entities-and-search/report.md`
section 2 (in firstmate's home). Rigor-gate basis:
`data/vep-person-entity-store/eval/{plan,results}.md` - a false-accept
study on 83 real faces (the captain's own footage, 2 cameras, profile
and eyes-closed frames included) measured FAR=0/FRR=0 at
`FACE_MATCH_THRESHOLD`.

**Identity comes from measured embeddings, never from attributes.**
buffalo_l also reports sex/age; the scout measured those WRONG (Akshita
read as M 34-43) and this module never reads them. A face's identity is
its 512-d ArcFace embedding and nothing else.

**What this module measures, per source file:**

- Face tracks: sampled frames - the shared M2 I-frame sample
  (`source_memory.read_m2`) when a fresh one exists, else this module's
  own `ffmpeg -ss` seeks - each face insightface `buffalo_l` detects and
  embeds, clustered within the one source by cosine >=
  `FACE_MATCH_THRESHOLD`.
- Voice tracks: the source's own program-track audio, diarized with the
  already-landed ECAPA fallback (`single_track_diarization.diarize_track`,
  PR #1482) - reused as-is, not re-measured here.
- Speech-face links: a face span and a voice turn that overlap in time,
  recorded with the basis that produced them. Never invented past what
  overlaps.

**What this module deliberately does NOT do:** match voice embeddings
ACROSS source files to merge a person's identity. Only the face side
carries a measured false-accept rate (see the eval); porting ECAPA's
diarization proof (which measures separating voices WITHIN one file) into
a claim about matching voices ACROSS files would be exactly the
unmeasured leap the rigor gate exists to catch. Cross-source person
identity is **face-only**; a source's voice tracks travel with whichever
person their speech-face link (within that same source) attaches them to,
never on their own cosine distance to another source's voice track.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from library.tools import footage_identity, shared_environment, source_memory
    from library.tools import single_track_diarization as diarization
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity, shared_environment, source_memory
    from tools import single_track_diarization as diarization

FACE_MATCH_THRESHOLD = 0.30
"""Cosine floor for 'same person' on a 512-d ArcFace embedding.

Measured, not chosen: `data/vep-person-entity-store/eval/results.md` -
FAR=0, FRR=0 at this threshold across 83 real faces (2 cameras, profile
and eyes-closed frames included; closest different-person pair 0.124,
weakest same-person pair 0.346 with the threshold clear of both by a
wide margin). Biased toward FAR over FRR: a false merge silently
corrupts a person's whole span history, a missed match just leaves two
person_ids for one person - visible and recoverable in search results.
"""

FRAME_SAMPLE_INTERVAL_S = 10.0
"""How often a source is sampled for faces. `resolve_sample_frames`
reads the shared M2 I-frame sample (`source_memory.read_m2`) when a
fresh one exists for this digest, picking the nearest already-decoded
thumbnail to each planned timestamp; a project with no M2 sample yet
falls back to this module's own sparse `ffmpeg -ss` decode (fast; no
full decode)."""

MAX_FRAME_SAMPLES = 120
"""Upper bound on frames sampled per source, so an hours-long file costs
minutes, not the file's own runtime, at the measured ~0.1 s/frame
(buffalo_l) plus ffmpeg seek overhead."""

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
    """The insightface `FaceAnalysis` app, loaded once per process."""
    global _FACE_APP
    if _FACE_APP is not None:
        return _FACE_APP
    model_dir = shared_environment.require_insightface()
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name=shared_environment.INSIGHTFACE_PACK_NAME,
                       root=str(model_dir),
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    _FACE_APP = app
    return app


FRAME_SOURCE_M2 = "M2-shared-sample"
FRAME_SOURCE_OWN_DECODE = "own-decode"


def extract_frame(source_file: str, timestamp: float, out_path: str) -> bool:
    """One JPEG frame at `timestamp`, fast-seek. True on success."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", f"{timestamp:.3f}",
             "-i", source_file, "-frames:v", "1", "-q:v", "2", out_path,
             "-loglevel", "error"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=120, check=False)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    return out.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def _nearest_m2_frames(m2_frames: List[dict], timestamps: List[float],
                       digest: str, root: Optional[Path]) -> List[Tuple[float, str]]:
    """One M2 thumbnail nearest each wanted timestamp, deduplicated.

    M2 samples at its own I-frame cadence (~2 Hz), denser than this
    module needs; this picks the closest already-decoded frame to each
    planned timestamp rather than running insightface over every I-frame
    M2 kept. Two planned timestamps landing on the same nearest frame
    contribute it once.
    """
    if not m2_frames:
        return []
    frame_times = [f["t"] for f in m2_frames]
    chosen: Dict[int, float] = {}
    for ts in timestamps:
        idx = min(range(len(frame_times)),
                  key=lambda i: abs(frame_times[i] - ts))
        chosen.setdefault(idx, frame_times[idx])
    return [(chosen[idx], source_memory.frame_abspath(digest, m2_frames[idx], root))
           for idx in sorted(chosen)]


def resolve_sample_frames(source_file: str, digest: str, duration_seconds: float,
                          root: Optional[Path], scratch_dir: str,
                          interval_s: float = FRAME_SAMPLE_INTERVAL_S,
                          max_samples: int = MAX_FRAME_SAMPLES
                          ) -> Tuple[List[Tuple[float, str, bool]], str]:
    """Frames to run face detection over, and which source they came from.

    Reads the shared M2 I-frame sample (`source_memory.read_m2`) when a
    fresh one exists - no shared sampler had landed on `origin/main` when
    this module was first written, and M2 lands on main afterward
    (`library/tools/source_memory.py`). Falls back to this module's own
    sparse `ffmpeg -ss` decode otherwise, so a project that has not run
    `source_memory frames` yet still gets an identity measurement.

    Returns `[(timestamp, path, owned)]` - `owned` marks a path this
    function extracted into `scratch_dir` and the caller must delete;
    an M2 thumbnail is never owned (it belongs to the shared memory).
    """
    timestamps = sample_timestamps(duration_seconds, interval_s, max_samples)
    if not timestamps:
        return [], FRAME_SOURCE_OWN_DECODE

    m2 = source_memory.read_m2(digest, root)
    if m2 is not None and source_memory.is_fresh(m2, source_file) and m2.get("frames"):
        picked = _nearest_m2_frames(m2["frames"], timestamps, digest, root)
        if picked:
            return [(t, path, False) for t, path in picked], FRAME_SOURCE_M2

    frames = []
    for ts in timestamps:
        frame_path = os.path.join(scratch_dir, f"f_{ts:.3f}.jpg")
        if extract_frame(source_file, ts, frame_path):
            frames.append((ts, frame_path, True))
    return frames, FRAME_SOURCE_OWN_DECODE


def measure_face_observations(frames: List[Tuple[float, str, bool]]
                              ) -> List[FaceObservation]:
    """Every face insightface finds across the resolved frames.

    A frame insightface finds no face in contributes nothing - absence,
    never a synthesized observation. `frames` is `[(timestamp, path,
    owned)]` from `resolve_sample_frames`; an owned (scratch) file is
    removed after reading, an M2 thumbnail never is.
    """
    import cv2

    app = _face_app()
    observations: List[FaceObservation] = []
    for ts, path, owned in frames:
        image = cv2.imread(path)
        if owned:
            os.remove(path)
        if image is None:
            continue
        for face in app.get(image):
            observations.append(FaceObservation(
                timestamp=ts,
                bbox=tuple(round(float(v), 1) for v in face.bbox),
                det_score=round(float(face.det_score), 4),
                embedding=tuple(round(float(v), 6)
                                for v in face.normed_embedding)))
    return observations


def _cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a), np.asarray(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


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
                         num_speakers: Optional[int] = None
                         ) -> Tuple[List[dict], Optional[str]]:
    """Per-cluster voice tracks for one source's program-track audio,
    and why there are none when there are none.

    Reuses `single_track_diarization.diarize_track` outright - the DER
    proof (0.017, PR #1482) is not re-measured here, only consumed.
    Returns `([], reason)` when the ECAPA weights are unreachable or the
    VAD gate keeps nothing to cluster (silence). The reason is recorded
    as `instrument.voice_unavailable_reason`: every geo-podcast source
    once built with `voices: []` and nothing saying speechbrain was
    missing, which reads exactly like "nobody spoke".
    """
    try:
        result = diarization.diarize_track(wav_path, num_speakers=num_speakers)
    except diarization.DiarizationUnavailable as refused:
        return [], str(refused)
    out = []
    for i, cluster in enumerate(result.clusters):
        out.append({"track_id": f"voice_{i + 1:03d}",
                   "embedding": list(cluster.centroid),
                   "spans": [list(turn) for turn in cluster.turns]})
    return out, None


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

    Heavy: sparse frame extraction + face embedding + voice diarization.
    Callers run this under the heavy-work lock; media is read-only.
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
        return {"source_file": os.path.abspath(source_file),
               "content_digest": digest, "status": STATUS_NO_VIDEO,
               "faces": 0, "voices": 0, "links": 0}

    face_unavailable_reason = None
    try:
        shared_environment.require_insightface()
    except shared_environment.InsightfaceEnvironmentMissing as missing:
        face_unavailable_reason = str(missing)

    frame_source = FRAME_SOURCE_OWN_DECODE
    sample_count = 0
    with tempfile.TemporaryDirectory(prefix="person-entity-",
                                     dir=scratch_parent) as scratch:
        if face_unavailable_reason is None:
            frames, frame_source = resolve_sample_frames(
                source_file, digest, duration, root, scratch,
                interval_s, max_samples)
            sample_count = len(frames)
            observations = measure_face_observations(frames)
            face_tracks = cluster_face_observations(observations)
        else:
            face_tracks = []

        voice_tracks: List[dict] = []
        if primitives["program_wav"] is not None:
            voice_tracks, voice_unavailable_reason = measure_voice_tracks(
                primitives["program_wav"], declared_speaker_count)
        else:
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
            "voice": ("speechbrain ECAPA (192-d) via "
                      "single_track_diarization.diarize_track"
                      if voice_tracks else None),
            "voice_unavailable_reason": voice_unavailable_reason,
            "frame_source": frame_source if face_unavailable_reason is None else None,
            "sample_count": sample_count,
            "sample_interval_s": interval_s,
        },
    }
    source_memory.write_json(target / source_memory.SLOT_IDENTITY, record)
    return {"source_file": os.path.abspath(source_file),
           "content_digest": digest, "status": status,
           "faces": len(face_tracks), "voices": len(voice_tracks),
           "links": len(links)}


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


def resolve_person_tracks(project_folder: str,
                          root: Optional[Path] = None) -> dict:
    """The project's person roster: face tracks unified across every
    source digest its catalog references, by cosine >=
    `FACE_MATCH_THRESHOLD` (face-only - see the module docstring for why
    voice never merges identity across sources). Each person's spans are
    clip-scoped for the project doing the asking; voice spans and
    speech-face links travel along from whichever source produced them.
    """
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    names = declared_person_names(project_folder)

    nodes = []  # one per (clip, face_track)
    for clip in catalog:
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        identity = read_identity(digest, root)
        if not identity:
            continue
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

    persons: List[dict] = []
    for node in nodes:
        emb = node["face_track"]["embedding"]
        best_idx, best_sim = None, -2.0
        for idx, person in enumerate(persons):
            sim = _cosine(emb, person["_centroid"])
            if sim > best_sim:
                best_idx, best_sim = idx, sim
        if best_idx is not None and best_sim >= FACE_MATCH_THRESHOLD:
            person = persons[best_idx]
        else:
            person = {"_centroid": emb, "_members": [], "face_spans": [],
                      "face_tracks": [], "voice_spans": [],
                      "speech_face_links": []}
            persons.append(person)
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
        for link in node["links"]:
            voice = node["voice_by_id"].get(link["voice_track"])
            person["speech_face_links"].append({
                "clip_id": node["clip_id"], **link})
            if voice:
                for start, end in voice["spans"]:
                    entry = {"clip_id": node["clip_id"],
                            "content_digest": node["content_digest"],
                            "start": start, "end": end}
                    if entry not in person["voice_spans"]:
                        person["voice_spans"].append(entry)

    out = []
    for i, person in enumerate(persons):
        person_id = f"person_{i + 1:03d}"
        out.append({
            "person_id": person_id,
            "name": names.get(person_id),
            "face_embedding": list(person["_centroid"]),
            "face_spans": person["face_spans"],
            "face_tracks": person["face_tracks"],
            "voice_spans": person["voice_spans"],
            "speech_face_links": person["speech_face_links"],
        })
    return {"project_folder": os.path.abspath(project_folder),
           "face_match_threshold": FACE_MATCH_THRESHOLD, "persons": out}


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
    import sys

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
    if args.command == "build":
        result = build_project_identity(args.project, args.clip)
    elif args.command == "roster":
        result = resolve_person_tracks(args.project)
    elif args.command == "find":
        result = find_person(args.project, args.query)
        if result is None:
            print(f"no person matching {args.query!r}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
