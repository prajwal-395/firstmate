"""Every fetched executable or model is pinned and hash-verified, or it refuses.

`library/tools/shared_environment.py` owns every download pin and
`library/tools/frame_ranker.py:download_head` fetches the LAION head.
The defect these name: an install that curls bytes and executes them
with no integrity check (micromamba came from `/latest`, the
DeepFilterNet binary had no checksum, the LAION head had none), and a
model download that floats or can never resolve (MFA's dictionary/G2P
were unpinned; the pinned acoustic `--version` carried a doubled `v`
MFA raises on).

Nothing here reaches the network: pins are asserted as data, hashing
against `tmp_path` bytes, and the download is a monkeypatched
`urlretrieve` writing attacker-chosen bytes.
"""

import hashlib
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import shared_environment as se  # noqa: E402
from library.tools import frame_ranker as fr  # noqa: E402


def _is_hex64(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


# ── the hash primitive ─────────────────────────────────────────────

def test_file_sha256_matches_hashlib(tmp_path):
    """The pin checker hashes what hashlib hashes: no second algorithm."""
    target = tmp_path / "bytes.bin"
    target.write_bytes(b"tamper me" * 64)
    assert se.file_sha256(target) == hashlib.sha256(target.read_bytes()).hexdigest()


def test_file_sha256_refuses_absence_as_absence(tmp_path):
    """A missing download is FileNotFoundError, never an empty hash."""
    with pytest.raises(FileNotFoundError):
        se.file_sha256(tmp_path / "never-fetched.bin")


def test_verify_detects_tampered_bytes(tmp_path):
    """One flipped byte fails the pin and reports what was really there."""
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"genuine bytes")
    expected = hashlib.sha256(b"genuine bytes").hexdigest()
    ok, actual = se.verify_file_sha256(target, expected)
    assert (ok, actual) == (True, expected)
    target.write_bytes(b"genuine byted")
    ok, actual = se.verify_file_sha256(target, expected)
    assert ok is False
    assert actual == hashlib.sha256(b"genuine byted").hexdigest()
    assert actual != expected


# ── micromamba: versioned URL, never /latest ────────────────────────

def test_micromamba_url_is_versioned_not_latest():
    """The audit's MFA claim: `/latest` moved under every fresh machine."""
    for platform_name in ("osx-arm64", "linux-64"):
        url = se.micromamba_download_url(platform_name)
        assert "/latest" not in url, f"unpinned micromamba URL: {url}"
        assert se.MICROMAMBA_VERSION in url, f"version missing from {url}"


def test_micromamba_hashes_are_pinned_per_platform():
    """Both platforms the install supports carry a real sha256."""
    assert set(se.MICROMAMBA_SHA256) == {"osx-arm64", "linux-64"}
    for platform_name, digest in se.MICROMAMBA_SHA256.items():
        assert _is_hex64(digest), f"bad hash for {platform_name}: {digest!r}"
        assert (se.micromamba_expected_sha256(platform_name) == digest)


def test_micromamba_refuses_an_unpinned_platform(monkeypatch):
    """A machine with no prebuilt binary gets a refusal, not a wrong-arch fetch."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    with pytest.raises(se.MfaEnvironmentMissing):
        se.micromamba_download_url()


# ── MFA packages and models: exact, canonical, resolvable ──────────

def test_mfa_conda_spec_is_exact():
    """`3.4.*` floated every fresh machine onto that day's index."""
    assert "*" not in se.MFA_PACKAGE_SPEC, se.MFA_PACKAGE_SPEC
    assert se.MFA_PACKAGE_SPEC == (
        f"montreal-forced-aligner={se.MFA_PACKAGE_VERSION}")


def test_mfa_model_versions_are_canonical():
    """MFA resolves `--version` verbatim after tolerating ONE missing `v`;
    a doubled `vv` raises RemoteModelVersionNotFoundError - which is what
    the old `v$VERSION` acoustic invocation passed."""
    for version in (se.MFA_ACOUSTIC_MODEL_VERSION,
                    se.MFA_DICTIONARY_VERSION,
                    se.MFA_G2P_VERSION):
        assert version.startswith("v") and not version.startswith("vv"), version


# ── DeepFilterNet binary: the audit's checksum claim ────────────────

def test_deepfilter_url_and_hash_agree_on_the_release():
    """The pin is one release: tag, asset and hash move together or not at all."""
    assert se.DEEPFILTER_RELEASE_TAG == f"v{se.DEEPFILTER_VERSION}"
    assert se.DEEPFILTER_VERSION in se.DEEPFILTER_DOWNLOAD_URL
    assert se.DEEPFILTER_ASSET in se.DEEPFILTER_DOWNLOAD_URL
    assert _is_hex64(se.DEEPFILTER_SHA256)


# ── PANNs: the hash-checked path keeps its pin ──────────────────────

def test_panns_keeps_its_hash_pin():
    """The audit's one HELD claim: PANNs was already md5-verified."""
    assert se.PANNS_CHECKPOINT_MD5 == "70539c43c18b6a289b3199c503a82c5a"
    assert "3987831" in se.PANNS_DOWNLOAD_URL
    assert (se.PANNS_CHECKPOINT_NAME.replace("=", "%3D")
            in se.PANNS_DOWNLOAD_URL)


# ── LAION head: fetched bytes are verified or refused ───────────────

def test_laion_head_hash_is_pinned():
    assert fr.HEAD_FILENAME in fr.HEAD_URL
    assert _is_hex64(fr.HEAD_SHA256)


def test_download_head_reuses_a_valid_cache_without_network(tmp_path, monkeypatch):
    """A cached head with the pinned hash is reused: no fetch, no load."""
    cached = tmp_path / fr.HEAD_FILENAME
    cached.write_bytes(b"weights")
    monkeypatch.setattr(fr, "HEAD_SHA256",
                        hashlib.sha256(b"weights").hexdigest())
    fetched = mock.Mock()
    monkeypatch.setattr("urllib.request.urlretrieve", fetched)
    assert fr.download_head(str(tmp_path)) == str(cached)
    fetched.assert_not_called()


def test_download_head_refuses_tampered_bytes_and_deletes_them(tmp_path, monkeypatch):
    """Attacker bytes over the wire: refused AND removed, never loaded."""
    def _evil(url, dest):
        Path(dest).write_bytes(b"re-ranked everything")

    monkeypatch.setattr("urllib.request.urlretrieve", _evil)
    with pytest.raises(fr.FrameRankerUnavailable):
        fr.download_head(str(tmp_path))
    assert not (tmp_path / fr.HEAD_FILENAME).exists()


def test_download_head_replaces_a_tampered_cache(tmp_path, monkeypatch):
    """A poisoned cache is not trusted: it is replaced, then the
    replacement is verified like any fresh fetch."""
    cached = tmp_path / fr.HEAD_FILENAME
    cached.write_bytes(b"yesterday's bytes")

    def _good(url, dest):
        Path(dest).write_bytes(b"genuine head")

    monkeypatch.setattr("urllib.request.urlretrieve", _good)
    monkeypatch.setattr(fr, "HEAD_SHA256",
                        hashlib.sha256(b"genuine head").hexdigest())
    assert fr.download_head(str(tmp_path)) == str(cached)
    assert cached.read_bytes() == b"genuine head"
