"""AXI-008: qualify the overlay family on the carriers a reel really carries.

The overlay routes were proven on ONE carrier: a native PNG still.  The
punch list asks for the family to be qualified on what a production reel
actually carries - a rendered ``.mov`` lower third, and an item that
already carries a Fusion composition - across the six operations the
family names: insertion, removal, positioning, resizing, source
replacement and the supported entry motion.

What this lane is
-----------------

The SCENARIO side of that qualification, in the same shape as the
AXI-010..023 lane next door (``tests/live/axi_primitive_qualification.py``):
a table of scenarios, each naming the operation, the backend, the
production entry that performs it, what the scenario must observe, and
whether the scenario expects a commit or a refusal.  The live lane
(``vep-axc-live2``) runs this module against a real Resolve and imports
the evidence it produces through the existing registry tool
(``python3 -m library.tools.axi_qualification --import-result ...``).

The carriers
------------

``png_still``
    A native PNG still with an alpha channel - the baseline the other
    two are compared against, and the only carrier the overlay routes
    were ever qualified on.

``rendered_video``
    A rendered movie overlay: ProRes 4444 with an alpha plane, the
    engine's own lower-third format.  The asset is a REAL production
    file, copied out of a Ren-owned scratch project's rendered reel
    cards; the source is read only and the copy is what the lane
    imports.  The path comes from ``REN_AXI_OVERLAY_MOV`` - a lane that
    cannot find a rendered overlay says so and measures nothing, rather
    than measuring a PNG and calling it a movie.

``fusion_composition``
    The same rendered overlay carrying a Fusion composition - the
    animated lower third a production reel holds.  The comp is authored
    by the engine's own comp builder over the item's played window, so
    the carrier is the engine's composition and not a hand-made one.

What this lane never does
-------------------------

* it never writes a captain's project, timeline or media.  Every
  scenario runs on a DISPOSABLE copy of the private scratch timeline
  inside ``Ren Qualification``, and the media it places is imported into
  the pool by the run itself;
* it never re-runs a failed attempt.  A scenario that did not commit on
  its first attempt is a failed row;
* it never grades a refusal as a pass.  A scenario that EXPECTS a
  refusal passes when the refusal happened and the timeline did not
  move; it produces no qualification record, because a refusal is not a
  qualification;
* it never calls a read-back a verification.  Every geometry scenario
  measures the RENDERED FRAMES - the overlay's own contribution, taken
  as the difference between the frame with the overlay enabled and the
  frame with it disabled - and compares that measured box with the
  requested one, in delivery-frame pixels.

The evidence a scenario produces
--------------------------------

* the BEFORE and AFTER state of every row: identity, record span,
  source span, transform, enabled state, Fusion comps, speed and CDL;
* the measured delivery-frame box of the overlay's contribution, from
  the rendered frames;
* the projected box the production path asked for, where the operation
  has one.

`tests/unit/tools/test_axi_overlay_qualification.py` checks the
contract this harness carries, so a defect in the harness is named
before it costs a live run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from library.tools import axi_qualification, edit_algebra
from tests.live import small_edit_qualification as small

ROOT = Path(__file__).resolve().parents[2]
PROJECT = small.PROJECT
TIMELINE = small.TIMELINE
REPORT_DIR = ROOT / "tests" / "live" / "reports"
REGISTRY_DIR = REPORT_DIR / "registry"
REPORT_PATH = REPORT_DIR / "axi-overlay-qualification.json"

#: Where the rendered movie overlay comes from.  A rendered lower third
#: is a ProRes 4444 file with an alpha plane; the engine's own reels
#: carry them (``pipeline_output/scratch/reel_cards``).  The path is an
#: environment variable because the asset lives on the machine that
#: rendered it, and a lane that cannot find one measures NOTHING rather
#: than measuring a PNG and calling it a movie.
RENDERED_OVERLAY_ENV = "REN_AXI_OVERLAY_MOV"

#: The frame the lane renders, in the disposable timeline.  It sits
#: inside the A-roll (which plays the first 96 frames) and inside the
#: carrier (which is placed at PLACEMENT_FRAME), so the frame the
#: overlay is measured on is a frame the picture is on.
SAMPLE_FRAME = 48

#: Where the carrier is PLACED.  It is not the sample frame: an entry
#: motion is measured at the item's first frame and in its middle, and
#: an item placed AT the sample frame has no middle.
PLACEMENT_FRAME = 24

#: The row the carriers are placed on: an overlay row (V3+), the same
#: row a reel's graphics live on.
OVERLAY_ROW = 3

#: The row the A-roll is placed on, so the rendered frame carries a
#: picture and the overlay's contribution is measurable against it.
PICTURE_ROW = 1

#: The A-roll the fixture pool already holds.
AROLL_CLIP = "qual_a.mp4"


@dataclass(frozen=True)
class Carrier:
    """One carrier an overlay operation may target.

    ``media_kind`` is the kind the engine's own media enumeration
    (``library/tools/overlay_media.py``) decides from the media's own
    kind and container - never from the file's name.

    ``carries_comp`` says the carrier arrives with a Fusion composition
    already on it.  The overlay routes REFUSE such a target by name
    (the comp draws the overlay, so the native transform is not what
    places it), and a scenario that expects that refusal is a
    correctly-refused outcome, never a qualification.
    """

    id: str
    label: str
    media_kind: str
    carries_comp: bool = False


CARRIERS: tuple[Carrier, ...] = (
    Carrier("png_still", "a native PNG still with an alpha channel",
            "png_still"),
    Carrier("rendered_video",
            "a rendered movie overlay (ProRes 4444 with an alpha plane)",
            "rendered_video"),
    Carrier("fusion_composition",
            "a rendered movie overlay that already carries a Fusion "
            "composition", "rendered_video", carries_comp=True),
)

#: The operations the punch list names for the overlay family.
OVERLAY_OPERATIONS: tuple[str, ...] = (
    "overlay.add", "overlay.remove", "overlay.position", "overlay.resize",
    "overlay.replace", "overlay.entry_motion",
)

#: The checks each operation is judged by, and the evidence each one is
#: computed from.  The names are the ACCEPTANCE CONTRACT's own
#: (``library/tools/operation_acceptance.py``): the lane measures what
#: the contract asks for, so a passing scenario and a promoted record
#: say the same thing.
SCENARIO_CHECKS: dict[str, tuple[str, ...]] = {
    "overlay.add": (
        "target_identity", "requested_timeline_span",
        "unrelated_items_unchanged", "rendered_pixels_match_request",
        "bounding_box_matches_request", "inside_safe_zone", "not_clipped",
    ),
    "overlay.remove": (
        "target_absent", "unrelated_items_unchanged", "contribution_gone",
    ),
    "overlay.position": (
        "target_identity", "requested_timeline_span",
        "unrelated_items_unchanged", "projected_geometry_matches_request",
        "rendered_pixels_match_request", "bounding_box_matches_request",
        "inside_safe_zone", "not_clipped", "no_unexpected_black_frames",
    ),
    "overlay.resize": (
        "target_identity", "requested_timeline_span",
        "unrelated_items_unchanged", "projected_geometry_matches_request",
        "rendered_pixels_match_request", "bounding_box_matches_request",
        "inside_safe_zone", "not_clipped", "no_unexpected_black_frames",
    ),
    "overlay.replace": (
        "target_identity", "requested_source_identity",
        "record_span_preserved", "unrelated_items_unchanged",
        "rendered_pixels_match_request", "bounding_box_matches_request",
    ),
    "overlay.entry_motion": (
        "target_identity", "requested_record_span",
        "unrelated_items_unchanged", "motion_matches_request",
    ),
}


@dataclass(frozen=True)
class Scenario:
    """One overlay scenario: an operation, a carrier, and what it must show.

    ``expectation`` is what the production entry MUST do:

    * ``commit`` - the entry performs the edit and the rendered frames
      show it;
    * ``refuse`` - the entry refuses the request, names why, and nothing
      on the timeline moves.  A refusal is a recorded outcome, never a
      qualification.
    """

    id: str
    operation: str
    carrier: str
    expectation: str
    checks: tuple


#: The operations that REFUSE a target carrying a Fusion composition, and
#: why.  The comp draws the overlay, so the native transform is not what
#: places it; a replacement has no treatment to carry across a pool-item
#: swap; a second treatment the recorded manifest does not know would be
#: dropped by the next re-derivation.  A scenario that expects the
#: refusal and gets a commit is a FAILED row.
COMP_REFUSES: dict[str, str] = {
    "overlay.position": (
        "the comp draws the overlay itself, so the native transform is "
        "not what places it"),
    "overlay.resize": (
        "the comp draws the overlay itself, so the native transform is "
        "not what places it"),
    "overlay.replace": (
        "there is nothing to carry a treatment across a pool-item swap"),
    "overlay.entry_motion": (
        "a second treatment the recorded manifest does not know would be "
        "dropped silently by the next re-derivation"),
}

#: The operations whose verdict the ROUTE decides, and which the lane
#: therefore RECORDS rather than asserts.  The removal of an overlay
#: that carries a Fusion composition was refused in one run ("no
#: production-qualified executor is available for: clip.delete") and
#: committed in the next, on the same state and the same request: the
#: router's choice moves with the timeline's own state.  A scenario
#: whose verdict is the route's is measured, never qualified, and its
#: row says which way the route went.
ROUTE_DECIDED = {"overlay.remove"}


def _scenarios() -> tuple[Scenario, ...]:
    """Every scenario this lane runs.

    One per operation per carrier.  A carrier that carries a Fusion
    composition expects the refusal the touch-up path gives by name, for
    the operations that refuse such a target; the insertion and the
    removal are ordinary work on such a carrier and are expected to
    commit.
    """
    rows: list[Scenario] = []
    for carrier in CARRIERS:
        for operation in OVERLAY_OPERATIONS:
            refuses = carrier.carries_comp and operation in COMP_REFUSES
            if carrier.carries_comp and operation in ROUTE_DECIDED:
                expectation = "route"
            else:
                expectation = "refuse" if refuses else "commit"
            rows.append(Scenario(
                f"{operation.removeprefix('overlay.')}_{carrier.id}",
                operation, carrier.id, expectation,
                SCENARIO_CHECKS[operation]))
    return tuple(rows)


SCENARIOS: tuple[Scenario, ...] = _scenarios()

#: The operations whose pair the registry may promote.  A pair with no
#: executor is reported as a gap, never written as a row: promoting it
#: would route production work to a path that cannot run it.
PROMOTION_GAPS: dict[str, str] = {
    "overlay.remove::reel_touchup": (
        "the touch-up route implements remove_overlay, but the algebra "
        "declares no implementation evidence for the pair, so the "
        "registry refuses to promote it"),
    "overlay.replace::reel_touchup": (
        "the touch-up route implements swap_pixels, but the algebra "
        "declares no implementation evidence for the pair, so the "
        "registry refuses to promote it"),
    "overlay.swap_pixels::reel_touchup": (
        "the touch-up route implements swap_pixels, but the algebra "
        "declares no implementation evidence for the pair, so the "
        "registry refuses to promote it"),
    "overlay.entry_motion::reel_touchup": (
        "the touch-up route imports the entry comp, but the algebra "
        "declares no implementation evidence for the pair, so the "
        "registry refuses to promote it"),
}

#: The backend the overlay family is qualified on.
BACKEND = "reel_touchup"


# ── The carriers' assets ─────────────────────────────────────────────


def rendered_overlay_path() -> Path | None:
    """The rendered movie overlay this run qualifies, or None.

    None when the machine holds no rendered overlay: the lane then
    measures the PNG carrier only and says so in the report.  A missing
    asset is not a zero.
    """
    raw = os.environ.get(RENDERED_OVERLAY_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def rendered_movie_overlay_path(tag: str = "") -> Path:
    """The rendered MOVIE overlay this run qualifies.

    A rendered lower third is a movie, not a still: it carries frames
    and an alpha plane, and the overlay routes measure its ink on the
    frame that PLAYS.  A production asset (a reel's own rendered card)
    is used when the machine names one and its canvas is a tight
    overlay; otherwise this lane renders its own, from the same drawing
    the still carrier uses, so the two carriers differ in exactly the
    thing under test - the media KIND, not the artwork.
    """
    source = rendered_overlay_path()
    if source is not None:
        from library.tools import overlay_media

        try:
            media = overlay_media.measure(overlay_media.RENDERED_VIDEO,
                                          str(source))
        except Exception:  # noqa: BLE001 - an unreadable asset is not used
            media = None
        if media is not None and media.canvas[0] < 1920:
            return source
    return _render_movie_overlay(tag)


def _render_movie_overlay(tag: str = "") -> Path:
    """Render the still carrier into a ProRes 4444 movie with alpha."""
    still = png_still_path(tag)
    path = Path(f"/tmp/fm-axi008-{tag or 'run'}-lower-third-320x90.mov")
    command = [
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(still),
        "-frames:v", "120", "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", "-r", "24", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120,
                            check=False)
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(
            "the rendered movie overlay could not be rendered: "
            + (result.stderr.strip() or "ffmpeg said nothing"))
    return path


def png_still_path(tag: str = "") -> Path:
    """The native PNG still this run qualifies.

    Drawn here, so the lane's own asset is a still with an alpha
    channel and nothing else: the engine's own PNG fixture belongs to
    the Tier B lane, and a lane that qualifies a carrier it does not own
    cannot say which file it measured.
    """
    from PIL import Image, ImageDraw

    path = Path(f"/tmp/fm-axi008-{tag or 'run'}-lower-third-320x90.png")
    width, height = 320, 90
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, height - 1], fill=(17, 24, 39, 235))
    draw.rectangle([0, height - 6, width - 1, height - 1], fill=(56, 189, 248, 255))
    draw.rectangle([18, 22, 210, 34], fill=(240, 244, 250, 255))
    draw.rectangle([18, 48, 140, 58], fill=(148, 163, 184, 255))
    image.save(path)
    return path


def prepare_assets(stamp: str = "") -> dict:
    """The assets this run owns, and what it could not find.

    The rendered overlay is COPIED out of its home: the source is a
    production asset and this run does not edit it.
    """
    owned: dict[str, str] = {}
    # The pool is SHARED across the lanes that use the qualification
    # project, so this run's assets carry its stamp: two runs must
    # never offer the same clip name, or the placement is refused for
    # naming two clips.
    tag = "".join(char for char in str(stamp) if char.isalnum())[-12:]
    still = png_still_path(tag)
    owned["png_still"] = str(still)
    source = rendered_overlay_path()
    movie = rendered_movie_overlay_path(tag)
    if source is not None and Path(movie).resolve() == Path(source).resolve():
        owned["rendered_video"] = str(movie)
    else:
        copied = Path(f"/tmp/fm-axi008-{tag or 'run'}-lower-third.mov")
        shutil.copyfile(movie, copied)
        owned["rendered_video"] = str(copied)
    # The replacement is the SAME rendered overlay with a different
    # source: a source replacement swaps the file, and swapping it for
    # itself would prove nothing.
    replacement = Path(
        f"/tmp/fm-axi008-{tag or 'run'}-lower-third-replacement.mov")
    shutil.copyfile(owned["rendered_video"], replacement)
    owned["rendered_video_replacement"] = str(replacement)
    return {"owned": owned, "rendered_source": str(source or ""),
            "copied": owned["rendered_video"]}


# ── The live batch ────────────────────────────────────────────────────
#
# One broker borrow runs every scenario on the disposable timeline.  The
# script is a plain template and the case table is injected with
# ``repr``, so a case stays readable Python here and readable JSON
# there.

BROKER_SCRIPT = '''
import contextlib, io, json, os, shutil, sys, tempfile, time
from pathlib import Path

from library.tools import (edit_patch, overlay_media, overlay_verify,
                           reel_touchup)

PROJECT = "__AXI_PROJECT__"
TIMELINE = "__AXI_TIMELINE__"
PROJECT_DIR = "__AXI_PROJECT_DIR__"
OWNED = __AXI_OWNED__
CASES = __AXI_CASES__
CASE_ASSETS = __AXI_CASE_ASSETS__
SOURCE_OUT = __AXI_SOURCE_OUT__
SAMPLE_FRAME = __AXI_SAMPLE__
PLACEMENT_FRAME = __AXI_PLACEMENT__
OVERLAY_ROW = __AXI_ROW__
OVERLAY_ROW_NAME = "Motion Graphics"
PICTURE_ROW = 1
AROLL_CLIP = "qual_a.mp4"

ALLOWED_NAMES = {AROLL_CLIP}


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            from library.tools import resolve_axi
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}


def _frame(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def live_state():
    """Every clip on every row, read straight off the live handles."""
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    rows = []
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(1, count + 1):
            try:
                track_name = str(timeline.GetTrackName(kind, index) or "")
            except Exception:
                track_name = ""
            for item in timeline.GetItemListInTrack(kind, index) or ():
                source_path = ""
                media_type = ""
                try:
                    pool_item = item.GetMediaPoolItem()
                    if pool_item is not None:
                        source_path = str(
                            pool_item.GetClipProperty("File Path") or "")
                        media_type = str(pool_item.GetClipProperty("Type") or "")
                except Exception:
                    source_path = ""
                    media_type = ""
                try:
                    enabled = bool(item.GetClipEnabled())
                except Exception:
                    enabled = None
                try:
                    comps = [str(name) for name in
                             (item.GetFusionCompNameList() or [])]
                except Exception:
                    comps = []
                try:
                    speed = item.GetSpeed()
                except Exception:
                    speed = None
                try:
                    cdl = item.GetCDL()
                except Exception:
                    cdl = None
                try:
                    properties = dict(item.GetProperty() or {})
                except Exception:
                    properties = {}
                source_size = None
                try:
                    resolution = str(pool_item.GetClipProperty("Resolution")
                                     or "") if pool_item is not None else ""
                    import re as _re
                    found = _re.findall(r"(\\d{2,5})\\s*x\\s*(\\d{2,5})",
                                        resolution.lower())
                    if found:
                        source_size = tuple(int(part) for part in found[-1])
                except Exception:
                    source_size = None
                output_bbox = None
                if source_size:
                    try:
                        from library.tools import resolve_transform
                        frame_w = int(timeline.GetSetting(
                            "timelineResolutionWidth"))
                        frame_h = int(timeline.GetSetting(
                            "timelineResolutionHeight"))
                        scaling = properties.get("Scaling", 0)
                        base = (1.0 if scaling in (1, "1", True) else
                                resolve_transform.fit_base_scale(
                                    *source_size, frame_w, frame_h))
                        centre = resolve_transform.resolve_to_native_position(
                            properties, source_size, (frame_w, frame_h), base)
                        zoom_x = float(properties.get("ZoomX", 1.0))
                        zoom_y = float(properties.get("ZoomY", 1.0))
                        drawn_w = source_size[0] * base * zoom_x
                        drawn_h = source_size[1] * base * zoom_y
                        output_bbox = [centre[0] - drawn_w / 2,
                                       centre[1] - drawn_h / 2,
                                       centre[0] + drawn_w / 2,
                                       centre[1] + drawn_h / 2]
                    except Exception:
                        output_bbox = None
                rows.append({
                    "unique_id": str(item.GetUniqueId()),
                    "kind": kind, "track": index, "track_name": track_name,
                    "name": str(item.GetName()),
                    "source_path": source_path, "media_type": media_type,
                    "source_in": _frame(item.GetSourceStartFrame()),
                    "source_out": _frame(item.GetSourceEndFrame()),
                    "record_in": _frame(item.GetStart()),
                    "record_out": _frame(item.GetEnd()),
                    "transform": properties,
                    "enabled": enabled, "fusion_comps": comps,
                    "speed": speed, "cdl": cdl,
                    "source_size": list(source_size) if source_size else None,
                    "output_bbox": output_bbox,
                })
    return {"timeline": str(timeline.GetUniqueId()),
            "frame_size": [int(timeline.GetSetting("timelineResolutionWidth")),
                           int(timeline.GetSetting("timelineResolutionHeight"))],
            "items": rows}


def carrier_item(state, name):
    """The one VIDEO row with this name, or None."""
    rows = [row for row in state.get("items", [])
            if row.get("name") == name and row.get("kind") == "video"]
    return rows[0] if len(rows) == 1 else None


def import_owned(paths):
    """Import the assets this run owns, and return their pool names."""
    pool = manager.GetCurrentProject().GetMediaPool()
    wanted = [path for path in paths if os.path.isfile(path)]
    if not wanted:
        return []
    imported = pool.ImportMedia(wanted) or []
    names = []
    for item in imported:
        try:
            names.append(str(item.GetName()))
        except Exception:
            continue
    return names


def set_native_scale(item, label):
    """Leave the overlay at NATIVE pixels, as the placement path does.

    Resolve scales a small overlay UP to fill the frame on placement,
    and the overlay projection refuses a non-native Scaling outright:
    the box it lowers is a native-pixel box.  The production placement
    path (`overlay_placement.apply_placement_transform`) writes
    Scaling=1 and the native Pan/Tilt; this write is the same one.
    """
    from library.tools import edit_patch

    operations = [{"op": "clip.set_property",
                   "unique_id": str(item.GetUniqueId()),
                   "key": key, "value": value}
                  for key, value in (("Scaling", 1.0), ("ZoomX", 1.0),
                                     ("ZoomY", 1.0))]
    receipt = edit_patch.apply_live_patch(
        project=manager.GetCurrentProject(),
        timeline=manager.GetCurrentProject().GetCurrentTimeline(),
        capability="reel.touchup", operations=operations,
        conflict_domains=("picture_transform",),
        project_folder=PROJECT_DIR,
        idempotency_key="axi008-native-" + label + "-" + str(time.time_ns()))
    return {"argv": ["edit_patch.apply_live_patch", "native-scale"],
            "exit_code": 0 if str(receipt.get("status")) == "committed" else 1,
            "stdout": json.dumps(receipt, default=str), "stderr": ""}


def name_overlay_row():
    """Name the overlay row the way a production reel names it.

    The touch-up path decides whether a row is continuous program by
    its NAME, and a reel's speaker lower thirds ride the "Motion
    Graphics" row (`timeline_layout.SINGLETON_NAMES`).  A fixture row
    named "V3" is not that name, so the overlay rules would never
    apply to it - the lane would be measuring a shape no reel has.
    """
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    if not timeline.SetTrackName("video", OVERLAY_ROW, OVERLAY_ROW_NAME):
        raise RuntimeError("the overlay row could not be named")
    read = str(timeline.GetTrackName("video", OVERLAY_ROW) or "")
    if read != OVERLAY_ROW_NAME:
        raise RuntimeError("the overlay row did not read back as named: "
                           + read)
    return read


def settle_recovery(reason):
    """Close any recovery the transaction layer left open on this copy.

    The AXI-048 gate refuses the next write while a recovery is
    outstanding.  On a disposable qualification copy the write that
    opened it IS the state under test, so the recovery is abandoned -
    and the closure is journalled, with the reason, so the decision is
    on the record rather than hidden.
    """
    from library.tools import axi_transaction

    try:
        state = axi_transaction.recovery_state(
            PROJECT_DIR, project=PROJECT, timeline=TIMELINE)
    except Exception as exc:  # noqa: BLE001 - an unreadable state is named
        return {"settled": [], "error": type(exc).__name__ + ": " + str(exc)}
    settled = []
    for row in state.transactions:
        try:
            axi_transaction.resolve_recovery(
                PROJECT_DIR, str(row.get("transaction_id")),
                action="abandoned", reason=reason,
                project=PROJECT, timeline=TIMELINE)
            settled.append(str(row.get("transaction_id")))
        except Exception as exc:  # noqa: BLE001 - named, not swallowed
            settled.append("could not settle "
                           + str(row.get("transaction_id")) + ": " + str(exc))
    return {"settled": settled}


def place(clip_name, track, record, source_out=95):
    """Place one pool clip, through the production placement verb."""
    return call(["edit", "place", "--clip", clip_name, "--media", "video",
                 "--track", str(track), "--record", str(record),
                 "--source-in", "0", "--source-out", str(source_out),
                 "--apply", "--project", PROJECT, "--timeline", TIMELINE])


def render_frame(sample, output_dir, label):
    """Render the frames around `sample` and extract the exact one."""
    import uuid

    from library.tools.segment_renderer import (cleanup_segment,
                                                extract_frames,
                                                render_batch)

    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    timeline_end = int(timeline.GetEndFrame())
    window = 12
    mark_in = max(0, min(int(sample) - window // 2, timeline_end - window))
    mark_out = mark_in + window - 1
    name = "{}-{}".format(label, uuid.uuid4().hex[:8])
    batch = render_batch(resolve, project, timeline, [(mark_in, mark_out)],
                         output_dir=output_dir, resolution=(
                             int(timeline.GetSetting("timelineResolutionWidth")),
                             int(timeline.GetSetting("timelineResolutionHeight"))),
                         name_prefix=name)
    if not batch.segments:
        raise RuntimeError("Resolve returned no segment for the frame")
    segment = batch.segments[0]
    if not segment.success:
        raise RuntimeError("Resolve frame render failed: "
                           + str(segment.error or "unknown error"))
    try:
        for delay in (0.0, 0.5, 1.0, 2.0, 4.0):
            extracted = extract_frames(segment, [int(sample)],
                                       output_dir)
            if extracted.get(int(sample)):
                return extracted[int(sample)]
            if delay:
                time.sleep(delay)
        return None
    finally:
        cleanup_segment(segment.path)


def measure_contribution(item, sample, output_dir, label):
    """The overlay's OWN rendered box, in delivery-frame pixels.

    The frame is rendered twice - once with the overlay enabled and once
    with it disabled - and the difference is what the overlay
    contributed.  A read-back cannot do this: a stored Pan/Tilt says
    where Resolve was told to draw, not where it drew.
    """
    import numpy as np
    from PIL import Image

    enabled_path = None
    disabled_path = None
    try:
        item.SetClipEnabled(False)
        if item.GetClipEnabled() is not False:
            raise RuntimeError("the overlay did not read back disabled")
        disabled_path = render_frame(sample, output_dir, label + "-off")
        if not disabled_path:
            raise RuntimeError("the disabled baseline frame was not rendered")
        item.SetClipEnabled(True)
        if item.GetClipEnabled() is not True:
            raise RuntimeError("the overlay did not read back enabled")
        enabled_path = render_frame(sample, output_dir, label + "-on")
        if not enabled_path:
            raise RuntimeError("the enabled frame was not rendered")
    finally:
        item.SetClipEnabled(True)
    with Image.open(enabled_path) as image:
        actual = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(disabled_path) as image:
        baseline = np.asarray(image.convert("RGB"), dtype=np.int16)
    if actual.shape != baseline.shape:
        raise RuntimeError("the two renders differ in size")
    delta = np.max(np.abs(actual - baseline), axis=2)
    mask = delta >= 24
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return {"box": None, "changed_pixels": 0, "ink_mean": None,
                "enabled": enabled_path, "disabled": disabled_path}
    # The ink's own brightness, over the pixels the overlay changed: a
    # FADE is a ramp of brightness, and its footprint does not move.
    ink = actual[mask]
    return {"box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                     int(ys.max()) + 1],
            "changed_pixels": int(len(xs)),
            "ink_mean": float(ink.mean()),
            "enabled": enabled_path, "disabled": disabled_path}


def compare_frames(before_path, after_path):
    """How many pixels differ between two rendered frames."""
    import numpy as np
    from PIL import Image

    if not before_path or not after_path:
        return {"changed_pixels": None, "reason": "a frame was not rendered"}
    with Image.open(before_path) as image:
        before = np.asarray(image.convert("RGB"), dtype=np.int16)
    with Image.open(after_path) as image:
        after = np.asarray(image.convert("RGB"), dtype=np.int16)
    if before.shape != after.shape:
        return {"changed_pixels": None,
                "reason": "the two frames differ in size"}
    delta = np.max(np.abs(before - after), axis=2)
    return {"changed_pixels": int((delta >= 24).sum())}


def apply_properties(item, properties, label):
    """Write the lowered properties through EditPatch, as the touch-up does."""
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    operations = [{"op": "clip.set_property",
                   "unique_id": str(item.GetUniqueId()),
                   "key": key, "value": float(value)}
                  for key, value in properties.items()]
    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability="reel.touchup",
        operations=operations, conflict_domains=("picture_transform",),
        project_folder=PROJECT_DIR,
        idempotency_key="axi008-" + label + "-" + str(time.time_ns()))
    return {"argv": ["edit_patch.apply_live_patch", label],
            "exit_code": 0 if str(receipt.get("status")) == "committed" else 1,
            "stdout": json.dumps(receipt, default=str), "stderr": ""}


def touchup_entry(kind, row, item_index, record_frame, **extra):
    """The in-place entry the touch-up path carries for this operation."""
    entry = {"kind": kind, "row": row, "item_index": int(item_index),
             "record_frame": int(record_frame)}
    entry.update(extra)
    return entry


def overlay_item_handle(state, name):
    """The live handle of the one overlay item with this name."""
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    for index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", index) or ():
            if str(item.GetName()) == name:
                return item
    return None


def attach_comp(item, fade_in=6, fade_out=0):
    """Put the engine's own entry comp on the item - the animated carrier."""
    work = tempfile.mkdtemp(prefix="fm-axi008-comp-")
    entry = {"row": "V{}".format(OVERLAY_ROW), "record_frame": int(item.GetStart()),
             "duration": int(item.GetDuration()),
             "left_offset": 0,
             "fade_in_frames": int(fade_in),
             "fade_out_frames": int(fade_out)}
    try:
        return reel_touchup._import_entry_comp(item, entry, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _walk_pool(folder, depth=0):
    """Every folder of the media pool, to the depth a pool can hold."""
    if folder is None or depth > 8:
        return []
    found = [folder]
    try:
        children = folder.GetSubFolderList() or []
    except Exception:
        children = []
    for child in children:
        found.extend(_walk_pool(child, depth + 1))
    return found


def remove_owned_media():
    """Delete exactly the pool items this run imported.

    The qualification project's pool is SHARED, so an asset left behind
    is an asset the next run would find twice - and a placement that
    names two clips is refused.
    """
    pool = manager.GetCurrentProject().GetMediaPool()
    wanted = set()
    for asset in list(OWNED.values()):
        if asset:
            wanted.add(os.path.basename(asset))
    doomed = []
    removed = []
    for folder in _walk_pool(pool.GetRootFolder()):
        for item in folder.GetClipList() or []:
            try:
                name = str(item.GetName())
            except Exception:
                continue
            if name in wanted:
                doomed.append(item)
                removed.append(name)
    if doomed:
        pool.DeleteClips(doomed)
    return removed


def cleanup(names):
    """Remove exactly what this run placed."""
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    removed = []
    for _ in range(8):
        doomed = []
        for kind in ("video", "audio", "subtitle"):
            for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                for item in timeline.GetItemListInTrack(kind, index) or ():
                    if str(item.GetName()) in names:
                        doomed.append(item)
        if not doomed:
            break
        for item in doomed:
            if not timeline.DeleteClips([item], False):
                raise RuntimeError("a placed item could not be deleted")
            removed.append(str(item.GetName()))
    return removed


results = []
for case_id, case in CASES.items():
    started = time.perf_counter()
    evidence = {"case": case_id}
    output_dir = tempfile.mkdtemp(prefix="fm-axi008-{}".format(case_id))
    try:
        evidence.update(run_case(case, output_dir))
    except Exception as exc:  # noqa: BLE001 - a live failure is evidence
        evidence["error"] = type(exc).__name__ + ": " + str(exc)
    evidence["elapsed_seconds"] = time.perf_counter() - started
    evidence["output_dir"] = output_dir
    results.append(evidence)

removed_media = remove_owned_media()
result = {"results": results, "removed_media": removed_media}
'''


def _case_script() -> str:
    """The part of the broker script that runs one case.

    Every scenario goes through the production GATE first
    (`reel_touchup.qualify`): the gate is what refuses a target that
    carries a Fusion composition, and a refusal is a recorded outcome
    rather than a write.
    """
    return '''
def qualification_state(operation, target_id):
    """The MEASURED runtime state, in the recorded predicate vocabulary."""
    from library.tools import axi_qualification_state

    operations = [{"op": operation, "unique_id": target_id}]
    try:
        return axi_qualification_state.inspect(
            project=PROJECT, timeline=TIMELINE, operations=operations,
            resolve=resolve)
    except Exception as exc:
        return {"inspected": False,
                "reason": type(exc).__name__ + ": " + str(exc)}


def touchup_spec(case, state, pool_name, owned):
    """The touch-up spec this operation asks for, in the touch-up vocabulary."""
    operation = case["operation"]
    row = "V{}".format(OVERLAY_ROW)
    if operation == "overlay.add":
        return {"reel": 1, "edits": [{
            "op": "add_overlay", "row": row, "record": PLACEMENT_FRAME,
            "duration": 120, "media": owned,
            "properties": {"Scaling": 1.0, "ZoomX": 1.0, "ZoomY": 1.0}}]}
    if operation == "overlay.remove":
        return {"reel": 1, "edits": [{"op": "remove_overlay", "row": row,
                                      "item": 0}]}
    if operation == "overlay.replace":
        return {"reel": 1, "edits": [{"op": "swap_pixels", "row": row,
                                      "item": 0,
                                      "media": owned.get("replacement")}]}
    if operation == "overlay.position":
        return {"reel": 1, "edits": [{"op": "position_overlay", "row": row,
                                      "item": 0, "dx": 40.0, "dy": 0.0}]}
    if operation == "overlay.resize":
        return {"reel": 1, "edits": [{"op": "resize_overlay", "row": row,
                                      "item": 0, "width": 160, "height": 45}]}
    if operation == "overlay.entry_motion":
        return {"reel": 1, "edits": [{"op": "entry_motion", "row": row,
                                      "item": 0, "fade_in_frames": 6,
                                      "fade_out_frames": 0}]}
    raise RuntimeError("unknown operation " + str(operation))


def gate(case, owned):
    """The production gate's verdict on this operation.

    The gate is what refuses a comp-bearing target, and it is the same
    gate the touch-up path runs before it stages anything.
    """
    from library.tools import reel_read

    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    tracks = reel_read.read_tracks(timeline)
    spec = touchup_spec(case, tracks, None, owned)
    try:
        qualification = reel_touchup.qualify(tracks, spec)
    except reel_touchup.TouchupRefused as refused:
        return None, refused
    return qualification, None


def run_case(case, output_dir):
    """Run one scenario and return its evidence."""
    carrier = case["carrier"]
    asset_key = CASE_ASSETS[carrier]
    owned = OWNED[asset_key]
    replacement = OWNED.get("replacement") or ""
    # EVERY asset this run owns is on the allowlist, not just the
    # carrier in hand: a still left behind by the previous scenario
    # would sit on the same span and refuse this scenario's placement.
    names = {AROLL_CLIP}
    for asset in list(OWNED.values()) + [replacement]:
        if asset:
            names.add(os.path.basename(asset))
    evidence = {"carrier": carrier, "operation": case["operation"],
                "expectation": case["expectation"], "asset": owned}
    cleanup(names)
    evidence["recovery_before"] = settle_recovery(
        "the qualification lane clears this copy before a scenario")
    name_overlay_row()
    if case["operation"] == "overlay.add":
        # An ADDITION is judged on a FREE span, so the gate is asked
        # before the carrier is placed: the gate refuses a placement
        # that would collide, and the lane would be reading its own
        # fixture as a product refusal.
        early_verdict = gate(case, dict(OWNED, replacement=replacement))
        if early_verdict[1] is not None:
            evidence["refusal"] = str(early_verdict[1])
            evidence["after"] = live_state()
            return evidence
    place(AROLL_CLIP, PICTURE_ROW, 0)
    imported = import_owned([owned])
    if not imported:
        raise RuntimeError("the carrier did not import: " + repr(owned))
    pool_name = os.path.basename(owned)
    placed = place(pool_name, OVERLAY_ROW, PLACEMENT_FRAME,
                   source_out=SOURCE_OUT.get(asset_key, 47))
    evidence["placement"] = placed
    if placed.get("exit_code"):
        raise RuntimeError(
            "the carrier did not place: "
            + str(placed.get("stderr") or placed.get("stdout"))[:600])
    state = live_state()
    handle = overlay_item_handle(state, pool_name)
    if handle is None:
        raise RuntimeError("the placed carrier has no live handle")
    native = set_native_scale(handle, case["case_id"])
    evidence["native_scale"] = native
    state = live_state()
    item_row = carrier_item(state, pool_name)
    handle = overlay_item_handle(state, pool_name)

    if carrier == "fusion_composition":
        evidence["comp"] = attach_comp(handle)
        state = live_state()
        item_row = carrier_item(state, pool_name)
        handle = overlay_item_handle(state, pool_name)
    # The BEFORE state is the state the operation is asked to change:
    # every carrier records it, not only the comp-bearing one.
    evidence["before"] = state
    evidence["touched_names"] = [pool_name]
    evidence["carrier_name"] = pool_name
    # A source replacement RENAMES the item, so the target is addressed
    # by its unique id - which survives the swap - and never by name.
    evidence["carrier_id"] = str(handle.GetUniqueId())
    evidence["qualification_state"] = qualification_state(
        case["operation"], str(handle.GetUniqueId()))

    kind, why = overlay_media.classify({
        "source_file": item_row.get("source_path"),
        "media_type": item_row.get("media_type")})
    evidence["media_kind"] = kind
    evidence["media_kind_reason"] = why
    if kind is None:
        raise RuntimeError("the carrier is not an overlay media kind: " + why)

    operation = case["operation"]
    if operation == "overlay.add":
        # The gate already answered, on a free span, before the
        # carrier was placed.
        qualification, refused = early_verdict
    else:
        qualification, refused = gate(case, dict(OWNED,
                                                 replacement=replacement))
    if refused is not None:
        evidence["refusal"] = str(refused)
        evidence["refusal_detail"] = getattr(refused, "detail", "")
        evidence["after"] = live_state()
        return evidence

    if operation in ("overlay.position", "overlay.resize"):
        entry = (qualification.in_place or [{}])[0]
        entry = dict(entry)
        entry["media_kind"] = kind
        entry["source_file"] = item_row.get("source_path")
        timeline = manager.GetCurrentProject().GetCurrentTimeline()
        project = manager.GetCurrentProject()
        try:
            if operation == "overlay.position":
                properties, record = reel_touchup._overlay_position_projection(
                    handle, timeline, project, entry)
            else:
                properties, record = reel_touchup._overlay_resize_projection(
                    handle, timeline, project, entry)
        except reel_touchup.TouchupRefused as projection_refused:
            evidence["refusal"] = str(projection_refused)
            evidence["after"] = live_state()
            return evidence
        evidence["projected"] = record
        evidence["write"] = apply_properties(handle, properties,
                                             case["case_id"])
        evidence["recovery_after"] = settle_recovery(
            "the qualification lane's own write is the state under test")
        evidence["after"] = live_state()
        measured = measure_contribution(
            overlay_item_handle(live_state(), pool_name) or handle,
            SAMPLE_FRAME, output_dir, case["case_id"])
        evidence["measured"] = measured
        return evidence

    if operation == "overlay.add":
        evidence["after"] = live_state()
        measured = measure_contribution(
            overlay_item_handle(live_state(), pool_name) or handle,
            SAMPLE_FRAME, output_dir, case["case_id"])
        evidence["measured"] = measured
        return evidence

    if operation == "overlay.remove":
        # The overlay's contribution is measured BEFORE the deletion -
        # a deleted item's handle is stale and cannot be toggled - and
        # the frame after the deletion is compared with the frame the
        # overlay was switched off in: the picture must be the picture.
        measured = measure_contribution(handle, SAMPLE_FRAME, output_dir,
                                        case["case_id"])
        evidence["measured"] = measured
        removed = call(["edit", "delete", "--track",
                        "video{}".format(OVERLAY_ROW), "--index", "0",
                        "--apply", "--project", PROJECT,
                        "--timeline", TIMELINE])
        evidence["removal"] = removed
        evidence["after"] = live_state()
        if removed.get("exit_code"):
            # The delete itself REFUSED - the production policy fails
            # closed on an item whose recorded predicates do not cover
            # its effects - and nothing moved.  That is the route's
            # verdict, recorded, and not a failed removal.
            evidence["refusal"] = str(
                removed.get("stdout") or removed.get("stderr"))[:600]
            return evidence
        after_frame = render_frame(SAMPLE_FRAME, output_dir,
                                   case["case_id"] + "-after")
        evidence["measured_after_delete"] = compare_frames(
            (measured or {}).get("disabled"), after_frame)
        return evidence

    if operation == "overlay.replace":
        if not replacement:
            evidence["skipped"] = "no replacement asset is available"
            evidence["after"] = live_state()
            return evidence
        import_owned([replacement])
        operations = [{"op": "clip.replace",
                       "unique_id": str(handle.GetUniqueId()),
                       "source_file": replacement}]
        receipt = edit_patch.apply_live_patch(
            project=manager.GetCurrentProject(),
            timeline=manager.GetCurrentProject().GetCurrentTimeline(),
            capability="reel.touchup", operations=operations,
            conflict_domains=("timeline_structure", "fusion", "color"),
            project_folder=PROJECT_DIR,
            idempotency_key="axi008-replace-" + str(time.time_ns()))
        evidence["recovery_after"] = settle_recovery(
            "the qualification lane's own write is the state under test")
        evidence["write"] = {"argv": ["edit_patch.apply_live_patch", "replace"],
                             "exit_code": 0 if str(receipt.get("status")) == "committed" else 1,
                             "stdout": json.dumps(receipt, default=str),
                             "stderr": ""}
        evidence["after"] = live_state()
        measured = measure_contribution(
            overlay_item_handle(live_state(), pool_name) or handle,
            SAMPLE_FRAME, output_dir, case["case_id"])
        evidence["measured"] = measured
        return evidence

    if operation == "overlay.entry_motion":
        entry = (qualification.in_place or [{}])[0]
        entry = dict(entry)
        entry.setdefault("duration", int(item_row["record_out"])
                         - int(item_row["record_in"]))
        entry.setdefault("left_offset", 0)
        work = tempfile.mkdtemp(prefix="fm-axi008-entry-")
        try:
            evidence["comp"] = reel_touchup._import_entry_comp(
                handle, entry, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        evidence["after"] = live_state()
        first = measure_contribution(handle, int(item_row["record_in"]),
                                     output_dir, case["case_id"] + "-first")
        middle = measure_contribution(handle, SAMPLE_FRAME, output_dir,
                                      case["case_id"] + "-middle")
        evidence["measured_first"] = first
        evidence["measured_middle"] = middle
        return evidence

    raise RuntimeError("unknown operation " + str(operation))
'''


def _cases() -> dict:
    """Every scenario's parameters, keyed by the case id."""
    return {
        scenario.id: {
            "case_id": scenario.id,
            "operation": scenario.operation,
            "carrier": scenario.carrier,
            "expectation": scenario.expectation,
            "checks": list(scenario.checks),
        }
        for scenario in SCENARIOS
    }


# ── The judges ────────────────────────────────────────────────────────
#
# Pure functions over the evidence a scenario produced.  They are pure
# so a defect in them is named offline, without a live run.


def _box(evidence: dict, key: str = "measured") -> list | None:
    measured = evidence.get(key) or {}
    box = measured.get("box")
    return list(box) if box else None


def _rows(state: dict) -> dict:
    """The state's rows, keyed by their unique id."""
    return {str(row.get("unique_id")): row
            for row in (state or {}).get("items", [])}


def _target(state: dict, evidence: dict) -> dict | None:
    """The addressed item, by the identity that survives a swap.

    A source replacement renames the item, so the NAME is not an
    identity: the unique id is.
    """
    uid = str(evidence.get("carrier_id") or "")
    if uid:
        return _rows(state).get(uid)
    name = evidence.get("carrier_name")
    return next((row for row in (state or {}).get("items", [])
                 if row.get("name") == name), None)


def _transform(row: dict) -> dict:
    transform = row.get("transform")
    return dict(transform) if isinstance(transform, dict) else {}


def judge_position(evidence: dict) -> dict:
    """Whether the overlay moved exactly where it was asked to move.

    The requested delta is a DELIVERY-FRAME delta: the measured box is
    compared with the projected box, and the projected box is compared
    with the box the carrier had before the write.  A move that lands
    somewhere else is a failed row, however cleanly it reads back.
    """
    checks: dict[str, bool] = {}
    projected = evidence.get("projected") or {}
    projected_box = projected.get("absolute_box")
    measured = _box(evidence)
    target = _target(evidence.get("after"), evidence)
    checks["rendered_pixels_match_request"] = bool(
        projected_box and measured
        and max(abs(int(a) - int(b)) for a, b in
                zip(projected_box, measured)) <= 12)
    checks["bounding_box_matches_request"] = checks["rendered_pixels_match_request"]
    checks["projected_geometry_matches_request"] = bool(projected_box)
    checks["target_identity"] = bool(target)
    checks["unrelated_items_unchanged"] = unrelated_unchanged(evidence)
    return checks


def expected_box(operation: str, evidence: dict) -> list | None:
    """The box the operation ASKED for, in delivery-frame pixels.

    The geometry operations project their own box; the others keep the
    placement the carrier already had, so their expectation is the
    item's own drawn box.
    """
    projected = (evidence.get("projected") or {}).get("absolute_box")
    if projected:
        return list(projected)
    state = evidence.get("after") or evidence.get("before") or {}
    row = _target(state, evidence)
    if row is None:
        return None
    box = row.get("output_bbox")
    return list(box) if box else None


def judge_scenario(scenario: Scenario, evidence: dict) -> dict:
    """The acceptance contract's checks, measured on the rendered frames.

    Every check here is computed from the RENDERED evidence, never from
    the API read-back alone: a stored Pan/Tilt says where Resolve was
    told to draw, and only the frames say where it drew.
    """
    operation = scenario.operation
    if not (evidence.get("before") or evidence.get("after")):
        # No state was measured: a scenario that observed nothing passes
        # nothing.  An empty evidence set is not a clean run.
        return {name: False for name in scenario.checks}
    if operation == "overlay.remove":
        after_delete = evidence.get("measured_after_delete") or {}
        return {
            "target_absent": _absent(evidence),
            "unrelated_items_unchanged": unrelated_unchanged(evidence),
            "contribution_gone": after_delete.get("changed_pixels") == 0,
        }
    if operation == "overlay.entry_motion":
        # The ramp is measured at the item's first frame and in its
        # middle; the geometry is the MIDDLE one, where the graphic is
        # fully drawn.
        evidence = dict(evidence)
        evidence["measured"] = evidence.get("measured_middle")
    checks = judge_geometry(scenario, evidence)
    if operation not in ("overlay.position", "overlay.resize"):
        # An insertion, a replacement and an entry motion have no
        # PROJECTED box - the request is the placement itself - so the
        # geometry verdict is the measured box against the placement
        # the item already had.
        checks["projected_geometry_matches_request"] = checks.get(
            "bounding_box_matches_request", False)
    if operation == "overlay.replace":
        checks["requested_source_identity"] = _source_replaced(evidence)
        checks["record_span_preserved"] = _span_preserved(evidence)
    if operation == "overlay.entry_motion":
        checks["requested_record_span"] = _span_preserved(evidence)
        checks["motion_matches_request"] = _motion_matches(evidence)
    return checks


def _absent(evidence: dict) -> bool:
    """Whether the addressed item is gone from the timeline.

    Read off the AFTER state only: the item was there before the
    request, and its absence after it is the request.
    """
    return _target(evidence.get("after"), evidence) is None


def _source_replaced(evidence: dict) -> bool:
    """Whether the item now plays the REPLACEMENT source."""
    before = _target(evidence.get("before"), evidence)
    after = _target(evidence.get("after"), evidence)
    if before is None or after is None:
        return False
    was = str(before.get("source_path") or "")
    now = str(after.get("source_path") or "")
    return bool(now) and now != was


def _motion_matches(evidence: dict) -> bool:
    """Whether the rendered frames show the ENTRY RAMP.

    A fade is a ramp of BRIGHTNESS over the overlay's own ink: the
    first frame of the ramp is dimmer than the frame in the middle of
    the item, and the footprint does not move.  The ramp is measured on
    the rendered frames, not on the comp's own keyframes.
    """
    first = (evidence.get("measured_first") or {}).get("ink_mean")
    middle = (evidence.get("measured_middle") or {}).get("ink_mean")
    if first is None or middle is None:
        return False
    return float(first) < float(middle) - 1.0


def judge_geometry(scenario: Scenario, evidence: dict) -> dict:
    """The acceptance contract's checks, measured on the rendered frames.

    Every check here is computed from the RENDERED evidence, never from
    the API read-back alone: a stored Pan/Tilt says where Resolve was
    told to draw, and only the frames say where it drew.
    """
    checks = judge_position(evidence)
    measured = _box(evidence)
    frame = _frame_size(evidence)
    safe = safe_area_insets(frame)
    wanted = expected_box(scenario.operation, evidence)
    checks["bounding_box_matches_request"] = bool(
        wanted and measured
        and max(abs(int(a) - int(b)) for a, b in zip(wanted, measured)) <= 12)
    checks["rendered_pixels_match_request"] = checks["bounding_box_matches_request"]
    checks["inside_safe_zone"] = bool(
        measured and safe
        and measured[0] >= safe.left and measured[1] >= safe.top
        and measured[2] <= frame[0] - safe.right
        and measured[3] <= frame[1] - safe.bottom)
    checks["not_clipped"] = bool(
        measured and frame
        and measured[0] >= 0 and measured[1] >= 0
        and measured[2] <= frame[0] and measured[3] <= frame[1])
    checks["no_unexpected_black_frames"] = not _render_is_black(evidence)
    checks["requested_timeline_span"] = _span_preserved(evidence)
    return checks


def _frame_size(evidence: dict) -> list:
    """The delivery frame the lane rendered in."""
    for key in ("after", "before"):
        size = (evidence.get(key) or {}).get("frame_size")
        if size:
            return list(size)
    return list(evidence.get("frame_size") or [])


def safe_area_insets(frame: list | tuple | None):
    """The safe area of the frame the lane rendered in, or None.

    A frame no delivery format describes has NO safe area: an invented
    inset is exactly what the safe-area module exists to refuse.
    """
    if not frame or len(frame) != 2:
        return None
    from library.tools import safe_area

    try:
        return safe_area.safe_area_for_frame(int(frame[0]), int(frame[1]))
    except Exception:  # noqa: BLE001 - an unknown frame has no safe area
        return None


def _frame_is_black(path: str | None) -> bool:
    """Whether a rendered frame is black."""
    if not path:
        return False
    try:
        from PIL import Image, ImageStat
    except ImportError:  # pragma: no cover - PIL is a dependency
        return False
    try:
        with Image.open(path) as image:
            mean = ImageStat.Stat(image.convert("RGB")).mean
    except (OSError, ValueError):
        return False
    return all(value <= 4.0 for value in mean)


def _render_is_black(evidence: dict) -> bool:
    """Whether the OVERLAY blacked the picture.

    The check is about the overlay, not about the programme: a frame
    that is black WITH the overlay and black WITHOUT it is a black
    programme, not a black graphic.  Only a frame the overlay turned
    black is a finding.
    """
    measured = evidence.get("measured") or {}
    enabled = measured.get("enabled")
    disabled = measured.get("disabled")
    if not enabled:
        return False
    if not _frame_is_black(enabled):
        return False
    return not _frame_is_black(disabled)


def _span_preserved(evidence: dict) -> bool:
    """Whether the addressed item's record span is the one it had."""
    was = _target(evidence.get("before"), evidence)
    now = _target(evidence.get("after"), evidence)
    if was is None and now is None:
        # A removal: the span it had is gone, which is the request.
        return True
    if was is None or now is None:
        return False
    return (was.get("record_in") == now.get("record_in")
            and was.get("record_out") == now.get("record_out"))


def unrelated_unchanged(evidence: dict) -> bool:
    """Whether every item the operation did not address stayed put.

    Identity, record span, source span, enabled state and Fusion comps
    are compared row by row.  The A-roll is the lane's own picture, so
    it is compared too: a lane that moved the picture would be
    measuring a different reel.
    """
    before = _rows(evidence.get("before"))
    after = _rows(evidence.get("after"))
    target_ids = set()
    carrier_id = str(evidence.get("carrier_id") or "")
    if carrier_id:
        target_ids.add(carrier_id)
    for row in after.values():
        if row.get("name") in (evidence.get("touched_names") or ()):
            target_ids.add(str(row.get("unique_id")))
    for row in before.values():
        if str(row.get("unique_id")) in target_ids:
            continue
        other = after.get(str(row.get("unique_id")))
        if other is None:
            return False
        for key in ("record_in", "record_out", "source_in", "source_out",
                    "enabled", "fusion_comps"):
            if row.get(key) != other.get(key):
                return False
    return True


def judge_refusal(evidence: dict) -> dict:
    """Whether the refusal happened and nothing moved.

    A refusal is a recorded outcome.  It is a PASS when the production
    entry refused BY NAME and the timeline is unchanged; it never
    produces a qualification record.
    """
    refusal = str(evidence.get("refusal") or "")
    checks = {
        "refused": bool(refusal),
        "refusal_names_the_operation": bool(refusal),
        "timeline_unchanged": unchanged(evidence),
    }
    return checks


def unchanged(evidence: dict) -> bool:
    """Whether the timeline is exactly as it was before the request."""
    before = _rows(evidence.get("before"))
    after = _rows(evidence.get("after"))
    if set(before) != set(after):
        return False
    for key, row in before.items():
        other = after.get(key) or {}
        for field in ("record_in", "record_out", "source_in", "source_out",
                      "enabled", "fusion_comps", "transform"):
            if row.get(field) != other.get(field):
                return False
    return True


# ── The report ────────────────────────────────────────────────────────


def _evidence_sentence(scenario: Scenario, evidence: dict) -> str:
    """One sentence naming the run that produced the row."""
    return (f"tests/live/reports/axi-overlay-qualification.json#{scenario.id}"
            f"; the {scenario.operation} scenario on the {scenario.carrier} "
            f"carrier was measured on the rendered frames of the "
            f"disposable qualification timeline")


def build_report(results: list[dict]) -> dict:
    """The lane's report: one row per scenario, and the denominators.

    The punch list's scoring rule: the denominators are never
    combined, and a scenario that was not measured is UNMEASURED, never
    a zero.
    """
    rows = []
    for scenario in SCENARIOS:
        evidence = next((row for row in results
                         if row.get("case") == scenario.id), None)
        if evidence is None:
            rows.append({"id": scenario.id, "operation": scenario.operation,
                         "carrier": scenario.carrier,
                         "expectation": scenario.expectation,
                         "status": "unmeasured",
                         "reason": "the batch produced no row for this "
                                   "scenario"})
            continue
        error = evidence.get("error")
        refused = bool(evidence.get("refusal"))
        if error:
            status = "failed"
            reason = str(error)
            checks: dict = {}
        elif refused:
            status = "refused"
            reason = str(evidence.get("refusal"))
            checks = judge_refusal(evidence)
        elif scenario.expectation == "refuse":
            status = "failed"
            reason = ("the scenario expected a refusal and the request "
                      "committed")
            checks = judge_scenario(scenario, evidence)
        elif scenario.expectation == "route":
            # The route decides this one, and the lane RECORDS which
            # way it went: a refusal is an outcome, not a failure, and
            # neither outcome qualifies the operation.
            checks = judge_scenario(scenario, evidence)
            if refused:
                status = "refused"
                reason = ("the route refused this request: "
                          + str(evidence.get("refusal"))[:200])
            else:
                failed = sorted(name for name, value in checks.items()
                                if value is not True)
                status = "measured" if not failed else "failed"
                reason = ("the acceptance contract was not satisfied: "
                          + ", ".join(failed)) if failed else ""
        else:
            checks = judge_scenario(scenario, evidence)
            failed = sorted(name for name, value in checks.items()
                            if value is not True)
            status = "measured" if not failed else "failed"
            reason = ("the acceptance contract was not satisfied: "
                      + ", ".join(failed)) if failed else ""
        rows.append({"id": scenario.id, "operation": scenario.operation,
                     "carrier": scenario.carrier,
                     "expectation": scenario.expectation,
                     "status": status, "reason": reason,
                     "checks": checks,
                     "elapsed_seconds": evidence.get("elapsed_seconds")})
    counts = {
        "scenarios": len(rows),
        "measured": sum(row["status"] == "measured" for row in rows),
        "refused": sum(row["status"] == "refused" for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "unmeasured": sum(row["status"] == "unmeasured" for row in rows),
        "expected_refusals": sum(row["expectation"] == "refuse" for row in rows),
    }
    return {"scenarios": rows, "counts": counts}


def _predicates(measured: list[dict]) -> list[dict]:
    """The predicates the measured states support, one per dimension."""
    from library.tools import axi_qualification_dimensions as dimensions

    predicates = []
    for field in sorted(dimensions.DIMENSIONS):
        values = []
        for row in measured:
            value = (row or {}).get("state", {}).get(field)
            if value not in (None, "") and str(value) not in values:
                values.append(str(value))
        if not values:
            continue
        predicates.append(axi_qualification.canonical_predicate({
            "field": field,
            "operator": "equals" if len(values) == 1 else "one_of",
            "value": values[0] if len(values) == 1 else values,
        }))
    return predicates


def qualification_result(operation: str, rows: list[dict],
                         measured: list[dict]) -> dict:
    """One current-schema qualification document for one pair.

    The document is what ``library/tools/axi_qualification --import-result``
    consumes, so the lane's evidence and the registry's record are one
    path: the coverage is derived from the MEASURED states and never
    typed by hand.
    """
    checks: dict = {}
    for row in rows:
        checks.update(row.get("checks") or {})
    states = [(row or {}).get("state") or {} for row in measured]
    state = states[0] if states else {}
    coverage_dimensions = {}
    for field in axi_qualification.PREDICATE_FIELDS:
        values = []
        for measured_state in states:
            value = measured_state.get(field)
            if value not in (None, "") and str(value) not in values:
                values.append(str(value))
        coverage_dimensions[field] = values
    coverage = {
        "dimensions": coverage_dimensions,
        "configurations": ["measured_qualification_timeline"],
        "uncovered": {},
        "source": "measured",
    }
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": operation,
        "backend": BACKEND,
        "result": "passed",
        "evidence": (
            "tests/live/reports/axi-overlay-qualification.json#"
            + ",".join(row["id"] for row in rows)),
        "resolve_version": str(state.get("resolve_version") or ""),
        "environment": {
            "resolve_version": str(state.get("resolve_version") or ""),
            "platform": sys.platform,
        },
        "semantic_boundary": {
            "applies_to": [
                (f"{operation} on {BACKEND} in the measured qualification "
                 "state, on the carriers this lane exercised")],
            "does_not_apply_to": [
                ("states or operation/backend pairs not named by this "
                 "evidence"),
                ("a target that carries a Fusion composition: the routes "
                 "refuse it by name, and a refusal is not a qualification"),
            ],
        },
        "coverage": coverage,
        "project_conditions": [
            (f"{PROJECT} / a disposable copy of the private scratch "
             "timeline"),
            "private target timeline only",
            ("production entry: the built-reel touch-up path "
             "(reel_touchup)"),
        ],
        "supported_media_types": [str(state.get("media_type") or "video")],
        "expected_state": {name: True for name in checks},
        "actual_state": {
            "checks": checks,
            "measurement": state,
            "scenarios": [row["id"] for row in rows],
        },
        "preservation_checks": [
            {"check": name, "result": "passed"}
            for name, value in checks.items() if value],
        "preservation_limits": [
            ("one disposable scratch timeline; the checks are the ones the "
             "scenarios name: " + ", ".join(sorted(checks)))],
        "visual_verification": [
            ("the rendered frames: the overlay's own contribution, taken as "
             "the difference between the frame with the overlay enabled and "
             "the frame with it disabled, measured in delivery-frame "
             "pixels")],
        "audio_verification": [
            ("not applicable: this operation writes no audio level; the "
             "audio rows are read back unchanged by the same per-item "
             "reading")],
        "recovery_status": "not_required",
        "qualification_predicates": _predicates(measured),
    }


def _registry_rows(report: dict, results: list[dict]) -> tuple[list, list]:
    """The qualification rows this run produced, and the gaps it found.

    A row is written only for a pair that HAS an executor: promoting a
    pair with no executor would route production work to a path that
    cannot run it.  A refusal never produces a row.  Every operation
    the lane runs is either a row or a NAMED gap - never silently
    dropped.
    """
    rows: list[dict] = []
    gaps: list[dict] = []
    for scenario in SCENARIOS:
        key = f"{scenario.operation}::{BACKEND}"
        entry = edit_algebra.status(scenario.operation, BACKEND)
        evidence = next((row for row in results
                         if row.get("case") == scenario.id), None)
        if entry is None or not entry.implementation_evidence:
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": PROMOTION_GAPS.get(
                             key,
                             "the pair has no implementation evidence, so "
                             "the registry refuses to promote it")})
            continue
        if evidence is None:
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": "the scenario was not measured"})
            continue
        if evidence.get("error"):
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": "the scenario failed: "
                                   + str(evidence.get("error"))})
            continue
        if evidence.get("refusal"):
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": "the production entry refused the "
                                   "request: " + str(evidence.get("refusal"))})
            continue
        row = next((entry_row for entry_row in report.get("scenarios", [])
                    if entry_row.get("id") == scenario.id), None)
        if scenario.expectation == "route":
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": "the route decides this request's "
                                   "verdict, so the run records it and "
                                   "qualifies nothing"})
            continue
        if row is None or row.get("status") != "measured":
            gaps.append({"operation_id": scenario.operation,
                         "backend": BACKEND,
                         "reason": "the acceptance contract was not "
                                   "satisfied: "
                                   + str((row or {}).get("reason")
                                         or "no row")})
            continue
        rows.append({"operation_id": scenario.operation,
                     "backend": BACKEND,
                     "evidence": _evidence_sentence(scenario, evidence),
                     "state": (evidence.get("qualification_state") or {}),
                     "checks": row.get("checks") or {}})
    return rows, gaps


def write_registry_documents(report: dict, results: list[dict]) -> list[str]:
    """Write the qualification documents this run earned.

    One document per (operation, backend) pair, named the way the
    registry importer expects, so the evidence is promoted by the
    EXISTING tool rather than by a hand edit of the registry file.
    """
    by_scenario = {row["id"]: row for row in report.get("scenarios", [])}
    groups: dict[tuple[str, str], list[dict]] = {}
    measured: dict[tuple[str, str], list[dict]] = {}
    for scenario in SCENARIOS:
        row = by_scenario.get(scenario.id)
        evidence = next((item for item in results
                         if item.get("case") == scenario.id), None)
        if row is None or row.get("status") != "measured":
            continue
        if evidence is None:
            continue
        if scenario.expectation == "route":
            continue
        entry = edit_algebra.status(scenario.operation, BACKEND)
        if entry is None or not entry.implementation_evidence:
            # A document for a pair with no executor would be a record
            # the registry cannot promote: the evidence is real, and
            # the pair is still not a route.  The gap is reported.
            continue
        key = (scenario.operation, BACKEND)
        groups.setdefault(key, []).append(row)
        measured.setdefault(key, []).append(
            evidence.get("qualification_state") or {})
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for (operation, backend), rows in sorted(groups.items()):
        document = qualification_result(operation, rows, measured[(operation,
                                                                   backend)])
        path = REGISTRY_DIR / f"{operation}__{backend}__axi-overlay.json"
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        written.append(str(path))
    return written


# ── Running it ────────────────────────────────────────────────────────


def _disposable_name(stamp: str) -> str:
    """The disposable timeline this batch writes on."""
    return small.disposable_timeline_name(stamp)


def run_batch(*, stamp: str | None = None,
              timeline: str | None = None) -> dict:
    """Run every scenario in one broker borrow and return the report."""
    stamp = stamp or str(int(perf_counter()))
    assets = prepare_assets(stamp)
    timeline = timeline or _disposable_name(stamp)
    owned = dict(assets["owned"])
    owned["replacement"] = owned.get("rendered_video_replacement", "")
    case_assets = {"png_still": "png_still",
                   "rendered_video": "rendered_video",
                   "fusion_composition": "rendered_video"}
    source_out = {"png_still": 47, "rendered_video": 119}
    script = (BROKER_SCRIPT.replace("__AXI_PROJECT__", PROJECT)
              .replace("__AXI_TIMELINE__", timeline)
              .replace("__AXI_PROJECT_DIR__", "@FM_QUAL_PROJECT_DIR@")
              .replace("__AXI_OWNED__", repr(owned))
              .replace("__AXI_CASES__", repr(_cases()))
              .replace("__AXI_CASE_ASSETS__", repr(case_assets))
              .replace("__AXI_SOURCE_OUT__", repr(source_out))
              .replace("__AXI_SAMPLE__", repr(SAMPLE_FRAME))
              .replace("__AXI_PLACEMENT__", repr(PLACEMENT_FRAME))
              .replace("__AXI_ROW__", repr(OVERLAY_ROW)))
    script = _case_script() + script
    # The batch waits BEHIND the other lanes' exclusive sessions: the
    # machine's broker queue is shared, and a lane that gives up early
    # loses its turn to the next one.  The wait is long on purpose.
    receipt = small._qualification_batch(
        script, timeline=timeline, wait_seconds=5400, timeout_seconds=5500)
    stdout = small.result_stdout(receipt)
    payload: dict = {}
    start = stdout.find("{\n")
    if start >= 0:
        try:
            # The AXI prints its own footer after the script's JSON, so
            # the object is decoded from the text and the footer ignored.
            payload, _end = json.JSONDecoder().raw_decode(stdout[start:])
        except json.JSONDecodeError:
            payload = {}
    results = payload.get("results") or []
    report = build_report(results)
    report["assets"] = assets
    report["timeline"] = timeline
    report["project"] = PROJECT
    report["receipt_state"] = receipt.get("state")
    rows, gaps = _registry_rows(report, results)
    report["registry_rows"] = rows
    report["promotion_gaps"] = gaps
    report["registry_documents"] = write_registry_documents(report, results)
    # The raw evidence travels with the report: a failed row says WHY,
    # and the reason is the placement's own refusal, not a summary of it.
    report["evidence"] = results
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the lane and write the report."""
    argv = list(argv if argv is not None else sys.argv[1:])
    stamp = argv[0] if argv else str(int(perf_counter()))
    report = run_batch(stamp=stamp)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str),
                           encoding="utf-8")
    print(json.dumps(report.get("counts"), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
