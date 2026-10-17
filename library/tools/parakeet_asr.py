"""The original Parakeet checkpoint, run independently of Voz.

AUD-003 qualifies NVIDIA's `parakeet-tdt-0.6b-v3` as an INDEPENDENT
commercial alternative to Voz: the same base weights, but a separately
runnable distribution (publisher F32 safetensors / `.nemo`, CC BY 4.0)
through a different runtime (NeMo) at a different precision (full
float32, per the under-1.5B candidate rule) from Voz's shipped Core ML
build. Voz is a conversion of this checkpoint, so this comparison is an
independent runtime/distribution comparison, never an independent set of
trained weights - the scorecard says exactly that.

Every NeMo-specific fact lives behind `_load_model` and
`_words_from_hypothesis`, so a NeMo major-version API move is a small
edit here and nowhere else. NeMo is NOT a pipeline dependency: the
import happens inside `transcribe`, and without it the call refuses
with `ParakeetUnavailable` rather than importing at module load.

The return value reuses `heard_speech.HeardSpeech`, so a hypothesis from
this module and one from Voz score through the same
`library/tools/speech_eval.py` path with no adapter in between.

Callers must provide a language identified before transcription. Missing
and unsupported languages refuse before NeMo loads the checkpoint, so an
unsupported file cannot silently produce fluent-looking text.
"""

from __future__ import annotations

import os
import wave
from typing import Any, Dict, List, Optional

from library.tools import heard_speech

#: The artifact this module runs. Full precision, per the candidate rule
#: for models under ~1.5B parameters.
CHECKPOINT_FILENAME = "parakeet-tdt-0.6b-v3.nemo"

#: What the inference path consumes. NeMo resamples internally, but the
#: benchmark records the actual input rate on the engine block.
SAMPLE_RATE = 16000
SUPPORTED_LANGUAGES = frozenset({
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de",
    "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk",
    "sl", "es", "sv", "ru", "uk",
})

TRANSCRIBER_NAME = "parakeet-nemo"

# One resident model per benchmark process. Re-loading for each window would
# make every warm call another cold start and waste the scheduler reservation.
_MODEL_CACHE: Dict[str, Any] = {}


class ParakeetUnavailable(RuntimeError):
    """The checkpoint or its runtime cannot answer.

    Raised rather than returning an empty transcription, for the same
    reason `heard_speech` refuses: an empty hearing and a silent file
    must never arrive as the same object.
    """


def _require_nemo() -> Any:
    try:
        import nemo.collections.asr as nemo_asr  # type: ignore
    except ImportError as missing:
        raise ParakeetUnavailable(
            "NeMo is not importable in this interpreter, so the original "
            "Parakeet checkpoint cannot run here. Install nemo_toolkit[asr] "
            "in a scratch interpreter (never the pipeline venv) and retry."
        ) from missing
    return nemo_asr


def _load_model(nemo_asr: Any, model_path: str) -> Any:
    """Restore the checkpoint. The one NeMo entry point."""
    try:
        return nemo_asr.models.EncDecRNNTModel.restore_from(model_path)
    except Exception as failed:
        raise ParakeetUnavailable(
            f"NeMo could not restore {model_path}: {failed}") from failed


def is_model_loaded(model_path: str) -> bool:
    """Whether this interpreter already holds the requested checkpoint."""
    return os.path.realpath(model_path) in _MODEL_CACHE


def supports_language(detected_language: str) -> bool:
    """Whether a prior language-id result is in the checkpoint's coverage."""
    return detected_language.strip().lower() in SUPPORTED_LANGUAGES


def load_model(model_path: str) -> Any:
    """Load and retain one checkpoint for the life of this process."""
    key = os.path.realpath(model_path)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = _load_model(_require_nemo(), key)
    return _MODEL_CACHE[key]


def _words_from_hypothesis(hypothesis: Any) -> List[Dict[str, Any]]:
    """Map one NeMo timestamped hypothesis onto plain word rows.

    NeMo's timestamped transcription carries word segments as
    `hypothesis.timestamp["word"]`, each with `word`, `start`, `end`
    seconds. Anything without usable timing is dropped rather than
    guessed - the same rule `heard_speech.read_payload` applies.
    """
    timestamp = getattr(hypothesis, "timestamp", None) or {}
    segments = timestamp.get("word") if isinstance(timestamp, dict) else None
    words: List[Dict[str, Any]] = []
    if not isinstance(segments, list):
        return words
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("word") or "")
        if not text.strip():
            continue
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        if not (end > start):
            continue
        words.append({"word": text, "start": start, "end": end})
    words.sort(key=lambda w: (w["start"], w["end"]))
    return words


def _transcribe_with_model(model: Any, wav_path: str) -> Any:
    """Run one timestamped transcription. The other NeMo entry point."""
    try:
        hypotheses = model.transcribe([wav_path], timestamps=True)
    except Exception as failed:
        raise ParakeetUnavailable(
            f"NeMo transcription failed on "
            f"{os.path.basename(wav_path)}: {failed}") from failed
    if not hypotheses:
        raise ParakeetUnavailable(
            f"NeMo returned no hypothesis for {os.path.basename(wav_path)}")
    return hypotheses[0]


def _wav_duration_seconds(wav_path: str) -> Optional[float]:
    try:
        with wave.open(wav_path, "rb") as audio:
            return audio.getnframes() / float(audio.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


def transcribe(wav_path: str, *, model_path: str,
               checkpoint_revision: str = "",
               detected_language: str = "") -> heard_speech.HeardSpeech:
    """Transcribe one 16 kHz mono WAV with the original checkpoint.

    `model_path` is the local `.nemo` file (a download the lane records
    with its revision, size and path, and deletes after the benchmark
    unless the checkpoint is adopted). `checkpoint_revision` names the
    publisher revision it was fetched at, recorded verbatim on the
    engine block. `detected_language` must come from a prior language-id
    pass; unknown and unsupported codes are refused before model loading.
    """
    if not wav_path or not os.path.isfile(wav_path):
        raise ParakeetUnavailable(
            f"there is no file at {wav_path!r} to transcribe.")
    if not model_path or not os.path.isfile(model_path):
        raise ParakeetUnavailable(
            f"the Parakeet checkpoint is not at {model_path!r}. "
            f"Download {CHECKPOINT_FILENAME} first; it is never shipped.")
    language = detected_language.strip().lower()
    if not supports_language(language):
        raise ParakeetUnavailable(
            f"refusing Parakeet transcription for unsupported or unknown "
            f"detected language {detected_language!r}; run language "
            "identification first")
    model = load_model(model_path)
    hypothesis = _transcribe_with_model(model, wav_path)
    rows = _words_from_hypothesis(hypothesis)
    heard_words = [heard_speech.HeardWord(word=row["word"],
                                          start=row["start"],
                                          end=row["end"]) for row in rows]
    text = getattr(hypothesis, "text", "") or ""
    return heard_speech.HeardSpeech(
        words=heard_words,
        text=str(text),
        engine={
            "transcriber": TRANSCRIBER_NAME,
            "checkpoint": CHECKPOINT_FILENAME,
            "checkpoint_revision": checkpoint_revision,
            "model_path": model_path,
            "precision": "float32",
            "sample_rate": SAMPLE_RATE,
            "detected_language": language,
            "duration_seconds": _wav_duration_seconds(wav_path),
        },
        # No anomalies: `heard_speech._anomalies` applies bounds COUNTED
        # on Voz output (LONG_WORD_SECONDS et al). Reusing them here
        # would report a Parakeet defect under a Voz measurement, so the
        # list stays empty until equivalent bounds are counted for this
        # runtime. Word-boundary quality is carried by the benchmark's
        # boundary-error metrics instead.
        anomalies=[],
        media_path=wav_path,
    )
