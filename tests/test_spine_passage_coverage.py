"""Step 2.05's content-loss arithmetic belongs to the script.

The handoff asks the model for "No content loss: Every speech passage
from body_sequence appears in exactly one speech block" - set coverage
over small integers. The post-bridge already refused DANGLING refs; the
other two clerical failures (a passage no block names, a passage two
speech blocks name) had no check. `validate_passage_coverage` in
`library/tools/spine_contract.py` owns both, and `enrich_spine` runs
it before emitting.

A hook reusing a body passage is neither failure: the handoff permits
it explicitly. Proven below in both directions.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_2_05_mesh_spine.post_bridge import (  # noqa: E402
    enrich_spine,
)
from library.tools.spine_contract import (  # noqa: E402
    SpineContractError,
    validate_passage_coverage,
)


def _passage(clip_id, start, end, text):
    return {
        "clip_id": clip_id,
        "source_start": start,
        "source_end": end,
        "text": text,
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": text.split()[0], "source_start": start,
             "source_end": start + 1.0}
        ],
    }


def _speech_sequence(n):
    return {
        "body_sequence": [
            _passage(f"clip_{i:03d}", float(i * 10), float(i * 10 + 2),
                     f"Passage number {i}")
            for i in range(1, n + 1)
        ],
    }


def _spine(refs):
    """`refs`: list of (block_type, passage_ref)."""
    return {
        "structure": [
            {"position": i, "block_type": kind,
             "content": {"passage_ref": ref}}
            for i, (kind, ref) in enumerate(refs, start=1)
        ],
    }


# ── The contract function, both directions ───────────────────────────

def test_a_passage_no_block_names_is_content_loss():
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine([("speech", 1)])["structure"], 2)
    assert "[2]" in str(excinfo.value)
    assert "content loss" in str(excinfo.value)


def test_a_passage_in_two_speech_blocks_is_a_repeat():
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine([("speech", 1), ("speech", 1)])["structure"], 1)
    assert "[1]" in str(excinfo.value)
    assert "twice" in str(excinfo.value)


def test_a_hook_covers_but_does_not_double():
    """Hook on 2, speech on 2, body of 2: passage 1 is still lost, and
    the error must say loss, not doubling."""
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine([("hook", 2), ("speech", 2)])["structure"], 2)
    message = str(excinfo.value)
    assert "content loss" in message
    assert "twice" not in message


# ── Wired into enrich_spine, not defined beside it ───────────────────

def test_enrich_spine_refuses_a_dropped_passage():
    with pytest.raises(SpineContractError):
        enrich_spine(_spine([("speech", 1)]), _speech_sequence(2), {}, {})


def test_enrich_spine_accepts_hook_reuse():
    result = enrich_spine(
        _spine([("hook", 2), ("speech", 1), ("speech", 2)]),
        _speech_sequence(2), {}, {})
    assert len(result["audio_spine"]["structure"]) == 3
