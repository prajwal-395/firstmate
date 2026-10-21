"""`ren update`: move an installed Ren to a released version.

Fetches the release artifact that `.github/workflows/release.yml`
builds, verifies its checksum against the release's `SHA256SUMS.txt`,
and activates it through the same verify-then-switch path as
`ren upgrade <engine-source>`: the candidate must pass doctor and the
project-format check before `current` moves, so a broken release leaves
the active version untouched.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from ren import release
from ren.engine_root import vep_home


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren update",
        description="Move this Ren install to a released version.")
    parser.add_argument(
        "--url", default=None,
        help="fetch the engine artifact from this URL (https or file) instead "
             "of the newest GitHub release; its checksum is read from "
             "<dir>/SHA256SUMS.txt")
    parser.add_argument(
        "--tag", default=None,
        help="move to this release tag (e.g. v0.1.0) instead of the newest")
    args = parser.parse_args(argv)

    home = vep_home()
    try:
        if args.url is not None:
            zip_url = args.url
            sums_url = str(Path(args.url).parent / release.CHECKSUMS_ASSET_NAME)
            label = Path(args.url).name
        else:
            resolved = release.resolve_release(args.tag)
            zip_url = resolved.zip_url
            sums_url = resolved.sums_url
            label = resolved.tag
        print(f"Fetching Ren release {label}...")
        work = Path(tempfile.mkdtemp(prefix="ren-update-"))
        try:
            zip_path = release.download(zip_url, work / Path(zip_url).name)
            sums_path = release.download(
                sums_url, work / release.CHECKSUMS_ASSET_NAME)
            release.verify_checksum(zip_path, sums_path)
            engine = release.extract_engine(zip_path, work / "engine")
            from ren import upgrade
            candidate = upgrade.upgrade(engine, home=home)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except RuntimeError as exc:
        from library.tools.ren_refusal import RenRefusal
        raise RenRefusal(
            "Ren could not update to the released version",
            str(exc),
            "check the network and retry `ren update`", blocker="unsupported_operation") from exc
    print(f"Ren updated to {candidate.name}. "
          f"Run `ren rollback` to restore the previous version.")
    return 0
