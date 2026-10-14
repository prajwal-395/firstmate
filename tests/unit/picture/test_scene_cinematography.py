import json
from pathlib import Path

from library.tools.scene_cinematography import segment_and_profile


FIXTURE = Path(__file__).parents[2] / "fixtures/scene_cinematography/annotated_proxy.json"


def boundary_f1(predicted, expected, tolerance=0.05):
    remaining = list(expected)
    true_positive = 0
    for boundary in predicted:
        match = next((i for i, target in enumerate(remaining)
                      if abs(boundary - target) <= tolerance), None)
        if match is not None:
            true_positive += 1
            remaining.pop(match)
    precision = true_positive / len(predicted) if predicted else 1.0
    recall = true_positive / len(expected) if expected else 1.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def test_c6_labeled_proxy_boundary_and_attribute_scores():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    measurements = fixture["measurements"]
    result = segment_and_profile(
        fixture["duration"], measurements["scene_boundaries"],
        measurements["motion"], measurements["camera_motion"],
        measurements["face_presence"], measurements["color_curves"],
        scene_intervals=fixture["ground_truth"]["scenes"],
    )
    predicted = [shot["start"] for shot in result["shots"][1:]]
    expected = fixture["ground_truth"]["shot_boundaries"]
    assert boundary_f1(predicted, expected) == 1.0
    predicted_subshots = [span["start"] for span in result["subshots"]
                          if span["boundary_basis_ids"]]
    assert boundary_f1(predicted_subshots,
                       fixture["ground_truth"]["subshot_boundaries"]) == 1.0
    truth = fixture["ground_truth"]["attributes"]
    comparisons = [(shot["attributes"][key]["value"], target)
                   for shot in result["shots"]
                   for key, target in truth[shot["id"]].items()]
    agreement = sum(actual == target for actual, target in comparisons) / len(comparisons)
    assert agreement == 1.0
    assert all(attr["evidence_ids"] for shot in result["shots"]
               for attr in shot["attributes"].values() if attr["value"] != "unknown")


def test_hierarchy_is_separate_and_half_open():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    measurements = fixture["measurements"]
    result = segment_and_profile(
        fixture["duration"], measurements["scene_boundaries"],
        measurements["motion"], measurements["camera_motion"],
        measurements["face_presence"], measurements["color_curves"],
        scene_intervals=fixture["ground_truth"]["scenes"],
    )
    assert len(result["scenes"]) == 3
    assert len(result["shots"]) == 3
    assert len(result["subshots"]) == 4
    assert len(result["sequences"]) == 2
    assert {scene["certainty"] for scene in result["scenes"]} == {"interpreted"}
    assert {shot["certainty"] for shot in result["shots"]} == {"measured"}
    assert {subshot["certainty"] for subshot in result["subshots"]} == {"inferred"}
    assert {sequence["certainty"] for sequence in result["sequences"]} == {"interpreted"}
    assert all(span["start"] < span["end"] for key in ("scenes", "shots", "subshots", "sequences")
               for span in result[key])
    assert result["sequences"][0]["scene_ids"] == ["scene-0001", "scene-0002"]
    assert result["subshots"][-1]["start"] == 9.0
    assert result["subshots"][-1]["boundary_basis_ids"] == [
        "camera_motion:proxy-flow-v1:9"]
