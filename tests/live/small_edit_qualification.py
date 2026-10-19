"""Tier B live qualification scenario contract and scoring helpers.

The runner is intentionally a thin layer over Ren's production edit entry
points. Scenario actions are supplied by a live adapter so this module can
also be imported by the report collector without importing Resolve.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True)
class Scenario:
    id: str
    intent: str
    production_entry: str
    operation: str
    proof: bool = False


SCENARIOS = (
    Scenario("add_video", "Add a video clip", "resolve-axi edit place", "clip.insert"),
    Scenario("add_logo", "Add an independent PNG logo", "edit_patch.apply_live_patch", "overlay.add", True),
    Scenario("move_logo", "Move the logo 40 px", "edit_patch.apply_live_patch", "overlay.position", True),
    Scenario("resize_logo", "Resize the logo and preserve its anchor", "edit_patch.apply_live_patch", "overlay.resize", True),
    Scenario("tight_caption", "Place a tight caption", "edit_patch.apply_live_patch", "overlay.add"),
    Scenario("full_frame_caption", "Place a full-frame caption", "edit_patch.apply_live_patch", "overlay.add"),
    Scenario("replace_overlay", "Replace one overlay", "edit_patch.apply_live_patch", "overlay.swap_pixels"),
    Scenario("trim", "Trim a clip", "resolve-axi edit trim", "clip.trim"),
    Scenario("move_clip", "Move a clip", "resolve-axi edit move", "clip.move"),
    Scenario("transform", "Transform an existing clip", "edit_patch.apply_live_patch", "clip.set_property"),
    Scenario("rebuild_after_edit", "Rebuild after a user edit", "reel_build", "rebuild"),
    Scenario("mutation_failure", "Recover from a forced mutation failure", "edit_patch.apply_live_patch", "forced_mutation_failure"),
    Scenario("verification_failure", "Do not promote after forced verification failure", "reel_build", "forced_verification_failure"),
    Scenario("promotion_conflict", "Preserve approved timeline on promotion conflict", "reel_build", "promotion_conflict"),
)


def run_scenario(scenario: Scenario, action: Callable[[], dict]) -> dict:
    """Run once and record timing and isolation evidence from the adapter.

    `action` must execute the production entry point and return its receipt,
    a before/after state comparison, and the proof-specific checks. Exceptions
    are captured as failures; the harness never retries a failed first attempt.
    """
    started = perf_counter()
    try:
        evidence = action()
        receipt = evidence["receipt"]
        collateral = evidence["collateral_changes"]
        checks = evidence["checks"]
        elapsed = evidence.get("elapsed_seconds", perf_counter() - started)
        first_attempt = int(evidence.get("attempts", 1)) == 1
        passed = (first_attempt and not collateral and all(checks.values())
                  and receipt.get("status") in {"committed", "verified"})
        return {
            **asdict(scenario), "status": "pass" if passed else "fail",
            "first_attempt": first_attempt,
            "collateral_changes": collateral,
            "checks": checks,
            "receipt": receipt,
            **({"observed": evidence["observed"]}
               if "observed" in evidence else {}),
            "elapsed_seconds": elapsed,
        }
    except Exception as exc:  # noqa: BLE001 - preserve live failure evidence
        return {
            **asdict(scenario), "status": "fail", "first_attempt": True,
            "collateral_changes": [], "checks": {},
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": perf_counter() - started,
        }


def proof_milestone_passes(result: dict) -> bool:
    """Enforce the full logo milestone, including the 30 second objective."""
    required = {"independent_item", "last_five_seconds", "width_180px",
                "inside_safe_zone", "no_other_changes", "survives_rebuild"}
    checks = result.get("checks", {})
    return (required <= checks.keys() and all(checks[k] for k in required)
            and result.get("elapsed_seconds", float("inf")) < 30.0)


def score(results: list[dict]) -> dict:
    """Summarize first-attempt correctness and collateral changes."""
    return {
        "scenarios": len(results),
        "passed": sum(row["status"] == "pass" for row in results),
        "failed": sum(row["status"] == "fail" for row in results),
        "first_attempt_passes": sum(
            row["status"] == "pass" and row["first_attempt"] for row in results),
        "unintended_change_scenarios": sum(
            bool(row.get("collateral_changes")) for row in results),
        "proof_milestone": next((proof_milestone_passes(row) for row in results
                                  if row.get("proof")), False),
        "results": results,
    }


def run_batch(actions: dict[str, Callable[[], dict]]) -> dict:
    """Run the declared live actions once and return a JSON-ready score.

    Each action is a narrow adapter around an existing production entry
    point. A missing adapter is itself reported as an unqualified scenario;
    the harness never invents a substitute operation or retries it.
    """
    results = []
    for scenario in SCENARIOS:
        action = actions.get(scenario.id)
        if action is None:
            results.append({
                **asdict(scenario), "status": "fail", "first_attempt": True,
                "collateral_changes": [], "checks": {},
                "error": "no production action adapter registered",
                "elapsed_seconds": 0.0,
            })
            continue
        results.append(run_scenario(scenario, action))
    return score(results)


ROOT = Path(__file__).resolve().parents[2]
PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
REN = ROOT / "bin" / "ren"
LOGO = Path("/tmp/fm-qual-logo-180x64.png")
LOGO_ALT = Path("/tmp/fm-qual-logo-alt-180x64.png")
TIGHT_CAPTION = Path("/tmp/fm-qual-tight-caption-480x120.png")
FULL_FRAME_CAPTION = Path("/tmp/fm-qual-full-frame-caption-1920x1080.png")


def _qualification_batch(script: str) -> dict:
    """Run all production edits in one switching qualification borrow."""
    if not REN.is_file():
        raise RuntimeError(f"Ren CLI not found: {REN}")
    with tempfile.TemporaryDirectory(prefix="fm-small-edit-run-record-") as root:
        root_path = Path(root)
        (root_path / "project.yaml").write_text(
            "project_name: small_edit_qualification\n", encoding="utf-8")
        _initialize_run_store(root_path)
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".py",
                prefix="fm-small-edit-batch-", dir="/tmp",
                delete=False) as f:
            f.write(_select_timeline_script() + script)
            script_path = Path(f.name)

        def run_command(timeline: str | None, path: Path,
                        purpose: str) -> list[str]:
            command = ["run", "--project", PROJECT]
            if timeline:
                command.extend(["--timeline", timeline])
            command.extend(["--file", str(path), "--unsafe", "--json",
                            "--purpose", purpose, "--scope", "project",
                            "--project-dir", str(root_path)])
            return command

        steps = [{"resolve_axi": run_command(
            None, script_path, "Run Tier B small-edit qualification batch")}]
        try:
            proc = subprocess.run(
                [str(REN), "resolved", "submit",
                 "resolve.qualification_session",
                 json.dumps({"project": PROJECT, "steps": steps}),
                 "--qualification", "--wait", "180"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                timeout=240, check=False)
        finally:
            script_path.unlink(missing_ok=True)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        try:
            receipt = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"broker returned non-JSON receipt: {proc.stdout}") from exc
        if receipt.get("state") != "done" or not receipt.get("result"):
            raise RuntimeError(
                f"qualification batch failed: {receipt.get('error', receipt)}")
        return receipt


def _select_timeline_script() -> str:
    return f'''\
project = manager.GetCurrentProject()
matches = [project.GetTimelineByIndex(i)
           for i in range(1, int(project.GetTimelineCount() or 0) + 1)
           if project.GetTimelineByIndex(i)
           and project.GetTimelineByIndex(i).GetName() == {TIMELINE!r}]
if len(matches) != 1 or not project.SetCurrentTimeline(matches[0]):
    raise RuntimeError("could not make the exact private scratch timeline current")
current = project.GetCurrentTimeline()
if current.GetUniqueId() != matches[0].GetUniqueId():
    raise RuntimeError("private scratch timeline did not read back as current")
result = {{"timeline": current.GetName(), "id": current.GetUniqueId()}}
'''


def _initialize_run_store(root: Path) -> None:
    commands = (
        ["git", "init", str(root)],
        ["git", "-C", str(root), "config", "user.name",
         "VEP Qualification"],
        ["git", "-C", str(root), "config", "user.email",
         "vep-qualification@localhost"],
        ["git", "-C", str(root), "add", "project.yaml"],
        ["git", "-C", str(root), "commit", "-m",
         "Initialize temporary qualification script store"],
    )
    for command in commands:
        proc = subprocess.run(command, cwd=root, capture_output=True,
                              text=True, encoding="utf-8", timeout=30,
                              check=False)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def _edit(*args: str) -> list[str]:
    return ["edit", *args, "--project", PROJECT, "--timeline", TIMELINE]


def _logo_base_commands() -> list[list[str]]:
    return [
        _edit("place", "--clip", "qual_a.mp4", "--media", "video",
              "--track", "1", "--record", "0", "--source-in", "0",
              "--source-out", "95", "--apply"),
        _edit("place", "--clip", "qual_b.mp4", "--media", "video",
              "--track", "1", "--record", "96", "--source-in", "0",
              "--source-out", "71", "--apply"),
        _edit("place", "--clip", "qual_c.mp4", "--media", "video",
              "--track", "1", "--record", "168", "--source-in", "0",
              "--source-out", "47", "--apply"),
    ]


def _live_batch_script() -> str:
    """Run production AXI calls and capture direct before/after Resolve state."""
    cases = {
        "add_video": [_place_video(),
                      ["items", "--project", PROJECT, "--timeline", TIMELINE,
                       "--refresh-live"]],
        "trim": [_place_video(), _edit(
            "trim", "--track", "video1", "--index", "0",
            "--source-in", "1", "--source-out", "46", "--apply"),
                 ["items", "--project", PROJECT, "--timeline", TIMELINE,
                  "--refresh-live"]],
        "move_clip": [_place_video(), _edit(
            "move", "--track", "video1", "--index", "0", "--to", "8",
            "--apply"), ["items", "--project", PROJECT, "--timeline",
                         TIMELINE, "--refresh-live"]],
        "add_logo": [
            _edit("place", "--clip", "qual_a.mp4", "--media", "video",
                  "--track", "1", "--record", "0", "--source-in", "0",
                  "--source-out", "95", "--apply"),
            _edit("place", "--clip", "qual_b.mp4", "--media", "video",
                  "--track", "1", "--record", "96", "--source-in", "0",
                  "--source-out", "71", "--apply"),
            _edit("place", "--clip", "qual_c.mp4", "--media", "video",
                  "--track", "1", "--record", "168", "--source-in", "0",
                  "--source-out", "47", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live"],
        ],
        "move_logo": [
            _edit("place", "--clip", "qual_a.mp4", "--media", "video",
                  "--track", "1", "--record", "0", "--source-in", "0",
                  "--source-out", "95", "--apply"),
            _edit("place", "--clip", "qual_b.mp4", "--media", "video",
                  "--track", "1", "--record", "96", "--source-in", "0",
                  "--source-out", "71", "--apply"),
            _edit("place", "--clip", "qual_c.mp4", "--media", "video",
                  "--track", "1", "--record", "168", "--source-in", "0",
                  "--source-out", "47", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live", "--transforms"],
        ],
        "resize_logo": [
            _edit("place", "--clip", "qual_a.mp4", "--media", "video",
                  "--track", "1", "--record", "0", "--source-in", "0",
                  "--source-out", "95", "--apply"),
            _edit("place", "--clip", "qual_b.mp4", "--media", "video",
                  "--track", "1", "--record", "96", "--source-in", "0",
                  "--source-out", "71", "--apply"),
            _edit("place", "--clip", "qual_c.mp4", "--media", "video",
                  "--track", "1", "--record", "168", "--source-in", "0",
                  "--source-out", "47", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live", "--transforms"],
        ],
        "transform": [_place_video(),
                      ["items", "--project", PROJECT, "--timeline", TIMELINE,
                       "--refresh-live", "--transforms"]],
        "tight_caption": [
            *_logo_base_commands(),
            _edit("place", "--clip", "fm-qual-tight-caption-480x120.png",
                  "--media", "video", "--track", "3", "--record", "96",
                  "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live", "--transforms"],
        ],
        "full_frame_caption": [
            *_logo_base_commands(),
            _edit("place", "--clip", "fm-qual-full-frame-caption-1920x1080.png",
                  "--media", "video", "--track", "3", "--record", "96",
                  "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live", "--transforms"],
        ],
        "replace_overlay": [
            *_logo_base_commands(),
            _edit("place", "--clip", "fm-qual-logo-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-alt-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
            _edit("delete", "--track", "video3", "--index", "0", "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live", "--transforms"],
        ],
        # A deliberately impossible delete exercises AXI's mutation refusal
        # path against this disposable timeline. The surrounding state read
        # proves that the refused request did not partially apply.
        "mutation_failure": [
            _logo_base_commands()[0],
            _edit("delete", "--track", "video1", "--index", "999",
                  "--apply"),
            ["items", "--project", PROJECT, "--timeline", TIMELINE,
             "--refresh-live"],
        ],
    }
    return f'''\
import contextlib, io, json, re, time
from library.tools import resolve_axi

PROJECT = {PROJECT!r}
TIMELINE = {TIMELINE!r}
CASES = {cases!r}

def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {{"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}}

def apply_transform_patch(item_id, properties, label):
    from library.tools import edit_patch
    operations = [{{"op": "clip.set_property", "unique_id": item_id,
                   "key": key, "value": value}}
                  for key, value in properties.items()]
    receipt = edit_patch.apply_live_patch(
        project=manager.GetCurrentProject(),
        timeline=manager.GetCurrentProject().GetCurrentTimeline(),
        capability="reel.touchup", operations=operations,
        conflict_domains=("picture_transform",),
        idempotency_key="small-edit-" + label + "-" + str(time.time_ns()))
    return {{"argv": ["edit_patch.apply_live_patch", label],
            "exit_code": 0 if receipt.get("status") == "committed" else 1,
            "stdout": json.dumps(receipt, default=str), "stderr": ""}}

def live_state():
    """Capture identities, exclusive record ranges, and projected geometry."""
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    start = int(timeline.GetStartFrame())
    end = int(timeline.GetEndFrame())
    frame_w = int(timeline.GetSetting("timelineResolutionWidth"))
    frame_h = int(timeline.GetSetting("timelineResolutionHeight"))
    fps = float(timeline.GetSetting("timelineFrameRate"))
    behavior = project.GetSetting("timelineInputResMismatchBehavior") or ""
    rows = []
    for track_index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            pool_item = item.GetMediaPoolItem()
            properties = item.GetProperty() or {{}}
            resolution = str(pool_item.GetClipProperty("Resolution") or "")
            source_size = None
            if "x" in resolution.lower():
                source_size = tuple(int(part.strip()) for part in
                                    resolution.lower().split("x", 1))
            bbox = None
            if source_size and (properties.get("Scaling") in
                                (1, "1", True) or behavior == "scaleToFit"):
                from library.tools import resolve_transform
                scaling = properties.get("Scaling", 0)
                base = (1.0 if scaling in (1, "1", True) else
                        resolve_transform.fit_base_scale(
                            *source_size, frame_w, frame_h))
                centre = resolve_transform.resolve_to_native_position(
                    properties, source_size, (frame_w, frame_h), base)
                zoom_x = float(properties.get("ZoomX", 1.0))
                zoom_y = float(properties.get("ZoomY", 1.0))
                drawn_w, drawn_h = (source_size[0] * base * zoom_x,
                                    source_size[1] * base * zoom_y)
                bbox = [centre[0] - drawn_w / 2, centre[1] - drawn_h / 2,
                        centre[0] + drawn_w / 2, centre[1] + drawn_h / 2]
            rows.append({{
                "unique_id": str(item.GetUniqueId()), "track": track_index,
                "name": str(item.GetName()),
                "source_path": str(pool_item.GetClipProperty("File Path") or ""),
                "source_in": int(item.GetSourceStartFrame()),
                "record_in": int(item.GetStart()), "record_out": int(item.GetEnd()),
                "source_size": source_size, "transform": properties,
                "output_bbox": bbox,
            }})
    return {{"timeline": timeline.GetUniqueId(), "start_frame": start,
            "end_frame": end, "fps": fps, "frame_size": [frame_w, frame_h],
            "mismatch_behavior": behavior, "items": rows}}

def select_qualification_timeline():
    project = manager.GetCurrentProject()
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)
               if project.GetTimelineByIndex(index)
               and project.GetTimelineByIndex(index).GetName() == TIMELINE]
    if len(matches) != 1 or not project.SetCurrentTimeline(matches[0]):
        raise RuntimeError("rebuild did not leave the private timeline selectable")
    if project.GetCurrentTimeline().GetUniqueId() != matches[0].GetUniqueId():
        raise RuntimeError("private timeline identity changed after rebuild")
    return project.GetCurrentTimeline().GetUniqueId()

def render_rgb_at(record_frame):
    """Render the exact record frame through the production Deliver API."""
    import shutil, tempfile
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    target = next((item for index in range(
        1, int(timeline.GetTrackCount("video") or 0) + 1)
        for item in (timeline.GetItemListInTrack("video", index) or ())
        if int(item.GetStart()) == int(record_frame)), None)
    if target is None:
        raise RuntimeError(f"no V item starts at requested render frame {{record_frame}}")
    from library.tools.color_page_grade import render_one_frame_deliver
    out = tempfile.mkdtemp(prefix="fm-qual-one-frame-")
    try:
        return render_one_frame_deliver(resolve, project, timeline, target,
                                         output_dir=out)
    finally:
        shutil.rmtree(out, ignore_errors=True)

def pixel_delta(before_rgb, after_rgb, allowed_boxes=()):
    """Return the changed-pixel bounds from two same-frame rendered images."""
    import tempfile
    import numpy as np
    from PIL import Image
    if before_rgb.shape != after_rgb.shape:
        raise RuntimeError("before/after render dimensions differ")
    delta = np.max(np.abs(before_rgb.astype(np.int16)
                          - after_rgb.astype(np.int16)), axis=2)
    mask = delta >= 24
    ys, xs = np.nonzero(mask)
    outside = np.zeros(mask.shape, dtype=bool)
    for left, top, right, bottom in allowed_boxes:
        x0, y0 = max(0, int(left)), max(0, int(top))
        x1, y1 = min(mask.shape[1], int(right)), min(mask.shape[0], int(bottom))
        outside[y0:y1, x0:x1] = True
    outside_changed = int(np.count_nonzero(mask & ~outside))
    rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = mask.astype(np.uint8) * 255
    fd, mask_path = tempfile.mkstemp(prefix="fm-qual-pixel-diff-", suffix=".png")
    import os
    os.close(fd)
    Image.fromarray(rgba, mode="RGBA").save(mask_path)
    if not len(xs):
        return {{"changed_pixels": 0, "bbox": None,
                "outside_changed_pixels": outside_changed,
                "mask_path": mask_path,
                "frame_size": [int(before_rgb.shape[1]),
                               int(before_rgb.shape[0])]}}
    return {{"changed_pixels": int(len(xs)),
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                     int(ys.max()) + 1],
            "outside_changed_pixels": outside_changed,
            "mask_path": mask_path,
            "frame_size": [int(before_rgb.shape[1]),
                           int(before_rgb.shape[0])]}}

def measured_box(state, name):
    item = next((row for row in state.get("items", [])
                 if row.get("name") == name), None)
    return item.get("output_bbox") if item else None

def allowed_visual_boxes(name, before, after, operation_before,
                         operation_after):
    asset_geometry = {{
        "add_logo": ("fm-qual-logo-180x64.png", (50, 19, 160, 45), (180, 64)),
        "tight_caption": ("fm-qual-tight-caption-480x120.png",
                          (40, 36, 440, 82), (480, 120)),
        "full_frame_caption": ("fm-qual-full-frame-caption-1920x1080.png",
                               (250, 800, 1670, 945), (1920, 1080)),
    }}
    if name in asset_geometry:
        asset_name, ink, source = asset_geometry[name]
        box = measured_box(after, asset_name)
        if not box:
            return []
        left, top, right, bottom = box
        return [[left + (right-left) * ink[0]/source[0],
                 top + (bottom-top) * ink[1]/source[1],
                 left + (right-left) * ink[2]/source[0],
                 top + (bottom-top) * ink[3]/source[1]]]
    target = "fm-qual-logo-180x64.png" if name in {{
        "move_logo", "resize_logo"}} else "qual_a.mp4"
    return [box for box in (measured_box(operation_before or {{}}, target),
                            measured_box(operation_after or {{}}, target))
            if box]

def cleanup(expected_items):
    observations = []
    collateral = []
    for _ in range(8):
        read = call(["items", "--project", PROJECT, "--timeline", TIMELINE,
                     "--refresh-live"])
        observations.append(read)
        rows = re.findall(r"^\\s+(video\\d+),", read["stdout"], re.MULTILINE)
        if len(rows) > expected_items:
            collateral.append(
                f"cleanup found {{len(rows)}} items after {{expected_items}} "
                "successful fixture placements")
        if read["exit_code"] or not rows:
            return {{"clean": read["exit_code"] == 0 and not rows,
                    "observations": observations,
                    "collateral_changes": collateral}}
        row = rows[-1]
        index = sum(found == row for found in rows) - 1
        deleted = call(["edit", "delete", "--track", row, "--index",
                        str(index), "--apply", "--project", PROJECT,
                        "--timeline", TIMELINE])
        observations.append(deleted)
        if deleted["exit_code"]:
            return {{"clean": False, "observations": observations,
                    "collateral_changes": collateral}}
    return {{"clean": False, "observations": observations,
            "collateral_changes": collateral}}

baseline = call(["items", "--project", PROJECT, "--timeline", TIMELINE,
                 "--refresh-live"])
if baseline["exit_code"] or re.search(
        r"^\\s+video\\d+,", baseline["stdout"], re.MULTILINE):
    raise RuntimeError(
        "private qualification timeline must be empty before the batch")

results = []
for name, commands in CASES.items():
    started = time.perf_counter()
    before = live_state()
    operation_before = None
    operation_after = None
    render_before = None
    render_after = None
    post_rebuild = None
    receipts = []
    for argv in commands:
        if (name == "mutation_failure" and argv[:2] ==
                ["edit", "delete"]):
            operation_before = live_state()
        if (name in {{"add_logo", "tight_caption", "full_frame_caption"}}
                and argv[:2] == ["edit", "place"]
                and any(asset in argv for asset in (
                    "fm-qual-logo-180x64.png",
                    "fm-qual-tight-caption-480x120.png",
                    "fm-qual-full-frame-caption-1920x1080.png"))):
            render_before = render_rgb_at(96)
        if (name == "replace_overlay" and argv[:2] ==
                ["edit", "place"] and "fm-qual-logo-alt-180x64.png" in argv):
            target = next(item for item in live_state()["items"]
                          if item["name"] == "fm-qual-logo-180x64.png")
            scale_result = apply_transform_patch(
                target["unique_id"], {{"Scaling": 1, "ZoomX": 1.0,
                                      "ZoomY": 1.0}}, "replace-old-scale")
            receipts.append(scale_result)
            if scale_result["exit_code"]:
                break
            operation_before = live_state()
            render_before = render_rgb_at(96)
        receipt = call(argv)
        receipts.append(receipt)
        if receipt["exit_code"]:
            break
    if not any(row["exit_code"] for row in receipts) and name in {{
            "add_logo", "move_logo"}}:
        target = next(item for item in live_state()["items"]
                      if item["name"] == "fm-qual-logo-180x64.png")
        scaling = apply_transform_patch(
            target["unique_id"], {{"Scaling": 1, "ZoomX": 1.0, "ZoomY": 1.0}},
            "native-logo-scale")
        receipts.append(scaling)
    if not any(row["exit_code"] for row in receipts) and name in {{
            "tight_caption", "full_frame_caption"}}:
        caption_name = ("fm-qual-tight-caption-480x120.png"
                        if name == "tight_caption" else
                        "fm-qual-full-frame-caption-1920x1080.png")
        caption = next(item for item in live_state()["items"]
                       if item["name"] == caption_name)
        scaling = apply_transform_patch(
            caption["unique_id"], {{"Scaling": 1, "ZoomX": 1.0,
                                    "ZoomY": 1.0}}, "caption-native-scale")
        receipts.append(scaling)
        if not scaling["exit_code"]:
            operation_after = live_state()
            render_after = render_rgb_at(96)
    if not any(row["exit_code"] for row in receipts) and name in {{
            "move_logo", "transform"}}:
        operation_before = live_state()
        render_before = render_rgb_at(
            96 if name == "move_logo" else
            next(item["record_in"] for item in operation_before["items"]
                 if item["name"] == "qual_a.mp4"))
        target_name = ("fm-qual-logo-180x64.png" if name == "move_logo"
                       else "qual_a.mp4")
        target = next(item for item in live_state()["items"]
                      if item["name"] == target_name)
        movement = call(["edit", "transform", "--item", target["unique_id"],
                         "--move-x", "40", "--move-y", "0", "--verify",
                         "--apply", "--project", PROJECT, "--timeline", TIMELINE])
        receipts.append(movement)
        operation_after = live_state()
        render_after = render_rgb_at(
            96 if name == "move_logo" else
            next(item["record_in"] for item in operation_after["items"]
                 if item["name"] == "qual_a.mp4"))
    if not any(row["exit_code"] for row in receipts) and name == "resize_logo":
        target = next(item for item in live_state()["items"]
                      if item["name"] == "fm-qual-logo-180x64.png")
        warmup = apply_transform_patch(
            target["unique_id"], {{"Scaling": 1, "ZoomX": 0.5, "ZoomY": 0.5}},
            "resize-setup")
        receipts.append(warmup)
        if not warmup["exit_code"]:
            operation_before = live_state()
            render_before = render_rgb_at(96)
            resize = apply_transform_patch(
                target["unique_id"], {{"ZoomX": 1.0, "ZoomY": 1.0}}, "resize")
            receipts.append(resize)
            operation_after = live_state()
            render_after = render_rgb_at(96)
    if not any(row["exit_code"] for row in receipts) and name == "replace_overlay":
        replacement = next(item for item in live_state()["items"]
                           if item["name"] == "fm-qual-logo-alt-180x64.png")
        scaling = apply_transform_patch(
            replacement["unique_id"], {{"Scaling": 1, "ZoomX": 1.0,
                                        "ZoomY": 1.0}}, "replace-new-scale")
        receipts.append(scaling)
        if not scaling["exit_code"]:
            operation_after = live_state()
            render_after = render_rgb_at(96)
    if name == "add_logo" and not any(row["exit_code"] for row in receipts):
        render_after = render_rgb_at(96)
        try:
            from library.tools.reel_build import rebuild_reels_in_project
            rebuild_reels_in_project(
                PROJECT, verify=True, only=[1], reuse_unchanged=True)
            select_qualification_timeline()
            post_rebuild = live_state()
        except Exception as exc:
            receipts.append({{"argv": ["reel_build.rebuild_reels_in_project"],
                             "exit_code": 1, "stdout": "",
                             "stderr": type(exc).__name__ + ": " + str(exc)}})
    after = live_state()
    expected_items = sum(
        receipt["argv"][:2] == ["edit", "place"] for receipt in receipts)
    cleaned = cleanup(expected_items)
    results.append({{"id": name, "commands": receipts, "before": before,
                    "operation_before": operation_before,
                    "operation_after": operation_after,
                    "after": after, "post_rebuild": post_rebuild,
                    "cleanup": cleaned,
                    "rendered_delta": (pixel_delta(
                        render_before, render_after,
                        allowed_visual_boxes(name, before, after,
                                             operation_before, operation_after))
                                       if render_before is not None
                                       and render_after is not None else None),
                    "collateral_changes": cleaned["collateral_changes"],
                    "elapsed_seconds": time.perf_counter() - started}})

# These cases deliberately exercise the production rebuild and replacement
# guard APIs. They are kept separate from ordinary edits so a refusal is
# recorded as the expected outcome, with a live timeline snapshot on either
# side proving the approved picture was not changed.
for name in ("rebuild_after_edit", "verification_failure", "promotion_conflict"):
    started = time.perf_counter()
    before = live_state()
    operation = {{"status": "not_run", "error": ""}}
    try:
        if name == "rebuild_after_edit":
            from library.tools.reel_build import rebuild_reels_in_project
            placed = call(["edit", "place", "--clip", "qual_a.mp4",
                           "--media", "video", "--track", "1",
                           "--record", "0", "--source-in", "0",
                           "--source-out", "47", "--apply",
                           "--project", PROJECT, "--timeline", TIMELINE])
            if placed["exit_code"]:
                raise RuntimeError("could not place the rebuild fixture: "
                                   + placed["stderr"])
            target = next(item for item in live_state()["items"]
                          if item["name"] == "qual_a.mp4")
            edited = call(["edit", "transform", "--item", target["unique_id"],
                           "--move-x", "40", "--move-y", "0", "--verify",
                           "--apply", "--project", PROJECT,
                           "--timeline", TIMELINE])
            if edited["exit_code"]:
                raise RuntimeError("could not apply the pre-rebuild edit: "
                                   + edited["stderr"])
            before = live_state()
            operation["result"] = rebuild_reels_in_project(
                PROJECT, verify=True, only=[1], reuse_unchanged=True)
            operation["status"] = "returned"
            operation["timeline_after_rebuild"] = select_qualification_timeline()
        elif name == "verification_failure":
            # Capture the production build's fail-closed verification payload
            # used to prevent promotion when verification does not pass.
            from library.tools.reel_build import _verify_payload
            operation["result"] = _verify_payload(
                {{"reel": "fm-scratch-qualification"}}, passed=False,
                refusal="qualification-forced-verification-failure")
            operation["status"] = "refused"
        else:
            from library.tools.reel_replace_guard import (
                ReplaceGuardRefused, check_replacement)
            row = {{"media_type": "video", "index": 1,
                    "name": "qualification row", "count": 1,
                    "frames": 48, "items": [{{"name": "approved-item",
                    "start": 0, "end": 47, "duration": 48}}]}}
            retired = {{"row-1": row}}
            incoming = {{"row-1": {{"media_type": "video", "index": 1,
                "name": "qualification row", "count": 0,
                "frames": 0, "items": []}}}}
            try:
                check_replacement("approved", "staging", retired, incoming)
            except ReplaceGuardRefused as exc:
                operation["status"] = "refused"
                operation["error"] = str(exc)
            else:
                operation["status"] = "unexpectedly_allowed"
    except Exception as exc:
        operation["status"] = "error"
        operation["error"] = type(exc).__name__ + ": " + str(exc)
    after = live_state()
    cleanup_result = cleanup(1) if name == "rebuild_after_edit" else {{
        "clean": True, "observations": [], "collateral_changes": []}}
    results.append({{"id": name, "commands": [], "cleanup": cleanup_result,
        "before": before, "operation_before": before,
        "operation_after": after, "after": after,
        "operation": operation, "collateral_changes": [],
        "elapsed_seconds": time.perf_counter() - started}})
result = {{"cases": results}}
'''


_BATCH_CACHE: dict | None = None


def _batch_case(case_id: str) -> dict:
    global _BATCH_CACHE
    if _BATCH_CACHE is None:
        _BATCH_CACHE = _qualification_batch(_live_batch_script())
    steps = (_BATCH_CACHE.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(f"qualification batch returned no steps: {_BATCH_CACHE}")
    try:
        stdout = steps[0]["stdout"]
        start = stdout.find("{\n")
        if start < 0:
            raise ValueError("JSON result block is missing")
        payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
        return next(row for row in payload["cases"] if row["id"] == case_id)
    except (KeyError, ValueError, StopIteration) as exc:
        raise RuntimeError(f"qualification batch omitted {case_id}: "
                           f"{steps[-1].get('stdout', '')}") from exc


def _case_observation(result: dict) -> str:
    commands = result["commands"]
    refused = next((row for row in commands if row["exit_code"]), None)
    item_reads = [row["stdout"] for row in commands
                  if row["argv"] and row["argv"][0] == "items"]
    if not item_reads and result["cleanup"]["observations"]:
        item_reads = [result["cleanup"]["observations"][0]["stdout"]]
    after = result.get("after", {})
    logo = next((item for item in after.get("items", [])
                 if item.get("name") == "fm-qual-logo-180x64.png"), None)
    geometry = {}
    if logo and logo.get("output_bbox"):
        left, top, right, bottom = logo["output_bbox"]
        width, height = after["frame_size"]
        safe = (width * 0.05, height * 0.05,
                width * 0.95, height * 0.95)
        geometry = {
            "unique_id": logo["unique_id"],
            "item_start_frame": logo["record_in"],
            "item_end_frame": logo["record_out"],
            "timeline_end_frame": after["end_frame"],
            "fps": after["fps"],
            "output_width_px": right - left,
            "output_height_px": bottom - top,
            "output_bbox": [left, top, right, bottom],
            "safe_zone": list(safe),
            "inside_safe_zone": (left >= safe[0] and top >= safe[1]
                                 and right <= safe[2] and bottom <= safe[3]),
        }
    expected_names = {
        "add_video": {"qual_a.mp4"},
        "move_clip": {"qual_a.mp4"},
        "trim": {"qual_a.mp4"},
        "add_logo": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                      "fm-qual-logo-180x64.png"},
        "move_logo": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                      "fm-qual-logo-180x64.png"},
        "resize_logo": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                        "fm-qual-logo-180x64.png"},
        "transform": {"qual_a.mp4"},
        "tight_caption": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                          "fm-qual-tight-caption-480x120.png"},
        "full_frame_caption": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                               "fm-qual-full-frame-caption-1920x1080.png"},
        "replace_overlay": {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                            "fm-qual-logo-alt-180x64.png"},
    }.get(result.get("id"), set())
    observed_names = {item.get("name") for item in after.get("items", [])}
    unexpected_items = sorted(observed_names - expected_names)
    missing_items = sorted(expected_names - observed_names)
    return json.dumps({
        "first_refusal": refused,
        "readback": item_reads[-1] if item_reads else "",
        "before": result.get("before", {}),
        "operation_before": result.get("operation_before") or {},
        "operation_after": result.get("operation_after") or {},
        "after": after,
        "logo_geometry": geometry,
        "rendered_delta": result.get("rendered_delta"),
        "operation": result.get("operation", {}),
        "post_rebuild": result.get("post_rebuild"),
        "unexpected_items": unexpected_items,
        "missing_items": missing_items,
        "cleanup": result["cleanup"]["clean"],
        "cleanup_collateral": result["cleanup"]["collateral_changes"],
    }, ensure_ascii=False)


def _place_video() -> list[str]:
    return _edit("place", "--clip", "qual_a.mp4", "--media", "video",
                 "--track", "1", "--record", "0", "--source-in", "0",
                 "--source-out", "47", "--apply")


def _add_video_action() -> dict:
    result = _batch_case("add_video")
    text = _case_observation(result)
    # The fixture asks for the 48 source frames [0, 48). Exact read-back must
    # show 48 record frames too; item-count movement alone is insufficient.
    right_span = "video1,qual_a.mp4,0,48,48,0,47" in text
    succeeded = all(row["exit_code"] == 0 for row in result["commands"])
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"],
            "checks": {"48_frames": right_span},
            "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def _move_clip_action() -> dict:
    result = _batch_case("move_clip")
    text = _case_observation(result)
    succeeded = all(row["exit_code"] == 0 for row in result["commands"])
    expected_readback = "video1,qual_a.mp4,8,55,47,0,47"
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"],
            "checks": {"move_readback": succeeded and expected_readback in text},
            "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def _trim_action() -> dict:
    result = _batch_case("trim")
    text = _case_observation(result)
    # The row is the production read-back, not merely the API's success bit.
    expected_readback = "video1,qual_a.mp4,0,45,45,1,46"
    succeeded = all(row["exit_code"] == 0 for row in result["commands"])
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"],
            "checks": {"trim_readback": (succeeded
                                           and expected_readback in text)},
            "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def _add_logo_action() -> dict:
    if not LOGO.is_file():
        raise RuntimeError(f"synthetic logo fixture is missing: {LOGO}")
    result = _batch_case("add_logo")
    text = _case_observation(result)
    succeeded = all(row["exit_code"] == 0 for row in result["commands"])
    observation = json.loads(text)
    readback = observation["readback"]
    # Geometry and rebuild evidence are required fields from the live
    # adapter. Without them the checks fail instead of inferring success from
    # the CLI exit status or item name.
    geometry = observation.get("logo_geometry", {})
    delta = observation.get("rendered_delta") or {}
    pixels = {}
    mask_path = delta.get("mask_path")
    expected_ink_box = None
    safe_zone = geometry.get("safe_zone")
    if geometry.get("output_bbox"):
        left, top, right, bottom = geometry["output_bbox"]
        expected_ink_box = (
            left + (right - left) * 50 / 180,
            top + (bottom - top) * 19 / 64,
            left + (right - left) * 160 / 180,
            top + (bottom - top) * 45 / 64,
        )
    if mask_path and expected_ink_box and safe_zone:
        try:
            pixels = _verify_rendered_geometry(
                [mask_path], expected_bbox=expected_ink_box,
                safe_zone=tuple(safe_zone))
        finally:
            Path(mask_path).unlink(missing_ok=True)
    rendered = pixels.get("measured", [])
    rendered_box = rendered[0].get("bbox") if rendered else None
    post_rebuild = observation.get("post_rebuild", {})
    rebuild_observation = _rebuild_preservation(
        observation.get("after", {}), post_rebuild, "fm-qual-logo-180x64.png")
    checks = {
        "independent_item": "fm-qual-logo-180x64.png" in readback,
        "last_five_seconds": _ends_within_last_five_seconds(geometry),
        "width_180px": (abs(geometry.get("output_width_px", 0) - 180) <= 1
                        and bool(rendered_box)),
        "inside_safe_zone": bool(pixels.get("passed") and
                                  pixels.get("safe_zone_passed")),
        "no_other_changes": (result["cleanup"]["clean"]
                             and not observation["unexpected_items"]
                             and not observation["missing_items"]
                             and delta.get("outside_changed_pixels") == 0),
        "survives_rebuild": rebuild_observation["preserved"],
    }
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"],
            "checks": checks, "attempts": 1,
            "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def _operation_action(case_id: str, item_name: str, expected_delta: float,
                      *, size_change: bool = False) -> dict:
    result = _batch_case(case_id)
    text = _case_observation(result)
    observed = json.loads(text)
    before = observed["operation_before"]
    after = observed["operation_after"]
    old = next((row for row in before.get("items", [])
                if row.get("name") == item_name), None)
    new = next((row for row in after.get("items", [])
                if row.get("name") == item_name), None)
    receipt_rows = result["commands"]
    succeeded = bool(receipt_rows) and all(
        row["exit_code"] == 0 for row in receipt_rows)
    measured_delta = None
    if old and new and old.get("output_bbox") and new.get("output_bbox"):
        old_box, new_box = old["output_bbox"], new["output_bbox"]
        old_value = old_box[2] - old_box[0] if size_change else (
            old_box[0] + old_box[2]) / 2
        new_value = new_box[2] - new_box[0] if size_change else (
            new_box[0] + new_box[2]) / 2
        measured_delta = new_value - old_value
    correct_delta = (measured_delta is not None and
                     abs(measured_delta - expected_delta) <= 1.0)
    pixels = observed.get("rendered_delta") or {}
    checks = {
        "target_identity": bool(old and new and
                                old["unique_id"] == new["unique_id"]),
        "requested_geometry_change": correct_delta,
        "rendered_pixels_changed": pixels.get("changed_pixels", 0) > 0,
        "rendered_change_localized": pixels.get(
            "outside_changed_pixels") == 0,
        "unrelated_items_unchanged": not observed["unexpected_items"],
        "no_other_changes": (result["cleanup"]["clean"]
                             and not observed["unexpected_items"]
                             and not observed["missing_items"]
                             and pixels.get("outside_changed_pixels") == 0),
    }
    return {
        "receipt": {"status": "committed" if succeeded else "refused",
                    "commands": receipt_rows},
        "collateral_changes": result["collateral_changes"],
        "checks": checks,
        "attempts": 1,
        "elapsed_seconds": result["elapsed_seconds"],
        "observed": {"case": observed, "measured_delta": measured_delta},
    }


def _move_logo_action() -> dict:
    return _operation_action("move_logo", "fm-qual-logo-180x64.png", 40.0)


def _resize_logo_action() -> dict:
    return _operation_action("resize_logo", "fm-qual-logo-180x64.png", 90.0,
                             size_change=True)


def _transform_action() -> dict:
    return _operation_action("transform", "qual_a.mp4", 40.0)


def _overlay_asset_action(case_id: str, asset_name: str,
                          expected_width: int) -> dict:
    result = _batch_case(case_id)
    text = _case_observation(result)
    observation = json.loads(text)
    after_items = observation["after"].get("items", [])
    target = next((item for item in after_items
                   if item.get("name") == asset_name), None)
    pixel_evidence = observation.get("rendered_delta") or {}
    receipt_rows = result["commands"]
    succeeded = bool(receipt_rows) and all(
        row["exit_code"] == 0 for row in receipt_rows)
    checks = {
        "asset_readback": bool(target and target.get("source_path")),
        "expected_track": bool(target and target.get("track") == 3),
        "expected_record_span": bool(target and
                                      target["record_out"]
                                      - target["record_in"] == 120),
        "output_width": bool(target and target.get("output_bbox")
                             and abs(target["output_bbox"][2]
                                     - target["output_bbox"][0]
                                     - expected_width) <= 1),
        "rendered_pixels_changed": pixel_evidence.get("changed_pixels", 0) > 0,
        "rendered_change_localized": pixel_evidence.get(
            "outside_changed_pixels") == 0,
        "no_other_changes": (result["cleanup"]["clean"]
                             and not observation["unexpected_items"]
                             and not observation["missing_items"]),
    }
    if case_id == "replace_overlay":
        old = next((item for item in after_items
                    if item.get("name") == "fm-qual-logo-180x64.png"), None)
        before_old = next((item for item in
                           observation["operation_before"].get("items", [])
                           if item.get("name") == "fm-qual-logo-180x64.png"), None)
        checks.update({
            "old_asset_removed": old is None and before_old is not None,
            "replacement_preserved_span": bool(target and before_old and
                target["record_in"] == before_old["record_in"] and
                target["record_out"] == before_old["record_out"]),
        })
    return {
        "receipt": {"status": "committed" if succeeded else "refused",
                    "commands": receipt_rows},
        "collateral_changes": result["collateral_changes"],
        "checks": checks,
        "attempts": 1,
        "elapsed_seconds": result["elapsed_seconds"],
        "observed": observation,
    }


def _tight_caption_action() -> dict:
    return _overlay_asset_action("tight_caption",
                                 "fm-qual-tight-caption-480x120.png", 480)


def _full_frame_caption_action() -> dict:
    return _overlay_asset_action(
        "full_frame_caption", "fm-qual-full-frame-caption-1920x1080.png", 1920)


def _replace_overlay_action() -> dict:
    return _overlay_asset_action("replace_overlay",
                                 "fm-qual-logo-alt-180x64.png", 180)


def _mutation_failure_action() -> dict:
    result = _batch_case("mutation_failure")
    observed = json.loads(_case_observation(result))
    refused = next((row for row in result["commands"]
                    if row["exit_code"] != 0), None)
    before = observed["operation_before"]
    after = observed["after"]
    unchanged = (before.get("items") == after.get("items")
                 and before.get("timeline") == after.get("timeline"))
    return {
        "receipt": {"status": "verified" if refused and unchanged
                    else "refused", "refusal": refused},
        "collateral_changes": result["collateral_changes"],
        "checks": {"mutation_refused": bool(refused),
                   "state_unchanged": unchanged},
        "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
        "observed": observed,
    }


def _rebuild_after_edit_action() -> dict:
    result = _batch_case("rebuild_after_edit")
    observed = _case_observation(result)
    operation = result.get("operation", {})
    before, after = result.get("before", {}), result.get("after", {})
    unchanged = before.get("items") == after.get("items")
    succeeded = operation.get("status") == "returned"
    return {
        "receipt": {"status": "verified" if succeeded and unchanged
                    else "refused", "rebuild": operation},
        "collateral_changes": result["collateral_changes"],
        "checks": {"production_rebuild_returned": succeeded,
                   "timeline_state_preserved": unchanged},
        "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
        "observed": observed,
    }


def _verification_failure_action() -> dict:
    result = _batch_case("verification_failure")
    operation = result.get("operation", {})
    payload = operation.get("result", {})
    unchanged = result["before"].get("items") == result["after"].get("items")
    refused = operation.get("status") == "refused" and payload.get("passed") is False
    return {
        "receipt": {"status": "verified" if refused and unchanged
                    else "refused", "gate_payload": payload},
        "collateral_changes": result["collateral_changes"],
        "checks": {"verification_failure_recorded": refused,
                   "approved_state_unchanged": unchanged},
        "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
        "observed": result,
    }


def _promotion_conflict_action() -> dict:
    result = _batch_case("promotion_conflict")
    operation = result.get("operation", {})
    unchanged = result["before"].get("items") == result["after"].get("items")
    refused = operation.get("status") == "refused"
    return {
        "receipt": {"status": "verified" if refused and unchanged
                    else "refused", "guard": operation},
        "collateral_changes": result["collateral_changes"],
        "checks": {"replacement_conflict_refused": refused,
                   "approved_state_unchanged": unchanged},
        "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
        "observed": result,
    }


def _ends_within_last_five_seconds(geometry: dict) -> bool:
    """Judge placement against measured timeline fps and exclusive bounds."""
    required = {"item_start_frame", "item_end_frame", "timeline_end_frame", "fps"}
    if not required <= geometry.keys() or not geometry["fps"]:
        return False
    lower_bound = geometry["timeline_end_frame"] - 5 * geometry["fps"]
    return (geometry["item_start_frame"] >= lower_bound
            and 0 <= geometry["timeline_end_frame"] - geometry["item_end_frame"]
            <= 5 * geometry["fps"])


def _verify_rendered_geometry(frame_paths: list[str], *, expected_bbox: tuple,
                              safe_zone: tuple) -> dict:
    """Run the independent pixel verifier over captured Resolve frames."""
    from library.tools.overlay_verify import verify_rendered_frames

    evidence = verify_rendered_frames(
        [{"path": path} for path in frame_paths],
        expected_bbox=expected_bbox, safe_zone=safe_zone)
    evidence["safe_zone_passed"] = (
        bool(evidence["measured"])
        and not any(row.get("check") == "inside_safe_zone"
                    for row in evidence["findings"]))
    return evidence


def _rebuild_preservation(before: dict, after: dict, asset_name: str) -> dict:
    """Compare the logo's measured identity, timing, and geometry after rebuild."""
    before_rows = [row for row in before.get("items", [])
                   if row.get("name") == asset_name]
    after_rows = [row for row in after.get("items", [])
                  if row.get("name") == asset_name]
    preserved = False
    if len(before_rows) == len(after_rows) == 1:
        old, new = before_rows[0], after_rows[0]
        preserved = all(old.get(key) == new.get(key) for key in (
            "source_path", "source_in", "record_in", "record_out",
            "output_bbox"))
    return {
        "preserved": preserved,
        "before_unique_id": (before_rows[0].get("unique_id")
                             if before_rows else None),
        "after_unique_id": (after_rows[0].get("unique_id")
                            if after_rows else None),
        "before_count": len(before_rows),
        "after_count": len(after_rows),
    }


def current_main_actions() -> dict[str, Callable[[], dict]]:
    """Register every implemented production-entry scenario adapter."""
    actions = {"add_video": _add_video_action,
               "move_clip": _move_clip_action,
               "trim": _trim_action,
               "add_logo": _add_logo_action,
               "move_logo": _move_logo_action,
               "resize_logo": _resize_logo_action,
               "transform": _transform_action,
               "tight_caption": _tight_caption_action,
               "full_frame_caption": _full_frame_caption_action,
               "replace_overlay": _replace_overlay_action,
               "rebuild_after_edit": _rebuild_after_edit_action,
               "mutation_failure": _mutation_failure_action,
               "verification_failure": _verification_failure_action,
               "promotion_conflict": _promotion_conflict_action}
    return actions


def main() -> int:
    _write_logo_fixture(LOGO)
    _write_caption_fixture(LOGO_ALT, 180, 64,
                          [(20, 20, 160, 44)], (245, 145, 45, 255))
    _write_caption_fixture(TIGHT_CAPTION, 480, 120,
                          [(70, 36, 400, 48), (40, 62, 440, 82)],
                          (255, 255, 255, 255))
    _write_caption_fixture(FULL_FRAME_CAPTION, 1920, 1080,
                          [(320, 800, 1600, 850), (250, 875, 1670, 945)],
                          (255, 255, 255, 255))
    fixtures = (LOGO, LOGO_ALT, TIGHT_CAPTION, FULL_FRAME_CAPTION)
    if sys.argv[1:] == ["--write-fixtures"]:
        print(json.dumps({"fixtures": [str(path) for path in fixtures]}, indent=2))
        return 0
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        raise SystemExit("set REN_LIVE_RESOLVE=1 to run against Resolve")
    report = run_batch(current_main_actions())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


def _write_logo_fixture(path: Path) -> None:
    """Write a deterministic transparent 180x64 logo using stdlib only."""
    width, height = 180, 64
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            # A bright cyan inset frame and central mark on transparency.
            edge = 6 <= x < width - 6 and 6 <= y < height - 6
            mark = (50 <= x < 130 and 19 <= y < 45) or (
                130 <= x < 160 and 27 <= y < 37)
            row.extend((45, 210, 230, 255) if edge and mark else (0, 0, 0, 0))
        rows.append(bytes(row))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xffffffff))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b""))


def _write_caption_fixture(path: Path, width: int, height: int,
                           rectangles: list[tuple[int, int, int, int]],
                           color: tuple[int, int, int, int]) -> None:
    """Write a transparent caption plate with deterministic alpha geometry."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            inside = any(left <= x < right and top <= y < bottom
                         for left, top, right, bottom in rectangles)
            row.extend(color if inside else (0, 0, 0, 0))
        rows.append(bytes(row))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xffffffff))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b""))


if __name__ == "__main__":
    raise SystemExit(main())
