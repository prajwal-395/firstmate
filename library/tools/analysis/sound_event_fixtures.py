"""Deterministic synthetic sound-event fixtures with known ground truth.

Real-footage labels need independent agent labellers (the captain
never labels); these fixtures need none, because the generator IS the
annotator: every transient, tone and bed is placed at a recorded
sample, so reference onsets and offsets are construction truth
(`review_status: synthetic`, calibration only - never held-out gold).

Four scenes, 16 kHz mono, each exercising a punch-list slice:

- `slam_quiet`: a door-slam transient in room tone (AUD-056 edges).
- `glass_music_overlap`: glass break over a music bed (AUD-058
  music/environmental overlap; glass is the AUD-057 rare slice).
- `bark_speech_overlap`: dog barks over a speech-like proxy - the
  proxy is modulated harmonics, not speech, and carries no label, so
  no model is asked to detect it; it only crowds the barks (AUD-058
  dialogue overlap).
- `boing_nonstandard`: a fast pitch-sweep "boing" (AUD-057
  nonstandard) plus a second slam to test repeated-instance recall.

`synthesize(directory)` writes the WAVs and returns the
`ren_sound_event_benchmark/1`-shaped references document with empty
proposals; the bench runner fills proposals per candidate. All RNG is
seeded; byte-identical output for a given numpy version is the goal,
and `references.json` records the generator version and seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
GENERATOR = "synthetic:deterministic-generator-v1"
SEED = 20261016

SCENES = ("slam_quiet", "glass_music_overlap", "bark_speech_overlap",
          "boing_nonstandard")


def _room_tone(rng: np.random.Generator, n: int, level: float = 0.004) -> np.ndarray:
    return (level * rng.standard_normal(n)).astype(np.float64)


def _place(track: np.ndarray, event: np.ndarray, at_seconds: float) -> None:
    start = int(at_seconds * SAMPLE_RATE)
    end = min(len(track), start + len(event))
    track[start:end] += event[:end - start]


def _slam(rng: np.random.Generator, duration: float = 0.6) -> np.ndarray:
    """Door slam: low thump plus a decaying noise burst."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    thump = 0.55 * np.sin(2 * np.pi * 68 * t) * np.exp(-t * 14)
    burst = 0.30 * rng.standard_normal(n) * np.exp(-t * 22)
    return thump + burst


def _glass(rng: np.random.Generator, duration: float = 0.8) -> np.ndarray:
    """Glass break: a cluster of high-frequency pings."""
    n = int(duration * SAMPLE_RATE)
    out = np.zeros(n)
    t = np.arange(n) / SAMPLE_RATE
    for offset, freq, amp, decay in (
        (0.00, 5200, 0.30, 30), (0.09, 6400, 0.22, 34),
        (0.21, 4700, 0.25, 28), (0.33, 7100, 0.16, 40),
        (0.47, 5800, 0.12, 44),
    ):
        k = int(offset * SAMPLE_RATE)
        seg = amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
        out[k:k + len(seg)] += seg[:max(0, n - k)]
    out += 0.05 * rng.standard_normal(n) * np.exp(-t * 9)
    return out


def _bark(duration: float = 0.35) -> np.ndarray:
    """Dog bark: two harmonic bursts with a fast pitch drop."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    sweep = 420 - 1400 * t
    phase = 2 * np.pi * (420 * t - 700 * t * t)
    envelope = np.exp(-t * 16) * (t < 0.16) + np.exp(-(t - 0.18) * 16) * (t >= 0.18)
    _ = sweep
    return 0.4 * (np.sin(phase) + 0.4 * np.sin(2 * phase)) * envelope


def _boing(duration: float = 0.7) -> np.ndarray:
    """Cartoon boing: fast up-down pitch sweep on a twangy harmonic."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    freq = 180 + 520 * np.sin(2 * np.pi * 3.2 * t)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    return 0.35 * (np.sin(phase) + 0.3 * np.sin(3 * phase)) * np.exp(-t * 4)


def _music_bed(duration: float, rng: np.random.Generator) -> np.ndarray:
    """Steady chord bed (A3+C#4+E4) with slow tremolo."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    tremolo = 0.75 + 0.25 * np.sin(2 * np.pi * 1.6 * t)
    bed = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63))
    bed = 0.09 * bed * tremolo
    bed += 0.006 * rng.standard_normal(n)
    return bed


def _speech_proxy(duration: float) -> np.ndarray:
    """Speech-like amplitude-modulated harmonics; carries no label."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 3.7 * t) ** 2
    wave = (0.20 * np.sin(2 * np.pi * 150 * t)
            + 0.09 * np.sin(2 * np.pi * 300 * t)
            + 0.04 * np.sin(2 * np.pi * 450 * t))
    return wave * syllables


def _scene(name: str, rng: np.random.Generator):
    if name == "slam_quiet":
        duration = 6.0
        track = _room_tone(rng, int(duration * SAMPLE_RATE))
        _place(track, _slam(rng), 2.0)
        refs = [{"event_id": "slam_quiet-e1", "label": "Door slam",
                 "start": 2.0, "end": 2.6, "rarity": "common"}]
        negatives = [{"negative_id": "slam_quiet-n1", "label": "Laughter",
                      "start": 3.5, "end": 4.5,
                      "reason": "room tone must not read as laughter"}]
        conditions = ["quiet_room"]
    elif name == "glass_music_overlap":
        duration = 8.0
        track = _music_bed(duration, rng)
        _place(track, _glass(rng), 4.0)
        refs = [{"event_id": "glass_music_overlap-e1", "label": "Music",
                 "start": 0.0, "end": 8.0, "rarity": "common"},
                {"event_id": "glass_music_overlap-e2", "label": "Glass break",
                 "start": 4.0, "end": 4.8, "rarity": "rare"}]
        negatives = [{"negative_id": "glass_music_overlap-n1",
                      "label": "Gunshot", "start": 4.0, "end": 4.8,
                      "reason": "glass transients must not read as gunfire"}]
        conditions = ["overlapping_music_environmental"]
    elif name == "bark_speech_overlap":
        duration = 8.0
        track = _speech_proxy(duration)
        track += _room_tone(rng, len(track))
        _place(track, _bark(), 3.0)
        _place(track, _bark(), 5.5)
        refs = [{"event_id": "bark_speech_overlap-e1", "label": "Dog bark",
                 "start": 3.0, "end": 3.35, "rarity": "common"},
                {"event_id": "bark_speech_overlap-e2", "label": "Dog bark",
                 "start": 5.5, "end": 5.85, "rarity": "common"}]
        negatives = [{"negative_id": "bark_speech_overlap-n1",
                      "label": "Cough", "start": 1.0, "end": 2.0,
                      "reason": "voiced proxy must not read as cough"}]
        conditions = ["overlapping_speech_proxy_environmental"]
    elif name == "boing_nonstandard":
        duration = 8.0
        track = _room_tone(rng, int(duration * SAMPLE_RATE))
        _place(track, _boing(), 1.5)
        _place(track, _slam(rng), 5.0)
        refs = [{"event_id": "boing_nonstandard-e1", "label": "Boing",
                 "start": 1.5, "end": 2.2, "rarity": "nonstandard"},
                {"event_id": "boing_nonstandard-e2", "label": "Door slam",
                 "start": 5.0, "end": 5.6, "rarity": "common"}]
        negatives = [{"negative_id": "boing_nonstandard-n1", "label": "Music",
                      "start": 1.5, "end": 2.2,
                      "reason": "pitched sweep must not read as music"}]
        conditions = ["quiet_room"]
    else:  # pragma: no cover - SCENES is the only caller
        raise ValueError(f"unknown scene {name!r}")
    peak = max(1e-9, float(np.abs(track).max()))
    track = (0.89 * track / peak).astype(np.float32)
    for ref in refs:
        ref["provenance"] = {"annotator_id": GENERATOR,
                             "review_status": "synthetic",
                             "evidence_ref": f"{name}:{ref['start']}-{ref['end']}"}
    return track, {"source_id": name, "duration_seconds": duration,
                   "conditions": conditions, "proposals": [],
                   "reference_events": refs, "hard_negatives": negatives}


def synthesize(directory: str | Path) -> dict:
    """Write scene WAVs plus the references document; return the document."""
    import soundfile as sf

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    sources = []
    for name in SCENES:
        track, source = _scene(name, rng)
        sf.write(str(root / f"{name}.wav"), track, SAMPLE_RATE)
        sources.append(source)
    doc = {"schema_version": 1, "split": "calibration", "sources": sources,
           "generator": GENERATOR, "seed": SEED,
           "sample_rate": SAMPLE_RATE}
    (root / "references.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc
