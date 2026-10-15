import json
from pathlib import Path

import pytest

from library.tools import blind_label_pilot
from library.tools.blind_label_pilot import (
    add_runner_proposals,
    calibrate_abstention,
    ingest,
    label_summary,
    validate_answers,
    validate_spec,
)


def _corpus():
    return {
        "sources": [
            {"source_id": "cal", "split": "calibration", "duration_seconds": 20},
            {"source_id": "test", "split": "held_out", "duration_seconds": 20},
        ]
    }


def _spec():
    return {
        "kit_id": "kit",
        "items": [
            {
                "id": "s1",
                "kind": "search",
                "source_id": "cal",
                "start_seconds": 0,
                "end_seconds": 5,
                "query": "red cup",
                "prompt": "red cup",
            },
            {
                "id": "e1",
                "kind": "edit",
                "source_id": "cal",
                "start_seconds": 5,
                "end_seconds": 10,
                "proposal_id": "p1",
                "prompt": "Include this window",
            },
        ],
    }


def test_spec_requires_calibration_windows_and_search_query():
    spec = _spec()
    assert validate_spec(spec, _corpus()) == []
    spec["items"][0]["source_id"] = "test"
    assert any("calibration" in error for error in validate_spec(spec, _corpus()))


def test_answer_validation_and_ingest_are_evaluator_only(tmp_path):
    spec = _spec()
    kit = {"kit_id": "kit", "items": spec["items"]}
    answers = {
        "schema": "ren-blind-editorial-answers-v1",
        "kit_id": "kit",
        "labeller": "captain",
        "labels": [
            {"item_id": "s1", "judgment": "absent"},
            {"item_id": "e1", "judgment": "acceptable", "note": "clear"},
        ],
    }
    assert validate_answers(answers, kit) == []
    answers_path, kit_path = tmp_path / "answers.json", tmp_path / "kit.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8")
    kit_path.write_text(json.dumps(kit), encoding="utf-8")
    output = tmp_path / "evaluator" / "labels.json"
    result = ingest(answers_path, kit_path, output)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert result["label_count"] == 2
    assert record["evaluator_only"] is True
    assert record["training_data"] is False
    assert record["calibration_inputs"] is False
    assert record["labels"][1]["note"] == "clear"
    with pytest.raises(FileExistsError):
        ingest(answers_path, kit_path, output)


def test_search_labels_can_calibrate_abstention_without_mutating_inputs(tmp_path):
    spec = _spec()
    kit = {"kit_id": "kit", "items": spec["items"][:1]}
    answers = {
        "schema": "ren-blind-editorial-answers-v1",
        "kit_id": "kit",
        "labeller": "test",
        "labels": [{"item_id": "s1", "judgment": "absent"}],
    }
    ap, kp = tmp_path / "a.json", tmp_path / "k.json"
    ap.write_text(json.dumps(answers), encoding="utf-8")
    kp.write_text(json.dumps(kit), encoding="utf-8")
    record_path = tmp_path / "labels.json"
    ingest(ap, kp, record_path)
    assert label_summary(record_path, kind="search")["absent_count"] == 1
    assert (
        calibrate_abstention(record_path, {"s1": 0.2})["status"]
        == "insufficient labels or scores"
    )


def test_eval04_proposals_are_taken_only_from_calibration_outputs(tmp_path):
    run_root = tmp_path / "runner"
    run_root.mkdir()
    timeline = {
        "tracks": [
            {
                "clips": [
                    {
                        "name": "proposal",
                        "source": "cal",
                        "source_range": [1, 4],
                        "reason": {"description": "holds a moment"},
                    },
                    {"name": "held", "source": "test", "source_range": [2, 6]},
                ]
            }
        ]
    }
    (run_root / "edit.otio.json").write_text(json.dumps(timeline), encoding="utf-8")
    spec_path, corpus_path, output = (
        tmp_path / "spec.json",
        tmp_path / "corpus.json",
        tmp_path / "combined.json",
    )
    spec_path.write_text(json.dumps({"kit_id": "kit", "items": []}), encoding="utf-8")
    corpus_path.write_text(json.dumps(_corpus()), encoding="utf-8")
    assert add_runner_proposals(spec_path, run_root, corpus_path, output, limit=1) == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    proposal = result["items"][0]
    assert proposal["source_id"] == "cal"
    assert proposal["start_seconds"] == 1
    assert proposal["end_seconds"] == 4


def test_generate_keeps_source_window_in_kit_but_plays_proxy_from_zero(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "media"
    source_root.mkdir()
    (source_root / "cal.mov").write_bytes(b"fixture")
    corpus = {
        "sources": [
            {
                "source_id": "cal",
                "split": "calibration",
                "duration_seconds": 20,
                "path": "cal.mov",
            }
        ]
    }
    spec = {
        "kit_id": "kit",
        "items": [
            {
                "id": "s1",
                "kind": "search",
                "source_id": "cal",
                "start_seconds": 5,
                "end_seconds": 10,
                "query": "red cup",
                "prompt": "red cup",
            }
        ],
    }
    corpus_path, spec_path = tmp_path / "corpus.json", tmp_path / "spec.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def fake_run(args):
        Path(args[-1]).write_bytes(b"proxy")
        return ""

    monkeypatch.setattr(blind_label_pilot, "_probe", lambda _path: 20)
    monkeypatch.setattr(blind_label_pilot, "_run", fake_run)
    scratch = tmp_path / "scratch"
    page = blind_label_pilot.generate(spec_path, corpus_path, source_root, scratch)
    kit = json.loads((scratch / "kit.json").read_text(encoding="utf-8"))
    assert kit["items"][0]["start_seconds"] == 5
    assert kit["items"][0]["end_seconds"] == 10
    assert 'data-start="0"' in page.read_text(encoding="utf-8")
