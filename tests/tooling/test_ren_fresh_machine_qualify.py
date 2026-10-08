from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_QUALIFY_SPEC = importlib.util.spec_from_file_location(
    "_ren_fresh_machine_qualify",
    REPO_ROOT / "scripts" / "ren_fresh_machine_qualify.py",
)
qualify = importlib.util.module_from_spec(_QUALIFY_SPEC)
sys.modules[_QUALIFY_SPEC.name] = qualify
_QUALIFY_SPEC.loader.exec_module(qualify)


def test_build_artifact_matches_installer_layout(tmp_path, monkeypatch):
    def build(_src: Path, dest: Path) -> None:
        (dest / "ren").mkdir(parents=True)
        (dest / "ren" / "cli.py").write_text("artifact", encoding="utf-8")

    monkeypatch.setattr(qualify, "build_engine", build)
    artifact = qualify.build_artifact(tmp_path / "source", tmp_path / "sandbox")

    with zipfile.ZipFile(artifact) as archive:
        assert archive.read("ren-engine/ren/cli.py") == b"artifact"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert (artifact.parent / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{digest}  {artifact.name}\n"
    )


def test_install_artifact_uses_local_customer_download_path(tmp_path, monkeypatch):
    artifact = tmp_path / "engine.zip"
    artifact.write_bytes(b"zip")
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(qualify.subprocess, "run", run)
    sandbox = tmp_path / "sandbox"
    qualify.install_artifact(artifact, sandbox)

    command = calls[0][0][0]
    assert command[0:2] == ["bash", str(Path(qualify.__file__).with_name("install_ren.sh"))]
    assert command[2:4] == ["--url", artifact.as_uri()]
    assert "--home" in command and "--skip-system-deps" in command
    assert calls[0][1]["env"]["HOME"] == str(sandbox / "home")
    assert calls[0][1]["env"]["PIPELINE_VEP_HOME"] == str(sandbox / "vep")
