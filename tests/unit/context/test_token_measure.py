"""The engine's own token counter: WHAT it prices, and IN WHAT unit.

Three defects are named here, and each one is a way the pipeline's own
figures could not be trusted:

- **The price carried the UNPROJECTED inputs.**  `model_task` counted
  `str(inputs)` - the step's inputs BEFORE `project_context` narrowed
  them - and then added the prompt on top.  A request was billed for
  material it never sent: project 001's `speech_sequence` logged 246,252
  tokens for a request that was 63,598 bytes.
- **The unit was a guess.**  Both halves were `len(s.split()) * 1.3`, a
  word count scaled by a constant, which the LLM context audit measured
  to be wrong by 0.3x to 9.9x in both directions.
- **The count named nothing.**  It was published under `token_count` and
  `input_tokens` with no tokenizer attached, so two figures could not be
  compared even when both were right.

The fix: the price is the text that was FILED, counted by a NAMED
tokenizer, and the exact byte count travels beside it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import (
    llm_handshake,
    model_task,
    perf_ledger,
    token_measure,
)
from library.tools.project_layout import Area, layout_for
from library.tools.replay_bench import tokens as bench_tokens

NODE_ID = "priced_step"


def _run_one_call(tmp_path, monkeypatch, inputs, *, context_fields=None):
    """Ask one model question and return what it was billed."""
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nSENTINEL_HANDOFF_BODY\n", encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{NODE_ID}.json").write_text(
            json.dumps({"answer": "ok"}), encoding="utf-8")
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    task = model_task.ModelTask(
        key=NODE_ID,
        handoff_path=str(handoff),
        outputs=({"name": "answer", "type": "string", "required": True,
                  "description": "your answer"},),
        llm_outputs=({"name": "answer", "type": "string", "required": True,
                      "description": "your answer"},),
        context_fields=context_fields,
        reads_brief=False,
        skills=None,
    )
    with perf_ledger.capability(str(project), "planning", "r1"):
        model_task.run_model_task(
            task, dict(inputs, project_folder=str(project)),
            full_auto="agent", llm_timeout=5)

    request = llm_handshake.read_request(
        layout.read_dir(Area.LLM_REQUESTS) / f"{NODE_ID}.json")
    rows = [r for r in perf_ledger.read_rows(str(project))
            if r.get("layer") == "host_model"]
    assert rows, "the call must be priced in the ledger"
    return request, rows[-1]


def _filed_text(request) -> str:
    return (request.get("prompt") or "") + (request.get("context") or "")


# ── 1. The price is the text that was FILED ────────────────────────

def test_the_price_of_a_request_is_the_text_that_was_filed(
        tmp_path, monkeypatch):
    """The ledger's figure is the count of the filed request, exactly.

    It used to be the count of the UNPROJECTED inputs plus the prompt,
    so it priced material the request never carried.
    """
    request, row = _run_one_call(tmp_path, monkeypatch, {
        "kept": "the context the step asked for",
        "dropped": "word " * 5000,
    }, context_fields=["kept"])

    filed = _filed_text(request)
    expected = token_measure.measure_text(filed)
    assert row["input_bytes"] == expected["bytes"]
    if expected["tokens"] is not None:
        assert row["input_tokens"] == expected["tokens"]


def test_a_request_is_not_billed_for_the_inputs_the_projection_dropped(
        tmp_path, monkeypatch):
    """The defect: 246,252 logged tokens for a 63,598-byte request.

    The dropped key is thousands of times larger than what was filed, so
    a price that still carried it cannot hide behind the request's own
    size.
    """
    dropped = "word " * 50000
    request, row = _run_one_call(tmp_path, monkeypatch, {
        "kept": "small",
        "dropped": dropped,
    }, context_fields=["kept"])

    assert dropped not in _filed_text(request)
    unprojected = token_measure.measure_text(str({
        "kept": "small", "dropped": dropped, "project_folder": ""}))
    assert row["input_bytes"] < unprojected["bytes"] / 10


# ── 2. Every count names its tokenizer ─────────────────────────────

def test_every_published_count_names_its_tokenizer(tmp_path, monkeypatch):
    _request, row = _run_one_call(tmp_path, monkeypatch, {"kept": "x"})
    assert row.get("tokenizer") in token_measure.available()
    assert row.get("input_bytes") is not None


def test_a_measurement_carries_its_unit():
    measured = token_measure.measure_text("a small request")
    assert measured["bytes"] == len(b"a small request")
    if measured["tokens"] is None:
        # No tokenizer ran: there is no token count, and none is claimed.
        assert measured["tokenizer"] is None
    else:
        assert measured["tokenizer"] == token_measure.O200K
        assert measured["tokens"] > 0


def test_a_tokenizer_that_cannot_run_is_never_replaced_by_an_estimate():
    with pytest.raises(ValueError, match="unknown tokenizer"):
        token_measure.count("x", "gpt-guess")
    if token_measure.O200K not in token_measure.available():
        with pytest.raises(RuntimeError, match="tiktoken"):
            token_measure.count("x", token_measure.O200K)


def test_the_engine_and_the_bench_measure_with_the_same_code():
    """One implementation, so the two can be compared at all."""
    sample = "a request the engine and the bench both measure"
    assert bench_tokens.count is token_measure.count
    assert bench_tokens.measure(sample) == token_measure.measure(sample)


# ── 3. Two variants can be ranked ──────────────────────────────────

def test_two_variants_are_ranked_by_the_same_measure(tmp_path, monkeypatch):
    """The point of the whole fix: a bigger request costs more, and the
    two are measured in ONE unit, so the ranking is a fact."""
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    small_dir.mkdir()
    large_dir.mkdir()

    _r1, small_row = _run_one_call(small_dir, monkeypatch, {
        "kept": "one line"})
    r2, large_row = _run_one_call(large_dir, monkeypatch, {
        "kept": "one line " * 200})

    assert small_row["input_bytes"] < large_row["input_bytes"]
    if small_row.get("input_tokens") is not None:
        assert small_row["input_tokens"] < large_row["input_tokens"]
    # The old figure was a word count scaled by 1.3 - and it is not what
    # the engine prices a request with any more.
    assert small_row["input_bytes"] != bench_tokens.pipeline_heuristic(
        _filed_text(r2))
