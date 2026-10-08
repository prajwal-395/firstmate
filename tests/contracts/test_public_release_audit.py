from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[2] / ".github" / "scripts"
          / "verify_public_engine.py")
SPEC = importlib.util.spec_from_file_location("verify_public_engine", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def _engine(tmp_path: Path, *, edition: str = "public") -> Path:
    engine = tmp_path / "engine"
    (engine / "ren").mkdir(parents=True)
    (engine / "ren/_build.py").write_text(
        f"BUILD_EDITION = {edition!r}\n", encoding="utf-8")
    (engine / "THIRD_PARTY_NOTICES").write_text(
        "component-id : python.sample\n"
        "edition      : public\n"
        "component    : sample-lib\n", encoding="utf-8")
    for group in AUDIT.RUNTIME_REQUIREMENTS:
        path = engine / "requirements" / f"{group}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sample_lib>=1\n" if group == "core" else "",
                        encoding="utf-8")
    return engine


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "THIRD_PARTY_NOTICES").write_text(
        "component-id : python.sample\n"
        "edition      : public\n"
        "component    : sample-lib\n", encoding="utf-8")
    return source


def test_public_release_audit_accepts_inventoried_runtime_dependencies(tmp_path):
    engine = _engine(tmp_path)

    assert AUDIT.verify_public_engine(engine, _source(tmp_path)) == []


def test_public_release_audit_rejects_personal_build_metadata(tmp_path):
    engine = _engine(tmp_path, edition="personal")

    assert any("expected 'public'" in failure
               for failure in AUDIT.verify_public_engine(engine, _source(tmp_path)))


def test_public_release_audit_rejects_uninventoried_runtime_dependency(tmp_path):
    engine = _engine(tmp_path)
    (engine / "requirements/core.txt").write_text(
        "sample-lib>=1\nunknown-dependency>=2\n", encoding="utf-8")

    assert any("unknown-dependency" in failure
               for failure in AUDIT.verify_public_engine(engine, _source(tmp_path)))


def test_public_release_audit_rejects_restricted_files_and_dependencies(tmp_path):
    engine = _engine(tmp_path)
    source = _source(tmp_path)
    inventory = (
        "component-id : python.sample\n"
        "edition      : public\n"
        "component    : sample-lib\n\n"
        "component-id : model.private\n"
        "edition      : personal-only\n"
        "component    : private weights\n"
        "package-path : restricted-assets\n"
        "installer-path: scripts/install_private.sh\n"
        "requirement-ref: requirements/analysis.txt:private-lib\n")
    for root in (engine, source):
        (root / "THIRD_PARTY_NOTICES").write_text(inventory, encoding="utf-8")
    (engine / "restricted-assets").mkdir()
    (engine / "scripts").mkdir()
    (engine / "scripts/install_private.sh").touch()
    (engine / "requirements/analysis.txt").write_text(
        "private_lib>=1\n", encoding="utf-8")

    failures = AUDIT.verify_public_engine(engine, source)

    assert any("model.private" in failure and "restricted-assets" in failure
               for failure in failures)
    assert any("private-lib" in failure for failure in failures)


def test_release_workflow_forces_public_edition_and_audits_before_archiving():
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
                / "release.yml").read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, macOS, ARM64]" in workflow
    assert "env -u REN_EDITION python -m ren.package_engine" in workflow
    assert "--edition public" in workflow
    assert workflow.index("verify_public_engine.py") < workflow.index(
        "package_engine_archive.py")
