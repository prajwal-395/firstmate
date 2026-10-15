import copy
import hashlib
from pathlib import Path

import pytest

from library.tools import editorial_eval_corpus

MANIFEST = Path("docs/benchmarks/2026-10-15-editorial-eval-corpus.json")


def test_editorial_corpus_has_disjoint_rights_cleared_splits_and_access_report():
    manifest = editorial_eval_corpus.load_manifest(MANIFEST)

    result = editorial_eval_corpus.validate(manifest)
    report = editorial_eval_corpus.balance_report(manifest)

    assert result["valid"], result["errors"]
    assert result["split_source_counts"] == {
        "training": 3,
        "calibration": 19,
        "held_out": 2,
    }
    assert report["source_count"] == 24
    assert report["duration_minutes"] == pytest.approx(195.03, abs=0.01)
    assert report["genre_counts"] == {"podcast_interview": 7, "vlog_walk_and_talk": 17}
    assert report["held_out_access"]["tuning_access"] is False
    assert report["held_out_access"]["label_payloads_in_repository"] is False
    assert {
        row["task"] for row in report["coverage"] if row["status"] == "present"
    } >= {"non_podcast_scenes", "outdoor_vlog", "street"}
    assert {row["task"] for row in report["coverage"] if row["status"] == "absent"} >= {
        "classroom",
        "product",
        "b_roll",
        "noisy_speech",
        "ambience",
        "music",
    }


def test_source_hash_and_size_are_verified_against_explicit_root(tmp_path):
    payload = b"read-only media fixture"
    for name in ("training", "calibration", "held_out"):
        (tmp_path / f"clip-{name}.mov").write_bytes(payload)
    manifest = _manifest_with_source(
        sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))

    result = editorial_eval_corpus.validate(manifest, tmp_path, verify_hashes=True)

    assert result["valid"], result["errors"]


@pytest.mark.parametrize("mutation,expected", [
    ("rights", "permitted_uses"),
    ("group", "crosses splits"),
    ("held_out_labels", "sealed from tuning"),
    ("unsafe_path", "must stay inside"),
])
def test_invalid_rights_split_or_access_state_is_refused(mutation, expected, tmp_path):
    manifest = _manifest_with_source()
    source = manifest["sources"][0]
    if mutation == "rights":
        source["rights"]["permitted_uses"] = ["benchmarking"]
    elif mutation == "group":
        other = copy.deepcopy(source)
        other.update(source_id="second", split="held_out",
                     annotation={"annotation_id": "second:ann",
                                 "schema": "editorial-evidence-v1",
                                 "state": "not_started", "access": "not_created",
                                 "tuning_access": False})
        manifest["sources"].append(other)
    elif mutation == "held_out_labels":
        source = next(row for row in manifest["sources"]
                      if row["split"] == "held_out")
        source["annotation"].update(state="annotated", access="tuning_workspace")
    else:
        source["path"] = "../outside.mov"
        (tmp_path / "clip.mov").write_bytes(b"clip")

    result = editorial_eval_corpus.validate(
        manifest, tmp_path, verify_hashes=mutation == "unsafe_path")

    assert not result["valid"]
    assert any(expected in error for error in result["errors"])


def _manifest_with_source(*, sha256=None, size_bytes=4):
    sources = []
    for split in ("training", "calibration", "held_out"):
        source_id = f"clip-{split}"
        sources.append({
            "source_id": source_id,
            "path": f"{source_id}.mov",
            "sha256": sha256 or "a" * 64,
            "size_bytes": size_bytes,
            "duration_seconds": 1.0,
            "genre": "documentary",
            "split": split,
            "leakage_group_id": f"shoot-{split}",
            "tasks": ["scene"],
            "rights": {"status": "cleared", "basis": "test",
                       "permitted_uses": ["benchmarking", "training"]},
            "annotation": {"annotation_id": f"{source_id}:ann",
                           "schema": "editorial-evidence-v1", "state": "not_started",
                           "access": "not_created",
                           "tuning_access": split != "held_out"},
        })
    return {
        "annotation_schema": {"id": "editorial-evidence-v1"},
        "sources": sources,
        "coverage": [],
        "held_out_access": {"tuning_access": False},
    }
