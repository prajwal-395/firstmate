#!/usr/bin/env python3
"""The ML preflight belongs to the commands that need it.

The captain could not open the review dashboard on 2026-08-26 because
`manage_project.py` checked for `mlx_vlm`, `whisperx`, `easyocr` and
`torch` at IMPORT time, before argparse had seen the command.  The
dashboard needs none of them, and the error then told the captain to
run `source .venv/bin/activate` in a checkout that has no `.venv`.

These tests hold the three halves of the fix:

  1. Every command except the ones in ML_DEPENDENT_COMMANDS is served
     with the whole ML stack unimportable, and the dashboard server is
     constructible and serves in that state.
  2. The advice names a path that is really on disk.
  3. A project kept outside PROJECTS_ROOT is openable by path, and a
     slug that is not there says which root was searched and what was
     in it.
"""

import builtins
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_cli():
    """Import manage_project.py under its own name.

    The point of the fix is that this import performs no dependency
    check, so importing it here is itself part of the assertion.
    """
    spec = importlib.util.spec_from_file_location(
        "manage_project_under_test", REPO_ROOT / "manage_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# ── The ML stack, made unimportable ─────────────────────────────

ML_PACKAGES = ("mlx_vlm", "whisperx", "easyocr", "torch", "torchaudio")


@pytest.fixture
def ml_stack_absent(monkeypatch):
    """Make every ML package raise ImportError, present or not.

    This machine really does have torch, easyocr and mlx_vlm installed,
    so removing whisperx alone would not exercise the case the fix is
    about.  Blocking all of them proves the CLI reaches none of them.
    """
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in ML_PACKAGES:
            raise ImportError(f"blocked by test: {root}")
        return real_import(name, *args, **kwargs)

    for pkg in ML_PACKAGES:
        for mod in [m for m in list(sys.modules) if m.split(".")[0] == pkg]:
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    return blocked


# ── 1. Only the ML-dependent commands are checked ───────────────

def test_run_is_the_whole_ml_dependent_list():
    """`run` launches the steps; nothing else in this CLI reaches them."""
    assert cli.ML_DEPENDENT_COMMANDS == ("run",)


def test_all_commands_matches_the_parser():
    """A subcommand added without listing it fails loudly, not quietly.

    main() asserts this too; asserting it here means the drift is caught
    without invoking a command.
    """
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "manage_project.py"), "--help"],
        capture_output=True, encoding="utf-8", cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    for command in cli.ALL_COMMANDS:
        assert command in proc.stdout, f"{command} is not in --help"


@pytest.mark.parametrize(
    "command", [c for c in cli.ALL_COMMANDS if c not in cli.ML_DEPENDENT_COMMANDS])
def test_non_ml_commands_are_not_checked(command, ml_stack_absent):
    """preflight_check returns rather than exiting, for every other command."""
    cli.preflight_check(command)


def test_run_still_refuses_when_the_stack_is_absent(ml_stack_absent, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check("run")
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "whisperx" in out
    # It says which commands still work, so a reader is not stuck.
    assert "dashboard" in out


# ── 2. The advice names something real ──────────────────────────

def test_advice_names_the_activate_script_that_exists(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").write_text("# activate\n", encoding="utf-8")

    lines = cli._venv_advice(tmp_path)
    named = [tok for line in lines for tok in line.split() if ".venv" in tok]
    assert named, lines
    for token in named:
        assert Path(token).exists(), f"advice names a path that does not exist: {token}"


def test_advice_sends_the_reader_to_the_ONE_location(tmp_path, monkeypatch):
    """The old message told the captain to build a venv per checkout.

    That is 4 GB of the same packages per lane, it dies with the lane,
    and `docs/ML_ENVIRONMENT.md` had already moved the real environment
    outside every checkout for exactly that reason - the advice simply
    had not followed it (docs/SHARED_ENVIRONMENT.md).
    """
    from library.tools import shared_environment

    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    lines = cli._venv_advice(tmp_path)
    text = "\n".join(lines)
    durable = str(tmp_path / "vep" / shared_environment.DURABLE_VENV_DIRNAME)
    assert durable in text, "the advice does not name the per-machine location"
    assert text.index(durable) < text.index(str(tmp_path / ".venv")), (
        "the per-checkout venv is offered before the shared one")
    assert "ONCE PER MACHINE" in text
    assert "ML_ENVIRONMENT.md" in text
    # 3.12 EXPLICITLY. Bare `python3` is 3.14 on the build machine, and
    # following that instruction rebuilds the environment
    # requirements.txt forbids - see test_advice_does_not_send_anyone_to
    # _a_forbidden_interpreter below.
    assert "python3.12 -m venv" in text
    assert "requirements.txt" in text
    # It must NOT hand over a bare `source .venv/bin/activate`: that is
    # the instruction that produced the captain's second error.
    assert "    source .venv/bin/activate" not in text


def test_run_advice_is_true_of_this_checkout(ml_stack_absent, capsys):
    """Whatever this machine is, the printed advice describes it.

    Measured 2026-09-14: no checkout on the build machine has a `.venv`,
    so the branch that named one was the branch nobody ever saw.
    """
    from library.tools import shared_environment

    with pytest.raises(SystemExit):
        cli.preflight_check("run")
    out = capsys.readouterr().out
    durable = shared_environment.vep_home() / shared_environment.DURABLE_VENV_DIRNAME
    assert str(durable) in out
    if (REPO_ROOT / ".venv" / "bin" / "python3").is_file():
        assert str(REPO_ROOT / ".venv") in out


# ── 3. The dashboard really starts without the ML stack ─────────

# The guard the child interpreters install, as the first thing they run.
#
# NOT a sitecustomize.py on PYTHONPATH: that shadows the interpreter's
# own sitecustomize, and on a Homebrew python that is the module which
# puts /opt/homebrew/lib/pythonX.Y/site-packages on sys.path - so the
# blocker silently took fastapi away with it and the test failed for a
# reason that had nothing to do with the ML stack.
#
# A meta path finder rather than a wrapped builtins.__import__, because
# it covers importlib.import_module too.
_BLOCKER = textwrap.dedent(f"""
    import sys
    _blocked = {ML_PACKAGES!r}
    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in _blocked:
                raise ImportError("blocked by test: " + name)
            return None
    sys.meta_path.insert(0, _Block())
    for _m in [m for m in list(sys.modules) if m.split(".")[0] in _blocked]:
        del sys.modules[_m]
""")


def _child(code: str, *argv, cwd=None, env=None):
    """Run `code` in a fresh interpreter with the ML stack unimportable."""
    child_env = dict(env or os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), child_env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER + textwrap.dedent(code), *argv],
        capture_output=True, encoding="utf-8", env=child_env,
        cwd=str(cwd or REPO_ROOT), timeout=180)


def test_the_blocker_really_blocks():
    """Guard the guard: a blocker that stopped working would pass everything."""
    for package in ML_PACKAGES:
        proc = _child(f"import {package}")
        assert proc.returncode != 0, package
        assert f"blocked by test: {package}" in proc.stderr, proc.stderr


def test_the_blocker_leaves_everything_else_importable():
    """And it must not take the dashboard's own dependencies with it."""
    proc = _child("import fastapi, uvicorn; print('ok')")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_dashboard_server_is_constructible_without_the_ml_stack():
    """The import the captain's command performs, with nothing installed."""
    proc = _child(
        "from library.dashboard.server import start_server\n"
        "print(callable(start_server))")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.stdout.strip().splitlines()[-1] == "True"


def _write_project(project_dir: Path, slug: str) -> None:
    yaml = pytest.importorskip("yaml")
    (project_dir / "raw").mkdir(parents=True, exist_ok=True)
    (project_dir / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (project_dir / "project.yaml").write_text(yaml.safe_dump({
        "name": slug.upper(),
        "slug": slug,
        "status": "in_progress",
        "source": {"type": "iphone_mov", "resolution": "1080x1920", "fps": 30},
        "pipeline": {"brand_template": "default_brand"},
        "resolve": {"project_name": slug, "timeline_name": "Main Edit"},
    }), encoding="utf-8")
    (project_dir / "pipeline_data.json").write_text(
        json.dumps({"preflight_completed": {}, "edit_completed": {},
                    "step_outputs": {}}), encoding="utf-8")


def test_dashboard_serves_a_project_with_the_ml_stack_unimportable(tmp_path):
    """End to end, in a fresh interpreter, exactly as a person runs it.

    The child blocks the ML packages before anything is imported, so
    this fails if any part of the dashboard path reaches one of them -
    which is what an in-process fixture cannot fully prove, because
    pytest has already imported plenty.
    """
    project_dir = tmp_path / "001"
    _write_project(project_dir, "001")

    env = dict(os.environ)
    env["PIPELINE_PROJECTS_ROOT"] = str(tmp_path / "empty-root")

    proc = _child("""
        import sys, threading, time, urllib.request
        import uvicorn
        from library.dashboard import server
        server._project_dir = sys.argv[1]
        server._project_slug = "001"
        config = uvicorn.Config(server.app, host="127.0.0.1", port=0,
                                log_level="error")
        srv = uvicorn.Server(config)
        threading.Thread(target=srv.run, daemon=True).start()
        for _ in range(400):
            if srv.started and srv.servers:
                break
            time.sleep(0.05)
        assert srv.started, "server never started"
        port = srv.servers[0].sockets[0].getsockname()[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/project") as r:
            body = r.read().decode("utf-8")
        srv.should_exit = True
        print(body)
    """, str(project_dir), env=env)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["slug"] == "001"


# ── 4. A project outside PROJECTS_ROOT ──────────────────────────

def test_project_outside_the_root_is_openable_by_path(tmp_path):
    from library.tools.project_registry import get_project

    outside = tmp_path / "somewhere else" / "001"
    _write_project(outside, "001")

    config = get_project(str(outside))
    assert config.slug == "001"
    assert Path(config.project_root) == outside


def test_failed_lookup_names_the_root_and_what_it_found(tmp_path):
    from library.tools.project_registry import get_project

    root = tmp_path / "video_projects"
    for slug in ("4th-wall", "test-proof"):
        _write_project(root / slug, slug)

    with pytest.raises(FileNotFoundError) as err:
        get_project("001", root=root)
    message = str(err.value)
    assert str(root) in message, message
    assert "4th-wall" in message and "test-proof" in message, message
    # And how to reach one that is kept elsewhere.
    assert "path" in message, message


def test_the_picker_names_the_project_being_served(tmp_path, monkeypatch):
    """A project served from outside PROJECTS_ROOT is in its own picker.

    /api/projects enumerated PROJECTS_ROOT only, so the dropdown showed
    some other project as selected while the page rendered this one, and
    navigating away left no route back to it.
    """
    from fastapi.testclient import TestClient

    from library.dashboard import server

    outside = tmp_path / "somewhere else" / "001"
    _write_project(outside, "001")
    root = tmp_path / "video_projects"
    _write_project(root / "4th-wall", "4th-wall")

    monkeypatch.setattr(server, "_project_dir", str(outside))
    monkeypatch.setattr(server, "_project_slug", "001")
    monkeypatch.setattr("library.tools.paths.PROJECTS_ROOT", root)
    monkeypatch.setattr("library.tools.project_registry.PROJECTS_ROOT", root)

    with TestClient(server.app) as client:
        listed = client.get("/api/projects").json()

    assert listed[0]["project_root"] == str(outside), listed
    assert listed[0]["slug"] == "001"
    assert listed[0]["name"] == "001".upper()
    # And it appears exactly once, not twice.
    roots = [p["project_root"] for p in listed]
    assert roots.count(str(outside)) == 1, roots


# ── 4. Importable is not the same as USABLE ─────────────────────
#
# Added 2026-09-05.  Every ML environment on the build machine carried
# whisperx 3.2.0 against a declared `whisperx>=3.8,<4`, because they were
# built on Python 3.14 - which requirements.txt forbids in its header.
# 3.2.0 imports perfectly and raises TypeError on every transcribe call;
# step 1.04 catches that per clip, so a real run produced no transcript,
# no spine and no subtitles and reported success.  The import check above
# passed the whole time, because a wrong version is not a missing one.


def test_the_manifest_really_declares_a_whisperx_floor():
    """The check is only worth anything if requirements.txt pins something."""
    declared = cli._declared_specifiers(REPO_ROOT)
    assert "whisperx" in declared, declared
    # The floor that matters: 3.2.0 is out, 3.8.6 is in.
    assert not declared["whisperx"].contains("3.2.0", prereleases=True)
    assert declared["whisperx"].contains("3.8.6", prereleases=True)


def test_a_version_outside_the_declared_range_is_reported(monkeypatch):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("whisperx",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "3.2.0" if dist == "whisperx" else "1.0.0")
    problems = cli._noncompliant_ml_packages(REPO_ROOT)
    assert [p[0] for p in problems] == ["whisperx"]
    assert problems[0][1] == "3.2.0"


def test_a_version_inside_the_declared_range_is_not_reported(monkeypatch):
    """The check must be capable of PASSING, or it is not a check."""
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("whisperx",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "3.8.6" if dist == "whisperx" else "1.0.0")
    assert cli._noncompliant_ml_packages(REPO_ROOT) == []


def test_an_undeterminable_version_is_reported_not_passed(monkeypatch):
    """An environment that cannot say what it has has not been shown to comply."""
    from importlib.metadata import PackageNotFoundError

    def absent(dist):
        raise PackageNotFoundError(dist)

    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("whisperx",))
    monkeypatch.setattr("importlib.metadata.version", absent)
    problems = cli._noncompliant_ml_packages(REPO_ROOT)
    assert [(p[0], p[1]) for p in problems] == [("whisperx", "unknown")]


def test_a_package_the_manifest_does_not_constrain_is_not_invented(
        tmp_path, monkeypatch):
    """This file must not hold an opinion requirements.txt does not.

    Written against a SYNTHETIC manifest rather than the real one: which
    packages the repository happens to pin today is not an environment,
    and a test that skips on it is the always-skip `skip_audit` exists to
    catch.
    """
    (tmp_path / "requirements.txt").write_text(
        "whisperx>=3.8,<4\neasyocr\n", encoding="utf-8")

    declared = cli._declared_specifiers(tmp_path)
    assert "whisperx" in declared
    assert "easyocr" not in declared, "an unpinned line must yield no specifier"

    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("easyocr",))
    monkeypatch.setattr("importlib.metadata.version", lambda dist: "0.0.1")
    assert cli._noncompliant_ml_packages(tmp_path) == []


def test_comments_and_options_in_the_manifest_are_not_requirements(tmp_path):
    """requirements.txt is mostly prose; none of it may parse as a pin."""
    (tmp_path / "requirements.txt").write_text(
        "# whisperx>=99 in a comment is not a requirement\n"
        "--extra-index-url https://example.invalid\n"
        "\n"
        "whisperx>=3.8,<4  # trailing comment\n",
        encoding="utf-8")
    declared = cli._declared_specifiers(tmp_path)
    assert set(declared) == {"whisperx"}
    assert declared["whisperx"].contains("3.8.6", prereleases=True)


def test_an_unreadable_manifest_constrains_nothing_rather_than_raising(tmp_path):
    """A missing manifest must not crash the CLI on its way to a refusal."""
    assert cli._declared_specifiers(tmp_path / "nope") == {}


def test_run_refuses_a_wrong_version_and_says_both_numbers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("whisperx",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "3.2.0" if dist == "whisperx" else "1.0.0")

    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check("run")
    assert exit_info.value.code == 1

    out = capsys.readouterr().out
    assert "whisperx" in out
    assert "3.2.0" in out, "the refusal must say what IS installed"
    assert ">=3.8" in out, "the refusal must say what is REQUIRED"
    # It explains the consequence, because "wrong version" reads as
    # cosmetic and this one deletes the entire edit.
    assert "reports success" in out


def test_a_compliant_environment_is_not_refused(monkeypatch):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("whisperx",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "3.8.6" if dist == "whisperx" else "1.0.0")
    cli.preflight_check("run")  # must not raise


def test_advice_does_not_send_anyone_to_a_forbidden_interpreter(tmp_path):
    """`python3` is 3.14 on this machine, and 3.14 is the broken case.

    requirements.txt: "BUILD THIS ENVIRONMENT ON PYTHON 3.12. It is not a
    preference."  Advice that says bare `python3 -m venv` rebuilds the
    exact environment that caused the outage.
    """
    text = "\n".join(cli._venv_advice(tmp_path))
    assert "3.12" in text
    for line in text.splitlines():
        stripped = line.strip()
        if "-m venv" in stripped:
            assert "python3.12" in stripped, stripped
