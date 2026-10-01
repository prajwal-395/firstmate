"""A node's record is the union of its ops, never the last write.

Measured 2026-09-19 on Reel 04: `build_reels` owns two ops -
`reel.build` then `reel.ask` - and the reels runner recorded each
payload with a wholesale write, so the ask-only payload destroyed the
`reel_build` record the build had just placed. `reel.verify` then
refused (`verify_reels needs reel_build from build_reels`), leaving a
staged reel with no path to promotion.

Nothing here reaches Resolve or a real project.
"""

from library.processes.reels import run_reels


def test_second_op_of_a_node_keeps_the_firsts_keys():
    slot = {}
    run_reels.record_node_output(
        slot, "build_reels", {"reel_build": {"placed": ["Reel 04"]}}
    )
    run_reels.record_node_output(
        slot, "build_reels", {"reel_ask": {"asked": ["Reel 04"]}}
    )
    assert slot["build_reels"] == {
        "reel_build": {"placed": ["Reel 04"]},
        "reel_ask": {"asked": ["Reel 04"]},
    }




def test_nodes_do_not_share_a_record():
    slot = {}
    run_reels.record_node_output(slot, "build_reels", {"reel_build": {}})
    run_reels.record_node_output(slot, "verify_reels", {"reel_verification": {}})
    assert slot["build_reels"] == {"reel_build": {}}
    assert slot["verify_reels"] == {"reel_verification": {}}




def test_capability_record_is_read_first_and_old_projects_still_load():
    """The capability slot is authoritative, written beside the legacy
    node slot; state recorded before the key existed reads the node."""
    from library.tools import capability_outputs

    old = {"step_outputs": {"build_reels": {"reel_build": {"v": "old"}}}}
    assert capability_outputs.read(old, "reel.build") == {
        "reel_build": {"v": "old"}}

    state = {}
    run_reels._merge_project_output(state, "/p", "build_reels",
                                    {"reel_build": {"v": 1}},
                                    capability_id="reel.build")
    run_reels._merge_project_output(state, "/p", "build_reels",
                                    {"reel_ask": {}},
                                    capability_id="reel.ask")
    assert state["step_outputs"]["build_reels"] == {
        "reel_build": {"v": 1}, "reel_ask": {}}
    assert capability_outputs.read(state, "reel.build") == {
        "reel_build": {"v": 1}}
