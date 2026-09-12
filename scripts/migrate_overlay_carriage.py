#!/usr/bin/env python3
"""Carry a project's existing overlays under today's carriage, by TRANSCODE.

`overlay_mode.OVERLAY_CARRIAGE` went from `tight-480-3` to `tight-480-4`
on 2026-09-12 when overlays became QuickTime Animation RGBA instead of
ProRes 4444.  The carriage is digested into the caption reuse key
(`step_4_05_render_subtitles._reuse_key`) and stamped on every tight-box
sidecar, so without this every one of a project's cached overlays reads
as unusable and the next run redraws all of them - roughly 3.6 hours for
the captain's 1,088 caption segments.

It does not have to.  What a reuse key promises is a PICTURE, and
`qtrle` is lossless over the 8-bit RGBA a Chromium render produces, so
an existing artefact transcoded in place IS the picture its key already
names.  This rewrites the files (verifying each one bit-exact before it
replaces anything, and leaving the original alone where it is not) and
then moves the two recorded stamps.  Roughly 0.2 s a file.

Nothing here renders, and nothing here opens Resolve.

    python3 scripts/migrate_overlay_carriage.py <project-dir> [--apply]

Without `--apply` it reports what it would do and writes nothing.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.overlay_carriage import (  # noqa: E402
    OVERLAY_VIDEO_CODEC,
    probe_overlay,
    restamp_carriage,
    transcode_in_place,
)
from library.tools.overlay_mode import OVERLAY_CARRIAGE  # noqa: E402

SUPERSEDED_CARRIAGE = "tight-480-3"
"""The carriage this migration is FROM.

Named rather than "whatever is on disk": a stamp from an older carriage
than this one was superseded for a reason that a transcode does not
answer (`tight-480-2` sidecars carry half the Tilt their artefact
needs), so those are left for a re-render and are not quietly restamped
into currency.
"""

STEP_DIRS = (
    os.path.join("pipeline_output", "steps", "4_05_render_subtitles"),
    os.path.join("pipeline_output", "steps", "4_06_render_motion_graphics"),
)


def overlay_movs(project_dir: str) -> list[str]:
    found: list[str] = []
    for step in STEP_DIRS:
        root = os.path.join(project_dir, step)
        found += sorted(glob.glob(os.path.join(root, "**", "*.mov"),
                                  recursive=True))
    return found


def stamp_files(project_dir: str) -> list[str]:
    found: list[str] = []
    for step in STEP_DIRS:
        root = os.path.join(project_dir, step)
        found += sorted(glob.glob(os.path.join(root, "**",
                                               "*_reuse_key.txt"),
                                  recursive=True))
        found += sorted(glob.glob(os.path.join(root, "**", "*_box.json"),
                                  recursive=True))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the files; without it, report only")
    args = parser.parse_args()

    project = os.path.abspath(args.project_dir)
    if not os.path.isdir(project):
        print(f"not a directory: {project}", file=sys.stderr)
        return 2

    movs = overlay_movs(project)
    if not movs:
        print(f"no overlay .mov under {project}", file=sys.stderr)
        return 1

    to_carry, already = [], 0
    for path in movs:
        codec = probe_overlay(path).get("codec_name", "").lower()
        if codec == OVERLAY_VIDEO_CODEC:
            already += 1
        else:
            to_carry.append(path)

    stamps = stamp_files(project)
    print(f"{len(movs)} overlay file(s): {already} already "
          f"{OVERLAY_VIDEO_CODEC}, {len(to_carry)} to transcode")
    print(f"{len(stamps)} recorded stamp(s) to move "
          f"{SUPERSEDED_CARRIAGE} -> {OVERLAY_CARRIAGE}")
    if not args.apply:
        print("\nreporting only; pass --apply to rewrite")
        return 0

    before = after = 0
    changed = failed = 0
    for index, path in enumerate(to_carry, 1):
        record = transcode_in_place(path)
        before += record["before"]
        after += record["after"] or record["before"]
        if record["error"]:
            failed += 1
            print(f"  [{index}/{len(to_carry)}] FAILED {path}: "
                  f"{record['error']}", file=sys.stderr)
            continue
        if record["changed"]:
            changed += 1
        print(f"  [{index}/{len(to_carry)}] {os.path.basename(path)} "
              f"{record['before']:,} -> {record['after']:,}")

    if failed:
        # A stamp says the artefact is current. Moving one over a file
        # that is still the old codec would be a lie about what is on
        # disk, so the stamps stay where they are until every file
        # carried.
        print(f"\n{failed} file(s) did not transcode; the carriage stamps "
              f"are NOT moved, because a stamp over a file that did not "
              f"carry claims an artefact that is not there.",
              file=sys.stderr)
        return 1

    moved = restamp_carriage(stamps, SUPERSEDED_CARRIAGE, OVERLAY_CARRIAGE)
    print(f"\ntranscoded {changed} file(s): {before / 1e9:.3f} GB -> "
          f"{after / 1e9:.3f} GB ({100.0 * after / max(before, 1):.1f}%)")
    print(f"restamped {len(moved['changed'])}, left "
          f"{len(moved['skipped'])} carrying another carriage, "
          f"{len(moved['failed'])} failed")
    return 1 if moved["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
