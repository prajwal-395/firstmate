"""Audit gap G8: `ren project-model project` plans onto headless engines.

The verb reaches both front doors (`ren/commands.py` AND
`native/ren-cli/src/commands.rs` - a verb the Python list knows but the
native list does not is unreachable), plans and prints by default, and
executes only with `--execute`. The Rust qualification summary
advertises `pm-e2e` exactly as the Python registry does.

The FilmCraft half carries no adapter logic of its own: it plans
through the pinned Rust adapter on `ren_native`, so the tests below
prove the verb's plan EQUALS what the adapter itself returns for the
same model (placements, losses, per-edit verdicts and refusal codes),
rather than pinning a second copy of those numbers. Tests needing the
extension skip naming the build when this tree has none, the way the
ren-core parity harness does.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.project_model_cli import main  # noqa: E402

FIXTURE_MODEL = REPO / "tests/fixtures/project_model_e2e/expected-project-model.json"

FILMCRAFT_FUNCTIONS = (
    "filmcraft_plan_projection",
    "filmcraft_plan_ren_edit",
    "filmcraft_describe_operation",
    "filmcraft_reconcile_clip_ids",
    "filmcraft_verify_media",
    "filmcraft_import_operation",
    "filmcraft_placement_operations",
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Test Show"\nslug: show\n', encoding="utf-8")
    return root


def _model(tmp_path: Path, mutate=None) -> Path:
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    if mutate:
        mutate(model)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    return path


def _native_or_skip():
    """This tree's ren_native build with the FilmCraft adapter, or skip
    naming the build (parity-harness pattern)."""
    from library.tools import ren_native_cache

    module = ren_native_cache.select(REPO)
    if module is not None and all(hasattr(module, name) for name in FILMCRAFT_FUNCTIONS):
        return module
    pytest.skip(
        "the ren_native extension is not built - run "
        "scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>")


def _no_engine(monkeypatch) -> None:
    """Fail the test if the plan path ever touches a subprocess."""
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError(f"plan executed a subprocess: {args[0]!r}")

    monkeypatch.setattr(subprocess, "run", boom)


# ── Registration ────────────────────────────────────────────


def test_the_project_model_verb_is_registered():
    from ren.commands import VERBS

    by_name = {verb.name: verb for verb in VERBS}
    assert "project-model" in by_name, "ren lost its 'project-model' verb"
    assert by_name["project-model"].module_argv == (
        "library.tools.project_model_cli",)


def test_the_project_model_verb_is_registered_in_both_front_doors():
    """A verb the Python list knows but the native list does not is
    unreachable - the native front door refuses it before exec'ing
    the implementation."""
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "project-model"' in native


def test_the_rust_qualification_summary_advertises_pm_e2e():
    """`commands.rs` omitted `pm-e2e` while Python advertised it; the
    two summaries must agree verbatim."""
    from ren.commands import VERBS

    python_summary = next(
        verb.summary for verb in VERBS if verb.name == "qualification")
    assert "pm-e2e" in python_summary
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    line = next(line for line in native.splitlines()
                if 'name: "qualification"' in line)
    assert "pm-e2e" in line, (
        "native qualification summary still omits pm-e2e")


def test_unknown_project_refuses_like_other_verbs(tmp_path):
    """Outside a project root the verb exits 2, as `ren work-state`
    does for an unknown slug."""
    with pytest.raises(SystemExit) as exit_info:
        main(["project", "no-such-slug-xyz", "--engine", "filmcraft",
              "--out", str(tmp_path / "plan.json")])
    assert exit_info.value.code == 2


# ── The verb's plan is the adapter's plan ───────────────────


def test_filmcraft_plan_equals_the_adapter_projection(
        tmp_path, monkeypatch, capsys):
    """Placements, losses, clocks and versions come back from
    `plan_projection` itself: the verb adds the per-edit rows and the
    file/stdout shell, nothing else. No subprocess runs."""
    native = _native_or_skip()
    _no_engine(monkeypatch)
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    expected = native.filmcraft_plan_projection(model)

    root = _project(tmp_path)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["executed"] is False
    for key in ("adapter_version", "engine_revision", "timeline_clock",
                "imports", "placements", "losses"):
        assert plan[key] == expected[key], key
    assert not list(tmp_path.glob("*.fcproj"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["placements"] == len(expected["placements"])
    assert printed["executed"] is False


def test_filmcraft_edit_rows_match_the_adapter_verdicts(tmp_path, monkeypatch):
    """Each row's executable/code is the adapter's own translation
    verdict on that edit (planned with placeholder ids), including a
    stepped ramp and a plan-level record."""

    def add_unexecutable(model):
        model["edits"].append({
            "id": "edit-ramp", "operation": "retime.segments",
            "target_ids": ["use-ada"], "before": None,
            "after": {"segments": []}, "intent_id": None,
            "evidence_ids": [], "author": "test",
            "parent_revision": None})
        model["edits"].append({
            "id": "edit-mix", "operation": "audio_mix",
            "target_ids": [], "before": None, "after": {},
            "intent_id": None, "evidence_ids": [], "author": "test",
            "parent_revision": None})

    native = _native_or_skip()
    _no_engine(monkeypatch)
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    add_unexecutable(model)
    projection = native.filmcraft_plan_projection(model)
    dummy_ids = {p["clip_use_id"]: [0] for p in projection["placements"]}
    expected_rows = []
    for edit in model["edits"]:
        try:
            native.filmcraft_plan_ren_edit(model, projection, edit, dummy_ids, "")
            expected_rows.append({"id": edit["id"], "operation": edit["operation"],
                                  "executable": True})
        except Exception as exc:  # noqa: BLE001 - the adapter's code is the verdict
            text = str(exc)
            code = text.split("(")[1].split(")")[0] if "(" in text else "adapter_refused"
            expected_rows.append({"id": edit["id"], "operation": edit["operation"],
                                  "executable": False, "code": code})

    root = _project(tmp_path)
    model_path = _model(tmp_path, add_unexecutable)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["edits"] == expected_rows
    ramp = next(row for row in expected_rows if row["id"] == "edit-ramp")
    assert ramp == {"id": "edit-ramp", "operation": "retime.segments",
                    "executable": False,
                    "code": "retime_segments_not_supported"}


def test_filmcraft_refusal_carries_the_adapter_code(tmp_path, monkeypatch, capsys):
    """An overlapping timeline refuses with the adapter's own code -
    the verb quotes it, it never invents one."""

    def overlap(model):
        for use in model["clip_uses"]:
            if use["id"] == "use-ben":
                use["timeline_interval"]["start"] = 30

    _native_or_skip()
    _no_engine(monkeypatch)
    root = _project(tmp_path)
    model_path = _model(tmp_path, overlap)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out)]) == 4
    assert not out.exists()
    assert "timeline_overlap" in capsys.readouterr().err


def test_effectcraft_plan_projects_the_request_without_executing(
        tmp_path, monkeypatch, capsys):
    _no_engine(monkeypatch)
    root = _project(tmp_path)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "effectcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["executed"] is False
    assert plan["engine_revision"] == "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
    kinds = [op["op"] for op in plan["request"]["operations"]]
    assert kinds[0] == "new_comp"
    assert "add_mask" in kinds and "apply_effect" in kinds
    assert not list(tmp_path.glob("*.ecproj"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["operations"] == len(kinds)


# ── Fail-closed execution ────────────────────────────────


def test_ramp_execute_refuses_before_mutation(tmp_path, monkeypatch):
    """A stepped ramp plans (named loss) but --execute refuses with no
    engine project created - nothing is half-built."""

    def add_ramp(model):
        model["edits"].append({
            "id": "edit-ramp", "operation": "retime.segments",
            "target_ids": ["use-ada"], "before": None,
            "after": {"segments": []}, "intent_id": None,
            "evidence_ids": [], "author": "test",
            "parent_revision": None})

    _native_or_skip()
    monkeypatch.setattr("library.tools.project_model_cli._filmcraft_cli",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("no engine contact on refusal")))
    root = _project(tmp_path)
    model_path = _model(tmp_path, add_ramp)
    exec_out = tmp_path / "exec.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(exec_out),
                 "--execute"]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
    assert not exec_out.exists()


def test_execute_needs_the_pinned_cli(tmp_path, monkeypatch):
    """A model with no edits still refuses when the pinned CLI is
    absent, before creating anything."""

    def drop_edits(model):
        model["edits"] = []

    _native_or_skip()
    root = _project(tmp_path)
    model_path = _model(tmp_path, drop_edits)
    monkeypatch.setenv("REN_FILMCRAFT_CLI", str(tmp_path / "no-cli"))
    out = tmp_path / "exec.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute"]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
