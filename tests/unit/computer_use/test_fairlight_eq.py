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
    assert result.steps[0] == {"step": 0, "action": "page_fairlight",
                               "outcome": "failed", "via": "none"}
