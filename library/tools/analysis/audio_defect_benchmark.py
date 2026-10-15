"""Scoring and validation for held-out audio-defect benchmark runs.

Predictions and annotations use source-time half-open intervals. Detection
quality is scored independently from any correction recommendation so a
useful defect measurement cannot be hidden by a poor repair decision.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

DEFECTS = (
    "clipping", "noise", "loudness_mismatch", "dropout",
    "dialogue_interruption", "room_tone_discontinuity",
    "music_dialogue_masking",
)
SCHEMA = "audio-defect-benchmark/1"


def inject_defects(samples, sample_rate: int, recipes: list[dict]):
    """Apply recorded, deterministic defects and return PCM plus exact labels.

    Input is a held-out program excerpt selected and retained unchanged by
    the caller. The returned control is an independent copy. Recipes must
    state interval and strength explicitly; injected variants belong only
    to the synthetic held-out split.
    """
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim not in (1, 2) or not audio.size or sample_rate <= 0:
        raise ValueError("samples must be non-empty mono/stereo PCM with a positive sample rate")
    duration = len(audio) / sample_rate
    control = audio.copy()
    variants = []
    for recipe in recipes:
        start, end = _interval(recipe)
        if end > duration:
            raise ValueError("injected event ends after program duration")
        defect = recipe["defect"]
        if defect not in DEFECTS:
            raise ValueError(f"unknown injected defect {defect!r}")
        strength = recipe.get("strength")
        if not isinstance(strength, (int, float)) or not math.isfinite(strength):
            raise ValueError("each injection needs a finite numeric strength")
        lo, hi = int(start * sample_rate), int(end * sample_rate)
        piece = audio.copy()
        if defect == "clipping":
            if strength <= 0:
                raise ValueError("clipping strength is positive drive in dB")
            driven = piece[lo:hi] * (10.0 ** (float(strength) / 20.0))
            piece[lo:hi] = np.clip(driven, -1.0, 1.0)
        elif defect == "noise":
            if recipe.get("noise_kind", "broadband") == "hum":
                times = np.arange(hi - lo, dtype=np.float64) / sample_rate
                hum = float(strength) * np.sin(
                    2.0 * math.pi * float(recipe.get("frequency_hz", 60.0)) * times)
                if audio.ndim == 2:
                    hum = hum[:, None]
                piece[lo:hi] += hum.astype(np.float32)
            else:
                rng = np.random.default_rng(int(recipe.get("seed", 0)))
                noise = rng.normal(0.0, float(strength), piece[lo:hi].shape)
                piece[lo:hi] += noise.astype(np.float32)
        elif defect == "dropout" or defect == "dialogue_interruption":
            piece[lo:hi] = 0.0
        elif defect == "loudness_mismatch":
            piece[lo:hi] *= 10.0 ** (float(strength) / 20.0)
        elif defect == "room_tone_discontinuity":
            donor = np.asarray(recipe.get("donor_samples"), dtype=np.float32)
            donor_channels = donor.shape[1:] if donor.ndim > 1 else ()
            audio_channels = audio.shape[1:] if audio.ndim > 1 else ()
            if donor_channels != audio_channels:
                if audio.ndim == 2 and donor.ndim == 1:
                    donor = np.repeat(donor[:, None], audio.shape[1], axis=1)
                else:
                    raise ValueError("room-tone donor must match the source channel layout")
            if len(donor) < hi - lo:
                repeats = math.ceil((hi - lo) / len(donor))
                donor = np.tile(donor, (repeats,) + (1,) * (audio.ndim - 1))
            piece[lo:hi] = donor[:hi - lo] * (10.0 ** (float(strength) / 20.0))
        elif defect == "music_dialogue_masking":
            bed = np.asarray(recipe.get("bed_samples"), dtype=np.float32)
            if len(bed) < hi - lo:
                raise ValueError("masking bed must cover the injected interval")
            if bed.ndim == 1 and audio.ndim == 2:
                bed = np.repeat(bed[:, None], audio.shape[1], axis=1)
            piece[lo:hi] += bed[:hi - lo] * float(strength)
        variants.append({
            "samples": piece,
            "annotation": {"defect": defect, "start_seconds": start,
                           "end_seconds": end,
                           **({"noise_kind": recipe.get("noise_kind", "broadband")}
                              if defect == "noise" else {})},
            "recipe": {key: value for key, value in recipe.items()
                       if key not in ("donor_samples", "bed_samples")},
        })
    return {"control": control, "variants": variants,
            "duration_seconds": duration, "sample_rate": sample_rate}


def write_injected_set(base_wav: str | Path, recipes: list[dict],
                       output_dir: str | Path) -> dict:
    """Read one PCM WAV excerpt and write isolated control/defect WAVs."""
    source = Path(base_wav)
    with wave.open(str(source), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise ValueError("base WAV must be signed 16-bit PCM")
        channels, rate, frames = (reader.getnchannels(), reader.getframerate(),
                                  reader.getnframes())
        raw = reader.readframes(frames)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    samples = data.reshape(-1, channels) if channels > 1 else data
    injected = inject_defects(samples, rate, recipes)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    def write(path: Path, values):
        pcm = np.rint(np.clip(values, -1.0, 1.0) * 32768.0)
        pcm = np.clip(pcm, -32768, 32767).astype("<i2")
        payload = pcm.tobytes()
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(payload)

    control_path = destination / "control.wav"
    write(control_path, injected["control"])
    rows = []
    for index, variant in enumerate(injected["variants"], start=1):
        path = destination / f"variant-{index:02d}-{variant['annotation']['defect']}.wav"
        write(path, variant["samples"])
        rows.append({"audio_path": str(path), "annotations": [variant["annotation"]],
                     "recipe": variant["recipe"]})
    return {"control_audio_path": str(control_path), "sample_rate": rate,
            "duration_seconds": injected["duration_seconds"], "variants": rows}


def manifest_from_editorial_corpus(corpus: dict, source_ids: list[str],
                                   variants_by_source: dict[str, list[dict]]) -> dict:
    """Derive the benchmark split and provenance from the EVAL-01 manifest."""
    indexed = {source["source_id"]: source for source in corpus["sources"]}
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("source_ids must be unique")
    selected = []
    for source_id in source_ids:
        source = indexed[source_id]
        rights = source["rights"]
        if rights["status"] != "cleared" or "benchmarking" not in rights["permitted_uses"]:
            raise ValueError(f"source {source_id} is not cleared for benchmarking")
        split = source["split"]
        variants = variants_by_source.get(source_id, [])
        if split == "held_out" and not variants:
            raise ValueError(f"held-out source {source_id} needs synthetic variants")
        if split != "held_out" and variants:
            raise ValueError("synthetic injected material is held-out only")
        selected.append({
            "source_id": source_id,
            "path": source["path"],
            "split": split,
            "leakage_group_id": source["leakage_group_id"],
            "sha256": source["sha256"],
            "rights_status": rights["status"],
            "labels_access": "sealed_custodian_only" if split == "held_out" else "not_applicable",
            "synthetic_labels": "known_by_construction" if split == "held_out" else "none",
            "variants": variants,
        })
    return {"schema": SCHEMA, "source_separation": "excluded",
            "corpus_id": corpus["corpus_id"],
            "annotation_schema": "audio-defect-injection/1",
            "sources": selected}


def predictions_from_audio_quality(measurement: dict, *, speech_spans=()) -> list[dict]:
    """Independent baseline adapter over FI-12's measured channel intervals.

    It exposes only events the current measurement can support. The music
    masking class intentionally has no prediction route in this adapter.
    Thresholds are fixed, documented measurement heuristics, not tuned on
    the held-out variants.
    """
    channels = measurement["channels"]
    if not channels:
        return []
    channel = channels[0]
    predictions = []
    for row in channel["intervals"]:
        start, end = float(row["start_seconds"]), float(row["end_seconds"])
        if row["sample_peak_dbfs"] is not None and row["sample_peak_dbfs"] >= -0.01:
            predictions.append({"defect": "clipping", "start_seconds": start,
                                "end_seconds": end, "method": "FI-12 peak"})
        rms = row["rms_dbfs"]
        flatness = row["spectral_flatness"]
        if rms is not None and flatness is not None and rms > -50 and flatness >= 0.5:
            predictions.append({"defect": "noise", "start_seconds": start,
                                "end_seconds": end, "method": "FI-12 flatness"})
    for earlier, later in zip(channel["intervals"], channel["intervals"][1:]):
        before, after = earlier["rms_dbfs"], later["rms_dbfs"]
        if before is not None and after is not None and abs(after - before) >= 8:
            predictions.append({"defect": "loudness_mismatch",
                                "start_seconds": float(later["start_seconds"]),
                                "end_seconds": float(later["end_seconds"]),
                                "method": "FI-12 adjacent RMS delta"})
        a, b = earlier["spectral_centroid_hz"], later["spectral_centroid_hz"]
        if (before is not None and after is not None and before < -25 and after < -25
                and a is not None and b is not None and abs(a - b) >= 1500):
            predictions.append({"defect": "room_tone_discontinuity",
                                "start_seconds": float(later["start_seconds"]),
                                "end_seconds": float(later["end_seconds"]),
                                "method": "FI-12 quiet-band centroid jump"})
    for span in channel["silence_intervals"]:
        start, end = float(span["start_seconds"]), float(span["end_seconds"])
        if end - start < 0.1:
            continue
        kind = "dialogue_interruption" if any(
            start < float(speech_end) and end > float(speech_start)
            for speech_start, speech_end in speech_spans) else "dropout"
        predictions.append({"defect": kind, "start_seconds": start,
                            "end_seconds": end, "method": "FI-12 silence"})
    return predictions


def execute_audio_quality_baseline(audio_path: str, annotations: list[dict],
                                   program_seconds: float, *, speech_spans=()) -> dict:
    """Run the current FI-12 executor, score its event adapter, and time it.

    This performs a full audio decode and must be called under the repository
    heavy-work lock. The memory root is temporary so the benchmark leaves no
    source memory or project state behind.
    """
    from library.tools.audio_quality import measure_audio_quality

    with tempfile.TemporaryDirectory(prefix="audio-defect-benchmark-") as root:
        started = time.perf_counter()
        measurement = measure_audio_quality(audio_path, root=Path(root), force=True)
        elapsed = time.perf_counter() - started
    predictions = predictions_from_audio_quality(measurement, speech_spans=speech_spans)
    result = score_events(annotations, predictions, program_seconds,
                          runtime_seconds=elapsed)
    result["executor"] = {
        "id": "fi12-audio-quality-event-adapter/1",
        "measurement_method": measurement["method"],
        "audio_quality": "library.tools.audio_quality.measure_audio_quality",
        "predicted_events": predictions,
    }
    return result


def _interval(row: dict) -> tuple[float, float]:
    try:
        start, end = float(row["start_seconds"]), float(row["end_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("each defect event needs numeric start_seconds and end_seconds") from exc
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError(f"invalid half-open event interval [{start}, {end})")
    return start, end


def _validate_events(rows, duration: float, *, label: str) -> list[dict]:
    if not isinstance(rows, list):
        raise TypeError(f"{label} must be a list")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("defect") not in DEFECTS:
            raise ValueError(f"{label} contains an unknown defect event")
        start, end = _interval(row)
        if end > duration:
            raise ValueError(f"{label} event ends after program duration")
        result.append({**row, "start_seconds": start, "end_seconds": end})
    return result


def validate_manifest(manifest: dict) -> None:
    """Refuse split leakage, unapproved sources, and unsealed held-out labels."""
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"manifest schema must be {SCHEMA}")
    if manifest.get("source_separation") not in ("excluded", "not_used"):
        raise ValueError("source separation must remain explicitly excluded")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("manifest needs sources")
    ids, groups, digests, present_splits = set(), {}, set(), set()
    for source in sources:
        source_id = source.get("source_id")
        if not source_id or source_id in ids:
            raise ValueError("source_id values must be present and unique")
        ids.add(source_id)
        if source.get("rights_status") != "cleared":
            raise ValueError(f"source {source_id} is not rights-cleared")
        split = source.get("split")
        if split not in ("training", "calibration", "held_out"):
            raise ValueError(f"source {source_id} has an invalid split")
        present_splits.add(split)
        group = source.get("leakage_group_id")
        if not group:
            raise ValueError(f"source {source_id} needs a leakage_group_id")
        if group in groups and groups[group] != split:
            raise ValueError("a leakage group may not cross benchmark splits")
        groups[group] = split
        digest = source.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"source {source_id} needs a full SHA-256")
        if digest in digests:
            raise ValueError("source content may not cross benchmark splits")
        digests.add(digest)
        if split == "held_out" and source.get("labels_access") != "sealed_custodian_only":
            raise ValueError("held-out annotations must remain sealed from tuning")
        variants = source.get("variants", [])
        if split != "held_out" and variants:
            raise ValueError("synthetic injected material is held-out only")
        if split == "held_out" and not variants:
            raise ValueError(f"held-out source {source_id} needs injected variants")
        for variant in variants:
            duration = float(variant["duration_seconds"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("variant duration must be positive and finite")
            _validate_events(variant.get("annotations"), duration, label="annotations")
    if present_splits != {"training", "calibration", "held_out"}:
        raise ValueError("manifest needs disjoint training, calibration, and held-out splits")


def _match_events(gold: list[dict], predicted: list[dict], tolerance: float):
    """Greedy one-to-one same-class match maximizing temporal IoU."""
    candidates = []
    for gi, truth in enumerate(gold):
        gs, ge = _interval(truth)
        for pi, guess in enumerate(predicted):
            if truth["defect"] != guess["defect"]:
                continue
            ps, pe = _interval(guess)
            intersection = max(0.0, min(ge, pe) - max(gs, ps))
            union = max(ge, pe) - min(gs, ps)
            iou = intersection / union if union else 0.0
            boundary = (abs(gs - ps) + abs(ge - pe)) / 2.0
            if iou > 0 and boundary <= tolerance:
                candidates.append((iou, -boundary, gi, pi, boundary,
                                   abs((ge - gs) - (pe - ps))))
    candidates.sort(reverse=True)
    used_gold, used_pred, matches = set(), set(), []
    for _, _, gi, pi, boundary, duration_error in candidates:
        if gi in used_gold or pi in used_pred:
            continue
        used_gold.add(gi)
        used_pred.add(pi)
        matches.append((gi, pi, boundary, duration_error))
    return used_gold, used_pred, matches


def score_events(annotations: list[dict], predictions: list[dict],
                 program_seconds: float, *, boundary_tolerance_seconds=2.0,
                 runtime_seconds: float | None = None,
                 correction_suggestions: list[dict] | None = None) -> dict:
    """Score event precision/recall, duration/boundary errors and false alarms/hour."""
    if not math.isfinite(program_seconds) or program_seconds <= 0:
        raise ValueError("program_seconds must be positive and finite")
    if boundary_tolerance_seconds < 0:
        raise ValueError("boundary tolerance must be non-negative")
    if runtime_seconds is not None and (
            not math.isfinite(runtime_seconds) or runtime_seconds < 0):
        raise ValueError("runtime_seconds must be non-negative and finite")
    gold = _validate_events(annotations, program_seconds, label="annotations")
    pred = _validate_events(predictions, program_seconds, label="predictions")
    result = {"program_seconds": program_seconds, "boundary_tolerance_seconds": boundary_tolerance_seconds,
              "by_defect": {}, "overall": {}}
    result["cost"] = {
        "runtime_seconds": runtime_seconds,
        "program_minutes": program_seconds / 60.0,
        "runtime_seconds_per_program_minute": (
            runtime_seconds / (program_seconds / 60.0)
            if runtime_seconds is not None else None),
    }
    totals = defaultdict(int)
    boundary_errors, duration_errors = [], []
    for defect in DEFECTS:
        g = [row for row in gold if row["defect"] == defect]
        p = [row for row in pred if row["defect"] == defect]
        _, _, matches = _match_events(g, p, boundary_tolerance_seconds)
        tp, fp, fn = len(matches), len(p) - len(matches), len(g) - len(matches)
        precision = tp / (tp + fp) if tp + fp else (1.0 if not fn else 0.0)
        recall = tp / (tp + fn) if tp + fn else 1.0
        for _, _, boundary, duration in matches:
            boundary_errors.append(boundary)
            duration_errors.append(duration)
        result["by_defect"][defect] = {
            "gold_events": len(g), "predicted_events": len(p), "true_positive": tp,
            "false_positive": fp, "false_negative": fn, "precision": precision,
            "recall": recall, "f1": (2 * precision * recall / (precision + recall)
                                          if precision + recall else 0.0),
            "mean_boundary_error_seconds": (sum(x[2] for x in matches) / tp if tp else None),
            "mean_duration_error_seconds": (sum(x[3] for x in matches) / tp if tp else None),
            "false_alarms_per_program_hour": fp * 3600.0 / program_seconds,
        }
        for key, value in (("tp", tp), ("fp", fp), ("fn", fn)):
            totals[key] += value
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if tp + fp else (1.0 if not fn else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    result["overall"] = {
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "mean_boundary_error_seconds": sum(boundary_errors) / len(boundary_errors) if boundary_errors else None,
        "mean_duration_error_seconds": sum(duration_errors) / len(duration_errors) if duration_errors else None,
        "false_alarms_per_program_hour": fp * 3600.0 / program_seconds,
    }
    if correction_suggestions is not None:
        result["correction_decision"] = {
            "suggestions": len(correction_suggestions),
            "separate_from_detection_score": True,
            "note": "Correction quality requires its own human-reviewed decision labels.",
        }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path,
                        help="JSON with annotations, predictions/audio and duration")
    args = parser.parse_args(argv)
    document = json.loads(args.input.read_text(encoding="utf-8"))
    if document.get("audio_path"):
        result = execute_audio_quality_baseline(
            document["audio_path"], document["annotations"],
            float(document["program_seconds"]),
            speech_spans=document.get("speech_spans", ()))
    else:
        result = score_events(
            document["annotations"], document["predictions"],
            float(document["program_seconds"]),
            boundary_tolerance_seconds=float(
                document.get("boundary_tolerance_seconds", 2.0)),
            runtime_seconds=float(document["runtime_seconds"]),
            correction_suggestions=document.get("correction_suggestions"))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
