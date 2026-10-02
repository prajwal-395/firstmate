"""The build resolves the speech channel or refuses it loudly.

The catalog records which stream is the program mix (declared or
measured); the build must honour that record rather than falling back
to a silent channel 1. These tests cover the resolution both builders
share - the 6.01 master path resolves per angle, the reel path reads
the project's declaration first - and the refusal an undeclared,
unmeasurable source earns instead of a quiet default.
"""

import pytest

from library.steps.step_6_01_render.resolve_build_timeline import (
    SpeechChannelRefused,
    mapping_carries_program,
    resolve_speech_channel,
)

# (angle_sources, catalog_channels, catalog_refusals, manifest_decl,
#  project_decl, single_stream, expected_channel, basis_fragment)
RESOLVED = [
    # The manifest's own angle declaration wins over everything.
    ({"a.MXF"}, {"/x/a.MXF": 1, "a.MXF": 1}, {}, 2, 1, {"a.MXF"},
     2, "manifest angle declaration"),
    # The project's declaration beats a stale catalog recording.
    ({"a.MXF"}, {"a.MXF": 2}, {}, None, 1, set(), 1, "declaration"),
    # A catalog recording is honoured when nothing is declared.
    ({"a.MXF"}, {"a.MXF": 3}, {}, None, None, set(), 3, "catalog"),
    # A single-stream source is its own answer.
    ({"phone.MOV"}, {}, {}, None, None, {"phone.MOV"},
     1, "single-stream source"),
    # Every source on an angle agrees.
    ({"a.MXF", "b.MXF"}, {"a.MXF": 1, "b.MXF": 1}, {}, None, None, set(),
     1, "catalog"),
]

REFUSED = [
    # Sources on one angle recorded on different program channels.
    ({"a.MXF", "b.MXF"}, {"a.MXF": 1, "b.MXF": 2}, {}, None,
     ["different program"]),
    # An undeclared multi-stream source refuses loudly, naming the fix.
    ({"cam.MXF"}, {}, {}, None,
     ["REFUSING to place speech", "cam.MXF", "source.program_stream"]),
    # The catalog's own refusal is carried into the build refusal.
    ({"cam.MXF"}, {}, {"cam.MXF": "Refusal: cam.MXF carries 4 audio streams"},
     None, ["declared or recorded"]),
    # A garbage manifest declaration refuses rather than tracebacks.
    ({"a.MXF"}, {"a.MXF": 1}, {}, "CH1", ["not a channel ordinal"]),
]


def test_the_speech_channel_resolves_by_precedence():
    for (sources, recorded, refusals, manifest_decl, project_decl, single,
         expected, basis_fragment) in RESOLVED:
        channel, basis = resolve_speech_channel(
            "a", "Akshita", manifest_decl, sources, recorded, refusals,
            project_decl, single)
        assert channel == expected, (sources, recorded, basis)
        assert basis_fragment in basis, (sources, basis)


def test_an_unresolvable_speech_channel_refuses_by_name():
    for sources, recorded, refusals, manifest_decl, fragments in REFUSED:
        with pytest.raises(SpeechChannelRefused) as exc:
            resolve_speech_channel(
                "a", "Akshita", manifest_decl, sources, recorded, refusals,
                None, set())
        for fragment in fragments:
            assert fragment in str(exc.value), (fragment, str(exc.value))


def test_a_mapping_carries_the_program_stream_when_it_includes_it():
    """Finding 4: iPhone stereo speech maps CH[1, 2] and carries
    program CH1 in it. Exact-equality (`channels == [1]`) deleted
    every such item as "non-program audio" - twelve deletions, an
    export at -91 dB over the spoken hook, the step green."""
    assert mapping_carries_program([1, 2], 1) is True
    assert mapping_carries_program([1], 1) is True
    assert mapping_carries_program([2], 1) is False
    assert mapping_carries_program([3, 4], 1) is False
    assert mapping_carries_program([], 1) is False
    assert mapping_carries_program(None, 1) is False
