"""No constant zoom factor out of step 1.04's camera-motion decomposition.

`decompose_camera_motion` used to emit `zoom_factor =
1.0 + max(0, mag - (|dx| + |dy|)) * 0.3` on every sample. Its inputs are
a single global translation vector per sample with magnitude =
sqrt(dx^2 + dy^2), which never exceeds |dx| + |dy| - so the max() term
was 0.0 on every sample and the factor read 1.0 always: a constant
presented as a measurement, reaching the temporal index as though zoom
had been estimated. True zoom needs a center-weighted dense field the
block matcher does not compute, so the key is gone rather than fixed.

This test names that defect: it fails on any decomposition output that
carries a `zoom_factor` key again.
"""
from library.steps.step_1_04_temporal_index.step import (
    decompose_camera_motion,
)


def test_decomposition_emits_no_zoom_factor():
    flow = {
        "sample_rate_hz": 5,
        "values": [
            {"dx": 0.5, "dy": 0.0, "magnitude": 0.5},
            {"dx": 0.3, "dy": 0.4, "magnitude": 0.5},
            {"dx": 0.0, "dy": 0.0, "magnitude": 0.0},
        ],
    }
    out = decompose_camera_motion(flow)
    assert len(out["values"]) == 3
    for sample in out["values"]:
        assert "zoom_factor" not in sample, (
            f"zoom_factor is back in the decomposition output: {sample} - "
            "it always evaluated to 1.0 and must not be reported"
        )
    assert set(out["values"][0]) == {
        "translation_x", "translation_y", "residual",
    }
