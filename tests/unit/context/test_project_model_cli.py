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
import shutil
import sys
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")

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


# ── Native treatments on the export path ────────────────────
#
# The PR 2310 composite, wired into the verb: --execute --export
# names the native render, so the treated picture lands AT the
# export path without a manual step. The engine is stubbed (its
# CLI never builds here); the composite runs for real on tiny
# ffmpeg renders, so the pixel assertions are behavioural.

STRONG_LOOK_YAML = """\
name: "Test Show"
slug: show
status: draft
source:
  language: en
style:
  series_look:
    name: strong-grade
    cdl:
      slope: [1.2, 1.2, 1.2]
      offset: [0.1, 0.1, 0.1]
      power: [1.0, 1.0, 1.0]
      saturation: 1.5
    contrast: 0.3
"""


def _look_root(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(STRONG_LOOK_YAML, encoding="utf-8")
    return root


def _stub_native():
    """The FilmCraft adapter surface execute_filmcraft drives."""
    import types

    projection = {
        "adapter_version": "test-1",
        "engine_revision": "test-rev",
        "timeline_clock": "clock-project",
        "imports": [],
        "placements": [],
        "losses": [],
    }
    native = types.SimpleNamespace()
    native.filmcraft_plan_projection = lambda model: dict(projection)
    native.filmcraft_plan_ren_edit = lambda *args: []
    native.filmcraft_describe_operation = lambda op: {
        "command": op["kind"],
        "params": ({"path": op["path"]} if op.get("path") else {}),
        "save": False}
    native.filmcraft_verify_media = lambda _projection, _media_dir: {}
    native.filmcraft_import_operation = lambda *args: {"kind": "import"}
    native.filmcraft_placement_operations = lambda *args: []
    native.filmcraft_reconcile_clip_ids = lambda *args: {}
    return native


def _stub_engine(monkeypatch, tmp_path: Path) -> None:
    """Stub the pinned CLI; the export renders a tiny mp4 for real."""
    import subprocess

    import library.tools.project_model_cli as pmc

    def fake_fc_exec(cli, project, command, params, *,
                     save=False, save_as=None):
        if save_as is not None:
            Path(save_as).write_bytes(b"fake-project")
            return {}
        if command == "import":
            return {"items": [], "errors": []}
        if command == "sequence.inspect":
            return {}
        if command == "export":
            out = Path(params["path"])
            done = subprocess.run(
                ["ffmpeg", "-v", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc=size=64x112:rate=24:duration=0.3333",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(out)],
                capture_output=True, text=True, encoding="utf-8",
                check=False)
            assert done.returncode == 0, done.stderr[:300]
            (tmp_path / "engine-base.mp4").write_bytes(out.read_bytes())
            return {"exported": str(out)}
        return {}

    monkeypatch.setattr(pmc, "filmcraft_native", lambda: _stub_native())
    monkeypatch.setattr(pmc, "_filmcraft_cli",
                        lambda: tmp_path / "fake-cli")
    monkeypatch.setattr(pmc, "_check_cli_version",
                        lambda cli, expect, engine: None)
    monkeypatch.setattr(pmc, "_fc_exec", fake_fc_exec)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_export_composites_the_declared_grade(tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    root = _look_root(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    treatments = receipt["native_treatments"]
    assert treatments["grade_applied"] is True
    assert treatments["bezel_applied"] is False
    assert treatments["grade_name"] == "strong-grade"
    assert treatments["out"] == str(export)
    assert treatments["refused"] == []
    assert export.is_file()
    assert not list(tmp_path.glob("*.treatments-tmp.mp4"))
    base = tmp_path / "engine-base.mp4"
    scores = nec.measure_ssim(base, export, frames=8)
    assert scores["Y"] < 0.99  # the export carries the declared grade


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_export_leaves_engine_bytes_alone_without_declarations(
        tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(
        nec, "composite_declared_treatments",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no declarations means no composite call")))
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    treatments = receipt["native_treatments"]
    assert treatments["grade_applied"] is False
    assert treatments["bezel_applied"] is False
    assert treatments["out"] == str(export)
    assert export.read_bytes() == (tmp_path / "engine-base.mp4").read_bytes()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_without_export_composites_nothing(tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(
        nec, "composite_declared_treatments",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no export means no composite call")))
    root = _look_root(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute"]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["native_treatments"]["skipped"].startswith("no export")


def test_execute_refuses_a_malformed_look_before_mutation(tmp_path, monkeypatch):
    """A series_look that cannot be delivered refuses before the
    engine project exists - the export never renders half-treated."""
    import library.tools.project_model_cli as pmc

    monkeypatch.setattr(pmc, "filmcraft_native", lambda: _stub_native())
    monkeypatch.setattr(
        pmc, "_filmcraft_cli",
        lambda: (_ for _ in ()).throw(
            AssertionError("no engine contact on refusal")))
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(
        STRONG_LOOK_YAML.replace("      saturation: 1.5\n", ""),
        encoding="utf-8")
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
    assert not export.exists()
    assert not out.exists()
