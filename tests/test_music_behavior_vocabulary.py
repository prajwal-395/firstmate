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
    MUSIC_BEHAVIORS,
    MusicBehaviorError,
    WITHDRAWN_BEHAVIORS,
    resolve_music_behavior,
)
from library.tools.spine_contract import SpineContractError, validate_spine_blocks

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
