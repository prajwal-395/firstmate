"""`ren preview`: a watchable, low-cost file of each built reel.

Why this exists
---------------
After `ren build` the only way to see a reel was to open Resolve and
play the timeline, and the only file Ren made was the delivery - the
full-quality render the captain asks for by name (`reel_deliver`). The
carefree loop (plan item T12) wants a file to WATCH after every build
and revision. This verb makes one, per approved and built reel, and
names it.

It is a verb, not a step
------------------------
The captain's standing ruling (2026-09-09/10) is that a render is the
expensive thing they must ask for, and `tests/contracts/
test_reel_deliver_is_explicit.py` keeps every build path away from a
render. Whether a preview may run after a build without being asked is
the captain's call and is NOT made here: this module is reached only
from `ren preview`, and nothing in `build-reels`, `run`, `revise` or the
DAGs calls it. What it settles is everything that call would need: the
preview spec, the cache key and the concurrency budget.

The preview spec
----------------
`PREVIEW_SPEC`: H.264 + AAC in MP4 at HALF the reel's frame (even
dimensions), CRF 28 at the `veryfast` preset, 128 kb/s audio. It is a
proxy for watching, never a deliverable: no mastering, no loudness
normalisation, no QC beyond "it plays and is as long as the reel".
The engine renders, then ONE proxy encode makes the preview file.

Two engines
-----------
- `filmcraft` (the default, headless): the reel's slice of the Project
  Model the build maintains (`reel_project_model`) is projected and
  exported by the pinned FilmCraft CLI through `project_model_cli` -
  the same adapter `ren project-model --engine filmcraft --execute`
  runs. No Resolve process is reached, so a preview never moves the
  captain's cursor or queues a job in their render page. What the
  adapter could not project is recorded on the preview as `losses`.
- `resolve`: the live reel timeline through `resolve_render.
  render_timeline`, the path `ren deliver` renders with - it carries
  everything Resolve draws, at Resolve's cost.

`auto` takes FilmCraft when the adapter's own plan accepts the reel's
slice, and Resolve otherwise, recording the adapter's reason on the
preview (`headless_refused`). Today that is every multicam conversation
reel: the build mints its dialogue rows as `speech` uses and its angles
as stacked base picture, and the pinned adapter (`native/ren-filmcraft/
src/adapter.rs`) maps neither - teaching it to is engine work, not this
verb's. FilmCraft draws what the model carries and nothing else: a
caption the build placed only as a Resolve overlay is not in the model,
so it is not in a headless preview.

The cache key
-------------
A preview is keyed by CONTENT, not by time: the spec, the engine and its
pinned revision, and what the reel IS as Ren recorded it -

- the build's own signatures for the reel (`plan_provenance.
  reel_content_hashes`: derivation, carried, caption and footage
  binding), so a rebuild that changes anything changes the key;
- the newest Ren-verified snapshot of the timeline (a `ren touch` or
  `ren revise` records one), so an in-place change changes the key;
- for FilmCraft, the reel's model slice and the size/mtime of every
  source file it imports.

A preview whose key file is on disk and whose sidecar passed is a CACHE
HIT: nothing renders, and the hit is recorded in `previews.json` and on
the assignment like a render is. A reel Ren has no record of at all
(no signatures, no snapshot, no model slice) cannot be keyed and is
always rendered - fail-closed, said as `uncached`.

The concurrency budget
----------------------
A miss renders under the `reel_preview` scheduler profile
(`resource_scheduler`): engine then proxy encode in turn, never nested,
under grants that gate on live OS memory headroom. The scheduler no
longer serialises a preview against a Resolve render or a model pass:
Resolve's own lease arbitrates Resolve work. The Resolve engine
renders under `render.build:render`, `render_timeline`'s own grant, and
its proxy encode under `reel_preview` afterwards - the two are taken in
turn, never nested. A cache hit takes no grant at all.

Where it lands
--------------
`pipeline_output/previews/<reel slug>/<key>.mp4` beside a
`<key>.preview.json` sidecar, and `pipeline_output/previews/
previews.json`, the record of every render and cache hit. The verb's
last lines name the file to watch and the round's review page
(`round_page`, rewritten after every preview), and a macOS notice names
the file too (`completion_notice`, `REN_NOTIFY=0` opts out).

`tests/scenarios/test_reel_preview.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

PREVIEW_SPEC = {
    "version": 1,
    "scale_divisor": 2,
    "container": "mp4",
    "video_codec": "libx264",
    "x264_preset": "veryfast",
    "crf": 28,
    "pix_fmt": "yuv420p",
    "audio_codec": "aac",
    "audio_bitrate": "128k",
}
"""What a preview is. Changing any value changes every cache key."""

ENGINES = ("filmcraft", "resolve")
PROFILE = "reel_preview"
INDEX_FILE = "previews.json"
MAX_INDEX_EVENTS = 500
"""`previews.json` keeps the newest events; the files themselves are
the cache, so dropping old events forgets history, never a preview."""


class PreviewRefused(RenRefusal):
    """The preview cannot be made, and this says why."""

    CODE = "REN_PREVIEW_REFUSED"


# ── Which reels ─────────────────────────────────────────────────────


def _slug(text: str) -> str:
    from library.tools.reel_project_model import _slug as model_slug
    return model_slug(text)


def reel_targets(project: str, reels: list[int] | None = None) -> list[dict]:
    """Every approved reel the request names, with its built timeline.

    Returns `{number, timeline, slug, built}` per reel. A named reel the
    plan does not have, or has not approved, is refused by name; an
    approved reel that was never built is returned `built: False` so the
    caller says so instead of rendering nothing.
    """
    from library.tools import reel_deliver
    from library.tools.reel_proposal import Approval

    moments = reel_deliver._proposal_moments(project)
    by_number = {int(m.number): m for m in moments}
    wanted = sorted({int(n) for n in reels}) if reels else sorted(
        n for n, m in by_number.items() if m.approval is Approval.APPROVED)
    targets = []
    for number in wanted:
        moment = by_number.get(number)
        if moment is None:
            raise PreviewRefused(
                f"the plan names no reel {number}",
                f"known: {sorted(by_number) or '(none)'}",
                "preview one of the planned reels, e.g. "
                "`ren preview <project> --reel 1`")
        if moment.approval is not Approval.APPROVED:
            raise PreviewRefused(
                f"reel {number} ({moment.slug!r}) is "
                f"{moment.approval.value}, not approved",
                "only an approved reel is built, so there is nothing to "
                "watch",
                f"approve it first: `ren approve <project> {number}`")
        built = reel_deliver._built_names_for(
            project, number, moment.timeline_name)
        if len(built) > 1:
            raise PreviewRefused(
                f"reel {number} has {len(built)} built names "
                f"({', '.join(sorted(built))})",
                "guessing would preview the wrong timeline",
                "rebuild the reel so one timeline carries it")
        timeline = built[0] if built else moment.timeline_name
        targets.append({"number": number, "timeline": timeline,
                        "slug": _slug(timeline),
                        "built": bool(built) or _has_build_signature(
                            project, timeline)})
    if not targets:
        raise PreviewRefused(
            "no reel is approved", "there is nothing built to watch",
            "approve reels with `ren approve`, build them with "
            "`ren build`, then preview")
    return targets


def _has_build_signature(project: str, timeline: str) -> bool:
    """Whether a build recorded this reel, though the newest build may
    not have placed it.

    `timelines_built` names only what the NEWEST build placed; a build
    that left an unchanged reel alone (`reel_rebuild_need`) names none,
    and the reel is as built as it was. The build's signature for the
    reel outlives that build, so it is what answers.
    """
    from library.tools import plan_provenance
    return bool(plan_provenance.reel_content_hashes(
        _review_dir(project), timeline).get("derivation"))


# ── What the reel is: the model slice and the expected length ──────


def _load_model(project: str) -> dict | None:
    from library.tools import project_model_store as store
    try:
        return store.load_project_model(Path(project))[1]
    except (FileNotFoundError, ValueError):
        return None


def reel_model_slice(model: dict, reel_slug: str) -> dict | None:
    """The model narrowed to one reel, or None when it has no uses.

    A reel's clock is matched by the timeline it names, read as the
    final the build promoted: the build mints the model while the reel
    still sits in its staging container, so the clock can name `Reel
    01 - x (rebuild staging)` for the reel delivered as `Reel 01 - x`.

    The FilmCraft adapter projects ONE timeline clock and refuses a
    model whose clip uses span several (`plan_projection`), and the
    stored model carries every reel. So the reel's own rows are kept -
    its clip uses (on `clock:reel:<slug>`), the edits and effects on
    them, its frame map - and every other reel's are dropped. Evidence
    (assets, entities, observations, masks) is kept whole: it is shared
    by every reel and the adapter reads only what the uses reach.
    Renders, receipts and NLE bindings describe other outputs, not this
    timeline, and are dropped.
    """
    clocks = {row["id"] for row in model.get("clocks") or []
              if isinstance(row, dict) and _slug(_final_name(str(
                  (row.get("domain") or {}).get("timeline_id") or "")))
              == reel_slug}
    uses = [row for row in model.get("clip_uses") or []
            if isinstance(row, dict)
            and (row.get("timeline_interval") or {}).get("clock_id")
            in clocks]
    if not uses:
        return None
    kept = {row["id"] for row in uses}
    edits = []
    for edit in model.get("edits") or []:
        targets = [t for t in edit.get("target_ids") or [] if t in kept]
        if targets:
            edits.append({**edit, "target_ids": targets})
    effects = [row for row in model.get("effects") or []
               if isinstance(row, dict)
               and (row.get("target_id") in kept
                    or (row.get("interval") or {}).get("clock_id")
                    in clocks)]
    frame_maps = [row for row in model.get("frame_maps") or []
                  if isinstance(row, dict) and row.get("to_clock") in clocks]
    sliced = dict(model)
    sliced.update(clip_uses=uses, edits=edits, effects=effects,
                  frame_maps=frame_maps, renders=[], receipts=[],
                  nle_bindings=[])
    return sliced


def _final_name(timeline: str) -> str:
    """A staging container's name read as the final it was staged for."""
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX
    if timeline.endswith(STAGING_TIMELINE_SUFFIX):
        return timeline[:-len(STAGING_TIMELINE_SUFFIX)]
    return timeline


def _slice_digest(sliced: dict) -> tuple[str, list[dict]]:
    """A digest of what the engine will draw, and the media it reads.

    Covers the reel's own rows and the assets its uses import, with each
    asset's file size and mtime, so a re-rendered caption file or a
    replaced source changes the key while another reel's change does
    not.
    """
    asset_ids = {row.get("asset_id") for row in sliced["clip_uses"]}
    assets = sorted((row for row in sliced.get("assets") or []
                     if isinstance(row, dict) and row.get("id") in asset_ids),
                    key=lambda row: str(row.get("id")))
    media = []
    for asset in assets:
        path = str(asset.get("uri") or "")
        stat = None
        if path and os.path.isfile(path):
            info = os.stat(path)
            stat = [info.st_size, info.st_mtime_ns]
        media.append({"asset": asset.get("id"), "path": path, "stat": stat})
    payload = {key: sliced.get(key) for key in (
        "clip_uses", "edits", "effects", "frame_maps", "clocks")}
    payload["assets"] = assets
    payload["media"] = media
    return _digest(payload), media


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()


def _build_record(project: str) -> dict:
    from library.tools import capability_outputs
    from library.tools.project_layout import ProjectLayout

    path = ProjectLayout(project).pipeline_data_path
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return capability_outputs.read(state, "reel.build").get(
        "reel_build") or {}


def _review_dir(project: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout
    return str(ProjectLayout(project).write_dir(Area.REVIEW))


def _ren_snapshot_digest(project: str, timeline: str) -> str:
    """The newest Ren-verified snapshot of the timeline, or ""."""
    from library.tools import plan_provenance
    try:
        doc = plan_provenance.read_provenance(_review_dir(project)) or {}
    except (OSError, ValueError):
        return ""
    table = doc.get(plan_provenance.REN_TIMELINE_SNAPSHOTS_KEY) or {}
    return str((table.get(timeline) or {}).get("sha256") or "")


def expected_length(project: str, target: dict,
                    sliced: dict | None) -> dict:
    """How long the reel plays, from what the build recorded.

    `{seconds, fps, frames, source}`. The frame rate is the build's own
    `reel_fps`; the length is the end of the last thing placed on the
    reel's clock in the model the build maintained. Refused rather than
    guessed when the build recorded neither.
    """
    fps = _build_record(project).get("reel_fps")
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        fps = 0.0
    if fps <= 0:
        raise PreviewRefused(
            f"the build recorded no frame rate for {target['timeline']}",
            "a preview's length is checked against the reel's, and the "
            "reel's frame clock is the build's record",
            "rebuild the reel with `ren build`, then preview")
    if sliced is None:
        raise PreviewRefused(
            f"the Project Model has no rows for {target['timeline']}",
            "the reel's length is read off the model the build maintains",
            "rebuild the reel with `ren build`, then preview")
    from library.tools.reel_project_model import TICKS_PER_SECOND
    # The reel clock starts at the reel's first frame, so its end is
    # its length.
    seconds = max(int(use["timeline_interval"]["end"])
                  for use in sliced["clip_uses"]) / TICKS_PER_SECOND
    return {"seconds": seconds, "fps": fps,
            "frames": round(seconds * fps),
            "source": "project model clip uses, build reel_fps"}


def content_key(project: str, target: dict, engine: str,
                engine_revision: str, sliced: dict | None) -> dict:
    """The preview's cache key and the basis it was computed from.

    `key` is None when Ren recorded nothing about the reel: such a reel
    cannot be cached and is always rendered.
    """
    from library.tools import plan_provenance

    hashes = plan_provenance.reel_content_hashes(
        _review_dir(project), target["timeline"])
    signatures = {k: hashes.get(k) or "" for k in (
        "derivation", "carried", "caption_binding", "footage_binding")}
    basis: dict[str, Any] = {
        "spec": PREVIEW_SPEC,
        "engine": engine,
        "engine_revision": engine_revision,
        "timeline": target["timeline"],
        "build_signatures": signatures,
        "ren_snapshot": _ren_snapshot_digest(project, target["timeline"]),
    }
    if engine == "filmcraft" and sliced is not None:
        basis["model_slice"], basis["media"] = _slice_digest(sliced)
    recorded = (any(signatures.values()) or basis["ren_snapshot"]
                or basis.get("model_slice"))
    return {"key": _digest(basis) if recorded else None, "basis": basis}


# ── The engines ────────────────────────────────────────────────────


def _filmcraft_revision() -> str:
    from library.tools import project_model_cli
    return project_model_cli.FILMCRAFT_REVISION


def filmcraft_available() -> str:
    """"" when the headless engine can run here, else why it cannot."""
    from library.tools import project_model_cli
    try:
        project_model_cli.filmcraft_native()
        project_model_cli._filmcraft_cli()
    except RenRefusal as refused:
        return str(getattr(refused, "what", "") or refused)
    return ""


def render_filmcraft(project: str, target: dict, sliced: dict,
                     workdir: Path) -> dict:
    """Export the reel's model slice through the pinned FilmCraft CLI.

    `project_model_cli` plans and executes it - the adapter is the one
    in `ren project-model --engine filmcraft --execute`, not a copy.
    """
    from library.tools import project_model_cli as pmc

    native = pmc.filmcraft_native()
    plan = pmc.plan_filmcraft(sliced, native)
    export = workdir / "engine.mp4"
    receipt = pmc.execute_filmcraft(
        sliced, plan, project_root=Path(project), out=workdir / "plan.json",
        media_root=None, filmcraft_project=workdir / "preview.fcproj",
        export_path=export, export_format="h264", native=native)
    if not export.is_file():
        raise PreviewRefused(
            f"FilmCraft exported no file for {target['timeline']}",
            json.dumps(receipt.get("export") or {})[:300],
            "run `ren project-model <project> --engine filmcraft --out "
            "<plan.json> --execute` to see the engine's answer")
    return {"path": str(export),
            "losses": list(receipt.get("losses") or [])}


def render_resolve(project: str, target: dict, workdir: Path) -> dict:
    """Render the live reel timeline through Resolve's render queue."""
    from library.tools.execution import resolve_render

    report = resolve_render.render_timeline(
        timeline_name=target["timeline"], output_dir=str(workdir),
        output_name="engine", fmt="mp4", codec="H264")
    return {"path": report["output_path"], "losses": []}


# ── The proxy encode and its check ─────────────────────────────────


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def encode_proxy(source: str, out_path: Path) -> None:
    """The ONE encode that makes the preview file from the engine's."""
    probe = probe_file(source)
    if not probe["width"] or not probe["height"]:
        raise PreviewRefused(
            f"the engine's file has no picture: {source}",
            "a preview is a file to watch",
            "check the engine's export, then preview again")
    divisor = PREVIEW_SPEC["scale_divisor"]
    width = _even(probe["width"] / divisor)
    height = _even(probe["height"] / divisor)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_suffix(".partial.mp4")
    command = [
        "ffmpeg", "-v", "error", "-y", "-i", source,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale={width}:{height}",
        "-c:v", PREVIEW_SPEC["video_codec"],
        "-preset", PREVIEW_SPEC["x264_preset"],
        "-crf", str(PREVIEW_SPEC["crf"]),
        "-pix_fmt", PREVIEW_SPEC["pix_fmt"],
        "-c:a", PREVIEW_SPEC["audio_codec"],
        "-b:a", PREVIEW_SPEC["audio_bitrate"],
        "-movflags", "+faststart", str(partial)]
    done = subprocess.run(command, capture_output=True, encoding="utf-8",
                          errors="replace", check=False)
    if done.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise PreviewRefused(
            "the preview encode failed",
            (done.stderr or f"ffmpeg exit {done.returncode}").strip()[-400:],
            "check ffmpeg with `ren doctor`, then preview again")
    os.replace(partial, out_path)


def probe_file(path: str) -> dict:
    """`{width, height, frames, seconds, fps, audio}` measured off disk."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets",
         "-show_entries",
         ("stream=codec_type,width,height,nb_read_packets,avg_frame_rate"
          ":format=duration"), "-of", "json", path],
        capture_output=True, encoding="utf-8", errors="replace",
        check=False)
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        data = {}
    video = next((s for s in data.get("streams") or []
                  if s.get("codec_type") == "video"), {})
    audio = any(s.get("codec_type") == "audio"
                for s in data.get("streams") or [])

    def number(value, cast=float):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    rate = str(video.get("avg_frame_rate") or "0/1")
    num, _, den = rate.partition("/")
    fps = (number(num) or 0.0) / (number(den) or 1.0) if den else number(num)
    return {"width": number(video.get("width"), int),
            "height": number(video.get("height"), int),
            "frames": number(video.get("nb_read_packets"), int),
            "fps": fps or None,
            "seconds": number((data.get("format") or {}).get("duration")),
            "audio": audio}


def verify_preview(path: str, expected: dict) -> dict:
    """Playable, and as long as the reel within ONE frame.

    Playable means every frame decodes (`ffmpeg -f null`), not only
    that a container header parses. The length is compared in frames of
    the reel's own clock: `|measured - expected| <= 1`.
    """
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
        capture_output=True, encoding="utf-8", errors="replace",
        check=False)
    probe = probe_file(path)
    playable = (decode.returncode == 0 and not decode.stderr.strip()
                and bool(probe["frames"]))
    measured = (probe["frames"] / probe["fps"]
                if probe["frames"] and probe["fps"] else probe["seconds"])
    measured_frames = (round(measured * expected["fps"])
                       if measured is not None else None)
    within = (measured_frames is not None
              and abs(measured_frames - expected["frames"]) <= 1)
    return {"playable": playable,
            "decode_errors": decode.stderr.strip()[-400:],
            "measured": probe,
            "measured_seconds": measured,
            "measured_frames": measured_frames,
            "expected_frames": expected["frames"],
            "expected_seconds": expected["seconds"],
            "within_one_frame": within,
            "passed": bool(playable and within)}


# ── The record ─────────────────────────────────────────────────────


def previews_dir(project: str) -> Path:
    from library.tools.project_layout import Area, ProjectLayout
    return Path(ProjectLayout(project).write_dir(Area.PREVIEWS))


def _sidecar(path: Path) -> Path:
    return path.with_name(path.stem + ".preview.json")


def _cached(path: Path) -> dict | None:
    """The sidecar of a preview that passed and is still on disk."""
    if not path.is_file():
        return None
    try:
        sidecar = json.loads(_sidecar(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (sidecar.get("verification") or {}).get("passed"):
        return None
    return sidecar


def _record_event(project: str, event: dict) -> None:
    index = previews_dir(project) / INDEX_FILE
    try:
        doc = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    events = list(doc.get("events") or [])
    events.append(event)
    del events[:-MAX_INDEX_EVENTS]
    latest = dict(doc.get("latest") or {})
    if event.get("path"):
        latest[event["timeline"]] = {key: event[key] for key in (
            "path", "content_key", "engine", "at") if key in event}
    doc.update(spec=PREVIEW_SPEC, events=events, latest=latest)
    fd, tmp = tempfile.mkstemp(prefix=".previews.", dir=index.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, default=str)
    os.replace(tmp, index)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── One reel ───────────────────────────────────────────────────────


def headless_refusal(sliced: dict | None) -> str:
    """"" when FilmCraft can draw this reel, else the reason it cannot.

    Asks the adapter itself: its PLAN (projection plus per-edit
    translation, no engine process) is what `--execute` would run, so a
    reel it refuses here is one it would refuse there. A reel-build
    model of a multicam conversation is refused today - its dialogue
    rows are `speech` uses and its angles are stacked base picture, and
    the pinned adapter maps neither - so `auto` previews such a reel
    through Resolve and says so.
    """
    if sliced is None:
        return "the Project Model has no rows for this reel"
    why = filmcraft_available()
    if why:
        return f"the headless FilmCraft engine is not available here: {why}"
    from library.tools import project_model_cli as pmc
    try:
        pmc.plan_filmcraft(sliced, pmc.filmcraft_native())
    except RenRefusal as refused:
        return str(getattr(refused, "what", "") or refused)
    return ""


def choose_engine(requested: str, sliced: dict | None) -> tuple[str, str]:
    """The engine, and why FilmCraft was not it ("" when it was).

    `auto` is FilmCraft when the adapter can draw the reel, else Resolve.
    An explicit `filmcraft` the adapter cannot draw is refused with the
    adapter's own reason rather than quietly rendered elsewhere.
    """
    if requested not in ("auto", *ENGINES):
        raise PreviewRefused(
            f"unknown preview engine {requested!r}",
            f"the engines are {', '.join(ENGINES)} (or auto)",
            "pass --engine auto, filmcraft or resolve")
    if requested == "resolve":
        return "resolve", ""
    why = headless_refusal(sliced)
    if not why:
        return "filmcraft", ""
    if requested == "filmcraft":
        raise PreviewRefused(
            "the headless FilmCraft engine cannot draw this reel", why,
            "preview with `--engine resolve` (or `auto`, which falls "
            "back to it)")
    return "resolve", why


def preview_reel(project: str, target: dict, *, engine: str = "auto",
                 force: bool = False,
                 lock: Callable | None = None) -> dict:
    """Preview one built reel: a cache hit, or one render and encode."""
    if not target["built"]:
        return {"reel": target["number"], "timeline": target["timeline"],
                "outcome": "not_built",
                "detail": "approved but not built yet - run `ren build`"}
    model = _load_model(project)
    sliced = (reel_model_slice(model, target["slug"])
              if model is not None else None)
    chosen, headless_why = choose_engine(engine, sliced)
    expected = expected_length(project, target, sliced)
    revision = _filmcraft_revision() if chosen == "filmcraft" else "resolve"
    keyed = content_key(project, target, chosen, revision, sliced)
    key = keyed["key"]
    reel_dir = previews_dir(project) / target["slug"]
    out_path = reel_dir / f"{(key or 'uncached')[:16]}.mp4"
    base = {"reel": target["number"], "timeline": target["timeline"],
            "engine": chosen, "content_key": key, "path": str(out_path),
            "duration_seconds": expected["seconds"]}
    if headless_why:
        base["headless_refused"] = headless_why

    cached = None if (force or key is None) else _cached(out_path)
    if cached is not None:
        result = {**base, "outcome": "cache_hit", "at": _now(),
                  "verification": cached.get("verification")}
        _record_event(project, result)
        _record_on_assignment(project, result)
        return result

    from library.tools.heavy_work_lock import heavy_work_lock
    take = lock or heavy_work_lock
    with tempfile.TemporaryDirectory(
            prefix="preview-", dir=str(reel_dir.parent)) as work:
        workdir = Path(work)
        if chosen == "filmcraft":
            # Engine and encode under ONE grant: nothing else heavy
            # starts between them.
            with take("ren preview", PROFILE):
                engine_out = render_filmcraft(project, target, sliced,
                                              workdir)
                encode_proxy(engine_out["path"], out_path)
                verification = verify_preview(str(out_path), expected)
        else:
            # `render_timeline` takes its own `render.build:render`
            # grant; the encode takes `reel_preview` after it - in
            # turn, never nested (a nested grant it does not cover
            # raises).
            engine_out = render_resolve(project, target, workdir)
            with take("ren preview", PROFILE):
                encode_proxy(engine_out["path"], out_path)
                verification = verify_preview(str(out_path), expected)
    result = {**base, "outcome": "rendered", "at": _now(),
              "verification": verification,
              "losses": engine_out["losses"],
              "key_basis": keyed["basis"], "expected": expected}
    _sidecar(out_path).write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    _record_event(project, {k: v for k, v in result.items()
                            if k not in ("key_basis", "losses")})
    if not verification["passed"]:
        raise PreviewRefused(
            f"rendered {out_path} but it did not check out",
            f"playable={verification['playable']}, "
            f"frames {verification['measured_frames']} against the "
            f"reel's {verification['expected_frames']}",
            f"read {_sidecar(out_path)} for the measurements; the file "
            "stays but is not recorded as the reel's preview")
    _record_on_assignment(project, result)
    return result


def _record_on_assignment(project: str, result: dict) -> None:
    """The reel's assignment names its newest preview (best-effort)."""
    from library.tools import assignment_spine
    try:
        assignment_spine.on_preview(project, result["reel"], result)
    except Exception as exc:  # noqa: BLE001 - the file is the product
        print(f"  preview not recorded on the assignment: {exc!r}",
              file=sys.stderr)


def preview(project: str, reels: list[int] | None = None, *,
            engine: str = "auto", force: bool = False,
            lock: Callable | None = None) -> list[dict]:
    """Preview every approved, built reel (or the named ones), in turn."""
    project = os.path.abspath(project)
    return [preview_reel(project, target, engine=engine, force=force,
                         lock=lock)
            for target in reel_targets(project, reels)]


# ── The verb ───────────────────────────────────────────────────────


def _project_folder(ref: str) -> str:
    from library.tools.project_model_cli import resolve_project_root
    try:
        return str(resolve_project_root(ref))
    except RenRefusal as refused:
        raise PreviewRefused(
            f"no Ren project found for {ref!r}",
            str(getattr(refused, "why", "")),
            "pass a project directory holding project.yaml, or a slug "
            "from `ren projects`") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ren preview",
        description="Render a watchable low-cost preview of each built "
                    "reel and name the file. Cached by content: an "
                    "unchanged reel is not rendered again.")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--reel", type=int, action="append", default=None,
                        help="preview this reel number (repeatable); "
                             "default every approved reel")
    parser.add_argument("--engine", default="auto",
                        choices=("auto", *ENGINES),
                        help="auto (default): headless FilmCraft when it "
                             "can draw the reel, else Resolve")
    parser.add_argument("--force", action="store_true",
                        help="render even when the cached preview matches")
    parser.add_argument("--json", action="store_true",
                        help="print the results as JSON")
    return parser


def _line(result: dict) -> str:
    if result["outcome"] == "not_built":
        return f"{result['timeline']}: {result['detail']}"
    verb = ("unchanged, cached preview" if result["outcome"] == "cache_hit"
            else f"rendered with {result['engine']}")
    line = f"{result['timeline']}: {verb} - {result['path']}"
    if result.get("headless_refused"):
        line += (f"\n  (not headless: {result['headless_refused']})")
    return line


def main(argv: list[str] | None = None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    args = build_parser().parse_args(argv)
    try:
        project = _project_folder(args.project)
        results = preview(project, args.reel, engine=args.engine,
                          force=args.force)
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for result in results:
            print(_line(result))
    watch = [r["path"] for r in results if r.get("path")
             and r["outcome"] in ("rendered", "cache_hit")]
    if watch:
        from library.tools import completion_notice
        completion_notice.post(
            f"Preview ready: {os.path.basename(watch[0])}"
            + (f" (+{len(watch) - 1} more)" if len(watch) > 1 else ""))
        # The round's review page plays these files; rewrite it so it
        # never shows a preview this run replaced. A page that cannot be
        # written costs the preview nothing - the files are the product.
        from library.tools import round_page
        try:
            page = round_page.write(project)["page"]
        except Exception as exc:  # noqa: BLE001 - see above
            print(f"  review page not written: {exc!r}", file=sys.stderr)
        else:
            if not args.json:
                print(f"Review page: {page}")
        if not args.json:
            print("Watch: " + "  ".join(watch))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
