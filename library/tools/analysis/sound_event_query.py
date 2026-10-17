"""Query-directed sound-event refinement with explicit separation lineage.

PANNs measurements on the original audio remain authoritative. When a
specific query's measured candidate is weak or overlaps speech/music, the
caller may run HTDemucs and inspect detections on its estimated stems. Those
detections are returned as candidates on the source time axis; they never
replace the original measurement automatically.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path


_MASKING_LABELS = {"music", "speech", "conversation", "singing"}
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD.findall(text)}


def _overlap(first: dict, second: dict) -> bool:
    return max(float(first["start"]), float(second["start"])) < min(
        float(first["end"]), float(second["end"])
    )


def should_separate_for_query(query: str, events: list[dict], *,
                              confidence_threshold: float = 0.45) -> bool:
    """Return whether measured query evidence calls for stem inspection.

    A specific query must match a measured event label. Separation is
    requested only for a low-confidence match or one temporally masked by a
    measured speech/music event. No match does not justify inventing a source
    or spending separation compute.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return False
    masking = [event for event in events
               if str(event["label"]).strip().lower() in _MASKING_LABELS]
    targets = [event for event in events
               if _tokens(str(event["label"])) & query_tokens
               and str(event["label"]).strip().lower() not in _MASKING_LABELS]
    return any(
        float(target["confidence"]) < confidence_threshold
        or any(_overlap(target, mask) for mask in masking)
        for target in targets
    )


def measure_query_with_separation(
    audio_path: str | Path,
    query: str,
    *,
    baseline: dict | None = None,
    checkpoint_path: str | None = None,
    separator=None,
) -> dict:
    """Measure the source, then conditionally inspect Demucs stem candidates.

    This call must run under `music.analyse:model_memory`, the existing
    HTDemucs resource lease. The source file is read-only. Separated WAVs are
    temporary; only measured candidate events and their estimated-source
    lineage are returned. Caller-owned original events remain untouched.
    """
    import soundfile as sf

    from library.tools.analysis.sound_event_pipeline import measure_sound_events

    source_path = Path(audio_path)
    source = baseline or measure_sound_events(
        str(source_path), checkpoint_path=checkpoint_path
    )
    events = source["events"]
    result = {
        "query": query,
        "source": source,
        "separation_triggered": False,
        "separation_reason": None,
        "candidate_events": [],
    }
    if not should_separate_for_query(query, events):
        return result

    from library.tools.heavy_work_lock import owns
    if not owns("music.analyse:model_memory"):
        raise RuntimeError(
            "query-triggered sound separation requires heavy_work_lock "
            "profile music.analyse:model_memory"
        )

    from library.tools.analysis.source_separation import separate_waveform

    waveform, sample_rate = sf.read(str(source_path), dtype="float32",
                                    always_2d=True)
    channel_first = waveform.T
    stems = separate_waveform(
        channel_first, int(sample_rate),
        source_signal_id=f"sound-query:{source_path.stem}",
        separator=separator,
    )
    result["separation_triggered"] = True
    result["separation_reason"] = "low_confidence_or_speech_music_overlap"
    with tempfile.TemporaryDirectory(prefix="ren-sound-query-") as temp_dir:
        for kind, stem in stems.items():
            stem_path = Path(temp_dir) / f"{kind}.wav"
            sf.write(str(stem_path), stem.samples.T, stem.sample_rate,
                     subtype="FLOAT")
            measured = measure_sound_events(
                str(stem_path), checkpoint_path=checkpoint_path
            )
            result["candidate_events"].append({
                "signal_id": stem.id,
                "signal_kind": stem.kind,
                "signal_status": stem.status,
                "source_signal_id": stem.source_signal_id,
                "method": stem.method,
                "model": stem.model,
                "events": measured["events"],
                "timing_basis": "measured_on_estimated_stem",
            })
    return result
