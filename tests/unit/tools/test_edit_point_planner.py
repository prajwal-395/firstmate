import json
from pathlib import Path

from library.tools.project_model_editorial import plan_edit_point_candidates

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures" / "project_model"
           / "labeled_edit_point_boundaries.json")


def test_labeled_boundary_fixture_preserves_half_open_edges_and_citations():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    candidates = plan_edit_point_candidates(fixture["project_model"])
    observed = [
        {key: row[key] for key in ("clock_id", "tick", "measurement_ids", "boundary_types")}
        for row in candidates
    ]

    assert observed == fixture["expected"]
    assert all(row["status"] == "candidate" and row["measurement_ids"]
               for row in candidates)


def test_supported_signal_families_propose_only_measured_ticks():
    model = {"signals": [
        {"id": "phoneme", "measurement_type": "speech.phoneme", "processing_status": "analyzed",
         "interval": {"clock_id": "c", "start": 1, "end": 9},
         "samples": [{"tick": 4, "value": "plosive"}]},
        {"id": "settle", "measurement_type": "camera.settle", "processing_status": "analyzed",
         "interval": {"clock_id": "c", "start": 1, "end": 9},
         "samples": [{"tick": 6, "value": "settled"}, {"tick": 9, "value": "settled"}]},
        {"id": "unrecognized", "measurement_type": "camera.pan", "processing_status": "analyzed",
         "interval": {"clock_id": "c", "start": 1, "end": 9}, "samples": [{"tick": 5}]},
    ]}

    candidates = plan_edit_point_candidates(model)

    assert [(row["tick"], row["signals"]) for row in candidates] == [
        (1, ["phoneme"]), (4, ["phoneme"]), (6, ["camera_settle"]),
        (9, ["phoneme"]),
    ]
    assert all(row["measurement_ids"] for row in candidates)
