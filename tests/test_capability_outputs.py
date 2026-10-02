"""A capability record is read BEFORE the node slot, so it must never
outlive the output it was split from: a rerun, a revised gate or a
cleared stage that left it behind would serve the old state to every
capability-first reader (`library/tools/capability_outputs.py`)."""
from library.tools import capability_outputs as co


def test_a_node_record_is_split_by_what_each_capability_produces():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "timed_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    assert state["step_outputs"]["mesh_spine"]["duration_zone"] == {"z": 1}
    assert state[co.KEY]["spine.mesh"] == {"audio_spine": {"v": 1},
                                           "timed_spine": {"v": 1}}
    assert state[co.KEY]["duration_zone.build"] == {"duration_zone": {"z": 1}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 1}


def test_a_rerecorded_node_leaves_no_stale_capability_record():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    co.record(state, "mesh_spine", {"audio_spine": {"v": 2}})
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 2}
    assert "duration_zone.build" not in state[co.KEY]


def test_a_forgotten_node_is_gone_under_both_keys():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1}})
    co.forget(state, "mesh_spine")
    assert "mesh_spine" not in state["step_outputs"]
    assert "spine.mesh" not in state[co.KEY]
    assert co.value(state, "spine.mesh", "audio_spine") is None


def test_a_project_recorded_before_the_key_still_reads():
    state = {"step_outputs": {"mesh_spine": {"audio_spine": {"v": 0}}}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 0}
