"""LE-11: a request's shared text is written once and rebuilt exactly.

The wire form (`prompt_ref`, `context_refs`) is what lands on disk; the
materialized form (`llm_handshake.read_request`) is what the model answers.
These pin the round trip, the refusals when shared text is gone or altered,
and that the archivers carry the shared text with the requests they copy.
"""

from __future__ import annotations

import json
import shutil

import pytest

from library.tools import llm_handshake, pending_asks


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for marker in ("PIPELINE_PROJECTS_ROOT", "REN_OUTPUT"):
        monkeypatch.delenv(marker, raising=False)


def _request(project, prompt="Decide the cut.\n", constraints="\nBrand: red.\n",
             context="rows: 3\n"):
    return llm_handshake.build_request(
        "plan_cut", prompt, constraints, context, '{"cut": "str"}',
        str(project), "2026-10-12T00:00:00+00:00")


def _requests_dir(project):
    return project / "pipeline_output" / "llm_requests"


def test_constraints_are_on_disk_once_and_the_prompt_rebuilds(tmp_path):
    payload = _request(tmp_path)
    assert payload["prompt"] == "Decide the cut.\n\nBrand: red.\n"
    path = _requests_dir(tmp_path) / "plan_cut.json"

    wire = llm_handshake.write_request_file(path, payload)

    on_disk = path.read_text(encoding="utf-8")
    assert on_disk.count("Brand: red.") == 1
    assert "prompt" not in json.loads(on_disk)
    shared = (llm_handshake.shared_dir(path.parent)
              / f"{wire['prompt_ref']['sha256']}.md")
    assert shared.read_text(encoding="utf-8") == "Decide the cut.\n"
    rebuilt = llm_handshake.read_request(path)
    assert rebuilt == payload
    assert list(rebuilt) == list(payload)


def test_a_prompt_that_does_not_end_with_its_constraints_refuses(tmp_path):
    payload = dict(_request(tmp_path), prompt="something else")
    with pytest.raises(ValueError, match="does not end with its"):
        llm_handshake.share_request(payload, _requests_dir(tmp_path))


def test_shared_context_sections_are_cut_out_and_spliced_back(tmp_path):
    roster = "roster: |\n  a\n  b\n"
    payload = _request(tmp_path, context=f"{roster}\nrows: 3\n{roster}tail")
    path = _requests_dir(tmp_path) / "plan_cut.json"

    wire = llm_handshake.write_request_file(
        path, payload, context_shared=(roster, roster))

    assert wire["context"] == "\nrows: 3\ntail"
    assert [ref["offset"] for ref in wire["context_refs"]] == [
        0, len(roster) + len("\nrows: 3\n")]
    assert llm_handshake.read_request(path) == payload
    with pytest.raises(ValueError, match="is not in the context"):
        llm_handshake.share_request(payload, path.parent,
                                    context_shared=("absent",))


def test_missing_or_altered_shared_text_refuses(tmp_path):
    path = _requests_dir(tmp_path) / "plan_cut.json"
    wire = llm_handshake.write_request_file(path, _request(tmp_path))
    shared = (llm_handshake.shared_dir(path.parent)
              / f"{wire['prompt_ref']['sha256']}.md")

    shared.write_text("Decide something else.\n", encoding="utf-8")
    with pytest.raises(llm_handshake.SharedTextMissing, match="altered"):
        llm_handshake.read_request(path)

    shared.unlink()
    with pytest.raises(llm_handshake.SharedTextMissing, match="missing"):
        llm_handshake.read_request(path)


def test_a_copied_request_resolves_through_its_recorded_path(tmp_path):
    path = _requests_dir(tmp_path) / "plan_cut.json"
    payload = _request(tmp_path)
    llm_handshake.write_request_file(path, payload)
    elsewhere = tmp_path / "copy"
    elsewhere.mkdir()
    shutil.copy2(path, elsewhere / path.name)

    assert llm_handshake.read_request(elsewhere / path.name) == payload


def test_a_request_written_before_shared_text_reads_unchanged(tmp_path):
    path = _requests_dir(tmp_path) / "plan_cut.json"
    path.parent.mkdir(parents=True)
    legacy = _request(tmp_path)
    path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")

    assert llm_handshake.read_request(path) == legacy
    report = pending_asks.collect(tmp_path)
    assert report["pending"] == 1
    assert report["shared"][report["asks"][0]["prompt"]]["text"] == \
        legacy["prompt"]


def test_publish_request_lands_the_wire_form_and_clears_the_answer(tmp_path):
    request = _requests_dir(tmp_path) / "plan_cut.json"
    response = tmp_path / "pipeline_output" / "llm_responses" / "plan_cut.json"
    response.parent.mkdir(parents=True)
    response.write_text("{}", encoding="utf-8")
    payload = _request(tmp_path)

    llm_handshake.publish_request(request, response, payload)

    assert not response.exists()
    assert "prompt_ref" in json.loads(request.read_text(encoding="utf-8"))
    assert llm_handshake.read_request(request) == payload
    # An assignment envelope carries no prompt and lands as it is.
    llm_handshake.publish_operation_request(request, response, {"op": 1})
    assert json.loads(request.read_text(encoding="utf-8")) == {
        "kind": "assignment_operation", "envelope": {"op": 1}}


def test_the_run_archive_carries_the_shared_text(tmp_path):
    from library.tools.versions import runs

    path = _requests_dir(tmp_path) / "plan_cut.json"
    payload = _request(tmp_path)
    llm_handshake.write_request_file(path, payload)

    archive = runs.archive_previous_run(str(tmp_path), "run-1")
    archived = archive / "llm_requests" / "plan_cut.json"
    shutil.rmtree(llm_handshake.shared_dir(path.parent))

    assert llm_handshake.read_request(archived) == payload


def test_ren_asks_skips_answered_and_reports_unreadable(tmp_path):
    requests = _requests_dir(tmp_path)
    llm_handshake.write_request_file(requests / "a.json", _request(tmp_path))
    llm_handshake.write_request_file(requests / "b.json", _request(tmp_path))
    (requests / "c.json").write_text("not json", encoding="utf-8")
    responses = tmp_path / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True)
    (responses / "b.json").write_text("{}", encoding="utf-8")

    report = pending_asks.collect(tmp_path)

    assert [ask["id"] for ask in report["asks"]] == ["a"]
    assert [bad["id"] for bad in report["unreadable"]] == ["c"]
    text = pending_asks.render_text(report)
    assert "Decide the cut." in text and "Brand: red." in text
    assert "--- ask c UNREADABLE" in text
