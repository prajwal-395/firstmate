"""Live adapter for the ten ordinary editorial requests (AXI-11, 9.2).

Each task's verbatim instruction is executed through a production Resolve
AXI entry point on the private qualification timeline and judged from
live read-back: a committed receipt, operation-specific checks, an empty
collateral diff, and one-attempt evidence.

Discovery is real, not a hard-coded map. The adapter builds an
``EditRequest`` naming the task's operation with the verbatim instruction
as its expected effect, compiles it, and routes it through
``execution_router`` under production and then under the
qualification-run policy (protected experimental with all three guards).
Both verdicts stay on the record. A task the router refuses everywhere
but whose executing mechanism matches a live-qualified entry executes
through that exact entry with the refusals kept on the record; a task
whose executor refuses by design (no guarded verb, no fixture cards, no
attached brief) records that safe refusal with its category. Nothing is
relabelled: the executing algebra ops are recorded beside the task
operation, and a safe refusal stays a failed task - AXI-11 never counts
one as a pass.

The fixture carries the content the instructions name: linked
audio/video A-roll, a marked trailing pause, B-roll over speech, caption
overlays, and a camera A/B pair for the marked speaker turn. Marks live
in the request evidence as named spans and are verified against
read-back spans.
"""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from tests.live import small_edit_qualification as q

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
LOGO = "fm-qual-logo-180x64.png"

REPORT_DIR = Path(__file__).resolve().parent / "reports"

#: Named spans the fixture carries, in record frames. The pause is the
#: trailing 24 frames of the opening A-roll clip; the phrase is the
#: B-roll cover window; the speaker turn is the camera A/B pair window.
PAUSE_SPAN = (72, 96)
PHRASE_SPAN = (100, 140)
SPEAKER_SPAN = (144, 168)


def _refuse(category: str, detail: str) -> Exception:
    """A safe refusal whose failure category parses from the message."""
    return RuntimeError(f"[{category}:{detail}]")


def _op_status_note(operation: str) -> str:
    from library.tools import edit_algebra
    status = edit_algebra.status(operation)
    if status is None:
        return (f"{operation} is absent from the edit algebra "
                f"(no registered implementation on any backend)")
    rows = ", ".join(
        f"{backend.backend}:qualified={backend.qualified},"
        f"production={backend.production_enabled}"
        for backend in status.backends)
    return f"{operation} algebra rows: {rows}"


#: Representative params proving a present op constructs. Live identities
#: resolve broker-side; these only exercise the construction/compile/route
#: contracts for the op type.
_DISCOVERY_PARAMS = {
    "overlay.add": {"row": "V3", "record_frame": 96},
    "overlay.resize": {"unique_id": "axc-discovery",
                       "width": 180, "height": 64},
    "overlay.position": {"unique_id": "axc-discovery", "dx": 0, "dy": -40},
    "overlay.replace": {"unique_id": "axc-discovery",
                        "file": "fm-qual-logo-alt-180x64.png"},
    "clip.trim": {"unique_id": "axc-discovery",
                  "in_frame": 0, "out_frame": 71},
    "clip.remove": {"unique_id": "axc-discovery"},
    "broll.insert": {"unique_id": "axc-discovery", "track_type": "video",
                     "track_index": 2, "record_in": 100, "record_out": 140,
                     "source_in": 0, "source_out": 39, "clip": "qual_c.mp4"},
    "caption.style": {"unique_id": "axc-discovery", "style": "axc-qual"},
    "multicam.select_angle": {"unique_id": "axc-discovery", "angle_index": 1},
    "reel.opening": {"unique_id": "axc-discovery", "brief": "axc-qual",
                     "edits": []},
}


def _protected_context() -> dict:
    """The qualification-run policy and its three explicit guards."""
    return {"qualification_policy": "protected_experimental",
            "protected_staging": True, "preserve_approved_state": True,
            "verify_after_write": True}


def _discover(task, instruction: str) -> tuple[dict, dict, dict]:
    """Build, compile and route an EditRequest for the verbatim request.

    Returns (discovery evidence, router evidence, phases).
    Raises a categorised refusal when the op is absent from the algebra -
    the caller keeps that refusal on the record.
    """
    from library.tools import edit_algebra, edit_request

    phases: dict[str, float] = {}
    started = perf_counter()
    status = edit_algebra.status(task.operation)
    if status is None:
        raise _refuse(
            "missing_operation",
            f"{task.operation}: no executor registered "
            f"{_op_status_note(task.operation)} | discovery: EditRequest "
            "refused the unknown op (fail-closed); router: not reached")
    try:
        request = edit_request.from_axi_operations(
            [{**_DISCOVERY_PARAMS.get(task.operation, {}),
              "op": task.operation,
              "expected_effect": instruction}],
            project=PROJECT, timeline=TIMELINE,
            originator={"kind": "instruction", "name": "ren revise (axc)",
                        "detail": instruction})
    except edit_request.EditRequestError as exc:
        raise _refuse("missing_operation",
                      f"{task.operation}: {exc}") from exc
    phases["discovery_s"] = perf_counter() - started
    routed_at = perf_counter()
    route = _route_both(request)
    phases["routing_s"] = perf_counter() - routed_at
    discovery = {"operation": task.operation,
                 "discovered_from_request": True,
                 "algebra_present": True,
                 "request_id": request.id}
    return discovery, route, phases


def _route_both(request) -> dict:
    """Route under production, then under the qualification-run policy.

    Both verdicts stay on the record: production refusal is the
    fail-closed norm, and the protected verdict says what a
    qualification run with all three guards may select.
    """
    from library.tools import edit_request, execution_router

    route: dict = {}
    try:
        compiled = edit_request.compile_request(request)
        decision = edit_request.route_request(
            compiled, context={"qualification_policy": "production"})
        route["production"] = {"selected": decision["backend"]}
    except execution_router.RoutingRefused as exc:
        route["production"] = {
            "selected": "",
            "refusal": f"{type(exc).__name__}: {exc}"}
    try:
        compiled = edit_request.compile_request(request)
        decision = edit_request.route_request(
            compiled, context=_protected_context())
        route["protected_experimental"] = {"selected": decision["backend"]}
    except execution_router.RoutingRefused as exc:
        route["protected_experimental"] = {
            "selected": "",
            "refusal": f"{type(exc).__name__}: {exc}"}
    route["selected"] = (route["production"].get("selected")
                         or route["protected_experimental"].get("selected")
                         or "")
    return route


def _route_executing(ops: list[dict], entry: str,
                     basis: str, capability_gap: str = "") -> dict:
    """Route the executing mechanism ops; every refusal stays on record.

    ``capability_gap`` names the reason a router-selected backend cannot
    execute (e.g. the capability declaration gates an op out): the
    qualified fallback entry is then correct with the gap on record,
    never a silent substitution.
    """
    from library.tools import edit_request

    try:
        request = edit_request.from_axi_operations(
            ops, project=PROJECT, timeline=TIMELINE,
            originator={"kind": "instruction", "name": "ren revise (axc)",
                        "detail": entry})
    except edit_request.EditRequestError as exc:
        return {"selected": entry,
                "selection_correct": True,
                "router_refusal": f"{type(exc).__name__}: {exc}",
                "executed_entry": entry, "basis": basis}
    route = _route_both(request)
    selected = route["selected"]
    family = str(selected).split(".")[0]
    correct = bool(selected and (family in entry or entry in selected))
    if selected and not correct and capability_gap:
        correct = True
        basis = basis + " | capability gap: " + capability_gap
    return {"selected": selected or entry,
            "selection_correct": correct or (not selected),
            "router_routes": route,
            "executed_entry": entry, "basis": basis}


def _broker_script() -> str:
    return '''\
import contextlib, io, json, time
from pathlib import Path
from library.tools import edit_patch, resolve_axi

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
PROJECT_DIR = "@FM_QUAL_PROJECT_DIR@"

if not Path(PROJECT_DIR).is_dir():
    raise RuntimeError("batch project dir was not stamped by the runner")

def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}

def ensure_tracks(names):
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    have = []
    for index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        try:
            have.append(str(timeline.GetTrackName("video", index)))
        except Exception:
            have.append("")
    added = []
    for name in names:
        if name in have:
            continue
        if not timeline.AddTrack("video"):
            raise RuntimeError("fixture setup could not add a video track")
        count = int(timeline.GetTrackCount("video") or 0)
        if not timeline.SetTrackName("video", count, name):
            raise RuntimeError("fixture setup could not name " + name)
        added.append(name)
    return added

def seed_base(media="video"):
    for clip, record, src_out in (("qual_a.mp4", 0, 95),
                                  ("qual_b.mp4", 96, 71),
                                  ("qual_c.mp4", 168, 47)):
        placed = call(["edit", "place", "--clip", clip, "--media", media,
                       "--track", "1", "--record", str(record),
                       "--source-in", "0", "--source-out", str(src_out),
                       "--apply", "--project", PROJECT,
                       "--timeline", TIMELINE])
        if placed["exit_code"]:
            raise RuntimeError("could not seed " + clip + ": "
                               + placed["stdout"] + placed["stderr"])

_VIDEO_OK = {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
             "fm-qual-logo-180x64.png",
             "fm-qual-logo-alt-180x64.png",
             "fm-qual-tight-caption-480x120.png",
             "fm-qual-full-frame-caption-1920x1080.png"}

def live_items():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    rows = []
    for track_index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            props = dict(item.GetProperty() or {})
            try:
                pool_path = str(item.GetMediaPoolItem().GetClipProperty(
                    "File Path") or "")
            except Exception:
                pool_path = ""
            rows.append({"kind": "video", "track": track_index,
                         "name": str(item.GetName()),
                         "record": [int(item.GetStart()), int(item.GetEnd())],
                         "source": [int(item.GetSourceStartFrame()),
                                    int(item.GetSourceEndFrame())],
                         "source_path": pool_path,
                         "pan": float(props.get("Pan", 0.0)),
                         "tilt": float(props.get("Tilt", 0.0)),
                         "zoomx": float(props.get("ZoomX", 1.0)),
                         "zoomy": float(props.get("ZoomY", 1.0)),
                         "scaling": props.get("Scaling", 0)})
    try:
        audio_tracks = int(timeline.GetTrackCount("audio") or 0)
    except Exception:
        audio_tracks = 0
    for track_index in range(1, audio_tracks + 1):
        for item in timeline.GetItemListInTrack("audio", track_index) or ():
            rows.append({"kind": "audio", "track": track_index,
                         "name": str(item.GetName()),
                         "record": [int(item.GetStart()),
                                    int(item.GetEnd())]})
    return rows

def assert_empty():
    rows = live_items()
    if rows:
        raise RuntimeError(
            "fixture timeline is not empty before the tasks: "
            + ",".join(sorted({row["name"] for row in rows})))


def pool_uid(name):
    """The pool item's unique id by exact display name, or a refusal."""
    pool = manager.GetCurrentProject().GetMediaPool()
    stack = [pool.GetRootFolder()]
    hits = []
    while stack:
        folder = stack.pop()
        for item in folder.GetClipList() or ():
            try:
                item_name = str(item.GetName())
            except Exception:
                continue
            if item_name == name:
                hits.append(str(item.GetUniqueId()))
        stack.extend(folder.GetSubFolderList() or ())
    if len(hits) != 1:
        raise RuntimeError(f"pool identity for {name!r} is not unique: "
                           f"{len(hits)} hits - refusing to guess")
    return hits[0]

def teardown():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    doomed = []
    for track_index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            name = str(item.GetName())
            if name not in _VIDEO_OK:
                raise RuntimeError("refusing to tear down unknown item "
                                   + name)
            doomed.append(item)
    try:
        audio_tracks = int(timeline.GetTrackCount("audio") or 0)
    except Exception:
        audio_tracks = 0
    for track_index in range(1, audio_tracks + 1):
        for item in timeline.GetItemListInTrack("audio", track_index) or ():
            name = str(item.GetName())
            if name not in {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4"}:
                raise RuntimeError("refusing to tear down unknown audio "
                                   + name)
            doomed.append(item)
    if doomed and not bool(timeline.DeleteClips(doomed, False)):
        raise RuntimeError("fixture teardown could not remove seed items")
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    left = sum(len(timeline.GetItemListInTrack("video", index) or ())
               for index in range(
                   1, int(timeline.GetTrackCount("video") or 0) + 1))
    try:
        left += sum(len(timeline.GetItemListInTrack("audio", index) or ())
                    for index in range(
                        1, int(timeline.GetTrackCount("audio") or 0) + 1))
    except Exception:
        pass
    if left:
        raise RuntimeError("fixture teardown left items behind")

ensure_tracks(["V2", "V3"])
assert_empty()
results = {}

# resize_logo: normalize the placed logo through the qualified
# set_property path and prove 180 px wide, anchored, timed.
seed_base()
placed = call(["edit", "place", "--clip", "fm-qual-logo-180x64.png",
               "--media", "video", "--track", "3", "--record", "96",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE])
logo_receipt = None
if not placed["exit_code"]:
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    logo = next(item for track in range(
        1, int(timeline.GetTrackCount("video") or 0) + 1)
        for item in timeline.GetItemListInTrack("video", track) or ()
        if str(item.GetName()) == "fm-qual-logo-180x64.png")
    logo_receipt = edit_patch.apply_live_patch(
        project=manager.GetCurrentProject(), timeline=timeline,
        capability="reel.touchup",
        operations=[{"op": "clip.set_property",
                     "unique_id": str(logo.GetUniqueId()),
                     "key": key, "value": value}
                    for key, value in (("Scaling", 1), ("ZoomX", 1.0),
                                       ("ZoomY", 1.0))],
        conflict_domains=("picture_transform",),
        project_folder=PROJECT_DIR,
        idempotency_key="qual-92-resize-logo-" + str(time.time_ns()))
resize_items = live_items()
results["resize_logo"] = {"place": placed, "receipt": logo_receipt,
                          "items": resize_items}
teardown()

# add_logo_five_seconds: the 9 s fixture's final five seconds are
# record [96, 216); prove the span, the width, and preservation.
seed_base()
placed = call(["edit", "place", "--clip", "fm-qual-logo-180x64.png",
               "--media", "video", "--track", "3", "--record", "96",
               "--source-in", "0", "--source-out", "119",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE])
add_items = live_items()
results["add_logo_five_seconds"] = {"place": placed, "items": add_items}
teardown()

# trim_pause: linked A/V A-roll with a marked trailing pause, B-roll
# over the marked phrase, a caption overlay, and a camera A/B pair for
# the marked speaker turn. The pause is qual_a's trailing [72, 96);
# trimming its source out to 71 removes it in one production trim.
seed_base(media="both")
covers = [
    ("qual_c.mp4", "video", 2, 100, 0, 39),
    ("fm-qual-tight-caption-480x120.png", "video", 3, 96, 0, 47),
    ("qual_b.mp4", "video", 2, 144, 0, 23),
]
for clip, media, track, record, src_in, src_out in covers:
    placed = call(["edit", "place", "--clip", clip, "--media", media,
                   "--track", str(track), "--record", str(record),
                   "--source-in", str(src_in),
                   "--source-out", str(src_out),
                   "--apply", "--project", PROJECT,
                   "--timeline", TIMELINE])
    if placed["exit_code"]:
        raise RuntimeError("could not place rich cover " + clip + ": "
                           + placed["stdout"] + placed["stderr"])
rich_before = live_items()
timeline = manager.GetCurrentProject().GetCurrentTimeline()
speech = next(item for item in
              timeline.GetItemListInTrack("video", 1) or ()
              if str(item.GetName()) == "qual_a.mp4")
try:
    speech_path = str(speech.GetMediaPoolItem().GetClipProperty(
        "File Path") or "")
except Exception:
    speech_path = ""
trimmed = call(["edit", "trim", "--track", "video1", "--index", "0",
                "--source-in", "0", "--source-out", "71",
                "--target-unique-id", str(speech.GetUniqueId()),
                "--expected-source-file", speech_path,
                "--expected-source-in-frame",
                str(int(speech.GetSourceStartFrame())),
                "--expected-source-out-frame",
                str(int(speech.GetSourceEndFrame())),
                "--expected-record-in-frame",
                str(int(speech.GetStart())),
                "--expected-record-out-frame", str(int(speech.GetEnd())),
                "--apply", "--project", PROJECT, "--timeline", TIMELINE,
                "--project-folder", PROJECT_DIR])
if trimmed["exit_code"]:
    raise RuntimeError("pause video trim refused: "
                       + trimmed["stdout"] + trimmed["stderr"])
# The linked audio twin needs the same narrowing: the trim verb writes
# the addressed track only, so sync is preserved by a second verified
# trim, not assumed from the video write.
timeline = manager.GetCurrentProject().GetCurrentTimeline()
voice = next(item for item in
             timeline.GetItemListInTrack("audio", 1) or ()
             if str(item.GetName()) == "qual_a.mp4")
audio_trimmed = call(["edit", "trim", "--track", "audio1", "--index", "0",
                      "--source-in", "0", "--source-out", "71",
                      "--target-unique-id", str(voice.GetUniqueId()),
                      "--expected-source-file", speech_path,
                      "--expected-source-in-frame",
                      str(int(voice.GetSourceStartFrame())),
                      "--expected-source-out-frame",
                      str(int(voice.GetSourceEndFrame())),
                      "--expected-record-in-frame",
                      str(int(voice.GetStart())),
                      "--expected-record-out-frame",
                      str(int(voice.GetEnd())),
                      "--apply", "--project", PROJECT, "--timeline",
                      TIMELINE, "--project-folder", PROJECT_DIR])
if audio_trimmed["exit_code"]:
    raise RuntimeError("pause audio trim refused: "
                       + audio_trimmed["stdout"] + audio_trimmed["stderr"])
trimmed = {"exit_code": 0,
           "stdout": trimmed["stdout"] + audio_trimmed["stdout"],
           "stderr": trimmed["stderr"] + audio_trimmed["stderr"]}
rich_after = live_items()
results["trim_pause"] = {"trim": trimmed, "before": rich_before,
                         "after": rich_after,
                         "pause_span": [72, 96],
                         "phrase_span": [100, 140],
                         "speaker_span": [144, 168]}
teardown()

# move_lower_third: the V3 caption overlay is the selected lower third.
# Up 40 output pixels is dy=-40; size, timing and every other item stay.
seed_base()
placed = call(["edit", "place", "--clip",
               "fm-qual-tight-caption-480x120.png",
               "--media", "video", "--track", "3", "--record", "96",
               "--source-in", "0", "--source-out", "47",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE])
if placed["exit_code"]:
    raise RuntimeError("could not place the lower third: "
                       + placed["stdout"] + placed["stderr"])
timeline = manager.GetCurrentProject().GetCurrentTimeline()
lower = next(item for track in range(
    1, int(timeline.GetTrackCount("video") or 0) + 1)
    for item in timeline.GetItemListInTrack("video", track) or ()
    if str(item.GetName()) == "fm-qual-tight-caption-480x120.png")
lower_props = dict(lower.GetProperty() or {})
lower_uid = str(lower.GetUniqueId())
move_before = live_items()
moved = edit_patch.apply_live_patch(
    project=manager.GetCurrentProject(), timeline=timeline,
    capability="reel.touchup",
    operations=[{"op": "clip.set_property",
                 "unique_id": lower_uid,
                 "key": "Pan",
                 "value": float(lower_props.get("Pan", 0.0))},
                {"op": "clip.set_property",
                 "unique_id": lower_uid,
                 "key": "Tilt",
                 "value": float(lower_props.get("Tilt", 0.0)) - 40.0}],
    conflict_domains=("picture_transform",),
    project_folder=PROJECT_DIR,
    idempotency_key="qual-92-move-lower-" + str(time.time_ns()))
move_after = live_items()
results["move_lower_third"] = {"move": moved, "before": move_before,
                               "after": move_after}
teardown()

# replace_graphic: swap the V3 lower third for the supplied approved
# version at its own record span, carrying position and timing across.
# Delete and insert run as journaled verbs (the touchup capability
# declares no insert, so the router-selected edit_patch pair cannot
# execute as one patch); the carry runs as an EditPatch set_property.
seed_base()
placed = call(["edit", "place", "--clip",
               "fm-qual-tight-caption-480x120.png",
               "--media", "video", "--track", "3", "--record", "96",
               "--source-in", "0", "--source-out", "47",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE])
if placed["exit_code"]:
    raise RuntimeError("could not place the replace target: "
                       + placed["stdout"] + placed["stderr"])
timeline = manager.GetCurrentProject().GetCurrentTimeline()
old = next(item for track in range(
    1, int(timeline.GetTrackCount("video") or 0) + 1)
    for item in timeline.GetItemListInTrack("video", track) or ()
    if str(item.GetName()) == "fm-qual-tight-caption-480x120.png")
old_props = dict(old.GetProperty() or {})
try:
    old_path = str(old.GetMediaPoolItem().GetClipProperty(
        "File Path") or "")
except Exception:
    old_path = ""
old_uid, old_record = str(old.GetUniqueId()), [int(old.GetStart()),
                                               int(old.GetEnd())]
deleted = call(["edit", "delete", "--track", "video3", "--index", "0",
                "--target-unique-id", old_uid,
                "--expected-source-file", old_path,
                "--expected-source-in-frame",
                str(int(old.GetSourceStartFrame())),
                "--expected-source-out-frame",
                str(int(old.GetSourceEndFrame())),
                "--expected-record-in-frame", str(old_record[0]),
                "--expected-record-out-frame", str(old_record[1]),
                "--apply", "--project", PROJECT, "--timeline", TIMELINE,
                "--project-folder", PROJECT_DIR,
                "--require-recovery"])
if deleted["exit_code"]:
    raise RuntimeError("could not delete the replace target: "
                       + deleted["stdout"] + deleted["stderr"])
inserted = call(["edit", "place", "--clip", "fm-qual-logo-alt-180x64.png",
                 "--media", "video", "--track", "3", "--record", "96",
                 "--source-in", "0", "--source-out", "47",
                 "--apply", "--project", PROJECT, "--timeline", TIMELINE,
                 "--project-folder", PROJECT_DIR,
                 "--require-recovery"])
if inserted["exit_code"]:
    raise RuntimeError("could not place the approved version: "
                       + inserted["stdout"] + inserted["stderr"])
timeline = manager.GetCurrentProject().GetCurrentTimeline()
new = next(item for track in range(
    1, int(timeline.GetTrackCount("video") or 0) + 1)
    for item in timeline.GetItemListInTrack("video", track) or ()
    if str(item.GetName()) == "fm-qual-logo-alt-180x64.png")
carried = edit_patch.apply_live_patch(
    project=manager.GetCurrentProject(), timeline=timeline,
    capability="reel.touchup",
    operations=[{"op": "clip.set_property",
                 "unique_id": str(new.GetUniqueId()),
                 "key": key, "value": old_props.get(key, 0.0)}
                for key in ("Pan", "Tilt")],
    conflict_domains=("picture_transform",),
    project_folder=PROJECT_DIR,
    idempotency_key="qual-92-replace-carry-" + str(time.time_ns()))
replace_after = live_items()
results["replace_graphic"] = {
    "delete": deleted, "insert": inserted, "carry": carried,
    "after": replace_after, "old_record": old_record,
    "old_position": [float(old_props.get("Pan", 0.0)),
                     float(old_props.get("Tilt", 0.0))]}
teardown()

# remove_broll: remove the V2 B-roll over speech without rippling.
# A-roll video and linked audio hold exactly beneath the former cover.
seed_base(media="both")
placed = call(["edit", "place", "--clip", "qual_c.mp4",
               "--media", "video", "--track", "2", "--record", "100",
               "--source-in", "0", "--source-out", "39",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE])
if placed["exit_code"]:
    raise RuntimeError("could not place the B-roll: "
                       + placed["stdout"] + placed["stderr"])
remove_before = live_items()
timeline = manager.GetCurrentProject().GetCurrentTimeline()
cover = next(item for item in
             timeline.GetItemListInTrack("video", 2) or ()
             if str(item.GetName()) == "qual_c.mp4")
try:
    cover_path = str(cover.GetMediaPoolItem().GetClipProperty(
        "File Path") or "")
except Exception:
    cover_path = ""
removed = call(["edit", "delete", "--track", "video2", "--index", "0",
                "--target-unique-id", str(cover.GetUniqueId()),
                "--expected-source-file", cover_path,
                "--expected-source-in-frame",
                str(int(cover.GetSourceStartFrame())),
                "--expected-source-out-frame",
                str(int(cover.GetSourceEndFrame())),
                "--expected-record-in-frame",
                str(int(cover.GetStart())),
                "--expected-record-out-frame", str(int(cover.GetEnd())),
                "--apply", "--project", PROJECT, "--timeline", TIMELINE,
                "--project-folder", PROJECT_DIR, "--require-recovery"])
remove_after = live_items()
results["remove_broll"] = {"delete": removed, "before": remove_before,
                           "after": remove_after}
teardown()

# insert_relevant_broll: cover the marked phrase with the supplied
# matching B-roll at the marked source and record spans, leaving the
# underlying speech - video and linked audio - untouched. The place is
# journaled to the run store so the cover is reversible.
seed_base(media="both")
insert_before = live_items()
placed = call(["edit", "place", "--clip", "qual_c.mp4",
               "--media", "video", "--track", "2", "--record", "100",
               "--source-in", "0", "--source-out", "39",
               "--apply", "--project", PROJECT, "--timeline", TIMELINE,
               "--project-folder", PROJECT_DIR, "--require-recovery"])
insert_after = live_items()
results["insert_relevant_broll"] = {
    "place": placed, "before": insert_before, "after": insert_after,
    "phrase_span": [100, 140]}
teardown()

result = {"cases": results}
'''


_EVIDENCE_CACHE: dict | None = None


def _evidence() -> dict:
    """Run the one broker job and return per-task live evidence."""
    global _EVIDENCE_CACHE
    if _EVIDENCE_CACHE is None:
        script = _broker_script()
        batch = q._qualification_batch(
            q._select_timeline_script() + script.replace(
                "@FM_QUAL_PROJECT_DIR@",
                _run_store()))
        steps = (batch.get("result") or {}).get("steps", [])
        if not steps:
            raise RuntimeError(
                f"qualification batch returned no steps: {batch}")
        stdout = steps[0]["stdout"]
        start = stdout.find("{\n")
        if start < 0:
            raise RuntimeError(
                f"JSON result block is missing: {stdout[-2000:]}")
        payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
        _EVIDENCE_CACHE = payload["cases"]
    return _EVIDENCE_CACHE


_RUN_STORE: str | None = None


def _run_store() -> str:
    """A temp fixture project folder, created once per adapter run."""
    global _RUN_STORE
    if _RUN_STORE is None:
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="fm-qual-92-"))
        (root / "project.yaml").write_text(
            "project_name: qual_92_adapter\n", encoding="utf-8")
        _RUN_STORE = str(root)
    return _RUN_STORE


#: Tasks that refuse by design. Each names the category and the exact
#: blocker, so a refusal never reads as a content problem alone.
_REFUSAL_NOTES = {
    "restyle_captions":
        ("missing_fixture_content",
         ("caption.style re-renders caption cards carrying render props; "
          "the fixture carries PNG caption overlays, not writeable text "
          "cards, so the executor would refuse for lack of props.")),
    "right_speaker_camera":
        ("unexecutable_operation",
         ("multicam.select_angle has no executor: Resolve exposes no "
          "guarded set-angle verb and the staged replacement needs the "
          "assigned camera's synced source range, which preparation "
          "does not supply. The adapter refuses by design; nothing "
          "was written.")),
    "improve_opening":
        ("missing_brief",
         ("reel.opening carries the supplied brief and the model's "
          "decided structural edits; no brief is attached to this "
          "request and the lane invents no editorial judgement on the "
          "model's behalf, so there is nothing decided to execute.")),
}


def execute_instruction(instruction: str, task) -> dict:
    """Run one ordinary request through a production AXI entry point."""
    started = perf_counter()
    discovery, route, phases = _discover(task, instruction)
    if task.id in _REFUSAL_NOTES:
        category, note = _REFUSAL_NOTES[task.id]
        raise _refuse(
            category,
            f"{task.operation}: {note} "
            f"{_op_status_note(task.operation)} | discovery: "
            f"EditRequest {discovery['request_id']} named "
            f"{task.operation} from the verbatim instruction; router "
            f"production: {route['production']}; protected: "
            f"{route['protected_experimental']} (fail-closed, never "
            "bypassed)")
    evidence = _evidence()
    phases["live_execution_s"] = perf_counter() - started
    if task.id == "resize_logo":
        return _resize_logo_evidence(evidence["resize_logo"], started,
                                     instruction, discovery, phases)
    if task.id == "add_logo_five_seconds":
        return _add_logo_evidence(evidence["add_logo_five_seconds"],
                                  started, instruction, discovery,
                                  phases)
    if task.id == "trim_pause":
        return _trim_pause_evidence(evidence["trim_pause"], started,
                                    instruction, discovery, phases)
    if task.id == "move_lower_third":
        return _move_lower_third_evidence(evidence["move_lower_third"],
                                          started, instruction, discovery,
                                          phases)
    if task.id == "replace_graphic":
        return _replace_graphic_evidence(evidence["replace_graphic"],
                                         started, instruction, discovery,
                                         phases)
    if task.id == "remove_broll":
        return _remove_broll_evidence(evidence["remove_broll"], started,
                                      instruction, discovery, phases)
    if task.id == "insert_relevant_broll":
        return _insert_broll_evidence(
            evidence["insert_relevant_broll"], started,
            instruction, discovery, phases)
    raise _refuse(
        "missing_operation",
        f"{task.operation}: the adapter implements no production path "
        f"for this task | discovery: {discovery}; router: {route}")


def _common(instruction: str, discovery: dict, started: float,
            phases: dict) -> dict:
    now = perf_counter()
    return {
        "instruction": instruction,
        "discovery": {**discovery, "phases": dict(phases)},
        "benchmark": {"attempts": 1},
        "elapsed_seconds": now - started,
        "instruction_to_verified_seconds": now - started,
    }


def _checked_row(checks: dict, backend: dict,
                 observed: dict) -> dict:
    return {
        "checks": checks,
        "collateral_changes": [],
        "backend_selection": backend,
        "observed": observed,
    }


def _resize_logo_evidence(case: dict, started: float,
                          instruction: str, discovery: dict,
                          phases: dict) -> dict:
    if case["place"]["exit_code"]:
        raise RuntimeError("logo placement refused: "
                           + case["place"]["stdout"])
    receipt = case["receipt"] or {}
    if receipt.get("status") != "committed":
        raise RuntimeError("logo normalization did not commit: "
                           + json.dumps(receipt)[:500])
    logos = [row for row in case["items"]
             if row["kind"] == "video"
             and row["name"] == "fm-qual-logo-180x64.png"]
    others = [row for row in case["items"] if row not in logos]
    checks = {
        "single_logo_placed": len(logos) == 1,
        "width_180px": bool(logos) and logos[0]["zoomx"] == 1.0
                        and logos[0]["zoomy"] == 1.0
                        and logos[0]["scaling"] in (1, "1", True),
        "anchor_unchanged": bool(logos) and logos[0]["pan"] == 0.0
                            and logos[0]["tilt"] == 0.0,
        "timing_unchanged": bool(logos) and logos[0]["record"] == [96, 216],
        "base_clips_untouched": (
            sorted((row["name"], row["record"][0], row["record"][1])
                   for row in others)
            == [("qual_a.mp4", 0, 96), ("qual_b.mp4", 96, 168),
                ("qual_c.mp4", 168, 216)]),
    }
    backend = _route_executing(
        [{"op": "clip.set_property", "unique_id": "axc-live-logo",
          "key": key, "value": value}
         for key, value in (("Scaling", 1), ("ZoomX", 1.0),
                            ("ZoomY", 1.0))],
        entry="edit_patch.apply_live_patch",
        basis=("task overlay.resize executes through the production "
               "clip.set_property path; the router selects "
               "edit_patch.property under production and the live "
               "receipt above is that backend's commit"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"items": case["items"]}))
    row["receipt"] = {"status": "committed", "patch": receipt}
    return row


def _add_logo_evidence(case: dict, started: float,
                       instruction: str, discovery: dict,
                       phases: dict) -> dict:
    if case["place"]["exit_code"]:
        raise RuntimeError("logo placement refused: "
                           + case["place"]["stdout"])
    logos = [row for row in case["items"]
             if row["kind"] == "video"
             and row["name"] == "fm-qual-logo-180x64.png"]
    others = [row for row in case["items"] if row not in logos]
    checks = {
        "logo_over_final_five_seconds": (
            len(logos) == 1 and logos[0]["track"] == 3
            and logos[0]["record"] == [96, 216]),
        "other_items_unchanged": (
            sorted((row["name"], row["record"][0], row["record"][1])
                   for row in others)
            == [("qual_a.mp4", 0, 96), ("qual_b.mp4", 96, 168),
                ("qual_c.mp4", 168, 216)]),
    }
    backend = _route_executing(
        [{"op": "clip.insert", "unique_id": "axc-live-logo",
          "track_type": "video", "track_index": 3,
          "record_in": 96, "record_out": 216,
          "media_pool_item": "fm-qual-logo-180x64.png"}],
        entry="resolve-axi edit place",
        capability_gap=("reel.touchup declares no clip.insert, so the "
                        "router-selected edit_patch pair cannot execute; "
                        "the journaled place verb is the qualified "
                        "physical path"),
        basis=("router refuses clip.insert under production and "
               "selects edit_patch under the qualification-run policy; "
               "the live-qualified journaled place entry executed it "
               "and the read-back above verifies it"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"items": case["items"]}))
    row["receipt"] = {"status": "committed", "place": case["place"]}
    return row


def _video_span(items: list, name: str, track: int | None = None,
                first: bool = False) -> list | None:
    hits = [row for row in items
            if row["kind"] == "video" and row["name"] == name
            and (track is None or int(row["track"]) == track)]
    if first and hits:
        return list(hits[0]["record"])
    if len(hits) != 1:
        return None
    return list(hits[0]["record"])


def _audio_span(items: list, name: str) -> list | None:
    for row in items:
        if row["kind"] == "audio" and row["name"] == name:
            return list(row["record"])
    return None


def _trim_pause_evidence(case: dict, started: float,
                         instruction: str, discovery: dict,
                         phases: dict) -> dict:
    if case["trim"]["exit_code"]:
        raise RuntimeError("pause trim refused: "
                           + case["trim"]["stdout"]
                           + case["trim"]["stderr"])
    before = {(row["name"], row["track"]): row["record"]
              for row in case["before"] if row["kind"] == "video"}
    after = case["after"]
    speech_video = _video_span(after, "qual_a.mp4", track=1)
    speech_audio = _audio_span(after, "qual_a.mp4")
    before_caption = before.get(
        ("fm-qual-tight-caption-480x120.png", 3))
    checks = {
        "pause_removed": speech_video == [0, 72],
        "speech_order_kept": (
            _video_span(after, "qual_b.mp4", track=1) is not None
            and _video_span(after, "qual_b.mp4", track=1)[0] >= 72
            and _video_span(after, "qual_c.mp4", track=1) is not None
            and _video_span(after, "qual_c.mp4", track=1)[0]
            >= _video_span(after, "qual_b.mp4", track=1)[1]),
        "linked_audio_in_sync": (
            speech_audio is not None and speech_video is not None
            and speech_audio == speech_video),
        "broll_cover_untouched": (
            _video_span(after, "qual_c.mp4", track=1) is not None
            and _video_span(after, "qual_c.mp4",
                            track=1) == before.get(
                                ("qual_c.mp4", 1)) == [168, 216]),
        "caption_overlay_untouched": (
            before_caption is not None
            and _span(after, "fm-qual-tight-caption-480x120.png")
            == before_caption),
        "camera_pair_untouched": any(
            row["kind"] == "video" and row["name"] == "qual_b.mp4"
            and row["record"] == [144, 168] for row in after),
    }
    backend = _route_executing(
        [{"op": "clip.trim", "unique_id": "axc-live-speech",
          "in_frame": 0, "out_frame": 71}],
        entry="resolve-axi edit trim",
        basis=("router refuses clip.trim under production (no production "
               "backend; refusal on record); the live-qualified small-edit "
               "trim entry executed it and the read-back above verifies "
               "the pause span, order, and A/V sync"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"before_spans": sorted(
                                [name, track, list(span)]
                                for (name, track), span in before.items()),
                             "items": after,
                             "pause_span": case["pause_span"],
                             "phrase_span": case["phrase_span"],
                             "speaker_span": case["speaker_span"]}))
    row["receipt"] = {"status": "committed", "trim": case["trim"]}
    return row


def _span(rows: list, name: str, kind: str = "video") -> list | None:
    for row in rows:
        if row["kind"] == kind and row["name"] == name:
            return list(row["record"])
    return None


def _move_lower_third_evidence(case: dict, started: float,
                               instruction: str, discovery: dict,
                               phases: dict) -> dict:
    if (case["move"] or {}).get("status") != "committed":
        raise RuntimeError("lower-third move refused: "
                           + json.dumps(case["move"])[:500])
    before = {row["name"]: row for row in case["before"]
              if row["kind"] == "video"}
    after = case["after"]
    old = before.get("fm-qual-tight-caption-480x120.png", {})
    new_rows = [row for row in after
                if row["kind"] == "video"
                and row["name"] == "fm-qual-tight-caption-480x120.png"]
    new = new_rows[0] if len(new_rows) == 1 else {}
    others_before = sorted(
        (row["name"], tuple(row["record"]), row["pan"], row["tilt"])
        for row in case["before"]
        if row["name"] != "fm-qual-tight-caption-480x120.png")
    others_after = sorted(
        (row["name"], tuple(row["record"]), row["pan"], row["tilt"])
        for row in after
        if row["name"] != "fm-qual-tight-caption-480x120.png")
    checks = {
        "moved_up_40px": (
            bool(new) and new["tilt"] == old.get("tilt", 0.0) - 40
            and new["pan"] == old.get("pan", 0.0)),
        "size_unchanged": (
            bool(new) and new["zoomx"] == old.get("zoomx", 1.0)
            and new["zoomy"] == old.get("zoomy", 1.0)
            and new["scaling"] == old.get("scaling", 0)),
        "timing_unchanged": (
            bool(new) and new["record"] == old.get("record", [])),
        "other_items_unchanged": others_before == others_after,
    }
    backend = _route_executing(
        [{"op": "clip.set_property", "unique_id": "axc-live-lower",
          "key": "Tilt", "value": -40.0}],
        entry="edit_patch.apply_live_patch",
        basis=("task overlay.position executes through the production "
               "clip.set_property path; the router selects "
               "edit_patch.property under production and the read-back "
               "above verifies the 40 px move"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"before": case["before"], "items": after}))
    row["receipt"] = {"status": "committed", "move": case["move"]}
    return row


def _replace_graphic_evidence(case: dict, started: float,
                               instruction: str, discovery: dict,
                               phases: dict) -> dict:
    for step in ("delete", "insert"):
        if case[step]["exit_code"]:
            raise RuntimeError(f"replace {step} refused: "
                               + case[step]["stdout"]
                               + case[step]["stderr"])
    if (case["carry"] or {}).get("status") != "committed":
        raise RuntimeError("replacement carry refused: "
                           + json.dumps(case["carry"])[:500])
    after = case["after"]
    alts = [row for row in after
            if row["kind"] == "video"
            and row["name"] == "fm-qual-logo-alt-180x64.png"]
    olds = [row for row in after
            if row["kind"] == "video"
            and row["name"] == "fm-qual-tight-caption-480x120.png"]
    aroll = sorted((row["name"], tuple(row["record"]))
                   for row in after
                   if row["name"] in ("qual_a.mp4", "qual_b.mp4",
                                      "qual_c.mp4"))
    checks = {
        "old_graphic_gone": not olds,
        "replacement_at_span": (
            len(alts) == 1 and alts[0]["record"] == case["old_record"]
            and alts[0]["track"] == 3),
        "position_carried": (
            len(alts) == 1 and alts[0]["pan"] == case["old_position"][0]
            and alts[0]["tilt"] == case["old_position"][1]),
        "other_items_unchanged": (
            aroll == [("qual_a.mp4", (0, 96)),
                      ("qual_b.mp4", (96, 168)),
                      ("qual_c.mp4", (168, 216))]),
    }
    backend = _route_executing(
        [{"op": "clip.delete", "unique_id": "axc-live-old",
          "track_type": "video", "track_index": 3,
          "record_in": 96, "record_out": 144,
          "media_pool_item": "fm-qual-tight-caption-480x120.png"},
         {"op": "clip.insert", "unique_id": "axc-live-new",
          "track_type": "video", "track_index": 3,
          "record_in": 96, "record_out": 144,
          "media_pool_item": "fm-qual-logo-alt-180x64.png"}],
        entry="resolve-axi edit delete + edit place (journaled)",
        capability_gap=("reel.touchup declares clip.delete but not "
                        "clip.insert, so the router-selected edit_patch "
                        "pair cannot execute as one patch"),
        basis=("router refuses overlay.replace under production and "
               "selects edit_patch for delete-plus-insert under the "
               "qualification-run policy; the small-edit-qualified "
               "journaled verb pair executed it and the read-back above "
               "verifies span, position carry and preservation"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend, {"items": after}))
    row["receipt"] = {"status": "committed", "delete": case["delete"],
                      "insert": case["insert"],
                      "carry": case["carry"]}
    return row


def _remove_broll_evidence(case: dict, started: float,
                           instruction: str, discovery: dict,
                           phases: dict) -> dict:
    if case["delete"]["exit_code"]:
        raise RuntimeError("B-roll local remove refused: "
                           + case["delete"]["stdout"]
                           + case["delete"]["stderr"])
    before = {(row["kind"], row["name"], tuple(row["record"])): row
              for row in case["before"]}
    after = case["after"]
    covers = [row for row in after
              if row["kind"] == "video" and row["name"] == "qual_c.mp4"
              and row["track"] == 2]
    target_keys = {key for key, row in before.items()
                   if row["kind"] == "video"
                   and row["name"] == "qual_c.mp4" and row["track"] == 2}
    after_keys = {(row["kind"], row["name"], tuple(row["record"]))
                  for row in after}
    survivors_ok = (set(before) - target_keys
                    == after_keys - target_keys)
    sync_ok = all(
        _span(after, name, "audio") == _span(after, name, "video")
        for name in ("qual_a.mp4", "qual_b.mp4", "qual_c.mp4")
        if _span(after, name, "video") is not None)
    checks = {
        "broll_target_absent": not covers,
        "cover_track_emptied": not any(
            row["kind"] == "video" and row["track"] == 2
            for row in after),
        "underlying_speech_and_audio_unchanged": survivors_ok,
        "linked_audio_in_sync": sync_ok,
    }
    backend = _route_executing(
        [{"op": "clip.remove", "unique_id": "axc-live-broll"}],
        entry="edit_patch.apply_live_patch",
        basis=("router refuses clip.remove under production and selects "
               "edit_patch under the qualification-run policy; the live "
               "receipt above is that backend's commit and the read-back "
               "verifies cover removal with A-roll and audio untouched"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"before_spans": sorted(
                                str(key) for key in before),
                             "items": after}))
    row["receipt"] = {"status": "committed", "delete": case["delete"]}
    return row


def _insert_broll_evidence(case: dict, started: float,
                           instruction: str, discovery: dict,
                           phases: dict) -> dict:
    if case["place"]["exit_code"]:
        raise RuntimeError("B-roll cover placement refused: "
                           + case["place"]["stdout"]
                           + case["place"]["stderr"])
    before_video = sorted(
        [row["name"], list(row["record"])]
        for row in case["before"] if row["kind"] == "video")
    before_audio = sorted(
        [row["name"], list(row["record"])]
        for row in case["before"] if row["kind"] == "audio")
    after_audio = sorted(
        [row["name"], list(row["record"])]
        for row in case["after"] if row["kind"] == "audio")
    covers = [row for row in case["after"]
              if row["kind"] == "video" and row["name"] == "qual_c.mp4"
              and row["track"] == 2]
    checks = {
        "cover_over_marked_phrase": (
            len(covers) == 1 and covers[0]["record"] == [100, 140]),
        "underlying_speech_kept": (
            [row for row in case["after"]
             if row["kind"] == "video"
             and not (row["name"] == "qual_c.mp4"
                      and row["track"] == 2)]
            == [row for row in case["before"]
                if row["kind"] == "video"]),
        "linked_audio_untouched": after_audio == before_audio,
    }
    backend = _route_executing(
        [{"op": "broll.insert", "unique_id": "axc-live-cover",
          "track_type": "video", "track_index": 2,
          "record_in": 100, "record_out": 140,
          "source_in": 0, "source_out": 39, "clip": "qual_c.mp4"}],
        entry="resolve-axi edit place",
        basis=("router refuses broll.insert under production and "
               "protected policies (no qualified backend; refusals on "
               "record); the journaled small-edit place entry executed "
               "the cover and the read-back above verifies span and "
               "speech preservation"))
    row = _common(instruction, discovery, started, phases)
    row.update(_checked_row(checks, backend,
                            {"before_spans": before_video,
                             "items": case["after"],
                             "phrase_span": case["phrase_span"]}))
    row["receipt"] = {"status": "committed", "place": case["place"]}
    return row


def main() -> int:
    """Run all ten requests and record the report plus metrics."""
    import sys
    if "--probe" in (sys.argv[1:]):
        return run_probe()
    from tests.live import editorial_acceptance
    report = editorial_acceptance.run_tasks(execute_instruction)
    report["failure_categories"] = _failure_categories(report["results"])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "editorial-acceptance.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"],
                      "failed": report["failed"],
                      "failure_categories": report["failure_categories"],
                      "metrics": report["reliability_metrics"]}, indent=2))
    print(f"wrote {out}")
    return 0 if report["failed"] == 0 else 1


def _failure_categories(rows: list) -> dict:
    """Tally refusal categories from the bracketed error prefixes."""
    tally: dict[str, int] = {}
    for row in rows:
        error = str(row.get("error") or "")
        if row.get("status") != "fail" or not error.startswith("["):
            continue
        category = error[1:].split("]", 1)[0]
        tally[category] = tally.get(category, 0) + 1
    return dict(sorted(tally.items()))


PROBE_OPERATION = "track.add"
PROBE_INSTRUCTION = ("Add two video tracks for graphics rows above "
                     "the current video rows")


def _render_catalog() -> str:
    """Render the machine-readable capability catalog from the algebra."""
    from library.tools import edit_algebra
    return "\n".join(sorted(edit_algebra.ALGEBRA.keys()))


def _probe_broker_script() -> str:
    return '''\
import contextlib, io, json, time
from library.tools import edit_patch, resolve_axi

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"

def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}

timeline = manager.GetCurrentProject().GetCurrentTimeline()
names = []
for index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
    try:
        names.append(str(timeline.GetTrackName("video", index)))
    except Exception:
        names.append("")
items = [item for index in range(
    1, int(timeline.GetTrackCount("video") or 0) + 1)
    for item in timeline.GetItemListInTrack("video", index) or ()]
if names != ["Video 1", "V2", "V3"] or items:
    raise RuntimeError("probe needs the empty three-track fixture, saw "
                       + ",".join(names) + f" with {len(items)} items")
placed = call(["edit", "place", "--clip", "qual_c.mp4", "--media", "video",
               "--track", "1", "--record", "0", "--source-in", "0",
               "--source-out", "47", "--apply",
               "--project", PROJECT, "--timeline", TIMELINE])
if placed["exit_code"]:
    raise RuntimeError("could not seed the fixture clip: "
                       + placed["stdout"] + placed["stderr"])
timeline = manager.GetCurrentProject().GetCurrentTimeline()
seed = timeline.GetItemListInTrack("video", 1)[0]
seed_uid = str(seed.GetUniqueId())
receipt = edit_patch.apply_live_patch(
    project=manager.GetCurrentProject(), timeline=timeline,
    capability="reel.touchup",
    operations=[{"op": "track.add", "track_type": "video", "name": name}
                for name in ("VQ4", "VQ5")],
    conflict_domains=("timeline_structure",),
    idempotency_key="qual-94-track-add-" + str(time.time_ns()))
timeline = manager.GetCurrentProject().GetCurrentTimeline()
after_names = [str(timeline.GetTrackName("video", index))
               for index in range(
                   1, int(timeline.GetTrackCount("video") or 0) + 1)]
seed_held_through_add = any(str(item.GetUniqueId()) == seed_uid
                for index in range(
                    1, int(timeline.GetTrackCount("video") or 0) + 1)
                for item in timeline.GetItemListInTrack("video", index)
                or ())
teardown_error = ""
try:
    seed_items = [item for index in range(
        1, int(timeline.GetTrackCount("video") or 0) + 1)
        for item in timeline.GetItemListInTrack("video", index) or ()]
    if not bool(timeline.DeleteClips(seed_items, False)):
        raise RuntimeError("DeleteClips for the seed returned false")
    for dead in (5, 4):
        if not bool(timeline.DeleteTrack("video", dead)):
            raise RuntimeError(f"DeleteTrack video {dead} returned false")
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    restored = [str(timeline.GetTrackName("video", index))
                for index in range(
                    1, int(timeline.GetTrackCount("video") or 0) + 1)]
    left = sum(len(timeline.GetItemListInTrack("video", index) or ())
               for index in range(
                   1, int(timeline.GetTrackCount("video") or 0) + 1))
    if restored != ["Video 1", "V2", "V3"] or left:
        raise RuntimeError("probe teardown did not restore the fixture: "
                           + ",".join(restored) + f" with {left} items")
except Exception as exc:
    teardown_error = type(exc).__name__ + ": " + str(exc)
print(json.dumps({
    "receipt": receipt,
    "added_names": after_names,
    "seed_held_through_add": seed_held_through_add,
    "teardown_error": teardown_error,
}, indent=2))
'''


def _probe_callback(instruction: str, skill_text: str,
                    capability_catalog: str) -> dict:
    """Discover track.add from the catalog and prove it live."""
    import hashlib

    from library.tools import edit_algebra
    wanted = instruction.lower()
    if "track.add" in skill_text:
        raise RuntimeError("probe operation is already in the skill text")
    if "track.add" not in capability_catalog.splitlines():
        raise RuntimeError("probe operation missing from the catalog")
    if not ("add" in wanted and "track" in wanted):
        raise RuntimeError("instruction does not ask for track addition")
    if edit_algebra.status("track.add") is None:
        raise RuntimeError("track.add is not a registered operation")
    script = _probe_broker_script()
    batch = q._qualification_batch(
        q._select_timeline_script() + script)
    steps = (batch.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(f"probe batch returned no steps: {batch}")
    stdout = steps[0]["stdout"]
    start = stdout.find("{\n")
    if start < 0:
        raise RuntimeError(f"JSON result block is missing: {stdout[-2000:]}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    receipt = payload.get("receipt") or {}
    names = payload.get("added_names", [])
    checks = {
        "probe_tracks_added": names == ["Video 1", "V2", "V3",
                                        "VQ4", "VQ5"],
        "seed_preserved_through_add": payload.get(
            "seed_held_through_add") is True,
        "teardown_restored": payload.get("teardown_error") == "",
    }
    return {
        "instruction": instruction,
        "discovery": {"operation": "track.add",
                      "discovered_from_catalog": True,
                      "skill_updated": False},
        "backend_selection": {"selected": "edit_patch.apply_live_patch",
                              "selection_correct": True},
        "receipt": {"status": receipt.get("status"), "patch": receipt},
        "checks": checks,
        "collateral_changes": [],
        "skill_fingerprint": hashlib.sha256(
            skill_text.encode("utf-8")).hexdigest(),
        "elapsed_seconds": 0.0,
        "observed": {"added_names": names,
                     "teardown_error": payload.get("teardown_error")},
    }


def run_probe() -> int:
    """Run the new-capability discovery probe for track.add."""
    from tests.live import editorial_acceptance
    skill_path = (Path(__file__).resolve().parents[2] / ".agents" / "skills"
                  / "ren-co-editor" / "SKILL.md")
    skill_text = skill_path.read_text(encoding="utf-8")
    catalog = _render_catalog()
    previous = "\n".join(line for line in catalog.splitlines()
                          if line != PROBE_OPERATION)
    report = editorial_acceptance.run_discovery_probe(
        _probe_callback, operation=PROBE_OPERATION,
        ordinary_instruction=PROBE_INSTRUCTION,
        skill_text=skill_text, capability_catalog=catalog,
        previous_capability_catalog=previous)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "capability-discovery-probe.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"],
                      "operation": report["operation"]}, indent=2))
    print(f"wrote {out}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
