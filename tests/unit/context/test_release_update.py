"""The release install route: fetch, verify, and activate a release.

These cases pin the behaviour the terminal install route creates:

- `ren/release.py` verifies an artifact against the release's
  `SHA256SUMS.txt` and refuses a mismatch or a missing entry.
- `ren update --url` fetches a local artifact, verifies it, and moves an
  install onto it through the same verify-then-switch path as
  `ren upgrade <engine-source>`.

The doctor and project-format checks are stubbed in the update case
because `tests/unit/context/test_engine_upgrade.py` owns them; what is
pinned here is that a verified artifact reaches the active pointer.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ren import engine_root, release, upgrade
from ren.version import format_version


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_payload(tag="v0.1.0"):
    return {
        "tag_name": tag,
        "assets": [
            {"name": f"ren-engine-{tag}.zip",
             "browser_download_url": f"https://example.com/ren-engine-{tag}.zip"},
            {"name": "SHA256SUMS.txt",
             "browser_download_url": "https://example.com/SHA256SUMS.txt"},
            {"name": f"ren-engine-{tag}-macos.zip",
             "browser_download_url": f"https://example.com/ren-engine-{tag}-macos.zip"},
        ],
    }


def test_resolve_release_picks_the_engine_zip_and_checksum_manifest(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(_install_payload()))

    resolved = release.resolve_release()

    assert resolved.tag == "v0.1.0"
    assert resolved.zip_url == "https://example.com/ren-engine-v0.1.0.zip"
    assert resolved.sums_url == "https://example.com/SHA256SUMS.txt"


def test_resolve_release_refuses_a_release_without_the_engine_artifact(monkeypatch):
    payload = _install_payload()
    payload["assets"] = [a for a in payload["assets"]
                         if a["name"] == "SHA256SUMS.txt"]
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(payload))

    with pytest.raises(RuntimeError, match="no ren-engine-v0.1.0.zip asset"):
        release.resolve_release()


def test_resolve_release_refuses_a_release_without_the_checksum_manifest(monkeypatch):
    payload = _install_payload()
    payload["assets"] = [a for a in payload["assets"]
                         if a["name"] != "SHA256SUMS.txt"]
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(payload))

    with pytest.raises(RuntimeError, match="no SHA256SUMS.txt asset"):
        release.resolve_release()


def test_resolve_release_reads_a_named_tag(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(_install_payload("v0.2.0")))

    resolved = release.resolve_release("v0.2.0")

    assert resolved.tag == "v0.2.0"
    assert resolved.zip_url == "https://example.com/ren-engine-v0.2.0.zip"


def test_verify_checksum_accepts_the_published_hash(tmp_path):
    blob = tmp_path / "ren-engine-v0.1.0.zip"
    blob.write_bytes(b"engine bytes")
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{digest}  ren-engine-v0.1.0.zip\n", encoding="utf-8")

    release.verify_checksum(blob, sums)


def test_verify_checksum_refuses_a_mismatch(tmp_path):
    blob = tmp_path / "ren-engine-v0.1.0.zip"
    blob.write_bytes(b"tampered engine")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(
        f"{'0' * 64}  ren-engine-v0.1.0.zip\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        release.verify_checksum(blob, sums)


def test_verify_checksum_refuses_an_asset_absent_from_the_manifest(tmp_path):
    blob = tmp_path / "ren-engine-v0.1.0.zip"
    blob.write_bytes(b"engine bytes")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{'0' * 64}  ren-engine-v0.2.0.zip\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no checksum for ren-engine-v0.1.0.zip"):
        release.verify_checksum(blob, sums)


def test_extract_engine_returns_the_engine_tree(tmp_path):
    zip_path = tmp_path / "ren-engine-v0.1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ren-engine/bin/ren", "#!/bin/sh\n")
        archive.writestr("ren-engine/ren/cli.py", "# front door\n")

    engine = release.extract_engine(zip_path, tmp_path / "out")

    assert engine == tmp_path / "out" / "ren-engine"
    assert (engine / "bin" / "ren").is_file()


def test_extract_engine_refuses_a_zip_without_the_engine_tree(tmp_path):
    zip_path = tmp_path / "ren-engine-v0.1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("not-the-engine.txt", "hello")

    with pytest.raises(RuntimeError, match="does not hold a ren-engine/"):
        release.extract_engine(zip_path, tmp_path / "out")


def test_download_reads_a_file_url(tmp_path):
    source = tmp_path / "artifact.zip"
    source.write_bytes(b"artifact bytes")
    dest = tmp_path / "nested" / "artifact.zip"

    result = release.download(source.as_uri(), dest)

    assert result == dest
    assert dest.read_bytes() == b"artifact bytes"


def _fake_engine(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manage_project.py").write_text("# fake engine\n", encoding="utf-8")
    (path / "library").mkdir(exist_ok=True)
    (path / "ren").mkdir(exist_ok=True)
    (path / "ren" / "cli.py").write_text("# fake front door\n", encoding="utf-8")
    (path / "bin").mkdir(exist_ok=True)
    (path / "bin" / "ren").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "bin" / "vep").write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def _build_artifact(dest: Path, sha: str) -> Path:
    """Build a release artifact the way .github/workflows/release.yml does."""
    engine = dest / "engine"
    subprocess.run(
        [sys.executable, "-m", "ren.package_engine",
         "--src", str(REPO_ROOT), "--dest", str(engine),
         "--channel", "stable", "--sha", sha, "--built-at", "2026-10-06"],
        check=True, capture_output=True, text=True, encoding="utf-8")
    zip_path = dest / "ren-engine-v0.1.0.zip"
    subprocess.run(
        [sys.executable, str(REPO_ROOT / ".github" / "scripts"
                             / "package_engine_archive.py"),
         "--engine", str(engine), "--archive", str(zip_path)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (dest / "SHA256SUMS.txt").write_text(
        f"{digest}  ren-engine-v0.1.0.zip\n", encoding="utf-8")
    return zip_path


def test_update_url_moves_the_install_onto_the_artifact(tmp_path, monkeypatch):
    """`ren update --url` fetches a local artifact and activates it.

    The doctor and project-format checks are stubbed (owned by
    test_engine_upgrade.py); what is pinned is that a checksum-verified
    artifact reaches the active pointer and the previous version is kept
    for rollback.
    """
    artifact_dir = tmp_path / "release"
    artifact_dir.mkdir()
    zip_path = _build_artifact(artifact_dir, "update-test-sha")
    expected_build = format_version({
        "version": "0.1.0", "sha": "update-test-sha", "channel": "stable",
        "edition": "personal", "built_at": "2026-10-06",
    })

    home = tmp_path / "vep"
    before = _fake_engine(home / "versions" / "0.1.0+before.sha.stable.personal.2026-01-01")
    (home / "current").symlink_to(Path("versions") / before.name)

    monkeypatch.setattr(upgrade, "_verify_doctor", lambda *_args: None)
    monkeypatch.setattr(upgrade, "_verify_project_formats", lambda *_args: None)

    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PIPELINE_VEP_HOME": str(home),
        "PIPELINE_PYTHON": sys.executable,
        "PYTHONPATH": str(REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-m", "ren.cli", "update", "--url", zip_path.as_uri()],
        capture_output=True, text=True, encoding="utf-8", env=env,
        timeout=300, check=False)

    assert result.returncode == 0, result.stderr + result.stdout
    assert engine_root.version_at_pointer(
        engine_root.current_link(home), home) == expected_build
    assert engine_root.version_at_pointer(
        engine_root.previous_link(home), home) == before.name
