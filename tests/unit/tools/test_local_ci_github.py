"""gh-axi's wire shapes: the envelope, the pull object, the merge reply.

Defects caught (both met live on the first install): the wrapper sent
gh-style `--method`/`-f` flags gh-axi does not have (its api usage is
`api [METHOD] <path> --field k=v`), and it json-parsed gh-axi's
`[N]:`+YAML envelope as if it were raw JSON - so the daemon read every
GitHub call as unreachable. No network here: the shapes are captured.
"""

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
                  "labels": ["ren-ci-ready"], "draft": False,
                  "url": "https://github.com/o/r/pull/2082",
                  "title": "fix"}


def test_plain_json_passes_through():
    assert _parse_response('{"merged": true, "sha": "deadbee"}', "merge") == {
        "merged": True, "sha": "deadbee"}


def test_empty_is_an_empty_object():
    assert _parse_response("   \n", "x") == {}
