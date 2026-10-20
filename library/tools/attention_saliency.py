"""Attention saliency: M3e (`saliency.json`) of the per-source footage
memory (`docs/SOURCE_MEMORY.md`).

Where a viewer's eye goes, per M2 sample: Apple Vision's
`VNGenerateAttentionBasedSaliencyImageRequest`
(`attention_saliency_helper.swift` beside this file) returns the
salient-object boxes it reads off its 68x68 attention heatmap. This is
the saliency lane of the multimodal-understanding plan (MU-13,
`data/vep-plan-multimodal-understanding/report.md` §3 A10 in firstmate's
home); its reader is `overlay_keep_clear`, which reports the overlays a
build placed over a salient box or a person region (M3d).

**Time.** One sample per M2 frame, at the M2 frame's own `t`. A sample
the helper could not read carries `boxes: null`, never an empty list
(an empty list means "measured, nothing salient").

**Space.** Boxes are image-normalised, top-left origin, like M3 and
M3d; the helper flips Vision's bottom-left boxes once.

**Only the boxes are kept.** The heatmap has no reader: the keep-clear
check asks "is this overlay over what the eye goes to", which the boxes
answer, and a declared output has a reader (AGENTS.md 10.1).

**The model is fed the M2 thumbnail, by measurement.** On 30 real frames
of the two geo-podcast cameras (20 evenly over LCATL0013, 10 over
LC4932, 2026-10-12), the union of each frame's salient boxes from the
384 x 216 M2 thumbnail against the same frame decoded at 1920 px: median
IoU 0.912, p10 0.542; 1152 px against 1920: median 0.973, p10 0.960.
Every input returned one box on every frame. Looked at, the
disagreements are one shape: on 6 of 30 frames the thumbnail box holds
the speaker's head and torso (bottom edge ~0.55) where the larger
inputs extend down over the laptop and the table (~0.83), and on one
LC4932 frame the 1920 px box widens to take in a hand entering from the
left edge. Neither is wrong for "where does the eye go"; the body below
the shoulders is already M3d's person region, which the keep-clear
check reads beside this lane. So the lane reads the M2 thumbnails M2
already decoded - ~3.4 ms/frame warm against ~4.3 at 1152 px, and no
second decode, which is half of M3d's wall time
(`docs/benchmarks/2026-10-11-person-regions-m3d.md`).

    python3 -m library.tools.attention_saliency build <project>
    python3 -m library.tools.attention_saliency status <project>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from library.tools import source_memory

M3E_STATUS_MEASURED = "measured"
M3E_METHOD = "attention-saliency-v1-m2"
"""The slot version. `footage_analysis.slot_is_fresh` rebuilds a record
stamped with anything else, so a change to what the record means (the
input frames, the box convention) bumps this."""

SWIFT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "attention_saliency_helper.swift")
HELPER_NAME = "attention_saliency_helper"

BATCH_FRAMES = 1000
"""Frames per helper process: cold start once per batch, and a killed
helper loses at most one batch."""


def _helper() -> str:
    from library.tools import vision_measure
    helper, reason = vision_measure.ensure_helper(SWIFT_SOURCE, HELPER_NAME)
    if helper is None:
        raise RuntimeError(f"attention-saliency helper unavailable: {reason}")
    return helper


def _measure(paths: list[str], helper: str) -> list[dict]:
    from library.tools import vision_measure
    docs: list[dict] = []
    for first in range(0, len(paths), BATCH_FRAMES):
        docs.extend(vision_measure.measure_frames(
            paths[first:first + BATCH_FRAMES], helper))
    return docs


def assemble(times: list[float], docs: list[dict | None]) -> list[dict]:
    """Helper documents -> one sample per M2 time. A doc that is missing
    or carries an error is `boxes: None` - not measured, never empty."""
    samples = []
    for t, doc in zip(times, docs):
        if doc is None or doc.get("error") or doc.get("boxes") is None:
            samples.append({"t": t, "boxes": None})
            continue
        samples.append({"t": t, "boxes": [
            {"bbox": [float(v) for v in box["bbox"]],
             "confidence": float(box["confidence"])}
            for box in doc["boxes"]]})
    return samples


def build_source_saliency(source_file: str, content_digest: str,
                          root: Path | None = None) -> dict:
    """M3e for one source. Refuses (raises) without a fresh M2."""
    started = time.perf_counter()
    m2 = source_memory.read_m2(content_digest, root)
    if (m2 is None or not source_memory.is_fresh(m2, source_file)
            or not source_memory.m2_frames_present(content_digest, m2, root)):
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no fresh M2 frame sample; "
            f"run `python3 -m library.tools.source_memory frames` first")
    frames = m2["frames"]
    times = [f["t"] for f in frames]
    paths = [source_memory.frame_abspath(content_digest, f, root)
             for f in frames]
    helper = _helper()
    t0 = time.perf_counter()
    docs = _measure(paths, helper) if paths else []
    vision_seconds = time.perf_counter() - t0
    samples = assemble(times, docs)
    measured = sum(1 for s in samples if s["boxes"] is not None)
    record = {
        "content_digest": content_digest,
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3E_STATUS_MEASURED,
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(times),
        "samples_measured": measured,
        "samples": samples,
        "instrument": {
            "method": M3E_METHOD,
            "request": "VNGenerateAttentionBasedSaliencyImageRequest",
            "input": "m2-thumbnail",
            "input_pixels": next((d.get("pixels") for d in docs
                                  if d and d.get("pixels")), None),
            "vision_seconds": round(vision_seconds, 1),
            "ms_per_frame": round(1000.0 * vision_seconds
                                  / max(1, measured), 1),
            "wall_seconds": round(time.perf_counter() - started, 1),
        },
    }
    sdir = source_memory.source_dir(content_digest, root)
    source_memory.write_json(sdir / source_memory.SLOT_SALIENCY, record)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": content_digest, "frames": len(times),
            "samples_measured": measured,
            "wall_seconds": record["instrument"]["wall_seconds"]}


@source_memory._project_scoped
def build_project_saliency(project_folder: str,
                           clip_ids: list[str] | None = None,
                           root: Path | None = None) -> dict:
    """M3e for every catalog source with media, one source at a time.
    Same reporting shape as `person_regions.build_project_regions`."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    results, failed, seen = [], [], set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "media-offline"})
            continue
        digest, _ = source_memory.digest_for_clip(project_folder, clip,
                                                  recorded)
        if digest is None or digest in seen:
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "no content digest" if digest is None
                            else "reused"})
            continue
        seen.add(digest)
        try:
            account = build_source_saliency(path, digest, root)
        except Exception as exc:  # noqa: BLE001 - one source's failure
            # is recorded on its row; the rest of the catalog still runs.
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            failed.append(clip_id)
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def read_m3e(content_digest: str,
             root: Path | None = None) -> dict | None:
    """A digest's M3e record, or None when never measured."""
    path = source_memory.source_dir(content_digest, root) / \
        source_memory.SLOT_SALIENCY
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="attention_saliency",
        description="M3e: Apple Vision attention-saliency boxes over each "
                    "source's M2 thumbnails.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3e for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3e")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_saliency(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3e(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3e": "measured" if doc else "missing",
                     "samples_measured": doc["samples_measured"] if doc else 0,
                     "ms_per_frame": (doc["instrument"]["ms_per_frame"]
                                      if doc else None)})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
