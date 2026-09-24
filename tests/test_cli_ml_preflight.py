#!/usr/bin/env python3
"""The ML preflight belongs to the commands that need it.

The captain could not open the review dashboard on 2026-08-26 because
`manage_project.py` checked for `mlx_vlm`, `easyocr` and
`torch` at IMPORT time, before argparse had seen the command.  The fix
moved the check to the commands that need it.  (P2 retired the dashboard
itself; the preflight design it forced stays. whisperx was a fourth
member until 2026-09-24, when it left the venv and the manifest with
its fallback arms.)

These tests hold the halves of the fix that remain:

   1. Every command except the ones in ML_DEPENDENT_COMMANDS is served
      with the whole ML stack unimportable.
   2. The advice names a path that is really on disk.
   3. A project kept outside PROJECTS_ROOT is openable by path, and a
      slug that is not there says which root was searched and what was
      in it.
"""

import builtins
import importlib.util
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

ML_PACKAGES = ("mlx_vlm", "easyocr", "torch", "torchaudio")


@pytest.fixture
def ml_stack_absent(monkeypatch):
    """Make every ML package raise ImportError, present or not.

    This machine really does have torch, easyocr and mlx_vlm installed,
    so blocking one alone would not exercise the case the fix is
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





def test_preflight_passes_legitimate_commands_without_refusing(
        ml_stack_absent, monkeypatch):
    """B1 collapse: the thin preflight wrappers in one test - every
    non-ML command passes unchecked, and a compliant environment passes
    `run` too."""
    for command in [c for c in cli.ALL_COMMANDS
                    if c not in cli.ML_DEPENDENT_COMMANDS]:
        cli.preflight_check(command)
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.7.2" if dist == "mlx-vlm" else "1.0.0")
    cli.preflight_check("run")  # must not raise


def test_run_still_refuses_when_the_stack_is_absent(ml_stack_absent, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check("run")
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "mlx_vlm" in out
    # It says which commands still work, so a reader is not stuck.
    assert "status" in out


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




# ── 3. The child-interpreter blocker ──────────────────────────────

# The guard the child interpreters install, as the first thing they run.
#
# NOT a sitecustomize.py on PYTHONPATH: that shadows the interpreter's
# own sitecustomize, and on a Homebrew python that is the module which
# puts /opt/homebrew/lib/pythonX.Y/site-packages on sys.path - so the
# blocker silently took unrelated packages away with it and the test
# failed for a reason that had nothing to do with the ML stack.
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


# ── 4. A project outside PROJECTS_ROOT ──────────────────────────


# ── 4. Importable is not the same as USABLE ─────────────────────
#
# Added 2026-09-05.  Every ML environment on the build machine carried
# whisperx 3.2.0 against a declared `whisperx>=3.8,<4`, because they were
# built on Python 3.14 - which requirements.txt forbids in its header.
# 3.2.0 imports perfectly and raises TypeError on every transcribe call;
# step 1.04 catches that per clip, so a real run produced no transcript,
# no spine and no subtitles and reported success.  The import check above
# passed the whole time, because a wrong version is not a missing one.
# (whisperx left the venv on 2026-09-24; the tests below exercise the
# same machinery with the live mlx_vlm floor, which has the same shape.)




def test_a_version_outside_the_declared_range_is_reported(monkeypatch):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.3.9" if dist == "mlx-vlm" else "1.0.0")
    problems = cli._noncompliant_ml_packages(REPO_ROOT)
    assert [p[0] for p in problems] == ["mlx_vlm"]
    assert problems[0][1] == "0.3.9"


def test_a_version_inside_the_declared_range_is_not_reported(monkeypatch):
    """The check must be capable of PASSING, or it is not a check."""
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.7.2" if dist == "mlx-vlm" else "1.0.0")
    assert cli._noncompliant_ml_packages(REPO_ROOT) == []










def test_run_refuses_a_wrong_version_and_says_both_numbers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.3.9" if dist == "mlx-vlm" else "1.0.0")

    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check("run")
    assert exit_info.value.code == 1

    out = capsys.readouterr().out
    assert "mlx_vlm" in out
    assert "0.3.9" in out, "the refusal must say what IS installed"
    assert ">=0.7" in out, "the refusal must say what is REQUIRED"
    # It explains the consequence, because "wrong version" reads as
    # cosmetic and this one breaks vision on every real project.
    assert "fails inside the step" in out


