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
             verdicts take the heavy-work lock themselves.

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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from library.tools import footage_analysis
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

DEFAULT_SET = Path(__file__).with_name("retrieval_eval_set.json")
FAMILIES = ("speech", "visual", "person", "verified_predicate")

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

    def by_path(self, path: str) -> Optional[str]:
        return (self.name_of_path.get(os.path.abspath(path))
                or self.name_of_path.get(os.path.realpath(path)))


# ── Speech ──────────────────────────────────────────────────────────


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


# ── The run ─────────────────────────────────────────────────────────


def run(project: str, eval_set: dict, families: Sequence[str] = FAMILIES,
        timing_records: Sequence[str] = ()) -> dict:
    corpus = Corpus(project, eval_set)
    rules = eval_set["scoring"]
    results, latency = [], {}
    text_index = frame_index = None
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
                frame_index = FrameIndex(project)
            report = frame_index.search_ranges(query["query"],
                                               top_ranges=rules["visual"]["top_k"])
            scored = score_visual(query, report, corpus, rules["visual"])
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
    return {
        "eval_set": eval_set["name"],
        "ran_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "code_revision": footage_analysis._git_head(),
        "project": os.path.basename(os.path.abspath(project)),
        "assets_missing_from_project": corpus.missing,
        "summary": summarise(results, latency),
        "coverage": coverage(project),
        "processing_time": processing_time([r for r in records if r]),
        "results": results,
    }


def summarise(results: List[dict], latency: Dict[str, list]) -> dict:
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
    out["latency_seconds"] = {f: {"median": round(sorted(v)[len(v) // 2], 3),
                                  "max": round(max(v), 3), "n": len(v)}
                              for f, v in latency.items()}
    return out


def main(argv=None) -> int:
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
    args = parser.parse_args(argv)
    from library.tools.project_registry import resolve_project_path
    found = resolve_project_path(args.project)
    if found is None:
        raise RenRefusal(f"{args.project!r} is not a project",
                         "the eval searches one project or collection",
                         "ren eval-search <path to project or collection>")
    with open(args.set, encoding="utf-8") as handle:
        eval_set = json.load(handle)
    result = run(str(found.parent), eval_set, args.families, args.timing_record)
    text = json.dumps(result, indent=1)
    if args.out_json:
        Path(args.out_json).write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
