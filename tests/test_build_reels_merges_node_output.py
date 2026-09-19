"""A node's record is the union of its ops, never the last write.

Measured 2026-09-19 on Reel 04: `build_reels` owns two ops -
`reel.build` then `reel.ask` - and `cmd_build_reels` recorded each
payload with a wholesale write, so the ask-only payload destroyed the
`reel_build` record the build had just placed. `reel.verify` then
refused (`verify_reels needs reel_build from build_reels`), leaving a
staged reel with no path to promotion.

Nothing here reaches Resolve or a real project.
"""

import manage_project


def test_second_op_of_a_node_keeps_the_firsts_keys():
    slot = {}
    manage_project._record_node_output(
        slot, "build_reels", {"reel_build": {"placed": ["Reel 04"]}}
    )
    manage_project._record_node_output(
        slot, "build_reels", {"reel_ask": {"asked": ["Reel 04"]}}
    )
    assert slot["build_reels"] == {
        "reel_build": {"placed": ["Reel 04"]},
        "reel_ask": {"asked": ["Reel 04"]},
    }


def test_first_write_to_an_empty_slot_records_whole():
    slot = {}
    manage_project._record_node_output(
        slot, "build_reels", {"reel_build": {"placed": []}}
    )
    assert slot["build_reels"] == {"reel_build": {"placed": []}}


def test_nodes_do_not_share_a_record():
    slot = {}
    manage_project._record_node_output(slot, "build_reels", {"reel_build": {}})
    manage_project._record_node_output(slot, "verify_reels", {"reel_verification": {}})
    assert slot["build_reels"] == {"reel_build": {}}
    assert slot["verify_reels"] == {"reel_verification": {}}


def test_non_dict_payload_keeps_overwrite():
    slot = {"build_reels": {"reel_build": {}}}
    manage_project._record_node_output(slot, "build_reels", None)
    assert slot["build_reels"] is None
