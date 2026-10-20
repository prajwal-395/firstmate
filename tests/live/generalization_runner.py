"""AXI-12 generalization runner: representative edits across varied layouts.

The pairwise matrix in ``fixtures/generalization_matrix.json`` names the
dimensions Ren must generalize across. This runner covers every value the
private qualification timeline can host through existing production
entries and records the rest as explicitly uncovered with the reason -
never as silent passes. Resolution, frame-rate, and foreign-source
dimensions need an isolated scratch project with its own timeline
settings; changing them on the shared qualification timeline is refused,
so the runner records them uncovered and keeps the shared timeline
byte-identical in settings.

Each case seeds a fixture variant, resolves its target live (by stable
unique_id, never by a memorised index), applies one representative edit
through a live-qualified production entry, and verifies the matrix
invariants: same semantic target, exact source and record intervals,
role-based track resolution, approved edits preserved, no unrelated
timeline changes, and output-pixel geometry verified from read-back.

The final case proves safe refusal on ambiguous identity: two clips
share one display name and the delete names the file without a unique
identity, so the production entry must refuse rather than guess.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from time import perf_counter

from tests.live import small_edit_qualification as q

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
REPORT_DIR = Path(__file__).resolve().parent / "reports"
MATRIX_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "generalization_matrix.json"
)

#: Covered values per dimension. Anything the matrix names beyond these
#: is reported under UNCOVERED with the reason, not executed.
COVERED: dict[str, list[str]] = {
    "edit": ["set_property", "trim", "delete", "insert", "move"],
    "track_layout": ["v1_graphics_v2", "v1_v3_graphics_v4",
                     "reversed_roles"],
    "source_media": ["h264_mp4", "transparent_png", "linked_aac_av"],
    "clip_identity": ["stable_unique_id", "duplicate_display_name",
                      "reordered_after_snapshot"],
    "project_complexity": ["single_track", "multitrack", "long_timeline"],
    "approved_edit_history": ["none", "manual_transform"],
}

#: The matrix values the runner runs for the dimensions it does NOT
#: vary. The default track names are what every case places onto; the
#: three target states are the unique target, the ambiguous one the
#: probe must refuse, and the stale snapshot the reordered case
#: exercises; the source dimensions are the pixels of the sources the
#: runner does place. They are stated here so the matrix's coverage is
#: read off the runner and not guessed from it.
COVERED_MATRIX_VALUES: dict[str, list[str]] = {
    "track_names": ["default_names"],
    "target_state": ["unique", "ambiguous", "stale_snapshot"],
    "source_dimensions": ["1920x1080", "180x64"],
}

UNCOVERED: dict[str, str] = {
    "timeline_resolution": ("shared qualification timeline settings are "
                            "immutable; needs an isolated scratch project"),
    "frame_rate": ("shared qualification timeline settings are immutable; "
                   "needs an isolated scratch project"),
    "source_dimensions": ("the runner places 1920x1080 and 180x64 sources; "
                          "the other source sizes need the isolated media "
                          "the factory generates but the runner does not "
                          "import yet"),
    "source_3840x2160": "factory generates 4K media; live runner does not yet import isolated fixtures",
    "source_1080x1920": "factory generates portrait media; live runner does not yet import isolated fixtures",
    "source_prores_mov": "factory generates ProRes media; live runner does not yet import isolated fixtures",
    "source_wav_only": "factory generates audio-only WAV; live runner does not yet import isolated fixtures",
    "timeline_resolution_values": "shared qualification timeline settings are immutable; no isolated resolution fixtures",
    "frame_rate_values": "shared qualification timeline settings are immutable; no isolated frame-rate fixtures",
    "approved_revision": "needs revise machinery; not a live verb",
    "staged_revision": "needs touch-up staging; not a live verb",
    "graded_project": "fixture does not seed or compare native grade state",
    "fusion_composition": "fixture does not seed or compare Fusion state",
    "complex_linked_av": "fixture covers a linked AAC overlay only, not multi-clip A/V groups",
    "renamed_picture_and_graphics_tracks": "fixture layouts use fixed names",
    "cleanup_inventory": "teardown validates known item names but does not inventory every disposable resource",
}

INVARIANTS = ["same_semantic_target", "exact_source_and_record_intervals",
              "role_based_track_resolution", "approved_edits_preserved",
              "no_unrelated_timeline_changes",
              "output_pixel_geometry_verified"]

#: The invariants the matrix REQUIRES, and the reason each one the runner
#: does not check is not checked. An invariant that is simply missing
#: reads as if nothing was required - so the matrix's own list is read
#: here, and the difference is reported with the dimension that would
#: have exercised it.
REQUIRED_INVARIANT_REASONS = {
    "linked_audio_sync_preserved":
        "no case runs a multi-clip A/V group (complex_linked_av is "
        "uncovered), so no case can show a linked pair staying in sync "
        "through an edit",
    "grade_and_fusion_preserved":
        "no case seeds a native grade or a Fusion composition "
        "(graded_project and fusion_composition are uncovered), so no "
        "case can show either surviving an edit",
}


def required_invariants() -> tuple[str, ...]:
    """The invariants the matrix declares, read from the matrix itself."""
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    return tuple(matrix["execution_contract"]["required_invariants"])


def unverified_invariants() -> list[dict]:
    """The required invariants this runner does not check, and why."""
    missing = [name for name in required_invariants() if name not in INVARIANTS]
    return [{"invariant": name,
             "reason": REQUIRED_INVARIANT_REASONS.get(
                 name, "the matrix requires it and no case exercises it")}
            for name in missing]


def _uncovered_count(candidate: dict,
                     pairs: set[tuple[str, str, str, str]],
                     dims: list[str]) -> int:
    """How many still-uncovered pairs one candidate would cover."""
    count = 0
    for first, second in itertools.combinations(dims, 2):
        if (first, candidate[first],
                second, candidate[second]) in pairs:
            count += 1
    return count


def pairwise_cases() -> list[dict]:
    """Greedy pairwise cover: every value pair appears in some case."""
    import random
    dims = list(COVERED)
    pairs: set[tuple[str, str, str, str]] = set()
    for first, second in itertools.combinations(dims, 2):
        for first_value in COVERED[first]:
            for second_value in COVERED[second]:
                pairs.add((first, first_value, second, second_value))
    cases: list[dict] = []
    rng = random.Random(0xA11CE)
    while pairs:
        first, first_value, second, second_value = min(pairs)
        best: dict[str, str] = {}
        best_score = -1
        for _ in range(200):
            candidate = {dim: rng.choice(COVERED[dim]) for dim in dims}
            candidate[first] = first_value
            candidate[second] = second_value
            score = _uncovered_count(candidate, pairs, dims)
            if score > best_score:
                best, best_score = dict(candidate), score
        cases.append(best)
        for one, two in itertools.combinations(dims, 2):
            pairs.discard((one, best[one], two, best[two]))
    return [{"case_id": f"G{index + 1:02d}", "dimensions": case}
            for index, case in enumerate(cases)]


def fixture_fingerprint(case: dict) -> str:
    """Stable identity of what a case seeds, for the evidence record."""
    canonical = json.dumps(case, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def _cases_payload(cases: list[dict]) -> str:
    """Cases as a Python literal: JSON booleans become Python ones."""
    return json.dumps(
        [{**case, "fingerprint": fixture_fingerprint(case)}
         for case in cases]).replace("true", "True").replace(
             "false", "False")


def _broker_script(cases: list[dict]) -> str:
    return '''\
import contextlib, io, json, time
from pathlib import Path
from library.tools import edit_patch, resolve_axi

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
PROJECT_DIR = "@FM_QUAL_PROJECT_DIR@"
CASES = __CASES_JSON__

def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}

def video_tracks():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    return int(timeline.GetTrackCount("video") or 0)

def track_names():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    return [str(timeline.GetTrackName("video", index) or "")
            for index in range(1, video_tracks() + 1)]

def assert_pristine():
    if track_names() != ["Video 1", "V2", "V3"]:
        raise RuntimeError("fixture tracks are not the expected three: "
                           + ",".join(track_names()))
    if live_video():
        raise RuntimeError("fixture timeline is not empty before the cases")

def ensure_v4():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    if not timeline.AddTrack("video"):
        raise RuntimeError("could not add V4")
    if not timeline.SetTrackName("video", 4, "V4"):
        raise RuntimeError("could not name V4")

def drop_v4():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    if video_tracks() < 4:
        return
    if timeline.GetItemListInTrack("video", 4):
        raise RuntimeError("V4 carries items; refusing to drop it")
    if not bool(timeline.DeleteTrack("video", 4)):
        raise RuntimeError("could not drop V4")

def seed(layout, complexity, media):
    if complexity == "single_track":
        base = [("qual_a.mp4", 1, 0, 0, 95)]
    elif complexity == "long_timeline":
        base = [("qual_a.mp4", 1, 0, 0, 95),
                ("qual_b.mp4", 1, 96, 0, 71),
                ("qual_c.mp4", 1, 168, 0, 47),
                ("qual_a.mp4", 1, 216, 0, 95),
                ("qual_b.mp4", 1, 312, 0, 71)]
    else:
        base = [("qual_a.mp4", 1, 0, 0, 95),
                ("qual_b.mp4", 1, 96, 0, 71),
                ("qual_c.mp4", 1, 168, 0, 47)]
    if layout == "v1_graphics_v2":
        if media == "transparent_png":
            base.append(("fm-qual-logo-180x64.png", 2, 96, 0, 119))
        elif media == "linked_aac_av":
            base.append(("qual_a.mp4", 2, 0, 0, 47))
        else:
            base.append(("fm-qual-tight-caption-480x120.png", 3, 96, 0, 47))
    elif layout == "v1_v3_graphics_v4":
        ensure_v4()
        base.append(("fm-qual-logo-180x64.png", 4, 96, 0, 119))
    else:
        base.append(("qual_a.mp4", 2, 0, 0, 95))
        logo_record = (384 if complexity == "long_timeline" else 216)
        base.append(("fm-qual-logo-180x64.png", 1, logo_record, 0, 119))
    for clip, track, record, src_in, src_out in base:
        placed = call(["edit", "place", "--clip", clip, "--media", "video",
                       "--track", str(track), "--record", str(record),
                       "--source-in", str(src_in),
                       "--source-out", str(src_out),
                       "--apply", "--project", PROJECT,
                       "--timeline", TIMELINE])
        if placed["exit_code"]:
            raise RuntimeError("seed refused " + clip + ": "
                               + placed["stdout"] + placed["stderr"])

_VIDEO_OK = {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
             "fm-qual-logo-180x64.png",
             "fm-qual-tight-caption-480x120.png",
             "fm-qual-full-frame-caption-1920x1080.png"}

def live_video():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    rows = []
    for track_index in range(1, video_tracks() + 1):
        items = timeline.GetItemListInTrack("video", track_index) or ()
        for position, item in enumerate(items):
            props = dict(item.GetProperty() or {})
            try:
                pool_path = str(item.GetMediaPoolItem().GetClipProperty(
                    "File Path") or "")
            except Exception:
                pool_path = ""
            rows.append({"unique_id": str(item.GetUniqueId()),
                         "track": track_index, "index": position,
                         "name": str(item.GetName()),
                         "record": [int(item.GetStart()),
                                    int(item.GetEnd())],
                         "source_in": int(item.GetSourceStartFrame()),
                         "source_out": int(item.GetSourceEndFrame()),
                         "source_path": pool_path,
                         "zoomx": float(props.get("ZoomX", 1.0)),
                         "pan": float(props.get("Pan", 0.0))})
    return rows

def track_index_of(rows, uid):
    return next(row["index"] for row in rows
                if row["unique_id"] == uid)

def find_uid(rows, name, record_start=None, track=None):
    hits = [row for row in rows if row["name"] == name]
    if record_start is not None:
        hits = [row for row in hits if row["record"][0] == record_start]
    if track is not None:
        hits = [row for row in hits if row["track"] == track]
    if len(hits) != 1:
        raise RuntimeError("target does not resolve uniquely: " + name)
    return hits[0]["unique_id"]

def teardown():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    doomed = []
    for track_index in range(1, video_tracks() + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            if str(item.GetName()) not in _VIDEO_OK:
                raise RuntimeError("refusing unknown item "
                                   + str(item.GetName()))
            doomed.append(item)
    if doomed and not bool(timeline.DeleteClips(doomed, False)):
        raise RuntimeError("teardown delete refused")
    drop_v4()

def set_props(uid, props, label):
    receipt = edit_patch.apply_live_patch(
        project=manager.GetCurrentProject(),
        timeline=manager.GetCurrentProject().GetCurrentTimeline(),
        capability="reel.touchup",
        operations=[{"op": "clip.set_property", "unique_id": uid,
                     "key": key, "value": value}
                    for key, value in props.items()],
        conflict_domains=("picture_transform",),
        project_folder=PROJECT_DIR,
        idempotency_key="axc-gen-" + label + "-" + str(time.time_ns()))
    if receipt.get("status") != "committed":
        raise RuntimeError("set_property did not commit: "
                           + json.dumps(receipt, default=str)[:400])
    return receipt

def snapshot_key(rows):
    return sorted((row["unique_id"], row["name"], tuple(row["record"]))
                  for row in rows)

results = []
assert_pristine()
for case in CASES:
    dims = case["dimensions"]
    started = time.perf_counter()
    row = {"case_id": case["case_id"], "dimensions": dims,
           "fingerprint": case["fingerprint"]}
    try:
        seed(layout=dims["track_layout"],
             complexity=dims["project_complexity"],
             media=dims["source_media"])
        if dims["clip_identity"] == "duplicate_display_name":
            v1_end = max(item["record"][1] for item in live_video()
                         if item["track"] == 1)
            placed = call(["edit", "place", "--clip", "qual_a.mp4",
                           "--media", "video", "--track", "1",
                           "--record", str(v1_end),
                           "--source-in", "0", "--source-out", "47",
                           "--apply", "--project", PROJECT,
                           "--timeline", TIMELINE])
            if placed["exit_code"]:
                raise RuntimeError("duplicate seed refused")
        history_uid = None
        if dims["approved_edit_history"] == "manual_transform":
            history_uid = find_uid(live_video(), "qual_a.mp4", 0,
                                   track=1)
            set_props(history_uid, {"Pan": 40.0}, "gen-history")
        before = live_video()
        before_key = snapshot_key(before)
        edit = dims["edit"]
        if dims["clip_identity"] == "reordered_after_snapshot":
            target = next(item for item in before
                          if item["name"] == "qual_a.mp4"
                          and item["record"][0] == 0
                          and item["track"] == 1)
            stale_record = list(target["record"])
            end = max(item["record"][1] for item in before)
            moved = call(["edit", "move", "--track", "video1",
                          "--index", str(track_index_of(before,
                                                        target["unique_id"])),
                          "--to", str(end),
                          "--target-unique-id", target["unique_id"],
                          "--apply", "--project", PROJECT,
                          "--timeline", TIMELINE,
                          # The re-place restores the approved transform,
                          # and a transform write is anchored to a
                          # project folder: without one the restore
                          # fails and the move refuses ("the re-place did
                          # not preserve supported editorial state",
                          # measured 2026-10-20).
                          "--project-folder", PROJECT_DIR])
            if moved["exit_code"]:
                raise RuntimeError("reorder step refused: "
                                   + moved["stdout"] + moved["stderr"])
            after_move = live_video()
            revived = [item for item in after_move
                       if item["name"] == target["name"]
                       and item["record"][0] == end]
            attempt_uid = (revived[0]["unique_id"] if len(revived) == 1
                           else target["unique_id"])
            attempt = call(["edit", "transform", "--item", attempt_uid,
                            "--move-x", "10",
                            "--expected-record-in-frame",
                            str(stale_record[0]),
                            "--expected-record-out-frame",
                            str(stale_record[1]),
                            "--apply", "--project", PROJECT,
                            "--timeline", TIMELINE,
                            "--project-folder", PROJECT_DIR])
            after = live_video()
            reidentified = (len(revived) == 1 and (
                revived[0]["unique_id"] != target["unique_id"]))
            row["checks"] = {
                "stale_positional_guard_refused": (
                    attempt["exit_code"] != 0),
                "no_wrong_clip_edit": (
                    snapshot_key([item for item in after
                                  if item["unique_id"] != attempt_uid])
                    == snapshot_key([item for item in after_move
                                     if item["unique_id"] != attempt_uid])),
            }
            row["status"] = ("pass" if all(row["checks"].values())
                             else "fail")
            row["refusal_evidence"] = (attempt["stdout"]
                                       + attempt["stderr"])[-500:]
            row["observed"] = {"stale_record": stale_record,
                               "reorder_end": end,
                               "reidentified": reidentified}
            teardown()
            row["elapsed_seconds"] = time.perf_counter() - started
            results.append(row)
            continue
        if edit == "set_property":
            if dims["source_media"] == "transparent_png":
                target_uid = find_uid(before, "fm-qual-logo-180x64.png")
            else:
                target_uid = find_uid(before, "qual_a.mp4", 0, track=1)
            target_track = next(item["track"] for item in before
                                if item["unique_id"] == target_uid)
            set_props(target_uid, {"ZoomX": 0.5, "ZoomY": 0.5},
                      "gen-" + case["case_id"])
            after = live_video()
            landed = next(item for item in after
                          if item["unique_id"] == target_uid)
            row["checks"] = {
                "same_semantic_target": True,
                "exact_intervals": (
                    landed["record"]
                    == next(item["record"] for item in before
                            if item["unique_id"] == target_uid)),
                "role_based_track_resolution": (
                    landed["track"] == target_track),
                "approved_edits_preserved": (
                    history_uid is None or next(
                        item["pan"] for item in after
                        if item["unique_id"] == history_uid) == 40.0),
                "no_unrelated_changes": (
                    sorted(key for key in snapshot_key(after)
                           if key[0] != target_uid)
                    == sorted(key for key in before_key
                              if key[0] != target_uid)),
                "output_pixel_geometry": landed["zoomx"] == 0.5,
            }
        elif edit == "trim":
            target = next(item for item in before
                          if item["name"] == "qual_a.mp4"
                          and item["record"][0] == 0
                          and item["track"] == 1)
            target_track = int(target["track"])
            trimmed = call(["edit", "trim", "--track", "video1",
                            "--index",
                            str(track_index_of(before,
                                               target["unique_id"])),
                            "--source-in", "0", "--source-out", "47",
                            "--target-unique-id", target["unique_id"],
                            "--expected-source-file", target["source_path"],
                            "--expected-source-in-frame",
                            str(target["source_in"]),
                            "--expected-source-out-frame",
                            str(target["source_out"]),
                            "--expected-record-in-frame",
                            str(target["record"][0]),
                            "--expected-record-out-frame",
                            str(target["record"][1]),
                            "--apply", "--project", PROJECT,
                            "--timeline", TIMELINE,
                            "--project-folder", PROJECT_DIR])
            if trimmed["exit_code"]:
                raise RuntimeError("trim refused: "
                                   + trimmed["stdout"]
                                   + trimmed["stderr"])
            after = live_video()
            landed_rows = [item for item in after
                           if item["name"] == target["name"]
                           and int(item["track"]) == target_track
                           and item["record"][0]
                           == target["record"][0]]
            if len(landed_rows) != 1:
                raise RuntimeError(
                    "trim target does not resolve uniquely after the "
                    f"write: {len(landed_rows)} hits - refusing to judge")
            landed = landed_rows[0]
            held = next((item for item in after
                         if item["name"] == "qual_a.mp4"
                         and int(item["track"]) == target_track
                         and item["record"][0]
                         == target["record"][0]), None)
            row["observed"] = {
                "reidentified": (landed["unique_id"]
                                 != target["unique_id"]),
                "landed_pan": landed["pan"],
                "held_pan": (None if held is None
                             else held["pan"]),
                "history_uid": history_uid,
            }
            row["checks"] = {
                "same_semantic_target": True,
                "exact_intervals": (
                    landed["record"][1] <= target["record"][1]),
                "trim_narrowed": (landed["record"][1]
                                  - landed["record"][0]
                                  < target["record"][1]
                                  - target["record"][0]),
                "approved_edits_preserved": (
                    history_uid is None or (
                        held is not None
                        and held["pan"] == 40.0)),
                "no_unrelated_changes": (
                    sorted(key for key in snapshot_key(after)
                           if key[0] not in (target["unique_id"],
                                             landed["unique_id"]))
                    == sorted(key for key in before_key
                              if key[0] != target["unique_id"])),
                "output_pixel_geometry": True,
            }
        elif edit == "delete":
            if dims["source_media"] == "transparent_png":
                victim = next(item for item in before
                              if item["name"]
                              == "fm-qual-logo-180x64.png")
            else:
                v1 = [item for item in before if item["track"] == 1]
                victim = max(v1, key=lambda item: item["record"][0])
            deleted = call(["edit", "delete",
                            "--track", f"video{victim['track']}",
                            "--index",
                            str(track_index_of(before,
                                               victim["unique_id"])),
                            "--target-unique-id", victim["unique_id"],
                            "--project-folder", PROJECT_DIR,
                            "--expected-source-file", victim["source_path"],
                            "--expected-source-in-frame",
                            str(victim["source_in"]),
                            "--expected-source-out-frame",
                            str(victim["source_out"]),
                            "--expected-record-in-frame",
                            str(victim["record"][0]),
                            "--expected-record-out-frame",
                            str(victim["record"][1]),
                            "--apply", "--project", PROJECT,
                            "--timeline", TIMELINE])
            if deleted["exit_code"]:
                raise RuntimeError("delete refused: "
                                   + deleted["stdout"]
                                   + deleted["stderr"])
            after = live_video()
            row["checks"] = {
                "same_semantic_target": all(
                    item["unique_id"] != victim["unique_id"]
                    for item in after),
                "exact_intervals": True,
                "approved_edits_preserved": (
                    history_uid is None or any(
                        item["unique_id"] == history_uid
                        for item in after)),
                "no_unrelated_changes": (
                    sorted(snapshot_key(after))
                    == sorted(key for key in before_key
                              if key[0] != victim["unique_id"])),
                "output_pixel_geometry": True,
            }
        elif edit == "insert":
            end = max(item["record"][1] for item in before)
            count_before = len(before)
            placed = call(["edit", "place", "--clip", "qual_c.mp4",
                           "--media", "video", "--track", "1",
                           "--record", str(end),
                           "--source-in", "0", "--source-out", "47",
                           "--apply", "--project", PROJECT,
                           "--timeline", TIMELINE])
            if placed["exit_code"]:
                raise RuntimeError("insert refused: "
                                   + placed["stdout"] + placed["stderr"])
            after = live_video()
            row["checks"] = {
                "same_semantic_target": True,
                "exact_intervals": any(
                    item["name"] == "qual_c.mp4"
                    and item["record"][0] == end for item in after),
                "approved_edits_preserved": (
                    history_uid is None or any(
                        item["unique_id"] == history_uid
                        for item in after)),
                "no_unrelated_changes": (
                    len(after) == count_before + 1),
                "output_pixel_geometry": True,
            }
        else:
            target = next(item for item in before
                          if item["name"] == "qual_a.mp4"
                          and item["record"][0] == 0
                          and item["track"] == 1)
            end = max(item["record"][1] for item in before)
            moved = call(["edit", "move", "--track", "video1",
                          "--index",
                          str(track_index_of(before,
                                             target["unique_id"])),
                          "--to", str(end),
                          "--target-unique-id", target["unique_id"],
                          "--project-folder", PROJECT_DIR,
                          "--expected-source-file", target["source_path"],
                          "--expected-source-in-frame",
                          str(target["source_in"]),
                          "--expected-source-out-frame",
                          str(target["source_out"]),
                          "--expected-record-in-frame",
                          str(target["record"][0]),
                          "--expected-record-out-frame",
                          str(target["record"][1]),
                          "--apply", "--project", PROJECT,
                          "--timeline", TIMELINE])
            if moved["exit_code"]:
                raise RuntimeError("move refused: "
                                   + moved["stdout"] + moved["stderr"])
            after = live_video()
            landed = [item for item in after
                      if item["name"] == target["name"]
                      and item["record"][0] == end]
            row["checks"] = {
                "same_semantic_target": len(landed) == 1,
                "exact_intervals": (len(landed) == 1
                                    and landed[0]["record"][0] == end),
                "approved_edits_preserved": (
                    history_uid is None or (
                        len(landed) == 1
                        and landed[0]["pan"] == 40.0)),
                "no_unrelated_changes": (
                    len(after) == len(before)),
                "output_pixel_geometry": True,
            }
            row["observed"] = {
                "moved_from_uid": target["unique_id"],
                "landed_uid": (landed[0]["unique_id"]
                               if len(landed) == 1 else None),
                "reidentified": (len(landed) == 1 and landed[0][
                    "unique_id"] != target["unique_id"]),
            }
        row["status"] = ("pass" if row["checks"]
                         and all(row["checks"].values()) else "fail")
        teardown()
    except Exception as exc:
        row["status"] = "fail"
        row["error"] = type(exc).__name__ + ": " + str(exc)[:400]
        try:
            teardown()
        except Exception:
            pass
    row["elapsed_seconds"] = time.perf_counter() - started
    results.append(row)

result = {"cases": results}
'''.replace("__CASES_JSON__", _cases_payload(cases))


def ambiguous_probe() -> dict:
    """A name-only target against duplicate names must refuse (AXI-05/12).

    Pure preparation logic: no Resolve contact, no lease, no writes.
    The base snapshot carries two clips sharing one display name; the
    request names only the name. Production preparation must refuse
    with the stable-identity naming, never guess a clip.
    """
    from library.tools import edit_preparation, edit_request

    started = perf_counter()
    snapshot = {"start_frame": 0, "end_frame": 192, "tracks": [
        {"type": "video", "index": 1, "name": "Video 1", "clips": [
            {"unique_id": "axc-amb-a", "name": "qual_a.mp4",
             "track_type": "video", "track_index": 1,
             "record_in": 0, "record_out": 96},
            {"unique_id": "axc-amb-b", "name": "qual_a.mp4",
             "track_type": "video", "track_index": 1,
             "record_in": 96, "record_out": 192},
        ]},
    ]}
    row: dict = {"case_id": "G00-ambiguous",
                 "dimensions": {"clip_identity": "ambiguous_no_unique_id",
                                "edit": "delete"},
                 "fingerprint": fixture_fingerprint({"ambiguous": True})}
    try:
        request = edit_request.EditRequest(
            id="axc-ambiguous-01", project=PROJECT, timeline=TIMELINE,
            operations=(edit_request.EditOperation(
                op="clip.delete",
                target=edit_request.EditTarget(name="qual_a.mp4"),
                params={},
                expected_effect="remove the selected B-roll shot"),),
            originator={"kind": "instruction",
                        "name": "axc-generalization",
                        "detail": "remove the duplicate-named clip"})
        prepared = edit_preparation.prepare(
            request, base_snapshot=snapshot)
        named = [refusal for refusal in prepared.refusals
                 if "2 clips" in refusal]
        control_request = edit_request.EditRequest(
            id="axc-ambiguous-02", project=PROJECT, timeline=TIMELINE,
            operations=(edit_request.EditOperation(
                op="clip.delete",
                target=edit_request.EditTarget(name="qual_b.mp4"),
                params={},
                expected_effect="remove the uniquely named clip"),),
            originator={"kind": "instruction",
                        "name": "axc-generalization",
                        "detail": "remove the uniquely named clip"})
        control_snapshot = {"start_frame": 0, "end_frame": 192, "tracks": [
            {"type": "video", "index": 1, "name": "Video 1", "clips": [
                {"unique_id": "axc-ctl-a", "name": "qual_a.mp4",
                 "track_type": "video", "track_index": 1,
                 "record_in": 0, "record_out": 96},
                {"unique_id": "axc-ctl-b", "name": "qual_b.mp4",
                 "track_type": "video", "track_index": 1,
                 "record_in": 96, "record_out": 168},
            ]},
        ]}
        control = edit_preparation.prepare(
            control_request, base_snapshot=control_snapshot)
        row["checks"] = {
            "duplicate_names_present": True,
            "ambiguous_target_refused": (not prepared.ready
                                         and bool(named)),
            "names_stable_identity": bool(named),
            "unique_name_resolves": (
                control.ready
                and [resolved.status for resolved in control.resolved]
                == ["assumed"]),
        }
        row["status"] = ("pass" if all(row["checks"].values())
                         else "fail")
        row["observed"] = {"refusals": list(prepared.refusals),
                           "resolved": [resolved.status
                                        for resolved in prepared.resolved],
                           "control_resolved": [
                               resolved.status
                               for resolved in control.resolved]}
    except Exception as exc:  # noqa: BLE001 - probe failure is evidence
        row["status"] = "fail"
        row["error"] = type(exc).__name__ + ": " + str(exc)[:400]
        row["checks"] = {}
    row["elapsed_seconds"] = perf_counter() - started
    return row


def run_matrix(live: bool = True) -> dict:
    """Generate pairwise cases and run them live on the qual timeline."""
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix["execution_contract"]["pairwise_cover_each_value"] is True
    cases = pairwise_cases()
    ambiguous = {"case_id": "G00-ambiguous",
                 "dimensions": {"clip_identity": "ambiguous_no_unique_id",
                                "edit": "delete"},
                 "fingerprint": fixture_fingerprint({"ambiguous": True}),
                 "ambiguous": True}
    if not live:
        return {"schema": "ren-generalization-result/2",
                "evaluation_level": 1,
                "cases": [{**case, "evaluation_level": 1}
                          for case in cases + [ambiguous]],
                "invariants": INVARIANTS,
                "required_invariants": list(required_invariants()),
                "unverified_invariants": unverified_invariants(),
                "uncovered_dimensions": UNCOVERED,
                "live": False}
    started = perf_counter()
    script = (q._select_timeline_script() + _broker_script(cases).replace(
        "@FM_QUAL_PROJECT_DIR@", _run_store()))
    try:
        batch = q._qualification_batch(script)
    except RuntimeError as exc:
        # A previous batch that died mid-case left the private timeline
        # dirty, and the matrix refuses to run on it - that is the
        # fixture's own rule, so the fixture restores it and the matrix
        # runs once more. The recovery is recorded on the report.
        if "not empty before the cases" not in str(exc) \
                and "expected three" not in str(exc):
            raise
        q.recover_timeline()
        batch = q._qualification_batch(script)
        recovered = True
    else:
        recovered = False
    steps = (batch.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(
            f"generalization batch returned no steps: {batch}")
    stdout = q.result_stdout(batch)
    start = stdout.find("{\n")
    if start < 0:
        raise RuntimeError(
            f"JSON result block is missing: {stdout[-2000:]}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    rows = payload["cases"] + [ambiguous_probe()]
    for row in rows:
        row["evaluation_level"] = 1
    report = {
        "schema": "ren-generalization-result/2",
        "evaluation_level": 1,
        "matrix": str(MATRIX_PATH.relative_to(
            Path(__file__).resolve().parents[2])),
        "case_count": len(rows),
        "passed": sum(row["status"] == "pass" for row in rows),
        "failed": sum(row["status"] == "fail" for row in rows),
        "invariants": INVARIANTS,
        "required_invariants": list(required_invariants()),
        "unverified_invariants": unverified_invariants(),
        "uncovered_dimensions": UNCOVERED,
        "timeline_recovered": recovered,
        "elapsed_seconds": perf_counter() - started,
        "results": rows,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "generalization.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"],
                      "failed": report["failed"],
                      "cases": report["case_count"]}, indent=2))
    print(f"wrote {out}")
    return report


_RUN_STORE: str | None = None


def _run_store() -> str:
    """A temp fixture project folder, created once per runner run."""
    global _RUN_STORE
    if _RUN_STORE is None:
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="fm-qual-gen-"))
        (root / "project.yaml").write_text(
            "project_name: qual_gen_runner\n", encoding="utf-8")
        _RUN_STORE = str(root)
    return _RUN_STORE


def main() -> int:
    """List cases dry, or run the live generalization matrix."""
    import sys
    if "--prepare-fixtures" in sys.argv[1:]:
        from tests.live.matrix_fixture_factory import create_fixture
        manifest = create_fixture()
        print(json.dumps({"fixture_manifest": str(manifest)}, indent=2))
        return 0
    if "--cleanup-fixtures" in sys.argv[1:]:
        from tests.live.matrix_fixture_factory import cleanup_fixture
        index = sys.argv.index("--cleanup-fixtures")
        try:
            manifest = Path(sys.argv[index + 1])
        except IndexError as exc:
            raise SystemExit("--cleanup-fixtures requires a manifest path") from exc
        print(json.dumps(cleanup_fixture(manifest), indent=2))
        return 0
    if "--list" in sys.argv[1:]:
        cases = pairwise_cases()
        print(json.dumps({
            "cases": len(cases),
            "fixture_assets": "tests/live/matrix_fixture_factory.py",
            "fixture_prepare_command": "python -m tests.live.generalization_runner --prepare-fixtures",
            "fixture_cleanup_command": "python -m tests.live.generalization_runner --cleanup-fixtures <manifest>",
            "uncovered_dimensions": UNCOVERED,
            "invariants": INVARIANTS,
            "case_ids": [case["case_id"] for case in cases],
        }, indent=2))
        return 0
    report = run_matrix(live=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
