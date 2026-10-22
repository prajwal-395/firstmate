"""INFRA-003: one multimodal query interface, measured before and after.

    python3 docs/benchmarks/2026-10-21-infra003-query-interface.py \
        --machine-root ~/.local/share/vep/source_memory \
        --scratch-root /tmp/fm-infra003/memory \
        --project ~/Documents/content_stuff/video_projects/ren-scratch-l3wave \
        [--out-json docs/benchmarks/2026-10-21-infra003-query-interface.json]

The punchlist's INFRA-003 asks for a reusable multimodal query
interface: search by speech, appearance, action, identity, relation and
temporal order; grounded media intervals with confidence or
uncertainty; evidence inspection independent of Resolve; and a shape
future consumers can read.

This harness measures that against the surface that exists BEFORE this
lane, on the same questions and the same corpus:

- ``before`` - the world model's query surface as it stood on main:
  interval, entity and who/what/where questions over the canonical
  lanes, each fact carrying the lane, the slot, the reader and the
  field. No search modes, no source reference, no epistemic class, no
  confidence, no evidence inspection.
- ``after`` - the same surface extended: the six search modes over
  the same canonical records, every interval carrying the universal
  source reference, the class its producer earned, and either a
  confidence or a named uncertainty.

The "before" route is the "after" route's rows with the three added
fields removed, which is exactly what main returned: the change is
additive, so stripping it reproduces the old answer.

The M0 cases are the M0 milestone's own cases, asked of every source
that carries the event lane, and the reel plan is a real reel's
candidate set from a Ren-owned scratch project, with the reel
builder's own speaker attribution as the ground truth. Both the
questions and the scoring come from the VI-013 harness, so the two
measurements speak the same language.

Nothing outside the repository and the scratch root is written, no
footage is touched, and no Resolve is asked for anything.
"""

from __future__ import annotations

import argparse
import importlib.util
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

VI013_PATH = (REPO_ROOT / "docs" / "benchmarks"
              / "2026-10-22-targeted-reasoning.py")
_spec = importlib.util.spec_from_file_location("vi013_bench", VI013_PATH)
vi013 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vi013)
"""The VI-013 harness, imported so the questions and the scoring are
the SAME ones, not a copy that can drift."""


GROUNDING_KEYS = ("source_reference", "confidence", "uncertainty")
PROVENANCE_KEYS = ("class", "claim", "producer", "measurement_type")


def strip_grounding(row: dict) -> dict:
    """The row as it stood before this lane: no grounding, no class."""
    out = {key: value for key, value in row.items()
           if key not in GROUNDING_KEYS}
    out["provenance"] = {key: value
                         for key, value in row["provenance"].items()
                         if key not in PROVENANCE_KEYS}
    return out


# ── The corpus ─────────────────────────────────────────────────────


def prepare_scratch(machine_root: Path, scratch_root: Path) -> list:
    """A scratch memory root: the machine's lanes, symlinked, read-only.

    The world models are built FRESH in the scratch root, because the
    per-second view is a derived artefact and the machine's store is
    not ours to write. Every lane file the machine carries is linked
    into the scratch root, so a reader of the scratch root reads the
    machine's measurements and nothing else.
    """
    digests = []
    for entry in sorted(machine_root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / source_memory.SLOT_EVENTS).exists():
            continue
        target = scratch_root / entry.name
        target.mkdir(parents=True, exist_ok=True)
        for item in entry.iterdir():
            if item.name == source_memory.SLOT_WORLD_MODEL:
                continue
            link = target / item.name
            if not link.exists():
                link.symlink_to(item)
        digests.append(entry.name)
    for digest in digests:
        m0 = source_memory.read_m0(digest, machine_root) or {}
        paths = m0.get("observed_paths") or []
        world_model.build_source_world_model(
            digest, paths[0] if paths else "", scratch_root)
    return digests


# ── The routes ─────────────────────────────────────────────────────


def _grounding_counts(rows: list) -> dict:
    """How many rows carry what the punchlist asks an answer to carry."""
    total = len(rows)
    referenced = sum(1 for row in rows if row.get("source_reference"))
    classed = sum(1 for row in rows
                  if (row.get("provenance") or {}).get("class"))
    with_confidence = sum(
        1 for row in rows
        if (row.get("confidence") or {}).get("value") is not None)
    with_uncertainty = sum(
        1 for row in rows
        if (row.get("uncertainty") or {}).get("reason"))
    return {
        "facts": total,
        "with_a_source_reference": referenced,
        "with_an_epistemic_class": classed,
        "with_a_confidence": with_confidence,
        "with_a_named_uncertainty": with_uncertainty,
    }


def route_before(world, question: dict) -> dict:
    """The surface as it stood on main: interval, entity and ask."""
    kind = question.get("kind")
    aspect = question.get("aspect") or ("events" if kind == "point"
                                         else "what")
    coverage = world.lanes()
    present = [row["lane"] for row in coverage["present"]]
    absent = [row["lane"] for row in coverage["absent"]]
    if kind == "entity":
        answer = world.entity(question["entity"])
        facts = answer["occurrences"]
    else:
        answer = world.answer({"aspect": aspect,
                                "start": question["start"],
                                "end": question["end"]})
        facts = answer["facts"]
    rows = [strip_grounding(row) for row in facts]
    return {
        "answerable": True,
        "facts": rows,
        "lanes_absent": absent,
        "lanes_present": present,
        "grounding": _grounding_counts(rows),
    }


def _search_for(question: dict) -> dict:
    """The search a M0 case is asked as, on the extended surface."""
    case = question.get("case")
    entity = question.get("entity")
    if case in ("M0-1", "M0-2", "M0-3"):
        return {"mode": "order", "of": "action", "action": "hand_near_mouth",
                "entity": entity, "ordinal": "second"}
    if case == "M0-P":
        return {"mode": "action", "action": "hand_near_mouth",
                "entity": entity,
                "window": (question["start"], question["end"])}
    return {"mode": "action", "action": "hand_near_mouth", "entity": entity}


def route_after(world, question: dict) -> dict:
    """The extended surface: the search modes over the same records."""
    if question.get("case") == "M0-8":
        answer = world.search({"mode": "action",
                                "action": "hand_near_mouth"})
        absent = answer["lanes_consulted_absent"]
        return {
            "answerable": True,
            "facts": answer["intervals"],
            "lanes_absent": absent,
            "lanes_present": [lane for lane in answer["lanes_consulted"]
                              if lane not in absent],
            "grounding": _grounding_counts(answer["intervals"]),
            "unanswered": answer["unanswered"],
        }
    answer = world.search(_search_for(question))
    return {
        "answerable": True,
        "facts": answer["intervals"],
        "lanes_absent": answer["lanes_consulted_absent"],
        "lanes_present": [lane for lane in answer["lanes_consulted"]
                          if lane not in answer["lanes_consulted_absent"]],
        "grounding": _grounding_counts(answer["intervals"]),
        "unanswered": answer["unanswered"],
    }


ROUTES = {"before": route_before, "after": route_after}


# ── The modes ──────────────────────────────────────────────────────


MODE_QUERIES = {
    "speech": {"mode": "speech"},
    "appearance": {"mode": "appearance"},
    "action": {"mode": "action", "action": "hand_near_mouth"},
    "identity": {"mode": "identity", "entity": "face_001"},
    "relation": {"mode": "relation"},
    "order": {"mode": "order", "of": "action", "action": "hand_near_mouth",
              "ordinal": "second"},
}


def measure_modes(memory_root: Path, digests: list) -> dict:
    """Every mode, over every source of the corpus."""
    report = {}
    for mode, query in MODE_QUERIES.items():
        rows = []
        absent_sources = 0
        unanswered = []
        for digest in digests:
            world = world_model_query.World(digest, memory_root)
            answer = world.search(query)
            rows.extend(answer["intervals"])
            if answer["lanes_consulted_absent"]:
                absent_sources += 1
            unanswered.extend(answer["unanswered"])
        counts = _grounding_counts(rows)
        report[mode] = {
            "sources": len(digests),
            "sources_with_a_missing_lane": absent_sources,
            "intervals": counts["facts"],
            "with_a_source_reference": counts["with_a_source_reference"],
            "with_an_epistemic_class": counts["with_an_epistemic_class"],
            "with_a_confidence": counts["with_a_confidence"],
            "with_a_named_uncertainty": counts["with_a_named_uncertainty"],
            "unanswered_reasons": sorted(set(unanswered)),
        }
    total = sum(row["intervals"] for row in report.values())
    report["corpus"] = {
        "sources": len(digests),
        "intervals": total,
        "with_a_source_reference": sum(row["with_a_source_reference"]
                                        for row in report.values()),
        "with_an_epistemic_class": sum(row["with_an_epistemic_class"]
                                       for row in report.values()),
        "with_a_confidence": sum(row["with_a_confidence"]
                                 for row in report.values()),
        "with_a_named_uncertainty": sum(row["with_a_named_uncertainty"]
                                        for row in report.values()),
    }
    return report


# ── The reel plan ──────────────────────────────────────────────────


def _speaker_for_track(segments: list, track, start: float, end: float):
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


def measure_reel_plan(project: Path, memory_root: Path) -> dict:
    """The real reel's candidates, asked through the surface.

    The plan's own speaker attribution (`seconds_by_speaker`) is the
    ground truth: the reel builder measured it from the master
    transcript, independently of this lane.
    """
    questions = vi013.reel_plan_questions(project)
    catalog = source_memory.load_catalog(str(project))
    recorded = source_memory.load_recorded_fingerprints(str(project))
    digest_of_file = {}
    for clip in catalog:
        digest, _basis = source_memory.digest_for_clip(
            str(project), clip, recorded)
        if digest:
            digest_of_file[clip.get("source_file")] = digest
    rows = []
    for question in questions:
        share = {"before": {}, "after": {}}
        answered = {"before": False, "after": False}
        intervals = []
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
                after = world.search({"mode": "speech",
                                      "window": (float(start), float(end))})
                before = world.facts(float(start), float(end), ["speech"])
                for route, facts in (("after", after["intervals"]),
                                     ("before", before)):
                    for fact in facts:
                        if fact["kind"] != "speaking":
                            continue
                        track = fact.get("entity")
                        speaker = _speaker_for_track(segments, track,
                                                     fact["start"], fact["end"])
                        if not speaker:
                            continue
                        answered[route] = True
                        spoken = max(0.0, min(fact["end"], float(end))
                                     - max(fact["start"], float(start)))
                        share[route][speaker] = (
                            share[route].get(speaker, 0.0) + spoken)
                intervals.extend(after["intervals"])
        row = {"candidate": question["candidate"],
               "plan_share": {name: round(float(value), 4)
                              for name, value in
                              (question["plan_share"] or {}).items()}}
        for route in ("before", "after"):
            total = sum(share[route].values())
            normalised = ({name: round(value / total, 4)
                           for name, value in share[route].items()}
                          if total else {})
            plan = row["plan_share"]
            differences = [abs(normalised.get(name, 0.0) - plan.get(name, 0.0))
                           for name in set(normalised) | set(plan)]
            row[route] = {
                "answered": answered[route],
                "speaker_set_match": set(normalised) == set(plan),
                "mean_absolute_share_difference": (
                    round(sum(differences) / len(differences), 4)
                    if differences else None),
            }
        row["intervals"] = len(intervals)
        row["with_a_source_reference"] = sum(
            1 for interval in intervals if interval.get("source_reference"))
        rows.append(row)
    def _summary(route: str) -> dict:
        answered = sum(1 for row in rows if row[route]["answered"])
        matched = sum(1 for row in rows if row[route]["speaker_set_match"])
        close = sum(1 for row in rows
                    if row[route]["mean_absolute_share_difference"] is not None
                    and row[route]["mean_absolute_share_difference"] <= 0.10)
        return {"candidates": len(rows),
                "answered": f"{answered}/{len(rows)}",
                "speaker_set_match": f"{matched}/{len(rows)}",
                "speaker_share_within_0.10": f"{close}/{len(rows)}"}
    referenced = sum(row["with_a_source_reference"] for row in rows)
    intervals = sum(row["intervals"] for row in rows)
    return {
        "before": _summary("before"),
        "after": _summary("after"),
        "intervals_returned": intervals,
        "intervals_with_a_source_reference":
            f"{referenced}/{intervals}" if intervals else "0/0",
        "rows": rows,
    }


# ── The run ────────────────────────────────────────────────────────


def run(machine_root: Path, scratch_root: Path, project: Path) -> dict:
    digests = prepare_scratch(machine_root, scratch_root)
    corpus = []
    per_question = []
    for digest in digests:
        model = source_memory.read_world_model(digest, scratch_root) or {}
        corpus.append({
            "content_digest": digest,
            "duration_seconds": model.get("duration_seconds"),
            "lanes": model.get("lanes") or [],
        })
        for question in vi013.m0_questions(scratch_root, digest):
            row = {"content_digest": digest, "case": question["case"],
                   "question": question["question"],
                   "truth": question.get("truth")}
            for name, route in ROUTES.items():
                world = world_model_query.World(digest, scratch_root)
                result = route(world, question)
                verdict = vi013.score(question, result)
                payload = json.dumps({"facts": result.get("facts") or []},
                                     ensure_ascii=False, sort_keys=True)
                row[name] = {
                    "answered": verdict["answered"],
                    "correct": verdict["correct"],
                    "reason": verdict["reason"],
                    "facts": len(result.get("facts") or []),
                    "grounding": result.get("grounding") or {},
                    "context_bytes": token_measure.measure(payload).get(
                        token_measure.BYTES, 0),
                }
            per_question.append(row)

    def _summary(route: str) -> dict:
        rows = per_question
        answered = sum(1 for row in rows if row[route]["answered"])
        correct = sum(1 for row in rows if row[route]["correct"])
        facts = sum(row[route]["facts"] for row in rows)
        referenced = sum(row[route]["grounding"].get(
            "with_a_source_reference", 0) for row in rows)
        classed = sum(row[route]["grounding"].get(
            "with_an_epistemic_class", 0) for row in rows)
        confidence = sum(row[route]["grounding"].get(
            "with_a_confidence", 0) for row in rows)
        uncertainty = sum(row[route]["grounding"].get(
            "with_a_named_uncertainty", 0) for row in rows)
        bytes_per_call = (sum(row[route]["context_bytes"] for row in rows)
                          // len(rows)) if rows else 0
        per_case = {}
        for row in rows:
            case = row["case"]
            counts = per_case.setdefault(
                case, {"questions": 0, "answered": 0, "correct": 0})
            counts["questions"] += 1
            counts["answered"] += 1 if row[route]["answered"] else 0
            counts["correct"] += 1 if row[route]["correct"] else 0
        return {
            "questions": len(rows),
            "answered": f"{answered}/{len(rows)}",
            "correct": f"{correct}/{len(rows)}",
            "facts": facts,
            "facts_with_a_source_reference": f"{referenced}/{facts}",
            "facts_with_an_epistemic_class": f"{classed}/{facts}",
            "facts_with_a_confidence": f"{confidence}/{facts}",
            "facts_with_a_named_uncertainty": f"{uncertainty}/{facts}",
            "context_bytes_per_call": bytes_per_call,
            "per_case": {case: f"{counts['correct']}/{counts['questions']}"
                         for case, counts in sorted(per_case.items())},
        }

    # The same questions, asked as SEARCHES. Before this lane the
    # surface had no search mode at all: a consumer had to read the
    # canonical records itself, so none of them was answerable through
    # the interface.
    search_answered = sum(1 for row in per_question if row["after"]["answered"])
    search_surface = {
        "before": {"questions": len(per_question), "answerable": "0/0",
                   "reason": "the surface had no search mode: a consumer "
                             "had to read the canonical records itself"},
        "after": {"questions": len(per_question),
                  "answerable": f"{search_answered}/{len(per_question)}"},
    }
    search_surface["before"]["answerable"] = f"0/{len(per_question)}"

    return {
        "corpus": corpus,
        "m0_questions": per_question,
        "routes": {name: _summary(name) for name in ROUTES},
        "search_surface": search_surface,
        "modes": measure_modes(scratch_root, digests),
        "reel_plan": measure_reel_plan(project, scratch_root),
        "tokenizer": token_measure.available(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="infra003-query-interface-bench",
        description="INFRA-003: one multimodal query interface, measured "
                    "before and after.")
    parser.add_argument("--machine-root", required=True,
                        help="the machine's source memory (read only)")
    parser.add_argument("--scratch-root", required=True,
                        help="a scratch copy the world models are built in")
    parser.add_argument("--project", required=True,
                        help="a Ren-owned scratch project holding a real "
                             "reel plan")
    parser.add_argument("--out-json")
    args = parser.parse_args(argv)

    machine_root = Path(args.machine_root).expanduser()
    scratch_root = Path(args.scratch_root).expanduser()
    project = Path(args.project).expanduser()
    os.environ[source_memory.MEMORY_ROOT_ENV] = str(scratch_root)
    report = run(machine_root, scratch_root, project)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out_json:
        Path(args.out_json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
