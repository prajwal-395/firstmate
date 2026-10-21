"""AXI-012: the audio operations declare the route that actually writes them.

The defect this file names: a capability declaration that does not match
the executor.  An agent reads "gain is implemented", asks for a level,
and the request dies inside an executor that has been refusing since it
was written - because the map never said WHICH route writes the gain, or
that the route is a build.

The rules these tests hold:

* every audio operation declares a route, and the route is one of the
  three the module enumerates;
* an operation with a route says WHAT writes it and what reads it back;
* an operation with NO route refuses BY NAME, and the refusal names the
  operation;
* a route that rebuilds the timeline says so, because a rebuild is not a
  preservation;
* nothing declares a Resolve spelling Ren has not measured - the old
  `SetProperty("Volume")` spelling is not one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import audio_operations

REPORT = (Path(__file__).resolve().parents[3] / "tests" / "live" / "reports"
          / "axi012-audio-measurement.json")


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _state(name: str) -> dict:
    return next(row for row in _report()["renders"] if row["name"] == name)


def test_every_audio_operation_declares_a_route():
    """Defect: an audio operation with no declared route is a capability
    declaration that says nothing, which is how a caller asks for it and
    gets an executor's RuntimeError instead of a refusal."""
    assert audio_operations.AUDIO_OPERATIONS
    for row in audio_operations.AUDIO_OPERATIONS:
        assert row.route in audio_operations.ROUTES, row.operation_id
        assert row.intent.strip(), row.operation_id


def test_a_supported_operation_names_its_writer_and_its_read_back():
    """Defect: a route that does not say what writes it cannot be
    checked, and a route that does not say what reads it back cannot be
    verified."""
    for row in audio_operations.AUDIO_OPERATIONS:
        if not row.supported:
            continue
        assert row.read_back.strip(), row.operation_id
        assert row.audible_verification.strip(), row.operation_id


def test_a_refused_operation_refuses_by_name():
    """Defect: a refusal that does not name the operation cannot be
    routed to the operation that raised it."""
    for row in audio_operations.AUDIO_OPERATIONS:
        if row.supported:
            continue
        assert row.refusal.strip(), row.operation_id
        assert row.operation_id in row.refusal, row.operation_id


def test_a_supported_operation_has_no_refusal():
    """Defect: an operation with a route that also carries a refusal
    reads as two truths, and a caller cannot tell which one it gets."""
    for row in audio_operations.AUDIO_OPERATIONS:
        if row.supported:
            assert audio_operations.refusal(row.operation_id) == ""


def test_a_rebuilding_route_says_it_rebuilds():
    """Defect: the OTIO route REBUILDS the timeline. A declaration that
    does not say so reads as a preservation it cannot keep - Fusion comps
    and CDL grades do not survive an import."""
    gain = audio_operations.operation("audio.gain")
    assert gain.route == audio_operations.ROUTE_OTIO
    assert gain.rebuilds is True
    assert any("REBUILD" in item for item in gain.evidence)


def test_the_native_fade_route_does_not_rebuild():
    """Defect: a native write that claimed a rebuild would send every
    in-place fade through an unnecessary import."""
    fade = audio_operations.operation("audio.fade")
    assert fade.route == audio_operations.ROUTE_NATIVE
    assert fade.rebuilds is False
    assert "SetFades" in fade.resolve_call


def test_the_native_mute_route_names_its_call_and_keeps_the_span():
    """Defect: a mute that changed the clip's span would be a deletion
    wearing a mute's name.  The mute is the enablement call, so the
    declaration has to name the algebra id that carries it."""
    mute = audio_operations.operation("clip.set_enabled")
    assert mute.audio_operation == "mute"
    assert mute.route == audio_operations.ROUTE_NATIVE
    assert "SetClipEnabled" in mute.resolve_call
    assert "GetClipEnabled" in mute.read_back
    assert any("record span" in item for item in mute.preserves)


def test_no_route_declares_the_unmeasured_volume_spelling():
    """Defect: `SetProperty("Volume")` is not a spelling Resolve 21.1
    accepts - the item's property dictionary carries `AudioVolume`. A
    declaration naming the old spelling declares a call that returns
    False."""
    for row in audio_operations.AUDIO_OPERATIONS:
        assert "SetProperty(\"Volume\")" not in row.resolve_call


def test_an_unknown_audio_operation_is_refused_by_name():
    """Defect: a lookup that raises KeyError with no name tells the
    caller nothing about which operation it asked for."""
    with pytest.raises(KeyError) as raised:
        audio_operations.operation("audio.loudness")
    assert "audio.loudness" in str(raised.value)


def test_every_row_publishes_itself_for_the_operation_map():
    """Defect: a published table that drops a field makes the map's row
    and the module's row disagree without either being wrong."""
    rows = audio_operations.rows()
    assert len(rows) == len(audio_operations.AUDIO_OPERATIONS)
    for row in rows:
        assert row["operation_id"] in audio_operations.BY_ID
        assert row["route"] in audio_operations.ROUTES
        assert set(row) == {
            "operation_id", "audio_operation", "intent", "route",
            "resolve_call", "read_back", "audible_verification",
            "applies_to", "preserves", "rebuilds", "refusal", "evidence"}


def test_the_operation_map_publishes_the_route_of_every_audio_operation():
    """Defect: the operation map is what an agent reads before it asks
    for an edit.  An audio operation with no route in the map is a
    declaration that says nothing, and the caller finds out from an
    executor's RuntimeError instead."""
    from library.tools import operation_map

    entries = operation_map.operation_map()["operations"]
    for row in audio_operations.AUDIO_OPERATIONS:
        published = entries[row.operation_id]["audio_route"]
        assert published is not None, row.operation_id
        assert published["route"] == row.route
        assert published["rebuilds"] == row.rebuilds
        assert published["refusal"] == audio_operations.refusal(
            row.operation_id)


def test_the_operation_map_publishes_no_route_for_a_non_audio_operation():
    """Defect: a row on every operation would make the audio route look
    like a property of the whole registry."""
    from library.tools import operation_map

    entries = operation_map.operation_map()["operations"]
    assert entries["clip.delete"]["audio_route"] is None
    assert entries["overlay.resize"]["audio_route"] is None


# ── The measurement the declarations rest on ──────────────────────────


def test_the_measurement_is_shipped_with_the_declarations():
    """Defect: a module that quotes a measurement it does not ship is a
    module that quotes prose.  The report is in the repository, it is the
    report the module names, and it holds every state."""
    assert REPORT.is_file(), (
        f"{REPORT} is missing: the audio operations' evidence has to be "
        "in the tree, not in a lane's scratch")
    report = _report()
    assert report["errors"] == []
    assert [row["name"] for row in report["renders"]] == [
        "unity", "gain_minus6", "muted", "faded", "otio_gain_minus6",
        "otio_automation"]
    assert report["resolve_version"] == (
        audio_operations.MEASURED_EVIDENCE["resolve_version"])


def test_the_measured_gain_is_the_dB_that_was_asked_for():
    """Defect: a gain that reads back -6.0 and renders at -3 dB is a
    gain that lies, and a read-back cannot see it.  The RENDER is what
    says."""
    unity = _state("unity")["clip_a"]["rms_db"]
    gained = _state("gain_minus6")["clip_a"]["rms_db"]
    assert unity is not None and gained is not None
    assert abs((unity - gained) - 6.0) <= 0.3, (
        f"a -6 dB request measured {unity - gained:.2f} dB in the render")


def test_the_measured_mute_is_digital_silence():
    """Defect: a mute that only makes the clip quieter is a fade, and a
    reader that judges on loudness cannot tell the two apart."""
    muted = _state("muted")["clip_a"]
    assert muted["digital_zero_fraction"] == 1.0, (
        "the muted clip's window must be digital zero, every sample")


def test_the_measured_fade_is_audible_at_its_head():
    """Defect: a fade that reads back and renders flat is a fade that
    did not happen."""
    head = _state("faded")["clip_a_head"]["rms_db"]
    tail = _state("faded")["clip_a_tail"]["rms_db"]
    assert head is not None and tail is not None
    assert tail - head >= 3.0, (
        f"the fade's head ({head} dBFS) must be well below its tail "
        f"({tail} dBFS)")


def test_the_measured_states_leave_the_other_clip_alone():
    """Defect: a mix that moved a clip nobody asked about is not a mix,
    and the level of the untouched clip is the only number that says so.
    This is the 'the existing mix is preserved' half of the brief."""
    levels = {row["name"]: row["clip_b"]["rms_db"]
              for row in _report()["renders"]}
    assert len(set(levels.values())) == 1, (
        f"the untouched clip's level moved between states: {levels}")


def test_the_measured_automation_carries_its_curve():
    """Defect: a keyframed level that renders flat is a static level, and
    the curve is the whole point of the operation."""
    head = _state("otio_automation")["clip_a_head"]["rms_db"]
    unity = _state("unity")["clip_a_head"]["rms_db"]
    assert head is not None and unity is not None
    assert unity - head >= 6.0, (
        f"the automation's head ({head} dBFS) must be well below unity "
        f"({unity} dBFS): the curve is not audible")
