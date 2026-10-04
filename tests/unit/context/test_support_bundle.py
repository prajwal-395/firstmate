"""P1 production support bundle + logging/error hardening.

Defects named here:

* `pipeline_log.jsonl` was append-only across every run with NO run id
  (docs/RULE_EVIDENCE.md: "50 `step_end` events" no run could own),
  no project id, no build id, and unbounded growth. A write failure
  printed a warning but left no machine-readable count or return value.
* `RenRefusal` carried what/why/fix prose and no machine-readable
  code, so machines parsed sentences.
* There was no `ren support-bundle`: diagnosing an install or runtime
  failure meant asking the person for files by hand, with no redaction
  story for the secrets and home paths inside them.
"""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import pipeline_logger
from library.tools.pipeline_logger import PipelineLogger
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
from ren import support_bundle


@pytest.fixture
def isolated_singleton(monkeypatch):
    """The process-wide logger, saved and restored: these tests bind
    it to throwaway projects."""
    monkeypatch.setattr(pipeline_logger, "_logger_instance", None)
    yield
    monkeypatch.setattr(pipeline_logger, "_logger_instance", None)


def _entries(log_path):
    return [json.loads(line) for line in
            log_path.read_text(encoding="utf-8").strip().splitlines()]


# ── Correlation ids ───────────────────────────────────────────────


def test_entries_carry_run_project_and_build_ids(tmp_path,
                                                 isolated_singleton):
    """An entry names the run, the project and the build that wrote
    it; one written before the run exists carries an honest null."""
    project = tmp_path / "my-project"
    log = (project / "pipeline_output" / "logs" / "pipeline_log.jsonl")

    logger = pipeline_logger.get_logger(str(project))
    assert logger.log(step_id="s", event_type="step_end") is True
    assert _entries(log)[0]["run_id"] is None

    pipeline_logger.get_logger(str(project), run_id="20261004T120000-1")
    assert logger.log(step_id="s", event_type="step_end") is True

    first, second = _entries(log)
    assert second["run_id"] == "20261004T120000-1"
    assert second["project_id"] == "my-project"
    assert second["ren_build"], "the build id must never be empty"
    assert first["ren_build"] == second["ren_build"]
    assert set(second) >= {"timestamp", "run_id", "project_id",
                           "ren_build", "step_id", "event_type", "code"}


def test_get_logger_rebinds_on_project_switch(tmp_path,
                                              isolated_singleton):
    """A singleton that kept serving the previous project would
    misattribute every line it wrote after the switch."""
    first = pipeline_logger.get_logger(str(tmp_path / "a"), run_id="r-a")
    second = pipeline_logger.get_logger(str(tmp_path / "b"))
    assert second is not first
    assert second.project_id == "b"
    assert second.run_id is None, "a new project starts with no run bound"
    assert pipeline_logger.get_logger() is second


def test_step_timer_records_the_refusal_code(tmp_path, isolated_singleton):
    """A refusal passing through a timed step lands in the log WITH its
    code; a plain bug keeps a null one."""
    log = (tmp_path / "p" / "pipeline_output" / "logs"
           / "pipeline_log.jsonl")
    pipeline_logger.get_logger(str(tmp_path / "p"), run_id="r-9")

    @pipeline_logger.step_timer(step_id_kwarg="node_id")
    def refused(node_id):
        raise RenRefusal("no reel 9", "never invented", "propose first")

    @pipeline_logger.step_timer(step_id_kwarg="node_id")
    def buggy(node_id):
        raise RuntimeError("a real bug")

    with pytest.raises(RenRefusal):
        refused("touch_reel")
    with pytest.raises(RuntimeError):
        buggy("touch_reel")

    entries = _entries(log)
    refused_entry = next(e for e in entries if "no reel 9" in e["error"])
    buggy_entry = next(e for e in entries if e["error"] == "a real bug")
    assert refused_entry["code"] == "REN_REFUSAL"
    assert buggy_entry["code"] is None


# ── Rotation ──────────────────────────────────────────────────────


def test_log_rotates_past_the_cap(tmp_path, isolated_singleton,
                                  monkeypatch):
    """An unbounded append across every run since the project was
    created is a disk leak with a project's name on it."""
    monkeypatch.setattr(pipeline_logger, "LOG_ROTATE_BYTES", 300)
    monkeypatch.setattr(pipeline_logger, "LOG_ROTATE_KEEP", 2)
    logs = tmp_path / "p" / "pipeline_output" / "logs"
    logger = PipelineLogger(str(tmp_path / "p"), run_id="r-rot")

    for n in range(6):
        assert logger.log(step_id=f"step_{n}", event_type="step_end",
                          detail={"pad": "x" * 120}) is True

    current = logs / "pipeline_log.jsonl"
    assert (logs / "pipeline_log.jsonl.1").is_file()
    assert not (logs / "pipeline_log.jsonl.3").is_file(), \
        "backups past the keep count must not accumulate"
    for backup in (current, logs / "pipeline_log.jsonl.1",
                   logs / "pipeline_log.jsonl.2"):
        if backup.is_file():
            for entry in _entries(backup):
                assert entry["run_id"] == "r-rot", \
                    "rotation must not lose the correlation ids"


# ── Failure accounting ────────────────────────────────────────────


def test_logging_failure_is_reported_and_counted(tmp_path, capsys,
                                                isolated_singleton,
                                                monkeypatch):
    """The old path printed a warning and kept nothing else: no count,
    no return, no record of how many lines were lost."""
    from library.tools.project_layout import ProjectLayout

    def gone(self, *parts, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(ProjectLayout, "write_path", gone)
    logger = PipelineLogger(str(tmp_path / "p"))

    assert logger.log(step_id="s", event_type="step_end") is False
    assert logger.log(step_id="s", event_type="step_end") is False
    assert logger.write_failures == 2
    assert "disk gone" in logger.last_write_error

    err = capsys.readouterr().err
    assert "[LOG] step_end s" in err, "the event still reaches stderr"
    assert "failure 2" in err, "the warning counts, not just fires"


# ── Refusal codes ─────────────────────────────────────────────────


def test_refusal_codes_default_from_the_class_name():
    """Every existing subclass is coded without touching its file."""
    assert RenRefusal("w", "y", "f").code == "REN_REFUSAL"

    from library.tools.plan_splice import SpliceRefused
    assert SpliceRefused("w", "y", "f").code == "REN_SPLICE_REFUSED"


def test_refusal_code_explicit_wins_and_to_dict_carries_all_four():
    class Pinned(RenRefusal):
        CODE = "REN_STABLE_ACROSS_RENAMES"

    explicit = RenRefusal("w", "y", "f", code="REN_ONE_OFF")
    assert explicit.code == "REN_ONE_OFF"
    assert Pinned("w", "y", "f").code == "REN_STABLE_ACROSS_RENAMES"
    assert Pinned("w", "y", "f", code="REN_X").code == "REN_X"

    assert explicit.to_dict() == {
        "code": "REN_ONE_OFF", "what": "w", "why": "y", "fix": "f"}


def test_render_shape_is_unchanged():
    """The human three lines stay the pinned shape; the code travels
    in `to_dict`, never in prose a machine would parse."""
    refused = RenRefusal("the plan names no reel 9",
                         "a touchup never invents one",
                         "run `ren propose <project>` first")
    assert refused.render() == (
        "ren: refused - the plan names no reel 9\n"
        "  why: a touchup never invents one\n"
        "  fix: run `ren propose <project>` first")
    assert "REN_" not in refused.render()


# ── The bundle ────────────────────────────────────────────────────

BUNDLE_MEMBERS = {"ren.json", "config.json", "doctor.json",
                  "packages.json", "models.json", "errors.jsonl",
                  "manifests.json"}


def _stub_doctor(monkeypatch, cache_root):
    """The real doctor probes Resolve and the interpreters; the bundle
    tests pin what is collected, not the checks themselves."""
    from ren import doctor as ren_doctor

    checks = [ren_doctor.Check("stub", True, "stubbed", need="")]
    monkeypatch.setattr(ren_doctor, "run_checks",
                        lambda probe=None, needs=None: checks)
    monkeypatch.setattr(ren_doctor, "required_failures", lambda c: [])
    monkeypatch.setattr(ren_doctor, "capability_report", lambda c: {})
    monkeypatch.setattr(ren_doctor, "resolve_interpreter",
                        lambda: (sys.executable, ""))
    monkeypatch.setattr(ren_doctor, "hf_hub_cache", lambda: cache_root)


def _make_project(root: Path) -> Path:
    (root / "pipeline_output" / "logs").mkdir(parents=True)
    (root / "project.yaml").write_text("project_name: demo\n",
                                       encoding="utf-8")
    (root / "pipeline_run.json").write_text(
        '{"mode": "full run", "current_step": "mesh_spine"}',
        encoding="utf-8")
    return root


def _read_bundle(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == BUNDLE_MEMBERS
        return {name: bundle.read(name).decode("utf-8")
                for name in bundle.namelist()}


def test_bundle_collects_the_allowlist_and_nothing_else(tmp_path,
                                                        monkeypatch):
    """Footage, transcripts and prompts are never read, so they can
    never be shipped: the member set is fixed, whatever the project
    holds."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    project = _make_project(tmp_path / "demo")
    footage = project / "raw" / "clip.mp4"
    footage.parent.mkdir()
    footage.write_bytes(b"\x00" * 16)
    (project / "pipeline_output" / "llm_requests").mkdir()
    (project / "pipeline_output" / "llm_requests" / "p.txt").write_text(
        "the prompt", encoding="utf-8")
    from library.tools.project_layout import Area, ProjectLayout
    manifest_path = ProjectLayout(project).write_path(
        Area.ASSEMBLY_MANIFEST, "assembly_manifest.json")
    manifest_path.write_text(
        json.dumps({"speech": [{"caption": "private transcript",
                                "clip_id": "clip-1"}]}),
        encoding="utf-8")

    out = tmp_path / "out"
    archive, names = support_bundle.build_bundle(str(project), out, 200)

    assert set(names) == BUNDLE_MEMBERS
    members = _read_bundle(archive)
    whole = "\n".join(members.values())
    assert "clip.mp4" not in whole and "the prompt" not in whole
    assert "private transcript" not in whole
    manifests = json.loads(members["manifests.json"])
    assert manifests["pipeline_run.json"]["mode"] == "full run"
    assert json.loads(members["ren.json"])["project"] == str(project)


def test_bundle_redacts_secrets_home_and_users(tmp_path, monkeypatch):
    """Tokens, the home directory and other users' names fold before
    anything reaches the archive."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    users_root = Path(os.sep) / "Users"
    examined_home = users_root / "examined"
    other_home = users_root / "other"
    monkeypatch.setenv("HOME", str(examined_home))
    project = _make_project(tmp_path / "demo")
    (project / "pipeline_output" / "logs" / "pipeline_log.jsonl").write_text(
        json.dumps({"event_type": "step_error", "step_id": "s",
                    "error": "fetch with hf_abcDEF1234567890 from "
                             f"{examined_home / 'secrets'} and "
                             f"{other_home / 'x'} Authorization: "
                             "Bearer secret-bearer-token",
                    "code": "REN_X"}) + "\n", encoding="utf-8")

    archive, _ = support_bundle.build_bundle(
        str(project), tmp_path / "out", 200)
    errors = _read_bundle(archive)["errors.jsonl"]

    assert "hf_abcDEF1234567890" not in errors
    assert "secret-bearer-token" not in errors
    assert str(examined_home) not in errors and str(other_home) not in errors
    assert "<redacted>" in errors
    assert str(users_root / "<user>") in errors
    assert "~" in errors


def test_bundle_error_tail_is_newest_errors_only(tmp_path, monkeypatch):
    """Non-error events never ride along, and the tail keeps the
    newest errors, newest last."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    project = _make_project(tmp_path / "demo")
    lines = []
    for n in range(5):
        lines.append({"event_type": "step_error", "step_id": "s",
                      "error": f"failure {n}"})
        lines.append({"event_type": "step_end", "step_id": "s"})
    lines.extend({"event_type": "step_end", "step_id": "s"}
                 for _ in range(20))
    (project / "pipeline_output" / "logs" / "pipeline_log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")

    archive, _ = support_bundle.build_bundle(str(project),
                                             tmp_path / "out", 2)
    events = [json.loads(line) for line in
              _read_bundle(archive)["errors.jsonl"].splitlines()]
    events = [event for event in events if "error" in event]

    assert [e["error"] for e in events] == ["failure 3", "failure 4"]


def test_bundle_errors_drop_unbounded_detail_payloads(tmp_path, monkeypatch):
    """An error event's arbitrary detail can carry prompts or source
    text, so support exports include only the structured error fields."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    project = _make_project(tmp_path / "demo")
    (project / "pipeline_output" / "logs" / "pipeline_log.jsonl").write_text(
        json.dumps({"event_type": "step_error", "step_id": "s",
                    "error": "bad input", "code": "REN_BAD_INPUT",
                    "detail": {"prompt": "private prompt"}}) + "\n",
        encoding="utf-8")

    archive, _ = support_bundle.build_bundle(
        str(project), tmp_path / "out", 200)
    errors = _read_bundle(archive)["errors.jsonl"]

    assert "bad input" in errors
    assert "REN_BAD_INPUT" in errors
    assert "private prompt" not in errors


def test_bundle_without_a_project_is_machine_only(tmp_path, monkeypatch):
    """An install failure has no project yet; the bundle still
    diagnoses the machine."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    archive, _ = support_bundle.build_bundle(None, tmp_path / "out", 200)
    members = _read_bundle(archive)

    ren = json.loads(members["ren.json"])
    from ren.version import build_info, version_string
    assert ren["ren_build"] == version_string()
    assert ren["build"] == build_info()
    assert ren["project"] is None
    assert json.loads(members["manifests.json"]) == {}
    assert json.loads(members["doctor.json"])["checks"][0]["name"] == "stub"
    first = json.loads(members["errors.jsonl"].splitlines()[0])
    assert first["_note"] == "no project: machine-only bundle"
    packages = json.loads(members["packages.json"])["groups"]
    from library.tools import dependency_groups
    assert set(packages) == set(dependency_groups.RUNTIME_GROUPS)
    for rows in packages.values():
        assert all(set(row) == {"name", "version"} for row in rows)


def test_bundle_refuses_an_unknown_project_in_shape(tmp_path):
    """No project, no bundle - and the refusal says the next step."""
    with pytest.raises(RenRefusal) as refused:
        support_bundle.build_bundle("no-such-project", tmp_path, 200)
    assert "ren projects" in refused.value.fix
    assert refused.value.code == "REN_REFUSAL"

    from ren import cli as ren_cli
    code = ren_cli.main(["support-bundle", "no-such-project",
                         "--out", str(tmp_path)])
    assert code == REFUSAL_EXIT_CODE


def test_doctor_section_matches_doctor_json_shape(monkeypatch, tmp_path):
    """The bundle's doctor section is the `--json` report, not a
    second rendering of it."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    section = support_bundle.doctor_section()
    assert set(section) == {
        "ren", "checks", "required_failures", "capabilities"}
    from ren.version import build_info
    assert section["ren"]["version"] == build_info()["version"]
    assert section["ren"]["channel"] == build_info()["channel"]


def test_models_section_reports_cached_huggingface_revision(
        monkeypatch, tmp_path):
    """A report names the model revision already cached, without opening
    its weights or walking model contents."""
    _stub_doctor(monkeypatch, tmp_path / "hf-cache")
    repo = (tmp_path / "hf-cache" /
            "models--mlx-community--gemma-4-12b-it-4bit")
    revision = "a" * 40
    (repo / "snapshots" / revision).mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(revision, encoding="utf-8")

    models = support_bundle.models_section()
    gemma = next(row for row in models["huggingface"]
                 if row["id"] == "mlx-community/gemma-4-12b-it-4bit")

    assert gemma["cached"] is True
    assert gemma["revision"] == revision
    assert gemma["cached_revisions"] == [revision]
