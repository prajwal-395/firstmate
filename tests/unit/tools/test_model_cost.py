"""FIX 6 defects: a model answer's cost that no ledger ever recorded.

The captain asked what planning costs against what building costs, and
the ledger could not answer: a host model that answered Ren outside a
pipeline step's own capability - a touch-up's edit spec, a reel's visual
ask - left no row at all, so `ren profile`'s cost section read empty on
every run that asked a model anything.

Each test below names the defect it pins.
"""

import json
from pathlib import Path

from library.tools import edit_spec, llm_handshake, model_cost, perf_ledger
from library.tools.perf_report import render_cost
from library.tools.project_layout import ProjectLayout


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectLayout(str(project)).ensure()
    return project


def _host_model_rows(project):
    return [row for row in perf_ledger.read_rows(str(project))
            if row.get("layer") == "host_model"]


def test_a_model_answer_taken_outside_a_capability_is_recorded(tmp_path):
    """DEFECT: an answer nobody attached to a capability cost nothing.

    The touch-up's edit spec is answered by a host model while no
    capability is working, so the row the ledger has no home for was
    never written. The cost existed; the ledger did not have it.
    """
    project = _project(tmp_path)

    recorded = model_cost.record_answer(
        str(project), exchange_id="edit_spec_abc",
        request_text="one two three four five",
        response_text="six seven eight")

    assert recorded is not None
    rows = _host_model_rows(project)
    assert len(rows) == 1
    # The count is the engine's ONE counting module's, and it names the
    # tokenizer that produced it. A count without its unit is the defect
    # the LLM context audit found in the old word-count figure.
    from library.tools import token_measure
    request_measure = token_measure.measure_text("one two three four five")
    response_measure = token_measure.measure_text("six seven eight")
    assert rows[0]["input_bytes"] == request_measure["bytes"]
    assert rows[0]["output_bytes"] == response_measure["bytes"]
    assert rows[0].get("input_tokens") == request_measure["tokens"]
    assert rows[0].get("output_tokens") == response_measure["tokens"]
    # The count names the unit that produced it: the tokenizer where one
    # ran, and the byte count's own name where none did. Never None.
    assert rows[0]["tokenizer"] in token_measure.available()
    assert rows[0]["tokenizer"] == (request_measure["tokenizer"]
                                     or token_measure.BYTES)
    assert rows[0]["calls"] == 1
    assert rows[0]["tokens_estimated"] is False


def test_the_same_answer_read_twice_is_recorded_once(tmp_path):
    """DEFECT: a cost recorded twice is a cost the captain did not pay.

    A proposal previewed and then proposed, a revision re-read on
    resume: the same answer, read again, cost the host nothing more.
    """
    project = _project(tmp_path)

    first = model_cost.record_answer(
        str(project), exchange_id="edit_spec_abc",
        request_text="one two", response_text="three four")
    second = model_cost.record_answer(
        str(project), exchange_id="edit_spec_abc",
        request_text="one two", response_text="three four")

    assert first is not None
    assert second is None
    assert len(_host_model_rows(project)) == 1


def test_a_second_answer_to_the_same_request_is_recorded_again(tmp_path):
    """DEFECT (the other direction): a re-answer cost the host again.

    The marker is the exchange and the ANSWER, so a host that answered
    twice is paid for twice.
    """
    project = _project(tmp_path)

    model_cost.record_answer(
        str(project), exchange_id="edit_spec_abc",
        request_text="one two", response_text="three four")
    model_cost.record_answer(
        str(project), exchange_id="edit_spec_abc",
        request_text="one two", response_text="five six")

    rows = _host_model_rows(project)
    assert len(rows) == 2
    assert rows[0]["exchange"] != rows[1]["exchange"]


def test_an_answer_read_inside_a_capability_joins_that_capability(tmp_path):
    """DEFECT: an answer read inside a capability lost its run.

    The row must carry the run and the capability that were working, so
    a profile can charge the cost to the work it belongs to.
    """
    project = _project(tmp_path)

    with perf_ledger.capability(str(project), "edit_spec", "run-1"):
        model_cost.record_answer(
            str(project), exchange_id="edit_spec_abc",
            request_text="one two", response_text="three four")

    rows = _host_model_rows(project)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["capability"] == "edit_spec"


def test_the_profile_reports_a_run_s_model_cost(tmp_path):
    """DEFECT: `ren profile`'s cost section was empty of model cost.

    A run with model calls and no verified editorial decision still has
    a cost, and the profile has to say what it was.
    """
    rows = [
        {"kind": "capability", "run_id": "run-1", "capability": "reel.build",
         "started_at": 10.0, "wall_s": 10.0, "cpu_s": 1.0},
        {"kind": "span", "run_id": "run-1", "capability": "reel.build",
         "layer": "host_model", "started_at": 11.0, "wall_s": 0.0,
         "calls": 1, "input_tokens": 100, "output_tokens": 20,
         "input_bytes": 400, "output_bytes": 80,
         "tokenizer": "o200k_base", "tokens_estimated": False},
    ]

    report = perf_ledger.profile(rows, "run-1")

    assert report["model_cost"]["measured"] is True
    assert report["model_cost"]["unit"] == "tokens"
    assert report["model_cost"]["input_tokens"] == 100
    assert report["model_cost"]["output_tokens"] == 20
    assert report["model_cost"]["input_bytes"] == 400
    assert report["cost_per_decision"]["run_cost"]["model_tokens"] == 120
    text = "\n".join(render_cost(report))
    assert "Model cost in this run" in text
    assert "100" in text and "20" in text


def test_a_run_whose_only_cost_is_bytes_says_which_unit_it_measured():
    """DEFECT (the other direction): a byte count is not a token count.

    Where no tokenizer ran, the token columns are ABSENT and the exact
    byte count is the measure - and the profile says which it is rather
    than printing a zero as though it were a token count.
    """
    rows = [
        {"kind": "capability", "run_id": "run-1", "capability": "reel.build",
         "started_at": 10.0, "wall_s": 10.0, "cpu_s": 1.0},
        {"kind": "span", "run_id": "run-1", "capability": "reel.build",
         "layer": "host_model", "started_at": 11.0, "wall_s": 0.0,
         "calls": 1, "input_bytes": 400, "output_bytes": 80,
         "tokens_estimated": False},
    ]

    report = perf_ledger.profile(rows, "run-1")

    assert report["model_cost"]["unit"] == "bytes"
    text = "\n".join(render_cost(report))
    assert "utf8_bytes" in text
    assert "400" in text and "80" in text


def test_a_run_with_no_model_call_reads_its_cost_as_unmeasured(tmp_path):
    """DEFECT (the other direction): no model call is not a zero.

    A run that asked no model anything has no measured model cost, and
    the profile says so rather than reporting nothing measured as
    nothing spent.
    """
    rows = [
        {"kind": "capability", "run_id": "run-1", "capability": "scan",
         "started_at": 10.0, "wall_s": 10.0, "cpu_s": 1.0},
    ]

    report = perf_ledger.profile(rows, "run-1")

    assert report["model_cost"]["measured"] is False
    text = "\n".join(render_cost(report))
    assert "unmeasured" in text


def test_the_reel_visual_answer_records_what_the_model_cost(tmp_path):
    """DEFECT: the reel's visual planning was the model's cost, unseen.

    The ask is written on every build and its answer outlives it, so
    the cost was paid once for an answer the build read again and again.
    """
    from library.tools import reel_semantic_visual
    from library.tools.project_layout import Area

    project = _project(tmp_path)
    layout = ProjectLayout(str(project))
    request_id = reel_semantic_visual.request_stem(13)
    requests_dir = layout.write_dir(Area.LLM_REQUESTS)
    responses_dir = layout.write_dir(Area.LLM_RESPONSES)
    llm_handshake.write_request_file(
        requests_dir / f"{request_id}.json",
        {"step_id": request_id, "prompt": "plan the visuals",
         "constraints": "", "context": {"shots": ["one", "two"]},
         "expected_schema": "{}", "project_folder": str(project),
         "timestamp": "2026-10-20T00:00:00+00:00"})
    (responses_dir / f"{request_id}.json").write_text(
        json.dumps({"motion_graphics_plan": []}), encoding="utf-8")

    answer = reel_semantic_visual.read_answer(str(project), 13)

    assert answer == []
    rows = _host_model_rows(project)
    assert len(rows) == 1
    assert rows[0]["exchange_id"] == request_id
    assert rows[0]["input_bytes"] > 0


def test_the_edit_spec_answer_records_what_the_model_cost(tmp_path):
    """DEFECT: the touch-up's planning was the model's cost, unseen.

    The edit spec is answered by a host model outside any capability,
    so its cost was the one the captain could not see at all.
    """
    project = _project(tmp_path)
    request_id, _path, _note_id = edit_spec.prepare_request(
        str(project), "isolate the host mic")
    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps({
            "format": edit_spec.FORMAT,
            "request": "isolate the host mic",
            "clauses": [{
                "id": "op-1",
                "text": "isolate the host mic",
                "op": "voice_isolation",
                "op_source": "model",
                "status": "resolved",
                "anchor": {"kind": "reel"},
                "anchor_source": "requester",
                "reel": "Reel 09 - hook",
                "reel_source": "requester",
                "values": {
                    "track": {"value": 1, "unit": "track index",
                              "stated_by": "model"},
                    "amount": {"value": 60, "unit": "percent",
                               "stated_by": "requester"},
                },
            }],
        }), encoding="utf-8")

    edit_spec.load_response_spec(str(project), request_id)

    rows = _host_model_rows(project)
    assert len(rows) == 1
    assert rows[0]["input_bytes"] > 0
    assert rows[0]["output_bytes"] > 0
    assert rows[0]["exchange_id"] == request_id
