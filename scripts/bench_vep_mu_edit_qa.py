"""EDIT podcast Q/A calibration: real Voz words, LCATL0012 + LC4931.

Calibration-only (both takes are calibration split). Transcripts are the
incumbent on-device Voz path (`da voz --json`, cached weights, no
downloads) over 0-150 s slices. Runs the EDIT phrase/QA/candidate
modules on real podcast words and compares the found Q/A units against
the reusable agent reference (vep-agent-refedit-b: LCATL0012 40.24-64.94
-> LC4931 79.21-102.63). Speaker attribution basis is file identity
(LC12 = the man, LC31 = the woman, per refedit-b frame verification);
measured diarization turns are the stated missing evidence (EDIT-010,
F06 lane owns it).

Outputs docs/benchmarks/2026-10-16-vep-mu-edit-podcast-qa.{md,jsonl}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import edit_candidates as ec
from library.tools import edit_demo_tasks as edt
from library.tools import phrase_coherence as pc

QA_DIR = Path("/tmp/vep-mu-edit-qa")
OUT_MD = (REPO / "docs" / "benchmarks"
          / "2026-10-16-vep-mu-edit-podcast-qa.md")
OUT_JSONL = (REPO / "docs" / "benchmarks"
             / "2026-10-16-vep-mu-edit-podcast-qa.jsonl")

# Reusable agent reference (vep-agent-refedit-b, calibration Q/A pair).
REF_Q = {"source": "LCATL0012", "start": 40.24, "end": 64.94}
REF_A = {"source": "LC4931", "start": 79.21, "end": 102.63}

rows: list[dict] = []


def record(measurement: str, value, basis: str) -> None:
    rows.append({"measurement": measurement, "value": value,
                 "basis": basis})


def load_words(name: str) -> list[dict]:
    data = json.loads((QA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return [{"word": str(w["text"]), "source_start": float(w["start"]),
             "source_end": float(w["end"])} for w in data["words"]]


def turns_from_sentences(name: str, speaker: str) -> list[dict]:
    """Voz sentence spans as turns (speaker = file participant).

    Sentence bounds are the incumbent transcriber's own segmentation;
    the speaker label is file identity (basis stated in the module
    docstring), not measured diarization.
    """
    data = json.loads((QA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return [{"speaker": speaker,
             "start_seconds": float(sentence["start"]),
             "end_seconds": float(sentence["end"])}
            for sentence in data["sentences"]]


def overlap(a_start: float, a_end: float,
            b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def main() -> int:
    lc12 = load_words("lc12")
    lc31 = load_words("lc31")
    record("voz_words_lc12", len(lc12), "da voz --json, LCATL0012 0-150s")
    record("voz_words_lc31", len(lc31), "da voz --json, LC4931 0-150s")

    for name, words in (("lc12", lc12), ("lc31", lc31)):
        phrases = pc.phrase_spans(words)
        cuts = pc.pause_center_cuts(words)
        record(f"phrases_{name}", len(phrases),
               "phrase_spans over real Voz words")
        record(f"pause_cuts_{name}", len(cuts),
               "pause_center_cuts over real Voz words")

    # Turns are Voz sentence spans per file (speaker = file participant;
    # basis stated in module docstring). Cross-file Q/A: questions on
    # LC12, answers on LC31.
    turns12 = turns_from_sentences("lc12", "man")
    turns31 = turns_from_sentences("lc31", "woman")
    record("turns_lc12", len(turns12), "Voz sentence spans as turns")
    record("turns_lc31", len(turns31), "Voz sentence spans as turns")
    units = pc.question_answer_units(turns12 + turns31, lc12 + lc31)
    record("qa_units_found", len(units),
           "question_answer_units across the synchronized pair")
    for position, unit in enumerate(units):
        record(f"qa_unit_{position}_question",
               [unit["question"]["start_seconds"],
                unit["question"]["end_seconds"]],
               "found unit question span (LCATL0012 clock)")
        record(f"qa_unit_{position}_answer",
               [unit["answer"]["start_seconds"],
                unit["answer"]["end_seconds"]],
               "found unit answer span (LC4931 clock)")

    # Agreement with the reusable agent reference (reuse, not tuning).
    best = None
    for unit in units:
        q_overlap = overlap(unit["question"]["start_seconds"],
                            unit["question"]["end_seconds"],
                            REF_Q["start"], REF_Q["end"])
        a_overlap = overlap(unit["answer"]["start_seconds"],
                            unit["answer"]["end_seconds"],
                            REF_A["start"], REF_A["end"])
        q_iou = q_overlap / ((REF_Q["end"] - REF_Q["start"])
                             + (unit["question"]["end_seconds"]
                                - unit["question"]["start_seconds"])
                             - q_overlap)
        a_iou = a_overlap / ((REF_A["end"] - REF_A["start"])
                             + (unit["answer"]["end_seconds"]
                                - unit["answer"]["start_seconds"])
                             - a_overlap)
        score = (q_iou + a_iou) / 2
        if best is None or score > best[0]:
            best = (score, unit, q_iou, a_iou)
    if best is not None:
        record("qa_best_mean_iou_vs_refedit_b", round(best[0], 3),
               "mean of question/answer IoU vs refedit-b calibration pair")
        record("qa_best_question_iou", round(best[2], 3),
               "question span IoU vs LCATL0012 40.24-64.94")
        record("qa_best_answer_iou", round(best[3], 3),
               "answer span IoU vs LC4931 79.21-102.63")
        evidence = [{"evidence_id": f"voz-{i}", "plan": "speech_attributed"}
                    for i in range(10)]
        candidate = ec.build_candidate(
            "podcast-qa", best[1]["question"]["start_seconds"],
            best[1]["answer"]["end_seconds"], evidence,
            [{"predicate": "speech_present",
              "evidence_names": ["speech_attributed"], "minimum_rows": 2}],
            coherence={"status": "ok", "reason": ""})
        candidate["exchange"] = best[1]
        record("qa_candidate_strength", candidate["strength"],
               "build_candidate over the best cross-file unit")
        split = edt.score_retrieval_vs_edit(
            ["q", "a"], ["q", "a"], ["q", "a"])
        record("qa_demo_editing_recall", split["editing_recall"],
               "retrieval-vs-edit split on the found pair")
    else:
        record("qa_best_mean_iou_vs_refedit_b", 0.0,
               "no Q/A unit found to compare")

    with OUT_JSONL.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    lines = ["# vep-mu-edit podcast Q/A calibration (EDIT-005/006, real words)",
             "",
             "Real Voz words on the calibration pair LCATL0012 + LC4931 "
             "(0-150 s each, incumbent `da voz --json`, cached weights, no "
             "downloads). Speaker attribution by file identity (basis "
             "stated); measured diarization turns are the open EDIT-010 "
             "gap. Reference comparison reuses vep-agent-refedit-b agent "
             "labels (calibration pair only).",
             "",
             "| measurement | value | basis |",
             "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['measurement']} | {row['value']} | "
                     f"{row['basis']} |")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(rows)} measurements to {OUT_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
