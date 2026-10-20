"""Region stats: M3f (`region_stats.json`) of the per-source footage
memory (`docs/SOURCE_MEMORY.md`).

What the picture looks like INSIDE each person region against outside
it, per M2 sample. Step 5.01 measured exposure and colour over the
whole frame only (`grade.measure_luma`, `shot_colour`), and a whole
frame cannot say "the speaker sits a stop under the wall behind him":
a dark subject on a bright set and a correctly lit subject on a dark
set can carry the same average. This is the region half, for the
multimodal-understanding plan's MU-11
(`data/vep-plan-multimodal-understanding/report.md` §3 E1/E2 and §5.4
"Region-aware grade" in firstmate's home).

**What it reads.** The M2 thumbnail at each sample and the M3d masks
measured on that thumbnail's own grid (`person_regions`: masks are
stored on the M2 grid precisely so a lane like this one needs no
resampling). No decode, no model: arithmetic over pixels M2 already
decoded.

**What it measures, per region and for OUTSIDE.** OUTSIDE is every
cell no person instance holds - the set, not the other speaker: a
colourist holding "the background" is not holding a second person, so
a second person is never averaged into the first one's surround.

- `luma_p50`: the median of per-cell Rec.709 luma
  (`LUMA_WEIGHTS`) over the region's cells, 0-255, on the thumbnail's
  decoded RGB. NOT the scale of `clip_exposure.luma` (ffprobe YAVG on
  the source's Y plane): the two are different instruments, and the
  difference INSIDE one frame is what this reading is for.
- `rgb_mean`: per-channel mean over the region's cells, 0-255.
- `skin_rgb` / `skin_fraction`: `shot_colour.skin_tone_of` over the
  region's cells - the same rule the whole-frame skin vector uses, with
  the same stated absence.
- `sharpness`: the variance of the 4-neighbour Laplacian
  (`picture_quality.laplacian`) over the cells whose WHOLE stencil lies
  in the region, so the subject's silhouette - the strongest edge in
  most frames - is counted in neither side. A Laplacian variance has no
  absolute scale across instruments (this grid is not
  `picture_quality`'s 180 px sample), so it is read as inside against
  outside in one frame and never against `picture_quality`'s threshold.

Every reading carries its cell count, and an absence is said with its
reason: a region too thin to hold one full stencil has no sharpness,
not a sharpness of zero. There is no threshold here for a consumer to
inherit; nothing decides what difference is a fault.

**Time.** One record per M3d sample, at its M2 `t`. A sample M3d did
not measure, or whose thumbnail is gone, is `regions: null`.

**Where it goes.** `project_model_evidence` migrates every sample into
`Observation(modality="region_stats")`. Step 5.01's bridge reads it
over the source seconds each clip PLAYS (`clip_region_stats`), and the
colourist sees it through the named view `view:region_exposure`
(`library/tools/context_views.py`); a `subject_grades` entry cites the
row it rests on (`resolve_measured_basis`).

    python3 -m library.tools.region_stats build <project>
    python3 -m library.tools.region_stats status <project>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from statistics import median

import numpy as np
from PIL import Image

from library.tools import source_memory
from library.tools.ren_refusal import RenRefusal

M3F_STATUS_MEASURED = "measured"
M3F_METHOD = "region-stats-v2-m2"
"""The slot version. `footage_analysis.slot_is_fresh` rebuilds a record
stamped with anything else, so a change to what a reading means (the
luma weights, the stencil rule, the OUTSIDE definition) bumps this."""

LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)
"""Rec.709 luma over decoded 8-bit RGB. Stated, not tuned: the delivery
transform this pipeline ships by default is Rec.709."""

TIME_DECIMALS = 3
"""M2 and M3d both record `t` rounded to the millisecond (M3d's build
refuses an M3 whose times are not M2's), so a sample is joined on `t`
at that precision."""


# ── Readings over one frame ────────────────────────────────────────


def luma_of(rgb: np.ndarray) -> np.ndarray:
    """Per-cell Rec.709 luma of an `(h, w, 3)` RGB array, 0-255."""
    rgb = np.asarray(rgb, dtype=np.float64)
    return (LUMA_WEIGHTS[0] * rgb[..., 0] + LUMA_WEIGHTS[1] * rgb[..., 1]
            + LUMA_WEIGHTS[2] * rgb[..., 2])


def stencil_interior(mask: np.ndarray) -> np.ndarray:
    """The cells of `mask`, shaped like `laplacian`'s output (`(h-2,
    w-2)`), whose centre AND four neighbours are all in the mask."""
    m = np.asarray(mask, dtype=bool)
    return (m[1:-1, 1:-1] & m[:-2, 1:-1] & m[2:, 1:-1]
            & m[1:-1, :-2] & m[1:-1, 2:])


def region_reading(rgb: np.ndarray, luma: np.ndarray, lap: np.ndarray,
                   mask: np.ndarray) -> dict:
    """Luma median, mean RGB, skin vector and stencil-interior Laplacian
    variance over the cells of `mask`. Absences carry their reason."""
    from library.tools.shot_colour import skin_tone_of

    cells = int(mask.sum())
    if cells == 0:
        return {"cells": 0, "luma_p50": None, "rgb_mean": None,
                "skin_rgb": None, "skin_fraction": 0.0, "sharpness": None,
                "sharpness_cells": 0,
                "unmeasured_because": "the region holds no cell"}
    pixels = np.asarray(rgb, dtype=np.float64)[mask]
    mean = pixels.mean(axis=0)
    reading = {
        "cells": cells,
        "luma_p50": round(float(np.median(luma[mask])), 2),
        "rgb_mean": [round(float(v), 2) for v in mean],
    }
    skin = skin_tone_of(pixels)
    reading["skin_rgb"] = skin["skin_rgb"]
    reading["skin_fraction"] = skin["skin_fraction"]
    if skin.get("absent"):
        reading["skin_unmeasured_because"] = skin["reason"]
    interior = stencil_interior(mask)
    count = int(interior.sum())
    reading["sharpness_cells"] = count
    if count < 2:
        reading["sharpness"] = None
        reading["sharpness_unmeasured_because"] = (
            f"{count} cell(s) of the region hold a whole Laplacian "
            f"stencil; a variance needs two")
    else:
        reading["sharpness"] = round(float(lap[interior].var()), 3)
    return reading


def measure_frame(rgb: np.ndarray, masks: Sequence[np.ndarray]) -> dict:
    """One sample: a reading per instance mask, and one for OUTSIDE (the
    cells no instance holds)."""
    from library.tools.analysis.picture_quality import laplacian

    luma = luma_of(rgb)
    lap = laplacian(luma)
    union = np.zeros(luma.shape, dtype=bool)
    for mask in masks:
        union |= mask
    whole = np.ones(luma.shape, dtype=bool)
    return {"whole_frame": {**region_reading(rgb, luma, lap, whole),
                             "mask_sha256": _mask_digest(whole)},
            "outside": {**region_reading(rgb, luma, lap, ~union),
                        "mask_sha256": _mask_digest(~union)},
            "regions": [region_reading(rgb, luma, lap, m) for m in masks]}


def _mask_digest(mask: np.ndarray) -> str:
    """Stable digest of the exact row-major mask raster that was measured."""
    bits = np.asarray(mask, dtype=np.uint8)
    header = f"{bits.shape[1]}x{bits.shape[0]}:".encode("ascii")
    return hashlib.sha256(header + np.packbits(bits, bitorder="big").tobytes()).hexdigest()


# ── Measuring a source ──────────────────────────────────────────────


def _frame_rgb(path: str) -> np.ndarray | None:
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.float64)
    except (OSError, ValueError):
        return None


def _key(t) -> float:
    return round(float(t), TIME_DECIMALS)


def measure_samples(content_digest: str, m2: dict, m3d: dict,
                    times: set | None = None,
                    root: Path | None = None) -> list[dict]:
    """Per-sample readings for every M3d sample (or only those whose `t`
    is in `times`). Pure read of M2 and M3d; writes nothing."""
    from library.tools import person_regions

    frames = {_key(f["t"]): f for f in m2.get("frames") or []}
    grid = [int(v) for v in m3d["grid"]]
    masks = person_regions.read_masks(content_digest, m3d, root)
    out = []
    for sample in m3d.get("samples") or []:
        t = sample["t"]
        if times is not None and _key(t) not in times:
            continue
        frame = frames.get(_key(t))
        rgb = (_frame_rgb(source_memory.frame_abspath(content_digest, frame,
                                                      root))
               if frame else None)
        if rgb is None:
            out.append({"t": t, "regions": None,
                        "unmeasured_because": "no M2 thumbnail at this "
                                              "sample"})
            continue
        if [rgb.shape[1], rgb.shape[0]] != grid:
            out.append({"t": t, "regions": None,
                        "unmeasured_because": (
                            f"the M2 thumbnail is {rgb.shape[1]}x"
                            f"{rgb.shape[0]}, the M3d grid "
                            f"{grid[0]}x{grid[1]}")})
            continue
        if sample.get("instances") is None:
            luma = luma_of(rgb)
            from library.tools.analysis.picture_quality import laplacian
            lap = laplacian(luma)
            whole = np.ones(luma.shape, dtype=bool)
            out.append({"t": t, "whole_frame": {
                **region_reading(rgb, luma, lap, whole),
                "mask_sha256": _mask_digest(whole)},
                "outside": None, "regions": None,
                "unmeasured_because": "M3d did not measure this sample"})
            continue
        instances = sample["instances"]
        decoded = [person_regions.decode_runs(masks[inst["mask"]], grid)
                   for inst in instances]
        measured = measure_frame(rgb, decoded)
        out.append({"t": t, "whole_frame": measured["whole_frame"],
                    "outside": measured["outside"], "regions": [
            {"track": inst["track"], "mask": inst["mask"],
             "mask_sha256": _mask_digest(mask),
             "inside": {**reading, "mask_sha256": _mask_digest(mask)}}
            for inst, mask, reading in zip(
                instances, decoded, measured["regions"])]})
    return out


def _inputs(content_digest: str, root: Path | None
            ) -> tuple[dict | None, dict | None, str]:
    """`(m2, m3d, why_not)` for a digest: both present and joinable, or
    the reason a reading cannot be taken."""
    from library.tools import person_regions

    m3d = person_regions.read_m3d(content_digest, root)
    if m3d is None:
        return None, None, ("no M3d person regions for this source "
                            "(`ren analyze <project> --lanes regions`)")
    if not person_regions.sidecar_present(content_digest, m3d, root):
        return None, None, "the M3d mask sidecar is missing; rebuild M3d"
    m2 = source_memory.read_m2(content_digest, root)
    if (m2 is None
            or not source_memory.m2_frames_present(content_digest, m2, root)):
        return None, None, "no M2 frame sample with its thumbnails on disk"
    return m2, m3d, ""


def build_source_region_stats(source_file: str, content_digest: str,
                              root: Path | None = None) -> dict:
    """M3f for one source. Refuses (raises) without M2 and M3d."""
    started = time.perf_counter()
    m2, m3d, why_not = _inputs(content_digest, root)
    if m2 is None:
        raise RuntimeError(f"{os.path.basename(source_file)}: {why_not}")
    samples = measure_samples(content_digest, m2, m3d, root=root)
    measured = sum(1 for s in samples if s["regions"] is not None)
    record = {
        "content_digest": content_digest,
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3F_STATUS_MEASURED,
        "grid": m3d["grid"],
        "outside": "cells no person instance holds",
        "frame_count": len(samples),
        "samples_measured": measured,
        "tracks": m3d.get("tracks") or [],
        "samples": samples,
        "instrument": {
            "method": M3F_METHOD,
            "input": "m2-thumbnail + m3d masks",
            "luma": "rec709",
            "m3d_sidecar_sha256": (m3d.get("sidecar") or {}).get("sha256"),
            "wall_seconds": round(time.perf_counter() - started, 1),
        },
    }
    source_memory.write_json(source_memory.source_dir(content_digest, root)
                             / source_memory.SLOT_REGION_STATS, record)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": content_digest, "frames": len(samples),
            "samples_measured": measured,
            "wall_seconds": record["instrument"]["wall_seconds"]}


@source_memory._project_scoped
def build_project_region_stats(project_folder: str,
                               clip_ids: list[str] | None = None,
                               root: Path | None = None) -> dict:
    """M3f for every catalog source, one source at a time. Same reporting
    shape as `attention_saliency.build_project_saliency`."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    results, failed, seen = [], [], set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        digest, _ = source_memory.digest_for_clip(project_folder, clip,
                                                  recorded)
        if digest is None or digest in seen:
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "no content digest" if digest is None
                            else "reused"})
            continue
        seen.add(digest)
        try:
            account = build_source_region_stats(path, digest, root)
        except Exception as exc:  # noqa: BLE001 - one source's failure
            # is recorded on its row; the rest of the catalog still runs.
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            failed.append(clip_id)
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def read_m3f(content_digest: str,
             root: Path | None = None) -> dict | None:
    """A digest's M3f record, or None when never measured."""
    path = (source_memory.source_dir(content_digest, root)
            / source_memory.SLOT_REGION_STATS)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def _current(doc: dict | None, m3d: dict) -> bool:
    """Whether an M3f record was measured off THIS M3d: the method and
    the mask sidecar's content address both match."""
    if doc is None:
        return False
    instrument = doc.get("instrument") or {}
    return (instrument.get("method") == M3F_METHOD
            and instrument.get("m3d_sidecar_sha256")
            == (m3d.get("sidecar") or {}).get("sha256"))


# ── Reading a clip's played seconds (step 5.01) ─────────────────────


def _in_scope(times: list[float], spans: Sequence[Sequence[float]]
              ) -> tuple[set, list[str]]:
    """The sample times inside the played spans, and a note per span no
    sample falls in (read at the sample nearest its middle instead)."""
    chosen, notes = set(), []
    for start, end in spans:
        inside = [t for t in times if start <= t <= end]
        if inside:
            chosen.update(_key(t) for t in inside)
            continue
        if times:
            middle = (start + end) / 2.0
            nearest = min(times, key=lambda t: abs(t - middle))
            chosen.add(_key(nearest))
            notes.append(f"{start:g}-{end:g}s holds no M2 sample; read at "
                         f"{nearest:g}s, the one nearest its middle")
    return chosen, notes


def _median(values: list) -> float | None:
    return round(float(median(values)), 3) if values else None


def _median_vector(vectors: list) -> list | None:
    if not vectors:
        return None
    return [round(float(median(v[i] for v in vectors)), 2)
            for i in range(3)]


def summarize_readings(readings: list[dict]) -> dict:
    """Medians over samples of one side's per-sample readings."""
    skins = [r["skin_rgb"] for r in readings if r.get("skin_rgb")]
    sharp = [r["sharpness"] for r in readings
             if r.get("sharpness") is not None]
    return {
        "luma_p50": _median([r["luma_p50"] for r in readings
                             if r.get("luma_p50") is not None]),
        "rgb_mean": _median_vector([r["rgb_mean"] for r in readings
                                    if r.get("rgb_mean")]),
        "skin_rgb": _median_vector(skins),
        "skin_samples": len(skins),
        "sharpness": _median(sharp),
        "sharpness_samples": len(sharp),
        "cells_median": _median([r["cells"] for r in readings]),
    }


def summarize_tracks(samples: list[dict], tracks: list[dict]) -> list[dict]:
    """One row per region track seen in `samples`: medians of its inside
    readings, and of OUTSIDE over the same samples."""
    owners = {t["track_id"]: t for t in tracks}
    per: dict[str, dict] = {}
    for sample in samples:
        for region in sample.get("regions") or []:
            row = per.setdefault(region["track"], {"inside": [],
                                                   "outside": [], "t": []})
            row["inside"].append(region["inside"])
            row["outside"].append(sample["outside"])
            row["t"].append(sample["t"])
    rows = []
    for track_id in sorted(per):
        owner = owners.get(track_id) or {}
        row = {"region_track": track_id,
               "face_track": owner.get("face_track"),
               "samples": len(per[track_id]["t"]),
               "first_t": min(per[track_id]["t"]),
               "last_t": max(per[track_id]["t"]),
               "inside": summarize_readings(per[track_id]["inside"]),
               "outside": summarize_readings(per[track_id]["outside"])}
        if not owner.get("face_track"):
            row["unassigned"] = owner.get("unassigned") or "no_face_inside"
        rows.append(row)
    return rows


@source_memory._project_scoped
def clip_region_stats(project_folder: str, clip: dict,
                      spans: Sequence[Sequence[float]],
                      root: Path | None = None) -> dict:
    """What step 5.01 reads for one clip: per region track, the subject
    against OUTSIDE over the source seconds the clip plays (`spans`;
    every sample of the source when no placement states its seconds).

    Serves the M3f slot when it was measured off the current M3d, and
    otherwise measures just the samples it needs in memory - the same
    arithmetic, writing nothing. A clip that cannot be read says why."""
    clip_id = clip.get("clip_id")
    digest, _ = source_memory.digest_for_clip(project_folder, clip)
    if digest is None:
        return {"clip_id": clip_id, "status": "absent",
                "absent_because": "no content digest for this clip's source"}
    m2, m3d, why_not = _inputs(digest, root)
    if m2 is None:
        return {"clip_id": clip_id, "content_digest": digest,
                "status": "absent", "absent_because": why_not}
    times = [s["t"] for s in m3d.get("samples") or []]
    notes: list[str] = []
    if spans:
        wanted, notes = _in_scope(times, spans)
        scope = "played"
    else:
        wanted, scope = {_key(t) for t in times}, "whole_source"
    slot = read_m3f(digest, root)
    if _current(slot, m3d):
        samples = [s for s in slot["samples"] if _key(s["t"]) in wanted]
        served = "m3f"
    else:
        samples = measure_samples(digest, m2, m3d, wanted, root)
        served = "measured_now"
    measured = [s for s in samples if s.get("regions") is not None]
    record = {"clip_id": clip_id, "content_digest": digest,
              "scope": scope, "spans": [list(s) for s in spans],
              "served_from": served, "samples_read": len(measured),
              "status": "measured" if measured else "absent"}
    if notes:
        record["scope_notes"] = notes
    if not measured:
        record["absent_because"] = ("no M3d-measured sample with a "
                                    "thumbnail inside the played seconds")
        return record
    record["regions"] = summarize_tracks(measured, m3d.get("tracks") or [])
    if not record["regions"]:
        record["status"] = "no_person"
        record["absent_because"] = ("M3d found no person in any sample "
                                    "read: there is no subject to read")
    return record


# ── The reading the colourist sees, and what a grade cites ──────────


def basis_id(clip_id: str, region_track: str) -> str:
    """The id a `subject_grades` entry names as its `measured_basis`."""
    return f"{clip_id}/{region_track}"


def stops(subject, outside) -> float | None:
    """`log2(subject / outside)` on the two luma medians, the same
    arithmetic as 5.01's `stops_between`: negative is a subject darker
    than its surround. None where either side is absent or zero."""
    if subject is None or outside is None or subject <= 0 or outside <= 0:
        return None
    return round(math.log2(subject / outside), 3)


def exposure_rows(region_stats: list[dict]) -> tuple[list[dict], list[str]]:
    """The reading: one flat row per (clip, region track), and one line per
    clip that has none. This is `view:region_exposure` and the table a
    `measured_basis` resolves against - one function, so the prompt and
    the post-bridge cannot disagree about a number."""
    rows, absent = [], []
    for clip in region_stats or []:
        if clip.get("status") != "measured":
            absent.append(f"{clip.get('clip_id')}: "
                          f"{clip.get('absent_because')}")
            continue
        for region in clip.get("regions") or []:
            inside, outside = region["inside"], region["outside"]
            row = {
                "basis_id": basis_id(clip["clip_id"], region["region_track"]),
                "clip_id": clip["clip_id"],
                "region_track": region["region_track"],
                "owner": region.get("face_track")
                or f"unassigned ({region.get('unassigned')})",
                "samples": region["samples"],
                "scope": clip["scope"],
                "subject_luma_p50": inside["luma_p50"],
                "outside_luma_p50": outside["luma_p50"],
                "subject_minus_outside_stops": stops(
                    inside["luma_p50"], outside["luma_p50"]),
                "subject_rgb": inside["rgb_mean"],
                "outside_rgb": outside["rgb_mean"],
                "subject_skin_rgb": inside["skin_rgb"],
                "subject_sharpness": inside["sharpness"],
                "outside_sharpness": outside["sharpness"],
            }
            rows.append(row)
    return rows, absent


REGION_EXPOSURE_LEGEND = {
    "basis_id": ("the row's id, `<clip_id>/<region_track>`: what a "
                 "`subject_grades` entry names as its `measured_basis`."),
    "region_track": ("one person region tracked across the clip's samples "
                     "(M3d, Apple Vision person instance masks); `owner` "
                     "is the M3b face track whose face lies inside it, or "
                     "`unassigned` with the reason (a hand whose owner is "
                     "out of frame, a body turned away)."),
    "outside": ("every cell no person instance holds - the set, never "
                "another person."),
    "subject_luma_p50": ("median over the samples of the median Rec.709 "
                         "luma inside the region, 0-255 on the M2 thumbnail "
                         "grid. A different instrument from "
                         "`clip_exposure.luma` (ffprobe YAVG): compare "
                         "subject with outside here, never with that."),
    "subject_minus_outside_stops": (
        "log2(subject_luma_p50 / outside_luma_p50): negative means the "
        "subject reads darker than the set around them. A measurement, "
        "not a verdict - a lit set behind a speaker can be the look."),
    "subject_rgb": "median over samples of the region's mean RGB, 0-255.",
    "subject_skin_rgb": ("median over samples of the skin vector inside "
                         "the region (the same skin rule as `skin_rgb`); "
                         "null where no sample's region carried skin."),
    "subject_sharpness": (
        "median over samples of the Laplacian variance inside the region, "
        "counting only cells whose whole stencil is inside, so the "
        "silhouette edge is in neither side. No absolute scale: read it "
        "against `outside_sharpness` in the same row."),
    "scope": ("`played` - the samples inside the source seconds the clip's "
              "placements play; `whole_source` - every sample, because no "
              "placement stated its seconds."),
}


class UnsourcedSubjectGrade(RenRefusal):
    """A subject grade that cites no measured row, or one that does not
    exist, while the colourist was shown the region reading."""


def resolve_measured_basis(entries: list, region_stats: list) -> list:
    """Each `subject_grades` entry with its `measured_basis` resolved to
    the row it names (the row's numbers, verbatim).

    A clip the region reading MEASURED must be cited: a subject grade on
    it with no `measured_basis`, or naming a row that is not that clip's,
    raises `UnsourcedSubjectGrade` (the post-bridge retry carries it to
    the model). A clip the reading could not measure has nothing to cite,
    so its entry carries `measured_basis: null` and the stated reason.
    Non-dict entries pass through untouched: `parse_plan_entry` drops
    them by name."""
    rows, _ = exposure_rows(region_stats)
    by_id = {row["basis_id"]: row for row in rows}
    measured_clips = {row["clip_id"] for row in rows}
    absent = {c.get("clip_id"): c.get("absent_because")
              for c in region_stats or [] if c.get("status") != "measured"}
    out = []
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        clip_id = entry.get("clip_id")
        cited = entry.get("measured_basis")
        if clip_id in measured_clips:
            row = by_id.get(cited) if isinstance(cited, str) else None
            if row is None or row["clip_id"] != clip_id:
                choices = sorted(i for i, r in by_id.items()
                                 if r["clip_id"] == clip_id)
                raise UnsourcedSubjectGrade(
                    what=(f"subject_grades entry {index} on {clip_id} "
                          f"cites measured_basis {cited!r}, which is not "
                          f"a region_exposure row of {clip_id}"),
                    why=("the colourist was shown the subject-against-"
                         "outside reading for this clip, and a region "
                         "grade on it must say which measured row it "
                         "rests on - a grade sourced from nothing in its "
                         "own context is the decision AGENTS.md 10.1 "
                         "refuses"),
                    fix=(f"set measured_basis on entry {index} to one of "
                         f"{', '.join(choices)}, or drop the entry"))
            out.append({**entry, "measured_basis": dict(row)})
            continue
        if cited is not None:
            raise UnsourcedSubjectGrade(
                what=(f"subject_grades entry {index} on {clip_id} cites "
                      f"measured_basis {cited!r}, but region_exposure "
                      f"carries no row for {clip_id}"),
                why=("a basis that is not in the context is a number the "
                     "grade did not read"),
                fix=(f"set measured_basis to null on entry {index}: "
                     f"{clip_id} has no region reading "
                     f"({absent.get(clip_id) or 'not in the cut'})"))
        out.append({**entry, "measured_basis": None,
                    "measured_basis_absent_because": (
                        absent.get(clip_id)
                        or "the clip is not in the region reading")})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="region_stats",
        description="M3f: luma, colour, skin and sharpness inside each M3d "
                    "person region against outside every region, per M2 "
                    "sample. Arithmetic over M2 thumbnails; no model.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3f for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3f")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_region_stats(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3f(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3f": "measured" if doc else "missing",
                     "samples_measured": doc["samples_measured"] if doc else 0})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
