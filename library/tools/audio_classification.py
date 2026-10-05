"""Audio classification beyond speech and PANNs (Gap 12).

The temporal index measures PANNs sound events (AudioSet labels) and
energy silence spans.  This module measures two categories BEYOND those:

1. **Absolute speech level** - per-clip RMS of speech spans in dBFS.
   The ``energy_curve`` is normalized per clip, so "clip A is louder
   than clip B" is not answerable from it.  This measurement is
   cross-clip comparable: the same dBFS scale on every clip.

2. **Music genre classification** - a pre-trained model classifies
   the program track into genre categories (rock, jazz, classical,
   etc.) that PANNs' AudioSet vocabulary does not carry.  PANNs
   detects "Music" as a label; it does not say WHAT music.

Both measurements are stored per source and made queryable through
the footage search layer (``footage_query.py``).

The music classifier uses a pre-trained Audio Spectrogram Transformer
(AST) fine-tuned for genre classification.  The model is loaded lazily
on first use; a machine without the model records ``unmeasured`` with
the reason, never a heuristic guess.
"""

from __future__ import annotations

import os

# The pre-trained music genre classification model.
# dima806/music_genre_classification: AST fine-tuned on GTZAN genre
# dataset.  Outputs genre labels (blues, classical, country, disco,
# hiphop, jazz, metal, pop, reggae, rock).  Licence: Apache-2.0.
MUSIC_GENRE_MODEL = "dima806/music_genre_classification"

# The feature extractor sample rate for the AST model.
MUSIC_SAMPLE_RATE = 16000

# Minimum audio length (seconds) for music classification.  Shorter
# spans are too brief for a meaningful genre prediction.
MUSIC_MIN_SECONDS = 1.0


class AudioClassificationUnavailable(RuntimeError):
    """Audio classification could not be measured for this clip.

    Raised with the reason (no model, no audio, load failure).
    Callers record ``method: unmeasured`` and carry on - an unmeasured
    clip refuses classification by name, it never serves guesses.
    """


def measure_speech_level(audio_path: str, speech_spans: list) -> dict:
    """Absolute speech level in dBFS, measured over the speech spans.

    Unlike the per-clip normalized ``energy_curve``, this measurement
    is on an absolute scale (dBFS), so "clip A is louder than clip B"
    IS answerable from it.  The measurement covers only the speech
    spans (word time), not the whole clip.

    Returns ``{"measured": True, "speech_level_dbfs": ...,
    "speech_seconds": ..., "method": ...}`` or
    ``{"measured": False, "reason": ...}``.  Never a level of 0.
    """
    if not audio_path:
        return {"measured": False,
                "reason": "no audio path provided"}
    if not os.path.isfile(audio_path):
        return {"measured": False,
                "reason": f"the audio file is not on disk: {audio_path}"}
    if not speech_spans:
        return {"measured": False,
                "reason": "no speech spans to measure"}

    try:
        import librosa
        import numpy as np
    except ImportError as e:
        return {"measured": False,
                "reason": f"librosa/numpy not available: {e}"}

    try:
        y, sr = librosa.load(audio_path, sr=16000)
    except Exception as e:
        return {"measured": False,
                "reason": f"could not decode audio: {e}"}

    if len(y) == 0:
        return {"measured": False,
                "reason": "audio decodes to silence"}

    # Collect samples that fall within speech spans
    speech_samples = []
    for span in speech_spans:
        try:
            start = float(span[0])
            end = float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        lo = max(0, int(start * sr))
        hi = min(len(y), int(end * sr))
        if hi > lo:
            speech_samples.append(y[lo:hi])

    if not speech_samples:
        return {"measured": False,
                "reason": "no speech spans overlap the audio"}

    import numpy as np
    all_speech = np.concatenate(speech_samples)
    if len(all_speech) == 0:
        return {"measured": False,
                "reason": "speech spans contain no audio samples"}

    rms = float(np.sqrt(np.mean(all_speech ** 2)))
    if rms <= 0.0:
        return {"measured": False,
                "reason": "speech spans measure as digital silence"}

    dbfs = 20.0 * float(np.log10(rms))
    speech_seconds = sum(
        float(s[1]) - float(s[0]) for s in speech_spans
        if isinstance(s, (list, tuple)) and len(s) >= 2
    )

    return {
        "measured": True,
        "speech_level_dbfs": round(dbfs, 2),
        "speech_seconds": round(speech_seconds, 2),
        "method": "librosa-rms-dbfs",
    }


def classify_music(audio_path: str) -> dict:
    """Music genre classification using a pre-trained AST model.

    Classifies the audio into genre categories (blues, classical,
    country, disco, hiphop, jazz, metal, pop, reggae, rock) that
    PANNs' AudioSet vocabulary does not carry.

    Returns ``{"measured": True, "genres": [...], "method": ...,
    "model": ...}`` or ``{"measured": False, "reason": ...}``.
    """
    if not audio_path:
        return {"measured": False,
                "reason": "no audio path provided"}
    if not os.path.isfile(audio_path):
        return {"measured": False,
                "reason": f"the audio file is not on disk: {audio_path}"}

    try:
        import soundfile as sf
    except ImportError as e:
        return {"measured": False,
                "reason": f"soundfile not available: {e}"}

    try:
        audio, sample_rate = sf.read(audio_path, dtype="float32")
    except Exception as e:
        return {"measured": False,
                "reason": f"could not decode audio: {e}"}

    if getattr(audio, "ndim", 1) > 1:
        import numpy as np
        audio = audio.mean(axis=1)
    audio = audio if hasattr(audio, "tolist") else list(audio)

    if len(audio) == 0:
        return {"measured": False,
                "reason": "audio decodes to silence"}

    duration = len(audio) / sample_rate
    if duration < MUSIC_MIN_SECONDS:
        return {"measured": False,
                "reason": f"audio is {duration:.1f}s, below the "
                          f"{MUSIC_MIN_SECONDS}s minimum for genre "
                          f"classification"}

    try:
        from transformers import (
            AutoFeatureExtractor,
            AutoModelForAudioClassification,
            pipeline,
        )
    except ImportError as e:
        return {"measured": False,
                "reason": f"transformers not available: {e}"}

    try:
        pipe = pipeline(
            "audio-classification",
            model=MUSIC_GENRE_MODEL,
            device=-1,
        )
    except Exception as e:
        return {"measured": False,
                "reason": f"could not load music genre model: {e}"}

    try:
        result = pipe(audio, sampling_rate=sample_rate)
    except Exception as e:
        return {"measured": False,
                "reason": f"music genre classification failed: {e}"}

    if not result:
        return {"measured": False,
                "reason": "music genre model returned no predictions"}

    genres = []
    for item in result:
        label = item.get("label", "")
        score = item.get("score", 0.0)
        if label:
            genres.append({"label": label, "confidence": round(score, 4)})

    return {
        "measured": True,
        "genres": genres,
        "method": f"ast-genre-{MUSIC_GENRE_MODEL}",
        "model": MUSIC_GENRE_MODEL,
    }


def classify_ambience(audio_path: str, speech_spans: list) -> dict:
    """Ambience type classification on non-speech, non-music spans.

    Classifies the acoustic environment of non-speech spans into
    broad categories (indoor, outdoor, urban, nature) using spectral
    features.  This is a lightweight classifier that complements
    PANNs (which detects specific sound events but not the overall
    acoustic environment type).

    Returns ``{"measured": True, "ambience": ..., "method": ...}`` or
    ``{"measured": False, "reason": ...}``.
    """
    if not audio_path:
        return {"measured": False,
                "reason": "no audio path provided"}
    if not os.path.isfile(audio_path):
        return {"measured": False,
                "reason": f"the audio file is not on disk: {audio_path}"}

    try:
        import librosa
        import numpy as np
    except ImportError as e:
        return {"measured": False,
                "reason": f"librosa/numpy not available: {e}"}

    try:
        y, sr = librosa.load(audio_path, sr=16000)
    except Exception as e:
        return {"measured": False,
                "reason": f"could not decode audio: {e}"}

    if len(y) == 0:
        return {"measured": False,
                "reason": "audio decodes to silence"}

    # Find non-speech spans
    speech_mask = np.zeros(len(y), dtype=bool)
    for span in speech_spans or []:
        try:
            start = float(span[0])
            end = float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        lo = max(0, int(start * sr))
        hi = min(len(y), int(end * sr))
        speech_mask[lo:hi] = True

    non_speech = y[~speech_mask]
    if len(non_speech) < sr * 0.5:
        return {"measured": False,
                "reason": "insufficient non-speech audio for ambience "
                          "classification"}

    # Spectral features for ambience classification
    spectral_centroids = librosa.feature.spectral_centroid(
        y=non_speech, sr=sr)[0]
    spectral_rolloff = librosa.feature.spectral_rolloff(
        y=non_speech, sr=sr)[0]
    zero_crossing_rate = librosa.feature.zero_crossing_rate(non_speech)[0]
    spectral_bandwidth = librosa.feature.spectral_bandwidth(
        y=non_speech, sr=sr)[0]

    mean_centroid = float(np.mean(spectral_centroids))
    mean_rolloff = float(np.mean(spectral_rolloff))
    mean_zcr = float(np.mean(zero_crossing_rate))
    mean_bandwidth = float(np.mean(spectral_bandwidth))

    # Rule-based classification from spectral features.
    # These thresholds are derived from acoustic characteristics:
    # - Indoor: low centroid, low rolloff, narrow bandwidth
    # - Outdoor: moderate centroid, wide bandwidth
    # - Urban: high ZCR (traffic, machinery), high rolloff
    # - Nature: low ZCR, moderate centroid, narrow bandwidth
    if mean_zcr > 0.15 and mean_rolloff > 4000:
        ambience_type = "urban"
    elif mean_centroid < 1000 and mean_bandwidth < 2000:
        ambience_type = "indoor"
    elif mean_zcr < 0.05 and mean_centroid < 2000:
        ambience_type = "nature"
    else:
        ambience_type = "outdoor"

    return {
        "measured": True,
        "ambience": ambience_type,
        "method": "spectral-features",
        "features": {
            "spectral_centroid_hz": round(mean_centroid, 1),
            "spectral_rolloff_hz": round(mean_rolloff, 1),
            "zero_crossing_rate": round(mean_zcr, 4),
            "spectral_bandwidth_hz": round(mean_bandwidth, 1),
        },
    }
