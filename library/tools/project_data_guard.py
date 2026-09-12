"""The pipeline repo never holds the captain's project data.

Captain's ruling, 2026-09-10: project data and edits are never committed
to the pipeline repo, because the pipeline is a product; only
generalizable lessons belong there.  PR 968 proved a remembered rule is
no rule - it committed 23 files of live Resolve state under `captures/`
(the live state of nine timelines, two audio transcripts, two frame
stills, overlay measurements) and nothing refused it.

This module is the enforced half.  It keys on two things a future lane
cannot trivially name around:

1. CAPTURED-TIMELINE SHAPES - the JSON structures only a live Resolve
   read produces.  No pipeline output has these shapes: the pipeline
   writes catalogs, step outputs and manifests, never a timeline read.
   A renamed file keeps its shape, so renaming evades nothing.
2. THE `captures/` PATH CONVENTION - the drop-zone directory PR 968
   created at the repo root.  Tooling lives in `library/tools/`; a
   `captures/` directory in this repo is project data by definition.
   (The stills are binary, so no shape can catch them - the path rule
   is what covers them.)

Run it as a refusing check - locally, in a hook, or in CI:

    python -m library.tools.project_data_guard [--root .]

Exit 0 when clean, 1 with the offending paths listed when not.
`tests/test_project_data_guard.py` pins every refusal below.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: The drop-zone directory PR 968 created.  Nothing under it belongs to
#: the product - capture tooling lives in `library/tools/`.
DROP_ZONE_DIRS = ("captures/",)

#: Directories never scanned (vcs, caches, environments).
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
})


def capture_shape_reason(doc) -> str:
    """Why a parsed JSON document is project data, or "" when it is not.

    Every predicate is a conjunction of keys that only a live Resolve
    read produces together.  Single keys (`tracks`, `items`,
    `segments`) appear in legitimate pipeline JSON, so none of them
    refuses on its own.
    """
    if isinstance(doc, dict):
        # capture_timeline.py output: the full live state of one
        # timeline.  `timeline_settings` appears nowhere else in this
        # repo - no pipeline step writes it.
        if ("timeline_settings" in doc and "tracks" in doc
                and "track_counts" in doc):
            return ("live timeline-state capture "
                    "(timeline_settings + tracks + track_counts)")
        # The earlier per-timeline item capture (nine reels + master):
        # items carrying both the source file and the clip's own
        # markers.  `clip_markers` is written only by the serializer
        # into project repos - never into pipeline JSON.
        items = doc.get("items")
        if ("markers" in doc and isinstance(items, list) and items
                and isinstance(items[0], dict)
                and "source_file" in items[0]
                and "clip_markers" in items[0]):
            return ("per-timeline item capture "
                    "(items with source_file + clip_markers)")
        # The pull inventory: which timelines were read and where each
        # timeline's marker pull was written in the PROJECT repo.
        # `pull_file` appears in no pipeline JSON.
        timelines = doc.get("timelines")
        if ("captured_at" in doc and isinstance(timelines, list)
                and any(isinstance(t, dict) and "pull_file" in t
                        for t in timelines)):
            return "capture inventory (captured_at + timelines[].pull_file)"
        # The retired measure_overlay_draw_positions.py output:
        # per-overlay ink geometry against the caption intent.  The
        # tool is gone (it computed its screen rows FROM the draw-gain
        # constant, so it could never contradict it), but its files are
        # still on disk in project folders, and `draw_gain` beside
        # `overlays` is that measurement and nothing else.
        if "draw_gain" in doc and "overlays" in doc:
            return "overlay draw-position measurement (draw_gain + overlays)"
        # whisperX transcript of a built reel's audio: segments plus
        # the word alignment.  No pipeline step writes `word_segments`.
        if ("segments" in doc and "word_segments" in doc
                and isinstance(doc["segments"], list)
                and isinstance(doc["word_segments"], list)):
            return "word-aligned audio transcript (segments + word_segments)"
        return ""
    if isinstance(doc, list):
        # capture_fusion_comps.py output: every comp tool-by-tool.
        # `comps` holding tools with image dimensions is that dump.
        if doc and isinstance(doc[0], dict) and "comps" in doc[0]:
            comps = doc[0]["comps"]
            if isinstance(comps, list) and comps \
                    and isinstance(comps[0], dict) \
                    and "tools" in comps[0]:
                return "fusion comp dump (list[].comps[].tools)"
        return ""
    return ""


def _is_drop_zone(rel: str) -> bool:
    return rel == "captures" or rel.startswith(DROP_ZONE_DIRS)


def scan_repo(root: str | Path) -> list[tuple[str, str]]:
    """Every project-data violation under root, as (relpath, reason).

    Walks the working tree (tracked or not - an uncommitted drop is
    still a drop).  Unparseable JSON is not project data by shape and
    is left alone; a renamed capture keeps its shape and is caught.
    """
    root = Path(root)
    violations: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in Path(rel).parts):
            continue
        if _is_drop_zone(rel):
            violations.append(
                (rel, "inside captures/ - the project-data drop zone; "
                      "tooling lives in library/tools/"))
            continue
        if path.suffix.lower() != ".json":
            continue
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except (ValueError, UnicodeError, OSError):
            continue
        reason = capture_shape_reason(doc)
        if reason:
            violations.append((rel, reason))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse a tree carrying the captain's project data.")
    parser.add_argument("--root", default=".",
                        help="Repo root to scan (default: cwd).")
    args = parser.parse_args(argv)
    violations = scan_repo(args.root)
    if not violations:
        print("project-data guard: clean")
        return 0
    print("project-data guard: REFUSED - "
          "the captain's project data does not belong in the pipeline repo:")
    for rel, reason in violations:
        print(f"  {rel}: {reason}")
    print("Move it to the project repo beside the reels it describes; "
          "only generalizable lessons belong here.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
