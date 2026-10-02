"""The plan says how long a sound plays, inside what the sound measures, and how it fades.

A requested length past what the file measures is REFUSED BY NAME and
never clamped; no length plays the whole sound; a sound cut short or
given a stated fade carries its ramp to the OTIO keyframes. The fixture
carries `whoosh_impact.mp3`'s real measured numbers from the run of
record. History: docs/evidence/sfx_duration.md.
"""
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_04_plan_sfx.post_bridge import resolve_sfx  # noqa: E402
from library.tools.otio_mix import (  # noqa: E402
    MIN_VOLUME_DB,
    declick_curve,
    mix_targets,
)
from library.tools.sfx_duration import (  # noqa: E402
    DECLICK_FADE_FRAMES,
    SfxDurationRefused,
    declick_fade_seconds,
    resolve_played_seconds,
)
from library.tools.sfx_library import (  # noqa: E402
    catalog_document,
    load_sfx_catalog,
)

# The run of record, exactly.
WHOOSH = "whoosh_impact.mp3"
WHOOSH_SECONDS = 8.04
WHOOSH_TRANSIENT = 0.714
RUN_OF_RECORD_REQUEST = 0.25
FPS = 30.0


@pytest.fixture
def entry(tmp_path):
    """`whoosh_impact.mp3`'s measurements, on a library of our own."""
    lib = tmp_path / "sfx"
    lib.mkdir()
    audio = lib / WHOOSH
    audio.write_bytes(b"ID3\x03\x00\x00\x00")
    (lib / "sfx_index.json").write_text(json.dumps([{
        "file": WHOOSH,
        "path": str(audio),
        "folder_category": "Whoosh",
        "description": "A sharp air movement resolving into a low impact.",
        "technical": {
            "basic": {"duration": WHOOSH_SECONDS},
            "energy_profile": {"envelope_shape": "punchy"},
        },
        "transient_offset_sec": WHOOSH_TRANSIENT,
    }]), encoding="utf-8")
    catalog = load_sfx_catalog(str(lib))
    assert len(catalog) == 1
    return catalog[0]


# ── The bound ─────────────────────────────────────────────────────────

def test_a_stated_length_is_honoured_and_no_length_plays_the_whole_sound(
        entry):
    """0.25 s of an 8.04 s whoosh is what the plan asked for; declaring
    no length is the ABSENCE of a decision."""
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, RUN_OF_RECORD_REQUEST, FPS) == \
        RUN_OF_RECORD_REQUEST
    assert resolve_played_seconds(entry, 0.0, None, FPS) == WHOOSH_SECONDS
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, None, FPS) == pytest.approx(
            WHOOSH_SECONDS - WHOOSH_TRANSIENT)


def test_a_length_past_the_measured_one_is_refused_not_clamped(entry):
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, 0.0, WHOOSH_SECONDS + 0.5, FPS)
    message = str(raised.value)
    assert WHOOSH in message
    assert "8.04" in message
    assert "Nothing is clamped" in message


def test_the_transient_trim_is_inside_the_bound(entry):
    """`punchy` skips 0.714 s, so 8.04 s is no longer askable."""
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, WHOOSH_TRANSIENT, WHOOSH_SECONDS, FPS)
    assert "after the transient trim" in str(raised.value)
    remaining = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, remaining, FPS) == pytest.approx(remaining)


def test_the_catalogue_advertised_duration_is_accepted_by_the_contract():
    """Regression: a model copying the catalogue's advertised length was
    refused, because the catalogue rounded UP past the measured length
    (0.4599999 formatted as 0.46)."""
    catalog = [
        {"sfx_id": "camera soft click.wav",
         "duration_seconds": 0.4591609977324263, "category": "Foley"},
        {"sfx_id": "camera-shutter-6305.mp3",
         "duration_seconds": 0.3395918367346939, "category": "Foley"},
        {"sfx_id": "edge-case.wav",
         "duration_seconds": 0.4599999, "category": "Foley"},
    ]
    doc = catalog_document(catalog)
    for row in catalog:
        match = re.search(re.escape(row["sfx_id"])
                          + r".*?category Foley \| plays for ([\d\.]+) s",
                          doc, re.DOTALL)
        assert match, f"no identity line for {row['sfx_id']} in:\n{doc}"
        advertised = float(match.group(1))
        assert resolve_played_seconds(row, 0.0, advertised, 30.0) == \
            advertised


# ── The click ─────────────────────────────────────────────────────────

def test_a_truncated_sound_gets_a_ramp_and_a_whole_one_does_not():
    playable = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    cut_short = declick_fade_seconds(RUN_OF_RECORD_REQUEST, playable, FPS)
    assert cut_short == pytest.approx(DECLICK_FADE_FRAMES / FPS)
    assert declick_fade_seconds(playable, playable, FPS) == 0.0


def test_the_manifest_carries_the_ramp_to_the_mix_route():
    """Manifest -> `mix_targets` -> keyframes, which is the real seam
    (AGENTS.md 10.2): a dB's reader is `otio_mix`."""
    frames = int(round(RUN_OF_RECORD_REQUEST * FPS))
    manifest = {"tracks": {"A3": {"clips": [
        {"source_file": "/sfx/whoosh_impact.mp3", "label": "sfx_001",
         "timeline_in_frame": 1038, "timeline_out_frame": 1038 + frames,
         "volume_db": -18, "fade_out_seconds": DECLICK_FADE_FRAMES / FPS},
        {"source_file": "/sfx/riser_2.mp3", "label": "sfx_002",
         "timeline_in_frame": 1384, "timeline_out_frame": 1474,
         "volume_db": -14, "fade_out_seconds": 0.0},
    ]}}}
    cut_short, whole = mix_targets(manifest, fps=FPS)

    assert cut_short["role"] == "sfx"
    assert cut_short["keyframes"][0] == -18.0
    assert cut_short["keyframes"][frames - 1] == -100.0
    assert cut_short["keyframes"][frames - 1 - DECLICK_FADE_FRAMES] == -18.0
    # A sound that plays to its own end keeps the static level it had.
    assert whole["keyframes"] == {}
    assert whole["level_db"] == -14.0


# ── A stated fade (rung 7, E3: fade lengths in frames) ────────────────

def _fade_spine():
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 110.0,
         "timeline_start": 8.0, "timeline_end": 18.0,
         "word_timestamps": [
             {"word": "tell", "source_start": 100.0,
              "source_end": 100.4},
         ],
         "alignment_method": "mfa"},
    ]}


def _resolve_fade(**over):
    plan = {"sfx_id": "test_whoosh.wav", "spine_block_position": 1,
            "volume_db": -12.0, "anchor": {"word": "tell"},
            "rationale": "on the word", **over}
    catalog = [{
        "sfx_id": "test_whoosh.wav",
        "path": "/nonexistent/test_whoosh.wav",
        "category": "Accents",
        "duration_seconds": 1.2,
        "envelope": "punchy",
        "transient_offset_sec": 0.05,
    }]
    (placed,) = resolve_sfx([plan], _fade_spine(), [], {}, {}, FPS, {}, {},
                            catalog=catalog)["sfx_list"]
    return placed


def test_a_stated_fade_ships():
    for key, seconds, said in (
            ("fade_out_seconds", 0.5, "fades out over 0.500s (stated)"),
            ("fade_in_seconds", 0.25, "fades in over 0.250s (stated)")):
        placed = _resolve_fade(**{key: seconds})
        assert placed[key] == pytest.approx(seconds)
        assert said in placed["placement_method"]


def test_fade_frames_match_seconds():
    assert _resolve_fade(fade_out_frames=15)["fade_out_seconds"] == \
        pytest.approx(_resolve_fade(fade_out_seconds=0.5)["fade_out_seconds"])


def test_the_head_ramp_reaches_the_curve():
    """The stated fade-in renders as silence-to-level at the head."""
    keys = declick_curve(0.0, fps=FPS, clip_frame_count=36,
                         level_db=-12.0, fade_in_seconds=0.5)
    frames = sorted(keys)
    assert keys[frames[0]] == MIN_VOLUME_DB
    assert keys[15] == -12.0
    assert keys[frames[-1]] == -12.0
