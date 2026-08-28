"""No step declares scene_boundaries in its context_fields.

The temporal index's `scene_boundaries` was degenerate: on project 001,
16 of 17 clips carried a single `{"time": 0.0, "score": 1.0, "type":
"start"}` marker.  Only `clip_015` had a second entry.  Seventeen rows
of context spent to convey one fact about one clip.

The per-clip visual records (`view:picture`) give every planner the
vision pass's per-window descriptions - 86 across 001's seventeen clips,
covering 95% of the footage - and are the field that replaced it.

This test asserts the field does not come back (#225).
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STEPS = REPO / "library" / "steps"


def _all_context_fields():
    """Yield (step_id, field) for every context_fields entry in every manifest."""
    for manifest_path in sorted(STEPS.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        step_id = manifest.get("id", manifest_path.parent.name)
        for field in manifest.get("context_fields", []):
            yield step_id, field


def test_no_step_declares_scene_boundaries():
    hits = [
        (step_id, field)
        for step_id, field in _all_context_fields()
        if "scene_boundaries" in field
    ]
    assert hits == [], (
        f"scene_boundaries must not appear in any step's context_fields "
        f"(#225). Found in: {hits}"
    )
