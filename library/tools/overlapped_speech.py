"""Overlapped speech: the seconds where two voices sound at once (MU-20).

Diarization gives one label per window (`single_track_diarization`
says so: overlapped speech is attributed to ONE speaker), and the
per-ISO path hears each speaker's rebuilt track alone. Neither says
WHEN two people talk over each other, so captions and the reel's speech
boundaries treated crosstalk exactly like one voice. This module
measures it and says what answered.

**The arm: per-camera energy, on the timeline clock.** Every
microphone in a room hears every voice, so "both tracks are loud" is
bleed, not crosstalk. A frame is a track's OWN voice when the track is
voiced and its lead over the other track clears that pair's measured
bleed by `ISO_MARGIN_DB`; crosstalk is two tracks own-voiced inside one
`ISO_WINDOW_S` window. The bleed is read off the audio itself: a pair's
dB lead over frames where either track is voiced is bimodal (one
talker or the other), `_bleed_modes` splits it at the Otsu threshold,
and the low mode is the other talker's bleed onto this mic.

**Which audio.** Not the per-speaker tracks `timeline_transcript`
transcribes: on a cut those hold each camera only while its picture
plays, so two of them are never voiced at once and nothing can be
measured from them (measured on geo-podcast: 0 frames). The cameras
kept recording, though. `timeline_tracks` rebuilds, for each speaker,
THEIR camera under every slot of the timeline - their own clips as
cut, and under another speaker's clip the same instant on their camera,
found through the M6 conversation clock (`_same_instant`)
and read from the source's own program audio in source memory. A slot
with no clock or no program audio is digital silence, and digital
silence is outside the measurement (`RECORDING_FLOOR_DB`), never read
as a quiet voice.

**Thresholds came from the captain's footage**, not from taste:
`docs/benchmarks/2026-10-12-mu20-overlapped-speech.md` measured the
grid on constructed crosstalk over the real synced LC4932/LCATL0013
pair (calibration seed), confirmed on a second seed - precision 0.80,
recall 0.83 - and recorded the single-track ECAPA alternative the plan
also named: best held-out balance P 0.42 / R 0.36, so it is NOT a
producer. A single-track transcript says why it carries no
measurement (`SINGLE_TRACK_REASON`).

A transcript carries the result as `overlapped_speech` (`as_record`);
one never measured has no such key and `spans_of` returns None, so
"not measured" and "measured, none found" never read alike.

Readers: `reel_spine` keeps both blocks of a same-words pair inside
measured crosstalk instead of dropping one as bleed (captions);
`reel_proposal.snap_to_speech` moves a reel boundary out of crosstalk
(the speech sequence); `reel_project_model.overlap_observations` mints
an `Observation(modality="overlap")` per voice on its source clock.
"""

from __future__ import annotations

import json
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from library.tools import single_track_diarization as std

FRAME_S = std.FRAME_S
FRAME_HOP_S = std.FRAME_HOP_S
"""The diarization VAD's own frame grid and gate (`std.VAD_DB_BELOW_PEAK`),
so a voice is "voiced" by one definition across both modules."""

SAMPLE_RATE = std.SAMPLE_RATE

ARM_ISO = "per-camera-energy"

RECORD_KEY = "overlapped_speech"
"""Where a transcript document carries the measurement."""

ISO_MARGIN_DB = 7.5
"""How far a frame's lead must clear the other talker's bleed mode to be
this track's own voice. Measured: the grid's best min(P, R) on the
calibration seed (benchmark doc)."""

ISO_WINDOW_S = 0.5
"""Window over which both tracks must show their own voice. Speech
alternates syllable by syllable, so two voices rarely win the same
10 ms frame; they do share half a second. Measured with the margin."""

ISO_WINDOW_FRACTION = 0.1
"""Share of the window's frames each track must own. Measured."""

MIN_OVERLAP_S = 0.4
"""Shortest crosstalk run reported. Measured with the three above."""

RECORDING_FLOOR_DB = -120.0
"""Below this a frame is digital silence: a camera not rolling or a slot
nothing could fill. Real room noise sits near -60 dBFS."""

SCORE_COLLAR_S = 0.25
"""No-score zone around every reference boundary, the entities report
section 5 scorer's collar."""

SINGLE_TRACK_REASON = (
    "one audio path: the single-track ECAPA two-cluster arm measured "
    "held-out precision 0.42 / recall 0.36 against constructed "
    "crosstalk (docs/benchmarks/2026-10-12-mu20-overlapped-speech.md), "
    "too weak to act on, so nothing is claimed")


class OverlapUnavailable(RuntimeError):
    """No overlap could be measured; the message says why, and the caller
    records it rather than writing an empty span list that would read as
    "measured, none found"."""


@dataclass(frozen=True)
class OverlapSpan:
    start: float
    end: float
    voices: tuple
    """The speakers heard at once, by their track names."""

    def as_dict(self) -> dict:
        return {"start": round(float(self.start), 3),
                "end": round(float(self.end), 3), "voices": list(self.voices)}


@dataclass
class OverlapMeasurement:
    spans: list
    parameters: dict = field(default_factory=dict)
    basis: dict = field(default_factory=dict)
    """What the audio itself contributed: each pair's bleed modes."""

    @property
    def seconds(self) -> float:
        return round(float(sum(s.end - s.start for s in self.spans)), 3)

    def as_record(self) -> dict:
        return {"measured": True, "arm": ARM_ISO,
                "parameters": self.parameters, "basis": self.basis,
                "seconds": self.seconds, "span_count": len(self.spans),
                "spans": [s.as_dict() for s in self.spans]}


def not_measured(reason: str) -> dict:
    """The record for a transcript whose crosstalk could not be measured."""
    return {"measured": False, "reason": reason, "spans": []}


def spans_of(document: Mapping | None) -> list | None:
    """The measured crosstalk spans on a transcript, or None.

    None when nothing was measured - an old transcript, a single-track
    one, a refused measurement. Readers keep their behaviour on None;
    it never means "no crosstalk".
    """
    record = (document or {}).get(RECORD_KEY)
    if not isinstance(record, Mapping) or not record.get("measured"):
        return None
    return [OverlapSpan(float(s["start"]), float(s["end"]),
                        tuple(s["voices"]))
            for s in record["spans"]]


# ── frame measurement ───────────────────────────────────────────────

def _read_track(path) -> np.ndarray:
    """16 kHz mono s16 PCM, as source memory and the rebuilt tracks are."""
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getcomptype() != "NONE" or source.getsampwidth() != 2
                    or source.getnchannels() != 1
                    or source.getframerate() != SAMPLE_RATE):
                raise OverlapUnavailable(
                    f"{Path(path).name} is not 16 kHz mono s16 PCM")
            raw = source.readframes(source.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise OverlapUnavailable(f"{path} is unreadable: {exc}") from exc
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def frame_db(audio: np.ndarray) -> np.ndarray:
    """Frame RMS in dBFS on the `FRAME_HOP_S` grid. Pure."""
    frame_len = round(FRAME_S * SAMPLE_RATE)
    hop = round(FRAME_HOP_S * SAMPLE_RATE)
    if len(audio) < frame_len:
        audio = np.pad(audio, (0, frame_len - len(audio)))
    count = 1 + (len(audio) - frame_len) // hop
    out = np.empty(count)
    # Chunked so an 80-minute track never builds a frames x samples matrix.
    step = 100_000
    for first in range(0, count, step):
        last = min(count, first + step)
        index = (np.arange(frame_len)[None, :]
                 + hop * np.arange(first, last)[:, None])
        out[first:last] = np.sqrt(np.mean(audio[index] ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(out, 1e-10))


def voiced(levels: np.ndarray) -> np.ndarray:
    """The diarization VAD's frame gate, on a track's own levels."""
    if not len(levels):
        return np.zeros(0, dtype=bool)
    return levels >= levels.max() - std.VAD_DB_BELOW_PEAK


def _otsu(values: np.ndarray) -> float:
    """The split maximising between-class variance, on a 0.1 dB grid."""
    ordered = np.sort(values)
    count = len(ordered)
    cumulative = np.cumsum(ordered)
    total = cumulative[-1]
    best, best_split = -1.0, float(ordered[count // 2])
    for split in np.arange(ordered[0], ordered[-1], 0.1):
        below = int(np.searchsorted(ordered, split, side="right"))
        if below in (0, count):
            continue
        low_mean = cumulative[below - 1] / below
        high_mean = (total - cumulative[below - 1]) / (count - below)
        between = below * (count - below) * (high_mean - low_mean) ** 2
        if between > best:
            best, best_split = between, float(split)
    return best_split


def _bleed_modes(lead: np.ndarray) -> tuple[float, float]:
    """(low, high) modes of a pair's dB lead: medians either side of the
    Otsu split. Low is where the OTHER talker speaks alone and this mic
    carries their bleed; high where this mic's talker speaks alone."""
    if len(lead) < 2:
        raise OverlapUnavailable(
            "the two tracks are never voiced while both are recording")
    split = _otsu(lead)
    low, high = lead[lead <= split], lead[lead > split]
    if not len(low) or not len(high):
        raise OverlapUnavailable(
            "the inter-track lead is not bimodal, so bleed cannot be "
            "told from voice")
    return float(np.median(low)), float(np.median(high))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(edges[::2], edges[1::2]))


def iso_overlap(tracks: Mapping[str, object]) -> OverlapMeasurement:
    """Crosstalk across per-speaker microphones on one clock.

    `tracks` maps each speaker to a 16 kHz mono track - a wav path or
    the samples - all starting at the same instant. Raises
    `OverlapUnavailable` with fewer than two tracks, or a pair never
    voiced while both record.
    """
    named = [(str(name), audio) for name, audio in tracks.items()
             if name is not None]
    if len(named) < 2:
        raise OverlapUnavailable(
            f"{len(named)} speaker track(s): crosstalk needs two voices "
            f"on two microphones")
    levels = {}
    for name, audio in named:
        samples = (audio if isinstance(audio, np.ndarray)
                   else _read_track(audio))
        levels[name] = frame_db(samples)
    length = min(len(v) for v in levels.values())
    levels = {name: v[:length] for name, v in levels.items()}
    names = [name for name, _ in named]
    recording = np.all([levels[n] > RECORDING_FLOOR_DB for n in names],
                       axis=0)
    # A 20 ms frame straddling a seam between a filled slot and digital
    # silence reads as signal; the frames either side of any silence are
    # outside the measurement too, so a seam is never "both recording".
    reach = round(FRAME_S / FRAME_HOP_S)
    padded = np.pad(recording, reach, constant_values=False)
    recording = np.all([padded[shift:shift + len(recording)]
                        for shift in range(2 * reach + 1)], axis=0)
    gates = {n: voiced(levels[n]) & recording for n in names}
    own = {n: gates[n].copy() for n in names}
    modes = {}
    for index, x in enumerate(names):
        for y in names[index + 1:]:
            lead = levels[x] - levels[y]
            low, high = _bleed_modes(lead[gates[x] | gates[y]])
            modes[f"{x}|{y}"] = {f"bleed_of_{y}": round(low, 2),
                                 f"own_{x}": round(high, 2)}
            own[x] &= lead > low + ISO_MARGIN_DB
            own[y] &= lead < high - ISO_MARGIN_DB
    width = max(1, round(ISO_WINDOW_S / FRAME_HOP_S))
    kernel = np.ones(width) / width
    shares = np.vstack([np.convolve(own[n], kernel, mode="same")
                        for n in names])
    present = shares >= ISO_WINDOW_FRACTION
    mask = present.sum(axis=0) >= 2
    spans = []
    for first, last in _runs(mask):
        if (last - first) * FRAME_HOP_S < MIN_OVERLAP_S:
            continue
        held = present[:, first:last].mean(axis=1)
        voices = tuple(n for n, share in zip(names, held) if share >= 0.5)
        spans.append(OverlapSpan(round(float(first * FRAME_HOP_S), 3),
                                 round(float(last * FRAME_HOP_S), 3), voices))
    return OverlapMeasurement(
        spans=spans,
        parameters={"margin_db": ISO_MARGIN_DB, "window_s": ISO_WINDOW_S,
                    "window_fraction": ISO_WINDOW_FRACTION,
                    "min_overlap_s": MIN_OVERLAP_S,
                    "vad_db_below_peak": std.VAD_DB_BELOW_PEAK},
        basis={"bleed_modes_db": modes,
               "recording_seconds": round(float(recording.sum())
                                          * FRAME_HOP_S, 2)})


# ── each speaker's camera under the whole timeline ──────────────────

def _program_audio(digest: str, root: Path | None, cache: dict):
    """A source's program audio from source memory, or None."""
    if digest not in cache:
        from library.tools import source_memory
        path = source_memory.source_dir(digest, root) / "program.16k.wav"
        try:
            cache[digest] = _read_track(path) if path.is_file() else None
        except OverlapUnavailable:
            cache[digest] = None
    return cache[digest]


def _same_instant(digest: str, seconds: float, root: Path | None) -> dict:
    """`{other_digest: other_source_time}` across this source's M6 group.

    `conversation_clock.map_time`'s arithmetic, read through
    `source_memory` directly: importing the clock module would pull its
    timecode cross-check (and Resolve's locks) into every preflight
    step's code identity. `test_overlapped_speech` pins the two equal.
    """
    from library.tools import source_memory

    def clock(member):
        path = source_memory.source_dir(member, root) / source_memory.SLOT_CLOCK
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    record = clock(digest)
    if record is None:
        return {}
    reference_time = seconds + record["offset_to_reference_seconds"]
    out = {}
    for member in record.get("group_members") or []:
        other = clock(member) if member != digest else None
        if other is not None:
            out[member] = round(
                reference_time - other["offset_to_reference_seconds"], 3)
    return out


def timeline_tracks(slots: Sequence, duration: float,
                    root: Path | None = None,
                    digest_of=None) -> tuple[dict, dict]:
    """Each speaker's camera, laid under every slot of the timeline.

    `slots` are timeline clips (`speaker`, `source_file`, `source_in`,
    `timeline_start`, `timeline_end`). A speaker's cameras are the
    source files their own slots play. Under a slot of theirs the track
    plays that slot; under anyone else's it plays the same instant on
    one of their cameras, found through the M6 clock; otherwise silence.
    `digest_of(path)` names a file's content digest (default: the live
    fingerprint). Returns `({speaker: samples}, coverage)` where
    coverage counts the seconds each speaker's track could fill.
    """
    from library.tools import footage_identity

    if digest_of is None:
        def digest_of(path):
            return footage_identity.fingerprint(path)["content_digest"]

    digests: dict = {}

    def digest(path):
        if path not in digests:
            try:
                digests[path] = digest_of(path)
            except OSError:
                digests[path] = None
        return digests[path]

    cameras: dict = {}
    for slot in slots:
        if slot.speaker is not None:
            cameras.setdefault(str(slot.speaker), set()).add(slot.source_file)
    audio_cache: dict = {}
    total = round(duration * SAMPLE_RATE)
    tracks = {name: np.zeros(total, dtype=np.float32) for name in cameras}
    covered = {name: 0.0 for name in cameras}
    for slot in slots:
        start = round(slot.timeline_start * SAMPLE_RATE)
        length = min(round(slot.timeline_end * SAMPLE_RATE), total) - start
        if length <= 0:
            continue
        slot_digest = digest(slot.source_file)
        mapped = (_same_instant(slot_digest, slot.source_in, root)
                  if slot_digest else {})
        for name, files in cameras.items():
            source_in = None
            source_digest = None
            if slot.source_file in files:
                source_in, source_digest = slot.source_in, slot_digest
            else:
                for camera in sorted(files):
                    camera_digest = digest(camera)
                    if camera_digest in mapped:
                        source_in, source_digest = (mapped[camera_digest],
                                                    camera_digest)
                        break
            if source_digest is None:
                continue
            samples = _program_audio(source_digest, root, audio_cache)
            if samples is None:
                continue
            first = round(source_in * SAMPLE_RATE)
            piece = samples[max(first, 0):max(first + length, 0)]
            if first < 0 or len(piece) < length:
                # The camera was not rolling for all of the slot: keep
                # silence (outside the measurement) rather than shift.
                continue
            tracks[name][start:start + length] = piece
            covered[name] += length / SAMPLE_RATE
    return tracks, {name: round(s, 2) for name, s in covered.items()}


def timeline_record(slots: Sequence, duration: float,
                    root: Path | None = None, digest_of=None) -> dict:
    """The transcript record for a per-speaker timeline; never raises."""
    tracks, covered = timeline_tracks(slots, duration, root, digest_of)
    try:
        measurement = iso_overlap(tracks)
    except OverlapUnavailable as exc:
        return not_measured(f"{ARM_ISO}: {exc} (camera seconds filled: "
                            f"{covered})")
    record = measurement.as_record()
    record["basis"]["camera_seconds_filled"] = covered
    return record


# ── scoring against a reference ─────────────────────────────────────

def as_turn_rows(spans, *, speakers_by_track=None) -> list[dict]:
    """The measured crosstalk as the turn rows an overlap-aware scorer
    reads.

    The arm measures CROSSTALK BETWEEN TRACKS: two cameras that both
    heard the two talkers at once. A reference speaks in PEOPLE, so the
    track each voice was heard on has to be mapped onto the person who
    spoke it. That map is supplied by the caller, because this arm
    cannot know it: two cameras that both heard the same person are two
    tracks and one person.

    With no map, the rows carry the track names as they are. That
    scores the crosstalk's TIMING against a reference that names people,
    and reads as confusion rather than as the mapping failure it is.

    A track the map does not cover is left OUT rather than carried as a
    person: a voice with no name is not a voice with an invented one.
    """
    rows: list[dict] = []
    for span in spans:
        start = float(getattr(span, "start", None) if not isinstance(span, dict)
                      else span["start"])
        end = float(getattr(span, "end", None) if not isinstance(span, dict)
                    else span["end"])
        voices = (tuple(span["voices"]) if isinstance(span, dict)
                  else tuple(getattr(span, "voices", ())))
        if end <= start:
            continue
        if speakers_by_track is not None:
            mapped: list[str] = []
            for voice in voices:
                person = speakers_by_track.get(voice)
                if person is not None and person not in mapped:
                    mapped.append(person)
            if not mapped:
                continue
            voices = tuple(mapped)
        if not voices:
            continue
        rows.append({"start": round(start, 3), "end": round(end, 3),
                     "speakers": list(voices)})
    rows.sort(key=lambda row: (row["start"], row["end"]))
    return rows


def as_turn_rows_of(document, *, speakers_by_track=None) -> list[dict]:
    """The crosstalk on a transcript, as turn rows an overlap-aware
    scorer reads.

    A transcript that never measured crosstalk produces no rows: that
    is "not measured", and the scorer is told so rather than handed an
    empty estimate that reads as "nobody ever talked over anybody".
    """
    return as_turn_rows(spans_of(document) or [],
                        speakers_by_track=speakers_by_track)


def score(hypothesis: Sequence, reference: Sequence, duration: float,
          collar: float = SCORE_COLLAR_S) -> dict:
    """Time-weighted precision and recall of crosstalk spans. Pure.

    Both are `(start, end)` pairs or `OverlapSpan`s on one clock.
    Frames within `collar` of a reference boundary are not scored (the
    NIST convention the entities report section 5 scorer used).
    """
    hop = FRAME_HOP_S
    frames = int(np.ceil(duration / hop)) + 1

    def mask_of(spans):
        mask = np.zeros(frames, dtype=bool)
        for span in spans:
            start, end = ((span.start, span.end)
                          if isinstance(span, OverlapSpan) else span)
            mask[max(0, round(start / hop)):max(0, round(end / hop))] = True
        return mask

    hyp, ref = mask_of(hypothesis), mask_of(reference)
    scored = np.ones(frames, dtype=bool)
    pad = round(collar / hop)
    for first, last in _runs(ref):
        for edge in (first, last):
            scored[max(0, edge - pad):edge + pad] = False
    tp = float((hyp & ref & scored).sum() * hop)
    fp = float((hyp & ~ref & scored).sum() * hop)
    fn = float((~hyp & ref & scored).sum() * hop)
    return {"precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "true_positive_s": round(tp, 2), "false_alarm_s": round(fp, 2),
            "missed_s": round(fn, 2)}


def main(argv=None) -> int:
    """Measure crosstalk for a project's saved transcript, no Resolve.

    The transcript's own bound rows are the slots (each row is speech
    on a known source span), so this answers for a transcript written
    before MU-20 without re-hearing anything. Prints the record;
    `--write` stores it on the transcript.
    """
    import argparse
    from types import SimpleNamespace

    from library.tools import timeline_transcript

    parser = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    parser.add_argument("project_folder")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    path = timeline_transcript.transcript_path(args.project_folder)
    document = json.loads(path.read_text(encoding="utf-8"))
    slots = [SimpleNamespace(
                speaker=row["speaker"], source_file=row["source_file"],
                source_in=float(row["source_start"]),
                timeline_start=float(row["timeline_start"]),
                timeline_end=float(row["timeline_end"]))
             for row in document["segments"]
             if row["source_file"] and row["source_start"] is not None]
    record = timeline_record(
        slots, float(document["derived_from"]["duration_seconds"]))
    print(json.dumps({k: v for k, v in record.items() if k != "spans"},
                     indent=2))
    if args.write:
        document[RECORD_KEY] = record
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        print(f"wrote {RECORD_KEY} onto {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
