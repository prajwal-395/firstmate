"""One capability's record never destroys another's.

Measured 2026-09-19 on Reel 04: `build_reels` owns two ops -
`reel.build` then `reel.ask` - and the reels runner recorded each
payload with a wholesale write into the node's one slot, so the
ask-only payload destroyed the `reel_build` record the build had just
placed. `reel.verify` then refused (`verify_reels needs reel_build from
build_reels`), leaving a staged reel with no path to promotion.

Nothing here reaches Resolve or a real project.
"""

import json

from library.processes.reels import run_reels
from library.tools import capability_outputs


def test_the_ask_after_the_build_leaves_the_build_for_verify(tmp_path):
    project = str(tmp_path)
    run_reels.record_project_output(
        project, "reel.build", {"reel_build": {"placed": ["Reel 04"]}})
    run_reels.record_project_output(
        project, "reel.ask", {"reel_ask": {"asked": ["Reel 04"]}})

    state = json.loads((tmp_path / "pipeline_data.json").read_text(
        encoding="utf-8"))
    # What the edge to `verify_reels` carries.
    assert capability_outputs.node_output(state, "build_reels")[
        "reel_build"] == {"placed": ["Reel 04"]}


def test_a_project_recorded_before_the_records_still_loads(tmp_path):
    """A pre-migration state is read through, then written as records."""
    path = tmp_path / "pipeline_data.json"
    path.write_text(json.dumps({"step_outputs": {
        "build_reels": {"reel_build": {"v": "old"}}}}), encoding="utf-8")

    run_reels.record_project_output(str(tmp_path), "reel.ask",
                                    {"reel_ask": {}})

    state = json.loads(path.read_text(encoding="utf-8"))
    assert "step_outputs" not in state
    assert capability_outputs.read(state, "reel.build") == {
        "reel_build": {"v": "old"}}
    assert capability_outputs.read(state, "reel.ask") == {"reel_ask": {}}
