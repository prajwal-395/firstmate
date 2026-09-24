import json
import sys
import os
from pathlib import Path

# Add repo root to PYTHONPATH to ensure library imports work
repo_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(repo_root))

from library.tools.analysis.object_segmentation import get_segmenter
from library.tools.project_layout import Area, ProjectLayout

#: THE TRIGGER. This step segments ONLY the clips a matte-needing plan
#: names on this run - never every clip on every run (measured 6.1x
#: realtime and 58.3 MB for 807s at 2 fps on 001, so a full pass is a
#: cost every run would pay for mattes almost no run uses). A clip is
#: wanted when:
#:
#: * a `subject_grades` entry in `color_grade_spec` (step 5.01) names
#:   its clip_id - the Loader-matte path grades that subject; or
#: * a `behind_subject_overlays` segment (step 4.06) plays over a
#:   timeline span a placed picture clip covers - the Fusion comp puts
#:   that title under this clip's matte. Spans resolve to clips
#:   through `a_roll_assignments` and the `b_roll_assignments` /
#:   `b_roll_interjections` that cover them.
#:
#: Nothing planned, nothing segmented: the step records the empty
#: trigger and returns without loading the segmenter.


def _entry_clip_id(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    for key in ("source_clip_id", "clip_id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    content = entry.get("content") or {}
    if isinstance(content, dict):
        value = content.get("clip_id")
        if isinstance(value, str) and value:
            return value
    return ""


def _entry_span(entry: dict) -> tuple | None:
    try:
        start = float(entry.get("timeline_start"))
        end = float(entry.get("timeline_end"))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return (start, end)


def matte_trigger(color_grade_spec=None,
                  behind_subject_overlays=None,
                  a_roll_assignments=None,
                  b_roll_assignments=None,
                  b_roll_interjections=None) -> dict:
    """`{clip_id: [reasons]}` for the clips this run needs mattes for.

    Pure function of the plans - no footage, no segmenter - so the
    trigger is testable without a GPU and the step can report it
    before paying anything.
    """
    wanted: dict[str, list] = {}

    def _want(clip_id: str, reason: str):
        if clip_id:
            wanted.setdefault(clip_id, [])
            if reason not in wanted[clip_id]:
                wanted[clip_id].append(reason)

    for entry in ((color_grade_spec or {}).get("subject_grades") or []):
        _want((entry or {}).get("clip_id", ""),
              "subject_grade: step 5.01 grades this subject")

    placements = []
    for assignments in (a_roll_assignments or [],
                        b_roll_assignments or [],
                        b_roll_interjections or []):
        for entry in assignments or []:
            if not isinstance(entry, dict):
                continue
            span = _entry_span(entry)
            if span is None:
                continue
            placements.append((_entry_clip_id(entry), span))

    for segment in ((behind_subject_overlays or {}).get("segments")
                    or []):
        if not isinstance(segment, dict):
            continue
        span = _entry_span(segment)
        if span is None:
            continue
        seg_label = str(segment.get("segment_id")
                        or segment.get("index", "?"))
        start, end = span
        for clip_id, (clip_start, clip_end) in placements:
            if clip_start < end and start < clip_end:
                _want(clip_id,
                      f"behind_subject: segment {seg_label} plays "
                      f"over this clip")
    return wanted


def _box_map_or_none(raw):
    """An explicit face-box map, or None where none arrived.

    Raises TypeError on a mistyped map - a wrong-typed value silently
    falling back to temporal_index boxes would mask a producer bug.
    A helper rather than an inline guard so the contract reads as a
    type check, not as refusing without an optional input.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError(
            "face_boxes_by_clip must be a clip_id to box map, got "
            f"{type(raw).__name__}.")
    return raw


def face_boxes_from_temporal_index(temporal_index) -> dict:
    """`{clip_id: box}` seeded from what step 1.04 already measured.

    The `temporal_event_indices` state value is a per-clip SUMMARY -
    the full index, with the `face_presence` block, lives in the
    per-clip JSON each summary's `index_path` names. The first located
    face box of each clip (the shape issue #268 gave the seeder) is
    the seed. A clip with no located box, or whose index file is not
    on disk, is absent here, which reads as decline, never as blob
    seeding.
    """
    boxes = {}
    indices = temporal_index
    if isinstance(temporal_index, dict):
        indices = temporal_index.get("temporal_event_indices",
                                     temporal_index.get("indices", []))
    for entry in indices or []:
        if not isinstance(entry, dict):
            continue
        clip_id = entry.get("clip_id", "")
        if not clip_id or clip_id in boxes:
            continue
        presence = (entry.get("face_presence") or {})
        index_path = entry.get("index_path", "")
        if not presence and index_path and os.path.exists(index_path):
            try:
                with open(index_path, encoding="utf-8") as handle:
                    presence = (json.load(handle).get("face_presence")
                                or {})
            except (OSError, ValueError):
                presence = {}
        for box in presence.get("face_boxes") or []:
            if (isinstance(box, (list, tuple)) and len(box) == 4
                    and all(isinstance(v, (int, float)) for v in box)):
                boxes[clip_id] = [float(v) for v in box]
                break
    return boxes


def run_step(raw_footage_files: list, clip_catalog: list, output_dir: str,
             face_boxes_by_clip: dict = None,
             color_grade_spec: dict = None,
             behind_subject_overlays: dict = None,
             a_roll_assignments: list = None,
             b_roll_assignments: list = None,
             b_roll_interjections: list = None,
             temporal_index=None):
    """
    Run object segmentation ONLY on the clips the trigger names.

    `face_boxes_by_clip` maps clip_id to the normalized [x1, y1, x2, y2]
    first-frame face box. An explicit map wins; otherwise the boxes are
    read off `temporal_index` (step 1.04's `face_presence`, which runs
    upstream of this step on every run that reaches it). A clip with no
    box is honestly declined - the legacy blob seeding, which seeded
    person-less clips on arbitrary regions and lost them in a median
    3.5s, is gone: a matte for nobody is not a measurement.

    `raw_footage_files` is accepted and ignored: the catalog carries
    every path this step reads, and a declaration nothing reads is the
    failure `output_contract` exists to stop.
    """
    wanted = matte_trigger(
        color_grade_spec=color_grade_spec,
        behind_subject_overlays=behind_subject_overlays,
        a_roll_assignments=a_roll_assignments,
        b_roll_assignments=b_roll_assignments,
        b_roll_interjections=b_roll_interjections)

    catalog_ids = [c.get("clip_id", "") for c in (clip_catalog or [])]
    trigger_record = {
        "wanted_clip_ids": sorted(wanted),
        "reasons": {cid: reasons for cid, reasons in sorted(wanted.items())},
        "catalog_clips": len(catalog_ids),
    }

    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    if not wanted:
        return {
            "object_segmentation": [],
            "matte_trigger": trigger_record | {"segmented": []},
        }

    if face_boxes_by_clip is None:
        face_boxes_by_clip = face_boxes_from_temporal_index(temporal_index)

    segmenter = get_segmenter()
    results = []
    declined = []

    for clip in clip_catalog:
        clip_path = clip["path"]
        clip_id = clip["clip_id"]

        if clip_id not in wanted:
            continue
        if not os.path.exists(clip_path):
            print(f"WARNING: File not found {clip_path}", file=sys.stderr)
            continue

        # Face seeding is the ONLY seeding. A clip the trigger names
        # but no face box reaches is declined with the reason - a
        # behind_subject title over a person-less clip refuses at
        # compile time, and a subject grade without a subject drops
        # there; neither may fall back to blob seeding here.
        face_box = (face_boxes_by_clip or {}).get(clip_id)
        if face_box is None:
            declined.append({
                "clip_id": clip_id,
                "reason": "no_face_box",
                "detail": (
                    f"Clip {clip_id} is wanted "
                    f"({'; '.join(wanted[clip_id])}) but no face box "
                    f"reached the seeder - so it is declined rather "
                    f"than seeded on an arbitrary region."),
            })
            print(f"Declining {clip_path}: no face box", file=sys.stderr)
            continue

        print(f"Segmenting {clip_path}...", file=sys.stderr)
        try:
            seg_result = segmenter.segment_clip(
                clip_path, sample_fps=2.0,
                face_box=face_box, require_face=True)

            # Save the detailed masks and metadata to output_dir
            seg_result.save(str(out_dir_path))

            # Rename output to use clip_id instead of stem to prevent collisions
            stem = Path(clip_path).stem
            old_file = out_dir_path / f"{stem}_segmentation.json"
            new_file_name = f"{clip_id}_segmentation.json"
            new_file = out_dir_path / new_file_name
            if old_file.exists() and old_file != new_file:
                os.rename(old_file, new_file)

            # Add summary to step results
            results.append({
                "clip_id": clip_id,
                "video_path": clip_path,
                "segmentation_file": new_file_name,
                "object_count": len(seg_result.objects),
                "seed_note": seg_result.seed_note,
                "trigger": wanted[clip_id],
            })

        except Exception as e:
            print(f"ERROR: Failed to segment {clip_path}: {e}", file=sys.stderr)

    return {
        "object_segmentation": results,
        "matte_trigger": trigger_record | {
            "segmented": [r["clip_id"] for r in results],
            "declined": declined,
        },
    }

def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON input", "step": "1.06_object_segmentation"}))
        sys.exit(1)

    clip_catalog = input_data.get("clip_catalog")

    if not clip_catalog:
        print(json.dumps({
            "error": "Missing required input: clip_catalog",
            "step": "1.06_object_segmentation"
        }))
        sys.exit(1)

    # Optional opt-in to explicit face seeding (issue #268): clip_id to
    # normalized first-frame face box. Absent means the boxes are read
    # off temporal_index, which runs upstream of this step.
    face_boxes_by_clip = _box_map_or_none(
        input_data.get("face_boxes_by_clip"))

    # Where masks land is the layout owner's call, not this step's.
    # See library/tools/project_layout.py.
    project_folder = input_data.get("project_folder") or os.getcwd()
    output_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SEGMENTATION, step="object_segmentation"))

    result = run_step(input_data.get("raw_footage_files"),
                      clip_catalog, output_dir,
                      face_boxes_by_clip=face_boxes_by_clip,
                      color_grade_spec=input_data.get("color_grade_spec"),
                      behind_subject_overlays=input_data.get(
                          "behind_subject_overlays"),
                      a_roll_assignments=input_data.get("a_roll_assignments"),
                      b_roll_assignments=input_data.get("b_roll_assignments"),
                      b_roll_interjections=input_data.get(
                          "b_roll_interjections"),
                      temporal_index=input_data.get("temporal_index"))
    json.dump(result, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
