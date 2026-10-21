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

import ast
import json
from pathlib import Path

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


# ── the fixture provisions the media it places ──────────────────────
#
# The live lane places the qualification clips BY NAME, so a pool that
# does not hold them refuses every case at setup: "no clip named
# 'qual_a.mp4' under the pool root" (measured 2026-10-21: nine of the
# ten AXI-11 rows failed there). The fixture owns its own media, so it
# imports what the pool is missing - through the AXI's ingest verb, and
# without replacing anything the pool already holds.

_MEDIA = {"qual_a.mp4": "/tmp/qual-media/qual_a.mp4",
          "qual_b.mp4": "/tmp/qual-media/qual_b.mp4",
          "qual_c.mp4": "/tmp/qual-media/qual_c.mp4"}


def _stamped_script() -> str:
    """The broker script with every placeholder stamped, as the runner
    stamps it."""
    script = adapter._BROKER_SCRIPT
    script = script.replace("@AXC_TASK@", "all")
    script = script.replace("@AXC_TIMELINE@",
                            "fm-scratch qualify 20261021T000000Z-ab12")
    script = script.replace("@FM_QUAL_PROJECT_DIR@", "/tmp/fm-qual-92-test")
    script = script.replace("@AXC_BRIEF@", "brief")
    script = script.replace("@AXC_ASSIGNMENT@", "{}")
    script = script.replace("@AXC_CAPTION_ONE@", "/tmp/one.mov")
    script = script.replace("@AXC_CAPTION_TWO@", "/tmp/two.mov")
    script = script.replace("@AXC_MEDIA_PATHS@", json.dumps(_MEDIA))
    return script


def _script_functions(script: str, *names: str) -> dict:
    """The named functions of the stamped script, ready to run."""
    tree = ast.parse(script)
    wanted = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in wanted} == set(names), (
        f"the script no longer defines {names}")
    namespace: dict = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]),  # noqa: S102
                 "<fixture script>", "exec"), namespace)
    return namespace


class _Item:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class _Pool:
    """A media pool: the root folder, and the folders filed under it."""

    def __init__(self, names=(), folders=()):
        self.names = list(names)
        self.folders = list(folders)

    def GetRootFolder(self):
        return self

    def GetCurrentFolder(self):
        return self

    def GetClipList(self):
        return [_Item(name) for name in self.names]

    def GetSubFolderList(self):
        return self.folders


class _Project:
    def __init__(self, pool):
        self.pool = pool

    def GetMediaPool(self):
        return self.pool


class _Manager:
    def __init__(self, pool):
        self.project = _Project(pool)

    def GetCurrentProject(self):
        return self.project


def _ingest_paths(argv) -> list:
    """The paths an ingest argv names: the ones before the first flag."""
    paths = []
    for part in argv[1:]:
        if str(part).startswith("--"):
            break
        paths.append(part)
    return paths


def _fixture_namespace(pool) -> tuple[dict, list]:
    """The script's provisioning functions, with the pool and the AXI
    call faked out. The returned list is every argv the fixture issued.
    """
    issued: list = []

    def call(argv):
        issued.append(list(argv))
        if argv and argv[0] == "ingest":
            for path in argv[1:]:
                if str(path).startswith("--"):
                    break
                pool.names.append(Path(str(path)).name)
        return {"argv": argv, "exit_code": 0, "stdout": "", "stderr": ""}

    namespace = _script_functions(_stamped_script(), "ingest_media",
                                   "pool_item_names", "ensure_pool_media")
    namespace["call"] = call
    namespace["manager"] = _Manager(pool)
    namespace["PROJECT"] = "Ren Qualification"
    namespace["FIXTURE_MEDIA"] = dict(_MEDIA)
    return namespace, issued


def test_the_fixture_provisions_the_media_it_places():
    """Defect: the fixture placed the qualification clips by name
    without ever putting them in the pool, so every case failed at
    setup."""
    pool = _Pool()
    namespace, issued = _fixture_namespace(pool)
    namespace["ensure_pool_media"]()
    assert pool.names == ["qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]
    assert len(issued) == 1
    argv = issued[0]
    assert argv[0] == "ingest", (
        "the fixture imported its media outside the AXI's guarded path")
    assert "--apply" in argv
    assert argv[argv.index("--project") + 1] == "Ren Qualification"
    assert _ingest_paths(argv) == [
        "/tmp/qual-media/qual_a.mp4", "/tmp/qual-media/qual_b.mp4",
        "/tmp/qual-media/qual_c.mp4"]


def test_the_fixture_never_replaces_a_pool_item_that_exists():
    """Defect: an import that replaces an existing qualification clip
    would swap the captain's own file for a synthetic one."""
    pool = _Pool(names=["qual_a.mp4"])
    namespace, issued = _fixture_namespace(pool)
    namespace["ensure_pool_media"]()
    assert _ingest_paths(issued[0]) == [
        "/tmp/qual-media/qual_b.mp4", "/tmp/qual-media/qual_c.mp4"]
    assert pool.names == ["qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]


def test_the_fixture_refuses_when_the_import_did_not_land():
    """Defect: an import that Resolve did not really make leaves the
    pool without the clip, and the fixture would place by a name that
    is not there."""
    pool = _Pool()
    namespace, _issued = _fixture_namespace(pool)
    namespace["call"] = lambda argv: {
        "argv": argv, "exit_code": 0, "stdout": "", "stderr": ""}
    try:
        namespace["ensure_pool_media"]()
    except RuntimeError as exc:
        assert "still absent" in str(exc)
    else:
        raise AssertionError(
            "the fixture reported a media import that never landed")


def test_a_pool_item_under_another_name_is_not_the_clip():
    """Defect: `edit place --clip qual_a.mp4` refuses anything that is
    not named exactly that, so a pool item called `qual_a` is as absent
    as no item at all - and a check that compared file stems would call
    the fixture present and import nothing."""
    pool = _Pool(names=["qual_a"])
    namespace, issued = _fixture_namespace(pool)
    namespace["ensure_pool_media"]()
    assert _ingest_paths(issued[0]) == [
        "/tmp/qual-media/qual_a.mp4", "/tmp/qual-media/qual_b.mp4",
        "/tmp/qual-media/qual_c.mp4"]


def test_a_clip_filed_in_a_bin_is_present():
    """Defect: the pool is a TREE, and a clip filed in a bin is still a
    clip the lane can place - a check that read only the root would
    import a second copy of it."""
    pool = _Pool(folders=[_Pool(names=["qual_a.mp4"])])
    namespace, issued = _fixture_namespace(pool)
    namespace["ensure_pool_media"]()
    assert _ingest_paths(issued[0]) == [
        "/tmp/qual-media/qual_b.mp4", "/tmp/qual-media/qual_c.mp4"]


def test_the_seeding_places_the_media_only_after_provisioning_it():
    """Defect: the fixture places `qual_a.mp4` by name, so the pool has
    to hold it before the first placement."""
    tree = ast.parse(_stamped_script())
    seed = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "seed_base")
    calls = [node for node in ast.walk(seed)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "ensure_pool_media"]
    assert calls, (
        "the fixture seeds the qualification clips without provisioning "
        "the media it places")


def test_the_multicam_case_provisions_the_media_it_places():
    """Defect: the multicam case seeds the clips itself, so it needs the
    provisioning too - it was the ninth AXI-11 row to fail at setup."""
    tree = ast.parse(_stamped_script())
    block = next(node for node in tree.body
                 if isinstance(node, ast.If)
                 and "right_speaker_camera" in ast.dump(node.test))
    calls = [node for node in ast.walk(block)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "ensure_pool_media"]
    assert calls, (
        "the multicam case places the qualification clips without "
        "provisioning the media first")


def test_the_media_the_fixture_imports_is_the_qualification_recipes_own(
        monkeypatch):
    """Defect: the fixture's media must be the synthetic qualification
    clips and the deterministic still fixtures, never anything the
    captain's own project holds."""
    from library.tools import qualification_project

    monkeypatch.setattr(
        qualification_project, "ensure_media",
        lambda: {name: Path(f"/tmp/qual-media/{name}.mp4")
                 for name, *_ in qualification_project.MEDIA})
    monkeypatch.setattr(
        q, "ensure_still_fixtures",
        lambda: {"fm-qual-logo-180x64.png": "/tmp/fm-qual-logo-180x64.png"})
    media = adapter._fixture_media_paths()
    assert media == {
        "qual_a.mp4": "/tmp/qual-media/qual_a.mp4",
        "qual_b.mp4": "/tmp/qual-media/qual_b.mp4",
        "qual_c.mp4": "/tmp/qual-media/qual_c.mp4",
        "fm-qual-logo-180x64.png": "/tmp/fm-qual-logo-180x64.png"}


def test_the_provisioning_lives_in_the_fixture_and_not_in_the_engine():
    """Defect: this script is executed by the broker against the
    INSTALLED engine, so a fixture that reached into the engine for its
    own media only worked once the installed engine caught up with it
    (measured: the AXI-11 rows failed with `module
    'library.tools.qualification_project' has no attribute
    'provision_pool_media'` while the engine served the lane)."""
    tree = ast.parse(_stamped_script())
    provisioning = [node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name in ("ensure_pool_media",
                                      "pool_item_names", "ingest_media")]
    assert len(provisioning) == 3
    for node in provisioning:
        for imported in ast.walk(node):
            if isinstance(imported, ast.ImportFrom):
                assert imported.module != "library.tools", (
                    f"{node.name} reaches into the engine; the fixture must "
                    f"provision its own media")
            if isinstance(imported, ast.Import):
                for alias in imported.names:
                    assert not alias.name.startswith("library."), (
                        f"{node.name} reaches into the engine; the fixture "
                        f"must provision its own media")
