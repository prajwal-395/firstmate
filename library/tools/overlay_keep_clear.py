"""Keep-clear: which placed overlays sit over a person or over what the
eye goes to. REPORT ONLY.

The safe area (`safe_area.py`) keeps an overlay out of the band the
PLATFORM paints over. Nothing kept one off the PICTURE: a lower third
can land across the speaker's face, and every placement gate passes,
because each of them judges the overlay against its own intent and
never against what is under it. This check is that missing half, for
the multimodal-understanding plan's MU-13
(`data/vep-plan-multimodal-understanding/report.md` §7 in firstmate's
home).

**What it reads.** Per overlay span, the rectangle the overlay DRAWS -
turned back out of the transform Resolve HOLDS (read back after
placement, AGENTS.md 10.2; `tight_box.ink_screen_box`) - and the
picture under it: the topmost playing clip on a picture row, its
source, the source time at each frame, and the rect the source is drawn
at (`reel_framing.delivered_picture` over that clip's read-back
transform). The overlay's rect is carried into the source's own
normalised space through that picture rect, and compared, at every M2
sample the span covers, with:

- each **person region** in M3d (`person_regions`): the share of the
  overlay's grid cells that are that person's mask
  (`fraction_of_overlay`), and the share of the person the overlay
  covers (`fraction_of_entity`). The region track names its owner - an
  M3b face track, the project's `person_id` and declared name where the
  roster joins it - or says it is unowned, with M3d's reason;
- each **salient box** in M3e (`attention_saliency`): the share of the
  overlay's rect inside the box, and of the box under the overlay.

A span that covers no M2 sample is read at the one nearest its middle,
and says so (`sampled_at`). A sample counts as overlapping when the
share is above zero: this measures, it does not judge, so there is no
threshold here for a consumer to inherit.

**It moves nothing.** Every finding is reported - on stderr and on the
build record beside the overlay sweep (`overlay_verify`) - and the
reel stands as placed. Which overlap is a defect (a caption across a
chin, a lower third across a laptop nobody looks at) is a consumer's
decision, and none is wired.

**What it cannot see is said.** An overlay with no readable transform,
a span with nothing on a picture row under it, a picture with a crop
the rect model refuses (`DeliveredPicture.crop_unread`), a source with
no content digest, and a source whose memory carries neither M3d nor
M3e are listed under `skipped` with the reason, never passed
(AGENTS.md 10.4). The overlay's rect is its CANVAS, not its ink
(`box_basis`): `overlay_draw_intent` measures canvases for the same
reason - no decode at build time - and a tight canvas is the ink union
plus the 480 px floor, so a fraction can only be over-stated, never
missed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from library.tools.resolve_transform import FALLBACK_DRAW_GAIN


def _area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersect(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [max(a[0], b[0]), max(a[1], b[1]),
            min(a[2], b[2]), min(a[3], b[3])]


def box_in_source(box_px: Sequence[float],
                  picture_rect: Sequence[float]) -> list[float] | None:
    """A delivery-frame rect -> the part of it over the picture, in the
    source's normalised top-left space. None when it misses the picture."""
    left, top, right, bottom = (float(v) for v in picture_rect)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = (float(v) for v in box_px)
    out = [(x0 - left) / width, (y0 - top) / height,
           (x1 - left) / width, (y1 - top) / height]
    out = _intersect(out, (0.0, 0.0, 1.0, 1.0))
    return out if _area(out) > 0 else None


def samples_for(samples: Sequence[dict], start: float, end: float
                ) -> list[dict]:
    """The samples whose `t` lies in `[start, end]`, else the one nearest
    the span's middle - a span between two M2 samples still reads one."""
    inside = [s for s in samples if start <= s["t"] <= end]
    if inside or not samples:
        return inside
    middle = (start + end) / 2.0
    return [min(samples, key=lambda s: abs(s["t"] - middle))]


def _owner(track: dict, roster: dict, digest: str) -> dict:
    owner = {"region_track": track["track_id"],
             "face_track": track.get("face_track")}
    if track.get("face_track"):
        person = roster.get((digest, track["face_track"]))
        if person:
            owner["person_id"] = person["person_id"]
            if person.get("name"):
                owner["name"] = person["name"]
    else:
        owner["unassigned"] = track.get("unassigned")
    return owner


def person_overlaps(m3d: dict, masks: list, box_src: Sequence[float],
                    samples: Sequence[dict], digest: str,
                    roster: dict) -> list[dict]:
    """Per region track, how much of the overlay it fills at the samples
    read: the peak and mean share of the overlay's cells that are the
    person, the peak share of the person under the overlay, and how many
    samples overlap at all."""
    from library.tools.person_regions import box_cells, decode_runs

    grid = m3d["grid"]
    rows, cols = box_cells(box_src, grid)
    cells = (rows.stop - rows.start) * (cols.stop - cols.start)
    tracks = {t["track_id"]: t for t in m3d["tracks"]}
    read = [s for s in samples if s.get("instances") is not None]
    per: dict[str, dict] = {}
    for sample in read:
        for inst in sample["instances"]:
            mask = decode_runs(masks[inst["mask"]], grid)
            hit = int(mask[rows, cols].sum())
            if not hit:
                continue
            row = per.setdefault(inst["track"], {"shares": [],
                                                 "entity": 0.0, "t": []})
            row["shares"].append(hit / cells)
            row["entity"] = max(row["entity"], hit / int(mask.sum()))
            row["t"].append(sample["t"])
    out = []
    for track_id, row in per.items():
        out.append({
            "kind": "person",
            **_owner(tracks[track_id], roster, digest),
            "fraction_of_overlay": round(max(row["shares"]), 3),
            "fraction_of_overlay_mean": round(
                sum(row["shares"]) / len(read), 3),
            "fraction_of_entity": round(row["entity"], 3),
            "samples_overlapping": len(row["shares"]),
            "samples_read": len(read),
            "first_t": row["t"][0], "last_t": row["t"][-1],
        })
    return sorted(out, key=lambda r: -r["fraction_of_overlay"])


def salient_overlaps(samples: Sequence[dict],
                     box_src: Sequence[float]) -> list[dict]:
    """How much of the overlay sits on a salient box at the samples read:
    one entry, or none when no salient box meets the overlay."""
    read = [s for s in samples if s.get("boxes") is not None]
    shares, entity, times = [], 0.0, []
    for sample in read:
        best = None
        for box in sample["boxes"]:
            overlap = _area(_intersect(box["bbox"], box_src))
            if overlap <= 0:
                continue
            share = overlap / _area(box_src)
            if best is None or share > best:
                best = share
            entity = max(entity, overlap / max(_area(box["bbox"]), 1e-12))
        if best is not None:
            shares.append(best)
            times.append(sample["t"])
    if not shares:
        return []
    return [{"kind": "salient",
             "fraction_of_overlay": round(max(shares), 3),
             "fraction_of_overlay_mean": round(sum(shares) / len(read), 3),
             "fraction_of_entity": round(entity, 3),
             "samples_overlapping": len(shares),
             "samples_read": len(read),
             "first_t": times[0], "last_t": times[-1]}]


class _Memory:
    """M3d/M3e records and mask sidecars, read once per digest."""

    def __init__(self, root: Path | None):
        self.root = root
        self._cache: dict = {}

    def get(self, digest: str) -> tuple:
        if digest not in self._cache:
            from library.tools import attention_saliency, person_regions
            m3d = person_regions.read_m3d(digest, self.root)
            masks = None
            if m3d is not None:
                try:
                    masks = person_regions.read_masks(digest, m3d, self.root)
                except (OSError, ValueError, KeyError):
                    m3d = None
            self._cache[digest] = (m3d, masks,
                                   attention_saliency.read_m3e(digest,
                                                               self.root))
        return self._cache[digest]


def _visible_pieces(r0: int, r1: int, pictures: Sequence[dict]
                    ) -> list[tuple[int, int, dict]]:
    """`[r0, r1)` cut where the picture under it changes: per piece, the
    topmost playing picture item covering it."""
    cuts = sorted({r0, r1} | {p[k] for p in pictures
                              for k in ("record_in", "record_out")
                              if r0 < p[k] < r1})
    pieces: list[tuple[int, int, dict]] = []
    for a, b in pairwise(cuts):
        middle = (a + b) / 2.0
        under = [p for p in pictures
                 if p["record_in"] <= middle < p["record_out"]]
        if not under:
            continue
        top = max(under, key=lambda p: p["track_index"])
        if pieces and pieces[-1][2] is top and pieces[-1][1] == a:
            pieces[-1] = (pieces[-1][0], b, top)
        else:
            pieces.append((a, b, top))
    return pieces


def keep_clear(overlays: Sequence[dict], pictures: Sequence[dict], *,
               fps: float, root: Path | None = None,
               roster: dict | None = None) -> dict:
    """The keep-clear report over plain data. Never raises on a gap: what
    cannot be read is listed in `skipped`.

    `overlays`: `label`, `box` (delivery-frame px, the drawn rect),
    `record_in`/`record_out` (timeline frames, out exclusive).
    `pictures`: `track_index`, `record_in`/`record_out`, `clip` (a
    name), `content_digest` (or None, with `digest_basis`),
    `source_in_seconds` (the source time at `record_in`), `speed`
    (1.0 = normal) and `rect` (where the source is drawn), or `skip`
    (why its rect cannot be trusted).
    `roster` maps `(content_digest, face_track)` to a person entry
    (`person_id`, `name`).
    """
    memory = _Memory(root)
    roster = roster or {}
    findings, clear, skipped = [], [], []
    for overlay in overlays:
        label = overlay["label"]
        pieces = _visible_pieces(int(overlay["record_in"]),
                                 int(overlay["record_out"]), pictures)
        if not pieces:
            skipped.append({"label": label, "reason": "no playing clip on a "
                            "picture row under this overlay"})
            continue
        overlaps, notes, pieces_read = [], [], 0
        for a, b, picture in pieces:
            where = {"clip": picture.get("clip"),
                     "timeline_seconds": [round(a / fps, 3),
                                          round(b / fps, 3)]}
            if picture.get("skip"):
                skipped.append({"label": label, **where,
                                "reason": picture["skip"]})
                continue
            digest = picture.get("content_digest")
            if not digest:
                skipped.append({"label": label, **where,
                                "reason": "source has no content digest "
                                f"({picture.get('digest_basis')})"})
                continue
            m3d, masks, m3e = memory.get(digest)
            if m3d is None and m3e is None:
                skipped.append({"label": label, **where,
                                "reason": "source memory carries neither "
                                "M3d (regions) nor M3e (saliency); run "
                                "`ren analyze --lanes regions saliency`"})
                continue
            pieces_read += 1
            box_src = box_in_source(overlay["box"], picture["rect"])
            if box_src is None:
                continue
            speed = float(picture.get("speed") or 1.0)
            s0 = (picture["source_in_seconds"]
                  + (a - picture["record_in"]) / fps * speed)
            s1 = (picture["source_in_seconds"]
                  + (b - picture["record_in"]) / fps * speed)
            source = {**where, "source_seconds": [round(s0, 3), round(s1, 3)],
                      "box_in_source": [round(v, 4) for v in box_src]}
            if m3d is None:
                notes.append({**where, "unread": "M3d (person regions) "
                              "missing or unreadable for this source"})
            else:
                read = samples_for(m3d["samples"], s0, s1)
                for row in person_overlaps(m3d, masks, box_src, read,
                                           digest, roster):
                    overlaps.append({**row, **source,
                                     "sampled_at": [s["t"] for s in read]})
            if m3e is None:
                notes.append({**where, "unread": "M3e (saliency) missing "
                              "for this source"})
            else:
                read = samples_for(m3e["samples"], s0, s1)
                for row in salient_overlaps(read, box_src):
                    overlaps.append({**row, **source,
                                     "sampled_at": [s["t"] for s in read]})
        entry = {"label": label, "box": [round(float(v), 1)
                                         for v in overlay["box"]],
                 "box_basis": overlay.get("box_basis", "canvas"),
                 "overlaps": overlaps}
        if notes:
            entry["notes"] = notes
        if overlaps:
            findings.append(entry)
        elif pieces_read:
            clear.append(entry)
    return {"checked": len(findings) + len(clear),
            "findings": findings,
            "clear": [({"label": c["label"], "notes": c["notes"]}
                       if c.get("notes") else c["label"]) for c in clear],
            "skipped": skipped}


# ── From the overlay sweep's reads ──────────────────────────────────


def _pair(text, sep: str = "x") -> tuple[float, float] | None:
    try:
        a, b = str(text).lower().split(sep)
        return float(a), float(b)
    except (TypeError, ValueError):
        return None


def _digests_by_path(project_folder: str) -> dict:
    """`{abspath: (digest, basis)}` over the project's catalog."""
    from library.tools import source_memory

    recorded = source_memory.load_recorded_fingerprints(project_folder)
    out = {}
    for clip in source_memory.load_catalog(project_folder):
        path = clip.get("source_file") or clip.get("path") or ""
        if not path:
            continue
        digest, basis = source_memory.digest_for_clip(project_folder, clip,
                                                      recorded)
        out[os.path.abspath(path)] = (digest, basis)
    return out


def _roster(project_folder: str, root: Path | None) -> dict:
    from library.tools.person_entity import resolve_person_tracks

    out = {}
    for person in resolve_person_tracks(project_folder, root)["persons"]:
        for track in person["face_tracks"]:
            out[(track["content_digest"], track["track_id"])] = person
    return out


def pictures_from_reads(items: Sequence[dict], project_folder: str,
                        full_wh: tuple, fps: float,
                        draw_gain: float = FALLBACK_DRAW_GAIN) -> list[dict]:
    """`reel_read.picture_items` rows -> the `pictures` `keep_clear` reads."""
    from library.tools.reel_framing import delivered_picture

    digests = _digests_by_path(project_folder)
    out = []
    for item in items:
        if item.get("enabled") is False:
            continue
        row = {"track_index": int(item["track_index"]),
               "record_in": int(item["record_in"]),
               "record_out": int(item["record_out"]),
               "clip": os.path.basename(item.get("source_file") or "")}
        size = _pair(item.get("resolution"))
        try:
            source_fps = float(item.get("source_fps"))
        except (TypeError, ValueError):
            source_fps = 0.0
        if (not item.get("source_file") or size is None or source_fps <= 0
                or item.get("source_in_frame") is None
                or item.get("transform") is None):
            row["skip"] = ("picture clip's file, resolution, rate, source "
                           "frame or transform did not read")
            out.append(row)
            continue
        picture = delivered_picture(size[0], size[1], full_wh[0], full_wh[1],
                                    item["transform"], draw_gain=draw_gain)
        if picture.crop_unread:
            row["skip"] = ("picture clip carries a Resolve crop the rect "
                           "model does not read")
            out.append(row)
            continue
        digest, basis = digests.get(os.path.abspath(item["source_file"]),
                                    (None, "not in the project catalog"))
        row.update({"content_digest": digest, "digest_basis": basis,
                    "source_in_seconds": (float(item["source_in_frame"])
                                          / source_fps),
                    "speed": float(item.get("speed") or 100.0) / 100.0,
                    "rect": picture.rect})
        out.append(row)
    return out


def overlays_from_reads(placed: Sequence[dict], stored: Sequence,
                        spans: Sequence, full_wh: tuple,
                        draw_gain: float = FALLBACK_DRAW_GAIN
                        ) -> tuple[list[dict], list[dict]]:
    """The sweep's placed entries, held transforms and read-back spans ->
    `(overlays, skipped)`. The rect is the canvas at the HELD transform."""
    from library.tools.tight_box import ink_screen_box

    overlays, skipped = [], []
    for entry, held, span in zip(placed, stored, spans):
        label = entry.get("label", "?")
        canvas = entry.get("canvas_wh")
        if held is None or not canvas or span is None:
            skipped.append({"label": label, "reason": "held transform, "
                            "canvas or span did not read back"})
            continue
        width, height = (float(v) for v in canvas)
        overlays.append({
            "label": label,
            "box": ink_screen_box(width, height, held,
                                  (0.0, 0.0, width, height),
                                  int(full_wh[0]), int(full_wh[1]),
                                  draw_gain),
            "box_basis": "canvas",
            "record_in": int(span[0]), "record_out": int(span[1])})
    return overlays, skipped


def keep_clear_from_reads(reads: dict, *, project_folder: str,
                          full_wh: tuple, fps: float,
                          draw_gain: float = FALLBACK_DRAW_GAIN,
                          root: Path | None = None) -> dict:
    """The sweep's half: `overlay_verify.read_reel_overlays` reads ->
    the keep-clear report, PRINTED to stderr. Never raises."""
    try:
        if not reads.get("pictures_read"):
            return {"unavailable": reads.get("pictures_unavailable")
                    or "the picture rows were not read"}
        overlays, skipped = overlays_from_reads(
            reads["placed"], reads["stored"], reads["spans"], full_wh,
            draw_gain)
        pictures = pictures_from_reads(reads["pictures"], project_folder,
                                       full_wh, fps, draw_gain)
        roster_failed = None
        try:
            roster = _roster(project_folder, root)
        except Exception as failed:  # noqa: BLE001 - owners stay face
            # tracks, and the report says why no person_id is named.
            roster, roster_failed = {}, f"{type(failed).__name__}: {failed}"
        report = keep_clear(overlays, pictures, fps=fps, root=root,
                            roster=roster)
        report["skipped"] = skipped + report["skipped"]
        if roster_failed:
            report["roster_unavailable"] = roster_failed
    except Exception as failed:  # noqa: BLE001 - report, never refuse
        print(f"  keep-clear unavailable ({failed}); the reel stands.",
              file=sys.stderr)
        return {"unavailable": str(failed)}
    for finding in report["findings"]:
        for hit in finding["overlaps"]:
            who = (hit.get("name") or hit.get("person_id")
                   or hit.get("face_track") or hit.get("region_track")
                   or "salient box")
            print(f"  keep-clear: {finding['label']} covers {hit['kind']} "
                  f"{who} ({hit['fraction_of_overlay']:.0%} of the overlay "
                  f"at peak, {hit['fraction_of_entity']:.0%} of the "
                  f"{hit['kind']})", file=sys.stderr)
    print(f"  keep-clear: {report['checked']} overlay(s) read, "
          f"{len(report['findings'])} over a person or salient box, "
          f"{len(report['skipped'])} skipped (reported, nothing moved).",
          file=sys.stderr)
    return report
