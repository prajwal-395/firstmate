"""Step 2.02's handoff names the word timings it actually receives.

Finding 23, execution-frontier report 2026-09-24: the prompt still
said "WhisperX word timings" after the transcription path had moved to
the temporal index. That names an instrument the step no longer owns
and can mislead the model about the source of its exact timings.

This pins the model-facing handoff and its generated operation summary
to the current source: word timings from the temporal index.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import operations  # noqa: E402


HANDOFF = (REPO_ROOT / "library" / "steps" / "step_2_02_speech_sequence"
           / "handoff.md")


def test_speech_sequence_prompt_names_temporal_index_word_timings():
    prompt = HANDOFF.read_text(encoding="utf-8")
    assert "WhisperX word timings" not in prompt
    assert "temporal index's word timings" in prompt
    assert "_align_words_to_text" in prompt
    operation = next(op for op in operations.all()
                     if op.name == "speech.enrich")
    assert "WhisperX" not in operation.summary
    assert "word timings from the temporal index" in operation.summary
    checked_in = (REPO_ROOT / ".agents" / "skills" / "pipeline_operations"
                  / "SKILL.md").read_text(encoding="utf-8")
    assert "WhisperX word timings" not in checked_in
    assert "word timings from the temporal index" in checked_in
