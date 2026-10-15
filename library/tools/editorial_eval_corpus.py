"""Rights and split checks for the editorial evaluation corpus.

This module is data validation only. It never discovers project media or
loads model resources; callers provide the manifest and source root explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

SPLITS = {"training", "calibration", "held_out"}
REQUIRED_USES = {"benchmarking"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate(manifest: dict, source_root: Path | None = None,
             verify_hashes: bool = False) -> dict:
    """Validate rights, split isolation, label access, and optionally source bytes."""
    errors: list[str] = []
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        return {"valid": False, "errors": ["sources must be a non-empty list"]}

    ids: set[str] = set()
    group_splits: dict[str, set[str]] = defaultdict(set)
    scene_splits: dict[str, set[str]] = defaultdict(set)
    annotation_ids: set[str] = set()
    split_counts = {split: 0 for split in sorted(SPLITS)}
    for index, source in enumerate(sources):
        label = source.get("source_id", f"sources[{index}]")
        source_id = source.get("source_id")
        if not source_id or source_id in ids:
            errors.append(f"{label}: source_id is missing or duplicated")
        ids.add(source_id)
        split = source.get("split")
        if split not in SPLITS:
            errors.append(f"{label}: split must be one of {sorted(SPLITS)}")
        else:
            split_counts[split] += 1
        group = source.get("leakage_group_id")
        if not group:
            errors.append(f"{label}: leakage_group_id is required")
        elif split in SPLITS:
            group_splits[group].add(split)
        scene_id = source.get("scene_id")
        if scene_id and split in SPLITS:
            scene_splits[scene_id].add(split)

        digest = source.get("sha256", "")
        if not HASH_RE.fullmatch(digest):
            errors.append(f"{label}: sha256 must be 64 lowercase hex characters")
        size = source.get("size_bytes")
        if not isinstance(size, int) or size <= 0:
            errors.append(f"{label}: size_bytes must be a positive integer")
        rights = source.get("rights", {})
        if rights.get("status") != "cleared" or not rights.get("basis"):
            errors.append(f"{label}: rights clearance evidence is required")
        permitted = set(rights.get("permitted_uses", []))
        required = REQUIRED_USES | ({"training"} if split == "training" else set())
        if not required.issubset(permitted):
            errors.append(f"{label}: permitted_uses must include {sorted(required)}")

        annotation = source.get("annotation", {})
        annotation_id = annotation.get("annotation_id")
        if annotation_id:
            if annotation_id in annotation_ids:
                errors.append(f"{label}: annotation_id crosses or duplicates a source")
            annotation_ids.add(annotation_id)
        if annotation.get("schema") != manifest.get("annotation_schema", {}).get("id"):
            errors.append(f"{label}: annotation schema reference is missing or unknown")
        if split == "held_out":
            if annotation.get("state") in {"not_started", "not_created"}:
                if annotation.get("access") != "not_created":
                    errors.append(f"{label}: uncreated held-out labels must not have a payload")
            elif annotation.get("access") != "sealed_custodian_only":
                errors.append(f"{label}: held-out labels must be sealed from tuning")
        if split == "held_out" and annotation.get("tuning_access") is not False:
            errors.append(f"{label}: held-out labels must set tuning_access=false")

        if verify_hashes:
            if source_root is None:
                errors.append("source_root is required when verifying hashes")
                break
            relative = Path(source.get("path", ""))
            if relative.is_absolute() or ".." in relative.parts:
                errors.append(f"{label}: path must stay inside the supplied source root")
                continue
            path = source_root / relative
            try:
                if path.stat().st_size != size:
                    errors.append(f"{label}: source size does not match manifest")
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != digest:
                    errors.append(f"{label}: source SHA-256 does not match manifest")
            except OSError as exc:
                errors.append(f"{label}: cannot read source for hash verification: {exc}")

    for group, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            errors.append(f"leakage group {group} crosses splits: {sorted(splits)}")
    for scene_id, splits in sorted(scene_splits.items()):
        if len(splits) > 1:
            errors.append(f"scene {scene_id} crosses splits: {sorted(splits)}")
    if any(split_counts[split] == 0 for split in SPLITS):
        errors.append("training, calibration, and held_out must all contain sources")
    return {"valid": not errors, "errors": errors, "source_count": len(sources),
            "split_source_counts": split_counts}


def balance_report(manifest: dict) -> dict:
    """Summarize declared categories and held-out label visibility."""
    by_split: dict[str, dict[str, int]] = {split: defaultdict(int) for split in SPLITS}
    for source in manifest["sources"]:
        for task in source["tasks"]:
            by_split[source["split"]][task] += 1
    return {
        "source_count": len(manifest["sources"]),
        "duration_minutes": round(sum(row["duration_seconds"]
                                       for row in manifest["sources"]) / 60, 2),
        "genre_counts": dict(sorted(_count(manifest["sources"], "genre").items())),
        "task_counts_by_split": {
            split: dict(sorted(by_split[split].items())) for split in sorted(SPLITS)},
        "coverage": manifest["coverage"],
        "held_out_access": manifest["held_out_access"],
    }


def _count(sources: list[dict], key: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for row in sources:
        result[row[key]] += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    result = validate(manifest, args.source_root, args.verify_hashes)
    print(json.dumps({"validation": result, "report": balance_report(manifest)},
                     indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
