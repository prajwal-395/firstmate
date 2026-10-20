"""The editorial live fixture's setup and its batch, checked offline.

The live AXI-11 lane runs against a real Resolve, and the fixture that
prepares it is where a live run loses its evidence (measured
2026-10-20: three requests reported "Fixture batch emitted no JSON"
because the fixture step's duplication of an existing `fm-scratch
qualify` was refused, and because one failing case ended the whole
batch). What is checked here is the contract the harness itself
carries, so a defect in the harness is named before it costs a live
run:

* the editorial batch never writes on the PROTECTED fixture timeline,
  and the protected timeline's own content is never cleared by the
  fixture;
* the disposable working timeline is created by DUPLICATING a source,
  never by duplicating over the protected one;
* a case that raises is reported as a JSON failure and the cases
  around it keep their evidence;
* the batch's result block is read from the step that carries it, and a
  batch that printed none is reported as a JSON failure rather than as
  silence;
* a fixture step that refused is retried, and a batch that failed is
  not.
"""

from __future__ import annotations

import json

from tests.live import editorial_live_adapter as adapter
from tests.live import small_edit_qualification as q

# ── the protected fixture is never written on ──────────────────────

def test_the_editorial_batch_works_on_a_disposable_timeline():
    working = adapter._working_timeline()
    assert working != q.PROTECTED_TIMELINE, (
        "the editorial batch writes on the protected fixture timeline; "
        "every live lane shares it")
    assert working.startswith(q.PROTECTED_TIMELINE), (
        f"{working!r} leaves the fixture namespace "
        f"({q.PROTECTED_TIMELINE!r}*), so the broker's fixture scoping "
        "does not recognise it")


def test_the_disposable_name_carries_a_stamp():
    first = q.disposable_timeline_name("20261020T120000Z-ab12")
    second = q.disposable_timeline_name("20261020T120000Z-cd34")
    assert first == f"{q.PROTECTED_TIMELINE} 20261020T120000Z-ab12"
    assert first != second, "two batches must never share a timeline"


def test_a_disposable_name_without_a_stamp_is_refused():
    try:
        q.disposable_timeline_name("   ")
    except ValueError:
        return
    raise AssertionError("a disposable timeline name needs a stamp")


def test_the_protected_fixture_is_never_cleared_by_the_fixture():
    protected = q._select_timeline_script(q.PROTECTED_TIMELINE)
    disposable = q._select_timeline_script(
        q.disposable_timeline_name("20261020T120000Z-ab12"))
    assert "DeleteClips" not in protected, (
        "the fixture clears the protected timeline's content; the other "
        "live lanes share it")
    assert "DeleteClips" in disposable, (
        "a disposable copy carries the source's items, so the batch "
        "would start on a dirty timeline")


def test_the_disposable_is_duplicated_from_a_source_not_over_the_protected():
    argv = q.duplicate_command("fm-scratch qualify 20261020T120000Z-ab12",
                                q.PROTECTED_TIMELINE)
    assert argv[:2] == ["timeline", "duplicate"]
    assert "--timeline" in argv and "--name" in argv
    source = argv[argv.index("--timeline") + 1]
    name = argv[argv.index("--name") + 1]
    assert source == q.PROTECTED_TIMELINE
    assert name != q.PROTECTED_TIMELINE, (
        "the duplication would be refused: the name is taken")


def test_the_fixture_sources_start_at_the_protected_fixture():
    sources = q.fixture_source_candidates()
    assert sources[0] == q.PROTECTED_TIMELINE
    assert "Q Spine" in sources, (
        "the qualification project's own fixture is the fallback when "
        "the protected one is absent")


# ── a failing case is a JSON failure, never silence ────────────────

_CASE_SCRIPT = '''\
def teardown():
    pass


def ensure_tracks(names):
    return []


results = {}

if TASK in ("all", "alpha"):
    results["alpha"] = {"ok": True}

if TASK in ("all", "beta"):
    raise RuntimeError("beta refused")

if TASK in ("all", "gamma"):
    results["gamma"] = {"ok": True}

result = {"cases": results}
'''


def _run_isolated(task: str) -> dict:
    script = adapter._with_isolated_cases(_CASE_SCRIPT)
    namespace = {"TASK": task}
    exec(compile(script, "<isolated>", "exec"), namespace)  # noqa: S102
    return namespace["result"]


def test_a_case_that_raises_does_not_silence_the_batch():
    result = _run_isolated("all")
    assert result["cases"]["alpha"] == {"ok": True}
    assert result["cases"]["gamma"] == {"ok": True}, (
        "one failing case took the other cases' evidence with it")
    assert "beta refused" in result["cases"]["beta"]["error"]


def test_a_case_that_raises_is_reported_under_its_own_name():
    result = _run_isolated("beta")
    assert set(result["cases"]) == {"beta"}
    assert "beta refused" in result["cases"]["beta"]["error"]


def test_the_batch_always_ends_with_its_json_result():
    script = adapter._with_isolated_cases(_CASE_SCRIPT)
    assert script.rstrip().endswith("result['failure'] = _failure") or (
        "result = {'cases': results}" in script)
    assert "result = {'cases': results}" in script


def test_a_failed_setup_is_reported_as_the_batch_failure():
    script = adapter._with_isolated_cases(
        "def teardown():\n    pass\n\n\ndef ensure_tracks(names):\n"
        "    raise RuntimeError('setup refused')\n\n\nresults = {}\n\n"
        "ensure_tracks([\"V2\", \"V3\"])\n\n"
        "if TASK in (\"all\", \"alpha\"):\n    results[\"alpha\"] = {}\n\n"
        "result = {\"cases\": results}\n")
    namespace = {"TASK": "all"}
    exec(compile(script, "<isolated>", "exec"), namespace)  # noqa: S102
    result = namespace["result"]
    assert "setup refused" in result["failure"]
    assert result["cases"] == {}


def test_the_result_block_is_read_from_the_step_that_carries_it():
    receipt = {"result": {"steps": [
        {"stdout": "FIXTURE_TIMELINE{'exit': 0}\n"},
        {"stdout": "run:\n  timeline: fm-scratch qualify\n"},
        {"stdout": "{\n  \"cases\": {}\n}\n"},
    ]}}
    assert "cases" in q.result_stdout(receipt)


def test_the_run_receipts_own_json_is_not_the_batch_result():
    stdout = ("{\n  \"inventory\": {}\n}\n"
              "run:\n  timeline: fm-scratch qualify\n"
              "{\n  \"cases\": {\"trim_pause\": {\"ok\": true}}\n}\n")
    payload = adapter._cases_payload(stdout)
    assert payload["cases"] == {"trim_pause": {"ok": True}}


def test_a_batch_without_a_json_block_is_reported_as_a_json_failure():
    failure = adapter._batch_failure(
        "no_json", "the batch printed no JSON result")
    document = json.loads(str(failure))
    assert document["category"] == "fixture_batch_failed"
    assert document["reason"] == "no_json"


def test_a_failed_case_is_reported_as_a_json_failure():
    failure = adapter._batch_failure(
        "case_failed", "RuntimeError: the case refused", task="trim_pause")
    document = json.loads(str(failure))
    assert document["task"] == "trim_pause"
    assert "the case refused" in document["detail"]


# ── the fixture step is retried; the batch is not ──────────────────

def _refused(step: int, message: str) -> dict:
    return {"state": "failed", "error": f"step {step} exited 1: {message}",
            "result": {"steps": [], "failed_step": step}}


def _done() -> dict:
    return {"state": "done", "result": {"steps": [{"stdout": "{\n}\n"}]}}


def test_a_fixture_refusal_is_classified():
    assert q._fixture_refusal(
        "error: a timeline named 'x' already exists") == "already_exists"
    assert q._fixture_refusal(
        "error: no timeline named exactly 'x'.") == "no_source"
    assert q._fixture_refusal("the batch ran out of time") == ""


def test_a_refused_fixture_step_is_retried_without_the_duplication(
        monkeypatch):
    seen: list[list[dict]] = []

    def fake_submit(steps, wait_seconds, timeout_seconds):
        seen.append(steps)
        if len(seen) == 1:
            return _refused(0, "error: a timeline named 'x' already exists")
        return _done()

    monkeypatch.setattr(q, "_submit_session", fake_submit)
    receipt = q._qualification_batch(
        "result = {'cases': {}}", timeline="fm-scratch qualify 20261020T-ab12")
    assert receipt["state"] == "done"
    assert len(seen) == 2, "the refused fixture step was not retried"
    assert seen[0][0]["resolve_axi"][:2] == ["timeline", "duplicate"]
    assert seen[1][0]["resolve_axi"][0] == "run", (
        "the retry duplicated again; the disposable is already there")


def test_a_failed_batch_is_not_retried(monkeypatch):
    seen: list[list[dict]] = []

    def fake_submit(steps, wait_seconds, timeout_seconds):
        seen.append(steps)
        return _refused(1, "error: the case refused")

    monkeypatch.setattr(q, "_submit_session", fake_submit)
    try:
        q._qualification_batch("result = {'cases': {}}",
                               timeline="fm-scratch qualify 20261020T-ab12")
    except RuntimeError as exc:
        assert "the case refused" in str(exc)
    else:
        raise AssertionError("the failed batch was reported as a success")
    assert len(seen) == 1, (
        "the batch ran again after a failure; its cases would be "
        "written twice")
