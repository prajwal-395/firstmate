"""Delivery-tier person mattes: one matte per PLAYED frame, from Apple Vision.

The survey tier (M3d, `library/tools/person_regions.py`) says where each
person is at every M2 sample, about twice a second. That is enough to
answer "where is Craig", and not enough to draw with: a matte held for
half a second lags a subject who moves, so a title "behind" a walker
shows through their shoulder and a subject grade paints the wall they
just left. This is the delivery tier of the multimodal-understanding
plan (MU-10, `data/vep-plan-multimodal-understanding/report.md` §5.5 in
firstmate's home): the same Vision request, run on EVERY source frame a
`ClipUse` plays, with the person picked out of each frame's instances by
the measured identity M3d already carries.

**Which person.** A plan names a person through a region track: the
colourist's `subject_grades` entry cites a `region_exposure` row
(`<clip_id>/<region_track>`, `region_stats.basis_id`), and a behind
title names nobody, so it takes the one owned region track that plays
under it (`subject_track`). A region track is a person only when M3d
linked it to an M3b face track - an unassigned track (a hand whose owner
is out of frame, two faces in one mask) is never drawn as someone.

**Which instance, per frame.** Vision's instance index is per frame, so
it says nothing across frames. At each frame nearest an M3d sample of
the track, the instance holding that sample's FACE is the person - the
same majority-containment rule M3d assigns faces with
(`person_regions.FACE_INSIDE_MIN_FRACTION`), so the anchor is a
measured identity, not a position. Between anchors the choice is carried
frame to frame by mask IoU (`person_regions.TRACK_LINK_MIN_IOU`, the
cut-off measured between adjacent 2 Hz samples - adjacent 24 fps frames
overlap far more), forwards and backwards, and each frame takes the
choice from its nearer anchor. A frame no anchor reaches gets an EMPTY
matte and is counted (`frames_without_person`): the person is not there
that we can show, which is different from a held guess.

**The decode.** Source frames are decoded at the source's own frame
rate, by index, from the frame the played window starts on (plus the
frames back to the nearest anchor before it and on to the nearest after
it, which is what lets a short window with no M2 sample inside still be
anchored). The model sees at most `MODEL_WIDTH` pixels across: M3d
measured that a 1152 px input already matches a 1920 px one at median
IoU 0.991, so a 4K source is scaled for the model and the matte is
scaled back to the source's own size, which is what the consumers
(`behind_subject`, `subject_grade`) gate pixels at.

**Cost.** One Vision request per decoded frame, ~33-43 ms warm on this
Mac (M3d's measurement), so ~1 s of compute per played second at 24 fps
plus the decode. Heavy: compile runs it only for a ClipUse a plan names.

Measured against the held 2 Hz matte on a laterally moving subject:
`docs/benchmarks/2026-10-12-person-matte-delivery.md`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from library.tools import person_regions

DELIVERY_METHOD = "person-instance-mask-delivery-v1"

MODEL_WIDTH = 1920
"""The widest frame the model is fed. Mechanical: M3d measured 1152 px
already matching 1920 px (median IoU 0.991, module docstring of
`person_regions`), and a 4K frame costs four times the decode and
runs-encoding for no measured gain. A source narrower than this is fed
at its own size."""

ANCHOR_REACH_SECONDS = 2.5
"""How far outside the played window the decode extends to reach the
nearest anchor sample of the track. M2 samples I-frames, whose spacing
is the source's GOP (about 0.5-2 s on the captain's cameras); a window
whose nearest sample is further than this is not anchored from outside
and says so."""

ABSENT_REASONS = {
    "no_region_record": "the source has no M3d (person regions) record "
                        "measured",
    "no_person_region": "no region track M3d linked to a face track plays "
                        "in the window",
    "several_person_regions": "more than one owned region track plays in "
                              "the window, and the plan named none of "
                              "them",
    "region_track_unknown": "the region track the plan names is not in "
                            "the source's M3d record",
    "region_track_unowned": "the region track the plan names was never "
                            "linked to a face track, so it is nobody",
    "no_face_anchor": "no M3d sample of the track within reach of the "
                      "window carries the person's face",
    "vision_unavailable": "the Vision helper could not run here",
    "decode_failed": "the played source frames would not decode",
}
"""Every reason a delivery matte is not made. Complete: a caller
matching on `reason` never meets one not listed here."""


SAM_ROUTE_REASONS = frozenset({"no_region_record", "no_person_region",
                               "several_person_regions"})
"""The reasons that mean "no single person instance to draw here", for a
plan that named no region: step 1.06's SAM 2 face-seeded subject stays
the route for those clips, as it does for objects. Every other reason is
a person that cannot be drawn, and the caller says so instead of
quietly drawing someone else."""


class PersonMatteUnavailable(Exception):
    """No delivery matte for this person over this window, and why."""

    def __init__(self, reason: str, detail: str):
        if reason not in ABSENT_REASONS:
            raise ValueError(f"unlisted person-matte reason {reason!r}")
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ── Which region track is the person ────────────────────────────────


def _track_covers(track: dict, start: float, end: float) -> bool:
    return float(track["start"]) <= end and start <= float(track["end"])


def subject_track(doc: dict | None, start: float, end: float, *,
                  region_track: str | None = None) -> dict:
    """The M3d region track that is the subject over `[start, end]` source
    seconds.

    `region_track` is the plan's own naming (a cited `region_exposure`
    row); without one, the subject is the single owned track playing in
    the window. Raises `PersonMatteUnavailable` with the reason - the
    caller decides whether that means another route (no person here at
    all) or a drop (a named person that cannot be drawn).
    """
    if not doc:
        raise PersonMatteUnavailable(
            "no_region_record", "M3d was never measured for this source")
    tracks = {t["track_id"]: t for t in doc.get("tracks") or []}
    if region_track is not None:
        track = tracks.get(region_track)
        if track is None:
            raise PersonMatteUnavailable(
                "region_track_unknown",
                f"{region_track!r} is not one of the record's "
                f"{len(tracks)} region track(s)")
        if not track.get("face_track"):
            raise PersonMatteUnavailable(
                "region_track_unowned",
                f"{region_track!r} is unassigned "
                f"({track.get('unassigned')})")
        return track
    owned = [t for t in tracks.values()
             if t.get("face_track") and _track_covers(t, start, end)]
    if not owned:
        raise PersonMatteUnavailable(
            "no_person_region",
            f"no owned region track covers {start:.3f}-{end:.3f}s")
    if len(owned) > 1:
        raise PersonMatteUnavailable(
            "several_person_regions",
            f"{', '.join(t['track_id'] for t in owned)} all cover "
            f"{start:.3f}-{end:.3f}s")
    return owned[0]


def face_anchors(doc: dict, m3: dict, track: dict, start: float,
                 end: float, *, reach: float = ANCHOR_REACH_SECONDS
                 ) -> list[tuple[float, list[float]]]:
    """`(t, face_box)` for every M3d sample of `track` inside the window,
    plus the nearest one before and after it within `reach`, whose
    instance carries the track's own face. The face box is M3's, at that
    sample, normalised top-left - what the instance was assigned by."""
    owner = track.get("face_track")
    frames = (m3 or {}).get("frames") or []
    by_t = {round(float(f["t"]), 3): f for f in frames}
    rows = []
    for sample in doc.get("samples") or []:
        for inst in sample.get("instances") or []:
            if (inst.get("track") != track["track_id"]
                    or inst.get("unassigned")
                    or inst.get("face_track") != owner
                    or inst.get("face") is None):
                continue
            frame = by_t.get(round(float(sample["t"]), 3))
            faces = (frame or {}).get("faces") or []
            if inst["face"] < len(faces):
                rows.append((float(sample["t"]),
                             list(faces[inst["face"]]["box"])))
    inside = [r for r in rows if start <= r[0] <= end]
    before = [r for r in rows if start - reach <= r[0] < start]
    after = [r for r in rows if end < r[0] <= end + reach]
    return ([before[-1]] if before else []) + inside + (
        [after[0]] if after else [])


# ── Frames ──────────────────────────────────────────────────────────


def probe_video(source_file: str) -> dict:
    """The fields the decode needs off ffprobe: frame rate, size, and the
    colour tags the YUV->RGB conversion reads."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,r_frame_rate,color_space,color_range",
         "-of", "json", source_file],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=60, check=False)
    if proc.returncode != 0:
        raise PersonMatteUnavailable(
            "decode_failed", f"ffprobe refused {source_file}: "
            f"{proc.stderr.strip()[-200:]}")
    streams = (json.loads(proc.stdout or "{}").get("streams") or [])
    if not streams:
        raise PersonMatteUnavailable(
            "decode_failed", f"{source_file} carries no video stream")
    stream = streams[0]
    num, _, den = str(stream.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    if fps <= 0:
        raise PersonMatteUnavailable(
            "decode_failed", f"{source_file} states no frame rate")
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": fps, "color_space": stream.get("color_space") or "",
            "color_range": stream.get("color_range") or ""}


def yuv_conversion(probe: dict) -> str:
    """The `scale` options that turn the source's YUV into RGB the way
    Resolve reads it: the stream's own matrix and range where it states
    them, else Rec.709 for HD and Rec.601 below it, video range. A
    precomposite drawn with a different matrix than the picture under it
    shows its edge as a hue shift."""
    space = probe.get("color_space") or ""
    matrix = {"bt709": "bt709", "smpte170m": "smpte170m",
              "bt470bg": "bt470bg", "bt2020nc": "bt2020",
              "bt2020c": "bt2020"}.get(space)
    if matrix is None:
        matrix = "bt709" if probe["height"] >= 720 else "smpte170m"
    rng = "pc" if probe.get("color_range") == "pc" else "tv"
    return f"in_color_matrix={matrix}:in_range={rng}:out_range=pc"


def played_source_frames(source_in: float, frames: int, timeline_fps: float,
                         source_fps: float) -> list[int]:
    """The source frame index each played timeline frame shows.

    The window starts on the source frame its `source_in` rounds to (how
    the build places it) and advances at the source rate per timeline
    frame - one for one when the rates agree."""
    first = round(float(source_in) * source_fps)
    step = source_fps / float(timeline_fps)
    return [first + int(np.floor(t * step + 1e-6)) for t in range(frames)]


def decode_frames(source_file: str, first: int, count: int, probe: dict,
                  out_dir: str, *, width: int | None = None) -> list[str]:
    """Source frames `first .. first+count-1`, by index, as RGB PNGs.

    Seeks half a frame before `first` so the first frame out is exactly
    frame `first` at a constant rate; `width` scales (aspect kept, even
    height) when the caller wants a smaller frame than the source."""
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "f_%06d.png")
    scale = yuv_conversion(probe)
    if width and width < probe["width"]:
        vf = f"scale={width}:-2:{scale},format=rgb24"
    else:
        vf = f"scale={probe['width']}:{probe['height']}:{scale},format=rgb24"
    seek = max(0.0, (first - 0.5) / probe["fps"])
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{seek:.6f}",
         "-i", source_file, "-an", "-sn", "-frames:v", str(int(count)),
         "-vf", vf, "-fps_mode", "passthrough", pattern],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=max(120, count), check=False)
    paths = [pattern % (i + 1) for i in range(int(count))]
    if proc.returncode != 0 or not all(os.path.isfile(p) for p in paths):
        raise PersonMatteUnavailable(
            "decode_failed",
            f"{os.path.basename(source_file)} frames {first}-"
            f"{first + count - 1}: {proc.stderr.strip()[-200:] or 'short'}")
    return paths


def measure_instances(paths: Sequence[str], grid: Sequence[int]
                      ) -> list[list[np.ndarray] | None]:
    """Per frame, every person instance Vision finds as a bool mask on
    `grid` (w, h); None for a frame the helper could not read."""
    from library.tools import vision_measure

    helper, reason = vision_measure.ensure_helper(
        person_regions.SWIFT_SOURCE, person_regions.HELPER_NAME)
    if helper is None:
        raise PersonMatteUnavailable("vision_unavailable", reason or "")
    try:
        docs = vision_measure.measure_frames(
            list(paths), helper,
            extra_args=["--grid", str(grid[0]), str(grid[1])])
    except vision_measure.VisionUnavailable as exc:
        raise PersonMatteUnavailable("vision_unavailable", str(exc)) from exc
    out: list[list[np.ndarray] | None] = []
    for doc in docs:
        if not doc or doc.get("error"):
            out.append(None)
            continue
        masks = [person_regions.decode_runs(i["runs"], grid)
                 for i in doc.get("instances") or []]
        out.append([m for m in masks if m.any()])
    return out


# ── Picking the person out of each frame ────────────────────────────


def select_person(frames: Sequence[list[np.ndarray] | None],
                  anchors: dict[int, list[float]]) -> tuple[list, dict]:
    """Per frame, the index of the person's instance, or None.

    `anchors` maps a frame index to the person's face box there. An
    anchor frame takes the one instance holding the face; every other
    frame takes the instance its nearer anchor's choice links to by IoU,
    walking frame by frame. Returns `(choice, account)`.
    """
    n = len(frames)
    anchored: dict[int, int] = {}
    unresolved = []
    for f, box in sorted(anchors.items()):
        masks = frames[f] or []
        hits = [i for i, m in enumerate(masks)
                if person_regions.containment(box, m)
                > person_regions.FACE_INSIDE_MIN_FRACTION]
        if len(hits) == 1:
            anchored[f] = hits[0]
        else:
            unresolved.append({"frame": f, "instances_holding_face":
                               len(hits)})

    def walk(order):
        reached: list[tuple[int, int] | None] = [None] * n
        prev = None
        distance = 0
        for f in order:
            if f in anchored:
                prev, distance = frames[f][anchored[f]], 0
                reached[f] = (anchored[f], 0)
                continue
            masks = frames[f]
            if prev is None or not masks:
                prev = None
                continue
            ious = [person_regions.mask_iou(prev, m) for m in masks]
            best = int(np.argmax(ious))
            if ious[best] <= person_regions.TRACK_LINK_MIN_IOU:
                prev = None
                continue
            distance += 1
            prev = masks[best]
            reached[f] = (best, distance)
        return reached

    forward = walk(range(n))
    backward = walk(range(n - 1, -1, -1))
    choice: list[int | None] = []
    for fwd, bwd in zip(forward, backward):
        options = [o for o in (fwd, bwd) if o is not None]
        choice.append(min(options, key=lambda o: o[1])[0]
                      if options else None)
    return choice, {"anchor_frames": sorted(anchored),
                    "unresolved_anchors": unresolved,
                    "frames_without_person": sum(1 for c in choice
                                                 if c is None)}


# ── The matte ───────────────────────────────────────────────────────


def _to_source_size(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """A model-grid mask at the source's own (height, width)."""
    height, width = size
    if mask.shape == (height, width):
        return mask
    from PIL import Image
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    return np.asarray(image.resize((width, height), Image.BILINEAR)) >= 128


def write_person_matte(source_file: str, doc: dict, m3: dict, *,
                       out_dir: str, stem: str, source_in: float,
                       played_frames: int, timeline_fps: float,
                       region_track: str | None = None,
                       resolution: tuple | None = None,
                       content_digest: str | None = None) -> dict:
    """One delivery matte: a person's mask on every played frame.

    `source_in` is where the window starts in source seconds and
    `played_frames` how many timeline frames it plays at
    `timeline_fps`. Writes `<stem>_subject_matte_%05d.png` (L, 255 where
    the person is) plus a provenance sidecar into `out_dir`, the record
    shape `subject_grade.write_subject_matte` writes, so `behind_subject`
    and `subject_grade` read either route the same way.

    Raises `PersonMatteUnavailable` with the reason when there is no
    person to draw - the caller routes elsewhere or drops, by name.
    """
    probe = probe_video(source_file)
    size = (probe["height"], probe["width"])
    if resolution is not None and tuple(resolution) != size:
        raise PersonMatteUnavailable(
            "decode_failed",
            f"{os.path.basename(source_file)} decodes at {list(size)}, the "
            f"catalog states {list(resolution)} - refusing rather than "
            f"gating the wrong pixels")
    fps = probe["fps"]
    shown = played_source_frames(source_in, played_frames, timeline_fps, fps)
    start_s, end_s = shown[0] / fps, shown[-1] / fps
    track = subject_track(doc, start_s, end_s, region_track=region_track)
    anchors_t = face_anchors(doc, m3, track, start_s, end_s)
    if not anchors_t:
        raise PersonMatteUnavailable(
            "no_face_anchor",
            f"{track['track_id']} has no face sample within "
            f"{ANCHOR_REACH_SECONDS}s of {start_s:.3f}-{end_s:.3f}s")
    lo = min(shown[0], round(anchors_t[0][0] * fps))
    hi = max(shown[-1], round(anchors_t[-1][0] * fps))
    model_width = min(MODEL_WIDTH, probe["width"])
    with tempfile.TemporaryDirectory(prefix="ren-matte-") as scratch:
        paths = decode_frames(source_file, lo, hi - lo + 1, probe, scratch,
                              width=model_width)
        from PIL import Image
        with Image.open(paths[0]) as first_image:
            grid = first_image.size
        instances = measure_instances(paths, grid)
    anchors = {round(t * fps) - lo: box for t, box in anchors_t}
    choice, account = select_person(instances, anchors)
    if not account["anchor_frames"]:
        raise PersonMatteUnavailable(
            "no_face_anchor",
            f"{track['track_id']}: the face sample(s) at "
            f"{[round(t, 3) for t, _ in anchors_t]}s fall inside no single "
            f"instance on the decoded frame")

    os.makedirs(out_dir, exist_ok=True)
    from PIL import Image
    files = []
    held: dict[int, np.ndarray] = {}
    for t, frame_index in enumerate(shown):
        k = frame_index - lo
        if k not in held:
            pick = choice[k]
            if pick is None:
                held[k] = np.zeros(size, dtype=bool)
            else:
                held[k] = _to_source_size(instances[k][pick], size)
        path = os.path.join(out_dir, f"{stem}_subject_matte_{t:05d}.png")
        Image.fromarray(held[k].astype(np.uint8) * 255, mode="L").save(path)
        files.append(path)

    played = {f - lo for f in shown}
    record = {
        "clip_stem": stem,
        "object_id": None,
        "region_track": track["track_id"],
        "files": files,
        "frame_count": played_frames,
        "resolution": [size[0], size[1]],
        "provenance": {
            "created_by": "person_matte.write_person_matte",
            "method": DELIVERY_METHOD,
            "request": "VNGeneratePersonInstanceMaskRequest",
            "content_digest": content_digest,
            "region_track": track["track_id"],
            "face_track": track.get("face_track"),
            "named_by_plan": region_track is not None,
            "source_fps": fps,
            "timeline_fps": float(timeline_fps),
            "source_frames": [shown[0], shown[-1]],
            "decoded_frames": [lo, hi],
            "model_grid": list(grid),
            "anchor_samples": [round(t, 3) for t, _ in anchors_t],
            "anchor_frames": [lo + f for f in account["anchor_frames"]],
            "unresolved_anchors": account["unresolved_anchors"],
            "frames_without_person": sum(
                1 for k in played if choice[k] is None),
        },
    }
    sidecar = os.path.join(out_dir, f"{stem}_subject_matte.json")
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump({k: v for k, v in record.items() if k != "files"}
                  | {"file_count": len(files), "first_file": files[0],
                     "last_file": files[-1]}, handle, indent=2)
    return record


def person_matte_for_clip(project_folder: str, clip_id: str,
                          source_file: str, **kwargs: Any) -> dict:
    """`write_person_matte` for a catalog clip, reading its M3d and M3 by
    content digest out of the source memory. Raises
    `PersonMatteUnavailable("no_region_record")` when either is absent."""
    from library.tools import person_measurements, source_memory

    root = kwargs.pop("root", None)
    digest, _ = source_memory.digest_for_clip(
        project_folder, {"clip_id": clip_id, "source_file": source_file})
    doc = person_regions.read_m3d(digest, root) if digest else None
    m3 = person_measurements.read_m3(digest, root) if digest else None
    if doc is None or m3 is None:
        raise PersonMatteUnavailable(
            "no_region_record",
            f"{clip_id}: no {'M3d' if doc is None else 'M3'} record for "
            f"{os.path.basename(source_file)}")
    return write_person_matte(source_file, doc, m3,
                              content_digest=digest, **kwargs)


def has_person_regions(project_folder: str, clip_id: str,
                       source_file: str, root: Path | None = None) -> bool:
    """Whether the clip's source carries an M3d record at all - the
    question that decides between this route and SAM 2 (step 1.06)."""
    from library.tools import source_memory

    digest, _ = source_memory.digest_for_clip(
        project_folder, {"clip_id": clip_id, "source_file": source_file})
    return bool(digest) and person_regions.read_m3d(digest, root) is not None
