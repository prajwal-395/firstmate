"""gh-axi's wire shapes: the envelope, the pull object, the merge reply.

Defects caught (both met live on the first install): the wrapper sent
gh-style `--method`/`-f` flags gh-axi does not have (its api usage is
`api [METHOD] <path> --field k=v`), and it json-parsed gh-axi's
`[N]:`+YAML envelope as if it were raw JSON - so the daemon read every
GitHub call as unreachable. No network here: the shapes are captured.
"""

import base64
import json
from pathlib import Path

import pytest

from library.tools.local_ci import github
from library.tools.local_ci.github import _parse_response, _shape_pull

PULLS_ENVELOPE = """[2]:
  - number: 2082
    state: open
    title: "fix"
    draft: false
    html_url: "https://github.com/o/r/pull/2082"
    head: {sha: "abc123", ref: "fm/x"}
    base: {ref: "main"}
    labels: [{name: "ren-ci-ready"}]
"""


def test_envelope_parses_to_the_payload_list():
    data = _parse_response(PULLS_ENVELOPE, "pulls")
    assert isinstance(data, list) and len(data) == 2 - 1
    assert data[0]["number"] == 2082


def test_pull_shapes_to_the_integration_fields():
    data = _parse_response(PULLS_ENVELOPE, "pulls")
    pr = _shape_pull(data[0])
    assert pr == {"number": 2082, "head": "abc123", "base": "main",
                  "base_sha": "",
                  "labels": ["ren-ci-ready"], "draft": False,
                  "url": "https://github.com/o/r/pull/2082",
                  "title": "fix", "body": ""}


def test_plain_json_passes_through():
    assert _parse_response('{"merged": true, "sha": "deadbee"}', "merge") == {
        "merged": True, "sha": "deadbee"}


def _projected_pull(number, label):
    return {"number": number, "head": {"sha": f"head-{number}"},
            "base": {"ref": "main"}, "labels": [label],
            "draft": False,
            "html_url": f"https://github.com/o/r/pull/{number}",
            "title": f"change {number}", "body": ""}


def _jq_response(records):
    encoded = [base64.b64encode(json.dumps(record).encode()).decode()
               for record in records]
    body = "\n".join(encoded)
    return ("api_response:\n"
            f"  body: {json.dumps(body)}\n"
            "  truncated: false\n")


def test_jq_envelope_unwraps_the_full_body():
    assert _parse_response(
        'api_response:\n  body: "{\\"ok\\":true}"\n  truncated: false',
        "pulls") == {"ok": True}


def test_list_open_prs_preserves_labels_from_gh_axi(monkeypatch):
    records = [_projected_pull(2083, "ren-ci-ready"),
               _projected_pull(2084, "reviewed")]
    calls = []

    def fake_run(*args):
        calls.append(args)
        return _jq_response(records)

    monkeypatch.setattr(github, "_run", fake_run)
    prs = github.list_open_prs()

    assert [pr["labels"] for pr in prs] == [["ren-ci-ready"], ["reviewed"]]
    assert [pr["number"] for pr in prs] == [2083, 2084]
    assert "--jq" in calls[0] and "@base64" in calls[0][calls[0].index("--jq") + 1]


def test_get_pr_preserves_ready_label_from_gh_axi(monkeypatch):
    record = _projected_pull(2083, "ren-ci-ready")
    monkeypatch.setattr(github, "_run", lambda *args: _jq_response([record]))

    pr = github.get_pr(2083)

    assert pr["head"] == "head-2083"
    assert pr["labels"] == ["ren-ci-ready"]


def test_jq_envelope_rejects_truncated_data():
    with pytest.raises(github.GitHubError, match="was truncated"):
        _parse_response("api_response:\n  body: abc\n  truncated: true", "pulls")


def test_empty_is_an_empty_object():
    assert _parse_response("   \n", "x") == {}


def test_status_target_is_a_browser_link(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "_api",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    target = "https://github.com/o/r/pull/42"
    github.post_status("abc123", "failure", "ren-ci: timed out", target)
    assert calls[0][1]["fields"]["target_url"] == target
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        github.post_status("abc123", "failure", "ren-ci: timed out",
                           str(Path.home() / ".ren-ci/run.log"))


def test_label_changes_use_repo_scoped_rest_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "_api",
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    github.set_label(2083, "ren-ci-ready")
    github.remove_label(2083, "ren-ci-ready")

    assert calls == [
        (("repos/prajwal-395/video_editing_pilot/issues/2083/labels",),
         {"method": "POST", "fields": {"labels[]": "ren-ci-ready"}}),
        ((("repos/prajwal-395/video_editing_pilot/issues/2083/labels/"
           "ren-ci-ready"),), {"method": "DELETE"}),
    ]


def test_label_removal_quotes_the_label_path_component(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "_api",
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    github.remove_label(2083, "ready / reviewed")

    assert calls[0][0][0].endswith("/ready%20%2F%20reviewed")


def test_pull_url_points_at_the_configured_repository():
    assert github.pull_url(42) == (
        "https://github.com/prajwal-395/video_editing_pilot/pull/42")


def test_list_open_prs_requests_full_output(monkeypatch):
    """gh-axi 0.1.37 truncates long lines with `... (truncated)` unless
    `--full` is passed; a truncated base64 line fails to decode and the
    daemon refuses every pass as "github unreachable". The stub below
    truncates exactly like that when `--full` is absent, so a passing
    parse proves the call requested full output.
    """
    records = [_projected_pull(2083, "ren-ci-ready"),
               _projected_pull(2084, "reviewed")]
    encoded = [base64.b64encode(json.dumps(record).encode()).decode()
               for record in records]
    calls = []

    def fake_run(*args):
        calls.append(args)
        if "--full" not in args:
            truncated = encoded[0][:len(encoded[0]) // 2] + "... (truncated)"
            return truncated + "\n" + "\n".join(encoded[1:])
        return "\n".join(encoded)

    monkeypatch.setattr(github, "_run", fake_run)
    prs = github.list_open_prs()

    assert [pr["number"] for pr in prs] == [2083, 2084]
    assert "--full" in calls[0]


def test_truncated_jq_output_without_full_is_unreachable(monkeypatch):
    """The 0.1.37 failure mode, pinned: a truncated line is a loud
    GitHubError, the daemon's "github unreachable" refusal.
    """
    record = _projected_pull(2083, "ren-ci-ready")
    encoded = base64.b64encode(json.dumps(record).encode()).decode()

    def fake_run(*args):
        assert "--full" not in args
        return encoded[:len(encoded) // 2] + "... (truncated)"

    monkeypatch.setattr(github, "_run", fake_run)
    with pytest.raises(github.GitHubError, match="invalid base64 JSON"):
        github._decode_jq_objects(
            github._parse_response(
                github._run("api", "pulls", "--jq", ". | @base64"), "pulls"),
            "pulls")
