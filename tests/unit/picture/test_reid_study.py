"""reid_study helpers: manifest extras, false-match summary, strata.

Synthetic only: the heavy production-path measurement runs under the
heavy-work lock against real footage, not here.
"""

import json

import cv2
import numpy as np

from library.tools import reid_study


def _faces():
    return [
        {
            "key": "A@1",
            "person": "ann",
            "file": "A",
            "condition": "frontal",
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "key": "A@2",
            "person": "ann",
            "file": "A",
            "condition": "frontal",
            "embedding": [0.99, 0.01, 0.0],
        },
        {
            "key": "B@1",
            "person": "ann",
            "file": "B",
            "condition": "profile",
            "embedding": [0.97, 0.03, 0.0],
        },
        {
            "key": "B@2",
            "person": "bob",
            "file": "B",
            "condition": "profile",
            "embedding": [0.0, 1.0, 0.0],
        },
    ]


def test_extra_map():
    assert reid_study._extra_map([1.5, {"t": 2.0, "condition": "profile"}]) == {
        1.5: "",
        2.0: "profile",
    }


def test_false_match_summary_names_pairs():
    summary = reid_study.false_match_summary(_faces(), 0.25)
    assert summary["different_person_pairs"] == 3
    assert summary["at_threshold"] == 0
    assert all("ann" in row["a"] or "bob" in row["a"] for row in summary["closest"])


def test_strata_split_same_and_cross_file():
    strata = reid_study.cross_condition_same_person(_faces(), 0.25)
    assert strata["same_file"]["n"] == 1
    assert strata["cross_file"]["n"] == 2
    assert strata["same_condition"]["n"] == 1
    assert strata["cross_condition"]["n"] == 2


def _write_crop(directory, base, person, bgr, associated):
    cv2.imwrite(
        str(directory / (base + ".jpg")), np.full((24, 12, 3), bgr, dtype=np.uint8)
    )
    (directory / (base + ".json")).write_text(
        json.dumps({"person": person, "face_associated": associated}), encoding="utf-8"
    )


def test_score_crops_associated_only(tmp_path):
    _write_crop(tmp_path, "a1", "ann", (10, 10, 200), True)
    _write_crop(tmp_path, "a2", "ann", (12, 12, 198), True)
    _write_crop(tmp_path, "b1", "bob", (200, 10, 10), True)
    _write_crop(tmp_path, "hand", "ann", (200, 10, 10), False)
    full = reid_study.score_crops(str(tmp_path))
    assert full["crops"] == 4
    filtered = reid_study.score_crops(str(tmp_path), associated_only=True)
    assert filtered["crops"] == 3
    assert [s["crop"] for s in filtered["skipped"]] == ["hand"]
    assert filtered["margins"]["different_person_pairs"] == 2
