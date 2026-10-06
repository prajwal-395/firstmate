"""Ren release artifacts: resolve, fetch, verify and extract a release.

The release contract (what `.github/workflows/release.yml` publishes for
a `v*` tag):

- Asset `ren-engine-v<TAG>.zip` - the immutable engine tree, built by
  `ren package_engine` and zipped by `.github/scripts/package_engine_archive.py`.
  This is the artifact the terminal install route fetches.
- Asset `SHA256SUMS.txt` - `shasum -a 256` output covering every asset.
- Asset `ren-engine-v<TAG>-macos.zip` - the signed and notarized macOS app
  bundle, published only when Apple credentials are configured. The terminal
  route never fetches it: a curl or Homebrew install needs no signature.

Standard library only: this module is packaged inside the engine tree and is
also reached by the curl installer from the downloaded artifact, before the
ML environment exists.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

RELEASE_REPOSITORY = "prajwal-395/video_editing_pilot"
"""The GitHub repository that owns Ren's releases."""

ENGINE_ASSET_PREFIX = "ren-engine-"
CHECKSUMS_ASSET_NAME = "SHA256SUMS.txt"
SIGNED_ASSET_SUFFIX = "-macos.zip"

_DOWNLOAD_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Release:
    """One GitHub release as the artifact URLs the terminal route fetches."""

    tag: str
    zip_url: str
    sums_url: str


def engine_asset_name(tag: str) -> str:
    """The engine zip asset name for a release tag (the tag keeps its `v`)."""
    return f"{ENGINE_ASSET_PREFIX}{tag}.zip"


def _api_release(endpoint: str) -> dict:
    url = (f"https://api.github.com/repos/{RELEASE_REPOSITORY}"
           f"/releases/{endpoint}")
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "ren-update"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"could not read the Ren release from GitHub: {exc}") from exc


def resolve_release(tag: str | None = None) -> Release:
    """The latest release, or the release for `tag`, as artifact URLs.

    Refuses when the release exists but carries no engine zip or no
    checksum manifest: an artifact that cannot be verified is not installed.
    """
    payload = _api_release(f"tags/{tag}") if tag else _api_release("latest")
    assets = {asset["name"]: asset["browser_download_url"]
              for asset in payload.get("assets", [])}
    zip_name = engine_asset_name(payload["tag_name"])
    if zip_name not in assets:
        raise RuntimeError(
            f"the Ren release {payload['tag_name']} has no {zip_name} asset")
    if CHECKSUMS_ASSET_NAME not in assets:
        raise RuntimeError(
            f"the Ren release {payload['tag_name']} has no "
            f"{CHECKSUMS_ASSET_NAME} asset")
    return Release(tag=payload["tag_name"], zip_url=assets[zip_name],
                   sums_url=assets[CHECKSUMS_ASSET_NAME])


def download(url: str, dest: Path) -> Path:
    """Fetch `url` (https or file) to `dest`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, \
                dest.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"could not download {url}: {exc}") from exc
    return dest


def verify_checksum(zip_path: Path, sums_path: Path) -> None:
    """Verify `zip_path` against its line in a `shasum -a 256` sums file."""
    name = Path(zip_path).name
    expected = None
    for line in Path(sums_path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name:
            expected = parts[0]
            break
    if expected is None:
        raise RuntimeError(
            f"{Path(sums_path).name} has no checksum for {name}")
    digest = hashlib.sha256()
    with Path(zip_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {name}: the release published {expected}, "
            f"the download hashed {actual}")


def extract_engine(zip_path: Path, dest: Path) -> Path:
    """Extract the engine tree from a release zip; return its path.

    The zip holds one top-level `ren-engine/` directory (what
    `.github/scripts/package_engine_archive.py` writes). The stored Unix
    modes are restored after extraction: `zipfile.extractall` drops them,
    and the engine's `bin/ren` must stay executable for the candidate to
    run.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(dest)
            for info in archive.infolist():
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o7777
                if mode:
                    (dest / info.filename).chmod(mode)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError(f"could not extract {zip_path}: {exc}") from exc
    engine = dest / "ren-engine"
    if not (engine / "bin" / "ren").is_file():
        raise RuntimeError(
            f"{Path(zip_path).name} does not hold a ren-engine/ engine tree")
    return engine


def update_to_release(home: Path, url: str | None = None,
                      tag: str | None = None) -> Path:
    """Fetch a release artifact, verify it, and move `home` onto it.

    With `url`, the artifact is fetched from that URL (https or file) and
    its checksum from `<dir>/SHA256SUMS.txt`; otherwise the latest release -
    or the release for `tag` - is resolved from GitHub. The artifact is
    activated through `ren upgrade`'s verify-then-switch path, so a
    candidate that fails doctor or the project-format check leaves the
    active version untouched.
    """
    if url is not None:
        zip_url = url
        sums_url = str(Path(url).parent / CHECKSUMS_ASSET_NAME)
    else:
        release = resolve_release(tag)
        zip_url = release.zip_url
        sums_url = release.sums_url
    work = Path(tempfile.mkdtemp(prefix="ren-release-"))
    try:
        zip_path = download(zip_url, work / Path(zip_url).name)
        sums_path = download(sums_url, work / CHECKSUMS_ASSET_NAME)
        verify_checksum(zip_path, sums_path)
        engine = extract_engine(zip_path, work / "engine")
        from ren import upgrade
        return upgrade.upgrade(engine, home=home)
    finally:
        shutil.rmtree(work, ignore_errors=True)
