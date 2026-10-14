"""Labeled pair fixtures pin evidence supported continuity claims."""
import pytest

from library.tools.continuity_feasibility import (
    assess_target_crop,
    score_shot_pair,
    transformation_feasibility,
)


@pytest.mark.parametrize(("label", "left", "right", "expected"), [
    ("matched measured pair", {"scale": 0.4}, {"scale": 0.4}, 1.0),
    ("different measured pair", {"scale": 0.2}, {"scale": 0.8}, 0.4),
    ("unsupported pair", {}, {"scale": 0.8}, None),
])
def test_pair_scores_only_measured_axes(label, left, right, expected):
    result = score_shot_pair(left, right, {"tolerances": {"scale": 1.0}})
    assert result["scale"]["score"] == expected, label
    if label == "unsupported pair":
        assert result["overall"] is None


@pytest.mark.parametrize(("label", "subject", "crop", "policy", "safe"), [
    ("contained subject and permitted overlay", {"box": [.4,.2,.6,.6]},
     {"box": [0,0,1,1]}, {"overlay_regions": [[0,.7,1,.9]]}, True),
    ("overlay overlaps subject", {"box": [.4,.2,.6,.6]}, {"box": [0,0,1,1]},
     {"overlay_regions": [[.45,.3,.8,.5]]}, False),
    ("missing measurements", None, {"box": [0,0,1,1]}, {}, None),
])
def test_crop_safe_claim_requires_measurements_and_policy(label, subject, crop, policy, safe):
    result = assess_target_crop(subject, crop, policy)
    assert result["safe"] is safe, label


def test_transform_feasibility_is_unmeasured_without_policy_limits():
    result = transformation_feasibility({"residual_motion": 0.2,
                                        "grade_headroom": 0.5})
    assert result["stabilize"] == "unmeasured"
    assert result["grade"] == "unmeasured"
