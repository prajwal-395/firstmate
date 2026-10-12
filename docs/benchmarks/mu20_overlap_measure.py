"""MU-20 acceptance: crosstalk precision and recall on the captain's ISO pair.

The entities report section 5 method (mix ISO tracks, reference from the
per-ISO word timings), with the overlap constructed the way its E3 run
did, scaled up and kept physical: a span where Craig talks alone is
copied from BOTH cameras - his voice on his camera and its real bleed on
Akshita's - and added at a moment where Akshita talks alone. Reference
crosstalk = the shifted Craig words AND Akshita's words, plus the
transcript's natural overlap. Scored inside the labelled region (within
1 s of a transcript word) with the module's own scorer.

Reads (never writes) source memory's program audio and M6 clocks for
LC4932/LCATL0013 and geo-podcast's timeline transcript; writes only the
constructed tracks under --out. On main this fails at the import.

    bin/vep docs/benchmarks/mu20_overlap_measure.py --out <scratch> --seed 21
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

from library.tools import overlapped_speech as ov

MEMORY = Path.home() / ".local/share/vep/source_memory"
TRANSCRIPT = Path.home() / ("Documents/content_stuff/video_projects/lucie/"
                            "geo-podcast/pipeline_output/scratch/"
                            "timeline_transcript/transcript.json")
CAMERAS = {  # speaker -> (camera file, content digest)
    "Akshita": ("LC4932.MXF", "dca969b8ed907f10bd2b4fa7c4de24390586d5a841c4ebf97b56c2623a4151ea"),
    "Craig": ("LCATL0013.MXF", "157522998320e9352229df48c41722da817438bae118534410cb7d7411bb1c7c"),
}
HOP = ov.FRAME_HOP_S
SR = ov.SAMPLE_RATE


def _offset(digest):
    clock = json.loads((MEMORY / digest / "clock.json").read_text())
    return clock["offset_to_reference_seconds"]


def _on_reference_clock():
    """Both cameras' program audio on the M6 reference clock."""
    tracks = {}
    length = None
    for speaker, (_, digest) in CAMERAS.items():
        samples = ov._read_track(MEMORY / digest / "program.16k.wav")
        shift = round(_offset(digest) * SR)
        if length is None:
            length = len(samples) + shift
        placed = np.zeros(length, np.float32)
        lo, hi = max(0, shift), min(length, len(samples) + shift)
        placed[lo:hi] = samples[lo - shift:hi - shift]
        tracks[speaker] = placed
    return tracks


def _word_coverage(frames):
    """Each speaker's words from the timeline transcript, on the same clock."""
    document = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    cover = {speaker: np.zeros(frames, bool) for speaker in CAMERAS}
    for row in document["segments"]:
        speaker = row["speaker"]
        if (speaker not in CAMERAS
                or not str(row["source_file"]).endswith(CAMERAS[speaker][0])):
            continue
        shift = (row["source_start"] - row["timeline_start"]
                 + _offset(CAMERAS[speaker][1]))
        for word in row["words"]:
            if word.get("timed") is False:
                continue
            first = round((word["start"] + shift) / HOP)
            last = round((word["end"] + shift) / HOP)
            cover[speaker][max(first, 0):min(last, frames)] = True
    return cover


def _write(path, samples):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(
            (np.clip(samples, -1, 32767 / 32768) * 32768).astype(np.int16).tobytes())


def construct(out: Path, seed: int, count: int) -> dict:
    tracks = _on_reference_clock()
    a, c = tracks["Akshita"].copy(), tracks["Craig"].copy()
    frames = len(a) // round(HOP * SR)
    cover = _word_coverage(frames)
    akshita, craig = cover["Akshita"], cover["Craig"]
    guard = round(1.0 / HOP)

    def near(mask):
        return np.convolve(mask.astype(int), np.ones(2 * guard + 1), "same") > 0

    craig_alone, akshita_alone = craig & ~near(akshita), akshita & ~near(craig)
    rolling = ov.frame_db(c)[:frames] > ov.RECORDING_FLOOR_DB
    reference = akshita & craig
    region = near(akshita | craig)
    rng = np.random.default_rng(seed)
    used = np.zeros(frames, bool)
    hop = round(HOP * SR)
    inserted = 0
    for _ in range(50_000):
        if inserted == count:
            break
        length = round(rng.uniform(0.6, 3.0) / HOP)
        src, dst = (int(v) for v in rng.integers(0, frames - length, 2))
        if (craig_alone[src:src + length].mean() < 0.7
                or akshita_alone[dst:dst + length].mean() < 0.7
                or not rolling[src:src + length].all()
                or not rolling[dst:dst + length].all()
                or used[max(0, dst - 3 * guard):dst + length + 3 * guard].any()
                or abs(src - dst) < 4 * guard):
            continue
        for track in (a, c):
            track[dst * hop:(dst + length) * hop] += (
                track[src * hop:(src + length) * hop].copy())
        reference[dst:dst + length] |= (craig[src:src + length]
                                        & akshita[dst:dst + length])
        used[dst:dst + length] = True
        inserted += 1
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "akshita.wav", a)
    _write(out / "craig.wav", c)

    def spans(mask):
        return [(first * HOP, last * HOP) for first, last in ov._runs(mask)]

    return {"inserted": inserted, "reference": spans(reference),
            "region": spans(region), "duration": len(a) / SR,
            "reference_seconds": round(float(reference.sum() * HOP), 2)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--count", type=int, default=60)
    args = parser.parse_args()
    corpus = construct(args.out, args.seed, args.count)
    measured = ov.iso_overlap({"Akshita": args.out / "akshita.wav",
                               "Craig": args.out / "craig.wav"})
    inside = [(max(s.start, lo), min(s.end, hi)) for s in measured.spans
              for lo, hi in corpus["region"] if min(s.end, hi) > max(s.start, lo)]
    result = {
        "seed": args.seed, "inserted": corpus["inserted"],
        "reference_seconds": corpus["reference_seconds"],
        "bleed_modes_db": measured.basis["bleed_modes_db"],
        "collar_0.25": ov.score(inside, corpus["reference"], corpus["duration"]),
        "no_collar": ov.score(inside, corpus["reference"], corpus["duration"],
                              collar=0.0)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
