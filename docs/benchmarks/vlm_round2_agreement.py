"""Compute blind-labeller agreement for the vep-mu-vlm round-2 packet.

Matching rule v1 (stated before scoring; also recorded in
docs/benchmarks/2026-10-16-vlm-round2-agreement.md):

- Windows pair by (media basename, start_seconds, end_seconds).
- Scene: each side's type maps through SCENE_MAP to a canonical class;
  agreement iff both canonical classes are equal. Free-text settings are
  never scored.
- Objects and actions: labels normalize (lowercase, '_' -> ' ', alphanumeric
  tokens, trailing-'s' singularization for tokens longer than 4 chars);
  similarity = |A cap B| / |A cup B|. A pair matches iff similarity >= 0.4
  AND temporal IoU >= 0.5. Greedy one-to-one matching by
  similarity * IoU, descending. Matches are agreed; unmatched rows are
  disagreements. Consensus span = mean of the two boundaries.
- Camera: agreement iff normalized labels are equal. static-vs-unknown is an
  abstention disagreement: excluded from the agreed set but listed apart
  from true conflicts.

Outputs: consensus JSON (evaluator-only row shape, agreed object/action rows
only), a human-readable agreement summary printed to stdout, and a neutral
tiebreak packet (windows + questions only, no labeller answers, no Ren
outputs) when disagreements could change a verdict.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCENE_MAP = {
    "indoor vehicle cabin": "vehicle-cabin",
    "car_interior": "vehicle-cabin",
    "outdoor paved plaza": "outdoor-plaza",
    "outdoor_plaza": "outdoor-plaza",
    "indoor studio interview set": "indoor-studio",
    "indoor_studio": "indoor-studio",
    "unknown": "unknown",
}

PACKET_QUESTIONS = {
    "scene": "What scene type and physical setting are visibly present? "
    "Use only directly visible evidence; return `unknown` when unsupported.",
    "objects": "Which objects are clearly visible, and during what source-time "
    "intervals are they visible? Include one evidence frame time per object.",
    "actions": "Which distinct physical actions are visible, and what are their "
    "source start and end times? Include one evidence frame time per action.",
    "camera_motion": "What camera motion is visibly present, and what are its "
    "source start and end times? Return `static` when no camera movement is "
    "visible and `unknown` when it cannot be determined.",
}


def _norm(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words}


def _similarity(left: str, right: str) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _iou(s1: float, e1: float, s2: float, e2: float) -> float:
    overlap = max(0.0, min(e1, e2) - max(s1, s2))
    union = (e1 - s1) + (e2 - s2) - overlap
    return overlap / union if union > 0 else 0.0


def _window_key(row: dict) -> tuple:
    return (
        Path(row["media_path"]).name,
        float(row["start_seconds"]),
        float(row["end_seconds"]),
    )


def _is_abstention(row: dict) -> bool:
    return _norm(row.get("label", "")) in (set(), {"unknown"})


def _match_lists(
    left: list[dict], right: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    pairs = []
    for i, lhs in enumerate(left):
        for j, rhs in enumerate(right):
            sim = _similarity(lhs["label"], rhs["label"])
            iou = _iou(
                lhs["start_seconds"],
                lhs["end_seconds"],
                rhs["start_seconds"],
                rhs["end_seconds"],
            )
            if sim >= 0.4 and iou >= 0.5:
                pairs.append((sim * iou, sim, iou, i, j))
    pairs.sort(reverse=True)
    used_l, used_r, matched = set(), set(), []
    for score, sim, iou, i, j in pairs:
        if i in used_l or j in used_r:
            continue
        used_l.add(i)
        used_r.add(j)
        matched.append(
            {"score": score, "similarity": sim, "iou": iou, "a": left[i],
             "b": right[j]}
        )
    return (
        matched,
        [r for i, r in enumerate(left) if i not in used_l],
        [r for j, r in enumerate(right) if j not in used_r],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True, type=Path)
    parser.add_argument("--b", required=True, type=Path)
    parser.add_argument("--consensus-out", required=True, type=Path)
    parser.add_argument("--tiebreak-out", required=True, type=Path)
    parser.add_argument("--tiebreak", type=Path, default=None,
                        help="third-labeller labels.jsonl; adjudicates disputes")
    args = parser.parse_args()

    a = json.loads(args.a.read_text(encoding="utf-8"))
    b = json.loads(args.b.read_text(encoding="utf-8"))
    a_by_window = {_window_key(r): r for r in a["labels"]}
    b_by_window = {_window_key(r): r for r in b["labels"]}
    assert set(a_by_window) == set(b_by_window), "window sets differ"
    path_by_window = {_window_key(r): r["media_path"] for r in a["labels"]}
    t_by_window: dict[tuple, dict] = {}
    t_id = None
    if args.tiebreak is not None:
        for line in args.tiebreak.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                t_by_window[_window_key(row)] = row
        t_id = json.loads(
            args.tiebreak.read_text(encoding="utf-8").splitlines()[0]
        )["labeller_id"]
    tb_counts = {"confirmed_a": 0, "confirmed_b": 0, "unresolved": 0,
                 "tiebreak_only": 0}
    tb_only_labels: list[str] = []

    consensus_rows: list[dict] = []
    stats = {"scene": [0, 0], "object": [0, 0], "action": [0, 0],
             "camera": [0, 0]}
    abstentions_by_field = {"object": 0, "action": 0}
    disputes: list[dict] = []
    abstentions: list[dict] = []

    for key in sorted(a_by_window):
        take = Path(key[0]).stem
        ra, rb = a_by_window[key], b_by_window[key]
        ca = SCENE_MAP.get(ra["scene"]["type"], "UNMAPPED:" + ra["scene"]["type"])
        cb = SCENE_MAP.get(rb["scene"]["type"], "UNMAPPED:" + rb["scene"]["type"])
        stats["scene"][1] += 1
        if ca == cb and not ca.startswith("UNMAPPED"):
            stats["scene"][0] += 1
        else:
            disputes.append({"window": key, "field": "scene"})

        for field, kind, name_key in (
            ("objects", "object", "name"),
            ("actions", "action", "verb"),
        ):
            claims_a = [r for r in ra[field] if not _is_abstention(r)]
            claims_b = [r for r in rb[field] if not _is_abstention(r)]
            abst_a = len(ra[field]) - len(claims_a)
            abst_b = len(rb[field]) - len(claims_b)
            matched, only_a, only_b = _match_lists(claims_a, claims_b)
            stats[kind][1] += len(matched) + len(only_a) + len(only_b)
            stats[kind][0] += len(matched)
            abstentions_by_field[kind] += abst_a + abst_b
            for m in matched:
                row_a, row_b = m["a"], m["b"]
                consensus_rows.append(
                    {
                        "take": take,
                        "split": "calibration",
                        "kind": kind,
                        name_key: row_a["label"],
                        "start": round(
                            (row_a["start_seconds"] + row_b["start_seconds"]) / 2, 3
                        ),
                        "end": round(
                            (row_a["end_seconds"] + row_b["end_seconds"]) / 2, 3
                        ),
                        "consensus_method": "mean of two agent boundaries",
                        "agreement": {
                            "labellers": [a["labeller_id"], b["labeller_id"]],
                            "iou": round(m["iou"], 4),
                            "similarity": round(m["similarity"], 4),
                            "a": {
                                kind: row_a["label"],
                                "start": row_a["start_seconds"],
                                "end": row_a["end_seconds"],
                            },
                            "b": {
                                kind: row_b["label"],
                                "start": row_b["start_seconds"],
                                "end": row_b["end_seconds"],
                            },
                        },
                    }
                )
            for row in only_a + only_b:
                disputes.append(
                    {"window": key, "field": field, "unmatched": row["label"]}
                )
            if t_id is not None and key in t_by_window:
                t_row = t_by_window[key]
                t_claims = [r for r in t_row.get(field, [])
                            if not _is_abstention(r)]
                for side, claimant, claim_id in (
                    (only_a, "a", a["labeller_id"]),
                    (only_b, "b", b["labeller_id"]),
                ):
                    confirmed, _, _ = _match_lists(side, t_claims)
                    for m in confirmed:
                        claim, t_hit = m["a"], m["b"]
                        consensus_rows.append(
                            {
                                "take": take,
                                "split": "calibration",
                                "kind": kind,
                                name_key: claim["label"],
                                "start": round(
                                    (claim["start_seconds"]
                                     + t_hit["start_seconds"]) / 2, 3),
                                "end": round(
                                    (claim["end_seconds"]
                                     + t_hit["end_seconds"]) / 2, 3),
                                "consensus_method": "tiebreak-confirmed: mean "
                                "of labeller and tiebreaker boundaries",
                                "agreement": {
                                    "labellers": [claim_id, t_id],
                                    "iou": round(m["iou"], 4),
                                    "similarity": round(m["similarity"], 4),
                                    "claim": {
                                        kind: claim["label"],
                                        "start": claim["start_seconds"],
                                        "end": claim["end_seconds"],
                                    },
                                    "tiebreak": {
                                        kind: t_hit["label"],
                                        "start": t_hit["start_seconds"],
                                        "end": t_hit["end_seconds"],
                                    },
                                },
                            }
                        )
                        tb_counts["confirmed_a" if side is only_a
                                  else "confirmed_b"] += 1
                    n_unconfirmed = len(side) - len(confirmed)
                    tb_counts["unresolved"] += n_unconfirmed
                _, unmatched_t, _ = _match_lists(
                    t_claims, claims_a + claims_b)
                tb_counts["tiebreak_only"] += len(unmatched_t)
                tb_only_labels.extend(
                    f"{take} {kind} {r['label']} "
                    f"[{r['start_seconds']},{r['end_seconds']})"
                    for r in unmatched_t
                )

        matched_c, only_ca, only_cb = _match_lists(
            ra["camera_motion"], rb["camera_motion"]
        )
        stats["camera"][1] += len(matched_c) + len(only_ca) + len(only_cb)
        stats["camera"][0] += len(matched_c)
        for row in only_ca + only_cb:
            labels = {row["label"], *(
                r["label"] for r in (only_ca + only_cb) if r is not row
            )}
            entry = {"window": key, "field": "camera_motion"}
            if labels <= {"static", "unknown"}:
                abstentions.append(entry)
            else:
                disputes.append(entry)

    print("AGREEMENT (matching rule v1; labellers: %s [%s] vs %s [%s])" % (
        a["labeller_id"], a["model_family"],
        b["labeller_id"], b["model_family"]))
    for field, (hit, total) in stats.items():
        extra = ""
        if field in abstentions_by_field and abstentions_by_field[field]:
            extra = f" (+{abstentions_by_field[field]} abstained unknown rows)"
        print(f"  {field}: {hit}/{total} agreed{extra}")
    print(f"consensus rows: {len(consensus_rows)}")
    print(f"disputed items: {len(disputes)}; abstentions: {len(abstentions)}")
    if t_id is not None:
        print(f"tiebreak ({t_id}): confirmed_a={tb_counts['confirmed_a']} "
              f"confirmed_b={tb_counts['confirmed_b']} "
              f"unresolved={tb_counts['unresolved']} "
              f"tiebreak_only={tb_counts['tiebreak_only']}")
        for label in tb_only_labels:
            print(f"  tiebreak-only: {label}")

    consensus = {
        "schema": "vlm-round2-consensus-v1",
        "split": "calibration",
        "label_type": "blind agent consensus; not human/gold labels",
        "labeller_models": [a["model_family"], b["model_family"]],
        "match_policy": "same window; label token-overlap similarity >= 0.4; "
        "temporal IoU >= 0.5; greedy one-to-one by similarity*IoU",
        "boundary_policy": "mean of the two agent boundaries for matched "
        "pairs; no invented labels for unmatched rows",
        "scene_map": SCENE_MAP,
        "labels": consensus_rows,
    }
    if t_id is not None:
        consensus["tiebreak"] = {
            "labeller_id": t_id,
            "counts": tb_counts,
            "tiebreak_only_labels": tb_only_labels,
            "policy": "a disputed claim joins the consensus iff a tiebreak "
            "row matches it under the same rule; tiebreak-only rows are "
            "recorded, never scored",
        }
    args.consensus_out.write_text(
        json.dumps(consensus, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = ["# Tiebreak packet for vep-mu-vlm round-2 disagreements", ""]
    lines.append(
        "Label each item below independently. Do not open any other "
        "labeller's directory, Ren outputs, or indexes."
    )
    lines.append("")
    by_window: dict[tuple, list[dict]] = {}
    for d in disputes:
        by_window.setdefault(d["window"], []).append(d)
    for key in sorted(by_window):
        lines.append(f"## {path_by_window[key]} [{key[1]}, {key[2]})")
        lines.append("")
        for field in ("scene", "objects", "actions", "camera_motion"):
            if any(
                d["field"] in (field, field.rstrip("s") + "s", field + "s")
                or d["field"].rstrip("s") == field.rstrip("s")
                for d in by_window[key]
            ):
                lines.append(f"- {field}: {PACKET_QUESTIONS[field]}")
        lines.append("")
    lines.append("## Return format")
    lines.append("")
    lines.append(
        "One JSON document with the shape from the calibration blind-label "
        "packet (labeller_id, model_family, labels[] with media_path, "
        "start_seconds, end_seconds, scene, objects[], actions[], "
        "camera_motion[], each with label, start_seconds, end_seconds and "
        "evidence_frame_seconds)."
    )
    args.tiebreak_out.write_text("\n".join(lines), encoding="utf-8")
    print(f"WROTE {args.consensus_out} + {args.tiebreak_out}")


if __name__ == "__main__":
    main()
