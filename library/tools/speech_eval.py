"""Score a Voz transcript and its MFA word placement against speech gold.

The evaluator is deliberately independent of both producers. Gold and
prediction are JSON documents with ``words`` rows containing ``text``,
``start``, ``end`` and ``speaker``. A prediction may instead mark
``status`` as ``abstained`` or ``failed``; these outcomes are counted
separately from recognition errors.
"""
from __future__ import annotations

import argparse
import json
import math
import string
import unicodedata
from collections import defaultdict
from pathlib import Path


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in text if ch not in string.punctuation).strip()


def _edit_alignment(reference: list[str], hypothesis: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Minimum edit path with deterministic tie breaking: match, sub, del, ins."""
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    back = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0], back[i][0] = i, "del"
    for j in range(1, cols):
        cost[0][j], back[0][j] = j, "ins"
    for i in range(1, rows):
        for j in range(1, cols):
            same = reference[i - 1] == hypothesis[j - 1]
            choices = [(cost[i - 1][j - 1] + (not same), 0 if same else 1,
                        "match" if same else "sub"),
                       (cost[i - 1][j] + 1, 2, "del"),
                       (cost[i][j - 1] + 1, 3, "ins")]
            value, _, op = min(choices)
            cost[i][j], back[i][j] = value, op
    result = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        op = back[i][j]
        if op in ("match", "sub"):
            result.append((op, i - 1, j - 1)); i -= 1; j -= 1
        elif op == "del":
            result.append((op, i - 1, None)); i -= 1
        else:
            result.append((op, None, j - 1)); j -= 1
    return list(reversed(result))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def score(reference: dict, hypothesis: dict, *, split: str, condition: str,
          audio_seconds: float, elapsed_seconds: float | None) -> dict:
    """Score one independently annotated speech sample."""
    if audio_seconds <= 0 or (elapsed_seconds is not None and elapsed_seconds < 0):
        raise ValueError("audio_seconds must be positive and elapsed_seconds non-negative")
    ref_words = reference["words"]
    hyp_words = hypothesis.get("words", [])
    ref_tokens = [normalize(row["text"]) for row in ref_words]
    hyp_tokens = [normalize(row["text"]) for row in hyp_words]
    ref_tokens = [word for word in ref_tokens if word]
    hyp_tokens = [word for word in hyp_tokens if word]
    alignment = _edit_alignment(ref_tokens, hyp_tokens)
    substitutions = sum(op == "sub" for op, _, _ in alignment)
    deletions = sum(op == "del" for op, _, _ in alignment)
    insertions = sum(op == "ins" for op, _, _ in alignment)
    denom = len(ref_tokens)
    wer = (substitutions + deletions + insertions) / denom if denom else None
    ref_chars = " ".join(ref_tokens)
    hyp_chars = " ".join(hyp_tokens)
    char_path = _edit_alignment(list(ref_chars), list(hyp_chars))
    char_errors = sum(op != "match" for op, _, _ in char_path)
    cer = char_errors / len(ref_chars) if ref_chars else None

    start_errors, end_errors, speaker_total, speaker_correct = [], [], 0, 0
    for op, ri, hi in alignment:
        if op != "match":
            continue
        ref, hyp = ref_words[ri], hyp_words[hi]
        if all(key in ref and key in hyp for key in ("start", "end")):
            start_errors.append(abs(float(hyp["start"]) - float(ref["start"])) * 1000)
            end_errors.append(abs(float(hyp["end"]) - float(ref["end"])) * 1000)
        if ref.get("speaker") is not None:
            speaker_total += 1
            speaker_correct += ref["speaker"] == hyp.get("speaker")

    state = hypothesis.get("status", "answered")
    return {
        "sample_id": reference["sample_id"], "split": split,
        "condition": condition, "status": state,
        "reference_words": denom, "hypothesis_words": len(hyp_tokens),
        "wer": wer, "cer": cer,
        "character_errors": char_errors,
        "reference_characters": len(ref_chars),
        "substitutions": substitutions, "deletions": deletions,
        "insertions": insertions,
        "boundary": {
            "status": "measured" if start_errors else "unmeasured",
            "matched_words": len(start_errors),
            "start_median_abs_ms": _percentile(start_errors, .5),
            "start_p95_abs_ms": _percentile(start_errors, .95),
            "end_median_abs_ms": _percentile(end_errors, .5),
            "end_p95_abs_ms": _percentile(end_errors, .95),
        },
        "speaker_attribution": {
            "scored_words": speaker_total,
            "correct_words": speaker_correct,
            "accuracy": speaker_correct / speaker_total if speaker_total else None,
        },
        "failure_abstention": {
            "failed": state == "failed", "abstained": state == "abstained",
            "reason": hypothesis.get("reason"),
        },
        "cost": {"audio_seconds": audio_seconds,
                 "elapsed_seconds": elapsed_seconds,
                 "seconds_per_audio_minute": (
                     elapsed_seconds / (audio_seconds / 60)
                     if elapsed_seconds is not None else None)},
    }


def score_batch(samples: list[dict], cost_measurements: list[dict] | None = None) -> dict:
    """Score a fixed set; aggregate quality and independent executor cost."""
    results = [score(row["reference"], row["hypothesis"],
                     split=row["split"], condition=row["condition"],
                     audio_seconds=row["audio_seconds"],
                     elapsed_seconds=row.get("elapsed_seconds"))
               for row in samples]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for result in results:
        groups[(result["split"], result["condition"])].append(result)
    summaries = []
    for (split, condition), rows in sorted(groups.items()):
        references = sum(row["reference_words"] for row in rows)
        errors = sum(row["substitutions"] + row["deletions"] + row["insertions"]
                     for row in rows)
        character_errors = sum(row["character_errors"] for row in rows)
        reference_characters = sum(row["reference_characters"] for row in rows)
        audio_seconds = sum(row["cost"]["audio_seconds"] for row in rows)
        measured_costs = [row["cost"]["elapsed_seconds"] for row in rows
                          if row["cost"]["elapsed_seconds"] is not None]
        elapsed_seconds = sum(measured_costs) if measured_costs else None
        summaries.append({
            "split": split, "condition": condition, "samples": len(rows),
            "reference_words": references,
            "word_errors": errors,
            "wer": errors / references if references else None,
            "character_errors": character_errors,
            "reference_characters": reference_characters,
            "cer": (character_errors / reference_characters
                    if reference_characters else None),
            "boundary_status": "unmeasured" if all(
                row["boundary"]["status"] == "unmeasured" for row in rows
            ) else "see per-sample rows",
            "speaker_scored_words": sum(
                row["speaker_attribution"]["scored_words"] for row in rows),
            "speaker_correct_words": sum(
                row["speaker_attribution"]["correct_words"] for row in rows),
            "failures": sum(row["failure_abstention"]["failed"] for row in rows),
            "abstentions": sum(row["failure_abstention"]["abstained"] for row in rows),
            "audio_seconds": audio_seconds,
            "elapsed_seconds": elapsed_seconds,
            "seconds_per_audio_minute": (elapsed_seconds / (audio_seconds / 60)
                                          if audio_seconds and elapsed_seconds is not None
                                          else None),
        })
    cost_groups: dict[str, list[dict]] = defaultdict(list)
    for measurement in cost_measurements or []:
        cost_groups[measurement["split"]].append(measurement)
    cost_by_split = []
    for split, measurements in sorted(cost_groups.items()):
        audio_seconds = sum(row["audio_seconds"] for row in measurements)
        elapsed_seconds = sum(row["elapsed_seconds"] for row in measurements)
        cost_by_split.append({
            "split": split, "runs": len(measurements),
            "audio_seconds": audio_seconds,
            "elapsed_seconds": elapsed_seconds,
            "seconds_per_audio_minute": (
                elapsed_seconds / (audio_seconds / 60) if audio_seconds else None),
            "measurements": measurements,
        })
    return {"summary_by_split_condition": summaries,
            "cost_by_split": cost_by_split, "samples": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, nargs="?")
    parser.add_argument("hypothesis", type=Path, nargs="?")
    parser.add_argument("--batch", type=Path,
                        help="JSON file with a samples array for grouped reports")
    parser.add_argument("--split", choices=("training", "calibration", "held_out"))
    parser.add_argument("--condition", choices=("clean", "accented", "overlapped", "noisy", "unknown"))
    parser.add_argument("--audio-seconds", type=float)
    parser.add_argument("--elapsed-seconds", type=float)
    args = parser.parse_args()
    if args.batch:
        document = json.loads(args.batch.read_text(encoding="utf-8"))
        samples = document["samples"] if isinstance(document, dict) else document
        costs = document.get("cost_measurements", []) if isinstance(document, dict) else []
        print(json.dumps(score_batch(samples, costs), indent=2, sort_keys=True))
        return 0
    if (not args.reference or not args.hypothesis or not args.split
            or not args.condition or args.audio_seconds is None):
        parser.error("provide reference and hypothesis with split, condition, "
                     "audio seconds, or provide --batch")
    result = score(json.loads(args.reference.read_text(encoding="utf-8")),
                   json.loads(args.hypothesis.read_text(encoding="utf-8")),
                   split=args.split, condition=args.condition,
                   audio_seconds=args.audio_seconds,
                   elapsed_seconds=args.elapsed_seconds)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
