"""A disposable pipeline project for the live rebuild rows (AXI-10/12).

The small-edit rebuild rows (`rebuild_after_edit`, and the `add_logo`
survives-rebuild proof) currently fail with ``Unknown project 'Ren
Qualification'``: `rebuild_reels_in_project` resolves a bare name through
the project registry, and the qualification project is not a pipeline
project. The same call accepts an absolute project folder, so the fix is
a disposable pipeline project materialized per run: `project.yaml`
pointing at the shared Resolve project, a timeline-kind transcript, and
a valid v2 proposal with one APPROVED moment over the deterministic
rebuild seed. The build stages its own reel timelines (never the shared
qual timeline) under the disposable stem, and the broker script deletes
exactly those timelines afterwards, so the shared project is left
byte-identical.

What this proves: the production rebuild path executes end to end and
preserves the unrelated qualification timeline. What it does not prove:
that a rebuild incorporates a user's edit into the same timeline -
that needs pipeline integration beyond a disposable folder, and the row
evidence says so.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

PROJECT_NAME = "Ren Qualification"
TIMELINE_NAME = "fm-scratch qualify"
DISPOSABLE_SLUG = "axc-disposable"
DISPOSABLE_REEL_STEM = "Reel 01 - axc-disposable"

QUAL_POOL_DIR = Path(
    "/Users/prajwal/.local/share/vep/qualification/622307e41719")


def materialize(parent: Path | str | None = None) -> Path:
    """Create the disposable project and return its folder."""
    root = Path(tempfile.mkdtemp(prefix="axc-disposable-project-",
                                 dir=str(parent) if parent else None))
    (root / "project.yaml").write_text(
        "project_name: axc_disposable_rebuild\n"
        "resolve:\n"
        f"  project_name: {PROJECT_NAME}\n"
        f"  timeline_name: {TIMELINE_NAME}\n"
        "pipeline:\n"
        "  delivery_format: horizontal_1920x1080\n",
        encoding="utf-8")
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(str(root))
    transcript_path = layout.write_path(
        Area.SCRATCH, "timeline_transcript", "transcript.json")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps({
        "derived_from": {"kind": "timeline",
                         "timeline": TIMELINE_NAME,
                         "project": PROJECT_NAME},
        "fps": 24,
        "frame_size": [1920, 1080],
        "duration_frames": 48,
        "segments": [{
            "speaker": "disposable",
            "text": "disposable rebuild-row speech",
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "source_file": "qual_a.mp4",
            "source_start": 0.0,
            "source_end": 2.0,
            "resolve_item_id": "",
            "words": [
                {"word": word, "start": round(index * 0.25, 2),
                 "end": round(index * 0.25 + 0.2, 2), "timed": True}
                for index, word in enumerate(
                    ["disposable", "rebuild", "row", "speech",
                     "covers", "two", "seconds", "here"])
            ],
        }],
        "clips": [{
            "name": "qual_a.mp4",
            "source_path": str(QUAL_POOL_DIR / "qual_a.mp4"),
            "source_file": "qual_a.mp4",
            "source_in": 0, "source_out": 48,
            "record_in": 0, "record_out": 48,
            "transform": {"Pan": 40.0, "Tilt": 0.0,
                          "ZoomX": 1.0, "ZoomY": 1.0},
        }],
        "words": [],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    proposal_path = layout.write_path(Area.REVIEW, "reel_proposals_v2.json")
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps({
        "format": "reel_proposal/1",
        "moments": [{
            "number": 1,
            "slug": DISPOSABLE_SLUG,
            "reason": ("AXI-12 disposable rebuild-row project: one "
                       "deterministic A-roll moment over the seeded edit."),
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "approval": "approved",
            "approved_by": "axc-disposable-project",
            "source_spans": [{"source_file": "qual_a.mp4",
                              "source_start": 0.0, "source_end": 2.0}],
        }],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root


def check(project_dir: Path | str) -> list[str]:
    """Offline gates: every checkable precondition of the rebuild entry."""
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_proposal import read_proposal
    from library.tools.source_transcript import refuse_build_from_source

    errors: list[str] = []
    root = Path(str(project_dir))
    if not (root.is_absolute() and root.is_dir()):
        return [f"{root} is not an absolute project folder"]
    try:
        import yaml
        config = yaml.safe_load(
            (root / "project.yaml").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - gate reports, never raises
        return [f"project.yaml unreadable: {exc}"]
    resolve = (config or {}).get("resolve", {}) or {}
    if resolve.get("project_name") != PROJECT_NAME:
        errors.append("resolve.project_name must name the shared "
                      "qualification project")
    if resolve.get("timeline_name") != TIMELINE_NAME:
        errors.append("resolve.timeline_name must name the private "
                      "qualification timeline")
    from library.tools.delivery_format import delivery_format_name
    if delivery_format_name(str(root)) != "horizontal_1920x1080":
        errors.append("pipeline.delivery_format must resolve landscape "
                      "like the qualification master")
    layout = ProjectLayout(str(root))
    proposal_path = layout.read_path(Area.REVIEW, "reel_proposals_v2.json")
    try:
        moments = read_proposal(proposal_path)
    except Exception as exc:  # noqa: BLE001 - gate reports, never raises
        errors.append(f"proposal unreadable: {exc}")
        moments = []
    if moments:
        if len(moments) != 1 or moments[0].approval.value != "approved":
            errors.append("proposal must carry exactly one APPROVED moment")
        if not moments[0].source_spans:
            errors.append("the approved moment names no source spans")
    transcript_path = layout.read_path(
        Area.SCRATCH, "timeline_transcript", "transcript.json")
    try:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - gate reports, never raises
        errors.append(f"transcript unreadable: {exc}")
        transcript = {}
    if transcript:
        try:
            refuse_build_from_source(transcript)
        except Exception as exc:  # noqa: BLE001 - gate reports
            errors.append(f"transcript refused: {exc}")
        segments = transcript.get("segments")
        required = ("speaker", "text", "timeline_start", "timeline_end",
                    "source_file", "source_start", "source_end",
                    "resolve_item_id")
        if not isinstance(segments, list) or not segments:
            errors.append("transcript carries no segments row the "
                          "mic-bleed merge reads")
        elif any(not isinstance(row, dict) or any(key not in row
                                                  for key in required)
                 for row in segments):
            errors.append("transcript segments lack the keys "
                          "resolve_document_mic_bleed reads")
    if "Ren Qualification" in str(root) and "axc-disposable" not in str(root):
        errors.append("disposable folder must not masquerade as a real project")
    return errors


def main() -> int:
    """Materialize to temp and run the offline gates (no Resolve)."""
    import sys
    root = materialize()
    problems = check(root)
    print(json.dumps({"project": str(root), "errors": problems},
                     indent=2))
    if "--keep" not in sys.argv[1:]:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
