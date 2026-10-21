"""VI-004: how well do the search lanes' floors ABSTAIN, and what do they
cost when they do?

The retrieval floors are the lanes' only defence against answering a
question the footage cannot answer.  A floor that is too low hands the
editor a confident wrong window; a floor that is too high turns a
moment that IS in the footage into silence.  The punch list asks for
both numbers, with denominators, before and after any change:

    "Recalibrate on a larger, independently labelled negative corpus.
     Separate not observed, insufficient evidence, and confirmed absent
     from the measured scope.  Report near misses without presenting
     them as confirmed results.  Check floor changes against both
     missed-present events and false positives."

**The negative corpus is labelled by looking, and this script REFUSES to
score a negative whose subject words occur in the measured transcript.**
A negative is only a negative if the thing is really not there, and the
only way to know that is to read the footage - not to ask a search lane,
because the search lane's answer is the thing being measured.

**Nothing here changes the search.**  It measures the shipped lanes on
a Ren-owned scratch copy and reports the numbers; the floors live in
`library.tools.analysis.footage_query` and
`library.tools.analysis.footage_frames`, and the report says which
value each one would have to take.

**The floor is fitted on the TUNE half and reported on the HELD-OUT
half**, and it is fitted with the engine's own method
(`library.tools.retrieval.calibration.abstention_threshold` - balanced
accuracy over labelled samples), so the number that lands is one the
engine could have computed itself.

    bin/vep docs/benchmarks/editorial_abstention_bench.py \\
        --project <scratch project> \\
        --negatives library/tools/editorial_abstention_negatives.json \\
        --out-json abstention.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.analysis import footage_query
from library.tools.analysis.footage_frames import (
    MEASURED_QUERY_FLOOR,
    FrameIndex,
)
from library.tools.retrieval import abstention, calibration

#: The floors this bench sweeps, per lane.  The shipped values are in
#: the sweep; the rest bracket them.
TEXT_FLOORS = (0.20, 0.24, 0.26, 0.28, 0.30, 0.32, 0.34, 0.36, 0.38, 0.40,
               0.42, 0.44, 0.46, 0.48, 0.50)
VISUAL_FLOORS = (0.10, 0.12, 0.13, 0.14, 0.15, 0.16, 0.1652, 0.17, 0.18,
                 0.19, 0.20, 0.22, 0.24, 0.30)

_WORD = re.compile(r"[a-z']+")


def _asset_map(*indexes) -> dict:
    """asset name (no extension) -> the clip id the index uses."""
    out: dict = {}
    for index in indexes:
        for clip_id, filename in index:
            out.setdefault(filename.rsplit(".", 1)[0], clip_id)
    return out


def _transcript_words(index) -> set:
    words = set()
    for segment in index.segments:
        if segment.get("kind") != "speech":
            continue
        for word in _WORD.findall((segment.get("text") or "").lower()):
            words.add(word)
    return words


def _text_profile(index, query: str) -> list:
    """Every segment's dense score for one query, with its span.

    The floor is read on the RAW dense cosine, so the profile is the raw
    cosine - the same number the lane's own floor compares.
    """
    report = index.search_report(query, top_k=len(index.segments), floor=0.0)
    if report.get("error"):
        return []
    return [{"clip_id": row.get("clip_id"),
             "filename": row.get("filename"),
             "start": row.get("start"),
             "end": row.get("end"),
             "score": row.get("dense_score")}
            for row in report.get("results") or ()]


def _visual_profile(index, query: str) -> list:
    """Every sampled frame's CLIP score for one query."""
    report = index.search_frames(query, top_k=len(index.frames))
    if report.get("refused") or report.get("error"):
        return []
    return [{"clip_id": hit.get("clip_id"),
             "filename": hit.get("filename"),
             "start": hit.get("t"),
             "end": hit.get("t"),
             "score": hit.get("score")}
            for hit in report.get("results") or ()]


def _overlaps(row, instance, tolerance) -> bool:
    start = row.get("start")
    end = row.get("end")
    if start is None or end is None:
        return False
    if end < start:
        start, end = end, start
    return (start <= instance["end"] + tolerance
            and end >= instance["start"] - tolerance)


def _best_overlapping(profile, moment, assets, tolerance) -> float | None:
    """The best score of a row that covers a wanted moment.

    `None` when no row covers the moment at all: the lane never had a
    candidate there, which is a different defect from the floor hiding
    one.
    """
    best = None
    for instance in moment.get("instances") or ():
        clip_id = assets.get(instance.get("asset"))
        if clip_id is None:
            continue
        for row in profile:
            if row.get("clip_id") != clip_id:
                continue
            if not _overlaps(row, instance, tolerance):
                continue
            score = row.get("score")
            if score is None:
                continue
            best = score if best is None else max(best, score)
    return best


def _sweep(floor: float, negatives, positives, *, gates_moments: bool
           ) -> dict:
    """The two numbers the punch list names, at one floor.

    A false positive is a NEGATIVE query the lane answers: the subject
    is not in the footage and the lane hands the editor rows anyway.

    A missed-present QUERY is a positive query the lane refuses: the
    moment IS in the footage and the floor turned the whole answer
    away.  For the text lane the floor gates each SEGMENT, so a moment
    can also be missed on its own while the query still answers; that
    is `missed_present_moments`, and it is only counted for a lane
    whose floor gates rows.  The frame lane's floor is a QUERY floor -
    it decides whether the lane answers at all - and its per-window
    floor ships off, so a moment is not gated by this number.
    """
    false_positives = {"tune": 0, "heldout": 0}
    false_positive_ids = []
    near_misses = {"tune": 0, "heldout": 0}
    near_miss_ids = []
    for entry in negatives:
        score = entry.get("best_score")
        if score is None:
            continue
        split = entry["split"]
        if score >= floor:
            false_positives[split] += 1
            false_positive_ids.append(entry["id"])
        elif entry.get("weak_floor") is not None and score >= entry["weak_floor"]:
            near_misses[split] += 1
            near_miss_ids.append(entry["id"])
    missed_queries = {"tune": 0, "heldout": 0}
    missed_query_ids = []
    missed_moments = 0
    covered_moments = 0
    uncovered_moments = 0
    for entry in positives:
        if entry.get("best_score") is not None and entry["best_score"] < floor:
            missed_queries[entry["split"]] += 1
            missed_query_ids.append(entry["id"])
        if not gates_moments:
            continue
        for moment in entry.get("moments") or ():
            best = moment.get("best_overlapping_score")
            if best is None:
                uncovered_moments += 1
                continue
            covered_moments += 1
            if best < floor:
                missed_moments += 1
    return {
        "floor": floor,
        "false_positives": false_positives,
        "false_positive_ids": false_positive_ids,
        "near_misses": near_misses,
        "near_miss_ids": near_miss_ids,
        "missed_present_queries": missed_queries,
        "missed_present_query_ids": missed_query_ids,
        "missed_present_moments": missed_moments,
        "present_moments_covered": covered_moments,
        "present_moments_never_covered": uncovered_moments,
    }


def _fit(negatives, positives, split: str | None = "tune") -> dict:
    """The floor the engine's own method would choose.

    The method is `library.tools.retrieval.calibration`'s: the cutoff
    that maximises BALANCED ACCURACY over labelled samples - the rate
    at which a right answer is kept and the rate at which a wrong one
    is refused.  A sample is a QUERY here, labelled present or absent,
    and the score is the lane's best score over its whole measured
    scope, which is the number the query floor judges.

    `split="tune"` fits on the half a floor may be fitted on; `None`
    fits on the whole corpus, which is the fit the shipped floor is
    taken from and the one the held-out half then judges.
    """
    samples = []
    for entry in positives:
        if split is not None and entry["split"] != split:
            continue
        samples.append({"score": entry.get("best_score"),
                        "hit": True, "signal": "frame_cosine",
                        "id": entry["id"]})
    for entry in negatives:
        if split is not None and entry["split"] != split:
            continue
        samples.append({"score": entry.get("best_score"),
                        "hit": False, "signal": "frame_cosine",
                        "id": entry["id"]})
    samples = [sample for sample in samples if sample["score"] is not None]
    threshold = calibration.abstention_threshold(samples)
    return {
        "method": ("balanced accuracy over labelled queries, fitted on the "
                   + ("TUNE half of the negative corpus and the positive "
                      "queries" if split else
                      "WHOLE negative corpus and the positive queries")),
        "cutoff": threshold.get("cutoff"),
        "balanced_accuracy": threshold.get("balanced_accuracy"),
        "present_retained_rate": threshold.get("present_retained_rate"),
        "absent_abstained_rate": threshold.get("absent_abstained_rate"),
        "samples": threshold.get("samples"),
        "positives": threshold.get("positives"),
        "negatives": threshold.get("negatives"),
        "status": threshold.get("status"),
    }


def run(project: str, negatives_path: str, positives_path: str,
        out_json: str | None = None) -> dict:
    corpus = json.loads(Path(negatives_path).read_text())
    positives_set = json.loads(Path(positives_path).read_text())
    tolerance = float(
        positives_set.get("scoring", {}).get("editorial", {})
        .get("overlap_tolerance_seconds", 1.0))

    index = footage_query.FootageIndex(project)
    frame_index = FrameIndex(project)
    assets = _asset_map(
        [(segment.get("clip_id"), segment.get("filename"))
         for segment in index.segments],
        [(frame.get("clip_id"), frame.get("filename"))
         for frame in frame_index.frames],
    )
    spoken = _transcript_words(index)

    negatives = []
    refused = abstention.unverified_negatives(corpus, spoken)
    unverified = {entry["id"] for entry in refused}
    for query in corpus["queries"]:
        lane = query["lane"]
        if lane == "text":
            if query["id"] in unverified:
                continue
            profile = _text_profile(index, query["query"])
            weak = footage_query.DENSE_WEAK_FLOOR
        else:
            profile = _visual_profile(frame_index, query["query"])
            weak = None
        if not profile:
            refused.append({"id": query["id"],
                            "reason": "the lane could not answer this query"})
            continue
        best = max((row.get("score") for row in profile
                     if row.get("score") is not None), default=None)
        negatives.append({
            "id": query["id"], "query": query["query"], "lane": lane,
            "split": query["split"], "category": query.get("category"),
            "best_score": best, "weak_floor": weak,
            "rows_scored": len(profile),
        })

    positives = []
    for query in positives_set.get("queries") or ():
        if query.get("expect") != "present":
            continue
        lane = query.get("lane")
        if lane == "text":
            profile = _text_profile(index, query["query"])
        else:
            profile = _visual_profile(frame_index, query["query"])
        moments = []
        for moment in query.get("moments") or ():
            best = _best_overlapping(profile, moment, assets, tolerance)
            moments.append({**moment, "best_overlapping_score": best})
        best = max((row.get("score") for row in profile
                     if row.get("score") is not None), default=None)
        positives.append({
            "id": query["id"], "query": query["query"], "lane": lane,
            "split": "tune", "moments": moments, "best_score": best,
        })

    text_neg = [entry for entry in negatives if entry["lane"] == "text"]
    visual_neg = [entry for entry in negatives if entry["lane"] == "visual"]
    text_pos = [entry for entry in positives if entry["lane"] == "text"]
    visual_pos = [entry for entry in positives if entry["lane"] == "visual"]

    report = {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": os.path.basename(os.path.abspath(project)),
        "negatives_corpus": corpus["name"],
        "positives_set": positives_set.get("name"),
        "overlap_tolerance_seconds": tolerance,
        "scope": corpus.get("scope"),
        "refused_negatives": refused,
        "negatives": negatives,
        "positives": positives,
        "shipped_floors": {
            "text_confident": footage_query.DENSE_SCORE_FLOOR,
            "text_weak": footage_query.DENSE_WEAK_FLOOR,
            "visual_query": MEASURED_QUERY_FLOOR,
        },
        "denominators": {
            "negatives": {
                "text": {"tune": sum(1 for e in text_neg
                                     if e["split"] == "tune"),
                         "heldout": sum(1 for e in text_neg
                                        if e["split"] == "heldout")},
                "visual": {"tune": sum(1 for e in visual_neg
                                       if e["split"] == "tune"),
                           "heldout": sum(1 for e in visual_neg
                                          if e["split"] == "heldout")},
            },
            "positives": {
                "text": {"queries": len(text_pos),
                         "moments": sum(len(e["moments"]) for e in text_pos)},
                "visual": {"queries": len(visual_pos),
                           "moments": sum(len(e["moments"])
                                          for e in visual_pos)},
            },
        },
        "sweeps": {
            "text": [_sweep(floor, text_neg, text_pos, gates_moments=True)
                     for floor in TEXT_FLOORS],
            "visual": [_sweep(floor, visual_neg, visual_pos,
                              gates_moments=False)
                       for floor in VISUAL_FLOORS],
        },
        "fit": {
            "text": _fit(text_neg, text_pos),
            "visual": _fit(visual_neg, visual_pos),
        },
        "fit_whole_corpus": {
            "text": _fit(text_neg, text_pos, split=None),
            "visual": _fit(visual_neg, visual_pos, split=None),
        },
    }
    if out_json:
        Path(out_json).write_text(json.dumps(report, indent=1))
    return report


def _print_table(report: dict) -> None:
    denominators = report["denominators"]
    for lane, sweeps in report["sweeps"].items():
        negatives = denominators["negatives"][lane]
        positives = denominators["positives"][lane]
        print(f"\n=== {lane} lane "
              f"(negatives: {negatives['tune']} tune + "
              f"{negatives['heldout']} heldout; "
              f"positives: {positives['queries']} queries / "
              f"{positives['moments']} moments) ===")
        print(f"{'floor':>6} {'FP tune':>8} {'FP held':>8} {'near':>5} "
              f"{'missed q':>9} {'missed m':>9} {'covered':>8}")
        for sweep in sweeps:
            fp = sweep["false_positives"]
            near = sweep["near_misses"]
            print(f"{sweep['floor']:6.4f} {fp['tune']:8d} {fp['heldout']:8d} "
                  f"{near['tune'] + near['heldout']:5d} "
                  f"{sweep['missed_present_queries']['tune']:9d} "
                  f"{sweep['missed_present_moments']:9d} "
                  f"{sweep['present_moments_covered']:8d}")
        print("fit (tune half):", json.dumps(report["fit"][lane]))
        print("fit (whole corpus):", json.dumps(report["fit_whole_corpus"][lane]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--negatives", default=str(
        REPO_ROOT / "library/tools/editorial_abstention_negatives.json"))
    parser.add_argument("--positives", default=str(
        REPO_ROOT / "library/tools/editorial_retrieval_set.json"))
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args(argv)
    report = run(args.project, args.negatives, args.positives, args.out_json)
    _print_table(report)
    for entry in report["refused_negatives"]:
        print(f"[refused] {entry['id']}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
