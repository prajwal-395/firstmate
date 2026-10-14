"""MU-09: `region_at` / `regions_overlapping` and region-carrying reads.

Pure read functions over a Project Model dict plus M3d (`regions.json`)
summaries. This is the agent-side half of "Ren knows where people are"
(multimodal-understanding plan §5.3): geometry answers without decoding
a mask, mask bytes read only by renderers.

**Time.** Seconds everywhere on the way in (source seconds, the unit
`ren where --at` and `ren search` hits already speak). Model track
samples are millisecond ticks on their own clock; the conversion is
`round(t * TICKS_PER_SECOND)`, the same floor/ceil-free point mapping
the evidence migration uses for point samples. A query between samples
is held on the track's latest sample at or before it while the track
covers it; a query in a gap between tracks is an absence, never the
nearest sample.

**Space.** Normalised top-left image coordinates, the same space M3d
summaries already use (`persons.json` `coordinates`). Viewer/timeline
transforms are computed by callers, never stored here.

**Identity.** A model region track names its entity directly. An M3d
region track names an M3b face track; the caller hands the
`face_to_person` mapping (roster `face_tracks`) that turns it into an
entity. Without the mapping, M3d rows report their face track and no
entity. Unassigned M3d tracks (no face inside, several faces, untracked
face, no majority) never resolve to a person.

**What is returned.** `region_at` answers `{"present": True, ...}` with
the stored summary (`bbox`, `centroid`, `interior_point`, `area`) or
`{"present": False, "reason": ...}` naming why (`unknown_person`,
`no_region_tracks`, `absent_at_time`). The interior point is the
stored distance-transform seed (`mask_seed.seed_point_in_mask`), so it
is inside the decoded mask for any shape - including an L-shape whose
bbox centre falls outside. `regions_overlapping` answers which
entities' bboxes touch a query box over an interval, by bbox overlap;
mask containment is a renderer's question, not this one.

Read-only: the only disk this module touches is the M3d slot and its
sidecar, through `person_regions.read_m3d`, and never for writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the same convention
:mod:`library.tools.project_model_evidence` migrates onto."""

REGION_GEOMETRY_KIND = "region"
"""The `TrackSample.geometry.kind` MU-08 region tracks carry. Model
tracks whose samples carry anything else are not regions and are never
answered here."""

REGION_COORDINATE_SPACE = "source-normalised-tl"
"""The coordinate space region geometry is stored in (§5.2). Samples in
any other space are left out rather than converted: a pixel-space box
compared against a normalised query is the silent mis-read this module
exists to stop."""

ABSENT_UNKNOWN_PERSON = "unknown_person"
ABSENT_NO_TRACKS = "no_region_tracks"
ABSENT_AT_TIME = "absent_at_time"
ABSENT_REASONS = (ABSENT_UNKNOWN_PERSON, ABSENT_NO_TRACKS, ABSENT_AT_TIME)
"""Every absence `region_at` can report. Complete: a caller matching on
`reason` never meets one not listed here."""


def _resolve_entity(model: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """The person entity `ref` names, by id then by label. None when neither."""
    want = str(ref or "").strip()
    if not want:
        return None
    for candidate in model.get("entities") or []:
        if candidate.get("kind") != "person":
            continue
        if candidate.get("id") == want:
            return candidate
    lowered = want.lower()
    for candidate in model.get("entities") or []:
        if (candidate.get("kind") == "person"
                and isinstance(candidate.get("label"), str)
                and candidate["label"].lower() == lowered):
            return candidate
    return None


def _is_region_sample(sample: Any) -> bool:
    return (isinstance(sample, dict)
            and isinstance(sample.get("geometry"), dict)
            and sample["geometry"].get("kind") == REGION_GEOMETRY_KIND
            and (sample.get("coordinate_space") in
                 (REGION_COORDINATE_SPACE, None, "")))


def is_region_track(track: Any) -> bool:
    """Whether a model track is a region track: at least one sample
    carries `geometry.kind == "region"` in the normalised space."""
    if not isinstance(track, dict):
        return False
    return any(_is_region_sample(sample)
               for sample in track.get("samples") or [])


def region_tracks_for(model: dict[str, Any],
                      entity_id: str) -> list[dict[str, Any]]:
    """Every region track the model holds for one entity, in model order."""
    return [track for track in model.get("tracks") or []
            if isinstance(track, dict)
            and track.get("entity_id") == entity_id
            and is_region_track(track)]


def _clock_of(track: dict[str, Any]) -> str | None:
    clock = track.get("clock_id")
    return clock if isinstance(clock, str) else None


def _sample_tick(sample: dict[str, Any]) -> int | None:
    try:
        return int(sample.get("tick"))
    except (TypeError, ValueError):
        return None


def _model_cover_at(tracks: list[dict[str, Any]], tick: int,
                    clock_id: str | None) -> dict[str, Any] | None:
    """The latest region sample at or before `tick` on a track covering
    it, or None when no track does. Cover is the track's own span
    (first to last sample tick): inside it the subject is held on the
    latest sample, outside every span the person is absent."""
    best: dict[str, Any] | None = None
    best_tick = -1
    for track in tracks:
        if clock_id is not None and _clock_of(track) != clock_id:
            continue
        samples = [s for s in track.get("samples") or []
                   if _is_region_sample(s) and _sample_tick(s) is not None]
        if not samples:
            continue
        ticks = [_sample_tick(s) for s in samples]  # type: ignore[misc]
        first, last = min(ticks), max(ticks)  # type: ignore[type-var]
        if not (first <= tick <= last):
            continue
        for sample in samples:
            sample_tick = _sample_tick(sample)
            assert sample_tick is not None
            if sample_tick <= tick and sample_tick > best_tick:
                best_tick = sample_tick
                best = {"track": track, "sample": sample}
    return best


def _summarise_model_hit(entity_id: str, cover: dict[str, Any],
                         tick: int) -> dict[str, Any]:
    track = cover["track"]
    sample = cover["sample"]
    geometry = dict(sample["geometry"])
    return {
        "present": True,
        "entity_id": entity_id,
        "track_id": track.get("id"),
        "clock_id": _clock_of(track),
        "tick": tick,
        "sample_tick": _sample_tick(sample),
        "bbox": geometry.get("bbox"),
        "centroid": geometry.get("centroid"),
        "interior_point": geometry.get("interior_point"),
        "area": geometry.get("area"),
        "confidence": sample.get("confidence"),
        "source": "project_model",
    }


# ── M3d (pre-MU-08: the lane MU-05 measures, the model does not yet hold) ──


def _m3d_person_tracks(doc: dict[str, Any],
                       face_tracks: set[str]) -> list[dict[str, Any]]:
    """The doc's region tracks owned by one person's M3b face tracks."""
    return [track for track in doc.get("tracks") or []
            if isinstance(track, dict)
            and track.get("face_track") in face_tracks]


def _m3d_cover_at(doc: dict[str, Any], person_tracks: list[dict[str, Any]],
                  moment: float) -> dict[str, Any] | None:
    """The latest M3d instance at or before `moment` on a person track
    covering it. Same held-span semantic as the model path: a moment in
    a gap between tracks is absent, never the nearest sample."""
    owned = {track["track_id"] for track in person_tracks
             if isinstance(track.get("track_id"), str)}
    spans = {track["track_id"]: (float(track["start"]), float(track["end"]))
             for track in person_tracks
             if isinstance(track.get("track_id"), str)}
    best: dict[str, Any] | None = None
    best_t = -1.0
    for sample in doc.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        try:
            sample_t = float(sample["t"])
        except (TypeError, ValueError, KeyError):
            continue
        if sample_t > moment or sample_t <= best_t:
            continue
        for inst in sample.get("instances") or []:
            if not isinstance(inst, dict):
                continue
            track_id = inst.get("track")
            if track_id not in owned:
                continue
            start, end = spans[track_id]
            if not (start <= moment <= end):
                continue
            best_t = sample_t
            best = {"sample_t": sample_t, "instance": inst,
                    "track_id": track_id}
    return best


def _summarise_m3d_hit(entity_id: str, digest: str,
                       cover: dict[str, Any]) -> dict[str, Any]:
    inst = cover["instance"]
    return {
        "present": True,
        "entity_id": entity_id,
        "track_id": cover["track_id"],
        "content_digest": digest,
        "t": cover["sample_t"],
        "tick": round(cover["sample_t"] * TICKS_PER_SECOND),
        "bbox": inst.get("bbox"),
        "centroid": inst.get("centroid"),
        "interior_point": inst.get("interior_point"),
        "area": inst.get("area"),
        "containment": inst.get("containment"),
        "source": "m3d",
    }


def _absence(reason: str, *, entity_id: str | None = None,
             detail: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"present": False, "reason": reason}
    if entity_id is not None:
        out["entity_id"] = entity_id
    if detail:
        out["detail"] = detail
    return out


def region_at(model: dict[str, Any], ref: str, t: float,
              clock_id: str | None = None,
              digest: str | None = None, *,
              m3d: dict[str, dict[str, Any]] | None = None,
              face_to_person: dict[tuple[str, str], str] | None = None,
              ) -> dict[str, Any]:
    """Where one person is at one moment.

    `model` is the Project Model dict, `ref` an entity id or label,
    `t` source seconds. The model is read first: a region track
    covering `t` answers from its held sample. Otherwise the M3d
    summaries in `m3d` (`{content_digest: regions.json doc}`) answer
    through the person's roster face tracks. `face_to_person` maps
    `(digest, m3b_face_track)` to the entity id and is how an M3d
    track becomes this person's; without it the M3d path stays silent.

    An absence carries a `reason` (`unknown_person`,
    `no_region_tracks`, `absent_at_time`), never the nearest sample's
    geometry.
    """
    entity = _resolve_entity(model, ref)
    if entity is None:
        return _absence(ABSENT_UNKNOWN_PERSON)
    entity_id = entity["id"]
    try:
        moment = float(t)
    except (TypeError, ValueError):
        return _absence(ABSENT_AT_TIME, entity_id=entity_id,
                        detail={"t": t, "why": "not a time"})
    tick = round(moment * TICKS_PER_SECOND)

    tracks = region_tracks_for(model, entity_id)
    if digest is not None or clock_id is not None:
        wanted = {digest, clock_id}
        tracks = [track for track in tracks
                  if _clock_of(track) in wanted
                  or _digest_of_clock(model, _clock_of(track)) in wanted]
    cover = _model_cover_at(tracks, tick, None)
    if cover is not None:
        return _summarise_model_hit(entity_id, cover, tick)

    if m3d:
        face_tracks = {face for (face_digest, face), owner
                       in (face_to_person or {}).items()
                       if owner == entity_id
                       and (digest is None or face_digest == digest)}
        for face_digest, doc in m3d.items():
            if not isinstance(doc, dict):
                continue
            if digest is not None and face_digest != digest:
                continue
            person_tracks = _m3d_person_tracks(doc, face_tracks)
            if not person_tracks:
                continue
            hit = _m3d_cover_at(doc, person_tracks, moment)
            if hit is not None:
                return _summarise_m3d_hit(entity_id, face_digest, hit)

    if not tracks and not any(
            _m3d_person_tracks(doc, {face for (face_digest, face), owner
                                     in (face_to_person or {}).items()
                                     if owner == entity_id
                                     and face_digest == doc_digest})
            for doc_digest, doc in (m3d or {}).items()
            if isinstance(doc, dict)):
        return _absence(ABSENT_NO_TRACKS, entity_id=entity_id)
    return _absence(ABSENT_AT_TIME, entity_id=entity_id,
                    detail={"t": moment, "tick": tick})


def _digest_of_clock(model: dict[str, Any],
                     clock_id: str | None) -> str | None:
    """The content digest a model clock ticks for, via its asset."""
    if not clock_id:
        return None
    for clock in model.get("clocks") or []:
        if (isinstance(clock, dict) and clock.get("id") == clock_id
                and isinstance(clock.get("domain"), dict)):
            asset_id = clock["domain"].get("asset_id")
            for asset in model.get("assets") or []:
                if (isinstance(asset, dict) and asset.get("id") == asset_id
                        and asset.get("content_digest")):
                    return asset["content_digest"]
    return None


def _boxes_overlap(a: list[float], b: list[float]) -> bool:
    """Whether two normalised `[x0, y0, x1, y1]` boxes touch."""
    try:
        ax0, ay0, ax1, ay1 = (float(v) for v in a)
        bx0, by0, bx1, by1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return False
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def _check_box(box: Any) -> list[float]:
    values = list(box)
    if len(values) != 4:
        raise ValueError(f"a region box is [x0, y0, x1, y1], got {box!r}")
    numbers = [float(v) for v in values]
    x0, y0, x1, y1 = numbers
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise ValueError(f"a region box lives in the unit square, got {box!r}")
    return numbers


def regions_overlapping(model: dict[str, Any], box: list[float],
                        start: float, end: float,
                        clock_id: str | None = None,
                        digest: str | None = None, *,
                        m3d: dict[str, dict[str, Any]] | None = None,
                        face_to_person: dict[tuple[str, str], str]
                        | None = None,
                        ) -> list[dict[str, Any]]:
    """Which entities' regions touch `box` between `start` and `end`.

    `box` is `[x0, y0, x1, y1]` in normalised top-left coordinates;
    `start`/`end` are source seconds. Model region tracks answer by
    bbox overlap at each sample in the interval; M3d summaries answer
    the same way through `face_to_person`. A malformed box is refused,
    never clipped. An empty interval answers empty, never everything.
    """
    query = _check_box(box)
    try:
        lo, hi = float(start), float(end)
    except (TypeError, ValueError):
        raise ValueError(
            f"a region interval is two seconds, got {start!r}, {end!r}")
    if not hi > lo:
        return []
    lo_tick, hi_tick = round(lo * TICKS_PER_SECOND), round(hi * TICKS_PER_SECOND)

    hits: dict[str, dict[str, Any]] = {}
    for track in model.get("tracks") or []:
        if not is_region_track(track):
            continue
        if clock_id is not None and _clock_of(track) != clock_id:
            continue
        if digest is not None and _digest_of_clock(
                model, _clock_of(track)) != digest:
            continue
        for sample in track.get("samples") or []:
            if not _is_region_sample(sample):
                continue
            sample_tick = _sample_tick(sample)
            if sample_tick is None or not lo_tick <= sample_tick < hi_tick:
                continue
            geometry = sample["geometry"]
            if not _boxes_overlap(query, geometry.get("bbox") or []):
                continue
            entity_id = track.get("entity_id")
            row = hits.setdefault(entity_id, {
                "entity_id": entity_id, "track_id": track.get("id"),
                "clock_id": _clock_of(track), "samples": 0,
                "first_tick": sample_tick, "source": "project_model"})
            row["samples"] += 1
            row["first_tick"] = min(row["first_tick"], sample_tick)

    for face_digest, doc in (m3d or {}).items():
        if not isinstance(doc, dict):
            continue
        if digest is not None and face_digest != digest:
            continue
        for sample in doc.get("samples") or []:
            if not isinstance(sample, dict):
                continue
            try:
                sample_t = float(sample["t"])
            except (TypeError, ValueError, KeyError):
                continue
            if not lo <= sample_t < hi:
                continue
            for inst in sample.get("instances") or []:
                if not isinstance(inst, dict):
                    continue
                if not _boxes_overlap(query, inst.get("bbox") or []):
                    continue
                owner = (face_to_person or {}).get(
                    (face_digest, inst.get("face_track") or ""))
                if owner is None:
                    track_owner = next(
                        (track.get("face_track") for track in doc.get("tracks")
                         or [] if isinstance(track, dict)
                         and track.get("track_id") == inst.get("track")), None)
                    owner = (face_to_person or {}).get(
                        (face_digest, track_owner or ""))
                if owner is None:
                    continue
                row = hits.setdefault(owner, {
                    "entity_id": owner, "track_id": inst.get("track"),
                    "content_digest": face_digest, "samples": 0,
                    "first_t": sample_t, "source": "m3d"})
                row["samples"] += 1
                if "first_t" in row:
                    row["first_t"] = min(row["first_t"], sample_t)
    return sorted(hits.values(), key=lambda row: str(row["entity_id"]))


# ── Roster path (footage search: no Project Model needed) ──


def roster_persons_for_tokens(roster: list[dict[str, Any]],
                              tokens: list[str]) -> list[dict[str, Any]]:
    """Roster persons a query names, by the same both-directions
    substring rule `_person_scores` ranks with: a token matches when it
    contains the person id/name or is contained in it, case-insensitive.
    The rule lives here once so search ranking and search regions agree
    on who a hit is about."""
    matched = []
    for person in roster or []:
        names = [person.get("person_id", "")]
        if person.get("name"):
            names.append(person["name"])
        if any(token and (token in (name or "").lower()
                          or (name or "").lower() in token)
               for token in tokens for name in names):
            matched.append(person)
    return matched


def m3d_region_at(doc: dict[str, Any], face_tracks: set[str],
                  moment: float) -> dict[str, Any]:
    """One M3d doc's region at one moment, for one person's face tracks.

    Entity-free: the caller names whose face tracks these are. Answers
    the stored summary or an absence (`no_region_tracks` when the doc
    holds no track they own, `absent_at_time` for a moment no owned
    track covers) - never the nearest sample.
    """
    try:
        moment_f = float(moment)
    except (TypeError, ValueError):
        return {"present": False, "reason": ABSENT_AT_TIME,
                "detail": {"t": moment, "why": "not a time"}}
    person_tracks = _m3d_person_tracks(doc, set(face_tracks))
    if not person_tracks:
        return {"present": False, "reason": ABSENT_NO_TRACKS}
    hit = _m3d_cover_at(doc, person_tracks, moment_f)
    if hit is None:
        return {"present": False, "reason": ABSENT_AT_TIME,
                "detail": {"t": moment_f}}
    inst = hit["instance"]
    return {
        "present": True,
        "track_id": hit["track_id"],
        "t": hit["sample_t"],
        "bbox": inst.get("bbox"),
        "centroid": inst.get("centroid"),
        "interior_point": inst.get("interior_point"),
        "area": inst.get("area"),
        "containment": inst.get("containment"),
        "source": "m3d",
    }


# ── Disk: the M3d summaries behind the pure reads ──


def load_m3d_for_digests(digests: list[str],
                         root: Path | str | None = None,
                         ) -> dict[str, dict[str, Any]]:
    """Every M3d doc the store holds for `digests`, keyed by digest.

    A digest with no measured M3d is absent, never null: a reader
    cannot place a sourceless row, so there is no row for it.
    """
    from library.tools import person_regions

    docs: dict[str, dict[str, Any]] = {}
    for digest in dict.fromkeys(digests):
        doc = person_regions.read_m3d(digest, root)
        if doc is not None:
            docs[digest] = doc
    return docs


def face_to_person_for_project(
        project_folder: str | Path,
        root: Path | str | None = None) -> dict[tuple[str, str], str]:
    """`(digest, m3b_face_track)` -> roster person id, for one project.

    The roster is the measured face-identity clustering; a face track
    it never vouched for maps nowhere rather than to a guess.
    """
    try:
        from library.tools import person_entity
    except ImportError:  # same-tree import, as in project_model_evidence
        from tools import person_entity  # type: ignore[no-redef]

    try:
        roster = person_entity.resolve_person_tracks(str(project_folder), root)
    except Exception:  # noqa: BLE001 - unbuilt store is an empty mapping
        return {}
    mapping: dict[tuple[str, str], str] = {}
    for person in (roster or {}).get("persons") or []:
        person_id = person.get("person_id")
        if not person_id:
            continue
        for track in person.get("face_tracks") or []:
            if not isinstance(track, dict):
                continue
            face_digest = track.get("content_digest")
            track_id = track.get("track_id")
            if face_digest and track_id:
                mapping[(face_digest, track_id)] = person_id
    return mapping


# ── Object region tracks (MU-15: detector seed → SAM 2 → ObjectClass) ──


def _resolve_object_entity(model: dict[str, Any],
                           ref: str) -> dict[str, Any] | None:
    """The object entity `ref` names, in model order.

    By id (exact), then label (case-insensitive exact), then the
    both-directions substring rule `roster_persons_for_tokens` ranks
    persons with - over the label, the `ObjectClass` name and the class
    id. So "laptop" answers the entity labelled "black laptop" whose
    class is `class-laptop`: Gemma phrases are descriptions, not ids,
    and the query speaks Gemma's words. A ref matching nothing is None,
    never the nearest object.
    """
    want = str(ref or "").strip()
    if not want:
        return None
    objects = [c for c in model.get("entities") or []
               if isinstance(c, dict) and c.get("kind") == "object"]
    for candidate in objects:
        if candidate.get("id") == want:
            return candidate
    lowered = want.lower()
    for candidate in objects:
        if (isinstance(candidate.get("label"), str)
                and candidate["label"].lower() == lowered):
            return candidate
    classes = {c.get("id"): c for c in model.get("object_classes") or []
               if isinstance(c, dict) and c.get("id")}

    def _names(candidate: dict[str, Any]) -> list[str]:
        out = []
        for text in (candidate.get("label"),
                     (classes.get(candidate.get("object_class_id") or "")
                      or {}).get("name"),
                     candidate.get("object_class_id")):
            if isinstance(text, str) and text.strip():
                out.append(text.strip().lower())
        return out

    def _matches(names: list[str]) -> bool:
        return any(lowered in name or name in lowered for name in names)

    for candidate in objects:
        if _matches(_names(candidate)):
            return candidate
    return None


def _pixel_space_dimensions(sample: Any) -> tuple[int, int] | None:
    """The (width, height) a `bbox_pixels` sample lives in, from its
    `coordinate_space` (`source_pixels_WxH`). None when the space is
    absent or malformed: a pixel box without its frame size is never
    compared against a normalised query."""
    space = sample.get("coordinate_space") if isinstance(sample, dict) \
        else None
    if not isinstance(space, str) or not space.startswith("source_pixels_"):
        return None
    try:
        dims = space[len("source_pixels_"):].lower().split("x")
        width, height = int(dims[0]), int(dims[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _object_sample_box(sample: Any) -> list[float] | None:
    """One object-track sample's bbox in normalised top-left coords.

    Region-kind samples (`geometry.kind == "region"`) already carry it;
    1.06 segmentation samples carry `bbox_pixels` (x, y, w, h) with
    their frame size in `coordinate_space`. Anything else is not an
    object region sample and answers None."""
    if not isinstance(sample, dict):
        return None
    geometry = sample.get("geometry")
    if not isinstance(geometry, dict):
        return None
    if geometry.get("kind") == REGION_GEOMETRY_KIND and isinstance(
            geometry.get("bbox"), (list, tuple)) and len(
            geometry["bbox"]) == 4:
        try:
            return [float(v) for v in geometry["bbox"]]
        except (TypeError, ValueError):
            return None
    pixels = geometry.get("bbox_pixels")
    if pixels is None:
        return None
    dims = _pixel_space_dimensions(sample)
    if dims is None:
        return None
    try:
        x, y, w, h = (float(v) for v in pixels)
    except (TypeError, ValueError):
        return None
    width, height = dims
    if w <= 0 or h <= 0:
        return None
    return [x / width, y / height, (x + w) / width, (y + h) / height]


def is_object_track(track: Any) -> bool:
    """Whether a model track is an object region track: at least one
    sample answers a normalised bbox through `_object_sample_box`."""
    if not isinstance(track, dict):
        return False
    return any(_object_sample_box(sample) is not None
               for sample in track.get("samples") or [])


def object_tracks_for(model: dict[str, Any],
                      entity_id: str) -> list[dict[str, Any]]:
    """Every object region track the model holds for one entity."""
    return [track for track in model.get("tracks") or []
            if isinstance(track, dict)
            and track.get("entity_id") == entity_id
            and is_object_track(track)]


def object_region_at(model: dict[str, Any], ref: str, t: float,
                     clock_id: str | None = None,
                     digest: str | None = None) -> dict[str, Any]:
    """Where one NAMED OBJECT is at one moment.

    `ref` is an object entity id, its label, or its `ObjectClass` name
    ("laptop"); `t` is source seconds. The track's own span (first to
    last sample tick) is the cover: inside it the object is held on the
    latest sample at or before `t`; outside every span - or where the
    model holds no object track - the answer is an absence with a
    reason (`unknown_person` for an unresolvable ref, `no_region_tracks`
    where the entity has no track, `absent_at_time` in a gap), never
    the nearest sample's geometry.
    """
    entity = _resolve_object_entity(model, ref)
    if entity is None:
        return _absence(ABSENT_UNKNOWN_PERSON)
    entity_id = entity["id"]
    try:
        moment = float(t)
    except (TypeError, ValueError):
        return _absence(ABSENT_AT_TIME, entity_id=entity_id,
                        detail={"t": t, "why": "not a time"})
    tick = round(moment * TICKS_PER_SECOND)
    tracks = object_tracks_for(model, entity_id)
    if digest is not None or clock_id is not None:
        wanted = {digest, clock_id}
        tracks = [track for track in tracks
                  if _clock_of(track) in wanted
                  or _digest_of_clock(model, _clock_of(track)) in wanted]
    best: dict[str, Any] | None = None
    best_tick = -1
    for track in tracks:
        samples = [(s, _sample_tick(s), _object_sample_box(s))
                   for s in track.get("samples") or []]
        samples = [(s, tk, box) for s, tk, box in samples
                   if tk is not None and box is not None]
        if not samples:
            continue
        ticks = [tk for _, tk, _ in samples]
        first, last = min(ticks), max(ticks)
        if not (first <= tick <= last):
            continue
        for sample, sample_tick, box in samples:
            if sample_tick <= tick and sample_tick > best_tick:
                best_tick = sample_tick
                best = {"track": track, "sample": sample, "box": box}
    if best is None:
        if not tracks:
            return _absence(ABSENT_NO_TRACKS, entity_id=entity_id)
        return _absence(ABSENT_AT_TIME, entity_id=entity_id,
                        detail={"t": moment, "tick": tick})
    track, sample = best["track"], best["sample"]
    return {
        "present": True,
        "entity_id": entity_id,
        "track_id": track.get("id"),
        "clock_id": _clock_of(track),
        "tick": tick,
        "sample_tick": best_tick,
        "bbox": best["box"],
        "confidence": sample.get("confidence"),
        "source": "project_model",
    }


def object_track_gaps(model: dict[str, Any], ref: str) -> dict[str, Any]:
    """Where one named object's track holds no measurement (VI-05).

    Reads the `object_visibility` observations the VI-05 migration
    writes: visible sample ticks plus the explicit gap intervals
    between the object's first and last sample. A gap is unmeasured
    coverage, never an occlusion verdict. An object with no
    visibility observation was migrated before VI-05 (or never
    migrated): refused by name, never derived from a guess about the
    sample cadence. An unresolvable ref is refused the same way -
    absences are `object_region_at`'s answer, not this one's.
    """
    entity = _resolve_object_entity(model, ref)
    if entity is None:
        raise ValueError(f"no object entity matches {ref!r}")
    entity_id = entity["id"]
    rows = [o for o in model.get("observations") or []
            if isinstance(o, dict) and o.get("modality") == "object_visibility"
            and isinstance(o.get("payload"), dict)
            and (o["payload"].get("object_id") is not None)]
    owned = [o for o in rows
             if _visibility_owner(model, o) == entity_id]
    if not owned:
        raise ValueError(
            f"object {entity_id!r} carries no object_visibility "
            f"observation: migrate with VI-05 evidence first")
    if len(owned) > 1:
        raise ValueError(
            f"object {entity_id!r} carries {len(owned)} visibility "
            f"observations, expected one")
    payload = owned[0]["payload"]
    return {"entity_id": entity_id,
            "observation_id": owned[0].get("id"),
            "visible_ticks": list(payload.get("visible_ticks") or []),
            "gaps": [dict(gap) for gap in payload.get("gaps") or []]}


def _visibility_owner(model: dict[str, Any],
                      observation: dict[str, Any]) -> str | None:
    """The object entity an `object_visibility` observation belongs
    to: its evidence record's target first, else the entity whose
    track the payload's track_id names. None when neither resolves -
    an orphaned row belongs to nobody."""
    contract = (observation.get("payload") or {}).get("evidence_contract")
    if isinstance(contract, dict):
        target = contract.get("target_entity_id")
        if isinstance(target, str) and target:
            return target
    track_id = (observation.get("payload") or {}).get("track_id")
    if not isinstance(track_id, str):
        return None
    for track in model.get("tracks") or []:
        if isinstance(track, dict) and track.get("id") == track_id:
            entity_id = track.get("entity_id")
            return entity_id if isinstance(entity_id, str) else None
    return None


def object_track_coverage(model: dict[str, Any], ref: str,
                          ground_truth: dict[int, list[float]], *,
                          project_folder: str | Path | None = None,
                          iou_threshold: float = 0.5) -> dict[str, Any]:
    """How much of a named object's sampled span its MASKS cover.

    `ground_truth` maps sample tick -> normalised laptop box (the
    fixture's known geometry, or hand-labelled boxes on captain
    frames). Each ground-truth tick is held back to the mask's latest
    sample at or before it (Ren's held-matte semantic); the decoded
    mask pixels and the box pixels meet at IoU, covered at
    `iou_threshold`. Answers `{frames, covered, fraction, per_frame}`.

    Only `sam2_rle_track` masks resolve today (the kind the 1.06
    migration writes for detector-seeded objects); any other kind is
    refused by name, never approximated. A ground-truth tick before
    the first mask sample is a miss, never filled by a guess.
    """
    from library.tools import project_model_masks

    entity = _resolve_object_entity(model, ref)
    if entity is None:
        raise ValueError(f"no object entity matches {ref!r}")
    entity_id = entity["id"]
    masks = [m for m in model.get("masks") or []
             if isinstance(m, dict) and m.get("entity_id") == entity_id]
    if not masks:
        raise ValueError(f"object {entity_id!r} carries no masks")
    try:
        ticks = sorted(int(k) for k in ground_truth)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"ground truth ticks are millisecond ints, got {exc}") from exc
    if not ticks:
        raise ValueError("ground truth holds no frame to score")
    per_frame = []
    covered = 0
    for tick in ticks:
        try:
            box = [float(v) for v in ground_truth[tick]]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"ground-truth box at tick {tick} is not four numbers: "
                f"{exc}") from exc
        row: dict[str, Any] = {"tick": tick, "iou": 0.0, "covered": False}
        per_frame.append(row)
        mask = next((m for m in masks
                     if _mask_covers_tick(model, m, tick, project_folder)),
                    None)
        if mask is None:
            continue
        iou = _mask_box_iou(mask, tick, box, project_folder)
        row["iou"] = round(iou, 3)
        row["covered"] = bool(iou >= iou_threshold)
        covered += row["covered"]
    return {"entity_id": entity_id, "frames": len(ticks),
            "covered": covered,
            "fraction": round(covered / len(ticks), 3),
            "iou_threshold": iou_threshold, "per_frame": per_frame}


def _mask_covers_tick(model: dict[str, Any], mask: dict[str, Any],
                      tick: int, project_folder: str | Path | None) -> bool:
    """Whether a mask entry has a sample at or before `tick`."""
    from library.tools import project_model_masks

    representation = mask.get("representation") or {}
    if representation.get("kind") != project_model_masks.SAM2_RLE_TRACK_KIND:
        raise ValueError(
            f"object mask {mask.get('id')!r} kind "
            f"{representation.get('kind')!r} is not "
            f"{project_model_masks.SAM2_RLE_TRACK_KIND!r}: "
            f"coverage resolves sam2 tracks, never approximates")
    _, _, frames = project_model_masks.sam2_track_frames(
        representation, project_folder=project_folder,
        mask_id=str(mask.get("id") or "mask"))
    return any(frame_tick <= tick for frame_tick, _ in frames)


def _mask_box_iou(mask: dict[str, Any], tick: int, box: list[float],
                  project_folder: str | Path | None) -> float:
    """IoU of a held mask sample's pixels against a normalised box."""
    from library.tools import project_model_masks

    representation = mask.get("representation") or {}
    width, height, frames = project_model_masks.sam2_track_frames(
        representation, project_folder=project_folder,
        mask_id=str(mask.get("id") or "mask"))
    held = project_model_masks.rle_frame_at(
        frames, tick, mask_id=str(mask.get("id") or "mask"))
    alpha = project_model_masks.decode_rle_to_alpha(
        held, width, height, mask_id=str(mask.get("id") or "mask"))
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return 0.0
    px0, py0 = int(x0 * width), int(y0 * height)
    px1, py1 = int(x1 * width), int(y1 * height)
    if px1 <= px0 or py1 <= py0:
        return 0.0
    inter = box_area = 0
    mask_area = sum(1 for b in alpha if b)
    for py in range(max(0, py0), min(height, py1)):
        base = py * width
        for px in range(max(0, px0), min(width, px1)):
            box_area += 1
            if alpha[base + px]:
                inter += 1
    union = mask_area + box_area - inter
    return inter / union if union > 0 else 0.0
