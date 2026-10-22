"""AXI-012b: the gain executes, and the render says what it played.

The defect this file names: an audio gain that is DECLARED and REFUSED
at the same time.  Until 2026-10-21 the operation map said the gain was
implemented and the executor raised before it wrote, because the
candidate receipt carried no independent audio-render evidence - so a
caller that believed the map got a RuntimeError instead of an edit.

The rules these tests hold:

* the gain's route is the NATIVE one, and it does not rebuild the
  timeline;
* the executor for the gain is REGISTERED on the backend the operation
  map routes it to, and the refusing path is gone;
* the measurement the executor rests on is SHIPPED, and it says the
  rendered audio measured the requested dB, held the rest of the mix,
  and held the linked A/V group;
* a measurement that does NOT match the request fails: the verdict is
  not a rubber stamp on whatever the render said;
* an untouched window that moved is a mix that moved;
* a silent window is not a window that held its level;
* the pan, which no executor writes, still refuses BY NAME - and the
  refusal names the route that was measured, so the next executor has a
  measured spelling instead of a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from library.tools import audio_operations
from library.tools import audio_render_evidence as evidence_module

ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / "tests" / "live" / "reports" / "axi012b-audio-gain.json"


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ── the declaration ──────────────────────────────────────────────────


def test_the_gain_is_declared_native_and_does_not_rebuild():
    """Defect: the OTIO route REBUILDS the timeline, and a gain sent
    through it costs the Fusion comps and CDL grades on that timeline.
    A single level is written natively (Resolve 21.1's AudioVolume)."""
    gain = audio_operations.operation("audio.gain")
    assert gain.route == audio_operations.ROUTE_NATIVE
    assert gain.rebuilds is False
    assert "AudioVolume" in gain.resolve_call
    assert gain.supported is True
    assert audio_operations.refusal("audio.gain") == ""


def test_the_gain_names_the_measurement_it_rests_on():
    """Defect: a route that does not say how it is verified reads as
    verified. The gain's audible verification is a RENDER, measured
    against the requested dB, with the other windows held."""
    gain = audio_operations.operation("audio.gain")
    assert "render" in gain.audible_verification.lower()
    assert gain.read_back.strip()
    assert any("mix" in item for item in gain.preserves)
    assert any("link" in item.lower() for item in gain.preserves)


# ── the executor ─────────────────────────────────────────────────────


def test_the_gain_executor_is_registered_on_the_backend_that_routes_it():
    """Defect: an operation the map routes to a backend with no
    executor dies in the dispatcher with "no callable adapter is
    registered" - the declaration and the dispatcher must agree."""
    from library.tools import edit_execution_adapters, resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()
    registered = set(resolve_axi_core.registered_edit_executors())
    assert ("audio.gain", "resolve_axi") in registered
    assert ("audio.gain", "reel_touchup") not in registered, (
        "the touch-up backend refused the gain for want of a rendered "
        "audio measurement; the measurement exists now, so the refusing "
        "route must be gone rather than kept beside the working one")


def test_the_gain_is_routed_to_the_backend_that_executes_it():
    """Defect: a router that sends the gain to a backend with no
    executor refuses every request for it."""
    from library.tools import edit_algebra

    status = edit_algebra.status("audio.gain")
    assert status is not None
    assert status.for_backend("resolve_axi") is not None
    assert status.for_backend("reel_touchup") is None


# ── the measurement ──────────────────────────────────────────────────


def test_the_request_time_measurement_is_shipped():
    """Defect: a module that quotes a measurement it does not ship is a
    module that quotes prose. The report is in the tree, it is the
    report the executor names, and it holds every state."""
    assert REPORT.is_file(), (
        f"{REPORT} is missing: the gain's evidence has to be in the "
        "tree, not in a lane's scratch")
    report = _report()
    assert report["errors"] == []
    assert report["resolve_version"] == "21.1.0.14"
    assert report["gain"]["verified"] is True


def test_the_rendered_audio_measured_the_requested_gain():
    """Defect: a gain that reads back -6.0 and renders at -3 dB is a
    gain that lies, and a read-back cannot see it. The RENDER is what
    says."""
    reading = _report()["gain"]["audio_evidence"]
    assert reading["requested_db"] == -6.0
    assert reading["measured_db"] == -6.0, (
        f"the render measured {reading['measured_db']} dB against a "
        "-6.00 dB request")
    assert reading["target_matches_request"] is True


def test_the_rendered_audio_showed_the_mix_did_not_move():
    """Defect: a gain that silences or moves a clip nobody asked about
    is not a gain. The untouched clip's window is the only number that
    says the mix held."""
    reading = _report()["gain"]["audio_evidence"]
    assert reading["mix_preserved"] is True
    assert reading["windows_moved"] == []
    other = _report()["other"]["uid"]
    untouched = reading["readings"][other]
    assert untouched["delta_db"] == 0.0, (
        f"the untouched clip moved {untouched['delta_db']} dB")


def test_the_linked_av_group_survived_the_write():
    """Defect: a gain that unlinks a clip's picture from its sound
    breaks the A/V the edit was built on."""
    gain = _report()["gain"]
    assert gain["linked_preserved"] is True
    assert gain["linked_before"] == gain["linked_after"]
    assert gain["linked_before"], "the target carried no linked partner"


def test_the_measurement_restored_the_level_it_found():
    """Defect: a measurement that leaves the level it wrote behind
    changes the timeline it was measuring. The probe restores it."""
    gain = _report()["gain"]
    assert gain["restored"] is True
    assert gain["restored_level"]["level_db"] == (
        gain["previous_level"]["level_db"])


# ── the verdict, on measurements that are not the good ones ──────────


def _windows() -> dict:
    return {"target": {"start": 0.0, "duration": 1.0},
            "other": {"start": 2.0, "duration": 1.0}}


def test_a_gain_that_rendered_at_the_wrong_level_fails(monkeypatch):
    """Defect: a verdict that passes whatever the render measured is a
    rubber stamp. A render 3 dB away from the request must fail."""
    def _fake_measure(path, start=None, duration=None):
        return {"window_seconds": 1.0,
                "rms_db": -10.0 if path == "before" else -13.0,
                "peak_db": -5.0, "digital_zero_fraction": 0.0}

    monkeypatch.setattr(evidence_module, "measure_window", _fake_measure)
    verdict = evidence_module.gain_evidence(
        "before", "after", _windows(), -6.0, target="target")
    assert verdict["measured_db"] == -3.0
    assert verdict["target_matches_request"] is False


def test_an_untouched_window_that_moved_fails(monkeypatch):
    """Defect: a gain that moves a clip nobody asked about is a mix
    that moved, and the verdict has to say so."""
    def _fake_measure(path, start=None, duration=None):
        if path == "before":
            level = -20.0
        else:
            level = -20.0 if (start or 0) < 1.5 else -12.0
        return {"window_seconds": 1.0, "rms_db": level, "peak_db": level,
                "digital_zero_fraction": 0.0}

    monkeypatch.setattr(evidence_module, "measure_window", _fake_measure)
    verdict = evidence_module.gain_evidence(
        "before", "after", _windows(), 0.0, target="target")
    assert verdict["mix_preserved"] is False
    assert [row["window"] for row in verdict["windows_moved"]] == ["other"]


def test_a_silent_window_is_not_a_window_that_held_its_level():
    """Defect: a window that is silent in one render and loud in the
    other has no comparable level, and a verdict that reads it as
    "unchanged" hides a mute behind a zero."""
    def _fake_measure(path, start=None, duration=None):
        if path == "before" and (start or 0) >= 1.5:
            return {"window_seconds": 1.0, "rms_db": None, "peak_db": None,
                    "digital_zero_fraction": 1.0}
        return {"window_seconds": 1.0, "rms_db": -20.0, "peak_db": -5.0,
                "digital_zero_fraction": 0.0}

    original = evidence_module.measure_window
    try:
        evidence_module.measure_window = _fake_measure
        verdict = evidence_module.gain_evidence(
            "before", "after", _windows(), 0.0, target="target")
    finally:
        evidence_module.measure_window = original
    assert verdict["mix_preserved"] is False
    assert verdict["windows_moved"][0]["window"] == "other"


def test_the_measurement_window_sits_inside_the_clip():
    """Defect: a window that straddles a cut mixes two clips' audio
    into one number, and the number then describes neither."""
    window = evidence_module.item_window(0, 96, 24.0, 0)
    assert window["start"] > 0.0
    assert window["start"] + window["duration"] < 4.0


# ── the pan, which no executor writes ────────────────────────────────


def test_the_pan_still_refuses_by_name_and_names_the_measured_route():
    """Defect: a declaration that claims a route nothing takes is worse
    than a refusal. The pan's route is MEASURED - and no executor
    writes it, so the operation refuses by name and says so."""
    pan = audio_operations.operation("audio.pan")
    assert pan.route == audio_operations.ROUTE_REFUSED
    assert pan.supported is False
    refusal = audio_operations.refusal("audio.pan")
    assert "audio.pan" in refusal
    assert "AudioPan" in refusal
    assert "no executor" in refusal


def test_the_measured_pan_moves_the_sound():
    """Defect: a pan that reads back and renders flat is not a pan. The
    render's channels have to follow the value."""
    sweep = _report()["pan"]["sweep"]
    left = sweep["-100.0"]["balance"]
    right = sweep["100.0"]["balance"]
    centre = sweep["0.0"]["balance"]
    assert left["balance_db"] >= 6.0, (
        f"a hard left pan moved the balance by only {left['balance_db']} dB")
    assert right["balance_db"] <= -6.0
    assert centre["balance_db"] == 0.0
    assert centre["difference_db"] is None, (
        "at centre the two channels must carry the same thing, so their "
        "difference is digital silence")
    assert _report()["pan"]["audible"] is True
