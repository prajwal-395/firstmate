"""An empty subtitle plan must not fail the run.

A speechless cut has no captions: `plan_subtitles` writes an empty plan
and `render_subtitles` has no cards to draw. The step's own contract
says that is not a refusal ("An empty plan is NOT a refusal"). But the
empty-plan return carried `available: False`, which the runner's hollow
gate (`run_pipeline.check_output_is_real`) reads anywhere in a step's
output as a failed run - so no speechless project could ever reach
`compile_manifest`.

The motion-graphics renderer keeps the same distinction the same way:
a plan of none renders `segments: []` with no `available` key, and a
test pins that shape. This test pins the subtitle half: the empty-plan
value passes the hollow gate, while a genuine `available: false` (a
renderer that cannot start) still fails it.
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_05_render_subtitles.step import _empty_overlay


def _check_output_is_real():
    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_hollow_test",
        REPO / "library" / "processes" / "edit_video" / "run_pipeline.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_output_is_real


def test_empty_plan_carries_no_cards_and_no_available_flag():
    overlay = _empty_overlay("No subtitle entries found in subtitle plan")
    assert overlay["segments"] == []
    assert "available" not in overlay
    assert overlay["reason"] == "No subtitle entries found in subtitle plan"


def test_empty_plan_passes_the_hollow_gate():
    check = _check_output_is_real()
    output = {
        "subtitle_overlay": _empty_overlay("No subtitle entries found in subtitle plan")
    }
    assert check("render_subtitles", output) == [], (
        "a speechless cut's empty overlay must not stop the run"
    )


def test_a_real_unavailable_still_fails_the_gate():
    """The control: `available: false` keeps its meaning for a renderer
    that cannot start, so this test can fail and is not coverage theatre."""
    check = _check_output_is_real()
    problems = check(
        "render_subtitles",
        {
            "subtitle_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/",
            }
        },
    )
    assert problems, "the gate must still catch a dead renderer"
    assert "available=false" in problems[0]
