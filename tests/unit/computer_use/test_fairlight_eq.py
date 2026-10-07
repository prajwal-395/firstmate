"""The Fairlight EQ recipe: intent in, falsifiable UI steps out.

The defect these tests pin: the chain intent slice 1 attaches to
`audio_mix_spec` has no executor - nothing turns its bands into the
UI acts the plan's section 9 names, and a script runner that can only
press and click cannot set a numeric frequency at all. Now
`fairlight_eq.eq_script` compiles a validated dialogue track plus a
recon controls map into a runnable Script (every anchor
provenance-marked, every unmapped control a build refusal naming the
gap), and the emitted JSON round-trips through the `ren
computer-use script` verb's own gate. The AX paths below are TEST
doubles standing in for the recon map - a live run promotes or fixes
each one; nothing here claims they resolve in real Resolve.
"""

from __future__ import annotations

import json

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import Script, run_script
from library.tools import fairlight_eq


def _track(**overrides):
    track = {
        "track": "dialogue",
        "role": "dialogue",
        "eq": [
            {"type": "high_pass", "frequency_hz": 80.0,
             "gain_db": 0.0, "q": 0.71},
            {"type": "peaking", "frequency_hz": 418.3,
             "gain_db": -22.8, "q": 0.62},
        ],
    }
    track.update(overrides)
    return track


def _controls(**overrides):
    controls = {
        "track": {"text": "Dialogue"},
        "eq_open": {"text": "EQ"},
        "bands": [
            {"frequency": {"path": ["Fairlight", "EQ", "Band 1",
                                    "Frequency"]}},
            {"frequency": {"path": ["Fairlight", "EQ", "Band 2",
                                    "Frequency"]},
             "gain": {"path": ["Fairlight", "EQ", "Band 2", "Gain"]},
             "q": {"path": ["Fairlight", "EQ", "Band 2", "Q"]}},
        ],
    }
    controls.update(overrides)
    return controls


def _values(script):
    """The set_value strings per set step, in order."""
    return [(step.action, step.anchors[0].params["value"])
            for step in script.steps
            if step.action.startswith("set_band")]


def _dynamics_track(**overrides):
    track = _track()
    track["dynamics"] = {
        "threshold_db": -20.0, "ratio": 2.0,
        "attack_ms": 10.0, "release_ms": 100.0,
    }
    track.update(overrides)
    return track


def _dynamics_controls(**overrides):
    controls = _controls()
    controls["dynamics_open"] = {"text": "Dynamics"}
    controls["dynamics"] = {
        "threshold_db": {"path": ["Fairlight", "Dynamics",
                                  "Threshold"]},
        "ratio": {"path": ["Fairlight", "Dynamics", "Ratio"]},
        "attack_ms": {"path": ["Fairlight", "Dynamics", "Attack"]},
        "release_ms": {"path": ["Fairlight", "Dynamics", "Release"]},
    }
    controls.update(overrides)
    return controls


def _master_track(**overrides):
    track = {
        "track": "master",
        "role": "master",
        "limiter": {"ceiling_dbTP": -1.0, "release_ms": 50.0},
    }
    track.update(overrides)
    return track


def _master_controls(**overrides):
    controls = {
        "track": {"text": "Master"},
        "limiter": {
            "ceiling": {"path": ["Fairlight", "Master", "Ceiling"]},
            "release": {"path": ["Fairlight", "Master", "Release"]},
        },
    }
    controls.update(overrides)
    return controls


# ── the compilation ──────────────────────────────────────────────────

def test_hp_and_peaking_compile_to_page_track_eq_and_sets_in_order():
    script = fairlight_eq.eq_script(_track(), _controls())
    assert [s.action for s in script.steps] == [
        "page_fairlight", "locate_track", "open_eq",
        "set_band_frequency",
        "set_band_frequency", "set_band_gain", "set_band_q",
    ]


def test_set_values_are_the_drivers_own_strings():
    script = fairlight_eq.eq_script(_track(), _controls())
    assert _values(script) == [
        ("set_band_frequency", "80"),
        ("set_band_frequency", "418.3"),
        ("set_band_gain", "-22.8"),
        ("set_band_q", "0.62"),
    ]


def test_every_anchor_and_expectation_names_its_provenance():
    script = fairlight_eq.eq_script(_track(), _controls())
    for step in script.steps:
        for anchor in step.anchors:
            assert anchor.params.get("source") in ("probe", "plan", "ren"), (
                f"{step.action}: anchor with no provenance")
        if step.expect is not None:
            assert step.expect.params.get("source") in ("probe", "plan",
                                                        "ren"), (
                f"{step.action}: expectation with no provenance")


def test_fairlight_control_paths_are_plan_until_recon():
    script = fairlight_eq.eq_script(_track(), _controls())
    sets = [s for s in script.steps if s.action.startswith("set_band")]
    assert sets, "no set steps to check"
    for step in sets:
        assert step.anchors[0].params["source"] == "plan"


# ── the refusals ─────────────────────────────────────────────────────

def test_dynamics_limiter_gate_and_sends_are_refused_by_name():
    for key, filler in (("dynamics", {"threshold_db": -20.0, "ratio": 2.0,
                                      "attack_ms": 10.0,
                                      "release_ms": 100.0}),
                        ("limiter", {"ceiling_dbTP": -1.0,
                                     "release_ms": 50.0}),
                        ("gate", {"threshold_db": -40.0, "ratio": 0.5,
                                  "attack_ms": 1.0, "release_ms": 100.0,
                                  "hold_ms": 10.0, "range_db": 12.0}),
                        ("sends", [{"target": "Reverb", "level_db": -6.0}])):
        track = _track()
        track[key] = filler
        with pytest.raises(ValueError, match=key):
            fairlight_eq.eq_script(track, _controls())


def test_a_non_dialogue_role_is_refused():
    track = _track(role="master", track="master")
    with pytest.raises(ValueError, match="dialogue"):
        fairlight_eq.eq_script(track, _controls())


def test_a_track_with_no_eq_bands_builds_no_script():
    track = _track()
    track["eq"] = []
    with pytest.raises(ValueError, match="must do something"):
        fairlight_eq.eq_script(track, _controls())


def test_an_impossible_band_is_refused_before_any_step():
    track = _track()
    track["eq"] = [{"type": "high_pass", "frequency_hz": 5.0,
                    "gain_db": 0.0, "q": 0.71}]
    with pytest.raises(ValueError, match="outside"):
        fairlight_eq.eq_script(track, _controls())


def test_a_controls_map_shorter_than_the_eq_is_refused():
    controls = _controls()
    controls["bands"] = controls["bands"][:1]
    with pytest.raises(ValueError, match="align them by index"):
        fairlight_eq.eq_script(_track(), controls)


def test_a_peaking_cut_without_gain_or_q_is_refused():
    for missing, named in (("gain", "gain"), ("q", "Q")):
        controls = _controls()
        del controls["bands"][1][missing]
        with pytest.raises(ValueError, match=named):
            fairlight_eq.eq_script(_track(), controls)


def test_a_gain_mapped_onto_a_high_pass_is_a_recon_error():
    controls = _controls()
    controls["bands"][0]["gain"] = {"path": ["Fairlight", "EQ", "Band 1",
                                             "Gain"]}
    with pytest.raises(ValueError, match="names no gain"):
        fairlight_eq.eq_script(_track(), controls)


def test_a_missing_eq_open_or_track_locator_is_refused():
    for missing in ("eq_open", "track"):
        controls = _controls()
        del controls[missing]
        if missing == "track":
            # The track falls back to the plan's own Dialogue label.
            script = fairlight_eq.eq_script(_track(), controls)
            assert script.steps[1].action == "locate_track"
        else:
            with pytest.raises(ValueError, match="track EQ"):
                fairlight_eq.eq_script(_track(), controls)


def test_a_text_only_frequency_map_has_no_field_to_set():
    controls = _controls()
    controls["bands"][0]["frequency"] = {"text": "80 Hz"}
    with pytest.raises(ValueError, match="no AX path"):
        fairlight_eq.eq_script(_track(), controls)


def test_a_low_pass_names_no_gain_like_a_high_pass():
    track = _track()
    track["eq"] = [{"type": "low_pass", "frequency_hz": 12000.0,
                    "gain_db": 0.0, "q": 0.71}]
    controls = _controls()
    controls["bands"] = [{"frequency": {"path": ["Fairlight", "EQ",
                                                 "Band 6",
                                                 "Frequency"]}}]
    script = fairlight_eq.eq_script(track, controls)
    assert [s.action for s in script.steps[3:]] == ["set_band_frequency"]
    controls["bands"][0]["gain"] = {"path": ["Fairlight", "EQ", "Band 6",
                                             "Gain"]}
    with pytest.raises(ValueError, match="names no gain"):
        fairlight_eq.eq_script(track, controls)


def test_dialogue_track_names_the_roles_when_absent():
    with pytest.raises(ValueError, match="master"):
        fairlight_eq.dialogue_track({"tracks": [
            {"track": "master", "role": "master", "limiter": {}}]})


# ── the verb's gate and the mock run ─────────────────────────────────

def test_the_emitted_file_round_trips_through_the_verbs_gate(tmp_path):
    path = fairlight_eq.emit_script_file(
        _track(), _controls(), str(tmp_path / "fairlight_eq.json"))
    raw = json.loads(open(path, encoding="utf-8").read())
    script = Script.from_dict(raw)
    assert len(script.steps) == 7
    assert script.steps[0].action == "page_fairlight"


def test_an_unresolvable_route_fails_instead_of_reporting_ok(
        client: DriverClient):
    """The mock tree holds no Fairlight page: the first step resolves
    nothing, takes no lease, and the script aborts - a route recon has
    not mapped never reports ok."""
    script = fairlight_eq.eq_script(_track(), _controls())
    result = run_script(client, script)
    assert result.outcome == "failed"
    step = result.steps[0]
    assert {key: step[key] for key in
            ("step", "action", "outcome", "via", "proved")} == {
                "step": 0, "action": "page_fairlight",
                "outcome": "failed", "via": "none", "proved": False,
            }
    assert step["seconds"] >= 0


# ── the chain ────────────────────────────────────────────────────────

def test_chain_covers_eq_then_dynamics_in_order():
    script = fairlight_eq.chain_script(_dynamics_track(),
                                       _dynamics_controls())
    assert [s.action for s in script.steps] == [
        "page_fairlight", "locate_track", "open_eq",
        "set_band_frequency",
        "set_band_frequency", "set_band_gain", "set_band_q",
        "open_dynamics",
        "set_dynamics_threshold", "set_dynamics_ratio",
        "set_dynamics_attack", "set_dynamics_release",
    ]


def test_chain_dynamics_values_are_the_drivers_own_strings():
    script = fairlight_eq.chain_script(_dynamics_track(),
                                       _dynamics_controls())
    sets = [(s.action, s.anchors[0].params.get("value"))
            for s in script.steps
            if s.action.startswith("set_dynamics")]
    assert sets == [
        ("set_dynamics_threshold", "-20"),
        ("set_dynamics_ratio", "2"),
        ("set_dynamics_attack", "10"),
        ("set_dynamics_release", "100"),
    ]


def test_chain_master_limiter_sets_ceiling_and_release():
    script = fairlight_eq.chain_script(_master_track(),
                                       _master_controls())
    assert [s.action for s in script.steps] == [
        "page_fairlight", "locate_track",
        "set_limiter_ceiling", "set_limiter_release",
    ]
    assert script.steps[2].anchors[0].params["value"] == "-1"
    assert script.steps[3].anchors[0].params["value"] == "50"


def test_chain_every_anchor_names_its_provenance():
    for script in (
            fairlight_eq.chain_script(_dynamics_track(),
                                      _dynamics_controls()),
            fairlight_eq.chain_script(_master_track(),
                                      _master_controls())):
        for step in script.steps:
            for anchor in step.anchors:
                assert anchor.params.get("source") in ("probe", "plan",
                                                       "ren"), (
                    f"{step.action}: anchor with no provenance")


def test_chain_refuses_a_dialogue_limiter_and_master_eq():
    track = _dynamics_track()
    track["limiter"] = {"ceiling_dbTP": -1.0, "release_ms": 50.0}
    with pytest.raises(ValueError, match="lives on master"):
        fairlight_eq.chain_script(track, _dynamics_controls())
    master = _master_track()
    master["eq"] = _track()["eq"]
    with pytest.raises(ValueError, match="master 'eq'|master eq"):
        fairlight_eq.chain_script(master, _master_controls())


def test_chain_refuses_gate_sends_and_foreign_roles():
    track = _dynamics_track()
    track["gate"] = {"threshold_db": -40.0, "ratio": 0.5,
                     "attack_ms": 1.0, "release_ms": 100.0,
                     "hold_ms": 10.0, "range_db": 12.0}
    with pytest.raises(ValueError, match="gate"):
        fairlight_eq.chain_script(track, _dynamics_controls())
    track = _dynamics_track(role="music", track="music")
    with pytest.raises(ValueError, match="music"):
        fairlight_eq.chain_script(track, _dynamics_controls())


def test_chain_refuses_unmapped_dynamics_and_limiter_controls():
    controls = _dynamics_controls()
    del controls["dynamics"]["ratio"]
    with pytest.raises(ValueError, match="ratio"):
        fairlight_eq.chain_script(_dynamics_track(), controls)
    controls = _master_controls()
    del controls["limiter"]["release"]
    with pytest.raises(ValueError, match="release"):
        fairlight_eq.chain_script(_master_track(), controls)


def test_chain_omits_defaulted_knee_and_makeup_but_not_a_set_one():
    track = _dynamics_track()
    track["dynamics"] = {**track["dynamics"], "knee_db": 0.0,
                         "makeup_gain_db": 0.0}
    script = fairlight_eq.chain_script(track, _dynamics_controls())
    assert not [s for s in script.steps
                if "knee" in s.action or "makeup" in s.action]
    track["dynamics"]["knee_db"] = 6.0
    with pytest.raises(ValueError, match="knee_db"):
        fairlight_eq.chain_script(track, _dynamics_controls())
    controls = _dynamics_controls()
    controls["dynamics"]["knee_db"] = {"path": ["Fairlight",
                                                "Dynamics", "Knee"]}
    script = fairlight_eq.chain_script(track, controls)
    assert [s.action for s in script.steps
            if "knee" in s.action] == ["set_dynamics_knee"]
    assert script.steps[-1].anchors[0].params["value"] == "6"


def test_chain_dynamics_only_track_plans_no_eq_steps():
    track = {"track": "dialogue", "role": "dialogue",
             "dynamics": _dynamics_track()["dynamics"]}
    script = fairlight_eq.chain_script(track, _dynamics_controls())
    assert [s.action for s in script.steps] == [
        "page_fairlight", "locate_track", "open_dynamics",
        "set_dynamics_threshold", "set_dynamics_ratio",
        "set_dynamics_attack", "set_dynamics_release",
    ]


def test_emit_chain_round_trips_and_matches_eq_only_output(tmp_path):
    path = fairlight_eq.emit_script_file(
        _dynamics_track(), _dynamics_controls(),
        str(tmp_path / "chain.json"))
    raw = json.loads(open(path, encoding="utf-8").read())
    assert len(Script.from_dict(raw).steps) == 12
    eq_only = fairlight_eq.emit_script_file(
        _track(), _controls(), str(tmp_path / "eq.json"))
    eq_raw = json.loads(open(eq_only, encoding="utf-8").read())
    assert [s["action"] for s in eq_raw["steps"]] == [
        "page_fairlight", "locate_track", "open_eq",
        "set_band_frequency",
        "set_band_frequency", "set_band_gain", "set_band_q",
    ]


# ── the gate ─────────────────────────────────────────────────────────

def _gate_track(**overrides):
    track = {"track": "dialogue", "role": "dialogue",
             "gate": {"threshold_db": -38.0, "ratio": 0.5,
                      "attack_ms": 1.0, "release_ms": 100.0,
                      "hold_ms": 200.0, "range_db": 24.0}}
    track.update(overrides)
    return track


def _gate_controls(**overrides):
    controls = {"track": {"text": "Dialogue"},
                "dynamics_open": {"text": "Dynamics"},
                "gate": {
                    "threshold_db": {"path": ["Fairlight", "Gate",
                                             "Threshold"]},
                    "ratio": {"path": ["Fairlight", "Gate", "Ratio"]},
                    "attack_ms": {"path": ["Fairlight", "Gate",
                                           "Attack"]},
                    "release_ms": {"path": ["Fairlight", "Gate",
                                            "Release"]},
                    "hold_ms": {"path": ["Fairlight", "Gate", "Hold"]},
                    "range_db": {"path": ["Fairlight", "Gate",
                                          "Range"]},
                }}
    controls.update(overrides)
    return controls


def test_chain_gate_sets_all_six_in_order():
    script = fairlight_eq.chain_script(_gate_track(), _gate_controls())
    assert [s.action for s in script.steps] == [
        "page_fairlight", "locate_track", "open_dynamics",
        "set_gate_threshold", "set_gate_ratio", "set_gate_attack",
        "set_gate_release", "set_gate_hold", "set_gate_range",
    ]
    assert script.steps[3].anchors[0].params["value"] == "-38"
    assert script.steps[4].anchors[0].params["value"] == "0.5"
    assert script.steps[7].anchors[0].params["value"] == "200"
    assert script.steps[8].anchors[0].params["value"] == "24"


def test_chain_gate_anchors_name_provenance():
    script = fairlight_eq.chain_script(_gate_track(), _gate_controls())
    for step in script.steps:
        for anchor in step.anchors:
            assert anchor.params.get("source") in ("probe", "plan",
                                                   "ren"), (
                f"{step.action}: anchor with no provenance")


def test_chain_gate_without_a_mapped_control_is_refused():
    controls = _gate_controls()
    del controls["gate"]["hold_ms"]
    with pytest.raises(ValueError, match="hold_ms"):
        fairlight_eq.chain_script(_gate_track(), controls)


def test_chain_gate_on_master_is_refused():
    track = {"track": "master", "role": "master",
             "limiter": {"ceiling_dbTP": -1.0, "release_ms": 50.0},
             "gate": _gate_track()["gate"]}
    with pytest.raises(ValueError, match="master 'gate'|master gate"):
        fairlight_eq.chain_script(track, _gate_controls())


def test_chain_eq_dynamics_gate_combine():
    track = _dynamics_track()
    track["gate"] = _gate_track()["gate"]
    controls = _dynamics_controls()
    controls["gate"] = _gate_controls()["gate"]
    script = fairlight_eq.chain_script(track, controls)
    actions = [s.action for s in script.steps]
    assert actions.index("open_dynamics") < actions.index(
        "set_dynamics_threshold") < actions.index("set_gate_threshold")
    assert len([a for a in actions if a.startswith("set_")]) == 4 + 4 + 6


def test_eq_script_still_refuses_a_gate():
    with pytest.raises(ValueError, match="gate"):
        fairlight_eq.eq_script(_gate_track(), _gate_controls())
