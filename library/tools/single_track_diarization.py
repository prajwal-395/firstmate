"""Single-track speaker diarization: ECAPA embeddings + VAD + clustering.

The fallback for timelines with ONE audio path. The per-ISO path
(`timeline_transcript` rebuilding one track per timeline speaker) stays
untouched; this module is taken only when there is nothing per-speaker
to rebuild from (routing: `should_diarize_single_track`).

Method (measured, not chosen): open ECAPA speaker embeddings
(`speechbrain/spkrec-ecapa-voxceleb` at the revision pinned in
`shared_environment.ECAPA_REVISION` - public, no HF token) on 1.5 s
windows at 0.5 s hop, an energy VAD gate (frame speech within 20 dB of
the file peak; windows with >= 30% speech frames embed), agglomerative
clustering (cosine, average linkage), speaker count by silhouette over
k = 2..6. Turns are the union of kept windows, merged over
hop-contiguous runs of one label, with the later run yielding where a
run tail overlaps the next run's head (the earlier tail wins the seam -
the measured construction; a hypothesis is single-label per frame, so
an untruncated seam would count twice downstream). Evidence:
`data/vep-single-track-diarization/eval/` in firstmate's home - DER
0.02-0.09 against 0.34-1.14 single-label baselines on the captain's
footage, k estimated correctly on 2- and 3-speaker inputs.

What this module does NOT do, stated rather than tuned around:

- Overlapped speech is attributed to ONE speaker (measured: ties the
  dominant-label default on fully-overlapped frames). Separation needs
  two labels per frame, which this method never claimed.
- Weights live once per machine (`shared_environment.ecapa_model_dir`,
  filled by `scripts/install_ecapa.sh`); absence REFUSES with
  `DiarizationUnavailable` and the caller transcribes single-label with
  the reason recorded - today's behavior said aloud, never a skip.

Owned output shape (for the person-entity task): `diarize_track`
returns per-cluster turns plus every kept window's 192-d ECAPA
embedding; `segment_voice_embeddings` reduces those to one embedding
per transcript segment (mean of member windows, L2-normalized, falling
back to the cluster centroid). `timeline_transcript` carries it on
`SpokenSegment.voice_embedding` - a list of 192 floats on disk, None
where nothing was measured (per-ISO path, single-label path, old
transcripts). Never derived, never defaulted.

Heavy imports (torch, speechbrain, sklearn) are function-local: importing
this module never requires the ML stack, so routing and record-keeping
run anywhere and only the embed path refuses without it.
"""

from __future__ import annotations

import os
import time
import wave
from dataclasses import dataclass

import numpy as np

from library.tools import shared_environment

SAMPLE_RATE = 16000
"""What the embedder hears. The rebuilt timeline track already is this."""

WIN_S = 1.5
"""Embedding window length. Frozen from the eval that measured it."""

HOP_S = 0.5
"""Embedding window hop. Frozen from the eval that measured it."""

FRAME_S = 0.02
"""VAD frame length."""

FRAME_HOP_S = 0.01
"""VAD frame hop."""

VAD_DB_BELOW_PEAK = 20.0
"""Frame speech gate, relative to the file peak. Calibrated once on the
eval's 53 s pair to reproduce the report's stated window count (99/104
kept at the >= 30% gate); frozen for every input since."""

VAD_WINDOW_THRESH = 0.30
"""A window embeds when this fraction of its frames is speech. The
report's winning operating point."""

ESTIMATE_K_MAX = 6
"""Silhouette scans k = 2..6. More speakers than six on one timeline
track is asserted by nobody; a project that has them declares them."""

EMBEDDING_DIM = 192
"""ECAPA embedding width. The person-entity task reads this, not magic."""

DIARIZE_SINGLE_TRACK_DEFAULT = True
"""Whether the single-track path diarizes when eligible and able.

Set from the quiet-machine re-measure (eval results.md): the fallback
cleared 1x realtime with margin, so eligible single-track timelines
diarize by default; `--no-diarize-single-track` opts a run out. Had it
measured under 1x, this would read False and the flag side would flip.
"""

SPEAKER_LABEL_FORMAT = "speaker_{:02d}"
"""Cluster labels, 1-based in first-appearance order. Generic on
purpose: which cluster is which declared person is not measured here,
and inventing a mapping would be guesswork. Downstream keyed lookups (subtitle
styles, lower thirds) simply find no entry, which is the honest answer
for an unnamed voice."""


class DiarizationUnavailable(RuntimeError):
    """No diarization could be measured for this track.

    Raised rather than returned as an empty result: an empty cluster
    list reads as "one voice throughout" downstream, which is exactly
    the single-label behavior this module exists to improve on. The
    caller transcribes single-label and records the reason.
    """


@dataclass
class SpeakerCluster:
    """One estimated voice: its turns and its voice print."""

    label: str
    turns: list[tuple[float, float]]
    """Speech spans in track seconds, disjoint, in order."""

    centroid: tuple[float, ...]
    """Mean of the cluster's window embeddings, L2-normalized."""


@dataclass
class Diarization:
    """Everything measured about one track's voices."""

    clusters: list[SpeakerCluster]
    window_embeddings: np.ndarray
    """Kept windows x 192, L2-normalized, in `window_starts` order."""

    window_starts: np.ndarray
    """Kept window start times in track seconds."""

    window_labels: list[int]
    """Cluster index per kept window."""

    speakers_estimated: int
    inference_seconds: float
    method: str
    weights: str


def _read_wav16_mono(path: str) -> tuple[int, np.ndarray]:
    """16-bit PCM mono samples in [-1, 1]. Multi-channel is averaged -
    the fallback runs on one mixed path, and a second channel here is
    an upmix artifact, not a second microphone."""
    with wave.open(path, "rb") as source:
        if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
            raise DiarizationUnavailable(
                f"{path} is not 16-bit PCM wav; refusing rather than "
                f"resampling a guess into the embedder")
        rate = source.getframerate()
        channels = source.getnchannels()
        raw = source.readframes(source.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE:
        raise DiarizationUnavailable(
            f"{path} is {rate} Hz, not {SAMPLE_RATE} Hz; refusing rather "
            f"than resampling a guess into the embedder")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return rate, audio


def frame_speech_mask(audio: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Per-frame speech flags at `FRAME_HOP_S` grid. Pure: no model.

    A frame is speech when its RMS sits within `VAD_DB_BELOW_PEAK` of
    the file peak. Raw 20 ms energy chatters inside words (measured:
    13 s of miss on the eval pair), so callers gate WINDOWS on these
    frames rather than attributing frames directly.
    """
    frame_len = int(FRAME_S * rate)
    hop = int(FRAME_HOP_S * rate)
    count = 1 + max(0, (len(audio) - frame_len) // hop)
    rms = np.empty(count)
    for index in range(count):
        piece = audio[index * hop:index * hop + frame_len]
        rms[index] = np.sqrt(np.mean(piece ** 2)) if len(piece) else 0.0
    peak = rms.max() if count else 0.0
    if peak <= 0:
        return np.zeros(count, dtype=bool)
    return (20 * np.log10(np.maximum(rms, 1e-10) / peak)
            >= -VAD_DB_BELOW_PEAK)


def window_starts_for(duration_s: float) -> np.ndarray:
    """1.5 s window starts on the 0.5 s hop over a track this long."""
    starts = np.arange(0.0, duration_s - WIN_S + 1e-9, HOP_S)
    if len(starts) == 0:
        starts = np.array([0.0])
    return starts


def kept_windows(speech: np.ndarray,
                 starts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Which windows embed: speech fraction >= `VAD_WINDOW_THRESH`.

    Returns (kept_mask, fractions). Pure numpy, no model - the routing
    half of the VAD, separated from frame measurement so tests can name
    a gating defect without embedding anything.
    """
    mask, fracs = [], []
    for start in starts:
        first = int(np.rint(start / FRAME_HOP_S))
        last = min(int(np.rint((start + WIN_S) / FRAME_HOP_S)), len(speech))
        frac = float(speech[first:last].mean()) if last > first else 0.0
        fracs.append(frac)
        mask.append(frac >= VAD_WINDOW_THRESH)
    return np.array(mask), np.array(fracs)


def merge_runs_to_turns(starts: np.ndarray, labels: list[int]) -> list[tuple]:
    """Hop-contiguous same-label runs as turns, later runs yielding at
    seams. Pure.

    A dropped window splits runs, but both runs' 1.5 s windows still
    cover the seam - and so does every speaker change, where the old
    label's tail overlaps the new label's head by a full second. Without
    truncation the hypothesis double-claims the seam and every overlap
    counts twice downstream (measured on the eval pair before this
    existed). The earlier tail wins the seam: the later turn starts
    where the earlier one ends. A turn truncated empty is dropped.
    """
    if len(starts) == 0:
        return []
    runs = []
    turn_start, current, prev = starts[0], labels[0], starts[0]
    for start, label in zip(starts[1:], labels[1:]):
        if label == current and start - prev <= HOP_S + 1e-6:
            prev = start
            continue
        runs.append((turn_start, prev + WIN_S, current))
        turn_start, current, prev = start, label, start
    runs.append((turn_start, prev + WIN_S, current))
    runs.sort()
    turns = []
    for start, end, label in runs:
        if turns and start < turns[-1][1]:
            start = turns[-1][1]
        if end > start:
            turns.append((start, end, label))
    return turns


def estimate_speaker_count(embeddings: np.ndarray,
                           k_max: int = ESTIMATE_K_MAX) -> tuple[int, dict]:
    """Silhouette k over 2..k_max. k values that collapse to singleton
    clusters are invalid and skipped; when every k is invalid the answer
    is one voice rather than a refusal - a single cluster is still a
    usable diarization, and refusing would fail a run over a k search.
    Returns (k, {k: silhouette}).
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    scores = {}
    best_k, best_score = 1, -1.0
    for candidate in range(2, min(k_max, len(embeddings) - 1) + 1):
        predicted = AgglomerativeClustering(
            n_clusters=candidate, metric="cosine",
            linkage="average").fit_predict(embeddings)
        _, counts = np.unique(predicted, return_counts=True)
        if len(counts) < candidate or counts.min() < 2:
            continue
        score = silhouette_score(embeddings, predicted, metric="cosine")
        scores[candidate] = round(float(score), 4)
        if score > best_score:
            best_score, best_k = score, candidate
    return best_k, scores


def _embedder(model_dir: str | None = None):
    """The ECAPA encoder, or a refusal naming the install that fixes it.

    `model_dir` overrides the machine resolution (tests, eval harnesses).
    speechbrain and sklearn import here, not at module top: routing and
    record-keeping must run on machines without the ML stack.
    """
    try:
        import torch  # noqa: F401
        from speechbrain.inference.speaker import SpeakerRecognition
    except ImportError as exc:
        raise DiarizationUnavailable(
            "speechbrain is not installed, so no voice embedding can be "
            f"measured (requirements.txt declares it): {exc}") from exc
    try:
        resolved = shared_environment.require_ecapa(model_dir)
    except shared_environment.EcapaEnvironmentMissing as exc:
        raise DiarizationUnavailable(str(exc)) from exc
    try:
        encoder = SpeakerRecognition.from_hparams(
            source=os.fspath(resolved), savedir=os.fspath(resolved))
    except Exception as exc:
        raise DiarizationUnavailable(
            f"the ECAPA checkout at {resolved} did not load: {exc}") from exc
    encoder.eval()
    return encoder


def embed_windows(audio: np.ndarray, rate: int, starts: np.ndarray,
                  model_dir: str | None = None,
                  checkpoint_path: str | None = None) -> np.ndarray:
    """L2-normalized ECAPA embeddings for window starts. Deterministic:
    same windows, same weights, same vectors - so a killed run resumes
    from its checkpoint rather than re-embedding."""
    import torch

    encoder = _embedder(model_dir)
    window_len = int(WIN_S * rate)
    done, parts = 0, []
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            previous = np.load(checkpoint_path)
            if (len(previous["starts"]) > 0
                    and np.allclose(previous["starts"],
                                    starts[:len(previous["starts"])])):
                parts.append(previous["embeddings"])
                done = len(previous["starts"])
        except (OSError, ValueError, KeyError):
            done, parts = 0, []
    with torch.no_grad():
        for index in range(done, len(starts), 32):
            batch = []
            for start in starts[index:index + 32]:
                first = int(np.rint(start * rate))
                piece = audio[first:first + window_len]
                if len(piece) < window_len:
                    piece = np.pad(piece, (0, window_len - len(piece)))
                batch.append(piece)
            encoded = encoder.encode_batch(
                torch.tensor(np.stack(batch)), torch.ones(len(batch)))
            parts.append(encoded.squeeze(1).cpu().numpy())
            if checkpoint_path:
                stacked = np.vstack(parts)
                np.savez(checkpoint_path, embeddings=stacked,
                         starts=starts[:len(stacked)])
    embeddings = np.vstack(parts)
    return embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)


def diarize_track(wav_path: str, num_speakers: int | None = None,
                  model_dir: str | None = None,
                  checkpoint_path: str | None = None) -> Diarization:
    """Diarize one mixed track. `num_speakers` given skips the k search
    (a declared roster, or eval replication); None estimates it. Raises
    `DiarizationUnavailable` rather than returning an empty result - an
    empty cluster list would read as "one voice throughout" downstream.
    """
    from sklearn.cluster import AgglomerativeClustering

    _, audio = _read_wav16_mono(wav_path)
    duration_s = len(audio) / SAMPLE_RATE
    started = time.monotonic()
    speech = frame_speech_mask(audio)
    starts = window_starts_for(duration_s)
    keep, _ = kept_windows(speech, starts)
    if keep.sum() == 0:
        raise DiarizationUnavailable(
            f"{wav_path}: the VAD gate kept no window; nothing to cluster")
    embeddings = embed_windows(audio, SAMPLE_RATE, starts[keep],
                               model_dir, checkpoint_path)
    if num_speakers is not None:
        # A declared count cannot ask for more clusters than windows.
        speakers = max(1, min(num_speakers, len(embeddings)))
    else:
        speakers, _ = estimate_speaker_count(embeddings)
    if len(embeddings) < 2:
        # sklearn refuses to cluster one sample; one kept window is one
        # voice by construction, not a clustering question.
        labels = np.zeros(len(embeddings), dtype=int)
    else:
        labels = AgglomerativeClustering(
            n_clusters=speakers, metric="cosine",
            linkage="average").fit_predict(embeddings)
    kept_starts = starts[keep]
    # Turns are merged ONCE over all kept windows, not per cluster: a
    # run splits where the label changes, and the seam truncation inside
    # `merge_runs_to_turns` resolves the union overlap across the
    # ownership boundary (the earlier tail wins the seam). Per-cluster
    # merging would leave every speaker change double-claimed.
    turns = merge_runs_to_turns(kept_starts, [int(v) for v in labels])
    order: dict[int, int] = {}
    for _, _, raw in turns:
        order.setdefault(int(raw), len(order))
    clusters = []
    for raw in sorted(order, key=order.get):
        member = embeddings[labels == raw]
        centroid = member.mean(axis=0)
        centroid = centroid / np.linalg.norm(centroid)
        cluster_turns = [(round(s, 3), round(e, 3))
                         for s, e, lab in turns if int(lab) == int(raw)]
        clusters.append(SpeakerCluster(
            label=SPEAKER_LABEL_FORMAT.format(len(clusters) + 1),
            turns=cluster_turns,
            centroid=tuple(round(float(v), 6) for v in centroid)))
    elapsed = time.monotonic() - started
    weights = os.fspath(shared_environment.ecapa_model_dir(model_dir))
    return Diarization(
        clusters=clusters,
        window_embeddings=embeddings,
        window_starts=kept_starts,
        window_labels=[int(v) for v in labels],
        speakers_estimated=len(clusters),
        inference_seconds=round(elapsed, 1),
        method=("speechbrain/spkrec-ecapa-voxceleb ECAPA 1.5s/0.5s + "
                "energy VAD(peak-20dB,>=30%) + agglomerative-cosine "
                "silhouette-k"),
        weights=weights)


def segment_voice_embeddings(segment_spans: list[tuple[float, float]],
                             diarization: Diarization
                             ) -> list[tuple[float, ...] | None]:
    """One 192-d voice embedding per segment span. Pure numpy.

    The mean of the kept-window embeddings overlapping the span,
    L2-normalized; a span overlapping no window falls back to the
    centroid of the cluster whose turns sit nearest the span (an edge
    effect of the window grid, not a second measurement). None only
    when the diarization carries no clusters at all - never a zero
    vector, which would read as a voice print.
    """
    out = []
    for start, end in segment_spans:
        hits = [index for index, window in enumerate(diarization.window_starts)
                if window < end and window + WIN_S > start]
        if hits:
            mean = diarization.window_embeddings[hits].mean(axis=0)
            norm = np.linalg.norm(mean)
            if norm > 0:
                out.append(tuple(round(float(v), 6)
                                 for v in mean / norm))
                continue
        nearest, nearest_gap = None, None
        for cluster in diarization.clusters:
            for turn_start, turn_end in cluster.turns:
                gap = max(turn_start - end, start - turn_end, 0.0)
                if nearest_gap is None or gap < nearest_gap:
                    nearest, nearest_gap = cluster, gap
        out.append(nearest.centroid if nearest is not None else None)
    return out


def should_diarize_single_track(project_folder: str, speaker_keys: list
                                ) -> tuple[bool, str, int | None]:
    """Whether the single-track fallback owns this transcript, and with
    what k. Pure routing - no audio, no model - so tests can name the
    defect it guards: the fallback taken when per-ISO tracks exist.

    Returns `(eligible, reason, num_speakers)`. Taken only when exactly
    one audio path exists; multiple paths belong to the per-ISO path
    unchanged. `speaker_keys` are the snapshot's distinct speaker
    values, None included: one key of any kind is one path.

    The project's speaker roster (`footage_identity.declared_speakers`)
    names voices, not tracks, so it never makes a second path. It does
    say how many voices there are, and a declared count beats an
    estimated one: two or more names diarize with k = that count; zero
    (music/montage) or one (a monologue) leaves nothing to separate, so
    the single label stands. Undeclared estimates k by silhouette.
    Clusters are never mapped to the declared NAMES - which cluster is
    which person is not measured here, and guessing it would be taste.
    """
    from library.tools import footage_identity

    paths = len(set(speaker_keys))
    if paths != 1:
        return False, (f"{paths} audio paths: the per-ISO path owns "
                       f"multi-track timelines unchanged"), None
    roster = footage_identity.declared_speakers(project_folder)
    if roster is None:
        return True, ("one audio path and no declared speaker roster: "
                      "single-track diarization fallback, k estimated"), None
    if len(roster) < 2:
        return False, (f"one audio path and the project declares "
                       f"{len(roster)} speaker(s): nothing to separate, "
                       f"so the single label stands"), None
    return True, (f"one audio path and the project declares "
                  f"{len(roster)} speakers: single-track diarization "
                  f"fallback, k = {len(roster)} (declared); clusters are "
                  f"not mapped to the declared names"), len(roster)


@dataclass
class DiarizationRecord:
    """The transcript document's account of its own speaker separation."""

    path: str
    reason: str
    method: str = ""
    speakers_estimated: int | None = None
    inference_seconds: float | None = None
    weights: str = ""

    def as_dict(self) -> dict:
        body = {"path": self.path, "reason": self.reason}
        if self.method:
            body["method"] = self.method
        if self.speakers_estimated is not None:
            body["speakers_estimated"] = self.speakers_estimated
        if self.inference_seconds is not None:
            body["inference_seconds"] = self.inference_seconds
        if self.weights:
            body["weights"] = self.weights
        return body
