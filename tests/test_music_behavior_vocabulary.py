"""A block the spine plans `silent` stays silent all the way down.

`mesh_spine` (2.05) plans what the music bed does under every block, in a
five-word vocabulary whose whole point is that one of the words is
`silent`: the creative direction names silence as a deliberate tool, and a
silence that survives planning but not translation is a decision nobody
executes.

`compile_manifest._spine_block_entry` used to DISCARD that word and
recompute a two-word `full`/`ducked` value from `block_type` - a private
vocabulary with no word for silence, which nothing then read.  The tests
here are the ones that make that irreversible: they assert the path, not
the implementation, so a future reduction anywhere along it fails here.

The path, traced (docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary):

    mesh_spine 2.05      audio_spine.structure[*].music_behavior
      ├─ audio_mix 5.02  -> audio_mix_spec.music_automation[*], the half
      │                     that carries a LEVEL (silent -> -96 dB), read
      │                     by render_qa's P3 and by the renderer's markers
      └─ compile 5.04    -> manifest._spine_blocks[*], the half read for
                            block_type and black-beat declarations
"""

import pytest

from library.steps.step_5_02_audio_mix.step import define_audio_mix
from library.steps.step_5_04_compile_manifest.step import _spine_block_entry
from library.tools.music_behavior import (
    MUSIC_BEHAVIORS,
    MusicBehaviorError,
    WITHDRAWN_BEHAVIORS,
    is_silent,
    music_level_db,
    resolve_music_behavior,
)
from library.tools.spine_contract import SpineContractError, validate_spine_blocks


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

    def test_silence_is_a_word_in_it(self):
        assert "silent" in MUSIC_BEHAVIORS
        assert is_silent("silent")

    def test_silence_is_a_level_the_ear_reads_as_no_music(self):
        assert music_level_db("silent") == -96

    def test_the_two_word_form_is_withdrawn_and_named_as_such(self):
        """`full`/`ducked` are not merely absent: asking for one says why."""
        assert set(WITHDRAWN_BEHAVIORS) == {"full", "ducked"}
        for word in WITHDRAWN_BEHAVIORS:
            assert word not in MUSIC_BEHAVIORS
            with pytest.raises(MusicBehaviorError, match="withdrawn"):
                resolve_music_behavior(word, block_carries_speech=False)

    def test_an_unknown_word_raises_rather_than_becoming_a_level(self):
        with pytest.raises(MusicBehaviorError):
            music_level_db("quiet-ish")

    def test_a_block_that_declares_nothing_takes_one_documented_default(self):
        """A card is assembled by bookends.py and plans no behaviour. The
        one true thing the old reduction knew - nothing is ducking under a
        block with no speech - survives, as a DEFAULT rather than an
        override."""
        assert resolve_music_behavior(None, block_carries_speech=False) == \
            "prominent"
        assert resolve_music_behavior(None, block_carries_speech=True) == \
            "background"


# ─── The narrowing this file exists to prevent ────────────────────────

class TestCompileManifestCarriesTheWord:

    def test_a_block_planned_silent_reaches_the_manifest_silent(self):
        """THE regression. This entry used to say `full` here."""
        entry = _spine_block_entry(
            _block("transition_slot", music_behavior="silent"))
        assert entry["music_behavior"] == "silent"

    def test_every_word_in_the_vocabulary_survives_unchanged(self):
        for word in MUSIC_BEHAVIORS:
            for block_type in ("hook", "speech", "intro", "transition_slot",
                               "outro", "intro_card", "end_card"):
                entry = _spine_block_entry(
                    _block(block_type, music_behavior=word))
                assert entry["music_behavior"] == word, (
                    f"{block_type} planned {word} and the manifest says "
                    f"{entry['music_behavior']}")

    def test_the_manifest_never_speaks_a_word_outside_the_vocabulary(self):
        """A reduction to ANY private vocabulary fails here, not only the
        `full`/`ducked` one that was there."""
        for block_type in ("hook", "speech", "intro", "transition_slot",
                           "outro", "intro_card", "outro_card", "end_card"):
            for planned in list(MUSIC_BEHAVIORS) + [None]:
                extra = {} if planned is None else {"music_behavior": planned}
                entry = _spine_block_entry(_block(block_type, **extra))
                assert entry["music_behavior"] in MUSIC_BEHAVIORS

    def test_a_card_that_plans_nothing_is_not_left_ducking_under_no_speech(self):
        entry = _spine_block_entry(_block("intro_card"))
        assert entry["music_behavior"] == "prominent"

    def test_a_withdrawn_word_arriving_from_upstream_fails_loudly(self):
        with pytest.raises(MusicBehaviorError):
            _spine_block_entry(_block("speech", music_behavior="ducked"))


# ─── The half that carries the level ──────────────────────────────────

class TestAudioMixTranslatesTheWord:

    def test_a_silent_block_is_planned_at_the_silent_level(self):
        spine = {"structure": [_block("transition_slot",
                                      music_behavior="silent")]}
        window = define_audio_mix(spine, {})["audio_mix_spec"][
            "music_automation"][0]
        assert window["music_behavior"] == "silent"
        assert window["target_level_db"] == -96

    def test_the_level_is_the_vocabulary_s_own_number_for_every_word(self):
        spine = {"structure": [
            _block("transition_slot", position=i, music_behavior=word)
            for i, word in enumerate(MUSIC_BEHAVIORS)]}
        windows = define_audio_mix(spine, {})["audio_mix_spec"][
            "music_automation"]
        for window in windows:
            assert window["target_level_db"] == \
                music_level_db(window["music_behavior"])

    def test_the_two_halves_agree_block_for_block(self):
        """`_spine_blocks` and `music_automation` are two readings of one
        plan. They disagreed for the whole life of the two-word form."""
        structure = [
            _speech_block(position=1, music_behavior="background"),
            _block("transition_slot", position=2, music_behavior="silent"),
            _block("outro", position=3, music_behavior="fade_out"),
            _block("end_card", position=4),
        ]
        automation = define_audio_mix({"structure": structure}, {})[
            "audio_mix_spec"]["music_automation"]
        entries = [_spine_block_entry(b) for b in structure]
        assert [w["music_behavior"] for w in automation] == \
            [e["music_behavior"] for e in entries]

    def test_a_withdrawn_word_arriving_from_upstream_fails_loudly(self):
        with pytest.raises(MusicBehaviorError):
            define_audio_mix(
                {"structure": [_block("speech", music_behavior="full")]}, {})


# ─── The gate at the top of the path ──────────────────────────────────

class TestTheSpineGate:

    def test_a_planned_silence_passes_the_spine_contract(self):
        validate_spine_blocks([_block("transition_slot",
                                      music_behavior="silent")])

    def test_a_block_that_plans_no_behaviour_still_passes(self):
        """A bookend card carries none - see library/tools/bookends.py -
        and neither does a pacing beat that simply did not say."""
        validate_spine_blocks([_block("intro")])

    def test_a_word_outside_the_vocabulary_is_rejected_at_the_spine(self):
        """Including the withdrawn two-word form, so a reduction upstream
        fails at the first hop instead of becoming a dB level."""
        for word in list(WITHDRAWN_BEHAVIORS) + ["quiet-ish"]:
            with pytest.raises(SpineContractError, match="music_behavior"):
                validate_spine_blocks(
                    [_block("transition_slot", music_behavior=word)])


# ─── One enumeration, not two ─────────────────────────────────────────

def test_no_step_keeps_a_private_behaviour_to_db_table():
    """`audio_mix` used to own the mapping. Two tables is how the two
    halves of the plan drift apart."""
    import inspect

    from library.steps.step_5_02_audio_mix import step as audio_mix_step

    source = inspect.getsource(audio_mix_step)
    assert "BEHAVIOR_TO_DB" not in source
    assert "music_level_db" in source
