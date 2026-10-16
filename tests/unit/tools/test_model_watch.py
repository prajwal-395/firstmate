import json

import pytest

from library.tools.model_watch import DEFAULT_DATABASE, load_database, should_evaluate


def test_seed_database_covers_every_section_15_2_candidate():
    database = load_database()
    names = {candidate["name"] for candidate in database["candidates"]}
    expected = {
        "GroundingPI", "Molmo2", "MolmoPoint-Vid", "PadCaptioner", "CapRL-Video",
        "TimeLens 2", "TimePLE", "Pocket-STVG", "COSED", "FlexSED", "TARA",
        "Cosmos Embed1", "V-JEPA 2.1", "DINOv3", "VideoITG", "Tempo", "C³ASD",
        "InsightFace 2.1", "SAM Audio", "SAM 3.1 MLX", "SAM 2.1 MLX", "EdgeTAM",
        "sam3.cpp", "MLX-CV", "MLX-video", "FluidAudio", "RF-DETR (Nano/Small/Medium/Large)",
        "YOLO26 MLX", "WeMM-Embedding", "Meta PE Core (S/B/L)", "Meta PE-AV",
        "PaddleOCR-VL-1.6", "Depth Anything 3 (Small/Base/Metric-Large)",
        "Desert Ant Uhm", "Desert Ant Ear", "Desert Ant Align", "Desert Ant Clear",
        "Desert Ant Clips", "Desert Ant Eye", "Desert Ant Who", "Desert Ant Face",
    }
    assert expected <= names
    assert len(database["candidates"]) == len({c["id"] for c in database["candidates"]})


def test_evaluation_flag_is_fail_closed_on_unknown_fields():
    record = {
        "relevance": "relevant",
        "decision": {"status": "candidate"},
        "code": {"status": "available"},
        "weights": {"status": "available"},
        "apple_silicon": {"status": "compatible"},
        "license": {"status": "permissive"},
    }
    assert should_evaluate(record)
    record["license"]["status"] = "unresolved"
    assert not should_evaluate(record)
    record["license"]["status"] = "permissive"
    record["decision"]["status"] = "rejected"
    assert not should_evaluate(record)


def test_reader_rejects_duplicate_ids(tmp_path):
    data = json.loads(DEFAULT_DATABASE.read_text(encoding="utf-8"))
    data["candidates"].append(dict(data["candidates"][0]))
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate candidate id"):
        load_database(path)
