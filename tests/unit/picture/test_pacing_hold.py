"""The moments a cut should slow down, proved against named defects.

EDIT-022 said "identify moments where pacing should intentionally slow
rather than simply minimizing duration", and the pipeline measured one
direction of pacing well: `library/tools/pacing.py` finds the stretches
where nothing happens and proposes to cut them.  Nothing measured the
other direction, so every module that saw a long pause treated it as a
gap to be closed - and a gap that is a beat is not a gap.

Each test names one defect this module closes:

- a stretch with NO WORDS is proposed as a moment to HOLD, so dead air
  is kept for its own sake;
- the baseline is the ENGINE'S OWN constant rather than the material's,
  so a speaker who is slow throughout is read as slowing at every
  moment;
- a moment with no measured evidence at all is proposed, so the module
  invents a hold;
- the length of the hold is INVENTED, so the module asks for time
  nobody asked it to ask for;
- a moment the direction asked to be slow is not recognised, so the
  direction's own pacing curve reaches nothing.

None of them asserts that the module contains a particular entry or a
particular count: blocks are built and measured, so a test passes only
when the module actually does what the defect requires.
"""
from __future__ import annotations

from library.tools import pacing_hold
from library.tools.editorial_policy import parse_policy


def _block(position, words, *, rate=2.0, serves_beats=(),
           clip_id="clip_001", source_start=0.0, source_end=4.0):
    span = float(source_end) - float(source_start)
    timestamps = []
    for index in range(words):
        start = source_start + index * (span / max(1, words))
        timestamps.append({"word": f"w{index}", "source_start": start,
                           "source_end": start + (span / max(1, words))})
    return {
        "position": position,
        "clip_id": clip_id,
        "timeline_start": float(position) * 10.0,
        "timeline_end": float(position) * 10.0 + span,
        "source_start": source_start,
        "source_end": source_end,
        "word_timestamps": timestamps,
        "serves_beats": list(serves_beats),
    }


def _policy(**slots):
    return parse_policy({"objective": "interview", "slots": slots},
                        project_id="demo")


def _prosody(word="w0", start=0.0, end=0.2, emphasis=0.9):
    return {"prosody": {"word_prosody": [
        {"word": word, "start": start, "end": end, "emphasis": emphasis}]}}


# ── What is never a hold ──────────────────────────────────────────

def test_a_stretch_with_no_words_is_never_a_hold():
    """Dead air is `library/tools/pacing.py`'s finding, not a moment to keep."""
    blocks = [_block(1, 0), _block(2, 4)]
    found = pacing_hold.hold_candidates(blocks)
    assert all(row["position"] != 1 for row in found["candidates"])


def test_a_block_with_no_evidence_at_all_is_not_proposed():
    """A hold with nothing behind it is an invention."""
    blocks = [_block(1, 4)]
    found = pacing_hold.hold_candidates(blocks)
    assert found["candidates"][0]["evidence"] == []


# ── The baseline is the material's ─────────────────────────────────

def test_the_baseline_is_the_materials_own_median_block_rate():
    """A speaker who is slow throughout is not read as slowing everywhere."""
    blocks = [_block(1, 8), _block(2, 8), _block(3, 8)]
    found = pacing_hold.hold_candidates(blocks)
    baseline = found["baseline_rate_words_per_second"]
    for row in found["candidates"]:
        assert row["delivery_rate_words_per_second"] == baseline


def test_a_block_slower_than_the_material_carries_the_evidence():
    """The speaker slowed here, and the material says so."""
    blocks = [_block(1, 8), _block(2, 8), _block(3, 2)]
    found = pacing_hold.hold_candidates(blocks)
    slow = [row for row in found["candidates"]
            if pacing_hold.SLOWED_HERE in row["evidence"]]
    assert slow
    assert slow[0]["position"] == 3


# ── The other three evidences ─────────────────────────────────────

def test_a_measured_emphasis_inside_the_block_is_evidence():
    """The speaker's own marking is evidence."""
    blocks = [_block(1, 4)]
    found = pacing_hold.hold_candidates(
        blocks, prosody_profiles={"clip_001": _prosody()})
    assert pacing_hold.EMPHASIS_INSIDE in found["candidates"][0]["evidence"]


def test_a_pause_inside_the_block_is_evidence():
    """A block whose words are separated by a gap is holding something."""
    block = _block(1, 2, source_start=0.0, source_end=6.0)
    block["word_timestamps"] = [
        {"word": "a", "source_start": 0.0, "source_end": 0.2},
        {"word": "b", "source_start": 5.0, "source_end": 5.2},
    ]
    found = pacing_hold.hold_candidates([block])
    assert pacing_hold.HOLDS_A_PAUSE in found["candidates"][0]["evidence"]
    assert found["candidates"][0]["internal_gap_seconds"] == 4.8


def test_the_directions_own_pacing_curve_is_evidence():
    """A beat the direction declared low is a direction the engine read."""
    blocks = [_block(1, 4, serves_beats=("beat:1",))]
    intent = {"pacing_curve": [{"beat_id": "beat:1", "energy": 0.2}]}
    found = pacing_hold.hold_candidates(blocks, intent_model=intent)
    assert pacing_hold.DIRECTED_LOW_ENERGY in found["candidates"][0]["evidence"]


def test_a_beat_the_direction_declared_energetic_is_not_evidence():
    """The direction asked for energy, and the engine read that too."""
    blocks = [_block(1, 4, serves_beats=("beat:1",))]
    intent = {"pacing_curve": [{"beat_id": "beat:1", "energy": 0.9}]}
    found = pacing_hold.hold_candidates(blocks, intent_model=intent)
    assert pacing_hold.DIRECTED_LOW_ENERGY not in found["candidates"][0]["evidence"]


# ── The length of the hold ────────────────────────────────────────

def test_an_undeclared_hold_length_asks_for_no_extra_time():
    """The module does not invent time nobody asked it to ask for."""
    blocks = [_block(1, 4)]
    found = pacing_hold.hold_candidates(blocks)
    assert found["hold_minimum_seconds"] is None
    assert found["candidates"][0]["hold_extension_seconds"] == 0.0


def test_a_declared_hold_minimum_extends_the_proposal():
    """The project says how long a deliberate hold may be."""
    blocks = [_block(1, 4)]
    found = pacing_hold.hold_candidates(
        blocks, policy=_policy(pacing={"hold_minimum_seconds": 8.0}))
    assert found["candidates"][0]["proposed_hold_seconds"] == 8.0
    assert found["candidates"][0]["hold_extension_seconds"] == 4.0


def test_the_declared_target_average_shot_length_is_reported():
    """The cut's own pace is reported against the target it declared."""
    blocks = [_block(1, 4), _block(2, 4)]
    found = pacing_hold.hold_candidates(
        blocks, policy=_policy(pacing={"target_asl_seconds": 3.0}))
    assert found["target_average_shot_seconds"] == 3.0
    assert found["measured_average_shot_seconds"] is not None
