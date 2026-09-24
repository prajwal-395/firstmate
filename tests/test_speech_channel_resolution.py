"""The build resolves the speech channel or refuses it loudly.

The catalog records which stream is the program mix (declared or
measured); the build must honour that record rather than falling back
to a silent channel 1. These tests cover the resolution both builders
share - the 6.01 master path resolves per angle, the reel path reads
the project's declaration first - and the refusal an undeclared,
unmeasurable source earns instead of a quiet default.
"""

import json

import pytest

from library.steps.step_6_01_render.resolve_build_timeline import (
    SpeechChannelRefused,
    resolve_speech_channel,
)


def test_manifest_declaration_wins():
    channel, basis = resolve_speech_channel(
        "a", "Akshita", 2, {"a.MXF"}, {"/x/a.MXF": 1, "a.MXF": 1}, {},
        1, {"a.MXF"})
    assert (channel, basis) == (2, "manifest angle declaration")


def test_project_declaration_beats_a_stale_catalog_recording():
    channel, basis = resolve_speech_channel(
        "a", "Akshita", None, {"a.MXF"}, {"a.MXF": 2}, {}, 1, set())
    assert channel == 1
    assert "declaration" in basis


def test_catalog_recording_is_honoured():
    channel, basis = resolve_speech_channel(
        "a", "Akshita", None, {"a.MXF"}, {"a.MXF": 3}, {}, None, set())
    assert channel == 3
    assert "catalog" in basis


def test_a_single_stream_source_is_its_own_answer():
    channel, basis = resolve_speech_channel(
        "main", "main", None, {"phone.MOV"}, {}, {}, None,
        {"phone.MOV"})
    assert (channel, basis) == (1, "single-stream source")


def test_every_source_on_an_angle_must_agree():
    channel, _ = resolve_speech_channel(
        "a", "Akshita", None, {"a.MXF", "b.MXF"},
        {"a.MXF": 1, "b.MXF": 1}, {}, None, set())
    assert channel == 1
    with pytest.raises(SpeechChannelRefused, match="different program"):
        resolve_speech_channel(
            "a", "Akshita", None, {"a.MXF", "b.MXF"},
            {"a.MXF": 1, "b.MXF": 2}, {}, None, set())


def test_an_undeclared_multi_stream_source_refuses_loudly():
    with pytest.raises(SpeechChannelRefused,
                       match="REFUSING to place speech") as exc:
        resolve_speech_channel(
            "a", "Akshita", None, {"cam.MXF"}, {}, {}, None, set())
    assert "cam.MXF" in str(exc.value)
    assert "source.program_stream" in str(exc.value)


def test_a_catalog_refusal_is_carried_into_the_build_refusal():
    with pytest.raises(SpeechChannelRefused,
                       match="declared or recorded"):
        resolve_speech_channel(
            "a", "Akshita", None, {"cam.MXF"}, {},
            {"cam.MXF": "Refusal: cam.MXF carries 4 audio streams"},
            None, set())


def test_a_garbage_manifest_declaration_refuses_not_tracebacks():
    with pytest.raises(SpeechChannelRefused,
                       match="not a channel ordinal"):
        resolve_speech_channel(
            "a", "Akshita", "CH1", {"a.MXF"}, {"a.MXF": 1}, {}, None,
            set())
