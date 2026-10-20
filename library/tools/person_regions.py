"""Person regions: M3d (`regions.json` + a mask sidecar) of the per-source
footage memory (`docs/SOURCE_MEMORY.md`).

Where exactly each person is, per M2 sample: Apple Vision's
`VNGeneratePersonInstanceMaskRequest` (`person_regions_helper.swift`
beside this file) segments every person instance, each instance gets an
inline summary a reader can answer geometry from without decoding a mask
(`bbox`, `centroid`, `interior_point`, `area`), and the instances are
linked across samples into REGION TRACKS. A region gets its identity
only through a measured link: the M3 face inside it, whose M3b face
track `event_spans.assign_face_tracks` already resolves. This is the
region-track contract of the multimodal-understanding plan (MU-05,
`data/vep-plan-multimodal-understanding/report.md` §5.2 in firstmate's
home); the Project Model projection and the query surface are later
items that read this slot.

**Time.** One sample per M2 frame, at the M2 frame's own `t` - the
shared frame grid every per-frame lane rides. A sample the decode did
not reach carries `instances: null`, never an empty list (an empty list
means "measured, nobody there").

**Space.** Summaries are image-normalised, top-left origin, like M3.
Masks are stored on the M2 thumbnail GRID (`grid`, e.g. 384 x 216), so
a later lane can read pixel statistics off the M2 frames with no
resampling; every summary is computed from the stored mask, so a reader
that decodes it gets exactly the geometry the summary states (the
interior point is inside the decoded mask, always).

**The model is NOT fed the M2 thumbnail, by measurement.** On 50 real
frames of the two geo-podcast cameras (LCATL0013, LC4932), masks from
the 384 px M2 thumbnails against masks from a 1920 px decode: median
IoU 0.934, p10 0.727, and 6 of 50 frames disagreed on the instance
count. Looked at, the 384 px masks swallow the glass table's
reflection of the subject and drop Craig entirely on two samples of
the hand window. A 768 px input still leaked the reflection on the
worst frames (median 0.983, p10 0.952); 1152 px did not (median 0.991,
p10 0.964 against 1920) at the same Vision cost. So M3d decodes its own
I-frame pass at `DECODE_WIDTH` = 1152 (`source_memory.extract_iframes`,
the M2 decoder unchanged, so the pts are M2's), into a temp dir it
deletes, and resamples each mask onto the M2 grid in the helper.
Numbers: `docs/benchmarks/2026-10-11-person-regions-m3d.md`.

**The codec** (`CODEC`): alternating run lengths over the row-major
grid, background first (`[3, 2, 5]` is 3 off, 2 on, 5 off). One mask
per instance per sample, all of a source's masks in one sidecar,
`regions.masks.<sha256>.json.gz`. The sha256 is of the UNCOMPRESSED
payload, so the name IS the content address: the gzip bytes are not,
because two Python builds' zlib compress the same masks to different
bytes (measured: identical masks from two runs of LCATL0013, same byte
count, different gzip hashes). The record names the file and its
sha256, and `read_mask` refuses a payload that does not hash to it.
Runs, not a bitmap: a seated person on a 384 x 216 grid is ~2 runs per
row, and a whole hour's sidecar is ~1.5 MB.

**Who a region is.** A face is INSIDE an instance when more than half
of the face box's grid cells are that instance's
(`FACE_INSIDE_MIN_FRACTION`); instances are disjoint, so a face is
inside at most one - the majority rule makes the answer unique by
construction, not by a tie-break. Per sample, an instance is assigned
the face track of the one face inside it, with the containment
fraction recorded as its confidence. Otherwise it is UNASSIGNED with a
named reason (`UNASSIGNED_REASONS`): no face inside (a body turned
away, or a hand whose owner is out of frame - the LCATL0013 t~60-90
case, which Vision does segment as a person instance at every input
size measured), two faces inside, or a face M3b never vouched for. A
region track takes a face track only when more than half of its
samples carry it; the share is recorded as `face_track_fraction`.
Never by position, never by the nearest track.

**Linking samples into tracks.** Vision's instance index is per frame
(it swaps between adjacent samples - seen on the geo-podcast frames),
so tracks are linked by mask IoU between adjacent measured samples,
one-to-one, best pair first, above `TRACK_LINK_MIN_IOU` - a cut-off
read off the measured distribution of adjacent-sample IoU, documented
where the constant is.

    bin/vep -m library.tools.heavy_work_lock run --owner <lane> -- \\
        python3 -m library.tools.person_regions build <project>
    python3 -m library.tools.person_regions status <project>
    python3 -m library.tools.person_regions window <project> \\
        --clip clip_002 --start 60 --end 90
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from library.tools import source_memory
from library.tools.project_model_masks import REGION_RUNS_CODEC as CODEC

M3D_STATUS_MEASURED = "measured"
M3D_METHOD = "person-instance-mask-v1"
"""The slot version. `footage_analysis.slot_is_fresh` rebuilds a record
stamped with anything else, so a change to what the record means (the
decode width, the codec, the linking rule) bumps this."""

SWIFT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "person_regions_helper.swift")
HELPER_NAME = "person_regions_helper"

DECODE_WIDTH = 1152
"""The width M3d decodes I-frames at for the model. Measured, module
docstring: 384 px (M2's own thumbnails) leaks the table reflection into
the mask; 1152 px matches a 1920 px decode at median IoU 0.991."""

# `CODEC` is imported above from the mask-kind registry
# (:mod:`library.tools.project_model_masks`), which is what reads these
# bytes back; the import keeps the ``pr.CODEC`` readers working while the
# tag is defined exactly once.
SIDECAR_PREFIX = "regions.masks."
SIDECAR_SUFFIX = ".json.gz"

BATCH_FRAMES = 1000
"""Frames per helper process: cold start once per batch, and a killed
helper loses at most one batch."""

FRAME_TIME_TOLERANCE = 0.002
"""Seconds. The decode reads the same keyframes with the same decoder
flags as M2, so its pts equal M2's to the millisecond M2 rounds to."""

FACE_INSIDE_MIN_FRACTION = 0.5
"""A face is inside an instance when MORE than this share of its box's
grid cells are the instance's. Majority, so that disjoint instances
can hold a face at most once - not a tuned number."""

TRACK_LINK_MIN_IOU = 0.2
"""Two instances at adjacent samples are one track when their masks'
IoU exceeds this. Read off the measured distribution on three
geo-podcast sources (LCATL0011, LCATL0013, LC4932; numbers in
`docs/benchmarks/2026-10-11-person-regions-m3d.md`): adjacent-sample
pairs carrying the SAME face track never fell below 0.294 (n = 18,179),
while 97.9% of pairs between a face-owned instance and anything else (a
faceless hand, another owner) sat at or below 0.1 (n = 8,326). 0.2 is
in that gap. The few other-owner pairs above it have the shape of the
same body on a sample whose face Vision missed - joining those is the
point of a track."""

UNASSIGNED_REASONS = {
    "no_face_inside": "no M3 face lies inside the instance (a body turned "
                      "away, or a limb whose owner is out of frame)",
    "several_faces_inside": "more than one M3 face lies inside the instance",
    "face_has_no_track": "the face inside was never assigned an M3b face "
                         "track",
    "no_majority_face_track": "no single face track holds more than half "
                              "of the region track's samples",
}


# ── The codec ───────────────────────────────────────────────────────


def encode_runs(mask: np.ndarray) -> list[int]:
    """A binary mask as alternating run lengths, row-major, background
    first. The inverse of `decode_runs`."""
    flat = np.asarray(mask, dtype=bool).ravel()
    if flat.size == 0:
        return [0]
    change = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    bounds = np.concatenate(([0], change, [flat.size]))
    runs = np.diff(bounds).tolist()
    return ([0] + runs) if flat[0] else runs


def decode_runs(runs: Sequence[int], grid: Sequence[int]) -> np.ndarray:
    """Alternating run lengths -> a `(h, w)` bool mask. Refuses runs that
    do not cover the grid exactly: a truncated mask is not a mask."""
    width, height = int(grid[0]), int(grid[1])
    lengths = np.asarray(runs, dtype=np.int64)
    if int(lengths.sum()) != width * height:
        raise ValueError(f"runs cover {int(lengths.sum())} cells, "
                         f"grid {width}x{height} has {width * height}")
    values = np.zeros(len(lengths), dtype=bool)
    values[1::2] = True
    return np.repeat(values, lengths).reshape(height, width)


# ── Geometry over one mask ──────────────────────────────────────────


def summarize(mask: np.ndarray) -> dict:
    """bbox, centroid, interior point and area of a non-empty mask, all
    image-normalised over the mask's own grid (cell centres).

    The interior point is `mask_seed.seed_point_in_mask` - the centre of
    the largest inscribed circle - so it is inside the mask for any
    shape, where a bbox or centroid on an L-shaped body is not.
    """
    from library.tools.mask_seed import seed_point_in_mask

    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    seed = seed_point_in_mask(mask)
    return {
        "bbox": [round(xs.min() / width, 4), round(ys.min() / height, 4),
                 round((xs.max() + 1) / width, 4),
                 round((ys.max() + 1) / height, 4)],
        "centroid": [round((xs.mean() + 0.5) / width, 4),
                     round((ys.mean() + 0.5) / height, 4)],
        "interior_point": [round((seed.x + 0.5) / width, 4),
                           round((seed.y + 0.5) / height, 4)],
        "area": round(float(mask.sum()) / mask.size, 5),
    }


def box_cells(box: Sequence[float], grid: Sequence[int]
              ) -> tuple[slice, slice]:
    """The grid cells whose centres lie inside a normalised box, as
    `(rows, cols)` slices; a box smaller than a cell keeps the one cell
    its centre falls in, so no face is too small to be asked about."""
    width, height = int(grid[0]), int(grid[1])
    x1, y1, x2, y2 = box

    def span(lo: float, hi: float, n: int) -> slice:
        first = max(0, int(np.ceil(lo * n - 0.5)))
        last = min(n - 1, int(np.floor(hi * n - 0.5)))
        if last < first:
            centre = min(n - 1, max(0, int((lo + hi) / 2.0 * n)))
            return slice(centre, centre + 1)
        return slice(first, last + 1)

    return span(y1, y2, height), span(x1, x2, width)


def containment(box: Sequence[float], mask: np.ndarray) -> float:
    """The share of a face box's grid cells that are in `mask`."""
    rows, cols = box_cells(box, (mask.shape[1], mask.shape[0]))
    cells = mask[rows, cols]
    return float(cells.mean()) if cells.size else 0.0


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int(np.logical_or(a, b).sum())
    return int(np.logical_and(a, b).sum()) / union if union else 0.0


# ── Assembly (pure: helper documents in, record pieces out) ─────────


def assign_sample(faces: Sequence[dict], face_tracks: Sequence[str | None],
                  masks: Sequence[np.ndarray]) -> tuple[list[dict], list[int]]:
    """Per instance, its face assignment; per face, how many instances it
    is inside.

    Returns `(per_instance, inside_counts)`: `per_instance[i]` is
    `{face, face_track, containment}` or `{unassigned, ...}`;
    `inside_counts[f]` the number of instances face `f` is inside (0 or
    1, since instances are disjoint - counted, not assumed).
    """
    inside: list[list[tuple[int, float]]] = [[] for _ in masks]
    inside_counts = []
    for f, face in enumerate(faces):
        count = 0
        for i, mask in enumerate(masks):
            share = containment(face["box"], mask)
            if share > FACE_INSIDE_MIN_FRACTION:
                inside[i].append((f, share))
                count += 1
        inside_counts.append(count)
    out = []
    for hits in inside:
        if not hits:
            out.append({"unassigned": "no_face_inside"})
            continue
        if len(hits) > 1:
            out.append({"unassigned": "several_faces_inside",
                        "faces": [f for f, _ in hits]})
            continue
        face, share = hits[0]
        entry = {"face": face, "containment": round(share, 3),
                 "face_track": face_tracks[face]}
        if face_tracks[face] is None:
            entry["unassigned"] = "face_has_no_track"
        out.append(entry)
    return out, inside_counts


def adjacent_pairs(samples_masks: Sequence[list[np.ndarray] | None]
                   ) -> list[tuple[int, int, int, int, float]]:
    """Every instance pair between adjacent MEASURED samples, with IoU:
    `(sample_a, inst_a, sample_b, inst_b, iou)`. A sample with
    `None` (not decoded) breaks adjacency - no track bridges a hole."""
    pairs = []
    for s in range(1, len(samples_masks)):
        prev, cur = samples_masks[s - 1], samples_masks[s]
        if prev is None or cur is None:
            continue
        for a, ma in enumerate(prev):
            for b, mb in enumerate(cur):
                pairs.append((s - 1, a, s, b, mask_iou(ma, mb)))
    return pairs


def link_tracks(samples_masks: Sequence[list[np.ndarray] | None],
                min_iou: float = TRACK_LINK_MIN_IOU
                ) -> list[list[int] | None]:
    """Per sample, per instance, a track number. Adjacent samples are
    matched one-to-one, highest IoU first, above `min_iou`; an instance
    left unmatched starts a new track."""
    tracks: list[list[int] | None] = []
    next_id = 0
    by_sample: dict[int, list[tuple[int, int, int, int, float]]] = {}
    for pair in adjacent_pairs(samples_masks):
        by_sample.setdefault(pair[2], []).append(pair)
    for s, masks in enumerate(samples_masks):
        if masks is None:
            tracks.append(None)
            continue
        row: list[int | None] = [None] * len(masks)
        used_prev = set()
        for _sa, a, _sb, b, iou in sorted(by_sample.get(s, []),
                                          key=lambda p: -p[4]):
            if iou <= min_iou or row[b] is not None or a in used_prev:
                continue
            row[b] = tracks[s - 1][a]
            used_prev.add(a)
        for b, value in enumerate(row):
            if value is None:
                row[b] = next_id
                next_id += 1
        tracks.append(row)  # type: ignore[arg-type]
    return tracks


def track_owner(entries: Sequence[dict]) -> dict:
    """A region track's face track: the one more than half its samples
    carry, else unassigned with the reason."""
    counts = Counter(e["face_track"] for e in entries
                     if e.get("face_track") and not e.get("unassigned"))
    if counts:
        owner, n = counts.most_common(1)[0]
        if n * 2 > len(entries):
            return {"face_track": owner,
                    "face_track_fraction": round(n / len(entries), 3)}
        return {"face_track": None, "unassigned": "no_majority_face_track",
                "face_track_fraction": round(n / len(entries), 3),
                "face_tracks_seen": sorted(counts)}
    reasons = Counter(e.get("unassigned") for e in entries)
    return {"face_track": None,
            "unassigned": reasons.most_common(1)[0][0] or "no_face_inside"}


def assemble(times: Sequence[float],
             docs: Sequence[dict | None],
             m3_frames: Sequence[dict],
             face_tracks: Sequence[Sequence[str | None]],
             grid: Sequence[int]) -> dict:
    """Helper documents -> `{samples, tracks, masks, faces}`.

    `docs[s]` is the helper's document for M2 sample `s` (None when not
    decoded); `m3_frames[s]` / `face_tracks[s]` are M3's faces at the
    same sample and their M3b tracks. `masks` is the flat list of run
    arrays the sidecar holds, indexed by each instance's `mask`.
    """
    samples_masks: list[list[np.ndarray] | None] = []
    samples_runs: list[list[list[int]] | None] = []
    for doc in docs:
        if doc is None or doc.get("error"):
            samples_masks.append(None)
            samples_runs.append(None)
            continue
        runs = [inst["runs"] for inst in doc.get("instances") or []]
        masks = [decode_runs(r, grid) for r in runs]
        keep = [i for i, m in enumerate(masks) if m.any()]
        samples_masks.append([masks[i] for i in keep])
        samples_runs.append([runs[i] for i in keep])
    track_rows = link_tracks(samples_masks)

    samples, flat_runs = [], []
    per_track: dict[int, list[dict]] = {}
    track_times: dict[int, list[float]] = {}
    faces_with_track = inside_one = inside_none = inside_several = 0
    for s, (t, masks) in enumerate(zip(times, samples_masks)):
        if masks is None:
            samples.append({"t": t, "instances": None})
            continue
        faces = m3_frames[s]["faces"]
        assigned, inside_counts = assign_sample(faces, face_tracks[s], masks)
        for f, count in enumerate(inside_counts):
            if face_tracks[s][f] is None:
                continue
            faces_with_track += 1
            if count == 1:
                inside_one += 1
            elif count == 0:
                inside_none += 1
            else:
                inside_several += 1
        instances = []
        for i, (mask, entry) in enumerate(zip(masks, assigned)):
            track = track_rows[s][i]
            row = {"track": track, **summarize(mask),
                   "mask": len(flat_runs), **entry}
            flat_runs.append(samples_runs[s][i])
            per_track.setdefault(track, []).append(row)
            track_times.setdefault(track, []).append(t)
            instances.append(row)
        samples.append({"t": t, "instances": instances})

    tracks, names = [], {}
    for number in sorted(per_track):
        names[number] = f"region_{len(names) + 1:03d}"
        tracks.append({"track_id": names[number],
                       "start": track_times[number][0],
                       "end": track_times[number][-1],
                       "samples": len(per_track[number]),
                       **track_owner(per_track[number])})
    for sample in samples:
        for inst in sample["instances"] or []:
            inst["track"] = names[inst["track"]]
    return {
        "samples": samples,
        "tracks": tracks,
        "masks": flat_runs,
        "faces": {
            "with_face_track": faces_with_track,
            "inside_exactly_one_instance": inside_one,
            "inside_no_instance": inside_none,
            "inside_several_instances": inside_several,
            "fraction_inside_exactly_one": (
                round(inside_one / faces_with_track, 4)
                if faces_with_track else None),
        },
    }


# ── The sidecar ─────────────────────────────────────────────────────


def sidecar_payload(masks: list[list[int]], grid: Sequence[int]) -> bytes:
    """The sidecar's content - compact JSON - which its name hashes."""
    return json.dumps({"codec": CODEC, "grid": list(grid), "masks": masks},
                      separators=(",", ":")).encode("utf-8")


def write_sidecar(sdir: Path, masks: list[list[int]],
                  grid: Sequence[int]) -> dict:
    """Write the content-addressed sidecar; its reference for the record."""
    payload = sidecar_payload(masks, grid)
    sha = hashlib.sha256(payload).hexdigest()
    data = gzip.compress(payload, mtime=0)
    name = f"{SIDECAR_PREFIX}{sha}{SIDECAR_SUFFIX}"
    path = sdir / name
    if not path.is_file():
        sdir.mkdir(parents=True, exist_ok=True)
        fd, staged = tempfile.mkstemp(dir=str(sdir), prefix=f".{name}.")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(staged, path)
    return {"file": name, "sha256": sha, "codec": CODEC,
            "bytes": len(data), "masks": len(masks)}


def _drop_superseded_sidecars(sdir: Path, keep: str) -> None:
    """After the record names its sidecar, an older one is unreachable:
    nothing references it, so it is removed rather than left to read as
    current."""
    for path in sdir.glob(f"{SIDECAR_PREFIX}*{SIDECAR_SUFFIX}"):
        if path.name != keep:
            path.unlink()


def sidecar_present(content_digest: str, doc: dict,
                    root: Path | None = None) -> bool:
    """Whether the record's sidecar is on disk (by name; `read_mask`
    checks the hash when it reads)."""
    ref = doc.get("sidecar") or {}
    name = ref.get("file")
    return bool(name) and (source_memory.source_dir(content_digest, root)
                           / name).is_file()


def read_masks(content_digest: str, doc: dict,
               root: Path | None = None) -> list[list[int]]:
    """All of a record's masks as run arrays. Refuses a sidecar whose
    content is not the one the record names."""
    ref = doc["sidecar"]
    path = source_memory.source_dir(content_digest, root) / ref["file"]
    raw = gzip.decompress(path.read_bytes())
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError(f"{path.name} does not hash to the sha256 its "
                         f"record names; rebuild M3d")
    payload = json.loads(raw)
    if payload.get("codec") != CODEC:
        raise ValueError(f"sidecar codec {payload.get('codec')!r} is not "
                         f"{CODEC!r}")
    return payload["masks"]


def read_mask(content_digest: str, doc: dict, mask_index: int,
              root: Path | None = None) -> np.ndarray:
    """One instance's mask as a `(h, w)` bool array on the record's grid."""
    return decode_runs(read_masks(content_digest, doc, root)[mask_index],
                       doc["grid"])


def read_m3d(content_digest: str,
             root: Path | None = None) -> dict | None:
    """A digest's M3d record, or None when never measured."""
    path = source_memory.source_dir(content_digest, root) / \
        source_memory.SLOT_REGIONS
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


# ── Measuring a source ──────────────────────────────────────────────


def _helper() -> str:
    from library.tools import vision_measure
    helper, reason = vision_measure.ensure_helper(SWIFT_SOURCE, HELPER_NAME)
    if helper is None:
        raise RuntimeError(f"person-regions helper unavailable: {reason}")
    return helper


def _measure(paths: list[str], grid: Sequence[int], helper: str) -> list[dict]:
    from library.tools import vision_measure
    docs: list[dict] = []
    for first in range(0, len(paths), BATCH_FRAMES):
        docs.extend(vision_measure.measure_frames(
            paths[first:first + BATCH_FRAMES], helper,
            extra_args=["--grid", str(grid[0]), str(grid[1])]))
    return docs


def build_source_regions(source_file: str, content_digest: str,
                         root: Path | None = None) -> dict:
    """M3d for one source. Heavy (an I-frame decode plus one Vision
    request per sample): callers hold the heavy-work lock. Refuses
    (raises) without a fresh M2 and the M3/M3b it assigns faces from."""
    from library.tools import event_spans, person_entity, person_measurements

    started = time.perf_counter()
    m2 = source_memory.read_m2(content_digest, root)
    if m2 is None or not source_memory.is_fresh(m2, source_file):
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no fresh M2 frame sample; "
            f"run `python3 -m library.tools.source_memory frames` first")
    m3 = person_measurements.read_m3(content_digest, root)
    identity = person_entity.read_identity(content_digest, root)
    if m3 is None or identity is None:
        raise RuntimeError(
            f"{os.path.basename(source_file)} needs M3 (persons) and M3b "
            f"(identity) before M3d: faces are what name a region")
    times = [f["t"] for f in m2["frames"]]
    if [f["t"] for f in m3["frames"]] != times:
        raise RuntimeError("M3 was not measured on this M2 sample; "
                           "rebuild M3")
    face_tracks = event_spans.assign_face_tracks(m3, identity)
    pixels = (m3.get("instrument") or {}).get("frame_pixels")
    if not pixels:
        raise RuntimeError("M3 records no frame_pixels; rebuild M3")
    grid = [int(pixels[0]), int(pixels[1])]

    helper = _helper()
    with tempfile.TemporaryDirectory(prefix="ren-m3d-") as scratch:
        t0 = time.perf_counter()
        pairs, hwaccel = source_memory.extract_iframes(
            source_file, scratch, DECODE_WIDTH)
        decode_seconds = time.perf_counter() - t0
        decoded = {round(t, 3): path for path, t in pairs}
        paths: list[str | None] = []
        for t in times:
            hit = decoded.get(round(t, 3))
            if hit is None:
                hit = next((p for p, pt in pairs
                            if abs(pt - t) <= FRAME_TIME_TOLERANCE), None)
            paths.append(hit)
        wanted = [p for p in paths if p is not None]
        t0 = time.perf_counter()
        measured = iter(_measure(wanted, grid, helper)) if wanted else iter(())
        vision_seconds = time.perf_counter() - t0
        docs = [next(measured) if p is not None else None for p in paths]
        decoded_pixels = next((d.get("pixels") for d in docs
                               if d and d.get("pixels")), None)
    built = assemble(times, docs, m3["frames"], face_tracks, grid)

    sdir = source_memory.source_dir(content_digest, root)
    sidecar = write_sidecar(sdir, built["masks"], grid)
    elapsed = time.perf_counter() - started
    measured_count = sum(1 for d in docs if d is not None)
    record = {
        "content_digest": content_digest,
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3D_STATUS_MEASURED,
        "coordinates": "image-normalised, top-left origin",
        "grid": grid,
        "sidecar": sidecar,
        "frame_count": len(times),
        "samples_measured": measured_count,
        "faces": built["faces"],
        "tracks": built["tracks"],
        "samples": built["samples"],
        "instrument": {
            "method": M3D_METHOD,
            "request": "VNGeneratePersonInstanceMaskRequest",
            "decode_width": DECODE_WIDTH,
            "decoded_pixels": decoded_pixels,
            "hwaccel": "videotoolbox" if hwaccel else None,
            "face_inside_min_fraction": FACE_INSIDE_MIN_FRACTION,
            "track_link_min_iou": TRACK_LINK_MIN_IOU,
            "decode_seconds": round(decode_seconds, 1),
            "vision_seconds": round(vision_seconds, 1),
            "ms_per_frame": round(1000.0 * vision_seconds
                                  / max(1, measured_count), 1),
            "wall_seconds": round(elapsed, 1),
        },
    }
    source_memory.write_json(sdir / source_memory.SLOT_REGIONS, record)
    _drop_superseded_sidecars(sdir, sidecar["file"])
    return {"source_file": os.path.abspath(source_file),
            "content_digest": content_digest, "frames": len(times),
            "samples_measured": measured_count,
            "instances": sidecar["masks"], "tracks": len(built["tracks"]),
            "fraction_inside_exactly_one":
                built["faces"]["fraction_inside_exactly_one"],
            "wall_seconds": record["instrument"]["wall_seconds"]}


@source_memory._project_scoped
def build_project_regions(project_folder: str,
                          clip_ids: list[str] | None = None,
                          root: Path | None = None) -> dict:
    """M3d for every catalog source with media, one source at a time
    (one decode and one Vision pass already fill the machine). Same
    reporting shape as `person_measurements.build_project_persons`."""
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
            account = build_source_regions(path, digest, root)
        except Exception as exc:  # noqa: BLE001 - one source's failure
            # is recorded on its row; the rest of the catalog still runs.
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            failed.append(clip_id)
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


# ── Reading ─────────────────────────────────────────────────────────


def window(doc: dict, start: float, end: float) -> dict:
    """Every instance between `start` and `end` seconds, with the region
    track it belongs to and that track's owner - what "is anyone there,
    and who" reads off, without decoding a mask."""
    owners = {t["track_id"]: t for t in doc["tracks"]}
    rows = []
    for sample in doc["samples"]:
        if not start <= sample["t"] <= end:
            continue
        for inst in sample["instances"] or []:
            track = owners[inst["track"]]
            rows.append({"t": sample["t"], "track": inst["track"],
                         "bbox": inst["bbox"],
                         "track_face_track": track.get("face_track"),
                         "track_unassigned": track.get("unassigned"),
                         "sample_unassigned": inst.get("unassigned")})
    people = sorted({r["track_face_track"] for r in rows
                     if r["track_face_track"]})
    return {"start": start, "end": end, "instances": len(rows),
            "person_face_tracks": people,
            "unowned_instances": sum(1 for r in rows
                                     if not r["track_face_track"]),
            "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="person_regions",
        description="M3d: Apple Vision person instance masks over each "
                    "source's M2 sample times, linked into region tracks "
                    "and to M3b face tracks. Heavy; run under the "
                    "heavy-work lock.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3d for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3d, and "
                              "how many faces fall inside one instance")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    p_window = sub.add_parser("window", help="the instances of one source "
                              "between two times, and whose they are")
    p_window.add_argument("project")
    p_window.add_argument("--clip", required=True)
    p_window.add_argument("--start", type=float, required=True)
    p_window.add_argument("--end", type=float, required=True)
    p_window.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_regions(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        if args.command == "window" and clip.get("clip_id") != args.clip:
            continue
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3d(digest, root) if digest else None
        if args.command == "window":
            if doc is None:
                print(json.dumps({"clip_id": args.clip, "m3d": "missing"}))
                return 1
            print(json.dumps({"clip_id": args.clip,
                              **window(doc, args.start, args.end)}, indent=2))
            return 0
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3d": "measured" if doc else "missing",
                     "tracks": len(doc["tracks"]) if doc else 0,
                     "faces": doc["faces"] if doc else None,
                     "wall_seconds": (doc["instrument"]["wall_seconds"]
                                      if doc else None)})
    if args.command == "window":
        print(json.dumps({"clip_id": args.clip, "m3d": "no such clip"}))
        return 1
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
