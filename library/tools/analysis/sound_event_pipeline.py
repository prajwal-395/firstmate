"""Measured non-speech sound events per clip (fidelity rung 5e, events).

Step 1.04's `classify_audio_events` used to threshold RMS energy and
zero-crossing rate into `silence` / `ambient_noise` guesses - no laugh,
no impact, no music ever named. Rung 3c's Resolve audio classification
sense labels a whole clip ("Effect / Dialogue, Car, Bus") with no
timing. This module measures TIMED events: PANNs Cnn14_DecisionLevelMax
frame-level sound-event detection over the AudioSet vocabulary, 100
frames per second, chunked at 60 s windows so an hour-long clip costs
bounded memory.

Licence first (the brief's gate, measured 2026-09-24 on this machine):

- code: `panns-inference` 0.1.1 MIT, `torchlibrosa` 0.1.0 MIT
  (installed METADATA classifiers, verified in the shared venv).
- weights: `Cnn14_DecisionLevelMax_mAP=0.385.pth` from Zenodo record
  3987831, whose record licence is CC-BY-4.0 - commercial use with
  attribution, md5 70539c43c18b6a289b3199c503a82c5a verified at
  download. The checkpoint lives once per machine under
  `vep_home()/models/panns/` (`library/tools/shared_environment.py`);
  `scripts/install_panns.sh` is the one way to fill it.

Measured against, not adopted (landscape evidence, same footage):

- sherpa-onnx CED-tiny sliding windows (4 s window, 1 s hop): the same
  cough/sneeze burst both models place at abs ~2695-2701 s on the
  captain's multicam audio, but at ~1 s resolution against PANNs'
  10 ms frames, 82 MB RSS against ~2.9 GB, AND its upstream
  (RicherMans/CED) is GPL-3.0 while its model tarball ships no
  licence file at all - so it stays a measurement, never a dependency.
- LAION CLAP (code CC0-1.0, `larger_clap_general` weights Apache-2.0):
  licence-clean but clip-level similarity with no native timestamps -
  the same timing weakness as CED with heavier machinery.
- BEATs: clip-level AudioSet tagging, no frame timestamps, weights
  licence unverified - not measured; PANNs and CED cover the decision.

Measured on the captain's audio (read-only, excerpts to scratch):

- 53 s podcast sides (akshita/craig): PANNs speech spans cover 97.5%
  of Voz+MFA transcript word-time - the frame timing is trustworthy
  against independent ground truth.
- 10 s clip_10-20: Music 0.32-1.92 / 3.52-7.67 / 7.99-10.0 s, max
  0.717, edges rising 0.02 to 0.71 within ~0.4 s; CED-tiny agrees
  (Music 0.59-0.74 every window).
- 82 min multicam LC4932 + 68 min LCATL0013: no laughter anywhere
  (Laughter max 0.008) - controlled shoots. The cough/sneeze burst
  both mics hear (PANNs Cough 0.26-0.30, Sneeze 0.13-0.23; CED-tiny
  Cough peaking 0.598 in the same span) is the cross-model timing
  check both agree on to ~1 s.
- Wall time on this machine (M4, CPU, torch 4 threads): 100-210x
  realtime, model load ~1 s, peak RSS ~2.9 GB (torch base + 327 MB
  weights). A 16 kHz source upsampled to 32 kHz reproduces the native
  measurement (Speech frames agree 1.0, Music 0.87), so the pipeline
  measures from its existing 16 kHz index audio with no extra pass.

What is recorded: every AudioSet label EXCEPT voiced-speech classes,
whose timing the transcript already carries to the word
(`WITHHELD_SPEECH_LABELS` below - Speech, Male/Female/Child speech,
Conversation, Narration, Babbling, Whispering). Laughter, Shout,
Screaming and the rest stay: a scream is an event a cut lands on even
where words time it too. Each event carries the model's own label
verbatim, start/end in source seconds, and max frame confidence.
Absent events stay absent: an unavailable checkpoint records
`method: unmeasured` with the reason, never a heuristic guess.
"""

from __future__ import annotations

import os

# Detector operating points, set by the measurement above - not taste.
# 0.25 keeps transcript-agreeing speech (97.5% word cover) while the
# sub-0.2 band on these clips is room-tone flicker; 0.15 s drops
# single-frame blips; gaps under 0.25 s merge one burst (a coughing
# fit reads as one event, not five).
FRAME_THRESHOLD = 0.25
MIN_DURATION_SECONDS = 0.15
MERGE_GAP_SECONDS = 0.25
CHUNK_SECONDS = 60
TARGET_SAMPLE_RATE = 32000

MODEL_CHECKPOINT_NAME = "Cnn14_DecisionLevelMax_mAP=0.385.pth"
MODEL_MD5 = "70539c43c18b6a289b3199c503a82c5a"
MODEL_LICENCE = "CC-BY-4.0"
METHOD = "panns-cnn14-decisionlevelmax"

# Voiced-speech classes whose timing the transcript owns to the word.
# Withheld from the event layer so a cut never lands on "speech"
# instead of on a word; everything else - including Laughter - records.
WITHHELD_SPEECH_LABELS = frozenset({
    "Speech",
    "Male speech, man speaking",
    "Female speech, woman speaking",
    "Child speech, kid speaking",
    "Conversation",
    "Narration, monologue",
    "Babbling",
    "Whispering",
})


class SoundEventsUnavailable(RuntimeError):
    """No sound events could be measured for this clip.

    Raised with the reason (no checkpoint, no torch, load failure).
    Callers record `method: unmeasured` and carry on - an unmeasured
    clip refuses event anchors by name, it never serves guesses.
    """


def default_checkpoint() -> str:
    """The Cnn14 DecisionLevelMax checkpoint this machine measures with.

    `PIPELINE_PANNS_CHECKPOINT` wins outright (the escape hatch, set in
    `~/.config/ren/config.env` like every other external path).
    Otherwise `vep_home()/models/panns/<name>` - where
    `scripts/install_panns.sh` puts it. This module never downloads:
    a missing file raises `SoundEventsUnavailable` naming the script.
    """
    from library.tools.shared_environment import panns_checkpoint
    return str(panns_checkpoint())


def measure_sound_events(audio_path: str,
                         checkpoint_path: str | None = None) -> dict:
    """Timed non-speech events in `audio_path`, in SOURCE seconds.

    Returns `{"events": [...], "method": METHOD, "model": {...}}` where
    each event is `{"label", "start", "end", "confidence"}`. Raises
    `SoundEventsUnavailable` where nothing can be measured - the caller
    owns the unmeasured record, this function writes none.
    """
    import numpy as np

    try:
        import soundfile as sf
    except ImportError as e:
        raise SoundEventsUnavailable(
            f"soundfile is not installed ({e})") from e
    try:
        import torch
    except ImportError as e:
        raise SoundEventsUnavailable(
            f"torch is not installed ({e})") from e
    try:
        from panns_inference import SoundEventDetection, labels
    except ImportError as e:
        raise SoundEventsUnavailable(
            f"panns-inference is not installed ({e}); "
            f"scripts/install_panns.sh fills the venv half") from e

    checkpoint = checkpoint_path or default_checkpoint()
    if (not os.path.isfile(checkpoint)
            or os.path.getsize(checkpoint) < 3e8):
        raise SoundEventsUnavailable(
            f"PANNs checkpoint is not on this machine ({checkpoint}); "
            f"run scripts/install_panns.sh")

    audio, sample_rate = sf.read(audio_path, dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) == 0:
        raise SoundEventsUnavailable(f"{audio_path} decodes to no audio")
    if sample_rate != TARGET_SAMPLE_RATE:
        import librosa
        audio = librosa.resample(audio, orig_sr=sample_rate,
                                 target_sr=TARGET_SAMPLE_RATE,
                                 res_type="soxr_hq").astype(np.float32)

    detector = SoundEventDetection(checkpoint_path=checkpoint,
                                   device="cpu")
    window = CHUNK_SECONDS * TARGET_SAMPLE_RATE
    frames = []
    with torch.no_grad():
        for start in range(0, len(audio), window):
            chunk = audio[start:start + window]
            frames.append(detector.inference(chunk[None, :])[0])
    framewise = np.concatenate(frames, axis=0)
    frame_rate = framewise.shape[0] / (len(audio) / TARGET_SAMPLE_RATE)

    events = []
    for index, label in enumerate(labels):
        if label in WITHHELD_SPEECH_LABELS:
            continue
        column = framewise[:, index]
        if float(column.max()) < FRAME_THRESHOLD:
            continue
        for span_start, span_end in _runs(column >= FRAME_THRESHOLD,
                                          frame_rate):
            peak = float(column[int(span_start * frame_rate):
                                int(span_end * frame_rate)].max())
            events.append({
                "label": label,
                "start": round(span_start, 3),
                "end": round(span_end, 3),
                "confidence": round(peak, 3),
            })
    events.sort(key=lambda e: (e["start"], e["end"]))
    return {
        "events": events,
        "method": METHOD,
        "model": {
            "checkpoint": os.path.basename(checkpoint),
            "licence": MODEL_LICENCE,
            "frame_threshold": FRAME_THRESHOLD,
            "min_duration_seconds": MIN_DURATION_SECONDS,
        },
    }


def _runs(mask, frame_rate: float):
    """Contiguous True runs as (start, end) seconds, short runs dropped
    and gaps under MERGE_GAP_SECONDS closed."""
    runs, start = [], None
    for frame, on in enumerate(mask):
        if on and start is None:
            start = frame
        elif not on and start is not None:
            runs.append((start, frame))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    merged = []
    for run_start, run_end in runs:
        if (merged and (run_start - merged[-1][1]) / frame_rate
                <= MERGE_GAP_SECONDS):
            merged[-1][1] = run_end
        else:
            merged.append([run_start, run_end])
    return [(run_start / frame_rate, run_end / frame_rate)
            for run_start, run_end in merged
            if (run_end - run_start) / frame_rate
            >= MIN_DURATION_SECONDS]
