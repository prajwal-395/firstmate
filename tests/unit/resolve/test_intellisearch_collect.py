"""IntelliSearch collection: the spec, the name intersection, the file.

The collector turns a query into a foreground ui_transaction spec, runs
it on the serving broker, reads the result names off the pixels, and
files `--external-hits` rows. Every test here runs offline: the broker,
the driver and Resolve are fakes or refusals. A blank query, a missing
box and a name that merely contains a clip name are all refused or
unclaimed by name.
"""

import json
from pathlib import Path

import pytest

from library.tools import intellisearch_collect as collect_mod
from library.tools import resolve_axi
from library.tools.ren_refusal import RenRefusal
from library.tools.resolved.jobs import JobRefused


BOX = (960.0, 540.0)


def _catalog(names):
    return {name: f"clip_{i:03d}" for i, name in enumerate(names)}


# ─── the spec ───────────────────────────────────────────────────────


def test_blank_query_is_refused_before_any_broker_contact(monkeypatch):
    """A call with nothing to ask is not made - refused before the
    broker is even consulted."""
    from library.tools.resolved import client

    def no_broker(*args, **kwargs):
        raise AssertionError("a blank query must not reach the broker")

    monkeypatch.setattr(client, "serving", no_broker)
    monkeypatch.setattr(client, "submit", no_broker)
    with pytest.raises(JobRefused):
        collect_mod.build_collection_spec("   ", BOX)
    with pytest.raises(RenRefusal):
        collect_mod.collect("/no/proj", "  ", BOX)


def test_missing_box_names_the_unmeasured_point():
    """The box is runtime-measured input: nothing here aims at a
    default coordinate."""
    with pytest.raises(JobRefused) as excinfo:
        collect_mod.build_collection_spec("parking lot", None)
    assert "box" in str(excinfo.value)
    with pytest.raises(JobRefused):
        collect_mod.build_collection_spec("parking lot", ("x", 1.0))


def test_spec_carries_the_take_and_the_media_route():
    """Menu path reaches the page in the background; the take reason
    names the query it was announced for."""
    spec = collect_mod.build_collection_spec("parking lot", BOX)
    assert spec["project"] == collect_mod.QUALIFICATION_PROJECT
    assert spec["foreground"]["reason"].startswith(
        "IntelliSearch search for 'parking lot'")
    press = spec["actions"][0]
    assert press["action"] == "press"
    assert press["target"]["path"] == ["Workspace", "Switch to Page",
                                       "Media"]
    kinds = [action["action"] for action in spec["actions"]]
    assert kinds == ["press", "wait", "click", "type", "key", "wait"]
    typed = spec["actions"][3]
    assert typed["target"]["text"] == "parking lot"
    assert spec["actions"][4]["target"]["key"] == "return"
    assert spec["verification"] == {"kind": "api_readback",
                                    "getter": "page", "equals": "media"}


# ─── the name intersection ──────────────────────────────────────────


def test_exact_names_match_stems_never_substrings():
    """The defect this pins: "take1" claimed off "take1 scene", or
    "take10" off "take1". Only exact full names and exact stems answer."""
    names = {"take1.MOV": "clip_001", "take1": "clip_001",
             "take10.MOV": "clip_002", "take10": "clip_002"}
    report = collect_mod.extract_rows(
        [{"text": "take1"}, {"text": "take10"}, {"text": "take1.MOV"},
         {"text": "take1 scene"}, {"text": "TAKE1"}, {"text": ""},
         "not-a-row", None],
        names, "takes")
    assert [(row["clip_id"], row["text"]) for row in report["rows"]] == [
        ("clip_001", "take1"), ("clip_002", "take10")]
    assert report["matched_clips"] == 2
    assert report["seen_strings"] == 5


def test_one_row_per_clip_first_occurrence_wins():
    report = collect_mod.extract_rows(
        [{"text": "take1"}, {"text": "take1.MOV"}, {"text": "take1"}],
        {"take1.MOV": "clip_001"}, "takes")
    assert [row["clip_id"] for row in report["rows"]] == ["clip_001"]
    assert report["rows"][0]["query"] == "takes"
    assert report["rows"][0]["collected_by"] == "resolve_intellisearch_ui"


def test_names_nowhere_on_screen_file_nothing():
    report = collect_mod.extract_rows(
        [{"text": "a street"}, {"text": "credits"}],
        {"take1.MOV": "clip_001"}, "takes")
    assert report["rows"] == []
    assert report["matched_clips"] == 0
    assert report["seen_strings"] == 2


# ─── the run ────────────────────────────────────────────────────────


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pipeline_data.json").write_text(json.dumps({
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "take1.MOV",
             "source_file": "/raw/take1.MOV", "duration_seconds": 10.0},
        ]}},
    }), encoding="utf-8")
    return root


def test_collect_refuses_without_a_serving_broker(tmp_path, monkeypatch):
    """Ren never starts a broker itself - the refusal names the start."""
    from library.tools.resolved import client

    monkeypatch.setattr(client, "serving", lambda *a, **k: False)
    with pytest.raises(RenRefusal) as excinfo:
        collect_mod.collect(str(_project(tmp_path)), "takes", BOX)
    assert "ren resolved serve" in str(excinfo.value)


def test_collect_refuses_an_empty_catalog(tmp_path, monkeypatch):
    """Names to intersect with live in the catalog; an empty one has
    nothing to answer from."""
    from library.tools.resolved import client

    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pipeline_data.json").write_text(json.dumps({}),
                                             encoding="utf-8")
    monkeypatch.setattr(client, "serving", lambda *a, **k: True)
    with pytest.raises(RenRefusal) as excinfo:
        collect_mod.collect(str(root), "takes", BOX)
    assert "search-index" in str(excinfo.value)


def test_collect_writes_rows_and_returns_the_file(tmp_path, monkeypatch):
    """End to end against fakes: broker job, pixel read, intersection,
    file on disk in the merge's shape."""
    from library.tools.resolved import client

    monkeypatch.setattr(client, "serving", lambda *a, **k: True)
    submitted = {}

    def fake_submit(kind, params):
        submitted["kind"] = kind
        submitted["params"] = params
        return {"id": "job-1"}

    monkeypatch.setattr(client, "submit", fake_submit)
    monkeypatch.setattr(client, "result",
                        lambda job_id, wait=0.0: {
                            "state": "done", "result": {}})

    seen = {}

    class _FakeDriver:
        def call(self, method, params=None):
            seen["method"] = method
            return {"text": "take1 Media Pool",
                    "elements": [{"text": "take1",
                                  "frame": {"x": 1, "y": 2}}]}

        def close(self):
            seen["closed"] = True

    import library.tools.computer_use.client as driver_client
    monkeypatch.setattr(driver_client, "DriverClient",
                        lambda *a, **k: _FakeDriver())
    root = _project(tmp_path)
    assert collect_mod._catalog_names(str(root)) == {
        "take1.MOV": "clip_001", "take1": "clip_001"}
    out = tmp_path / "hits.json"
    result = collect_mod.collect(str(root), "takes", BOX, out=str(out))
    assert result["rows"] == 1
    assert result["out"] == str(out)
    assert result["job_id"] == "job-1"
    assert submitted["kind"] == "resolve.ui_transaction"
    assert submitted["params"]["foreground"]["reason"].startswith(
        "IntelliSearch search for")
    assert seen == {"method": "read_text", "closed": True}
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert rows == [{"clip_id": "clip_001", "text": "take1",
                     "query": "takes",
                     "collected_by": "resolve_intellisearch_ui"}]


def test_collect_surfaces_a_failed_job(tmp_path, monkeypatch):
    from library.tools.resolved import client

    monkeypatch.setattr(client, "serving", lambda *a, **k: True)
    monkeypatch.setattr(client, "submit",
                        lambda kind, params: {"id": "job-9"})
    monkeypatch.setattr(client, "result",
                        lambda job_id, wait=0.0: {
                            "state": "failed",
                            "error": "backing off: SCREEN_LOCKED"})
    with pytest.raises(RenRefusal) as excinfo:
        collect_mod.collect(str(_project(tmp_path)), "takes", BOX)
    assert "SCREEN_LOCKED" in str(excinfo.value)


# ─── the verb ───────────────────────────────────────────────────────


def _ns(**kwargs):
    return type("Args", (), kwargs)()


def test_verb_dry_run_prints_the_validated_spec(capsys):
    assert resolve_axi.main(
        ["intellisearch-collect", "--query", "takes", "--box", "960,540",
         "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "intellisearch_collect_plan" in out
    assert "Switch to Page" in out


def test_verb_refuses_a_bad_box(capsys):
    assert resolve_axi.main(
        ["intellisearch-collect", "--query", "takes", "--box", "nowhere",
         "--dry-run"]) == 1
    assert "x,y" in capsys.readouterr().out


def test_verb_survey_reports_reach(monkeypatch, capsys):
    """The background-safe half runs under a serving broker too: it
    never touches the broker, so the broker never sees it."""

    class _FakeDriver:
        def call(self, method, params=None):
            if method == "list_commands":
                return {"commands": [
                    {"path": ["Workspace", "Switch to Page", "Media"]}]}
            if method == "get_accessibility_tree":
                return {"tree": {"role": "AXApplication", "children": [
                    {"role": "AXTextField", "title": None, "label": None,
                     "children": []}]}}
            raise AssertionError(f"survey reads only, not {method}")

        def close(self):
            pass

    import library.tools.computer_use.client as driver_client
    monkeypatch.setattr(driver_client, "DriverClient",
                        lambda *a, **k: _FakeDriver())
    from library.tools.resolved import client
    monkeypatch.setattr(client, "serving", lambda *a, **k: True)
    monkeypatch.setattr(resolve_axi, "_through_broker",
                        lambda *_args: pytest.fail("survey used the broker"))
    assert resolve_axi.main(["intellisearch-collect", "--survey"]) == 0
    out = capsys.readouterr().out
    assert "intellisearch_survey" in out
    assert "menu_commands: 1" in out
