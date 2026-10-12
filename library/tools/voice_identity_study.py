"""The cross-source voice FAR/FRR study that sets `VOICE_MATCH_THRESHOLD`.

`person_entity.VOICE_MATCH_THRESHOLD` is a measured number, the voice
counterpart of `face_identity_study`: labelled voices, every pair that
spans two DIFFERENT sources, FAR and FRR over a threshold grid. Same-source
pairs are excluded on purpose - separating voices within one file is the
diarizer's proof (PR #1482); the open question this study answers is
whether a voice print survives a change of camera, microphone and room.
`docs/CROSS_SOURCE_VOICE_MEASUREMENT.md` is the specification and holds
the measured result.

**Labels come from physical tracks, never from the audio content.** A
manifest labels a source two ways:

- a whole source by person (`"person"`), when one person speaks in it -
  the face study's own ground truth;
- per span, from the multitrack timeline transcript
  (`timeline_transcript`'s `transcript.json`): each segment's speaker is
  the ISO track it was transcribed from. A segment labels its own source
  directly, and labels every source SYNCED to it - the same moment heard
  on the other camera's program track (mic bleed), which is exactly the
  cross-microphone case. The sync is the timeline's own: where the edit
  cuts from one camera to another, the two items' offsets differ by the
  cameras' sync delta (`sync_deltas`). Seconds two speakers share are
  labelled nobody.

    {"timeline_transcript": "/abs/pipeline_output/scratch/timeline_transcript/transcript.json",
     "sources": [{"source_file": "/abs/LC4932.MXF"},
                 {"source_file": "/abs/IMG_1818.MOV", "person": "captain"}]}

**Two arms, both on production's own measurements.**

- TRACK: the voice tracks `person_entity.build_source_identity` stored in
  M3b (`identity.json`) - the centroids `match_voices_across_sources`
  will actually compare. A track is labelled by the person whose spans
  cover its turns (purity >= `TRACK_LABEL_PURITY` over >=
  `TRACK_LABEL_MIN_SECONDS` labelled); otherwise it is excluded and named.
- CHUNK: production's windows (the diarizer's own VAD gate and 1.5 s /
  0.5 s grid, embedded by its own ECAPA) inside one person's labelled
  spans, averaged in runs of N kept windows. A track centroid averages
  many windows; a short chunk is the noisy end of what a track can be,
  so the chunk arm says where a small track stops being trustworthy.

The study reads the memory root and never writes it: a source with no M3b
record is refused with the command that builds one. Heavy (ECAPA over the
labelled windows): run it under the heavy-work lock.

    python3 -m library.tools.voice_identity_study run <manifest.json> [--out report.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from library.tools import footage_identity, person_entity, source_memory
    from library.tools import single_track_diarization as diarization
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity, person_entity, source_memory
    from tools import single_track_diarization as diarization

THRESHOLD_GRID = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70,
                  0.75, 0.80, 0.85, 0.90)

CHUNK_WINDOWS = (4, 10, 40)
"""Kept windows averaged per chunk: 4 (~3 s of speech, the size of the
smallest tracks the diarizer emits), 10 (~6 s) and 40 (~21 s)."""

SYNC_CUT_GAP_S = 3.0
SYNC_AGREE_S = 0.15
SYNC_MIN_VOTES = 2
SYNC_MARGIN_S = 0.25

TRACK_LABEL_PURITY = 0.8
TRACK_LABEL_MIN_SECONDS = 3.0

FAILING_PAIRS_SHOWN = 20

Span = Tuple[float, float, str]


# ── labels ────────────────────────────────────────────────────────────


def _timeline_items(transcript: dict) -> List[dict]:
    """One `{source_file, offset, start, end}` per timeline item, in
    timeline order. An item plays one source at one fixed offset (source
    time = timeline time + offset); its extent is the span between its own
    first and last segment."""
    items: Dict[str, dict] = {}
    for seg in transcript["segments"]:
        item = items.setdefault(seg["resolve_item_id"], {
            "source_file": seg["source_file"],
            "offset": seg["source_start"] - seg["timeline_start"],
            "start": seg["timeline_start"], "end": seg["timeline_end"]})
        item["start"] = min(item["start"], seg["timeline_start"])
        item["end"] = max(item["end"], seg["timeline_end"])
    return sorted(items.values(), key=lambda i: i["start"])


def sync_deltas(items: Sequence[dict]) -> Dict[Tuple[str, str], float]:
    """`{(source_a, source_b): delta}` with source_b time = source_a time
    + delta, read off the timeline's own cuts.

    Two items of different sources that abut (gap < `SYNC_CUT_GAP_S`) are
    a cut between synced cameras, so their offsets differ by the sync
    delta. A pair is synced when at least `SYNC_MIN_VOTES` cuts, and at
    least half of its cuts, agree within `SYNC_AGREE_S`; the delta is
    their median. A pair whose cuts disagree is not synced - a label
    carried across a guessed sync would be a guess.
    """
    import numpy as np

    votes: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for a, b in zip(items, items[1:]):
        if a["source_file"] == b["source_file"]:
            continue
        if b["start"] - a["end"] >= SYNC_CUT_GAP_S:
            continue
        votes[(a["source_file"], b["source_file"])].append(
            b["offset"] - a["offset"])
        votes[(b["source_file"], a["source_file"])].append(
            a["offset"] - b["offset"])
    out = {}
    for pair, deltas in votes.items():
        best: List[float] = []
        for d in deltas:
            agreeing = [x for x in deltas if abs(x - d) <= SYNC_AGREE_S]
            if len(agreeing) > len(best):
                best = agreeing
        if len(best) >= SYNC_MIN_VOTES and 2 * len(best) >= len(deltas):
            out[pair] = round(float(np.median(best)), 3)
    return out


def spans_from_timeline_transcript(transcript: dict) -> Dict[str, List[Span]]:
    """`{source_file: [(start, end, person)]}` in each source's own seconds.

    A segment labels the source it was transcribed from, and every source
    `sync_deltas` finds synced to that one, shifted by the delta and
    trimmed by `SYNC_MARGIN_S` at each end for the sync's own spread.
    Seconds where two speakers' segments overlap on the timeline are cut
    out of both first.
    """
    items = _timeline_items(transcript)
    deltas = sync_deltas(items)
    timeline: List[Span] = [(s["timeline_start"], s["timeline_end"], s["speaker"])
                            for s in transcript["segments"]]
    out: Dict[str, List[Span]] = defaultdict(list)
    for start, end, person in _without_overlap(timeline):
        for item in items:
            if not (item["start"] <= start and end <= item["end"]):
                continue
            own = item["source_file"]
            src_start, src_end = start + item["offset"], end + item["offset"]
            out[own].append((round(src_start, 3), round(src_end, 3), person))
            for (a, b), delta in deltas.items():
                if a != own or src_end - src_start <= 2 * SYNC_MARGIN_S:
                    continue
                out[b].append((round(src_start + delta + SYNC_MARGIN_S, 3),
                               round(src_end + delta - SYNC_MARGIN_S, 3), person))
    return {source: sorted(set(spans)) for source, spans in out.items()}


def _without_overlap(spans: Sequence[Span]) -> List[Span]:
    """Each span less the seconds any other person's span covers."""
    out = []
    for start, end, person in spans:
        pieces = [(start, end)]
        for o_start, o_end, other in spans:
            if other == person or o_end <= start or o_start >= end:
                continue
            nxt = []
            for p_start, p_end in pieces:
                if o_end <= p_start or o_start >= p_end:
                    nxt.append((p_start, p_end))
                    continue
                if p_start < o_start:
                    nxt.append((p_start, o_start))
                if o_end < p_end:
                    nxt.append((o_end, p_end))
            pieces = nxt
        out.extend((s, e, person) for s, e in pieces if e - s > 0)
    return out


def label_seconds(turns: Sequence[Sequence[float]],
                  spans: Sequence[Span]) -> Dict[str, float]:
    """Seconds of `turns` each person's spans cover."""
    covered: Dict[str, float] = defaultdict(float)
    for t_start, t_end in turns:
        for s_start, s_end, person in spans:
            overlap = min(t_end, s_end) - max(t_start, s_start)
            if overlap > 0:
                covered[person] += overlap
    return {person: round(sec, 3) for person, sec in covered.items()}


def label_track(turns: Sequence[Sequence[float]],
                spans: Sequence[Span]) -> Tuple[Optional[str], dict]:
    """The person a voice track is, or None and why."""
    covered = label_seconds(turns, spans)
    total = sum(covered.values())
    if total < TRACK_LABEL_MIN_SECONDS:
        return None, {"labelled_seconds": covered,
                      "excluded": f"under {TRACK_LABEL_MIN_SECONDS}s labelled"}
    person = max(covered, key=covered.get)
    purity = covered[person] / total
    if purity < TRACK_LABEL_PURITY:
        return None, {"labelled_seconds": covered,
                      "excluded": f"purity {purity:.2f} under {TRACK_LABEL_PURITY}"}
    return person, {"labelled_seconds": covered, "purity": round(purity, 3)}


# ── pairs ─────────────────────────────────────────────────────────────


def cross_source_pair_report(items: Sequence[Tuple[str, str, str, Sequence[float]]],
                             grid: Sequence[float] = THRESHOLD_GRID) -> dict:
    """FAR/FRR over every pair of `(key, source, person, embedding)` whose
    sources differ. FAR = different-person pairs >= t over all of them;
    FRR = same-person pairs < t over all of them."""
    import numpy as np

    if len(items) < 2:
        return {"items": len(items), "same_person_pairs": 0,
                "different_person_pairs": 0, "grid": []}
    embeddings = np.asarray([i[3] for i in items], dtype=float)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = embeddings @ embeddings.T
    same: List[Tuple[float, str, str]] = []
    diff: List[Tuple[float, str, str]] = []
    for a, b in itertools.combinations(range(len(items)), 2):
        if items[a][1] == items[b][1]:
            continue
        row = (round(float(sims[a, b]), 4), items[a][0], items[b][0])
        (same if items[a][2] == items[b][2] else diff).append(row)
    same.sort()
    diff.sort(reverse=True)

    def rates(t: float) -> dict:
        false_accepts = sum(1 for s, _a, _b in diff if s >= t)
        false_rejects = sum(1 for s, _a, _b in same if s < t)
        return {"threshold": t,
                "FAR": round(false_accepts / len(diff), 6) if diff else None,
                "FRR": round(false_rejects / len(same), 6) if same else None,
                "false_accepts": false_accepts, "false_rejects": false_rejects}

    return {
        "items": len(items),
        "same_person_pairs": len(same),
        "different_person_pairs": len(diff),
        "weakest_same_person": list(same[0]) if same else None,
        "closest_different_person": list(diff[0]) if diff else None,
        "grid": [rates(t) for t in grid],
        "closest_different_shown": [list(r) for r in diff[:FAILING_PAIRS_SHOWN]],
        "weakest_same_shown": [list(r) for r in same[:FAILING_PAIRS_SHOWN]],
    }


def at_threshold(report: dict, threshold: float) -> dict:
    """The arm's FAR/FRR at one threshold, computed off its extremes."""
    weakest = report.get("weakest_same_person")
    closest = report.get("closest_different_person")
    return {"threshold": threshold,
            "no_false_accept": closest is None or closest[0] < threshold,
            "no_false_reject": weakest is None or weakest[0] >= threshold}


# ── measuring ─────────────────────────────────────────────────────────


def chunk_embeddings(starts: Sequence[float], embeddings, spans: Sequence[Span],
                     size: int) -> List[Tuple[str, float, list]]:
    """`(person, first_window_start, centroid)` per run of `size` kept
    windows lying wholly inside one person's labelled spans."""
    import numpy as np

    by_person: Dict[str, List[int]] = defaultdict(list)
    for index, start in enumerate(starts):
        end = start + diarization.WIN_S
        for s_start, s_end, person in spans:
            if s_start <= start and end <= s_end:
                by_person[person].append(index)
                break
    out = []
    for person, indices in sorted(by_person.items()):
        for first in range(0, len(indices) - size + 1, size):
            member = embeddings[indices[first:first + size]]
            mean = member.mean(axis=0)
            out.append((person, float(starts[indices[first]]),
                        list(mean / np.linalg.norm(mean))))
    return out


def labelled_window_embeddings(wav_path: str, spans: Sequence[Span],
                               device: str):
    """Production's kept windows inside the labelled spans, embedded.

    The diarizer's own VAD (`frame_speech_mask` + `kept_windows`, run on
    the whole track so the peak reference is production's) and its own
    encoder; only windows wholly inside a labelled span are embedded.
    """
    import numpy as np

    _, audio = diarization._read_wav16_mono(wav_path)
    speech = diarization.frame_speech_mask(audio)
    starts = diarization.window_starts_for(len(audio) / diarization.SAMPLE_RATE)
    keep, _ = diarization.kept_windows(speech, starts)
    inside = np.array([any(s <= w and w + diarization.WIN_S <= e
                           for s, e, _p in spans) for w in starts])
    chosen = starts[keep & inside]
    if len(chosen) == 0:
        return chosen, np.zeros((0, diarization.EMBEDDING_DIM))
    return chosen, diarization.embed_windows(
        audio, diarization.SAMPLE_RATE, chosen, device=device)


def measure_source(source_file: str, spans: Sequence[Span],
                   root: Optional[Path] = None,
                   device: Optional[str] = None,
                   chunk_sizes: Sequence[int] = CHUNK_WINDOWS) -> dict:
    """One source's labelled production tracks and labelled chunks."""
    digest = footage_identity.fingerprint(source_file)["content_digest"]
    identity = person_entity.read_identity(digest, root)
    if identity is None or not identity.get("voices"):
        raise RuntimeError(
            f"no M3b voice tracks for {source_file}; run "
            f"`python3 -m library.tools.person_entity build <project>` first")
    wav = source_memory.source_dir(digest, root) / "program.16k.wav"
    if not wav.is_file():
        raise RuntimeError(f"no program track for {source_file} at {wav}")
    import wave
    with wave.open(str(wav), "rb") as handle:
        duration = handle.getnframes() / handle.getframerate()
    spans = [(max(s, 0.0), min(e, duration), p) for s, e, p in spans
             if s < duration and e > 0]
    stem = Path(source_file).stem
    tracks, excluded = [], []
    for voice in identity["voices"]:
        person, basis = label_track(voice["spans"], spans)
        key = f"{stem}:{voice['track_id']}"
        speech_s = round(sum(e - s for s, e in voice["spans"]), 1)
        if person is None:
            excluded.append({"track": key, "speech_seconds": speech_s, **basis})
        else:
            tracks.append({"key": key, "person": person,
                           "speech_seconds": speech_s, **basis,
                           "embedding": voice["embedding"]})
    if device is None:
        device = diarization.embedding_device()
    starts, embeddings = labelled_window_embeddings(str(wav), spans, device)
    chunks = {size: [{"key": f"{stem}:{person}@{t0:.1f}", "person": person,
                      "embedding": emb}
                     for person, t0, emb in chunk_embeddings(
                         starts, embeddings, spans, size)]
              for size in chunk_sizes}
    labelled = defaultdict(float)
    for s, e, person in spans:
        labelled[person] += e - s
    return {"source_file": source_file, "content_digest": digest,
            "labelled_seconds": {p: round(v, 1) for p, v in labelled.items()},
            "labelled_windows": int(len(starts)), "device": device,
            "tracks": tracks, "excluded_tracks": excluded, "chunks": chunks}


def run_study(manifest: dict, root: Optional[Path] = None) -> dict:
    derived: Dict[str, List[Span]] = {}
    if manifest.get("timeline_transcript"):
        with open(manifest["timeline_transcript"], encoding="utf-8") as fh:
            derived = spans_from_timeline_transcript(json.load(fh))
    sources = []
    for entry in manifest["sources"]:
        path = entry["source_file"]
        if entry.get("person"):
            spans = [(0.0, float("inf"), entry["person"])]
        else:
            spans = derived.get(path) or []
        if not spans:
            raise RuntimeError(f"no labels for {path}: give it a `person` or "
                               f"a timeline transcript that plays it")
        sources.append(measure_source(path, spans, root))

    tracks = [(t["key"], s["source_file"], t["person"], t["embedding"])
              for s in sources for t in s["tracks"]]
    track_report = cross_source_pair_report(tracks)
    min_s = person_entity.VOICE_MATCH_MIN_SECONDS
    matchable = cross_source_pair_report(
        [(t["key"], s["source_file"], t["person"], t["embedding"])
         for s in sources for t in s["tracks"] if t["speech_seconds"] >= min_s])
    labelled_pairs = [(a[3], b[3], a[2] == b[2])
                      for a, b in itertools.combinations(tracks, 2)
                      if a[1] != b[1]]
    separation = person_entity.measure_cross_source_voice_separation(
        labelled_pairs)
    chunk_reports = {}
    for size in CHUNK_WINDOWS:
        items = [(c["key"], s["source_file"], c["person"], c["embedding"])
                 for s in sources for c in s["chunks"].get(size, [])]
        chunk_reports[str(size)] = cross_source_pair_report(items)
    return {
        "tracks": track_report,
        "matchable_tracks": {"min_seconds": min_s, **matchable},
        "separation": {k: v for k, v in separation.items()
                       if not k.endswith("_distances")},
        "chunks": chunk_reports,
        "inputs": [{"source_file": s["source_file"],
                    "labelled_seconds": s["labelled_seconds"],
                    "labelled_windows": s["labelled_windows"],
                    "device": s["device"],
                    "tracks": [{k: v for k, v in t.items() if k != "embedding"}
                               for t in s["tracks"]],
                    "excluded_tracks": s["excluded_tracks"],
                    "chunks": {str(k): len(v) for k, v in s["chunks"].items()}}
                   for s in sources],
    }


def format_report(report: dict) -> str:
    lines = []
    gated = report["matchable_tracks"]
    for label, arm in [("tracks", report["tracks"]),
                       (f"tracks >= {gated['min_seconds']}s", gated)] + [
            (f"chunks of {size} windows", r) for size, r in report["chunks"].items()]:
        lines += [f"## {label}: {arm['items']} items, {arm['same_person_pairs']} "
                  f"same-person / {arm['different_person_pairs']} different-person "
                  f"cross-source pairs",
                  f"weakest same-person {arm.get('weakest_same_person')}",
                  f"closest different-person {arm.get('closest_different_person')}",
                  "", "| threshold | FAR | FRR |", "|---|---|---|"]
        lines += [f"| {r['threshold']:.2f} | {r['FAR']} ({r['false_accepts']}) "
                  f"| {r['FRR']} ({r['false_rejects']}) |" for r in arm["grid"]]
        lines.append("")
    lines.append(f"separation (precision>=0.90, recall>=0.80): {report['separation']}")
    for s in report["inputs"]:
        for ex in s["excluded_tracks"]:
            lines.append(f"excluded {ex['track']}: {ex['excluded']} {ex['labelled_seconds']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="voice_identity_study")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="measure cross-source FAR/FRR over a labelled manifest")
    p_run.add_argument("manifest")
    p_run.add_argument("--out", help="write the full report as JSON here")
    args = parser.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    report = run_study(manifest)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
