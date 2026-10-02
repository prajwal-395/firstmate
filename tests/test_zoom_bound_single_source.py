"""The prompt's zoom bound and the code's are one source.

Finding 20, execution-frontier report 2026-09-24: step 4.03's prompt
stated an animated-zoom bound of 1.04 while the comp builder enforced
1.15 (`library/tools/fusion/nodes.py: MAX_ANIMATED_ZOOM`) - the prompt
went stale when the constant moved. The handoff now carries a marker
where the number was, and the step's bridge renders it from the live
constant, so the two cannot disagree again.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_03_plan_vfx.bridge import (  # noqa: E402
    PUNCH_TIMING_MARKER,
    ZOOM_BOUND_MARKER,
    zoom_bound_additions,
)
from library.tools.fusion.nodes import MAX_ANIMATED_ZOOM  # noqa: E402
from library.tools.punch_timing import (  # noqa: E402
    MAX_PUNCH_RAMP_SECONDS,
    MIN_PUNCH_RAMP_SECONDS,
)

STEP_DIR = (REPO_ROOT / "library" / "steps" / "step_4_03_plan_vfx")


def _handoff() -> str:
    return (STEP_DIR / "handoff.md").read_text(encoding="utf-8")


def test_the_rendered_prompt_states_the_enforced_bound():
    """The marker replacement `present_llm_step` performs, simulated:
    the prompt the model reads names the bound the builder enforces, and
    the punch-ramp band `punch_timing` refuses outside of."""
    rendered = _handoff()
    assert ZOOM_BOUND_MARKER in rendered
    assert PUNCH_TIMING_MARKER in rendered
    for marker, text in zoom_bound_additions().items():
        rendered = rendered.replace(marker, text)
    assert ZOOM_BOUND_MARKER not in rendered
    assert PUNCH_TIMING_MARKER not in rendered
    assert f"{MIN_PUNCH_RAMP_SECONDS:.2f}" in rendered
    assert f"{MAX_PUNCH_RAMP_SECONDS:.1f}" in rendered
    assert f"{MAX_ANIMATED_ZOOM:g}" in rendered
    assert "1.04" not in rendered
