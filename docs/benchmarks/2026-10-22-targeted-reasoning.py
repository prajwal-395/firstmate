"""VI-013: measure the world model answered narrowly against the world
model read whole.

    python3 docs/benchmarks/2026-10-22-targeted-reasoning.py \
        --memory-root <dir> --project <reel project> [--out-json FILE]

Three routes are measured over the same questions:

- ``today_api`` - the surface that exists today: ``world_model.json``
  read whole, and ``world_model.query_at_time`` for a point. It answers
  ONE SECOND. A span question and an entity question have no route.
- ``whole_view`` - an agent holding the whole per-second document and
  scanning it. It answers span and entity questions, but the document's
  rows are per-second, its ``speaking``/``on_screen`` rows carry NO
  timing at all, and no row says which lane produced it.
- ``targeted`` - ``library.tools.world_model_query``: interval and
  entity queries over the canonical records, at native timing, with a
  provenance row per fact.

For each route the harness records: whether the question was ANSWERABLE,
whether the answer was CORRECT against the canonical record, how many
facts carried PROVENANCE, and the TOKENS of world-model context one
planning call would carry. Every denominator is in the output.

The M0 cases are the M0 milestone's own cases (M0-1 event resolution,
M0-2 boundary precision, M0-3 repeat identity, M0-8 abstention
honesty). The reel plan is a real reel's candidate set from a Ren-owned
scratch project, and its own speaker attribution is the ground truth.

Stdlib plus the engine's own modules; nothing is written outside the
repository, and no footage is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import (
    source_memory,
    token_measure,
    world_model,
    world_model_query,
)

M0_BOUNDARY_TOLERANCE_SECONDS = 3.0 / 24.0
"""M0-2's bar: the boundary within 3 frames of the truth, at the corpus's
own 23.976 fps."""


# ── The corpus ─────────────────────────────────────────────────────


def _digests(memory_root: Path) -> list:
    rows = []
    for entry in sorted(memory_root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / source_memory.SLOT_WORLD_MODEL).exists():
            continue
        rows.append(entry.name)
    return rows


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _slot_bytes(memory_root: Path, digest: str, slot: str) -> int:
    path = memory_root / digest / slot
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ── The ground truth, read from the canonical records ──────────────


def canonical_occurrences(memory_root: Path, digest: str, kind: str) -> dict:
    """`{face_track: [span, ...]}` for one candidate kind, in time order.

    Read straight off M7, the way the record was written - no module of
    this lane is involved, so the truth does not depend on the route
    under test.
    """
    document = _read_json(memory_root / digest / source_memory.SLOT_EVENTS)
    if not isinstance(document, dict):
        return {}
    block = (document.get("candidates") or {}).get(kind) or {}
    by_track: dict = {}
    for span in block.get("spans") or []:
        track = span.get("face_track")
        by_track.setdefault(track, []).append({
            "start": float(span["start"]), "end": float(span["end"])})
    for spans in by_track.values():
        spans.sort(key=lambda row: (row["start"], row["end"]))
    return by_track


# ── The routes ─────────────────────────────────────────────────────


_DOCUMENT_CACHE: dict = {}
"""The whole per-second document per digest, read once.

The whole-view route has to hold the document to scan it, and the
document does not change while the bench runs, so it is read and
counted once per source.
"""


def _whole_document(memory_root: Path, digest: str):
    """`(model, text, size)` for one source's per-second document."""
    key = (str(memory_root), digest)
    if key not in _DOCUMENT_CACHE:
        path = memory_root / digest / source_memory.SLOT_WORLD_MODEL
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        model = None
        if text:
            try:
                model = json.loads(text)
            except ValueError:
                model = None
        _DOCUMENT_CACHE[key] = (model, text,
                                 token_measure.measure(text))
    return _DOCUMENT_CACHE[key]


def route_today_api(memory_root: Path, digest: str, question: dict) -> dict:
    """The surface that exists today: one second at a time.

    `world_model.query_at_time` answers a question about ONE second.
    There is no interval query and no entity query, so a span-scoped or
    entity-scoped question has no route at all - and answering it means
    reading the WHOLE document, because that is how the view is stored.
    """
    model, _text, size = _whole_document(memory_root, digest)
    if model is None:
        return {"answerable": False, "reason": "no world model built",
                "facts": [], "tokens": 0, "bytes_read": 0}
    payload_bytes = size.get(token_measure.BYTES, 0)
    if question.get("kind") != "point":
        return {"answerable": False,
                "reason": "no interval or entity query exists",
                "facts": [], "tokens": payload_bytes,
                "token_counts": size,
                "bytes_read": _slot_bytes(memory_root, digest,
                                          source_memory.SLOT_WORLD_MODEL),
                "lanes_present": None, "lanes_absent": None,
                "provenance_facts": 0}
    entry = world_model.query_at_time(model, float(question["start"]))
    if entry is None:
        return {"answerable": False, "reason": "outside the source",
                "facts": [], "tokens": payload_bytes,
                "token_counts": size, "bytes_read": 0,
                "lanes_present": None, "lanes_absent": None,
                "provenance_facts": 0}
    rows = (entry.get("events") or {}).get("hand_near_mouth") or []
    return {"answerable": True, "facts": rows,
            "tokens": payload_bytes,
            "token_counts": size,
            "bytes_read": _slot_bytes(memory_root, digest,
                                      source_memory.SLOT_WORLD_MODEL),
            "lanes_present": None, "lanes_absent": None,
            "provenance_facts": 0}


def route_whole_view(memory_root: Path, digest: str, question: dict) -> dict:
    """An agent holding the whole per-second document and scanning it."""
    model, _text, size = _whole_document(memory_root, digest)
    if model is None:
        return {"answerable": False, "reason": "no world model built",
                "facts": [], "tokens": 0, "bytes_read": 0}
    facts = []
    kind = question.get("kind")
    track = question.get("entity")
    if kind in ("interval", "entity", "abstention", "point"):
        seen = set()
        for entry in model.get("timeline") or []:
            if kind == "interval":
                start = float(question["start"])
                end = float(question["end"])
                if not (start <= float(entry["t"]) < end):
                    continue
            if kind == "point":
                if float(entry["t"]) != int(float(question["start"])):
                    continue
            for row in (entry.get("events") or {}).get("hand_near_mouth") or []:
                if kind == "entity" and row.get("face_track") != track:
                    continue
                key = (row.get("face_track"), row.get("start"), row.get("end"))
                if key in seen:
                    continue
                seen.add(key)
                facts.append(row)
        facts.sort(key=lambda row: (row.get("start") or 0.0,
                                    row.get("end") or 0.0))
    lanes = model.get("lanes") or []
    absent = [lane for lane in world_model_query.LANE_SLOT
              if lane not in lanes]
    return {"answerable": bool(facts) or kind == "abstention",
            "facts": facts, "tokens": size.get(token_measure.BYTES, 0),
            "token_counts": size,
            "bytes_read": _slot_bytes(memory_root, digest,
                                      source_memory.SLOT_WORLD_MODEL),
            "lanes_present": list(lanes), "lanes_absent": absent,
            "provenance_facts": 0}


def route_targeted(memory_root: Path, digest: str, question: dict) -> dict:
    """The query surface: interval and entity queries, native timing."""
    world = world_model_query.World(digest, memory_root)
    kind = question.get("kind")
    aspect = question.get("aspect") or ("events" if kind == "point"
                                         else "what")
    if kind == "entity":
        answer = world.entity(question["entity"])
        facts = answer["occurrences"]
    else:
        answer = world.answer({"aspect": aspect,
                                "start": question["start"],
                                "end": question["end"]})
        facts = answer["facts"]
    payload = json.dumps({"facts": facts}, ensure_ascii=False, sort_keys=True)
    size = token_measure.measure(payload)
    lanes_read = sorted(world._lanes)
    return {
        "answerable": True,
        "facts": facts,
        "tokens": size.get(token_measure.BYTES, 0),
        "token_counts": size,
        "bytes_read": sum(_slot_bytes(memory_root, digest,
                                      world_model_query.LANE_SLOT[lane])
                          for lane in lanes_read),
        "lanes_read": lanes_read,
        "lanes_present": [lane for lane in lanes_read],
        "lanes_absent": answer.get("lanes_absent", []),
        "provenance_facts": sum(
            1 for row in facts if row.get("provenance", {}).get("lane")),
    }


ROUTES = {"today_api": route_today_api,
          "whole_view": route_whole_view,
          "targeted": route_targeted}


# ── The questions ──────────────────────────────────────────────────


def m0_questions(memory_root: Path, digest: str) -> list:
    """The M0 milestone's cases, asked of this source's own records.

    M0-1 (event resolution), M0-2 (boundary precision), M0-3 (repeat
    identity) are asked of the SECOND occurrence of the hand-near-mouth
    candidate for every track that has one. M0-P is the control: the
    same occurrence, asked about ONE SECOND, which the surface that
    exists today can answer. M0-8 (abstention honesty) is asked of
    every source: did the person ever cover their mouth, and can the
    route tell "never happened" from "never measured"?
    """
    questions = []
    occurrences = canonical_occurrences(memory_root, digest,
                                        "hand_near_mouth")
    for track, spans in sorted(occurrences.items()):
        if len(spans) < 2:
            continue
        second = spans[1]
        questions.append({
            "case": "M0-1", "kind": "interval", "entity": track,
            "question": f"the second time {track} covers their mouth",
            "start": second["start"] - 0.5, "end": second["end"] + 0.5,
            "truth": second,
        })
        questions.append({
            "case": "M0-2", "kind": "interval", "entity": track,
            "question": f"the boundary of the second time {track} covers "
                        "their mouth",
            "start": second["start"] - 0.5, "end": second["end"] + 0.5,
            "truth": second,
        })
        questions.append({
            "case": "M0-3", "kind": "entity", "entity": track,
            "question": f"when does {track} cover their mouth, and which "
                        "one is the second",
            "start": second["start"] - 0.5, "end": second["end"] + 0.5,
            "truth": second,
        })
        questions.append({
            "case": "M0-P", "kind": "point", "entity": track,
            "question": f"what is {track} doing at "
                        f"{second['start'] + 0.1:.2f}s",
            "start": second["start"] + 0.1, "end": second["start"] + 0.1,
            "truth": second,
        })
    questions.append({
        "case": "M0-8", "kind": "abstention", "aspect": "events",
        "question": "did the person ever cover their mouth",
        "start": 0.0, "end": _duration(memory_root, digest),
        "truth": None,
    })
    return questions


def _duration(memory_root: Path, digest: str) -> float:
    """The source's own duration, read off its world model."""
    model, _text, _size = _whole_document(memory_root, digest)
    return float((model or {}).get("duration_seconds") or 0.0)


def _closest_span(facts: list, track: str, truth_start: float) -> dict | None:
    """The span a route reported that is closest to the truth's start."""
    spans = []
    for row in facts:
        if track is not None and not (
                row.get("face_track") == track
                or row.get("entity") == track
                or (row.get("value") or {}).get("face_track") == track):
            continue
        start = row.get("start")
        end = row.get("end")
        if start is None or end is None:
            continue
        spans.append((float(start), float(end)))
    if not spans:
        return None
    spans.sort()
    best = min(spans, key=lambda span: abs(span[0] - truth_start))
    return {"start": best[0], "end": best[1]}


def score(question: dict, result: dict) -> dict:
    """Whether one route answered one question, and whether it was right."""
    case = question["case"]
    if not result.get("answerable"):
        return {"answered": False, "correct": False,
                "reason": result.get("reason", "not answerable")}
    if case == "M0-8":
        # The honest answer names the lane that was never built, or says
        # the lane measured and found nothing. A route that reports
        # "nothing happened" without saying which of the two it is has
        # answered a question nobody measured - a false negative.
        absent = result.get("lanes_absent")
        present = result.get("lanes_present")
        if absent is None and present is None:
            return {"answered": True, "correct": False,
                    "reason": "the route cannot say whether the lane ran"}
        if "m7" in absent:
            if result.get("facts"):
                return {"answered": True, "correct": False,
                        "reason": "answered an unmeasured question"}
            return {"answered": True, "correct": True,
                    "reason": "the missing lane is named"}
        if "m7" in present:
            return {"answered": True, "correct": True,
                    "reason": "the lane ran, and its answer is reported"}
        return {"answered": True, "correct": False,
                "reason": "answered 'nothing happened' without a lane"}
    truth = question.get("truth") or {}
    if case in ("M0-1", "M0-2"):
        found = _closest_span(result.get("facts") or [],
                              question.get("entity"), truth["start"])
        if found is None:
            return {"answered": True, "correct": False,
                    "reason": "the occurrence was not found"}
        error = abs(found["start"] - truth["start"])
        if case == "M0-1":
            return {"answered": True, "correct": True,
                    "reason": f"the occurrence was resolved, "
                              f"boundary error {error:.3f}s"}
        ok = error <= M0_BOUNDARY_TOLERANCE_SECONDS
        return {"answered": True, "correct": ok,
                "reason": f"boundary error {error:.3f}s "
                          f"(bar {M0_BOUNDARY_TOLERANCE_SECONDS:.3f}s)"}
    if case == "M0-3":
        facts = result.get("facts") or []
        second = [row for row in facts
                  if row.get("occurrence") == 2
                  and row.get("kind") == "hand_near_mouth"]
        if second:
            return {"answered": True, "correct": True,
                    "reason": "the second occurrence is named"}
        return {"answered": True, "correct": False,
                "reason": "no occurrence is named an ordinal"}
    if case == "M0-P":
        found = _closest_span(result.get("facts") or [],
                              question.get("entity"), truth["start"])
        if found is None:
            return {"answered": True, "correct": False,
                    "reason": "the moment was not reported"}
        return {"answered": True, "correct": True,
                "reason": f"the moment was reported, boundary error "
                          f"{abs(found['start'] - truth['start']):.3f}s"}
    return {"answered": True, "correct": False, "reason": "unknown case"}


# ── The reel plan ──────────────────────────────────────────────────


def reel_plan_questions(project: Path) -> list:
    """The real reel's candidates, as planning questions over the source.

    The reel plan's own speaker attribution (`seconds_by_speaker`) is
    the ground truth: it was measured by the reel builder from the
    master transcript, independently of this lane.
    """
    data = _read_json(project / "pipeline_data.json")
    if not isinstance(data, dict):
        return []
    outputs = data.get("capability_outputs") or {}
    candidates = ((outputs.get("reel.candidates") or {}).get("reel_candidates")
                  or [])
    transcript = _read_json(project / "pipeline_output" / "scratch"
                             / "timeline_transcript" / "transcript.json")
    segments = (transcript or {}).get("segments") or []
    questions = []
    for index, candidate in enumerate(candidates):
        start = candidate.get("start")
        end = candidate.get("end")
        if start is None or end is None:
            continue
        inside = [row for row in segments
                  if row.get("timeline_start") is not None
                  and row.get("timeline_end") is not None
                  and float(row["timeline_end"]) > float(start)
                  and float(row["timeline_start"]) < float(end)]
        if not inside:
            continue
        by_source: dict = {}
        for row in inside:
            by_source.setdefault(row.get("source_file"), []).append(row)
        questions.append({
            "candidate": index,
            "start": float(start),
            "end": float(end),
            "plan_speakers": candidate.get("seconds_by_speaker") or {},
            "plan_share": candidate.get("share_by_speaker") or {},
            "segments": by_source,
        })
    return questions


def measure_reel_plan(project: Path, memory_root: Path) -> dict:
    """The reel plan, answered by the world model, against the plan."""
    questions = reel_plan_questions(project)
    rows = []
    catalog = source_memory.load_catalog(str(project))
    recorded = source_memory.load_recorded_fingerprints(str(project))
    digest_of_file = {}
    for clip in catalog:
        digest, _basis = source_memory.digest_for_clip(
            str(project), clip, recorded)
        if digest:
            digest_of_file[clip.get("source_file")] = digest
    for question in questions:
        plan_share = question["plan_share"] or {}
        world_share: dict = {}
        answered = False
        for source_file, segments in (question["segments"] or {}).items():
            digest = digest_of_file.get(source_file)
            if not digest:
                continue
            world = world_model_query.World(digest, memory_root)
            for segment in segments:
                start = segment.get("source_start")
                end = segment.get("source_end")
                if start is None or end is None:
                    continue
                facts = world.facts(float(start), float(end), ["speech"])
                for fact in facts:
                    if fact["kind"] != "speaking":
                        continue
                    track = fact.get("entity")
                    speaker = _speaker_for_track(segments, track,
                                                 fact["start"], fact["end"])
                    if not speaker:
                        continue
                    answered = True
                    spoken = max(0.0, min(fact["end"], float(end))
                                 - max(fact["start"], float(start)))
                    world_share[speaker] = world_share.get(speaker, 0.0) + spoken
        total = sum(world_share.values())
        world_norm = {name: round(value / total, 4)
                      for name, value in world_share.items()} if total else {}
        plan_norm = {name: round(float(value), 4)
                     for name, value in plan_share.items()}
        speakers_match = set(world_norm) == set(plan_norm)
        differences = [abs(world_norm.get(name, 0.0) - plan_norm.get(name, 0.0))
                       for name in set(world_norm) | set(plan_norm)]
        rows.append({
            "candidate": question["candidate"],
            "plan_share": plan_norm,
            "world_share": world_norm,
            "speakers_match": speakers_match,
            "mean_absolute_share_difference": (
                round(sum(differences) / len(differences), 4)
                if differences else None),
            "answered": answered,
        })
    matched = [row for row in rows if row["speakers_match"]]
    close = [row for row in rows
             if row["mean_absolute_share_difference"] is not None
             and row["mean_absolute_share_difference"] <= 0.10]
    return {
        "candidates_asked": len(rows),
        "candidates_answered": sum(1 for row in rows if row["answered"]),
        "speaker_set_match": f"{len(matched)}/{len(rows)}",
        "speaker_share_within_0.10": f"{len(close)}/{len(rows)}",
        "rows": rows,
    }


def _speaker_for_track(segments: list, track: str,
                        start: float, end: float) -> str | None:
    """The transcript's speaker for a track, by source-time overlap.

    The transcript's speaker labels and the source's face tracks are
    different measurements of the same moment; the join is the
    overlap in the SOURCE's clock, which both carry.
    """
    if not track:
        return None
    best = None
    best_overlap = 0.0
    for segment in segments:
        seg_start = segment.get("source_start")
        seg_end = segment.get("source_end")
        if seg_start is None or seg_end is None:
            continue
        overlap = min(float(seg_end), end) - max(float(seg_start), start)
        if overlap > best_overlap:
            best_overlap = overlap
            best = segment.get("speaker")
    return best


# ── The run ────────────────────────────────────────────────────────


def run(memory_root: Path, project: Path) -> dict:
    digests = _digests(memory_root)
    corpus = []
    per_question = []
    for digest in digests:
        model_path = memory_root / digest / source_memory.SLOT_WORLD_MODEL
        model = _read_json(model_path)
        whole_text = model_path.read_text(encoding="utf-8")
        whole_size = token_measure.measure(whole_text)
        corpus.append({
            "content_digest": digest,
            "duration_seconds": (model or {}).get("duration_seconds"),
            "lanes": (model or {}).get("lanes") or [],
            "world_model_bytes": model_path.stat().st_size,
            "world_model_tokens": whole_size,
        })
        for question in m0_questions(memory_root, digest):
            row = {"content_digest": digest, "case": question["case"],
                   "question": question["question"],
                   "truth": question.get("truth")}
            for name, route in ROUTES.items():
                result = route(memory_root, digest, question)
                verdict = score(question, result)
                row[name] = {
                    "answered": verdict["answered"],
                    "correct": verdict["correct"],
                    "reason": verdict["reason"],
                    "context_bytes": result.get("tokens", 0),
                    "token_counts": result.get("token_counts") or {},
                    "bytes_read": result.get("bytes_read", 0),
                    "provenance_facts": result.get("provenance_facts", 0),
                    "facts": len(result.get("facts") or []),
                }
            per_question.append(row)

    def _summary(route: str) -> dict:
        rows = per_question
        answered = sum(1 for row in rows if row[route]["answered"])
        correct = sum(1 for row in rows if row[route]["correct"])
        provenance = sum(row[route]["provenance_facts"] for row in rows)
        facts = sum(row[route]["facts"] for row in rows)
        tokens = [row[route].get("token_counts") or {}
                  for row in rows]
        o200k = [count.get("o200k_base") for count in tokens
                 if count.get("o200k_base") is not None]
        return {
            "questions": len(rows),
            "answered": f"{answered}/{len(rows)}",
            "correct": f"{correct}/{len(rows)}",
            "facts_with_provenance": f"{provenance}/{facts}",
            "context_bytes_per_call": (
                sum(row[route]["context_bytes"] for row in rows) // len(rows)
                if rows else 0),
            "o200k_tokens_per_call": (
                sum(o200k) // len(o200k) if o200k else None),
        }

    return {
        "corpus": corpus,
        "m0_questions": per_question,
        "routes": {name: _summary(name) for name in ROUTES},
        "reel_plan": measure_reel_plan(project, memory_root),
        "tokenizer": token_measure.available(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="targeted-reasoning-bench",
        description="VI-013: the world model answered narrowly against "
                    "the world model read whole.")
    parser.add_argument("--memory-root", required=True,
                        help="a source-memory root holding built world "
                             "models (a scratch copy, never the machine "
                             "store)")
    parser.add_argument("--project", required=True,
                        help="a Ren-owned scratch project holding a real "
                             "reel plan")
    parser.add_argument("--out-json")
    args = parser.parse_args(argv)

    memory_root = Path(args.memory_root).expanduser()
    project = Path(args.project).expanduser()
    os.environ[source_memory.MEMORY_ROOT_ENV] = str(memory_root)
    report = run(memory_root, project)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out_json:
        Path(args.out_json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
