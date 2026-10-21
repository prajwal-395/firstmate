"""Product-format AXI qualification: the four EDIT operations on a REAL reel.

The AXI-010..023 scenarios (`tests/live/axi_primitive_qualification.py`)
earn their evidence on synthetic fixtures: a 1920x1080 landscape project at
24fps, one or two clips, one empty row.  A Ren reel is none of those: it is
1080x1920 at 23.976fps, DENSE, with linked video+audio, rendered caption
cards, motion graphics, transforms and retiming on every picture row.  A
qualification earned only on the synthetic shape therefore refuses every
real reel, because the recorded predicates name the landscape state and
the live state is the portrait one.

This module runs the SAME scenarios - the ones the punch list names for
`clip.trim`, `clip.replace`, `clip.insert` and `broll.insert` - against a
Ren-owned SCRATCH COPY of a real reel in the product format, and imports
the evidence it produces through `library/tools/axi_qualification.py`.

What it never does:

* it never writes the captain's projects, timelines or media.  The target
  is a timeline duplicated into a Ren-owned scratch project, and the only
  media it adds is a copy it owns, in `/tmp`;
* it never runs `ren qualification reset`, and it never writes into the
  `Ren Qualification` project;
* it never grades a refusal as a pass.  A scenario that REFUSED is
  recorded as a refusal, with the reason the AXI gave, and it produces no
  qualification row - a refusal proves a refusal;
* it never imports a row whose evidence it did not measure.  The coverage
  of every row is the MEASURED runtime state of the case that earned it
  (`library/tools/axi_qualification_state.py` owns that vocabulary).

The broker that runs the batch must be the broker of THIS checkout: the
qualification predicates live in the registry the broker's worker reads,
so a run against another checkout's broker would measure a state and
import a row that the running code cannot see.  The batch records the
registry path it read, and this module REFUSES a run whose registry is
not this checkout's.

    REN_LIVE_RESOLVE=1 \\
    REN_AXI_QUAL_PROJECT="Ren Scratch - <scratch project>" \\
    REN_AXI_QUAL_TIMELINE="<disposable reel copy>" \\
    python3 -m tests.live.product_format_qualification

The replacement media the `clip.replace` case places is PROVISIONED by
the run when the operator names none: a synthetic clip rendered in the
product format, into the /tmp copy this run owns.  An operator may
name their own file with `REN_AXI_QUAL_REPLACEMENT_SOURCE`; the run
copies it into the same owned place.  The lane used to fall back to a
file inside an operator's home directory, which put a machine-specific
path in the checkout - the dependency itself was the defect, so the
fallback is gone rather than rewritten.

`tests/unit/resolve/test_product_format_qualification.py` names the
defects this closes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REN = ROOT / "bin" / "ren"

#: The evidence this run produces.
REPORT_DIR = ROOT / "tests" / "live" / "reports" / "product-format-qualification"
#: Where the OTHER rows' evidence reports live, and where the contract
#: reads the report a promoted row must re-import from.
CANONICAL_REPORT_DIR = ROOT / "tests" / "live" / "reports" / "registry"
EVIDENCE_PATH = REPORT_DIR / "product-format-evidence.json"

#: The media this run OWNS.  A replacement writes to the pool item it
#: re-points, so the source a case replaces with is a COPY this run made
#: in /tmp - never a captain's file, never a shared fixture.
OWNED_DIR = Path("/tmp/vep-qual-owned")
OWNED_REPLACEMENT = OWNED_DIR / "vep-qual-owned-replacement.mov"

#: The provisioned replacement: the PRODUCT format (portrait, 23.976),
#: and long enough that re-pointing a clip at it cannot shorten the
#: record span the replace case measures.
REPLACEMENT_WIDTH = 1080
REPLACEMENT_HEIGHT = 1920
REPLACEMENT_FPS = "24000/1001"
REPLACEMENT_SECONDS = 120

#: The report schema the importer consumes.
REPORT_SCHEMA = "ren-axi-qualification-result/v2"


@dataclass(frozen=True)
class Case:
    """One product-format scenario.

    `operation` and `backend` are the PAIR the row is keyed on; the row
    is only written when the pair has an executor, because promoting a
    pair without one would route production work to a path that cannot
    run it.

    `expectation` is `commit` or `refuse`: a refusal is a recorded
    outcome, never a qualification.

    `target` names how the case's target item is found on the reel: a
    track and an index (`track`, `index`, 0-based as the AXI's own
    `--index` is), or nothing for a case that only ADDS an item.
    `params` carries what the case needs beyond that.
    """

    id: str
    axi_item: str
    operation: str
    backend: str
    production_entry: str
    expectation: str
    checks: tuple[str, ...]
    track: int = 0
    index: int = 0
    kind: str = "video"
    measures: str = "target"
    promotes: bool = True
    params: dict = field(default_factory=dict)


#: The cases this lane runs.  The four operations the punch list names,
#: plus the two trim shapes that PROVE the boundary: the trim on a
#: rendered CAPTION card, and the trim on a LINKED video+audio target.
CASES: tuple[Case, ...] = (
    Case("broll_insert_keeps_dialogue", "AXI-018",
         "broll.insert", "resolve_axi",
         "resolve-axi edit place", "commit",
         ("cover_on_cover_track", "aroll_unchanged",
          "dialogue_audio_unchanged"),
         track=6, measures="operation",
         params={"clip": "b-soft-focus-craig.mov", "record": 400,
                 "source_in": 0, "source_out": 59}),
    Case("insert_at_frame_on_track", "AXI-010",
         "clip.insert", "resolve_axi",
         "resolve-axi edit place", "commit",
         ("placed_on_named_track", "exact_record_start",
          "exact_source_range", "existing_items_untouched"),
         track=6, measures="operation",
         params={"clip": "mg_geo-podcast_6566dd9b.mov", "record": 500,
                 "source_in": 0, "source_out": 59, "record_out": 560}),
    Case("insert_at_frame_on_track_edit_patch", "AXI-010",
         "clip.insert", "edit_patch",
         "edit_patch.apply_live_patch (reel.build capability)", "commit",
         ("placed_on_named_track", "exact_record_start",
          "exact_source_range", "existing_items_untouched"),
         track=6, measures="operation", promotes=False,
         params={"clip": "mg_geo-podcast_6566dd9b.mov", "record": 500,
                 "source_in": 0, "source_out": 59, "record_out": 559}),
    Case("replace_source_keeps_transform", "AXI-014",
         "clip.replace", "edit_patch",
         "edit_patch.apply_live_patch", "commit",
         ("source_replaced", "transform_preserved",
          "record_span_preserved", "aroll_unchanged"),
         track=6, index=0),
    Case("trim_narrows_source_range", "AXI-012",
         "clip.trim", "resolve_axi",
         "resolve-axi edit trim", "commit",
         ("source_range_narrowed", "record_start_shifted",
          "transform_preserved", "other_items_untouched"),
         track=4, index=2,
         params={"source_in": 4, "source_out": -5}),
    Case("trim_linked_audio_target", "AXI-012",
         "clip.trim", "resolve_axi",
         "resolve-axi edit trim", "commit",
         ("source_range_narrowed", "linked_audio_preserved",
          "other_items_untouched"),
         track=1, index=1,
         params={"source_in": 4, "source_out": -5}),
    Case("delete_linked_target", "AXI-011",
         "clip.delete", "edit_patch",
         "edit_patch.apply_live_patch", "commit",
         ("target_absent", "other_items_untouched"),
         track=1, index=2),
    Case("delete_transformed_target", "AXI-011",
         "clip.delete", "edit_patch",
         "edit_patch.apply_live_patch", "commit",
         ("target_absent", "other_items_untouched"),
         track=4, index=3),
)


#: The pairs this lane owns: the four operations the punch list names,
#: plus the delete whose qualification the trim's own production entry
#: depends on (a trim removes its original through `clip.delete`, so the
#: router refuses the trim on every real reel until the delete is
#: qualified there too).
_OWNED_PAIRS: tuple[tuple[str, str], ...] = (
    ("broll.insert", "resolve_axi"),
    ("clip.delete", "edit_patch"),
    ("clip.insert", "edit_patch"),
    ("clip.replace", "edit_patch"),
    ("clip.trim", "resolve_axi"),
)

#: The ROUTED recheck: the production router's own decision on the real
#: reel, measured live, for every pair the product-format run earned.
#: This is the refusal the punch list named - "no production-qualified
#: executor is available" - asked again on the product-format state.
ROUTED: tuple[dict, ...] = (
    {"operation": "broll.insert", "backend": "resolve_axi",
     "track": 6, "addresses": False,
     "params": {"unique_id": "vep-qual-routed-broll",
                "track_type": "video", "track_index": 6,
                "record_in": 800, "record_out": 860,
                "source_in": 0, "source_out": 59,
                "clip": "b-soft-focus-craig.mov"}},
    {"operation": "clip.insert", "backend": "edit_patch",
     "track": 6, "addresses": False,
     "params": {"unique_id": "vep-qual-routed-insert",
                "track_type": "video", "track_index": 6,
                "record_in": 700, "record_out": 760,
                "source_in": 0, "source_out": 59,
                "pool_clip": "mg_geo-podcast_6566dd9b.mov"}},
    {"operation": "clip.replace", "backend": "edit_patch",
     "track": 6, "index": 0, "addresses": True,
     "params": {"source_file": "@OWNED@"}},
    {"operation": "clip.trim", "backend": "resolve_axi",
     "track": 4, "index": 2, "addresses": True,
     "params": {"in_frame": 0, "out_frame": 0}},
    {"operation": "clip.delete", "backend": "edit_patch",
     "track": 1, "index": 2, "addresses": True,
     "params": {}},
)


#: The broker script.  It runs inside the broker's own worker, under the
#: exclusive Resolve lease, with the persistent scripting connection the
#: worker already holds - never a second client.
BROKER_SCRIPT = '''
import contextlib, io, json, time
from library.tools import axi_qualification_state, edit_patch, resolve_axi

PROJECT = "__AXI_PROJECT__"
TIMELINE = "__AXI_TIMELINE__"
PROJECT_DIR = "__AXI_PROJECT_DIR__"
OWNED_REPLACEMENT = "__AXI_OWNED__"
CASES = __AXI_CASES__
ROUTED = __AXI_ROUTED__
EVIDENCE = "__AXI_EVIDENCE__"


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
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
                try:
                    pool_item = item.GetMediaPoolItem()
                    source_path = str(
                        pool_item.GetClipProperty("File Path") or "")
                    pool_uid = str(pool_item.GetUniqueId() or "")
                    media_type = str(pool_item.GetClipProperty("Type") or "")
                except Exception:
                    source_path = ""
                    pool_uid = ""
                    media_type = ""
                try:
                    linked = [str(other.GetUniqueId())
                              for other in (item.GetLinkedItems() or [])]
                except Exception:
                    linked = []
                try:
                    speed = item.GetSpeed()
                except Exception:
                    speed = None
                try:
                    comps = [str(name) for name in
                             (item.GetFusionCompNameList() or [])]
                except Exception:
                    comps = []
                rows.append({
                    "unique_id": str(item.GetUniqueId()),
                    "kind": kind, "track": index, "track_name": track_name,
                    "name": str(item.GetName()),
                    "source_path": source_path, "pool_uid": pool_uid,
                    "media_type": media_type,
                    "source_in": _frame(item.GetSourceStartFrame()),
                    "source_out": _frame(item.GetSourceEndFrame()),
                    "record_in": _frame(item.GetStart()),
                    "record_out": _frame(item.GetEnd()),
                    "transform": item.GetProperty() or {},
                    "linked_unique_ids": linked,
                    "fusion_comps": comps,
                    "speed": speed,
                })
    return {"timeline": timeline.GetName(), "rows": rows}


def item_on(state, kind, track, index):
    """The item at a track index, or None.

    The index is the AXI's own: 0-based, the position of the item on the
    row (`resolve-axi edit --index`).
    """
    rows = [row for row in state.get("rows", [])
            if row.get("kind") == kind and int(row.get("track") or 0) == track]
    if index < 0 or index >= len(rows):
        return None
    return rows[index]


def pool_uid_of(state, name):
    for row in state.get("rows", []):
        if row.get("name") == name and row.get("pool_uid"):
            return row["pool_uid"]
    return ""


def select_timeline():
    project = manager.GetCurrentProject()
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)
               if project.GetTimelineByIndex(index)
               and project.GetTimelineByIndex(index).GetName() == TIMELINE]
    if len(matches) != 1:
        raise RuntimeError("the target timeline is not selectable: "
                           + repr([str(t.GetName()) for t in matches]))
    current = project.GetCurrentTimeline()
    if not (current is not None
            and str(current.GetUniqueId() or "")
            == str(matches[0].GetUniqueId() or "")):
        project.SetCurrentTimeline(matches[0])
    return str(matches[0].GetUniqueId())


def delete_item(uid):
    """Remove exactly the item a case added, by its unique id."""
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(kind, index) or ():
                if str(item.GetUniqueId()) == uid:
                    timeline.DeleteClips([item], False)
                    return True
    return False


def apply_patch(operations, domains, label, capability):
    """One EditPatch live write, the production entry for the edit_patch
    backend."""
    try:
        receipt = edit_patch.apply_live_patch(
            project=manager.GetCurrentProject(),
            timeline=manager.GetCurrentProject().GetCurrentTimeline(),
            capability=capability,
            operations=operations,
            conflict_domains=tuple(domains),
            project_folder=PROJECT_DIR,
            idempotency_key="vep-qual-" + label + "-" + str(time.time_ns()))
    except Exception as exc:
        return {"argv": ["edit_patch.apply_live_patch", label],
                "exit_code": 1, "stdout": "",
                "stderr": type(exc).__name__ + ": " + str(exc)}
    status = str(receipt.get("status") or "")
    return {"argv": ["edit_patch.apply_live_patch", label],
            "exit_code": 0 if status == "committed" else 1,
            "stdout": json.dumps(receipt, default=str), "stderr": ""}


evidence = {"project": PROJECT, "timeline": TIMELINE,
            "registry": __AXI_REGISTRY__,
            "engine": __AXI_ENGINE__}
evidence["timeline_id"] = select_timeline()
before_all = live_state()
evidence["baseline"] = before_all

results = []
for name, case in CASES.items():
    started = time.perf_counter()
    before = live_state()
    target = item_on(before, case.get("kind", "video"),
                     int(case.get("track") or 0), int(case.get("index") or 0))
    # The qualification state is the state the operation RAN in, so it
    # is measured BEFORE the write: a delete measures its target before
    # the target is gone.
    state = None
    if case.get("measures", "target") == "operation":
        # The op addresses NO existing item (it adds one), so the state
        # it is measured in is the one it declares: the measurement
        # resolves no item, and the dimensions say so.
        state = axi_qualification_state.inspect(
            project=PROJECT, timeline=TIMELINE,
            operations=[{"op": case["operation"],
                         "track_type": "video",
                         "track_index": int(case["track"])}],
            resolve=resolve)
    elif target is not None:
        state = axi_qualification_state.inspect(
            project=PROJECT, timeline=TIMELINE,
            operations=[{"op": case["operation"],
                         "unique_id": target["unique_id"]}],
            resolve=resolve)
    commands = []
    added = []
    if name in ("broll_insert_keeps_dialogue", "insert_at_frame_on_track"):
        params = case.get("params") or {}
        commands.append(call([
            "edit", "place", "--project", PROJECT, "--timeline", TIMELINE,
            "--clip", params["clip"], "--media", "video",
            "--track", str(case["track"]), "--record", str(params["record"]),
            "--source-in", str(params["source_in"]),
            "--source-out", str(params["source_out"]), "--apply"]))
    elif name == "insert_at_frame_on_track_edit_patch":
        params = case.get("params") or {}
        # The op's source range is EXCLUSIVE at its end, as the reel
        # build's own placements are (`record_duration = source_out -
        # source_in`), and the record span is that duration long.
        frames = int(params["source_out"]) - int(params["source_in"])
        commands.append(apply_patch(
            [{"op": "clip.insert", "unique_id": "vep-qual-insert-1",
              "track_type": "video", "track_index": int(case["track"]),
              "record_in": int(params["record"]),
              "record_out": int(params["record"]) + frames,
              "media_pool_item": pool_uid_of(before, params["clip"]),
              "source_in": int(params["source_in"]),
              "source_out": int(params["source_out"])}],
            ["timeline_structure"], "insert-on-cover-row", "reel.build"))
        added.append("vep-qual-insert-1")
    elif name == "replace_source_keeps_transform":
        commands.append(apply_patch(
            [{"op": "clip.replace",
              "unique_id": target["unique_id"] if target else "",
              "source_file": OWNED_REPLACEMENT}],
            ["timeline_structure", "fusion", "color"], "replace-cover",
            "reel.touchup"))
    elif name in ("delete_linked_target", "delete_transformed_target"):
        commands.append(apply_patch(
            [{"op": "clip.delete",
              "unique_id": target["unique_id"] if target else ""}],
            ["timeline_structure"], "delete-linked-target", "reel.touchup"))
    elif name in ("trim_narrows_source_range", "trim_linked_audio_target"):
        params = case.get("params") or {}
        commands.append(call([
            "edit", "trim", "--project", PROJECT, "--timeline", TIMELINE,
            "--track", "video" + str(case["track"]),
            "--index", str(case["index"]),
            "--source-in", str(int(target["source_in"])
                               + int(params["source_in"])),
            "--source-out", str(int(target["source_out"])
                                + int(params["source_out"])),
            "--apply"]))
    after = live_state()
    if name == "replace_source_keeps_transform" and target:
        # The replace re-points the MEDIA POOL ITEM, so every clip that
        # draws from it changes with it.  Point it back, or the next
        # case reads a reel this case changed.
        apply_patch(
            [{"op": "clip.replace",
              "unique_id": target["unique_id"],
              "source_file": target["source_path"]}],
            ["timeline_structure", "fusion", "color"],
            "restore-cover-source", "reel.touchup")
    # Remove exactly what the case added, so the next case reads the
    # reel as it found it.
    before_ids = {row["unique_id"] for row in before["rows"]}
    for row in after["rows"]:
        if row["unique_id"] not in before_ids:
            delete_item(row["unique_id"])
    for uid in added:
        delete_item(uid)
    results.append({
        "id": name,
        "operation": case["operation"],
        "backend": case["backend"],
        "expectation": case["expectation"],
        "checks": list(case["checks"]),
        "params": case.get("params") or {},
        "promotes": bool(case.get("promotes", True)),
        "target": target,
        "commands": commands,
        "before": before,
        "after": after,
        "qualification_state": state,
        "elapsed_seconds": time.perf_counter() - started,
    })

evidence["results"] = results

# The ROUTED recheck: the production router's own decision on the real
# reel, measured live.  This is the refusal the punch list named - "no
# production-qualified executor is available" - asked again, on the
# product-format state, after the evidence was imported.
routed = []
for entry in ROUTED:
    operation = dict(entry.get("params") or {})
    operation["op"] = entry["operation"]
    if operation.get("pool_clip"):
        # The placement names the pool item by its unique id, which is
        # read off the live reel.
        operation["media_pool_item"] = pool_uid_of(
            live_state(), operation.pop("pool_clip"))
    target = item_on(live_state(), entry.get("kind", "video"),
                     int(entry.get("track") or 0),
                     int(entry.get("index") or 0))
    if target is not None and entry.get("addresses"):
        # Only an op that ADDRESSES an existing item carries that item's
        # identity: an op that adds one keeps the id it asks for.
        operation["unique_id"] = target["unique_id"]
    try:
        state = axi_qualification_state.inspect(
            project=PROJECT, timeline=TIMELINE, operations=[operation],
            resolve=resolve)
    except Exception as exc:
        state = {"inspected": False,
                 "reason": type(exc).__name__ + ": " + str(exc)}
    try:
        from library.tools import execution_router
        decision = execution_router.route_axi_dispatch(
            [operation], context={"qualification_state": state,
                                  "qualification_policy": "production"})
    except Exception as exc:
        decision = {"refused": type(exc).__name__ + ": " + str(exc)}
    routed.append({"operation": entry["operation"],
                   "backend": entry.get("backend", ""),
                   "state": state, "decision": decision})
evidence["routed"] = routed

with open(EVIDENCE, "w", encoding="utf-8") as handle:
    json.dump(evidence, handle, indent=1, default=str)
result = {"written": EVIDENCE, "cases": len(results)}
'''


# ── The scoring ──────────────────────────────────────────────────────
#
# A check is TRUE only when the live read-back proves it.  A check that
# could not be read is FALSE: an unread measurement is not a pass.


def _rows(state: dict) -> list[dict]:
    return list((state or {}).get("rows") or ())


def _item(state: dict, uid: str) -> dict | None:
    for row in _rows(state):
        if row.get("unique_id") == uid:
            return row
    return None


def _unchanged(before: dict, after: dict, uid: str) -> bool:
    """The item with this id reads the same before and after."""
    was, now = _item(before, uid), _item(after, uid)
    if was is None or now is None:
        return was is None and now is None
    return (was.get("record_in"), was.get("record_out"),
            was.get("source_in"), was.get("source_out"),
            was.get("source_path")) == (
        now.get("record_in"), now.get("record_out"),
        now.get("source_in"), now.get("source_out"),
        now.get("source_path"))


def _others_untouched(before: dict, after: dict, uid: str) -> bool:
    """Every OTHER item reads the same before and after."""
    for row in _rows(before):
        if row.get("unique_id") == uid:
            continue
        if not _unchanged(before, after, row.get("unique_id") or ""):
            return False
    return True


def _transform_preserved(before: dict, after: dict, uid: str) -> bool:
    was, now = _item(before, uid), _item(after, uid)
    if was is None or now is None:
        return False
    keys = ("Pan", "Tilt", "ZoomX", "ZoomY", "Opacity")
    for key in keys:
        try:
            if abs(float((was.get("transform") or {}).get(key, 0) or 0)
                   - float((now.get("transform") or {}).get(key, 0) or 0)) > 1e-6:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _linked_audio_preserved(before: dict, after: dict, uid: str) -> bool:
    """The linked audio twin of a trimmed item is still on its row.

    A trim re-places the PICTURE and removes the original.  If the
    original carried a linked audio twin, the twin must still be there:
    a trim that drops the dialogue is not a trim, it is a mute.
    """
    was = _item(before, uid)
    if was is None:
        return False
    linked = list(was.get("linked_unique_ids") or ())
    for twin in linked:
        row = _item(after, twin)
        if row is None:
            return False
        if row.get("record_in") != was.get("record_in"):
            return False
    return True


def score(result: dict) -> dict:
    """The checks of one case, from its live read-backs."""
    before = result.get("before") or {}
    after = result.get("after") or {}
    target = result.get("target") or {}
    params = result.get("params") or {}
    uid = target.get("unique_id") or ""
    commands = result.get("commands") or []
    committed = bool(commands) and not any(
        row.get("exit_code") for row in commands)
    after_target = _item(after, uid)
    checks: dict[str, bool] = {}

    if result["id"] == "broll_insert_keeps_dialogue":
        record = int(params.get("record", 0))
        placed = [row for row in _rows(after)
                  if row.get("track") == 6 and row.get("record_in") == record]
        checks["cover_on_cover_track"] = len(placed) == 1
        checks["aroll_unchanged"] = _others_untouched(before, after, "")
        checks["dialogue_audio_unchanged"] = _others_untouched(before, after, "")
    elif result["id"] in ("insert_at_frame_on_track",
                          "insert_at_frame_on_track_edit_patch"):
        record = int(params.get("record", 0))
        record_out = int(params.get("record_out", record))
        placed = [row for row in _rows(after)
                  if row.get("track") == 6 and row.get("record_in") == record]
        checks["placed_on_named_track"] = len(placed) == 1
        checks["exact_record_start"] = bool(
            placed and placed[0].get("record_out") == record_out)
        checks["exact_source_range"] = bool(
            placed and placed[0].get("source_in") == int(
                params.get("source_in", 0))
            and int(placed[0].get("source_out") or 0) in {
                int(params.get("source_out", 0)),
                int(params.get("source_out", 0)) + 1})
        checks["existing_items_untouched"] = _others_untouched(
            before, after, "")
    elif result["id"].startswith("delete_"):
        checks["target_absent"] = _item(after, uid) is None
        checks["other_items_untouched"] = _others_untouched(
            before, after, uid)
    elif result["id"] == "replace_source_keeps_transform":
        checks["source_replaced"] = bool(
            after_target
            and after_target.get("source_path") == result.get("owned_replacement"))
        checks["transform_preserved"] = _transform_preserved(before, after, uid)
        checks["record_span_preserved"] = bool(
            target and after_target
            and (target.get("record_in"), target.get("record_out"))
            == (after_target.get("record_in"), after_target.get("record_out")))
        checks["aroll_unchanged"] = _others_untouched(before, after, uid)
    elif result["id"] == "trim_narrows_source_range":
        checks["source_range_narrowed"] = bool(
            after_target
            and target
            and int(after_target.get("source_in") or 0)
            > int(target.get("source_in") or 0))
        checks["record_start_shifted"] = bool(
            after_target
            and int(after_target.get("record_in") or 0)
            > int(target.get("record_in") or 0))
        checks["transform_preserved"] = _transform_preserved(before, after, uid)
        checks["other_items_untouched"] = _others_untouched(before, after, uid)
    elif result["id"] == "trim_linked_audio_target":
        checks["source_range_narrowed"] = bool(
            after_target
            and target
            and int(after_target.get("source_in") or 0)
            > int(target.get("source_in") or 0))
        checks["linked_audio_preserved"] = _linked_audio_preserved(
            before, after, uid)
        checks["other_items_untouched"] = _others_untouched(before, after, uid)
    return {"checks": checks, "committed": committed}


# ── The registry reports ─────────────────────────────────────────────


def _measured_state(result: dict) -> dict:
    state = (result.get("qualification_state") or {}).get("state") or {}
    return dict(state)


def _predicates(states: list[dict]) -> list[dict]:
    """The conditions every scenario that earned the pair was measured in.

    A field the scenarios agree on is an `equals`; a field they differ on
    is a `one_of` over the values they were measured in - the union of
    the conditions the evidence was ACTUALLY earned under.
    """
    from library.tools import axi_qualification

    values: dict[str, list[str]] = {}
    for state in states:
        for field, value in state.items():
            if value in (None, ""):
                continue
            values.setdefault(field, [])
            if str(value) not in values[field]:
                values[field].append(str(value))
    predicates = []
    for field in axi_qualification.PREDICATE_FIELDS:
        seen = values.get(field)
        if not seen:
            continue
        predicate = {"field": field,
                     "operator": "equals" if len(seen) == 1 else "one_of",
                     "value": seen[0] if len(seen) == 1 else seen}
        predicates.append(axi_qualification.canonical_predicate(predicate))
    return predicates


def _coverage(states: list[dict], configurations: list[str]) -> dict:
    from library.tools import axi_qualification

    dimensions: dict[str, list[str]] = {}
    for field in axi_qualification.PREDICATE_FIELDS:
        seen: list[str] = []
        for state in states:
            value = state.get(field)
            if value not in (None, "") and str(value) not in seen:
                seen.append(str(value))
        dimensions[field] = seen
    return {"dimensions": dimensions,
            "configurations": list(configurations),
            "uncovered": {},
            "source": "measured"}


def build_report(pair: tuple[str, str], results: list[dict],
                 states: list[dict], *,
                 configurations: list[str],
                 project_conditions: list[str],
                 semantic_boundary: dict,
                 preservation_limits: list[str],
                 visual_verification: list[str],
                 audio_verification: list[str]) -> dict:
    """One current-schema qualification report for one pair."""
    checks: dict[str, bool] = {}
    for result in results:
        checks.update(result.get("checks") or {})
    state = states[0] if states else {}
    return {
        "schema": REPORT_SCHEMA,
        "operation_id": pair[0],
        "backend": pair[1],
        "result": "passed",
        "evidence": ("tests/live/reports/product-format-qualification/"
                     "product-format-evidence.json#"
                     + ",".join(result["id"] for result in results)),
        "resolve_version": str(state.get("resolve_version") or ""),
        "environment": {"resolve_version": str(state.get("resolve_version") or ""),
                        "platform": sys.platform},
        "semantic_boundary": semantic_boundary,
        "coverage": _coverage(states, configurations),
        "project_conditions": list(project_conditions),
        "supported_media_types": [str(state.get("media_type") or "video")],
        "expected_state": {name: True for name in checks},
        "actual_state": {"checks": checks,
                         "measurement": state,
                         "scenarios": [result["id"] for result in results]},
        "preservation_checks": [{"check": name, "result": "passed"}
                                for name, value in sorted(checks.items())
                                if value],
        "preservation_limits": list(preservation_limits),
        "visual_verification": list(visual_verification),
        "audio_verification": list(audio_verification),
        "recovery_status": "not_required",
        "qualification_predicates": _predicates(states),
    }


def _existing_row(operation: str, backend: str) -> dict:
    """The registry row this pair already has, if any.

    A row is not narrowed by a new measurement: the evidence that earned
    the old coverage still covers the state it names.  The new evidence
    is ADDED to it, and the row's coverage becomes the union - so a
    fixture that was qualified stays qualified, and the reel that was
    refused becomes routable.
    """
    from library.tools import axi_qualification

    try:
        payload = json.loads(
            axi_qualification.REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    row = (payload.get("records") or {}).get(f"{operation}::{backend}")
    return row if isinstance(row, dict) else {}


def _merge_coverage(report: dict, existing: dict) -> dict:
    """The union of the new coverage and the coverage already recorded."""
    from library.tools import axi_qualification_configurations as qualification_configs

    if not existing:
        return report
    old_coverage = existing.get("coverage") or {}
    old_dimensions = old_coverage.get("dimensions") or {}
    new_dimensions = report["coverage"]["dimensions"]
    merged: dict[str, list[str]] = {}
    for field in axi_qualification_fields():
        values: list[str] = []
        for value in list(old_dimensions.get(field) or ()) + list(
                new_dimensions.get(field) or ()):
            if str(value) not in values:
                values.append(str(value))
        merged[field] = values
    configuration_names = list(dict.fromkeys(
        list(old_coverage.get("configurations") or ())
        + list(report["coverage"]["configurations"])))
    source = ("measured"
              if old_coverage.get("source") == "measured"
              else "derived_from_v1_evidence")
    report["coverage"] = {
        "dimensions": merged,
        "configurations": configuration_names,
        "uncovered": qualification_configs.uncovered_values(merged),
        "source": source,
    }
    # The predicates follow the coverage: the union of the values each
    # field was measured in.
    predicates: dict[str, list[str]] = {}
    for predicate in list(existing.get("qualification_predicates") or ()) + list(
            report.get("qualification_predicates") or ()):
        field = str(predicate.get("field") or "")
        if not field:
            continue
        values = ([predicate.get("value")]
                  if predicate.get("operator") == "equals"
                  else list(predicate.get("value") or ()))
        bucket = predicates.setdefault(field, [])
        for value in values:
            if str(value) not in bucket:
                bucket.append(str(value))
    report["qualification_predicates"] = [
        {"field": field,
         "operator": "equals" if len(values) == 1 else "one_of",
         "value": values[0] if len(values) == 1 else values}
        for field, values in sorted(predicates.items())
    ]
    old_evidence = str(existing.get("evidence") or "").strip()
    if old_evidence and old_evidence not in report["evidence"]:
        report["evidence"] = (report["evidence"] + "; earlier evidence: "
                               + old_evidence)
    return report


def axi_qualification_fields() -> tuple[str, ...]:
    """Every dimension a qualification predicate may name."""
    from library.tools import axi_qualification

    return tuple(axi_qualification.PREDICATE_FIELDS)


def _earned_reports(scored: list[dict], project: str,
                    timeline: str) -> dict:
    """The qualification report of every pair the run EARNED.

    A pair earns a row when a case that names it COMMITTED and every
    check it names passed.  A refusal earns nothing: a refusal proves a
    refusal, and the row it would have produced is not written.
    """
    groups: dict[tuple[str, str], dict] = {}
    for row in scored:
        if not row.get("committed"):
            continue
        if not all((row.get("checks") or {}).values()):
            continue
        if not row.get("promotes", True):
            # The evidence is real and is kept, but the row is not
            # written: the AXI's own path owns this operation, and a
            # second production-enabled writer for it would move the
            # ROUTE off the path the live lanes execute.
            continue
        state = (row.get("qualification_state") or {}).get("state") or {}
        if not state:
            continue
        pair = (row["operation"], row["backend"])
        group = groups.setdefault(pair, {"cases": [], "states": []})
        group["cases"].append(row)
        group["states"].append(state)
    reports = {}
    for pair, group in groups.items():
        operation, backend = pair
        checks = {}
        for case in group["cases"]:
            checks.update(case.get("checks") or {})
        report = build_report(
            pair, group["cases"], group["states"],
            configurations=["real_reel_product_format"],
            project_conditions=[
                f"{project} / {timeline}",
                "a Ren-owned scratch COPY of a real reel, never the "
                "captain's own timeline or media",
                "the product format: portrait 1080x1920, 23.976fps, a "
                "dense timeline with linked video+audio, rendered caption "
                "cards, transforms and retiming",
            ],
            semantic_boundary={
                "applies_to": [
                    f"{operation} on {backend} in the measured "
                    "product-format state: a portrait 1080x1920 reel at "
                    "23.976fps, dense, with linked video+audio, caption "
                    "cards, transforms and retiming",
                ],
                "does_not_apply_to": [
                    "a state outside coverage.dimensions",
                    "an operation/backend pair this evidence does not name",
                    "a timeline that is not a Ren-owned scratch copy of a "
                    "real reel in the product format",
                ],
            },
            preservation_limits=[
                "one operation, one backend, one product-format reel "
                "copy: " + ", ".join(sorted(checks)),
                "the evidence is one live run; it does not qualify other "
                "effect combinations, other frame rates or another Resolve "
                "version",
            ],
            visual_verification=[
                "the live read-back of every clip on every row before and "
                "after the write: source path, source range, record span, "
                "transform, linked group and Fusion comps",
            ],
            audio_verification=[
                "every audio row is read back item by item before and "
                "after the write, and the dialogue rows are compared "
                "unchanged",
            ])
        reports[pair] = _merge_coverage(
            report, _existing_row(operation, backend))
    return reports


def _import_report(path: Path) -> dict:
    """Import one report through the registry tool.

    The import is the ONLY way a row reaches the registry: the tool
    validates the report's evidence, its environment, its boundary and
    its coverage, and refuses a claim the measurement cannot carry.
    """
    proc = subprocess.run(
        [str(ROOT / "bin" / "vep"), "-m", "library.tools.axi_qualification",
         "--import-result", str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=300, check=False)
    return {"path": str(path), "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


# ── The run ──────────────────────────────────────────────────────────


def _render_replacement(path: Path) -> None:
    """Render the synthetic replacement this run owns.

    The clip is in the PRODUCT format, so the file the replace case
    re-points a reel clip at is the shape the reel is in, and it is
    long enough that the replace cannot shorten the record span the
    case measures.  ffmpeg is a tool this repo requires; without it the
    run refuses and names the variable an operator can set to supply
    the file itself.
    """
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i",
        (f"color=c=0x2050c0:size={REPLACEMENT_WIDTH}x{REPLACEMENT_HEIGHT}"
         f":rate={REPLACEMENT_FPS}:duration={REPLACEMENT_SECONDS}"),
        "-f", "lavfi", "-i",
        (f"sine=frequency=440:sample_rate=48000"
         f":duration={REPLACEMENT_SECONDS}"),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-r", REPLACEMENT_FPS,
        "-c:a", "aac", "-shortest", str(path),
    ]
    done = subprocess.run(command, capture_output=True, encoding="utf-8",
                          check=False)
    if done.returncode != 0:
        raise SystemExit(
            "the replacement media could not be provisioned: "
            + (done.stderr.strip() or "ffmpeg could not be run")
            + "; set REN_AXI_QUAL_REPLACEMENT_SOURCE to a media file "
              "this run may copy instead")


def _owned_media(source: str) -> str:
    """The replacement file this run owns, in /tmp.

    A replacement writes to the pool item it re-points, so the source
    a case replaces with is a COPY this run made in /tmp - never a
    captain's file, never a shared fixture.

    The copy's own source is the operator's, named by
    REN_AXI_QUAL_REPLACEMENT_SOURCE.  When that variable names nothing,
    the file is PROVISIONED here.  There is no fallback to a file
    outside the checkout: a checkout that names a machine's directory
    is the defect, so the dependency is removed rather than rewritten.
    """
    OWNED_DIR.mkdir(parents=True, exist_ok=True)
    if source:
        shutil.copyfile(source, OWNED_REPLACEMENT)
        return str(OWNED_REPLACEMENT)
    _render_replacement(OWNED_REPLACEMENT)
    return str(OWNED_REPLACEMENT)


def _submit(script_path: Path, project_dir: Path, purpose: str) -> dict:
    argv = ["run", "--project", os.environ["REN_AXI_QUAL_PROJECT"],
            "--file", str(script_path), "--unsafe", "--json",
            "--acknowledge-copy-grades", "--purpose", purpose,
            "--scope", "project", "--project-dir", str(project_dir)]
    proc = subprocess.run(
        [str(REN), "resolved", "submit", "resolve_axi",
         json.dumps({"argv": argv}), "--wait", "900"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=960, check=False)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()) from exc


def _submit_argv(argv: list[str], purpose: str) -> dict:
    """One broker job that runs an AXI command line."""
    proc = subprocess.run(
        [str(REN), "resolved", "submit", "resolve_axi",
         json.dumps({"argv": argv}), "--wait", "300"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=360, check=False)
    try:
        receipt = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()) from exc
    return _await_receipt(receipt)


def _await_receipt(receipt: dict, timeout_seconds: float = 900.0) -> dict:
    """The receipt of a job that is still running.

    Another lane's job can hold the exclusive lease for minutes, and the
    submission answers before the job runs.  The receipt is polled until
    the job reaches a conclusion, so a caller never reads a half-finished
    run as a refusal.
    """
    deadline = time.monotonic() + timeout_seconds
    while receipt.get("state") in {"queued", "running", "pending"}:
        if time.monotonic() > deadline:
            return receipt
        time.sleep(2.0)
        proc = subprocess.run(
            [str(REN), "resolved", "result", str(receipt.get("id") or "")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False)
        try:
            receipt = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
    return receipt


def _open_project(project: str) -> None:
    """Make the Ren-owned scratch project the OPEN one.

    Another lane may have left a different project open (the captain's
    own, or its own scratch copy).  Every AXI verb refuses to address
    a project that is not open - it would read the wrong timeline.

    The scratch verb that opens a project QUEUES for the machine's one
    scratch origin, and the queue runs while the caller holds the
    instance lease, so opening a project while another lane holds the
    origin holds the machine for the whole wait (measured 2026-10-21:
    a thirteen-minute hold that starved every lane).  So the run WAITS
    for the project to be open instead: the other lane re-opens its own
    scratch project before each of its commands, and that project is
    the one this run needs.
    """
    deadline = time.monotonic() + 900.0
    while True:
        receipt = _submit_argv(
            ["project"],
            "Read the open project for the product-format qualification")
        result = receipt.get("result") or {}
        stdout = str(result.get("stdout") or "")
        if f"name: {project}" in stdout:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(
                "the Ren-owned scratch project never became the open one: "
                + stdout[:400])
        time.sleep(5.0)


def _disposable_copy(project: str, source_timeline: str) -> str:
    """A FRESH copy of the reel, in the Ren-owned scratch project.

    The copy is what the run writes on: the pristine reel is read
    only, and every run gets its own copy so a case that mutates the
    timeline cannot leak into the next run.  The duplication is the
    AXI's own verb, so the copy carries the reel's frame rate and
    geometry - the product format is what is being qualified.
    """
    stamp = str(int(time.time()))
    name = f"vep-qual product format {stamp}"
    receipt: dict = {}
    for _attempt in range(6):
        _open_project(project)
        receipt = _submit_argv(
            ["timeline", "duplicate", "--project", project,
             "--timeline", source_timeline, "--name", name, "--apply"],
            "Copy a real reel into a disposable qualification timeline")
        result = receipt.get("result") or {}
        stdout = str(result.get("stdout") or "")
        if (receipt.get("state") == "done"
                and not result.get("exit_code")):
            return name
        if "the open project is" not in stdout:
            break
    result = receipt.get("result") or {}
    if receipt.get("state") != "done" or result.get("exit_code"):
        raise RuntimeError("the disposable copy was not made: "
                           + json.dumps(receipt)[:2000])
    return name


def _run_batch(script_path: Path, project_dir: Path, project: str,
               purpose: str, attempts: int = 6) -> dict:
    """Run the batch, against the scratch project, retrying the handover.

    Another lane shares the one Resolve and may open its own project
    between this lane's jobs.  A recorded run refuses to address a
    project that is not open - it would read the wrong timeline - so the
    batch is re-offered against the scratch project until it runs.
    """
    last: dict = {}
    for attempt in range(attempts):
        _open_project(project)
        last = _submit(script_path, project_dir, purpose)
        result = last.get("result") or {}
        stdout = str(result.get("stdout") or "")
        if (last.get("state") == "done"
                and not result.get("exit_code")):
            return last
        if "the open project is" not in stdout:
            break
    return last


def _import_existing_reports() -> list[dict]:
    """Re-import the reports this lane already produced.

    The merge is bookkeeping, not measurement: the evidence is the live
    run that is already in the report file, and the row it produces is
    the union of that evidence and the coverage the pair already had.

    Only THIS lane's pairs are touched: a report another lane left in
    the same directory is not this lane's row to import.
    """
    imported = []
    for operation, backend in sorted(_OWNED_PAIRS):
        path = REPORT_DIR / f"{operation}__{backend}.json"
        if not path.is_file():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        merged = _merge_coverage(report, _existing_row(operation, backend))
        path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        imported.append(_import_report(path))
    return imported


def main() -> int:
    if os.environ.get("REN_AXI_QUAL_IMPORT_ONLY") == "1":
        imported = _import_existing_reports()
        print(json.dumps({"imported": imported}, indent=2))
        return 0
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        raise SystemExit("set REN_LIVE_RESOLVE=1 to run against Resolve")
    project = os.environ.get("REN_AXI_QUAL_PROJECT", "").strip()
    source = os.environ.get("REN_AXI_QUAL_SOURCE_TIMELINE", "").strip()
    timeline = os.environ.get("REN_AXI_QUAL_TIMELINE", "").strip()
    if not project or not source:
        raise SystemExit("set REN_AXI_QUAL_PROJECT and "
                         "REN_AXI_QUAL_SOURCE_TIMELINE")
    if not timeline:
        timeline = _disposable_copy(project, source)
    selected = os.environ.get("REN_AXI_QUAL_ONLY", "").strip()
    wanted = {name.strip() for name in selected.split(",") if name.strip()}
    cases = {case.id: case for case in CASES
             if not wanted or case.id in wanted}
    if wanted - set(cases):
        raise SystemExit("unknown scenario IDs: "
                         + ", ".join(sorted(wanted - set(cases))))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    evidence_path = REPORT_DIR / "product-format-evidence.json"
    owned = _owned_media(
        os.environ.get("REN_AXI_QUAL_REPLACEMENT_SOURCE", "").strip())

    cases_arg = {name: {"operation": case.operation,
                        "backend": case.backend,
                        "expectation": case.expectation,
                        "checks": list(case.checks),
                        "kind": case.kind,
                        "track": case.track,
                        "index": case.index,
                        "measures": case.measures,
                        "promotes": case.promotes,
                        "params": dict(case.params)}
                 for name, case in cases.items()}
    script = BROKER_SCRIPT
    script = script.replace('PROJECT = "__AXI_PROJECT__"', f"PROJECT = {project!r}")
    script = script.replace('TIMELINE = "__AXI_TIMELINE__"', f"TIMELINE = {timeline!r}")
    script = script.replace('PROJECT_DIR = "__AXI_PROJECT_DIR__"',
                            'PROJECT_DIR = "@FM_QUAL_PROJECT_DIR@"')
    script = script.replace('"__AXI_OWNED__"', repr(owned))
    script = script.replace("__AXI_CASES__", repr(cases_arg))
    routed = [dict(entry) for entry in ROUTED]
    for entry in routed:
        entry["params"] = {
            key: (owned if value == "@OWNED@" else value)
            for key, value in entry["params"].items()}
    script = script.replace("__AXI_ROUTED__", repr(routed))
    script = script.replace('"__AXI_EVIDENCE__"', repr(str(evidence_path)))
    script = script.replace("__AXI_REGISTRY__", repr(
        str((ROOT / "library" / "tools" / "axi_qualification_records.json"))))
    script = script.replace("__AXI_ENGINE__", repr(str(ROOT)))

    previous_evidence: dict = {}
    if evidence_path.is_file():
        try:
            previous_evidence = json.loads(
                evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_evidence = {}

    with tempfile.TemporaryDirectory(prefix="vep-qual-product-format-") as root:
        root_path = Path(root)
        (root_path / "project.yaml").write_text(
            "project_name: vep-qual-product-format\n", encoding="utf-8")
        script = script.replace("@FM_QUAL_PROJECT_DIR@", str(root_path))
        script_path = Path(tempfile.mkstemp(
            prefix="vep-qual-product-format-", suffix=".py", dir="/tmp")[1])
        try:
            script_path.write_text(script, encoding="utf-8")
            receipt = _run_batch(
                script_path, root_path, project,
                "Qualify the AXI edit operations on a real reel in the "
                "product format")
        finally:
            script_path.unlink(missing_ok=True)

    result = receipt.get("result") or {}
    if receipt.get("state") != "done" or result.get("exit_code"):
        raise RuntimeError("the qualification batch did not run: "
                           + json.dumps(receipt)[:4000])
    if not evidence_path.is_file():
        raise RuntimeError("the batch wrote no evidence file")

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    # The evidence is MERGED across runs: a run that qualifies one
    # operation does not erase the evidence another run earned.
    merged = {row["id"]: row for row in previous_evidence.get("results") or ()}
    merged.update({row["id"]: row for row in evidence.get("results") or ()})
    evidence["results"] = [merged[key] for key in sorted(merged)]
    routed = {row["operation"]: row
              for row in previous_evidence.get("routed") or ()}
    routed.update({row["operation"]: row
                   for row in evidence.get("routed") or ()})
    evidence["routed"] = [routed[key] for key in sorted(routed)]
    evidence_path.write_text(
        json.dumps(evidence, indent=1, default=str) + "\n",
        encoding="utf-8")
    if Path(evidence.get("registry") or "") != (
            ROOT / "library" / "tools" / "axi_qualification_records.json"):
        raise RuntimeError(
            "the batch ran against another checkout's registry ("
            + str(evidence.get("registry"))
            + "); a qualification earned there would import a row this "
              "checkout cannot see")

    scored = []
    for row in evidence.get("results") or []:
        row["owned_replacement"] = owned
        row.update(score(row))
        scored.append(row)
    (REPORT_DIR / "product-format-cases.json").write_text(
        json.dumps({"project": project, "timeline": timeline,
                    "cases": scored}, indent=2, default=str) + "\n",
        encoding="utf-8")
    reports = _earned_reports(scored, project, timeline)
    imported = []
    for (operation, backend), report in sorted(reports.items()):
        path = REPORT_DIR / f"{operation}__{backend}.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        # The canonical evidence report lives beside the other rows', and
        # the report there MUST re-import to the row the registry holds
        # (`tests/contracts/test_axi_qualification_registry.py`).  The
        # report it replaces is kept under its own name, so the evidence
        # it carried is still in the repository.
        canonical = CANONICAL_REPORT_DIR / f"{operation}__{backend}.json"
        if canonical.is_file():
            kept = canonical.with_name(
                canonical.stem + "__earlier-evidence.json")
            canonical.replace(kept)
        canonical.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        imported.append(_import_report(path))
    print(json.dumps({"evidence": str(evidence_path),
                      "cases": [{"id": row["id"],
                                 "committed": row["committed"],
                                 "checks": row["checks"]} for row in scored],
                      "imported": imported},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
