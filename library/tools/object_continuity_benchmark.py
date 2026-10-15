"""Score held-out object/person identity and continuity annotations.

The scorer is intentionally independent of the detector/tracker. Predictions
are supplied as observations so the same frozen labels can score any producer.
All boxes are normalized ``[x0, y0, x1, y1]`` coordinates and frame keys are
``(clip_id, frame_id)``. Occluded and absent ground-truth observations are
excluded from box matching, but remain explicit in the annotation and report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

SCHEMA = "object-continuity-v1"
VISIBILITY = {"visible", "occluded", "absent"}


def _box(row: dict[str, Any]) -> list[float]:
    values = [float(value) for value in row["box"]]
    if (len(values) != 4 or not all(math.isfinite(v) for v in values)
            or not (0 <= values[0] < values[2] <= 1)
            or not (0 <= values[1] < values[3] <= 1)):
        raise ValueError(f"invalid normalized box: {row['box']!r}")
    return values


def _validate(rows: list[dict[str, Any]], *, ground_truth: bool) -> None:
    seen: set[tuple[str, str, str]] = set()
    entity_classes: dict[str, str] = {}
    for row in rows:
        for key in ("clip_id", "frame_id", "entity_id", "class"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"observation requires non-empty {key}")
        if ground_truth and "time_s" not in row:
            raise ValueError("ground-truth observation requires time_s")
        if "time_s" in row:
            time_s = float(row["time_s"])
            if not math.isfinite(time_s) or time_s < 0:
                raise ValueError("observation time_s must be finite and non-negative")
        if ground_truth:
            visibility = row["visibility"]
            if visibility not in VISIBILITY:
                raise ValueError(f"unsupported visibility: {visibility!r}")
            if visibility == "visible" or row.get("box") is not None:
                _box(row)
        elif row.get("status", "observed") == "observed":
            _box(row)
        elif row.get("status") != "abstained":
            raise ValueError("prediction status must be observed or abstained")
        elif row.get("box") is not None:
            raise ValueError("abstained prediction must not include a box")
        if not ground_truth and row.get("cross_clip_identity") not in {
            None, "linked", "abstained"
        }:
            raise ValueError("cross_clip_identity must be linked or abstained")
        identity = (row["clip_id"], row["frame_id"], row["entity_id"])
        if identity in seen:
            raise ValueError(f"duplicate observation: {identity}")
        seen.add(identity)
        entity_class = entity_classes.setdefault(row["entity_id"], row["class"])
        if entity_class != row["class"]:
            raise ValueError(f"entity changes class: {row['entity_id']}")


def _iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


def _best_matches(gt: list[dict[str, Any]], pred: list[dict[str, Any]],
                  threshold: float) -> list[tuple[int, int]]:
    """Maximum-cardinality IoU matching, deterministic on ties."""
    candidates = sorted(
        ((_iou(_box(g), _box(p)), gi, pi)
         for gi, g in enumerate(gt) for pi, p in enumerate(pred)
         if g["class"] == p["class"]), reverse=True,
    )
    neighbors: dict[int, list[int]] = defaultdict(list)
    for score, gi, pi in candidates:
        if score >= threshold:
            neighbors[gi].append(pi)
    matched_to_gt: dict[int, int] = {}

    def augment(gi: int, visited: set[int]) -> bool:
        for pi in neighbors[gi]:
            if pi in visited:
                continue
            visited.add(pi)
            if pi not in matched_to_gt or augment(matched_to_gt[pi], visited):
                matched_to_gt[pi] = gi
                return True
        return False

    for gi in range(len(gt)):
        augment(gi, set())
    return sorted((gi, pi) for pi, gi in matched_to_gt.items())


def _maximum_assignment(weights: dict[tuple[str, str], int]) -> int:
    """Exact maximum weight matching for identity counts via augmenting paths."""
    left = sorted({left for left, _ in weights})
    right = sorted({right for _, right in weights})
    if not left or not right:
        return 0
    # Successive shortest augmenting path on a small bipartite graph. Stop
    # when another assignment would reduce total matched observations.
    n, m = len(left), len(right)
    source, sink = n + m, n + m + 1
    graph: list[list[list[int]]] = [[] for _ in range(n + m + 2)]

    def edge(a: int, b: int, cap: int, cost: int) -> None:
        graph[a].append([b, cap, cost, len(graph[b])])
        graph[b].append([a, 0, -cost, len(graph[a]) - 1])

    for i in range(n):
        edge(source, i, 1, 0)
    for j in range(m):
        edge(n + j, sink, 1, 0)
    for (a, b), count in weights.items():
        if count:
            edge(left.index(a), n + right.index(b), 1, -count)
    total = 0
    while True:
        dist = [math.inf] * len(graph)
        prev: list[tuple[int, int] | None] = [None] * len(graph)
        dist[source] = 0
        # Bellman-Ford handles reverse edges introduced by prior assignments.
        for _ in range(len(graph) - 1):
            changed = False
            for u, edges in enumerate(graph):
                if dist[u] == math.inf:
                    continue
                for ei, e in enumerate(edges):
                    v, cap, cost, _ = e
                    if cap and dist[v] > dist[u] + cost:
                        dist[v] = dist[u] + cost
                        prev[v] = (u, ei)
                        changed = True
            if not changed:
                break
        if prev[sink] is None:
            break
        if dist[sink] >= 0:
            break
        node = sink
        while node != source:
            u, ei = prev[node]  # type: ignore[misc]
            e = graph[u][ei]
            e[1] -= 1
            graph[node][e[3]][1] += 1
            node = u
        total -= int(dist[sink])
    return total


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score(ground_truth: dict[str, Any], predictions: dict[str, Any], *,
          iou_threshold: float = 0.5) -> dict[str, Any]:
    """Return detection, IDF1, fragmentation, state and cross-clip metrics."""
    if ground_truth.get("schema") != SCHEMA:
        raise ValueError(f"ground-truth schema must be {SCHEMA}")
    if (ground_truth.get("split") != "held_out"
            or ground_truth.get("tuning_access") is not False):
        raise ValueError("identity labels must be held_out and unavailable to tuning")
    for key in ("annotation_id", "label_set_sha256", "annotator_id",
                "review_status", "access_audit_id"):
        if not isinstance(ground_truth.get(key), str) or not ground_truth[key]:
            raise ValueError(f"held-out annotation envelope requires {key}")
    source_ids = ground_truth.get("source_ids")
    if (not isinstance(source_ids, list) or not source_ids
            or not all(isinstance(source_id, str) and source_id for source_id in source_ids)):
        raise ValueError("held-out annotation envelope requires source_ids")
    digest = ground_truth["label_set_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("label_set_sha256 must be a lowercase SHA-256 digest")
    gt_rows = ground_truth["observations"]
    label_payload = {"observations": gt_rows,
                     "hard_cases": ground_truth.get("hard_cases", [])}
    actual_digest = hashlib.sha256(json.dumps(
        label_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    if actual_digest != digest:
        raise ValueError("label_set_sha256 does not match observations")
    pred_rows = predictions["observations"]
    _validate(gt_rows, ground_truth=True)
    _validate(pred_rows, ground_truth=False)
    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be in (0, 1]")

    gt_by_frame: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pred_by_frame: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in gt_rows:
        if row["visibility"] == "visible":
            gt_by_frame[(row["clip_id"], row["frame_id"])].append(row)
    for row in pred_rows:
        if row.get("status", "observed") == "observed":
            pred_by_frame[(row["clip_id"], row["frame_id"])].append(row)

    matched: dict[tuple[str, str, str], dict[str, Any]] = {}
    counts_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    identity_weights: dict[str, dict[tuple[str, str], int]] = defaultdict(Counter)
    for frame in sorted(set(gt_by_frame) | set(pred_by_frame)):
        gs, ps = gt_by_frame[frame], pred_by_frame[frame]
        pairs = _best_matches(gs, ps, iou_threshold)
        hit_g, hit_p = set(), set()
        for gi, pi in pairs:
            g, p = gs[gi], ps[pi]
            hit_g.add(gi)
            hit_p.add(pi)
            matched[(g["clip_id"], g["frame_id"], g["entity_id"])] = p
            identity_weights[g["class"]][(g["entity_id"], p["entity_id"])] += 1
        all_classes = {r["class"] for r in gs + ps}
        for klass in all_classes:
            class_g = {i for i, r in enumerate(gs) if r["class"] == klass}
            class_p = {i for i, r in enumerate(ps) if r["class"] == klass}
            counts_by_class[klass]["gt"] += len(class_g)
            counts_by_class[klass]["pred"] += len(class_p)
            counts_by_class[klass]["tp"] += len(class_g & hit_g)
            counts_by_class[klass]["fp"] += len(class_p - hit_p)
            counts_by_class[klass]["fn"] += len(class_g - hit_g)

    gt_tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    gt_tracks_by_clip: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in gt_rows:
        gt_tracks[row["entity_id"]].append(row)
        gt_tracks_by_clip[row["entity_id"]][row["clip_id"]].append(row)
    fragmentation_by_class: Counter[str] = Counter()
    for entity, clips in gt_tracks_by_clip.items():
        for rows in clips.values():
            rows.sort(key=lambda row: (row["time_s"], row["frame_id"]))
            visible = [r for r in rows if r["visibility"] == "visible"]
            was_matched = False
            missed_after_match = False
            for row in visible:
                is_matched = (row["clip_id"], row["frame_id"], entity) in matched
                if not is_matched and was_matched:
                    missed_after_match = True
                elif is_matched and missed_after_match:
                    fragmentation_by_class[row["class"]] += 1
                    missed_after_match = False
                was_matched = is_matched

    transition: dict[str, Counter[str]] = defaultdict(Counter)
    for entity, rows in gt_tracks.items():
        rows.sort(key=lambda row: (row["clip_id"], row["time_s"], row["frame_id"]))
        visible = [r for r in rows if r["visibility"] == "visible" and r.get("state")]
        for before, after in pairwise(visible):
            if before["clip_id"] != after["clip_id"] or before["state"] == after["state"]:
                continue
            first = matched.get((before["clip_id"], before["frame_id"], entity))
            second = matched.get((after["clip_id"], after["frame_id"], entity))
            if first is None or second is None or first.get("state") is None or second.get("state") is None:
                transition[before["class"]]["unscored"] += 1
                continue
            transition[before["class"]]["total"] += 1
            transition[before["class"]]["correct"] += int(
                first["state"] == before["state"] and second["state"] == after["state"]
            )

    cross_clip: dict[str, Counter[str]] = defaultdict(Counter)
    for entity, rows in gt_tracks.items():
        clips = sorted({r["clip_id"] for r in rows if r["visibility"] == "visible"})
        if len(clips) < 2:
            continue
        by_clip: dict[str, Counter[str]] = defaultdict(Counter)
        clip_link_status: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            pred = matched.get((row["clip_id"], row["frame_id"], entity))
            if pred is not None:
                by_clip[row["clip_id"]][pred["entity_id"]] += 1
                status = pred.get("cross_clip_identity")
                if status:
                    clip_link_status[row["clip_id"]].add(status)
        winners = [counts.most_common(1)[0][0] if counts else None for clip, counts in
                   ((clip, by_clip[clip]) for clip in clips)]
        link_abstained = any(
            "abstained" in clip_link_status[clip] for clip in clips
        )
        counts = cross_clip[rows[0]["class"]]
        counts["entities"] += 1
        counts["abstained"] += int(link_abstained or any(winner is None for winner in winners))
        counts["correct"] += int(not link_abstained and all(winner is not None for winner in winners)
                                  and len(set(winners)) == 1)
        counts["incorrect"] += int(not link_abstained and all(winner is not None for winner in winners)
                                    and len(set(winners)) != 1)

    classes = sorted(set(counts_by_class) | set(identity_weights) |
                     {row["class"] for row in gt_rows + pred_rows} |
                     set(fragmentation_by_class) | set(transition) | set(cross_clip))
    visibility_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    abstentions_by_class: Counter[str] = Counter()
    for row in gt_rows:
        visibility_by_class[row["class"]][row["visibility"]] += 1
    for row in pred_rows:
        abstentions_by_class[row["class"]] += int(row.get("status") == "abstained")
    observations_by_key = {
        (row["clip_id"], row["frame_id"], row["entity_id"]): row
        for row in gt_rows
    }
    occlusion_cases = 0
    lookalike_pairs = 0
    for case in ground_truth.get("hard_cases", []):
        if case.get("kind") == "occlusion":
            row = observations_by_key.get(
                (case["clip_id"], case["frame_id"], case["entity_id"])
            )
            if row is None or row["visibility"] != "occluded":
                raise ValueError("occlusion hard case must name an occluded label")
            occlusion_cases += 1
        elif case.get("kind") == "lookalike_pair":
            entity_ids = case["entity_ids"]
            pair = [observations_by_key.get(
                (case["clip_id"], case["frame_id"], entity_id)
            ) for entity_id in entity_ids]
            if (len(entity_ids) < 2 or len(set(entity_ids)) != len(entity_ids)
                    or any(row is None or row["visibility"] != "visible" for row in pair)
                    or len({row["class"] for row in pair if row is not None}) != 1):
                raise ValueError("lookalike case must name visible same-class instances")
            lookalike_pairs += 1
        else:
            raise ValueError(f"unsupported hard case: {case.get('kind')!r}")
    by_class = {}
    for klass in classes:
        c = counts_by_class[klass]
        idtp = _maximum_assignment(identity_weights[klass])
        idfp, idfn = c["pred"] - idtp, c["gt"] - idtp
        tr = transition[klass]
        ci = cross_clip[klass]
        by_class[klass] = {
            "visible_gt_observations": c["gt"], "predicted_observations": c["pred"],
            "visibility_counts": dict(visibility_by_class[klass]),
            "detection_precision": _ratio(c["tp"], c["pred"]),
            "detection_recall": _ratio(c["tp"], c["gt"]),
            "idf1": _ratio(2 * idtp, 2 * idtp + idfp + idfn),
            "identity_true_positives": idtp,
            "identity_false_positives": idfp,
            "identity_false_negatives": idfn,
            "identity_switches": sum(
                1 for entity, clips in gt_tracks_by_clip.items()
                for rows in clips.values()
                if rows[0]["class"] == klass
                for a, b in pairwise(
                    [r for r in sorted(rows, key=lambda x: (x["time_s"], x["frame_id"]))
                     if r["visibility"] == "visible"]
                )
                if (a["clip_id"], a["frame_id"], entity) in matched
                and (b["clip_id"], b["frame_id"], entity) in matched
                and matched[(a["clip_id"], a["frame_id"], entity)]["entity_id"]
                != matched[(b["clip_id"], b["frame_id"], entity)]["entity_id"]
            ),
            "fragmentations": fragmentation_by_class[klass],
            "state_transition_accuracy": _ratio(tr["correct"], tr["total"]),
            "state_transitions_scored": tr["total"],
            "state_transitions_unscored": tr["unscored"],
            "cross_clip_identity_accuracy": _ratio(ci["correct"], ci["correct"] + ci["incorrect"]),
            "cross_clip_entities": ci["entities"],
            "cross_clip_entities_scored": ci["correct"] + ci["incorrect"],
            "cross_clip_abstentions": ci["abstained"],
            "prediction_abstentions": abstentions_by_class[klass],
        }
    unobserved = sum(abstentions_by_class.values())
    return {
        "schema": "object-continuity-score-v1",
        "iou_threshold": iou_threshold,
        "visibility_counts": dict(Counter(row["visibility"] for row in gt_rows)),
        "prediction_abstentions": unobserved,
        "hard_case_coverage": {
            "occlusion_cases": occlusion_cases,
            "lookalike_pairs": lookalike_pairs,
            "complete": occlusion_cases > 0 and lookalike_pairs > 0,
        },
        "by_class": by_class,
    }


def run(annotation_path: Path, prediction_path: Path, output_path: Path,
        audit_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    annotations = json.loads(annotation_path.read_text(encoding="utf-8"))
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    annotation_resolved = annotation_path.resolve()
    audit_resolved = audit_path.resolve()
    worktree = Path.cwd().resolve()
    if annotation_resolved.is_relative_to(worktree) or audit_resolved.is_relative_to(worktree):
        raise ValueError("held-out labels and their access audit must stay outside the tuning worktree")
    if (audit.get("audit_id") != annotations.get("access_audit_id")
            or audit.get("label_set_sha256") != annotations.get("label_set_sha256")
            or audit.get("tuning_access") is not False
            or audit.get("access_policy") != "sealed_held_out_no_tuning"
            or Path(audit.get("custody_path", "")).resolve() != annotation_resolved
            or not audit.get("custodian")
            or not audit.get("sealed_at")):
        raise ValueError("access audit does not verify the sealed held-out labels")
    producer = predictions["producer"]
    for key in ("name", "revision", "threshold", "sampling_schedule"):
        if key not in producer:
            raise ValueError(f"prediction envelope missing producer.{key}")
    if any(row.get("status", "observed") == "observed"
           and row.get("cross_clip_identity") not in {"linked", "abstained"}
           for row in predictions["observations"]):
        raise ValueError("each observed prediction must report cross-clip identity or abstention")
    cost = predictions["cost"]
    for key in ("scope", "wall_seconds", "peak_rss_mb", "frames_requested",
                "frames_processed", "failures"):
        if key not in cost:
            raise ValueError(f"prediction cost record missing {key}")
    numeric_measurements = (float(cost["wall_seconds"]), float(cost["peak_rss_mb"]))
    frame_counts = (cost["frames_requested"], cost["frames_processed"])
    if (not all(math.isfinite(value) and value >= 0 for value in numeric_measurements)
            or not all(isinstance(value, int) and value >= 0 for value in frame_counts)
            or cost["frames_processed"] > cost["frames_requested"]):
        raise ValueError("invalid prediction cost measurement")
    result = score(annotations, predictions)
    result["annotation"] = {
        "annotation_id": annotations["annotation_id"],
        "split": annotations["split"],
        "tuning_access": annotations["tuning_access"],
        "label_set_sha256": annotations["label_set_sha256"],
        "source_ids": annotations["source_ids"],
        "access_audit_id": annotations["access_audit_id"],
        "access_audit_sha256": hashlib.sha256(
            audit_path.read_bytes()
        ).hexdigest(),
        "review_status": annotations["review_status"],
    }
    result["producer"] = producer
    result["cost"] = {
        "measurement_scope": cost["scope"],
        "wall_seconds": cost["wall_seconds"],
        "peak_rss_mb": cost["peak_rss_mb"],
        "frames_requested": cost["frames_requested"],
        "frames_processed": cost["frames_processed"],
        "failures": cost["failures"],
        "model_pass_seconds_per_processed_frame": (
            cost["wall_seconds"] / cost["frames_processed"]
            if cost["frames_processed"] else None
        ),
    }
    result["scorer_wall_seconds"] = time.perf_counter() - started
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotations", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.annotations, args.predictions, args.out, args.audit),
                     indent=2))


if __name__ == "__main__":
    main()
