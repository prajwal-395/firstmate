"""6.01 asks the model only for what the model authors.

Finding 11, execution-frontier report 2026-09-24: step 6.01 asked the
model for `render_watch_frames` and `visual_qa` - two bridge-owned
optional fields, both measured by the deterministic build. The manifest
declared no `llm_outputs`, so the runner asked for every declared
output the step had not already produced: a call with nothing to ask.

The manifest now declares `llm_outputs: [render_review]` - the one key
the model writes, a narrative verdict on the export it just built -
and `library/tools/render_review.py` reads it onto the run summary,
so the verdict has a reader (the 6.02 validation remains the gate
with teeth; reading is not gating).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    llm_output_declarations,
)
from library.tools import render_review as rr  # noqa: E402


def _manifest():
    with open(REPO_ROOT / "library" / "steps" / "step_6_01_render"
              / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


# ── The ask: only the model-owned key ──────────────────────────────

def test_the_model_is_asked_for_the_verdict_and_nothing_else():
    """Whatever the build already produced, the ask stays render_review.

    Before the fix the ask was the full outputs list minus what the
    build had in hand - on a default run, the two bridge-owned
    optional fields. Now the declared llm_outputs decide, and the
    bridge-owned keys are never asked for.
    """
    manifest = _manifest()
    for already_have in (
        set(),
        {"render_output"},
        {"render_output", "render_watch_frames", "visual_qa"},
    ):
        asked = llm_output_declarations(manifest, already_have)
        assert [o["name"] for o in asked] == ["render_review"], (
            f"already_have={already_have}: asked={[o['name'] for o in asked]}")
    from library.tools.output_contract import survey
    rows = survey()
    row = next(r for r in rows
               if r.node == "render" and r.name == "render_review")
    assert not row.unread, (
        "render_review has no reader: the summary stopped reading it")

    lines = rr.summary_lines({
        "overall": "concerns",
        "notes": ["V2 b-roll crowds the caption row at 37s"],
    })
    assert any("concerns" in line for line in lines)
    assert any("37s" in line for line in lines)
