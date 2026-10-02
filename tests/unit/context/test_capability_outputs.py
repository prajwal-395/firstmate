"""A node run is recorded under its capabilities and nothing is lost:
a record must never outlive the output it was split from (a rerun, a
revised gate or a cleared stage that left it behind would serve the old
state to every reader), and a project recorded before the records still
reads (`library/tools/capability_outputs.py`)."""
from library.tools import capability_outputs as co


def test_a_node_record_is_split_by_what_each_capability_produces():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "timed_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    assert state[co.KEY]["spine.mesh"] == {"audio_spine": {"v": 1},
                                           "timed_spine": {"v": 1}}
    assert state[co.KEY]["duration_zone.build"] == {"duration_zone": {"z": 1}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 1}


def test_a_key_no_capability_declares_is_kept_with_the_run():
    """A pre-bridge's table or a model answer the step returns beside its
    result: the node view must still carry it (an edge may read it)."""
    state = {}
    co.record(state, "review_rough_cut", {"rough_cut_review": {"ok": 1},
                                          "cut_decisions": [1]})
    assert co.node_output(state, "review_rough_cut") == {
        "rough_cut_review": {"ok": 1}, "cut_decisions": [1]}


def test_a_rerecorded_node_leaves_no_stale_capability_record():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    co.record(state, "mesh_spine", {"audio_spine": {"v": 2}})
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 2}
    assert "duration_zone.build" not in state[co.KEY]


def test_a_forgotten_node_is_gone():
    state = {"step_outputs": {"mesh_spine": {"audio_spine": {"v": 0}}}}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1}})
    co.forget(state, "mesh_spine")
    assert "spine.mesh" not in state[co.KEY]
    assert co.value(state, "spine.mesh", "audio_spine") is None


def test_a_project_recorded_before_the_key_still_reads():
    state = {"step_outputs": {"mesh_spine": {"audio_spine": {"v": 0}}}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 0}


def test_a_write_carries_the_old_slot_over_as_records():
    """The next write migrates: no legacy key survives it, and what the
    slot held for other nodes is still read."""
    state = {"step_outputs": {"scan": {"total_files": 3},
                              "mesh_spine": {"audio_spine": {"v": 0}}}}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1}})
    assert co.LEGACY_KEY not in state
    assert co.value(state, "footage.scan", "total_files") == 3
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 1}
