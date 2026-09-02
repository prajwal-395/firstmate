"""Tests for the mesh_spine pre-bridge that resolves the duration zone.

The zone was judged by the post-bridge but never shown to the model.
The pre-bridge now puts the resolved (min, target, max) into context
alongside a legend, following the MEASUREMENT_LEGEND pattern.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "library" / "steps" / "step_2_05_mesh_spine" / "bridge.py"

from library.tools.duration_targets import (
    DURATION_ZONE_LEGEND,
    get_target_duration_zone,
)


def _run_bridge(data: dict) -> dict:
    """Run the bridge as a subprocess, the way run_pipeline does."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"bridge failed: {proc.stderr}"
    return json.loads(proc.stdout)


class TestDurationZoneReachesBridge:
    """The pre-bridge resolves the zone the post-bridge judges against."""

    def test_project_config_produces_zone(self):
        """001's declaration: target 60 -> zone [54.0, 60.0, 66.0]."""
        out = _run_bridge({
            "project_config": {"target_duration_seconds": 60},
        })
        zone = out["duration_zone"]
        assert zone["minimum_seconds"] == 54.0
        assert zone["target_seconds"] == 60.0
        assert zone["maximum_seconds"] == 66.0

    def test_legend_is_shipped(self):
        """The legend travels with the numbers so the model can read both."""
        out = _run_bridge({
            "project_config": {"target_duration_seconds": 60},
        })
        legend = out["duration_zone"]["zone_legend"]
        assert set(legend) == set(DURATION_ZONE_LEGEND)
        for key in DURATION_ZONE_LEGEND:
            assert legend[key] == DURATION_ZONE_LEGEND[key]

    def test_no_declaration_emits_empty(self):
        """No project_config -> no zone -> empty output."""
        out = _run_bridge({})
        assert out == {}

    def test_zone_matches_what_post_bridge_validates_against(self):
        """The zone the model sees must be the zone it is judged against.

        If they drift, the bug this fix addresses comes back.
        """
        data = {"project_config": {"target_duration_seconds": 60}}
        bridge_out = _run_bridge(data)
        zone_from_bridge = (
            bridge_out["duration_zone"]["minimum_seconds"],
            bridge_out["duration_zone"]["target_seconds"],
            bridge_out["duration_zone"]["maximum_seconds"],
        )
        zone_from_validator = get_target_duration_zone(data)
        assert zone_from_bridge == (
            round(zone_from_validator[0], 1),
            round(zone_from_validator[1], 1),
            round(zone_from_validator[2], 1),
        )

    def test_brand_template_zone(self):
        """Brand template declares min/max -> zone is derived from that."""
        data = {
            "brand_template": {
                "content": {
                    "target_duration_seconds": {"min": 30, "max": 90}
                }
            }
        }
        out = _run_bridge(data)
        zone = out["duration_zone"]
        assert zone["minimum_seconds"] == 30.0
        assert zone["maximum_seconds"] == 90.0
        assert zone["target_seconds"] == 60.0  # average of min/max

    def test_project_config_takes_precedence_over_brand(self):
        """project_config.target_duration_seconds wins over brand template."""
        data = {
            "project_config": {"target_duration_seconds": 120},
            "brand_template": {
                "content": {
                    "target_duration_seconds": {"min": 30, "max": 90}
                }
            },
        }
        out = _run_bridge(data)
        zone = out["duration_zone"]
        # 120 * 0.9 = 108, 120 * 1.1 = 132
        assert zone["minimum_seconds"] == 108.0
        assert zone["target_seconds"] == 120.0
        assert zone["maximum_seconds"] == 132.0
