"""`ren eval-search`: score footage search against a pre-registered query set.

The separable-product report (item 4) asked for a held-out retrieval
evaluation: hand-marked spans, recall and precision AT TIME RANGES,
abstention, coverage, and processing time per video minute. This is the
harness; the set is data (`retrieval_eval_set.json` beside this file,
committed before any of its queries were scored - the commit is the
pre-registration).

    ren eval-search <project-or-collection> [--set FILE] [--families ...]
        [--timing-record analysis_run.json ...] [--out-json F] [--out-md F]

It asks the SHIPPED search paths, never a copy of them:

* speech   - `FootageIndex.search_report` (the text index `ren search`
              reads), top-k ranges, floor and abstain as shipped;
* visual   - `FrameIndex.search_ranges` (`ren search --visual`), which has
              no floor and refuses action queries by design;
* person   - `event_spans.query` (`ren search --person --predicate`);
* verified - `event_spans.verified_query` (`--verify`), whose new VLM
              verdicts take the heavy-work lock themselves;
* editorial - real editor searches, over the same lanes, scored against
              ground-truth INTERVALS.  An editorial query names a moment
              by what it is FOR and what it contains, so the set
              (`editorial_retrieval_set.json`) asks the way an editor
              asks rather than the way a caption is written.  Its
              scoring also prices the FALSE POSITIVES - the windows the
              search returned that hold nothing the editor asked for - in
              the edit they would cause (`library.tools.retrieval.
              consequence`), and measures the requirement decomposition
              against the requirement the set declares.

A person in the set is named by sight (`identified_by_asset`: the camera
that frames them), and resolved to the measured roster as the person whose
face tracks cover that asset - so the harness needs no name declaration in
the project and tests the cross-source face resolution as it runs.

Coverage is read off the path-portable export (`memory_export.build_export`)
and processing time off `analysis_run.json` records - this project's, plus
any `--timing-record` from a fresh run. The harness reports; it gates
nothing and is not part of the test suite.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from library.tools import footage_analysis
from library.tools import report_provenance
from library.tools.retrieval import regions as regions_mod
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

DEFAULT_SET = Path(__file__).with_name("retrieval_eval_set.json")
FAMILIES = ("speech", "visual", "person", "verified_predicate", "editorial")

EDITORIAL_RULES = {
    "top_k": 5,
    "overlap_tolerance_seconds": 1.0,
    "selection_k": 1,
}
"""The rules an editorial query is scored under when the set does not
carry its own.  The committed editorial set declares them; this is the
fallback for a set that does not."""

Interval = Tuple[float, float]


# ── Intervals ───────────────────────────────────────────────────────


def merge(spans: Iterable[Interval]) -> List[Interval]:
    out: List[list] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if b > a):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def pad(spans: Iterable[Interval], by: float) -> List[Interval]:
    return merge((a - by, b + by) for a, b in spans)


def intersect(xs: Sequence[Interval], ys: Sequence[Interval]) -> List[Interval]:
    xs, ys = merge(xs), merge(ys)
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if b > a:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def length(spans: Iterable[Interval]) -> float:
    return sum(b - a for a, b in merge(spans))


def overlaps(a: Interval, b: Interval) -> bool:
    return min(a[1], b[1]) > max(a[0], b[0]) or (
        a[0] == a[1] and b[0] <= a[0] <= b[1])


def _ratio(num: float, den: float) -> Optional[float]:
    return round(num / den, 4) if den else None


# ── The corpus, as this project sees it ────────────────────────────


class Corpus:
    """Asset names in the set <-> clip ids and paths in the project."""

    def __init__(self, project: str, eval_set: dict):
        self.project = project
        self.digest_of = {name: aid.split(":", 1)[1]
                          for name, aid in eval_set["corpus"]["assets"].items()}
        self.name_of_digest = {d: n for n, d in self.digest_of.items()}
        self.durations: Dict[str, float] = eval_set["corpus"]["durations"]
        self.sources = footage_analysis.catalog_sources(project)
        self.name_of_clip: Dict[str, str] = {}
        self.name_of_path: Dict[str, str] = {}
        for src in self.sources:
            name = self.name_of_digest.get(src["digest"])
            if name is None:
                continue
            self.name_of_clip[src["clip_id"]] = name
            if src["path"]:
                self.name_of_path[os.path.abspath(src["path"])] = name
                self.name_of_path[os.path.realpath(src["path"])] = name
        self.missing = sorted(set(self.digest_of) - set(self.name_of_clip.values()))
        # The other way round: the ASSET a wanted moment names -> the clip
        # the project holds it in.  The stage attribution walks from a
        # wanted moment to the frames of that clip, so it needs this.
        self.clip_of_asset: Dict[str, str] = {
            name: clip_id for clip_id, name in self.name_of_clip.items()}

    def by_path(self, path: str) -> Optional[str]:
        return (self.name_of_path.get(os.path.abspath(path))
                or self.name_of_path.get(os.path.realpath(path)))


# ── Speech ──────────────────────────────────────────────────────────


def _calibration_samples(query_id: str, family: str, lane: str,
                         rows: Sequence[dict]) -> list:
    """The labelled outcomes a calibration curve needs.

    Each returned window is either a hit - it overlaps a ground-truth
    interval - or it is not, and that is the population a reliability
    curve wants.  The score carried is the RAW signal the floor judged
    on, and it is NAMED, because a number cannot tell a probability from
    a fused rank: the text lane's dense cosine and the frame lane's CLIP
    cosine are calibratable, and the fused rank is not.
    """
    signal = "frame_cosine" if lane == "visual" else "dense"
    samples = []
    for row in rows:
        score = row.get("dense") if lane == "text" else row.get("score")
        samples.append({
            "query_id": query_id,
            "family": family,
            "lane": lane,
            "signal": signal,
            "score": score,
            "hit": bool(row.get("hit")),
        })
    return samples


def score_speech(query: dict, report: dict, corpus: Corpus, rules: dict) -> dict:
    tol = rules["overlap_tolerance_seconds"]
    rows = []
    for hit in report.get("results", []):
        rows.append({"asset": corpus.name_of_clip.get(hit["clip_id"]),
                     "start": hit["start"], "end": hit["end"],
                     "score": hit.get("score"), "dense": hit.get("dense_score"),
                     "text": (hit.get("text") or "")[:120]})
    moments = query["moments"]
    found = set()
    for row in rows:
        row["hit"] = False
        for m, moment in enumerate(moments):
            for inst in moment["instances"]:
                if inst["asset"] == row["asset"] and overlaps(
                        (row["start"], row["end"]),
                        (inst["start"] - tol, inst["end"] + tol)):
                    row["hit"] = True
                    found.add(m)
    out = {"id": query["id"], "family": "speech", "query": query["query"],
           "returned": len(rows), "rows": rows,
           "best_rejected": report.get("best_rejected"),
           "error": report.get("error"), "degraded": report.get("degraded")}
    if moments:
        out.update(kind="present", moments=len(moments), moments_found=len(found),
                   recall=_ratio(len(found), len(moments)),
                   precision=_ratio(sum(r["hit"] for r in rows), len(rows)),
                   first_hit_rank=next((i + 1 for i, r in enumerate(rows)
                                        if r["hit"]), None))
    else:
        out.update(kind="absent", abstained=not rows)
    out["calibration_samples"] = _calibration_samples(
        query["id"], "speech", "text", rows)
    return out


# ── Visual ──────────────────────────────────────────────────────────


def _frame_spans(frames: Dict[str, list], by: float) -> Dict[str, List[Interval]]:
    return {name: pad(((t, t) for t in ts), by) for name, ts in frames.items()}


def score_visual(query: dict, report: dict, corpus: Corpus, rules: dict) -> dict:
    out = {"id": query["id"], "family": "visual", "query": query["query"]}
    if query.get("expect") == "refused":
        refused = report.get("refused")
        out.update(kind="refusal", refused=bool(refused),
                   abstained=bool(refused),
                   reason=(refused or {}).get("reason"))
        return out
    by = rules["frame_pad_seconds"]
    positive = _frame_spans(query.get("positive_frames", {}), by)
    ambiguous = _frame_spans(query.get("ambiguous_frames", {}), by)
    frames = report.get("results", [])
    rows = []
    for rng in report.get("ranges", []):
        name = corpus.name_of_clip.get(rng["clip_id"])
        members = [f for f in frames if f["clip_id"] == rng["clip_id"]
                   and rng["start"] - 1e-6 <= f["t"] <= rng["end"] + 1e-6]
        best = max(members, key=lambda f: f["score"])["t"] if members else rng["start"]
        in_pos = any(a <= best <= b for a, b in positive.get(name, []))
        in_amb = (not in_pos) and any(a <= best <= b
                                      for a, b in ambiguous.get(name, []))
        rows.append({"asset": name, "start": rng["start"], "end": rng["end"],
                     "best_frame": best, "best_score": rng["best_score"],
                     "hit": in_pos, "ambiguous": in_amb})
    refused = report.get("refused")
    out["refused"] = bool(refused)
    # Keep the actual frame candidate pool so the benchmark can distinguish
    # missing temporal coverage from ranking/interval selection misses.
    out["candidate_frames"] = [
        {"asset": corpus.name_of_clip.get(frame["clip_id"]),
         "time": frame["t"], "score": frame["score"]}
        for frame in frames
    ]
    out["search_policy"] = report.get("search_policy", {})
    if report.get("temporal_pass"):
        out["temporal_pass"] = report["temporal_pass"]
    if positive:
        judged = [r for r in rows if not r["ambiguous"]]
        touched = sum(1 for name, spans in positive.items() for span in spans
                      if any(r["asset"] == name and overlaps(
                          (r["start"], r["end"]), span) for r in rows))
        total = sum(len(s) for s in positive.values())
        out.update(kind="present", returned=len(rows), rows=rows,
                   positive_spans=total, spans_touched=touched,
                   recall=_ratio(touched, total),
                   precision=_ratio(sum(r["hit"] for r in judged), len(judged)))
    else:
        out.update(kind="absent", returned=len(rows), rows=rows,
                   abstained=not rows,
                   top_score=max((r["best_score"] for r in rows), default=None))
    return out


# ── Person ──────────────────────────────────────────────────────────


def resolve_person_id(project: str, person: dict, corpus: Corpus) -> Tuple[Optional[str], dict]:
    """The roster person whose face tracks cover the identifying asset."""
    from library.tools import person_entity
    roster = person_entity.resolve_person_tracks(project)["persons"]
    digest = corpus.digest_of[person["identified_by_asset"]]
    best, best_spans = None, 0
    for p in roster:
        spans = sum(1 for s in p.get("face_spans") or []
                    if s.get("content_digest") == digest)
        if spans > best_spans:
            best, best_spans = p, spans
    if best is None:
        return None, {"roster": [p["person_id"] for p in roster]}
    covers = sorted({corpus.name_of_digest.get(t["content_digest"], "?")
                     for t in best.get("face_tracks") or []})
    return best["person_id"], {"roster_size": len(roster), "face_track_assets": covers}


def _hits_by_asset(hits: List[dict], corpus: Corpus) -> Dict[str, List[Interval]]:
    out: Dict[str, List[Interval]] = {}
    for h in hits:
        name = corpus.by_path(h["source_file"])
        out.setdefault(name or "?", []).append((h["start"], h["end"]))
    return out


def score_person(query: dict, answer: dict, corpus: Corpus, rules: dict,
                 own_assets: Sequence[str] = ()) -> dict:
    tol = rules["tolerance_seconds"]
    pred = _hits_by_asset(answer.get("hits", []), corpus)
    gt: Dict[str, List[Interval]] = {}
    window: Dict[str, List[Interval]] = {}
    if query["predicate"] == "on_screen":
        for s in query["spans"]:
            gt.setdefault(s["asset"], []).append((s["start"], s["end"]))
        for name, dur in corpus.durations.items():
            window[name] = [(0.0, dur)]
    else:
        for s in query["turns"]:
            gt.setdefault(s["asset"], []).append((s["start"], s["end"]))
        for s in query["turns"] + query["other_turns"]:
            window.setdefault(s["asset"], []).append(
                (s["start"] - 1.0, s["end"] + 1.0))
    covered = total_gt = kept = pred_in = 0.0
    per_asset = {}
    for name in sorted(set(gt) | set(pred) | set(window)):
        w = merge(window.get(name, []))
        g = intersect(merge(gt.get(name, [])), w) if w else []
        p = intersect(merge(pred.get(name, [])), w) if w else []
        c = length(intersect(g, pad(p, tol)))
        k = length(intersect(p, pad(g, tol)))
        per_asset[name] = {"ground_truth_s": round(length(g), 1),
                           "returned_s": round(length(p), 1),
                           "recall": _ratio(c, length(g)),
                           "precision": _ratio(k, length(p))}
        covered += c
        total_gt += length(g)
        kept += k
        pred_in += length(p)
    own = [a for a in own_assets if a in per_asset]
    own_gt = sum(per_asset[a]["ground_truth_s"] for a in own)
    own_ret = sum(per_asset[a]["returned_s"] for a in own)
    own_cov = sum((per_asset[a]["recall"] or 0) * per_asset[a]["ground_truth_s"]
                  for a in own)
    own_kept = sum((per_asset[a]["precision"] or 0) * per_asset[a]["returned_s"]
                   for a in own)
    return {"id": query["id"], "family": "person",
            "person": query["person"]["name"], "predicate": query["predicate"],
            # Not pre-registered: the same rules restricted to the cameras
            # whose face tracks are this person's - the only assets the
            # shipped query returns source spans on (it reaches the other
            # angles through timeline placements, not source spans).
            "own_camera": {"assets": own,
                           "recall": _ratio(own_cov, own_gt),
                           "precision": _ratio(own_kept, own_ret)},
            "returned_spans": len(answer.get("hits", [])),
            "recall": _ratio(covered, total_gt),
            "precision": _ratio(kept, pred_in),
            "ground_truth_seconds": round(total_gt, 1),
            "returned_seconds_in_window": round(pred_in, 1),
            "per_asset": per_asset}


# ── Verified predicate ──────────────────────────────────────────────


def score_verified(query: dict, answer: dict, corpus: Corpus, rules: dict) -> dict:
    by = rules["event_pad_seconds"]
    window = {}
    for w in query["window"]:
        window.setdefault(w["asset"], []).append((w["start"], w["end"]))

    def in_window(name, span):
        return any(overlaps(span, w) for w in window.get(name, []))

    yes = []
    for h in answer.get("hits", []):
        name = corpus.by_path(h["source_file"])
        if in_window(name, (h["start"], h["end"])):
            yes.append((name, h["start"], h["end"]))
    events = [(e["asset"], e["start"] - by, e["end"] + by) for e in query["events"]]
    amb = [(e["asset"], e["start"] - by, e["end"] + by)
           for e in query.get("ambiguous_events", [])]
    found = sum(1 for n, a, b in events
                if any(n == y[0] and overlaps((y[1], y[2]), (a, b)) for y in yes))
    strict = sum(1 for y in yes if any(y[0] == n and overlaps((y[1], y[2]), (a, b))
                                       for n, a, b in events))
    lenient = sum(1 for y in yes if any(y[0] == n and overlaps((y[1], y[2]), (a, b))
                                        for n, a, b in events + amb))
    rejected_in = sum(1 for r in answer.get("rejected", [])
                      if in_window(corpus.by_path(r["source_file"]),
                                   (r["start"], r["end"])))
    return {"id": query["id"], "family": "verified_predicate",
            "person": query["person"]["name"], "statement": query["statement"],
            "candidates_total": answer.get("candidates"),
            "candidates_in_window": len(yes) + rejected_in,
            "yes_in_window": len(yes),
            "yes_outside_window_unscored": answer.get("spans", 0) - len(yes),
            "events": len(events), "events_found": found,
            "recall": _ratio(found, len(events)),
            "precision_strict": _ratio(strict, len(yes)),
            "precision_lenient": _ratio(lenient, len(yes)),
            "cost": answer.get("cost"),
            "yes_spans": [{"asset": n, "start": a, "end": b} for n, a, b in yes]}


# ── Editorial ─────────────────────────────────────────────────────────


def _requirement_agreement(query: dict) -> dict:
    """Does the plan name the requirement this query actually makes?

    The set declares the modalities the answer must draw evidence from.
    The decomposition (`library.tools.retrieval.requirements`) says which
    ones the engine will look for.  A DECLARED modality the decomposition
    misses is a requirement the search will not look for, and that is
    the failure this measures.

    A decomposed modality the set did not declare is a weaker signal: it
    may be an AMBIGUOUS reading (a bare noun with no framing word could
    be the words about a thing or the thing itself, and the plan keeps
    both and says so) or a CONSTRAINT (a temporal relation is a
    condition on the answer, not evidence for it).  Those are reported
    separately from the extras that are neither.
    """
    from library.tools.retrieval.requirements import decompose

    declared = set(query.get("requirements") or ())
    if not declared:
        return {"declared": [], "decomposed": [], "decomposed_kinds": [],
                "missing": [],
                "extra": [], "unambiguous_extra": [], "ambiguous_requirements": 0,
                "requirement_recall": None, "agrees": None}
    plan = decompose(query["query"])
    decomposed = set(plan.modalities)
    kinds = sorted({f"{r.modality}:{r.kind}" for r in plan.requirements})
    missing = sorted(declared - decomposed)
    extra = sorted(decomposed - declared)
    ambiguous = [r for r in plan.requirements if r.ambiguous]
    ambiguous_modalities = {r.modality for r in ambiguous}
    constraint_modalities = {"temporal", "relational"}
    unambiguous_extra = sorted(
        modality for modality in extra
        if modality not in ambiguous_modalities
        and modality not in constraint_modalities
        and not any(r.modality == modality and not r.ambiguous
                    for r in plan.requirements))
    return {
        "declared": sorted(declared),
        "decomposed": sorted(decomposed),
        "decomposed_kinds": kinds,
        "missing": missing,
        "extra": extra,
        "unambiguous_extra": unambiguous_extra,
        "ambiguous_requirements": len(ambiguous),
        "requirement_recall": round(
            len(declared & decomposed) / len(declared), 4),
        "agrees": not missing and not unambiguous_extra,
    }


def score_editorial(query: dict, report: dict, corpus: Corpus,
                    rules: dict,
                    evidence: Optional["AttributionEvidence"] = None) -> dict:
    """Score one editorial query.

    The rows are whatever windows the lane returned, ranked.  A row HITS
    when it is on an asset and overlaps an instance of a wanted moment,
    padded by the tolerance.  The consequence of the wrong rows is
    priced as well, because a window that holds nothing the editor asked
    for is not free.

    **The credit is CAPPED.**  A wanted moment is a region - a
    contiguous run of labelled moments - and ONE window earns at most
    one credit for it, however many labelled samples of that region the
    window happens to span.  Without the cap a range that spans twenty
    ten-second samples earns twenty hits, and "return the whole
    recording" scores better than the search does.  The uncapped number
    is still reported, as `recall_uncredited`, so nothing is hidden by
    the change (`library.tools.retrieval.regions`).
    """
    if query.get("expect") == "unlabelled":
        return {
            "id": query["id"], "family": "editorial",
            "query": query["query"],
            "editorial_need": query.get("editorial_need"),
            "lane": query.get("lane"),
            "category": query.get("category"),
            "kind": "unlabelled", "scored": False, "abstained": None,
            "returned": 0, "rows": [],
            "requirements": _requirement_agreement(query),
            "attribution": _attribute_editorial(query, report, corpus, rules,
                                                 [], set(), evidence),
            "reason": ("no ground truth exists for this category yet; the "
                       "entry is reported and NOT scored, because an "
                       "unscored query must never be counted as a miss"),
        }
    if query.get("expect") == "refused":
        refused = report.get("refused")
        return {
            "id": query["id"], "family": "editorial",
            "query": query["query"],
            "editorial_need": query.get("editorial_need"),
            "lane": query.get("lane"),
            "category": query.get("category"),
            "kind": "refusal", "refused": bool(refused),
            "abstained": bool(refused),
            "returned": 0, "rows": [],
            "requirements": _requirement_agreement(query),
            "attribution": _attribute_editorial(query, report, corpus, rules,
                                                 [], set(), evidence),
            "reason": (refused or {}).get("reason"),
        }
    tol = rules["overlap_tolerance_seconds"]
    rows = []
    for hit in report.get("results") or ():
        rows.append({
            "asset": corpus.name_of_clip.get(hit.get("clip_id")),
            "start": hit.get("start"), "end": hit.get("end"),
            "score": hit.get("score"), "dense": hit.get("dense_score"),
            "text": (hit.get("text") or "")[:120],
        })
    moments = query.get("moments") or []
    found = set()
    for row in rows:
        row["hit"] = False
        for index, moment in enumerate(moments):
            for instance in moment["instances"]:
                if instance["asset"] == row["asset"] and overlaps(
                        (row["start"], row["end"]),
                        (instance["start"] - tol, instance["end"] + tol)):
                    row["hit"] = True
                    found.add(index)
    offsets = rules.get("multicam_offsets_to_reference") or {}
    regions = regions_mod.wanted_regions(moments, offsets)
    credit = regions_mod.credit_windows(moments, regions, rows, tol)
    credited = set(credit["credited"])
    out = {"id": query["id"], "family": "editorial",
           "query": query["query"],
           "editorial_need": query.get("editorial_need"),
           "lane": query.get("lane"),
           "category": query.get("category"),
           "returned": len(rows), "rows": rows,
           "requirements": _requirement_agreement(query),
           "refused": report.get("refused"),
           "error": report.get("error"), "degraded": report.get("degraded")}
    # The temporal pass's own account of itself: what it classified,
    # what it inspected, what it cost.  It is part of the measurement,
    # not a footnote to it.
    if report.get("temporal_pass"):
        out["temporal_pass"] = report["temporal_pass"]
    if moments:
        out.update(kind="present", moments=len(moments),
                   moments_found=len(credited),
                   recall=_ratio(len(credited), len(moments)),
                   recall_uncredited=_ratio(len(found), len(moments)),
                   moments_overlapped=len(found),
                   regions=len(regions),
                   regions_found=len(credit["regions_found"]),
                   region_recall=_ratio(len(credit["regions_found"]),
                                        len(regions)),
                   credit_cap="one credit per window per region",
                   excess_seconds=credit["excess_seconds"],
                   precision=_ratio(sum(r["hit"] for r in rows), len(rows)),
                   first_hit_rank=next((i + 1 for i, r in enumerate(rows)
                                        if r["hit"]), None))
    else:
        out.update(kind="absent", abstained=not rows)
    from library.tools.retrieval.consequence import edit_consequence
    out["consequence"] = edit_consequence(
        moments, rows,
        selection_k=rules.get("selection_k", 1),
        tolerance_seconds=tol)
    out["calibration_samples"] = _calibration_samples(
        query["id"], "editorial", query.get("lane") or "text", rows)
    # Which stage lost the wanted moment.  Reported beside the recall,
    # never inside it: the number says how much was missed and this says
    # WHERE.
    out["attribution"] = _attribute_editorial(query, report, corpus, rules,
                                               rows, credited, evidence)
    return out


def _floor_kwargs(rules: dict, floor_key: str, window_key: str) -> dict:
    """The floor arguments a set declares, and nothing else.

    A set that declares no floor measures the lane AS IT SHIPS, floor
    and all: passing `floor=None` would silently turn the lane's own
    measured cutoff off, and the benchmark would then report a policy
    nobody runs.
    """
    kwargs = {}
    if floor_key in rules:
        kwargs["floor"] = rules[floor_key]
    if window_key in rules:
        kwargs["window_floor"] = rules[window_key]
    return kwargs


def _editorial_rows(report: dict) -> list:
    """The frame lane's RANGES as the window rows the scorer reads.

    The text lane returns `results`; the frame lane returns `ranges`,
    because a frame search returns an interval rather than a still.
    Both are windows, so both are scored the same way.

    The frame report carries BOTH keys: `results` holds the individual
    STILLS (a `t`, not a `start`/`end` pair) and `ranges` holds the
    merged intervals.  The stills are what the scorer must NOT read -
    a row without an interval is a row the scorer cannot compare with a
    ground-truth interval, and reading them crashed the benchmark on
    every frame-lane query that returned a candidate (measured
    2026-10-20, see docs/benchmarks/2026-10-20-editorial-retrieval-benchmark.md).
    So the presence of `ranges` decides, not the presence of `results`.
    """
    if "ranges" in report:
        return [{"clip_id": entry.get("clip_id"),
                 "start": entry.get("start"),
                 "end": entry.get("end"),
                 "score": entry.get("best_score"),
                 "text": ""}
                for entry in report.get("ranges") or ()]
    return list(report.get("results") or ())


# ── Coverage and time ───────────────────────────────────────────────


def coverage(project: str) -> dict:
    """Per-lane presence and the measured coverage fields, off the export."""
    from library.tools import memory_export
    portable, _local = memory_export.build_export(project)
    assets = portable["assets"]
    sections = ("media", "transcript", "frames", "persons", "identity",
                "clock", "events", "verdicts")
    present = {s: sum(1 for a in assets if a.get(s, {}).get("status") in (
        "present", "measured", "built", "transcribed")) for s in sections}
    minutes = sum((a.get("duration_seconds") or 0.0) for a in assets) / 60.0
    speech = sum(((a.get("transcript") or {}).get("coverage") or {}).get(
        "speech_seconds") or 0.0 for a in assets)
    face_frac = [((a.get("persons") or {}).get("coverage") or {}).get(
        "frames_with_face_fraction") for a in assets]
    gaps = [((a.get("frames") or {}).get("coverage") or {}).get("max_gap_seconds")
            for a in assets]
    return {
        "assets": len(assets), "video_minutes": round(minutes, 2),
        "asset_status": portable["summary"]["status"],
        "lanes_present": present,
        "speech_seconds": round(speech, 1),
        "speech_fraction_of_footage": _ratio(speech, minutes * 60.0),
        "frames_with_face_fraction": {a["name"]: f for a, f in zip(assets, face_frac)},
        "frame_sample_max_gap_seconds": {a["name"]: g for a, g in zip(assets, gaps)},
        "per_asset_status": {a["name"]: {"status": a["status"],
                                         "missing": a.get("missing"),
                                         "failure_reason": a.get("failure_reason")}
                             for a in assets},
    }


def processing_time(records: List[dict]) -> List[dict]:
    out = []
    for rec in records:
        lanes = [{"lane": l["lane"], "label": l["label"], "seconds": l["seconds"],
                  "built_video_minutes": l.get("built_video_minutes"),
                  "seconds_per_video_minute": l.get("seconds_per_video_minute"),
                  "outcomes": _count(l["sources"])}
                 for l in rec.get("lanes", [])]
        out.append({"project": os.path.basename(rec.get("project_folder", "")),
                    "memory_root": rec.get("memory_root"),
                    "status": rec.get("status"), "seconds": rec.get("seconds"),
                    "video_minutes": rec.get("video_minutes"),
                    "steps": rec.get("steps"), "lanes": lanes,
                    "code_revision": rec.get("code_revision")})
    return out


def _count(outcomes: dict) -> dict:
    counts: Dict[str, int] = {}
    for o in outcomes.values():
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    return counts


# ── The stage attribution ───────────────────────────────────────────


class AttributionEvidence:
    """What the stage attribution reads, gathered once for a run.

    Three bodies of evidence, and each one separates a pair of stages
    that no other evidence can:

    * the EXTRACTED frame sample (M2) - the only thing that separates
      "never decoded" from "never indexed";
    * the INDEX - what the query actually searched;
    * the transcript segments - what the text lane actually searched.

    Everything here is read-only and cached: the attribution runs once
    per query and must not re-decode anything.
    """

    def __init__(self, project: str, eval_set: dict):
        self.project = project
        mapping = eval_set.get("ground_truth_mapping") or {}
        self.offsets: Dict[str, float] = dict(
            mapping.get("multicam_offsets_to_reference") or {})
        self.groups: Dict[str, str] = dict(mapping.get("groups") or {})
        self.frame_index = None
        self.text_index = None
        self._index_times: Dict[str, List[float]] | None = None
        self._sampled_times: Dict[str, List[float]] | None = None
        self._sampled_status: Dict[str, str] | None = None
        self._segment_spans: Dict[str, List[tuple]] | None = None

    def index_times(self) -> Dict[str, List[float]]:
        """The times the frame INDEX holds, per clip."""
        if self._index_times is None:
            times: Dict[str, List[float]] = {}
            frames = getattr(self.frame_index, "frames", None) or ()
            for frame in frames:
                times.setdefault(frame["clip_id"], []).append(float(frame["t"]))
            for values in times.values():
                values.sort()
            self._index_times = times
        return self._index_times

    def sampled_times(self) -> Dict[str, List[float]]:
        """The times the EXTRACTION produced, per clip.

        This is the M2 sample the index was built from - the frames that
        were decoded off the media.  A wanted span with no extracted
        frame was never decoded, and no stage downstream of the decoder
        could ever have found it.
        """
        self._sampling()
        return self._sampled_times or {}

    def sampling_status(self) -> Dict[str, str]:
        """Whether each source's extracted sample is READABLE.

        A sample that is missing, absent or stale cannot answer whether
        a frame was decoded, and the attribution says so rather than
        reading the absence as a gap in the extraction.
        """
        self._sampling()
        return self._sampled_status or {}

    def _sampling(self) -> None:
        if self._sampled_times is not None:
            return
        from library.tools import source_memory
        from library.tools.analysis import footage_segments

        times: Dict[str, List[float]] = {}
        status: Dict[str, str] = {}
        for clip in footage_segments.load_catalog(self.project):
            doc, basis = source_memory.read_m2_for_clip(self.project, clip)
            times[clip.get("clip_id")] = sorted(
                float(frame["t"]) for frame in (doc or {}).get("frames") or ())
            status[clip.get("clip_id")] = basis
        self._sampled_times = times
        self._sampled_status = status

    def segment_spans(self) -> Dict[str, List[tuple]]:
        """The transcript segments the text lane searches, per clip."""
        if self._segment_spans is None:
            spans: Dict[str, List[tuple]] = {}
            segments = getattr(self.text_index, "segments", None) or ()
            for segment in segments:
                if segment.get("kind") != "speech":
                    continue
                spans.setdefault(segment.get("clip_id"), []).append(
                    (float(segment.get("start") or 0.0),
                     float(segment.get("end") or 0.0)))
            self._segment_spans = spans
        return self._segment_spans


def _as_asset_row(row: Optional[dict], corpus: Corpus) -> Optional[dict]:
    """A lane row named by ASSET, which is how a wanted moment names it."""
    if not row:
        return None
    return {"asset": corpus.name_of_clip.get(row.get("clip_id")),
            "start": row.get("start"), "end": row.get("end"),
            "score": row.get("dense_score") if "dense_score" in row
            else row.get("score")}


def _attribute_editorial(query: dict, report: dict, corpus: Corpus,
                         rules: dict, rows: list, credited: set,
                         evidence: Optional[AttributionEvidence]) -> dict:
    """Which stage lost this query's wanted moment - or why it has none.

    Every scored query gets an answer.  A query with no wanted moment
    (an ABSENT one), a REFUSED one and an UNLABELLED one have no stage
    to lose, and each says which it is rather than borrowing a stage
    from another kind of query.
    """
    from library.tools.retrieval import miss_attribution

    lane = query.get("lane") or "text"
    expect = query.get("expect")
    if expect == "unlabelled":
        return miss_attribution.attribute_unscored(
            query, lane, "unlabelled_not_scored")
    if expect == "refused":
        return miss_attribution.attribute_unscored(
            query, lane, "refused_by_design")
    if not (query.get("moments") or ()):
        outcome = "absent_returned" if rows else "absent_abstained"
        return miss_attribution.attribute_unscored(query, lane, outcome)
    if evidence is None:
        return miss_attribution.attribute_unscored(
            query, lane, "no_attribution_evidence")
    tolerance = rules.get("overlap_tolerance_seconds", 1.0)
    clip_of_asset = getattr(corpus, "clip_of_asset", None) or {}
    if lane == "visual":
        return miss_attribution.attribute_visual(
            query,
            pool=report.get("candidate_frames") or (),
            ranges=report.get("ranges") or (),
            rows=rows,
            policy=report.get("search_policy") or {},
            abstained=bool(report.get("abstained")),
            index_times=evidence.index_times(),
            sampled_times=evidence.sampled_times(),
            sampled_status=evidence.sampling_status(),
            clip_of_asset=clip_of_asset,
            offsets=evidence.offsets,
            groups=evidence.groups,
            credited=credited,
            tolerance=tolerance)
    return miss_attribution.attribute_text(
        query,
        rows=rows,
        weak=[_as_asset_row(row, corpus) for row in (report.get("weak") or ())],
        best_rejected=_as_asset_row(report.get("best_rejected"), corpus),
        abstained=bool(report.get("abstained")),
        floor=report.get("floor"),
        segment_spans=evidence.segment_spans(),
        clip_of_asset=clip_of_asset,
        credited=credited,
        tolerance=tolerance)


# ── The run ─────────────────────────────────────────────────────────


def run(project: str, eval_set: dict, families: Sequence[str] = FAMILIES,
        timing_records: Sequence[str] = (),
        labels: Optional[Mapping[str, Mapping]] = None,
        frame_index_dir: Optional[str] = None) -> dict:
    """Score the shipped search paths over `eval_set`.

    ``frame_index_dir`` names an ALTERNATIVE frame index to score
    against, for a before/after measurement of one index build.  It
    defaults to the project's own index, so the shipped behaviour is
    unchanged; a caller that measures a different build points it at
    that build's directory.
    """
    rules = eval_set["scoring"]
    from library.tools import editorial_eval_labels
    if labels is None:
        labels = editorial_eval_labels.load_store()
    eval_set, sealed_applied = editorial_eval_labels.apply_labels(
        eval_set, labels)
    corpus = Corpus(project, eval_set)
    results, latency = [], {}
    text_index = frame_index = None
    evidence = AttributionEvidence(project, eval_set)
    person_ids: Dict[str, Tuple[Optional[str], dict]] = {}

    for query in eval_set["queries"]:
        family = query["family"]
        if family not in families:
            continue
        started = time.perf_counter()
        if family == "speech":
            if text_index is None:
                from library.tools.analysis.footage_query import FootageIndex
                text_index = FootageIndex(project)
            report = text_index.search_report(query["query"],
                                              top_k=rules["speech"]["top_k"])
            scored = score_speech(query, report, corpus, rules["speech"])
        elif family == "visual":
            if frame_index is None:
                from library.tools.analysis.footage_frames import FrameIndex
                frame_index = FrameIndex(project, index_dir=frame_index_dir)
            report = frame_index.search_ranges(
                query["query"], top_ranges=rules["visual"]["top_k"],
                temporal=True,
                **_floor_kwargs(rules["visual"], "floor", "window_floor"))
            scored = score_visual(query, report, corpus, rules["visual"])
        elif family == "editorial":
            editorial_rules = dict(rules.get("editorial", EDITORIAL_RULES))
            # The conversation clock's offsets are what tell two angles of
            # ONE wanted moment apart from two wanted things; the set
            # carries them beside the queries.
            editorial_rules.setdefault(
                "multicam_offsets_to_reference",
                (eval_set.get("ground_truth_mapping") or {}).get(
                    "multicam_offsets_to_reference") or {})
            if query.get("lane") == "visual":
                if frame_index is None:
                    from library.tools.analysis.footage_frames import FrameIndex
                    frame_index = FrameIndex(project, index_dir=frame_index_dir)
                    evidence.frame_index = frame_index
                visual_report = frame_index.search_ranges(
                    query["query"],
                    top_ranges=editorial_rules["top_k"],
                    temporal=True,
                    **_floor_kwargs(editorial_rules, "visual_floor",
                                    "visual_window_floor"))
                report = {
                    "results": _editorial_rows(visual_report),
                    # The lane's own words about what it did: the pool it
                    # ranked, the windows it presented, the policy it ran
                    # under.  The stage attribution reads these and
                    # changes none of them.
                    "ranges": visual_report.get("ranges") or [],
                    "candidate_frames": visual_report.get("results") or [],
                    "search_policy": visual_report.get("search_policy") or {},
                    "temporal_pass": visual_report.get("temporal_pass"),
                    "abstained": visual_report.get("abstained"),
                    "refused": visual_report.get("refused"),
                    "error": visual_report.get("error"),
                    "degraded": None,
                }
            else:
                if text_index is None:
                    from library.tools.analysis.footage_query import FootageIndex
                    text_index = FootageIndex(project)
                    evidence.text_index = text_index
                report = text_index.search_report(
                    query["query"], top_k=editorial_rules["top_k"])
            scored = score_editorial(query, report, corpus, editorial_rules,
                                     evidence=evidence)
        else:
            from library.tools import event_spans
            key = query["person"]["identified_by_asset"]
            if key not in person_ids:
                person_ids[key] = resolve_person_id(project, query["person"], corpus)
            pid, how = person_ids[key]
            if pid is None:
                scored = {"id": query["id"], "family": family,
                          "error": f"no measured person covers {key}", **how}
            elif family == "person":
                answer = event_spans.query(project, pid, query["predicate"])
                scored = score_person(query, answer, corpus, rules["person"],
                                      how.get("face_track_assets", ()))
            else:
                answer = event_spans.verified_query(
                    project, pid, query["predicate"], query["statement"])
                scored = score_verified(query, answer, corpus,
                                        rules["verified_predicate"])
            scored["resolved_person"] = {"person_id": pid, **how}
        seconds = time.perf_counter() - started
        scored["seconds"] = round(seconds, 3)
        latency.setdefault(family, []).append(seconds)
        results.append(scored)
        print(f"[eval] {scored['id']:5s} {family:18s} {seconds:7.2f} s",
              file=sys.stderr, flush=True)

    records = [footage_analysis.read_run_record(project)]
    for path in timing_records:
        with open(path, encoding="utf-8") as handle:
            records.append(json.load(handle))
    result = {
        "eval_set": eval_set["name"],
        "eval_categories": sorted((eval_set.get("categories") or {}).keys()),
        "ran_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "code_revision": footage_analysis._git_head(),
        "project": os.path.basename(os.path.abspath(project)),
        "assets_missing_from_project": corpus.missing,
        "sealed_labels_applied": sealed_applied,
        "summary": summarise(
            results, latency,
            sorted((eval_set.get("categories") or {}).keys())),
        "coverage": coverage(project),
        "processing_time": processing_time([r for r in records if r]),
        "results": results,
    }
    if "visual" in families:
        from library.tools.visual_retrieval_benchmark import enrich_visual_result
        enrich_visual_result(eval_set, result, frame_index=frame_index, corpus=corpus,
                             rules=eval_set["scoring"]["visual"])
        result["summary"]["visual_benchmark"] = result["visual_benchmark"]
    return _with_provenance(result, eval_set, project, families)


def _with_provenance(result: dict, eval_set: dict, project: str,
                     families: Sequence[str]) -> dict:
    """The report carrying the claim's provenance.

    A retrieval number is a claim about ONE pre-registered set, scored
    against ONE project's footage, by the shipped search paths. The
    scope says so, so a number measured on another set, or on another
    corpus, is never read as this one.
    """
    stamped = dict(result)
    stamped["provenance"] = report_provenance.header(
        evidence_kind="live-qualification",
        evaluation_schema=str(result.get("schema")
                              or "ren-retrieval-evaluation/1"),
        fixture=(f"eval set {eval_set.get('name') or 'not_recorded'}; "
                 f"corpus {result.get('project') or 'not_recorded'}"),
        source_commit=result.get("code_revision"),
        run_timestamp=result.get("ran_at"),
        producer="library/tools/retrieval_eval.py:run (ren eval-search)",
        scope={
            "measurement": "retrieval_evaluation",
            "evaluation_level": "not_applicable",
            "task_set": str(eval_set.get("name") or "not_recorded"),
            "fixture": str(result.get("project") or "not_recorded"),
        },
        families=list(families),
    )
    return stamped


def _editorial_by_lane(editorial: List[dict]) -> dict:
    """The editorial measures per lane, each on its own denominator.

    The text lane and the frame lane are different searches with
    different ground truths, and a macro average over both hides which
    one moved.  A set that carries queries for one lane only therefore
    reports a number about that lane, and says so here.
    """
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    out: dict = {}
    for lane in ("text", "visual"):
        rows = [r for r in editorial if (r.get("lane") or "text") == lane]
        present = [r for r in rows if r.get("kind") == "present"]
        absent = [r for r in rows if r.get("kind") == "absent"]
        if not rows:
            continue
        out[lane] = {
            "queries_present": len(present),
            "queries_absent": len(absent),
            "queries_unlabelled": sum(1 for r in rows
                                      if r.get("kind") == "unlabelled"),
            "queries_refused": sum(1 for r in rows
                                   if r.get("kind") == "refusal"),
            "macro_recall": mean(r.get("recall") for r in present),
            "macro_recall_uncredited": mean(
                r.get("recall_uncredited") for r in present),
            "macro_precision": mean(r.get("precision") for r in present),
            "absent_abstained": sum(1 for r in absent if r.get("abstained")),
        }
    return out


def _summarise_attribution(rows: List[dict],
                           declared_categories: Sequence[str] = ()) -> dict:
    """The category table: which stage lost what, by lane and by category.

    The table is the ANSWER to the punchlist's question, and it is
    built from the per-query attributions, never from a second pass over
    the numbers: a query's stage is the stage its wanted moments were
    attributed to, and the table counts them.

    EVERY category the set declares appears in the table, with zeroes
    where nothing was attributed.  A breakdown that shows only the axes
    that have misses cannot be read as "the other axes are fine" - the
    punchlist asks for objects, actions, identities, locations,
    relationships and object states, and an axis with no labelled query
    is an axis with no denominator, which the reader must be able to
    see.
    """
    from library.tools.retrieval import miss_attribution

    by_lane: Dict[str, Dict[str, int]] = {}
    by_category: Dict[str, Dict[str, int]] = {
        category: {} for category in declared_categories}
    by_requirement_kind: Dict[str, Dict[str, int]] = {}
    queries: Dict[str, dict] = {}
    missed: List[str] = []
    unattributed: List[str] = []
    for row in rows:
        attribution = row.get("attribution") or {}
        lane = attribution.get("lane") or "?"
        category = attribution.get("category") or "uncategorised"
        entry = queries.setdefault(row["id"], {
            "lane": lane,
            "category": category,
            "outcome": attribution.get("outcome"),
            "recall": row.get("recall"),
            "region_recall": row.get("region_recall"),
            "wanted_items": attribution.get("wanted_items"),
            "credited_items": attribution.get("credited_items"),
            "instances_localized_on_sibling": attribution.get(
                "instances_localized_on_sibling"),
            "failure_stage": attribution.get("failure_stage"),
            "failure_pipeline_stage": attribution.get("failure_pipeline_stage"),
            "failure_kind": attribution.get("failure_kind"),
            "uncredited_stages": attribution.get("uncredited_stages") or {},
        })
        kinds = (row.get("requirements") or {}).get("decomposed_kinds") or []
        entry["requirement_kinds"] = kinds
        for stage, count in (attribution.get("uncredited_stages")
                             or {}).items():
            by_lane.setdefault(lane, {}).setdefault(stage, 0)
            by_lane[lane][stage] += count
            by_category.setdefault(category, {}).setdefault(stage, 0)
            by_category[category][stage] += count
            for kind in kinds:
                by_requirement_kind.setdefault(kind, {}).setdefault(stage, 0)
                by_requirement_kind[kind][stage] += count
        if row.get("kind") == "present" and (row.get("recall") or 0) < 1.0:
            missed.append(row["id"])
            if not attribution.get("failure_stage"):
                unattributed.append(row["id"])
    return {
        "question": ("which stage of retrieval lost the wanted moment"),
        "stages": list(miss_attribution.STAGES),
        "declared_categories": list(declared_categories),
        "pipeline_stages": sorted(set(
            miss_attribution.PIPELINE_STAGE.values())),
        "retrieval_failure_stages": sorted(
            miss_attribution.RETRIEVAL_FAILURE_STAGES),
        "credit_accounting_stages": sorted(
            miss_attribution.CREDIT_ACCOUNTING_STAGES),
        "by_lane": by_lane,
        "by_category": by_category,
        "by_requirement_kind": by_requirement_kind,
        "queries": queries,
        "missed_queries": sorted(missed),
        "missed_queries_without_a_stage": sorted(unattributed),
    }


def summarise(results: List[dict], latency: Dict[str, list],
              declared_categories: Sequence[str] = ()) -> dict:
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    out = {}
    speech = [r for r in results if r["family"] == "speech"]
    present = [r for r in speech if r.get("kind") == "present"]
    absent = [r for r in speech if r.get("kind") == "absent"]
    if speech:
        out["speech"] = {
            "queries_present": len(present), "queries_absent": len(absent),
            "macro_recall": mean(r["recall"] for r in present),
            "macro_precision": mean(r["precision"] for r in present),
            "moments": sum(r["moments"] for r in present),
            "moments_found": sum(r["moments_found"] for r in present),
            "present_with_nothing_returned": sum(1 for r in present if not r["returned"]),
            "absent_abstained": sum(1 for r in absent if r["abstained"]),
        }
    visual = [r for r in results if r["family"] == "visual"]
    if visual:
        vp = [r for r in visual if r.get("kind") == "present"]
        out["visual"] = {
            "queries_present": len(vp),
            "macro_recall": mean(r["recall"] for r in vp),
            "macro_precision": mean(r["precision"] for r in vp),
            "absent_abstained": sum(1 for r in visual if r.get("kind") == "absent"
                                    and r["abstained"]),
            "absent_queries": sum(1 for r in visual if r.get("kind") == "absent"),
            "refusals_correct": sum(1 for r in visual if r.get("kind") == "refusal"
                                    and r["refused"]),
            "refusal_queries": sum(1 for r in visual if r.get("kind") == "refusal"),
        }
    person = [r for r in results if r["family"] == "person" and "recall" in r]
    if person:
        out["person"] = {r["id"]: {"recall": r["recall"], "precision": r["precision"],
                                   "own_camera": r["own_camera"]}
                         for r in person}
    verified = [r for r in results if r["family"] == "verified_predicate"
                and "recall" in r]
    if verified:
        out["verified_predicate"] = {r["id"]: {
            "recall": r["recall"], "precision_strict": r["precision_strict"],
            "precision_lenient": r["precision_lenient"]} for r in verified}
    editorial = [r for r in results if r["family"] == "editorial"]
    if editorial:
        from library.tools.retrieval.consequence import summarise as summarise_consequence
        present = [r for r in editorial if r.get("kind") == "present"]
        absent = [r for r in editorial if r.get("kind") == "absent"]
        agreements = [r["requirements"] for r in editorial
                      if r.get("requirements")]
        out["editorial"] = {
            "queries_present": len(present),
            "queries_absent": len(absent),
            "queries_unlabelled": sum(1 for r in editorial
                                      if r.get("kind") == "unlabelled"),
            "queries_refused": sum(1 for r in editorial
                                   if r.get("kind") == "refusal"),
            "refusals_correct": sum(1 for r in editorial
                                    if r.get("kind") == "refusal"
                                    and r.get("refused")),
            "macro_recall": mean(r["recall"] for r in present),
            "macro_precision": mean(r["precision"] for r in present),
            # The uncapped number is reported beside the capped one, so
            # the change of definition is visible rather than silent.
            "macro_recall_uncredited": mean(
                r.get("recall_uncredited") for r in present),
            "macro_region_recall": mean(
                r.get("region_recall") for r in present),
            "credit_cap": "one credit per window per region",
            "excess_seconds": round(sum(
                (r.get("excess_seconds") or 0.0) for r in present), 3),
            # The lane decomposition, because a set that measures one lane
            # says nothing about the other: the held-out set carried no
            # text-lane present query at all, so its recall was a frame
            # lane number read as a retrieval number.
            "by_lane": _editorial_by_lane(editorial),
            "absent_abstained": sum(1 for r in absent if r.get("abstained")),
            "requirement_recall": mean(a["requirement_recall"]
                                       for a in agreements
                                       if a.get("requirement_recall")
                                       is not None),
            "requirement_agreement": sum(1 for a in agreements
                                          if a.get("agrees")),
            "requirement_queries": len(agreements),
            "declared_modalities_missed": sorted({
                modality for a in agreements for modality in a["missing"]}),
            # VI-010: the other half of retrieval, priced in the edit it
            # would cause.  A vector, never a single score.
            "false_positive_consequence": summarise_consequence(
                r["consequence"] for r in editorial if r.get("consequence")),
        }
        # VI-001: which stage lost each missed query, and the table of
        # those stages by lane and by category.  Reported beside the
        # recall, never inside it.
        attribution = _summarise_attribution(
            [r for r in editorial if r.get("attribution")],
            declared_categories)
        if attribution["queries"]:
            out["stage_attribution"] = attribution
    # VI-004: the score reads as a probability, or it says it does not.
    # Per lane, because the text lane's dense cosine and the frame
    # lane's CLIP cosine are different spaces.
    samples = [sample for r in results
               for sample in (r.get("calibration_samples") or ())]
    if samples:
        from library.tools.retrieval.calibration import by_lane
        out["calibration"] = by_lane(samples)
    out["latency_seconds"] = {f: {"median": round(sorted(v)[len(v) // 2], 3),
                                  "max": round(max(v), 3), "n": len(v)}
                              for f, v in latency.items()}
    cost = _temporal_cost(results)
    if cost:
        out["temporal_pass"] = cost
    return out


def _temporal_cost(results) -> dict:
    """What the temporal pass cost, over every query that ran it.

    The incremental cost of verification is part of the measurement: a
    pass that buys recall with model calls the caller cannot see is a
    pass nobody can decide to keep.
    """
    calls = cached = candidates = verified = rejected = uncertain = 0
    seconds = 0.0
    ran = 0
    for result in results:
        record = result.get("temporal_pass") or {}
        cost = record.get("cost") or {}
        if not cost:
            continue
        ran += 1
        calls += int(cost.get("calls") or 0)
        cached += int(cost.get("cached_calls") or 0)
        candidates += int(cost.get("candidates") or 0)
        verified += int(cost.get("verified") or 0)
        rejected += int(cost.get("rejected") or 0)
        uncertain += int(cost.get("uncertain") or 0)
        seconds += float(cost.get("seconds") or 0.0)
    if not ran:
        return {}
    return {
        "queries": ran,
        "model_calls": calls,
        "cached_calls": cached,
        "candidates_inspected": candidates,
        "candidates_established": verified,
        "candidates_rejected": rejected,
        "candidates_uncertain": uncertain,
        "model_seconds": round(seconds, 1),
        "seconds_per_candidate": (round(seconds / candidates, 2)
                                  if candidates else None),
    }


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren eval-search",
        description=("Score footage search against a pre-registered query set "
                     "with hand-marked spans. Reports; gates nothing."))
    parser.add_argument("project", help="the project or collection to search")
    parser.add_argument("--set", default=str(DEFAULT_SET),
                        help="the eval set (default: the committed geo-podcast set)")
    parser.add_argument("--families", nargs="+", choices=FAMILIES,
                        default=list(FAMILIES))
    parser.add_argument("--timing-record", action="append", default=[],
                        help="another analysis_run.json to report processing "
                        "time from (e.g. a fresh run on a scratch memory root)")
    parser.add_argument("--out-json", help="write the full result here")
    parser.add_argument("--frame-index-dir",
                        help="score against an ALTERNATIVE frame index "
                             "instead of the project's own, for a "
                             "before/after measurement of one index build")
    args = parser.parse_args(argv)
    from library.tools.project_registry import resolve_project_path
    found = resolve_project_path(args.project)
    if found is None:
        raise RenRefusal(f"{args.project!r} is not a project",
                         "the eval searches one project or collection",
                         "ren eval-search <path to project or collection>", blocker="missing_media")
    with open(args.set, encoding="utf-8") as handle:
        eval_set = json.load(handle)
    result = run(str(found.parent), eval_set, args.families,
                 args.timing_record,
                 frame_index_dir=args.frame_index_dir)
    text = json.dumps(result, indent=1)
    if args.out_json:
        Path(args.out_json).write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
