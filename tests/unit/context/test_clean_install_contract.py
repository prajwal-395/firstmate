"""A CLEAN install must work without the development checkout.

The defects this names (PROD-001, measured 2026-10-21 on this Mac by
`scripts/ren_clean_install_check.py`, which installs a release-shaped
artifact into a throwaway prefix and a throwaway HOME):

* the interpreter ladder knew only `$PIPELINE_PYTHON`, the machine's
  default `~/.local/share/vep` and a checkout's `.venv`. An engine
  installed into a prefix of its own keeps its runtime venv beside that
  prefix, so the installed `current/bin/ren` refused - silently, with
  exit 3 and NO output, because the front door built the refusal
  message and threw it away;
* the install record named the prefix, the runtime and the models, and
  nothing named the ENGINE VERSION it installed, so a machine with two
  engines could not say which one its `ren` runs;
* the doctor's fixes named paths inside a checkout (`scripts/...`), which
  a customer cannot run - the engine's own tree is where those files
  live;
* the MFA refusal named `scripts/install_mfa.sh` and never the command a
  customer can run;
* the doctor called the broker in service "built from this tree" while
  the binary serving the machine was another tree's build;
* the build of the Rust edit backend needed `maturin` in the RUNTIME
  venv, and the locked runtime carries no build tool - so the fix the
  doctor named ("run ren setup to build it from the engine's Rust
  sources") could not work on an installed machine.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import shared_environment
from ren import doctor, setup


def _fake_engine(root: Path) -> Path:
    """A tree that satisfies the engine-root sentinels."""
    (root / "library").mkdir(parents=True, exist_ok=True)
    (root / "ren").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "manage_project.py").write_text("", encoding="utf-8")
    (root / "ren" / "cli.py").write_text("", encoding="utf-8")
    (root / "bin" / "ren").write_text("", encoding="utf-8")
    (root / "bin" / "vep").write_text("", encoding="utf-8")
    return root


# ── the ladder: an engine finds its own runtime ──────────────────────


def test_the_engines_own_install_home_is_on_the_ladder(tmp_path, monkeypatch):
    """An installed engine's runtime venv is beside its own prefix.

    Before this, the ladder stopped at the machine's default
    `~/.local/share/vep`, so an engine installed elsewhere could not run
    its own front door.
    """
    prefix = tmp_path / "vep"
    engine = prefix / "versions" / "0.1.0+test"
    engine.mkdir(parents=True)

    monkeypatch.delenv("PIPELINE_PYTHON", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "elsewhere"))

    home = shared_environment.engine_home(engine)
    assert home == prefix

    candidates = [str(c) for c in shared_environment.interpreter_candidates(engine)]
    assert str(prefix / "venv-py312" / "bin" / "python3") in candidates


def test_a_checkout_is_not_an_install_home(tmp_path):
    """A checkout has no install home, and the ladder says so.

    Otherwise the rung would look for a runtime venv inside a checkout
    and, worse, treat a checkout's parent directory as an install.
    """
    checkout = tmp_path / "video-editing-pilot"
    checkout.mkdir()
    assert shared_environment.engine_home(checkout) is None
    assert shared_environment.engine_home(None) is None


def test_the_ladder_finds_the_engines_own_runtime(tmp_path, monkeypatch):
    """The interpreter the ladder names is the one the engine owns."""
    prefix = tmp_path / "vep"
    engine = prefix / "versions" / "0.1.0+test"
    engine.mkdir(parents=True)
    interpreter = prefix / "venv-py312" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    interpreter.chmod(0o755)

    monkeypatch.delenv("PIPELINE_PYTHON", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "elsewhere"))

    found, why_not = shared_environment.python_interpreter(engine)
    assert found == str(interpreter), why_not


# ── the install record names the engine it installed ─────────────────


def test_the_install_record_names_the_engine_version(tmp_path):
    """A machine with two engines must be able to say which one it runs.

    The record named the prefix, the runtime and the models, and not the
    version - so "what is installed here?" had no answer on disk.
    """
    setup._install_record(
        tmp_path / "vep", "/opt/homebrew", tmp_path / ".zprofile",
        tmp_path / "vep" / "python", engine=tmp_path / "vep" / "versions" / "v1",
    )
    record = json.loads(
        (tmp_path / "vep" / setup.INSTALL_RECORD).read_text(encoding="utf-8"))
    assert record["engine_version"]
    assert record["engine_root"].endswith("v1")


# ── the doctor's fixes are runnable ──────────────────────────────────


def test_the_doctor_names_the_engines_own_build_script(tmp_path, monkeypatch):
    """A fix that names a checkout path is a fix a customer cannot run.

    The build scripts live in the ENGINE's tree; the doctor must name
    them there.
    """
    engine = _fake_engine(tmp_path / "engine")
    (engine / "scripts").mkdir(exist_ok=True)
    (engine / "scripts" / "build_ren_resolved.sh").write_text("", encoding="utf-8")
    monkeypatch.setenv("REN_ENGINE_ROOT", str(engine))
    # The broker in service is the machine's, not this tree's: the test
    # must not reach the live one.
    monkeypatch.setattr("library.tools.resolved.client.ping",
                        lambda *args, **kwargs: None)

    checks = doctor.native_freshness_checks()
    assert checks, "the doctor reported no native rows"
    for check in checks:
        fix = check.fix or ""
        if not fix or "build it with" not in fix:
            continue
        named = fix.split("build it with", 1)[1].strip()
        assert named.startswith(str(engine)), (
            f"the fix names {named!r}, which is not inside the engine tree")


def test_the_mfa_refusal_names_a_command_a_customer_can_run():
    """The MFA refusal must name `ren setup --with mfa`.

    It named `scripts/install_mfa.sh` - a path that exists only beside a
    checkout - and never the command the engine itself offers.
    """
    message = shared_environment.mfa_missing_message()
    assert "ren setup --with mfa" in message


# ── the broker in service is named honestly ──────────────────────────


def test_the_broker_row_says_when_another_trees_broker_serves(tmp_path):
    """The broker serving the machine and this engine's broker are two
    binaries, and the doctor must not confuse them."""
    own = tmp_path / "engine" / "native" / "bin" / "ren-resolved"
    own.parent.mkdir(parents=True)
    own.write_text("", encoding="utf-8")

    note = doctor._foreign_broker_note(
        "/somewhere/else/native/target/release/ren-resolved serve /tmp/x.sock",
        own)
    assert "ANOTHER tree" in note
    assert str(own) in note

    assert doctor._foreign_broker_note(f"{own} serve /tmp/x.sock", own) == ""
    assert doctor._foreign_broker_note("", own) == ""


# ── the Rust backend the doctor promises can actually be built ───────


def test_the_build_fetches_its_build_tool_when_the_runtime_has_none():
    """The locked runtime carries no `maturin`, and must not have to.

    The build refused with "no maturin in <venv>" on every install, so
    the fix the doctor named could not work. The build tool is fetched
    ephemerally through `uv` instead, at a pinned version.
    """
    script = REPO_ROOT / "scripts" / "build_ren_native.sh"
    text = script.read_text(encoding="utf-8")
    assert "uvx --from" in text, "the build does not fetch maturin through uv"
    assert "MATURIN_VERSION=" in text, "the fetched build tool is not pinned"


def test_a_packaged_engine_keeps_its_version_directory_immutable():
    """The build's scratch must not land inside a packaged engine tree.

    A version directory is immutable; a cargo target directory written
    into it would change the tree that `ren version` names.
    """
    script = (REPO_ROOT / "scripts" / "build_ren_native.sh").read_text(
        encoding="utf-8")
    assert "CARGO_TARGET_DIR" in script
    assert "ren/_build.py" in script, (
        "the build cannot tell a packaged engine from a checkout")
