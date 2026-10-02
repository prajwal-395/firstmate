"""The bed is a SEQUENCE - several sections, several tracks, spliced.

Captain, 2026-09-01: *"its not one continuous stretch from the music we
have to use, like we can use bits and pieces, or multiple tracks, and
splice pieces from different tracks"*.

These drive `library/tools/music_bed.py` and the real `compile_manifest`
against a fake project, so the thing asserted is a manifest that was
compiled rather than one that was written down.
"""
import os
import sys
import pytest
from pathlib import Path
from library.steps.step_5_02_audio_mix.mix import measure, solve_automation
from library.steps.step_5_04_compile_manifest.step import _spine_block_entry
from library.tools.music_behavior import (
    MUSIC_BEHAVIORS,
    MusicBehaviorError,
    WITHDRAWN_BEHAVIORS,
    resolve_music_behavior,
)
from library.tools.spine_contract import SpineContractError, validate_spine_blocks
from unittest.mock import patch
import ast


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.music_bed import (  # noqa: E402
    BED_KEY,
    MusicBedError,
    resolve_bed,
    track_table,
)

TRACK_A = "/music/one.wav"
TRACK_B = "/music/two.wav"


def _selection(**over):
    base = {
        "title": "One", "audio_path": TRACK_A, "duration_seconds": 300.0,
        "tracks": [{"title": "Two", "audio_path": TRACK_B,
                    "duration_seconds": 240.0}],
    }
    base.update(over)
    return base


def _spine(bed=None, blocks=4, block_seconds=15.0):
    structure = []
    for i in range(blocks):
        structure.append({
            "position": i + 1,
            "block_type": "speech",
            "timeline_start": round(i * block_seconds, 3),
            "timeline_end": round((i + 1) * block_seconds, 3),
            "music_behavior": "background",
        })
    spine = {"structure": structure, "frame_rate": 30.0}
    if bed is not None:
        spine[BED_KEY] = bed
    return spine


# ── Ceiling 1: one section ───────────────────────────────────────────

def test_two_disjoint_sections_of_one_track_are_playable():
    """The ceiling `music_section` could not clear: 12s and 180s of the
    same file, in one bed."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 12.0, "why": "the sparse intro"},
        {"source_in": 180.0, "starts_at_block": 3, "why": "the settled body"},
    ]), 60.0)
    assert bed.declared
    assert [s.source_in for s in bed.segments] == [12.0, 180.0]
    assert [s.timeline_start for s in bed.segments] == [0.0, 30.0]
    assert bed.tracks_used == [TRACK_A]
    assert bed.splice_count == 1


# ── Ceiling 2: one track ─────────────────────────────────────────────

def test_the_bed_can_be_pieces_of_several_tracks():
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"track": "Two", "source_in": 90.0, "starts_at_block": 2},
        {"source_in": 200.0, "starts_at_block": 4},
    ]), 60.0)
    assert [s.audio_path for s in bed.segments] == [TRACK_A, TRACK_B, TRACK_A]
    assert bed.tracks_used == [TRACK_A, TRACK_B]


def test_the_primary_track_is_the_one_a_segment_naming_none_plays():
    rows = track_table(_selection())
    assert [r["role"] for r in rows] == ["primary", "additional"]
    bed = resolve_bed(_selection(), _spine([{"source_in": 5.0}]), 60.0)
    assert bed.segments[0].audio_path == TRACK_A


# ── Ceiling 3: no conducting, and the absence of one ─────────────────

def test_a_spine_declaring_no_bed_gets_exactly_the_one_section_placement():
    """Every run before this one, unchanged: one clip, from the chosen
    section, under the whole video."""
    bed = resolve_bed(_selection(section={"source_in": 42.0}), _spine(), 60.0)
    assert not bed.declared
    assert len(bed.segments) == 1
    assert bed.segments[0].source_in == 42.0
    assert bed.segments[0].timeline_start == 0.0
    assert bed.segments[0].timeline_end == 60.0
    assert "one chosen section" in bed.reason


# ── The crossfade, and the refusal to invent one ─────────────────────

def test_a_splice_with_no_declared_crossfade_is_a_hard_splice():
    """No length is substituted - the absence of decoration, not a
    choice of it."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"source_in": 120.0, "starts_at_block": 3},
    ]), 60.0)
    assert bed.segments[1].crossfade_in_seconds == 0.0
    assert bed.segments[0].crossfade_out_seconds == 0.0
    assert bed.segments[0].placed_end == bed.segments[1].placed_start


def test_a_declared_crossfade_makes_the_two_pieces_OVERLAP():
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"source_in": 120.0, "starts_at_block": 3, "crossfade_seconds": 2.0},
    ]), 60.0)
    out, incoming = bed.segments
    assert out.placed_end == 32.0
    assert incoming.placed_start == 30.0
    assert out.placed_end > incoming.placed_start


# ── Refusals, none of which repair the plan ──────────────────────────

def test_each_unplayable_bed_is_refused_by_name_and_never_repaired():
    """Seven malformed beds; each refuses with its own reason."""
    rows = [
        # a track the selection did not choose: no nearest match
        ([{"source_in": 0.0},
          {"track": "/music/three.wav", "source_in": 0.0,
           "starts_at_block": 2}], "which the selection did not"),
        # a crossfade longer than the piece it fades into
        ([{"source_in": 0.0},
          {"source_in": 120.0, "starts_at_block": 4,
           "crossfade_seconds": 30.0}], "outlast"),
        # running past the end of its file
        ([{"source_in": 235.0, "track": "Two"}], "silent"),
        # out of order, never reordered
        ([{"source_in": 0.0},
          {"source_in": 10.0, "starts_at_block": 4},
          {"source_in": 20.0, "starts_at_block": 2}], "out of order"),
        # an unknown block
        ([{"source_in": 0.0},
          {"source_in": 10.0, "starts_at_block": "nowhere"}],
         "not a\\s+position on the spine"),
        # a later segment that does not say where it comes in
        ([{"source_in": 0.0}, {"source_in": 10.0}],
         "names no starts_at_block"),
        # no crossfade length invented for a malformed one
        ([{"source_in": 0.0},
          {"source_in": 10.0, "starts_at_block": 2,
           "crossfade_seconds": "long"}], "absence of a crossfade"),
    ]
    for bed, match in rows:
        with pytest.raises(MusicBedError, match=match):
            resolve_bed(_selection(), _spine(bed), 60.0)


# --------------------------------------------------------------------------
# From test_music_bed_end_and_fade.py
#
# Rung 7 (K1, SD3.2): the bed ends early and fades out on the plan's numbers.
#
# "Music out at 0:48 with a 2-second fade, then the last line dry" had
# no plan spelling: a bed piece ran until the next piece came in (the
# manifest's own words: "do not name an end"), and no fade-out existed
# beside the crossfade. `ends_at_block` (+ `end_offset_seconds` /
# `end_offset_frames`, E3) stops the piece; `fade_out_seconds` /
# `fade_out_frames` ramps its last seconds through `otio_mix`. What
# follows the end is silence under the picture - never a slide of the
# next piece. A fade beside a crossfade on one segment refuses (two
# endings), as does an end the piece cannot play (before its start,
# past the next piece's start, longer than the piece).

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.music_bed import (
    bed_clips,
)
from library.tools.otio_mix import MIN_VOLUME_DB, music_curve


def _selection_2(**over):
    base = {"title": "One", "audio_path": TRACK_A,
            "duration_seconds": 300.0}
    base.update(over)
    return base


def _spine_2(bed, blocks=4, block_seconds=15.0):
    structure = [{
        "position": i + 1,
        "block_type": "speech",
        "timeline_start": round(i * block_seconds, 3),
        "timeline_end": round((i + 1) * block_seconds, 3),
    } for i in range(blocks)]
    return {"structure": structure, "frame_rate": 30.0, BED_KEY: bed}


def test_a_piece_ends_early_and_the_rest_is_silence():
    """SD3.2's shape: out at block 4's start with a 2 s fade, dry after."""
    bed = resolve_bed(_selection_2(), _spine_2([
        {"source_in": 0.0, "ends_at_block": 4,
         "fade_out_seconds": 2.0, "why": "out before the last line"},
    ]), 60.0)
    (seg,) = bed.segments
    assert seg.timeline_start == 0.0
    assert seg.timeline_end == 45.0
    assert seg.fade_out_seconds == 2.0
    assert seg.ends_at_block == 4
    assert seg.played_seconds == 45.0
    assert seg.source_out == 45.0


def test_end_offset_in_frames_moves_the_end_off_the_block():
    bed = resolve_bed(_selection_2(), _spine_2([
        {"source_in": 0.0, "ends_at_block": 4, "end_offset_frames": 30,
         "why": "a second into the last block"},
    ]), 60.0)
    (seg,) = bed.segments
    assert seg.timeline_end == pytest.approx(46.0)


def test_the_fade_reaches_the_a2_curve():
    """The clip carries the fade; the curve ramps to silence at its end."""
    bed = resolve_bed(_selection_2(), _spine_2([
        {"source_in": 0.0, "ends_at_block": 4,
         "fade_out_seconds": 2.0, "why": "out"},
    ]), 60.0)
    (clip,) = bed_clips(bed, fps=30.0)
    assert clip["fade_out_seconds"] == 2.0
    assert clip["timeline_out"] == 45.0
    keys = music_curve(
        [{"timeline_start": 0.0, "timeline_end": 45.0,
          "target_level_db": -14.0}],
        fps=30.0, clip_start_frame=0,
        clip_frame_count=1350, fade_seconds=1.0,
        fade_out_seconds=2.0)
    frames = sorted(keys)
    assert frames[-1] == 1349
    assert keys[frames[-1]] == MIN_VOLUME_DB
    hold = 1349 - 60
    assert keys[hold] > MIN_VOLUME_DB


# --------------------------------------------------------------------------
# From test_music_bed_fits_each_block.py
#
# The bed is fitted per block to the decided separation, and a shortfall
# is reported on the row that misses. History (finding 25, the B4 run):
# docs/evidence/music_tests.md#bed-fits-each-block.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_02_audio_mix import mix as mix_module  # noqa: E402

BED_LUFS = -12.0
SEPARATION = 14.0


def _pre_output(speeches):
    """Two `background` windows with the given per-block speech."""
    return {
        "bed_measurements": {"measured": True,
                             "integrated_lufs": BED_LUFS,
                             "title": "bed", "audio_path": "bed.wav"},
        "mix_windows": [
            {"spine_block_position": i + 1,
             "block_type": "speech",
             "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5),
             "music_behavior": "background",
             "carries_speech": True,
             "speech_lufs": speech,
             "speech_loudness": ({"measured": True,
                                  "integrated_lufs": speech}
                                 if speech is not None else
                                 {"measured": False})}
            for i, speech in enumerate(speeches)
        ],
    }


def _by_scope(gain_at_reference):
    return {"background": {"value": gain_at_reference, "answer": SEPARATION},
            "prominent": {"value": gain_at_reference, "answer": SEPARATION}}


def test_each_block_hits_the_decided_separation():
    """The B4 shape: -20 and -39 LUFS speech under one behaviour. One
    level per behaviour misses by 19 dB on the quiet block; a per-block
    fit delivers 14.0 on both."""
    pre = _pre_output([-20.0, -39.0])
    # The scope-level gain, fitted to the loudest block as today.
    reference_gain = round((-20.0 - SEPARATION) - BED_LUFS, 2)
    automation, undetermined = mix_module.solve_automation(
        pre, _by_scope(reference_gain))

    assert undetermined == []
    assert len(automation) == 2
    gains = [row["target_level_db"] for row in automation]
    assert gains[0] != gains[1], (
        f"one gain for both blocks is the finding: {gains}")
    assert abs(gains[0] - gains[1] - 19.0) < 0.01
    for row in automation:
        assert row["separation_target_db"] == SEPARATION
        assert abs(row["separation_delivered_db"] - SEPARATION) < 0.01
        assert row["separation_shortfall_db"] == 0.0


def test_a_shortfall_is_reported_on_the_row_that_misses():
    """A window whose speech was never measured cannot be fitted: it
    keeps the scope gain, delivers nothing, and SAYS so on its own row
    and in the shortfall lines the post-bridge prints."""
    pre = _pre_output([-20.0, None])
    reference_gain = round((-20.0 - SEPARATION) - BED_LUFS, 2)
    automation, undetermined = mix_module.solve_automation(
        pre, _by_scope(reference_gain))

    calm, unmeasured = automation
    assert abs(calm["separation_delivered_db"] - SEPARATION) < 0.01
    assert unmeasured["separation_delivered_db"] is None
    assert unmeasured["separation_shortfall_db"] is None
    assert "speech" in (unmeasured["shortfall_basis"] or "").lower()

    lines = mix_module.shortfall_lines(automation)
    assert any("2" in line and "no measured speech" in line
               for line in lines), lines
    assert not any(line.startswith("Window 1") for line in lines)


def test_a_fallback_gain_carries_no_target_and_no_shortfall():
    """No separation decided (fallback gain): the row states what it
    holds - a gain, not a target - and claims no shortfall either way."""
    pre = _pre_output([-20.0])
    automation, _ = mix_module.solve_automation(
        pre, {"background": {"value": -18.0, "answer": None,
                             "basis": "fallback"},
              "prominent": {"value": -6.0, "answer": None,
                            "basis": "fallback"}})
    (row,) = automation
    assert row["target_level_db"] == -18.0
    assert row["separation_target_db"] is None
    assert row["separation_shortfall_db"] is None


# --------------------------------------------------------------------------
# From test_music_behavior_vocabulary.py
#
# A block the spine plans `silent` stays silent all the way down.
#
# `mesh_spine` (2.05) plans what the music bed does under every block, in a
# five-word vocabulary whose whole point is that one of the words is
# `silent`: the creative direction names silence as a deliberate tool, and a
# silence that survives planning but not translation is a decision nobody
# executes.
#
# `compile_manifest._spine_block_entry` used to DISCARD that word and
# recompute a two-word `full`/`ducked` value from `block_type` - a private
# vocabulary with no word for silence, which nothing then read.  The tests
# here are the ones that make that irreversible: they assert the path, not
# the implementation, so a future reduction anywhere along it fails here.
#
# The path, traced (docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary):
#
#     mesh_spine 2.05      audio_spine.structure[*].music_behavior
#       ├─ audio_mix 5.02  -> audio_mix_spec.music_automation[*], the half
#       │                     that carries a LEVEL (silent -> -96 dB), read
#       │                     by render_qa's P3 and by the renderer's markers
#       └─ compile 5.04    -> manifest._spine_blocks[*], the half read for
#                             block_type and black-beat declarations

# What a mix engineer decided this run.  The numbers are this test's, not
# the engine's: since 2026-09-16 no module holds a level for a behaviour,
# so a test that wants one has to supply it the way a run does.
DECIDED = {"prominent": -7.5, "background": -19.5}


def _automation_for(structure, decided=None):
    """`music_automation` for a spine, at the levels `decided` names."""
    pre = measure({"structure": structure}, {})
    by_scope = {scope: {"value": value, "answer": None}
                for scope, value in (DECIDED if decided is None
                                     else decided).items()}
    return solve_automation(pre, by_scope)


def _block(block_type="transition_slot", position=1, **extra):
    """A spine block carrying the full contract key set."""
    block = {
        "position": position,
        "block_type": block_type,
        "clip_id": None,
        "source_clip_id": None,
        "source_start": None,
        "source_end": None,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "word_timestamps": [],
        "alignment_method": None,
    }
    block.update(extra)
    return block


def _speech_block(position=1, **extra):
    return _block(
        "speech", position=position,
        clip_id="clip_001", source_start=0.0, source_end=2.0,
        word_timestamps=[{"word": "hello", "source_start": 0.0,
                          "source_end": 0.4}],
        alignment_method="whisperx", **extra)


# ─── The vocabulary itself ────────────────────────────────────────────

class TestTheVocabulary:

    def test_the_two_word_form_is_withdrawn_and_named_as_such(self):
        """`full`/`ducked` are not merely absent: asking for one says why."""
        assert set(WITHDRAWN_BEHAVIORS) == {"full", "ducked"}
        for word in WITHDRAWN_BEHAVIORS:
            assert word not in MUSIC_BEHAVIORS
            with pytest.raises(MusicBehaviorError, match="withdrawn"):
                resolve_music_behavior(word, block_carries_speech=False)

# ─── The narrowing this file exists to prevent ────────────────────────

class TestCompileManifestCarriesTheWord:

    def test_a_block_planned_silent_reaches_the_manifest_silent(self):
        """THE regression. This entry used to say `full` here."""
        entry = _spine_block_entry(
            _block("transition_slot", music_behavior="silent"))
        assert entry["music_behavior"] == "silent"

# ─── The half that carries the level ──────────────────────────────────

class TestAudioMixTranslatesTheWord:

    def test_a_silent_block_is_planned_at_the_silent_level(self):
        automation, _ = _automation_for(
            [_block("transition_slot", music_behavior="silent")])
        assert automation[0]["music_behavior"] == "silent"
        assert automation[0]["target_level_db"] == -96

    def test_the_two_halves_agree_block_for_block(self):
        """`_spine_blocks` and `music_automation` are two readings of one
        plan. They disagreed for the whole life of the two-word form."""
        structure = [
            _speech_block(position=1, music_behavior="background"),
            _block("transition_slot", position=2, music_behavior="silent"),
            _block("outro", position=3, music_behavior="fade_out"),
            _block("end_card", position=4),
        ]
        automation, _ = _automation_for(structure)
        entries = [_spine_block_entry(b) for b in structure]
        assert [w["music_behavior"] for w in automation] == \
            [e["music_behavior"] for e in entries]

    def test_a_word_outside_the_vocabulary_fails_the_mix_loudly(self):
        """A withdrawn word arriving from upstream, or an unknown one,
        raises rather than becoming a level."""
        for word in ("full", "quiet-ish"):
            with pytest.raises(MusicBehaviorError):
                _automation_for([_block("speech", music_behavior=word)])


# ─── The gate at the top of the path ──────────────────────────────────

class TestTheSpineGate:

    def test_a_word_outside_the_vocabulary_is_rejected_at_the_spine(self):
        """Including the withdrawn two-word form, so a reduction upstream
        fails at the first hop instead of becoming a dB level."""
        for word in list(WITHDRAWN_BEHAVIORS) + ["quiet-ish"]:
            with pytest.raises(SpineContractError, match="music_behavior"):
                validate_spine_blocks(
                    [_block("transition_slot", music_behavior=word)])


# --------------------------------------------------------------------------
# From test_mix_reads_the_bed.py
#
# The mix reads the bed it was handed (step 5.02).
#
# The chosen track's measurements travel on `music_selection` and 5.02 reads
# them; an unmeasured bed is an admitted absence, never a level of 0; the
# render-side check says when its margin was a clip gain. History:
# docs/evidence/music_tests.md#mix-reads-the-bed.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import music_measurement as mm  # noqa: E402
from library.tools import render_qa  # noqa: E402


# What a mix engineer decided on one run. The test supplies it because no
# module holds one any more.


def _mix_spec(spine, selection, decided=None):
    """`audio_mix_spec` for one spine and one bed, at decided levels."""
    from library.steps.step_5_02_audio_mix import mix as mix_module

    pre = mix_module.measure(spine, selection)
    by_scope = {scope: {"value": value, "answer": None}
                for scope, value in (DECIDED if decided is None
                                     else decided).items()}
    automation, undetermined = mix_module.solve_automation(pre, by_scope)
    return mix_module.assemble(pre, [], automation,
                               undetermined)["audio_mix_spec"]


def _candidate(audio_path: str) -> dict:
    """One measured candidate, in the shape `measure_candidates` emits."""
    return {
        "title": "rise",
        "source": "library",
        "audio_path": audio_path,
        "duration_ok": True,
        "measured": True,
        "measurement_note": "",
        "integrated_lufs": -13.9,
        "loudness_range_lu": 6.0,
        "true_peak_dbtp": -0.4,
        "rms_spread_db": 5.7,
        "window_seconds": 60.0,
        "window_spread_db": 3.1,
        "speech_band_ratio_db": -2.8,
        # The two that must NOT travel onward.
        "window_envelope_dbfs": [-16.3] * mm.ENVELOPE_BUCKETS,
        "track_sections": [
            {"start_seconds": 0.0, "end_seconds": 60.0,
             "mean_dbfs": -16.3, "spread_db": 3.1},
        ],
        "track_sections_note": "",
    }


def _spine_3(*behaviours) -> dict:
    return {"structure": [
        {"position": index, "block_type": "speech", "content": "words",
         "timeline_start": float(index * 2), "timeline_end": float(index * 2 + 2),
         "music_behavior": behaviour}
        for index, behaviour in enumerate(behaviours)
    ]}


# ── The measurements travel with the track that was chosen ──────────

def test_the_chosen_track_carries_its_own_scalars_and_no_raw_list():
    """The scalars come from the CHOSEN candidate; no raw value list travels
    (AGENTS.md 10.1: `music_selection` is declared whole by two prompts)."""
    chosen = _candidate("/library/rise.mp3")
    other = dict(_candidate("/library/other.mp3"), integrated_lufs=-22.0)

    measurements = mm.selection_measurements(
        {"audio_path": "/library/rise.mp3"}, [other, chosen])

    assert measurements["measured"] is True
    assert measurements["integrated_lufs"] == -13.9, \
        "the scalars must come from the CHOSEN candidate, not the first one"
    assert measurements["speech_band_ratio_db"] == -2.8
    for withheld in mm.WITHHELD_FROM_THE_SELECTION:
        assert withheld not in measurements, (
            f"{withheld} reached `music_selection`, which `plan_transitions` "
            f"and `mesh_spine` declare whole - it would land in both prompts")


def test_a_track_that_was_not_measured_says_so():
    measurements = mm.selection_measurements(
        {"audio_path": "/downloads/fetched.mp3"},
        [_candidate("/library/rise.mp3")])

    assert measurements["measured"] is False
    assert "not one of the 1 measured candidates" in \
        measurements["measurement_note"]
    assert "integrated_lufs" not in measurements, \
        "an unmeasured bed has no level, not a level of 0"


# ── Step 5.02 reads them ────────────────────────────────────────────

def test_the_mix_reads_the_bed_and_says_where_the_gain_puts_it():
    spec = _mix_spec(
        _spine_3("background", "prominent"),
        {"title": "rise", "audio_path": "/library/rise.mp3",
         "measurements": mm.selection_measurements(
             {"audio_path": "/library/rise.mp3"},
             [_candidate("/library/rise.mp3")])})

    assert spec["bed"]["measured"] is True
    assert spec["bed"]["integrated_lufs"] == -13.9
    assert spec["bed"]["speech_band_ratio_db"] == -2.8

    background, prominent = spec["music_automation"]
    # Where the gain PUTS the bed is arithmetic on the measurement, not a
    # chosen number - and the gain itself is now the decided one.
    assert background["target_level_db"] == DECIDED["background"]
    assert background["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["background"])
    assert prominent["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["prominent"])


# ── The check says what it judged against ───────────────────────────

def _fake_master(monkeypatch, *, music_gain_db: float, speech_db: float):
    """A two-second master: a known bed under a known voice."""
    import numpy as np  # a hard dependency of render_qa itself

    rate = 48000
    n = rate * 2
    rng = np.random.default_rng(7)
    music = rng.standard_normal(n) * 0.1
    speech = rng.standard_normal(n) * (10 ** (speech_db / 20.0))
    mix = music * (10 ** (music_gain_db / 20.0)) + speech

    def decode(path, sample_rate=rate):
        return (music if path == "MUSIC" else mix).astype("float32")

    monkeypatch.setattr(render_qa, "_decode_mono", decode)


def _automation(separation_target_db):
    return [{
        "spine_block_position": 0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "music_behavior": "background",
        "target_level_db": DECIDED["background"],
        "separation_target_db": separation_target_db,
    }]


BLOCKS = [{"position": 0, "block_type": "speech"}]


def test_the_check_names_a_clip_gain_when_that_is_what_it_judged(monkeypatch):
    _fake_master(monkeypatch, music_gain_db=DECIDED["background"],
                 speech_db=-20.0)

    result = render_qa.measure_speech_above_bed(
        "MASTER", "MUSIC", _automation(None), 0.0, BLOCKS)

    window = result.value["windows"][0]
    assert window["required_margin_basis"] == "clip_gain_read_as_separation"
    assert window["required_margin_db"] == abs(DECIDED["background"])
    assert result.threshold["judged_on_clip_gain"] is True
    assert "CLIP GAIN" in result.detail, (
        "the report must say the margin was a clip gain, or a reader takes "
        "it for a separation somebody declared")


# --------------------------------------------------------------------------
# From test_music_section.py
#
# Which part of the track plays is the model's decision, and it travels.
#
# `compile_manifest` wrote `"source_in": 0.0` as a literal, so every track
# played from its head whatever the model said - and step 2.04's frozen
# handoff has asked for splices since it was written. That is AGENTS.md
# 10.2 exactly: a capability is only real where the renderer reads it.
#
# These tests hold both halves:
#
#   * the decision REACHES the manifest, and the beat grid is mapped
#     through the same offset;
#   * a selection that declares none plays from the head of the file as
#     the ABSENCE of a decision.

from library.tools.beat_grid import (  # noqa: E402
    assert_music_offset_is_the_chosen_section,
    beat_positions,
)
from library.tools.music_measurement import track_sections  # noqa: E402
from library.tools.music_section import (  # noqa: E402
    UNDECLARED_SOURCE_IN,
    MusicSectionError,
    read_section,
    validate_section,
)

TRACK = 198.6      # 001's Sickick instrumental
EDIT = 60.0


# ── Declared, and not declared ────────────────────────────────────────

def test_no_section_is_the_absence_of_a_decision():
    section = read_section({"title": "t", "audio_path": "/t.wav"})
    assert section.declared is False
    assert section.source_in == UNDECLARED_SOURCE_IN == 0.0
    assert "absence of a decision" in section.reason
    assert validate_section({"title": "t"}, TRACK, EDIT) == []


@pytest.mark.parametrize("declared", [
    "60",
])
def test_a_malformed_section_raises(declared):
    """A number the placement depends on is not quietly read as zero."""
    with pytest.raises(MusicSectionError):
        read_section({"section": declared})


def test_a_section_the_track_cannot_play_is_refused_with_the_arithmetic():
    """Past the end, and too close to it - the latter says what WOULD work."""
    errors = validate_section({"section": {"source_in": 400.0}}, TRACK, EDIT)
    assert errors and "after the file ends" in errors[0]

    errors = validate_section({"section": {"source_in": 180.0}}, TRACK, EDIT)
    assert len(errors) == 1
    assert "would be silent" in errors[0]
    assert "138.6" in errors[0]


# ── One reading of the offset, and the grid moves with it ─────────────

def test_the_beat_grid_is_mapped_through_the_chosen_section():
    analysis = {"tempo": {"bpm": 120.0,
                          "beats": [round(0.37 + i * 0.5, 3)
                                    for i in range(32)]}}
    unmoved = beat_positions(analysis, None)
    moved = beat_positions(analysis, {"section": {"source_in": 5.0}})
    assert unmoved[0] == 0.37
    # The first beat at or after 5.0s is 5.37s into the file, and it is
    # 0.37s into the edit.
    assert moved[0] == 0.37
    assert all(t >= 0 for t in moved)
    # Beats before the section starts are not in the edit at all.
    assert moved == [round(t - 5.0, 4) for t in unmoved if t >= 5.0]
    assert len(moved) < len(unmoved)


def test_a_bed_that_does_not_start_the_timeline_raises():
    manifest = {"tracks": {"A2": {"clips": [
        {"source_in": 60.0, "timeline_in": 4.0}]}}}
    with pytest.raises(ValueError, match="timeline"):
        assert_music_offset_is_the_chosen_section(
            manifest, {"section": {"source_in": 60.0}})


# ── The decision reaches the manifest ─────────────────────────────────

def _compile_with(music_selection, tmp_path):
    """Drive the real compiler, with only its file loader stubbed."""
    a_roll = tmp_path / "vid1.mov"
    a_roll.write_text("dummy", encoding="utf-8")
    music = tmp_path / "bed.wav"
    music.write_text("dummy", encoding="utf-8")

    inputs = {
        "a_roll_assignments": [{
            "clip_id": "clip_1", "source_clip_id": "clip_1",
            "source_file": str(a_roll), "video_in": 10.317, "video_out": 70.317,
            "timeline_start": 0.0, "timeline_end": 60.0,
        }],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": dict(music_selection, audio_path=str(music)),
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "clip_1",
                "source_start": 10.317, "source_end": 70.317,
                "timeline_start": 0.0, "timeline_end": 60.0,
                "content": {"clip_id": "clip_1", "link_group_id": "lg_1"},
            }],
            "frame_rate": 30.0,
        },
        "clip_catalog": [{"clip_id": "clip_1", "path": str(a_roll),
                          "width": 1080, "height": 1920}],
        "semantic_analysis": {"semantic_analysis_documents": [{
            "clip_id": "clip_1",
            "analysis": {"motion": "The camera is mounted and still.",
                         "scene": "A speaker in a car."},
            "assessment": {"clip_type": "a-roll", "keywords": ["speaker"]},
        }]},
    }
    from library.steps.step_5_04_compile_manifest.step import compile_manifest
    from library.tools import music_audit_trail as audit
    # A resolved selection owes its audit sidecar: the pre-render check
    # refuses without it, so stage what 2.04's post-bridge writes on a
    # real run. The compile reads the project root off `out_dir`.
    audit.write_audit_trail(str(tmp_path), inputs["music_selection"])
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return compile_manifest(str(tmp_path / "pipeline_output"))


def test_the_chosen_section_is_where_the_bed_is_placed(tmp_path):
    manifest = _compile_with(
        {"title": "bed", "duration_seconds": 198.6,
         "section": {"source_in": 60.0, "why": "the intro swings 31 dB"}},
        tmp_path)
    clip = manifest["tracks"]["A2"]["clips"][0]
    assert clip["source_in"] == 60.0
    assert clip["timeline_in"] == 0.0
    assert clip["source_out"] == pytest.approx(120.0)


def test_a_section_that_cannot_cover_the_timeline_fails_the_build(tmp_path):
    with pytest.raises(MusicSectionError, match="silent"):
        _compile_with({"title": "bed", "duration_seconds": 90.0,
                       "section": {"source_in": 60.0}}, tmp_path)


# ── What the model decides FROM, and what nothing decides for it ──────

def test_every_playable_section_is_measured_and_the_last_one_is_included():
    """One row per span that could play, including the tail - and none for
    a track shorter than the edit."""
    per_second = [-20.0] * 199
    rows = track_sections(per_second, 60.0)
    assert [r["start_seconds"] for r in rows] == [0.0, 60.0, 120.0, 139.0]
    assert rows[-1]["end_seconds"] == 199.0
    for row in rows:
        assert set(row) == {"start_seconds", "end_seconds",
                            "mean_dbfs", "spread_db"}
    assert track_sections([-20.0] * 30, 60.0) == []


# --------------------------------------------------------------------------
# From test_music_sections.py
#
# The section grid: measured labels reach the planners, and only those.
#
# Fidelity rung 5c: plans cut on bars but no step could see the sections -
# `mesh_spine` paced gaps from prose, the cut planners counted bars from
# the track head, and the Infected drop landed only as a novelty section.
# Step 2.06 now emits `section_grid` (allin1, bar-aligned); these tests
# drive the readers on fixture grids shaped like the measured one
# (intro/intro/solo/outro on the Infected instrumental, verse/chorus on
# the lyrics cut) and assert the mapping, the addressing and the refusal
# half: a label the model did not give refuses with what the grid
# carries, never coined.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.context_views import build_view
from library.tools.frame_utils import seconds_to_frame
from library.tools.music_sections import (
    available,
    find_sections,
    sections_timeline,
)
from library.tools.sub_block_anchor import AnchorRefused, resolve_anchor
from library.tools.analysis.music_pipeline import (
    _snap_to_downbeats,
    _validate_section_grid,
)

FPS = 30.0


def _sections():
    # Shaped like the measured Infected instrumental grid: two intro
    # spans split at the 35.15 drop, then solo, then outro.
    return [
        {"label": "start", "start": 0.0, "end": 0.16,
         "first_downbeat": 0.16, "mean_label_activation": None},
        {"label": "intro", "start": 0.16, "end": 35.15,
         "first_downbeat": 0.16, "mean_label_activation": 0.81},
        {"label": "intro", "start": 35.15, "end": 67.43,
         "first_downbeat": 35.15, "mean_label_activation": 0.77},
        {"label": "solo", "start": 99.71, "end": 136.7,
         "first_downbeat": 99.71, "mean_label_activation": 0.69},
        {"label": "outro", "start": 185.83, "end": 198.6,
         "first_downbeat": 185.83, "mean_label_activation": 0.9},
        {"label": "end", "start": 198.6, "end": 198.61,
         "first_downbeat": 198.6, "mean_label_activation": None},
    ]


def _analysis(sections=None):
    return {"tempo": {"bpm": 90.0, "beats": [], "downbeats": [],
                      "downbeat_source": "detected"},
            "section_grid": {
                "available": True,
                "method": "allin1-harmonix-all",
                "validation": "first downbeat 0.16s, 70 bars over 198.6s",
                "labels_absent_note": "no drop or phrase labels",
                "sections": _sections() if sections is None else sections,
            }}


def _selection_3(source_in=0.0):
    if source_in == 0.0:
        return {}
    return {"section": {"source_in": source_in, "why": "test"}}


def _block_2(start=30.0, stop=45.0):
    return {"position": 1, "block_type": "speech", "clip_id": "clip_001",
            "source_start": 0.0, "source_end": 15.0,
            "timeline_start": start, "timeline_end": stop,
            "word_timestamps": [], "alignment_method": "mfa"}


# ── The reader maps file time through the chosen section ──────────────

def test_sections_map_through_the_chosen_section():
    """Bed from 35.15 s: the drop span opens the timeline at 0.0 s."""
    rows = sections_timeline(_analysis(), _selection_3(35.15))
    by_label = [(r["label"], r["start_seconds"],
                 r["first_downbeat_seconds"]) for r in rows]
    assert ("intro", 0.0, 0.0) in by_label
    assert ("solo", pytest.approx(64.56, abs=1e-3),
            pytest.approx(64.56, abs=1e-3)) in by_label
    # The pre-drop spans are not in the edit.
    assert all(r["start_seconds"] >= 0 for r in rows)


def test_unavailable_grid_reads_empty():
    assert sections_timeline({"section_grid": {"available": False}},
                             {}) == []
    assert sections_timeline({}, {}) == []
    assert not available({})
    assert not available({"section_grid": {"available": True,
                                           "sections": []}})


def test_find_sections_pairs_matches_with_what_is_present():
    matches, present = find_sections(_analysis(), {}, "intro")
    assert len(matches) == 2
    assert present == ["intro", "intro", "solo", "outro"]
    matches, _ = find_sections(_analysis(), {}, "chorus")
    assert matches == []


# ── The anchor resolves a section to its first downbeat ───────────────

def test_section_anchor_resolves_to_the_first_downbeat():
    """The drop span's first downbeat, file 35.15 s, timeline 35.15 s."""
    hit = resolve_anchor({"section": "intro", "occurrence": 2},
                         block=_block_2(), music_analysis=_analysis(),
                         music_selection={}, frame_rate=FPS,
                         step="plan_vfx", plan="vfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(35.15, abs=1e-9)
    assert hit["frame"] == seconds_to_frame(35.15, FPS)
    assert "first downbeat" in hit["method"]


def test_section_anchor_edge_end_resolves_to_the_span_end():
    hit = resolve_anchor({"section": "solo", "edge": "end"},
                         block=_block_2(90.0, 150.0),
                         music_analysis=_analysis(), music_selection={},
                         frame_rate=FPS, step="plan_vfx",
                         plan="vfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(136.7, abs=1e-9)


def test_a_section_anchor_the_grid_cannot_resolve_refuses_by_name():
    """Never coined, never first-matched: each row refuses with what the
    grid carries."""
    wide = _block_2(0.0, 200.0)
    rows = [
        # the drop is never coined on a grid without one
        ({"section": "drop"}, wide, _analysis(),
         "section 'drop' is not in the measured grid"),
        # start/end are grid bookkeeping
        ({"section": "start"}, wide, _analysis(),
         "is grid bookkeeping, not music"),
        ({"section": "intro", "occurrence": 3}, wide, _analysis(),
         "occurs 2 time"),
        ({"section": "  "}, wide, _analysis(), "names no section"),
        ({"section": "chorus"}, wide, {}, "no usable section grid"),
        ({"section": "solo"}, _block_2(), _analysis(), "outside block"),
    ]
    for anchor, block, analysis, match in rows:
        with pytest.raises(AnchorRefused, match=match):
            resolve_anchor(anchor, block=block, music_analysis=analysis,
                           music_selection={}, frame_rate=FPS,
                           step="plan_transitions", plan="transitions",
                           index=0)


# ── The producer validates before it adopts ───────────────────────────

def test_validation_catches_a_dropped_opening_and_passes_the_measured_grid():
    """The measured 1-in-3 failure: first downbeat six bars in."""
    dropped = [round(16.31 + i * 2.69, 3) for i in range(64)]
    assert any("first downbeat" in p
               for p in _validate_section_grid(dropped, 198.6))
    measured = [round(0.16 + i * 2.69, 3) for i in range(73)]
    assert _validate_section_grid(measured, 198.6) == []
    assert _validate_section_grid([1.0], 198.6) != []


def test_boundaries_snap_to_their_own_runs_downbeats():
    snapped, delta = _snap_to_downbeats(35.15, [32.46, 35.15, 37.84])
    assert (snapped, delta) == (35.15, 0.0)
    snapped, delta = _snap_to_downbeats(35.0, [32.46, 35.15, 37.84])
    assert snapped == 35.15 and abs(delta - 0.15) < 1e-9


def test_no_noncommercial_weights_in_the_pipeline():
    """madmom's RNN processors load CC-BY-NC-SA weights - the tempo
    chain must never instantiate or import them. (The module docstring
    names them to record WHY they are banned; that discussion is not a
    load path. beat_this's DBN post-processor is weight-free HMM code
    and lives outside this file.)"""
    path = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                        "music_pipeline.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    for banned in ("RNNBeatProcessor(", "RNNDownBeatProcessor(",
                   "madmom.features", "import madmom\n",
                   "import madmom "):
        assert banned not in source, f"non-commercial path: {banned!r}"


# ── The view shows labels, never the boundary series ──────────────────

def test_sectiongrid_view_lists_sections_with_provenance_or_nothing():
    data = {"music_analysis": _analysis(), "music_selection": {}}
    view = build_view("sectiongrid", data)["sectiongrid"]
    assert view["method"] == "allin1-harmonix-all"
    assert view["section_count"] == 6
    first = view["sections"][1]
    assert first["label"] == "intro"
    assert first["first_downbeat_seconds"] == pytest.approx(0.16)
    assert "legend" in view and "labels_absent_note" in view
    assert build_view("sectiongrid",
                      {"music_analysis": {}, "music_selection": {}}) == {}


# --------------------------------------------------------------------------
# From test_beat_grid.py
#
# P4.1: the real beat grid, and the two ways nobody was reaching it.
#
# `plan_transitions` synthesised `[i * 60/bpm for i in ...]` from t=0. No
# track's first beat lands at 0.000s, so every "beat-snapped" cut was
# snapped to a grid offset from the music by the track's lead-in.
#
# `plan_sfx` read `music_analysis["beat_grid"]["bars"]`, and the producer
# emits no `beat_grid` key at all. So `bars` was always `[]` and the SFX
# snapping never ran either - even though the standing plan credited
# `plan_sfx` as the one honest consumer of the real grid.
#
# The producer/consumer agreement is asserted here against the real
# producer, `library/tools/analysis/music_pipeline.py`, so a rename on
# either side fails rather than degrading to silence.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.beat_grid import (
    MIN_USABLE_BEATS,
)

# The bed plays from the head of the file unless a section says otherwise;
# these cases are about the grid's shape, not about the offset. The offset
# has its own tests in tests/unit/audio/test_music_bed.py.
NO_SECTION = None

MUSIC_PIPELINE = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                              "music_pipeline.py")


def analysis(beats=None, downbeats=None, tempo_bpm=120.0, **extra):
    a = {"tempo": {"bpm": tempo_bpm,
                   "beats": beats if beats is not None else [],
                   "downbeats": downbeats if downbeats is not None else []}}
    a.update(extra)
    return a


BEATS = [round(0.37 + i * 0.5, 3) for i in range(32)]


# ─────────────────────────────────────────────────────────
# The producer and the consumer must agree on the names
# ─────────────────────────────────────────────────────────

def test_the_producer_really_emits_tempo_beats_and_downbeats():
    """Read off music_pipeline itself, not off a fixture.

    A fixture agreeing with the reader proves the fixture. This asserts
    the shipped producer builds the keys this module reads.
    """
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    emitted.add(key.value)

    assert "tempo" in emitted
    assert "beats" in emitted
    assert "downbeats" in emitted
    assert "bpm" in emitted


# ─────────────────────────────────────────────────────────
# Reading the grid
# ─────────────────────────────────────────────────────────


class TestRefusesToInvent:
    """Empty means "do not snap", which every caller already honours."""


    def test_too_few_beats_is_not_a_rhythm(self):
        few = BEATS[:MIN_USABLE_BEATS - 1]
        assert beat_positions(analysis(beats=few), NO_SECTION) == []


    def test_malformed_entries_are_skipped_not_crashed(self):
        messy = list(BEATS) + ["x", None, {}, -1.0]
        assert beat_positions(analysis(beats=messy), NO_SECTION) == sorted(BEATS)


# ─────────────────────────────────────────────────────────
# The time domain the grid depends on
# ─────────────────────────────────────────────────────────

def test_legitimate_music_placements_pass():
    """B1 collapse: the three no-raise offset validators share one
    assert helper, so one test keeps all three shapes covered."""
    assert_music_offset_is_the_chosen_section(
        {"tracks": {"A2": {"clips": [{"source_in": 0.0, "timeline_in": 0.0}]}}},
        NO_SECTION)
    assert_music_offset_is_the_chosen_section({"tracks": {}}, NO_SECTION)
    assert_music_offset_is_the_chosen_section({}, NO_SECTION)
    assert_music_offset_is_the_chosen_section(
        {"tracks": {"A2": {"clips": [
            {"source_in": 4.5, "timeline_in": 0.0}]}}},
        {"section": {"source_in": 4.5}})


def test_music_placed_somewhere_other_than_the_chosen_section_raises():
    """A snapped cut that is off by the music's offset looks exactly like
    a snapped cut that is correct."""
    with pytest.raises(ValueError, match="section"):
        assert_music_offset_is_the_chosen_section(
            {"tracks": {"A2": {"clips": [
                {"source_in": 4.5, "timeline_in": 0.0}]}}},
            None)
