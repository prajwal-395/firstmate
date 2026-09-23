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

from library.steps.step_5_02_audio_mix.mix import measure, solve_automation
from library.steps.step_5_04_compile_manifest.step import _spine_block_entry
from library.tools.music_behavior import (
    DECIDED_BEHAVIORS,
    MUSIC_BEHAVIORS,
    MusicBehaviorError,
    SILENT_LEVEL_DB,
    WITHDRAWN_BEHAVIORS,
    is_silent,
    level_for_block,
    resolve_music_behavior,
)

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
        assert SILENT_LEVEL_DB == -96
        assert level_for_block(0, ["silent"], DECIDED)[0] == -96

    def test_no_word_carries_a_level_of_its_own_any_more(self):
        """The five dB are GONE (captain, 2026-09-16). A word means what
        the bed DOES; how loud that is, is decided per run."""
        for word, meaning in MUSIC_BEHAVIORS.items():
            assert isinstance(meaning, str), (
                f"{word} still carries a number: {meaning!r}")

    def test_only_two_words_have_a_level_anybody_decides(self):
        """`silent` is the absence of music and the two fades are a MOVE,
        so neither is a value a mix engineer is asked for."""
        assert set(DECIDED_BEHAVIORS) == {"prominent", "background"}
        assert "silent" not in DECIDED_BEHAVIORS

    def test_a_fade_takes_the_level_it_moves_to(self):
        """The old -12 was a plateau in the middle of a ramp: a level
        nobody planned. A fade has no level of its own."""
        words = ["silent", "fade_in", "background"]
        level, why, scope = level_for_block(1, words, DECIDED)
        assert level == DECIDED["background"]
        assert scope == "background"
        assert "move" in why

    def test_a_fade_out_that_ends_the_piece_ends_in_silence(self):
        level, why, _ = level_for_block(1, ["prominent", "fade_out"], DECIDED)
        assert level == SILENT_LEVEL_DB

    def test_a_word_nothing_decided_carries_no_level_rather_than_zero(self):
        """Never 0, which is a real level and reads as the file's own."""
        level, why, _ = level_for_block(0, ["background"], {})
        assert level is None
        assert "nothing decided" in why

    def test_the_two_word_form_is_withdrawn_and_named_as_such(self):
        """`full`/`ducked` are not merely absent: asking for one says why."""
        assert set(WITHDRAWN_BEHAVIORS) == {"full", "ducked"}
        for word in WITHDRAWN_BEHAVIORS:
            assert word not in MUSIC_BEHAVIORS
            with pytest.raises(MusicBehaviorError, match="withdrawn"):
                resolve_music_behavior(word, block_carries_speech=False)

    def test_an_unknown_word_raises_rather_than_becoming_a_level(self):
        with pytest.raises(MusicBehaviorError):
            level_for_block(0, ["quiet-ish"], DECIDED)

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
        automation, _ = _automation_for(
            [_block("transition_slot", music_behavior="silent")])
        assert automation[0]["music_behavior"] == "silent"
        assert automation[0]["target_level_db"] == -96

    def test_every_word_reaches_a_level_and_says_where_it_came_from(self):
        structure = [_block("transition_slot", position=i,
                            music_behavior=word)
                     for i, word in enumerate(MUSIC_BEHAVIORS)]
        # `background` last so the fades have something to move to.
        structure.append(_speech_block(position=99,
                                       music_behavior="background"))
        automation, undetermined = _automation_for(structure)
        assert undetermined == []
        for window in automation:
            assert window["target_level_db"] is not None
            assert window["target_level_basis"]

    def test_the_decided_level_is_what_reaches_the_automation(self):
        automation, _ = _automation_for(
            [_speech_block(position=1, music_behavior="background")])
        assert automation[0]["target_level_db"] == DECIDED["background"]

    def test_a_window_nothing_decided_carries_no_level_and_names_itself(self):
        """Never a substitute: the mix says which windows have no gain."""
        automation, undetermined = _automation_for(
            [_speech_block(position=1, music_behavior="background")],
            decided={})
        assert automation[0]["target_level_db"] is None
        assert [w["spine_block_position"] for w in undetermined] == [1]

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

    def test_a_withdrawn_word_arriving_from_upstream_fails_loudly(self):
        with pytest.raises(MusicBehaviorError):
            _automation_for([_block("speech", music_behavior="full")])


# ─── The gate at the top of the path ──────────────────────────────────

class TestTheSpineGate:

    def test_valid_and_undeclared_behaviours_pass_the_spine_contract(self):
        """B1 collapse: the two no-raise spine-gate validators in one test."""
        validate_spine_blocks([_block("transition_slot",
                                      music_behavior="silent")])
        # A bookend card carries none - see library/tools/bookends.py -
        # and neither does a pacing beat that simply did not say.
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
    halves of the plan drift apart - and since 2026-09-16 a table of
    levels is not a thing any module may hold at all."""
    import inspect

    from library.steps.step_5_02_audio_mix import mix as audio_mix_module

    source = inspect.getsource(audio_mix_module)
    assert "BEHAVIOR_TO_DB" not in source
    assert "level_for_block" in source
    # The two numbers step 5.02 held in `TRACK_LEVELS` beside the ones it
    # read from `music_behavior`. A second copy of a level is how the two
    # came to be able to disagree.
    #
    # Read only lines that can EXECUTE: the comment recording the
    # withdrawal names both keys on purpose, and prose recording a removal
    # is not a level the renderer reads - the same line
    # tests/test_no_creative_floors.py draws when it sweeps for fallbacks.
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert "prominent_level_db" not in code
    assert "background_level_db" not in code
