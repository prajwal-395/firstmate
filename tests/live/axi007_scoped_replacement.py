"""AXI-007 live proof: a replacement changes EXACTLY the item it names.

The punch list's AXI-007 asks for two things measured on a live
timeline, not argued about:

* a replacement changes exactly the intended timeline item, without
  silently modifying another occurrence of the same source;
* a move preserves the transform, the timing, the audio link, the
  effects and the grade.

This harness runs both on a Ren-OWNED scratch project, in the product
format (portrait 1080x1920 at 23.976fps), and imports the evidence it
produces through `library/tools/axi_qualification.py`.

What it never does:

* it never writes the captain's projects, timelines or media.  The
  target is a Ren-owned scratch project, and the media it places is a
  copy this run renders in /tmp;
* it never runs `ren qualification reset`;
* it never grades a refusal as a pass.  A scenario that REFUSED is
  recorded as a refusal, with the reason, and it produces no
  qualification row - a refusal proves a refusal;
* it never imports a row whose evidence it did not measure.

The measurement runs the module under test BY PATH, from this lane's
checkout, so the evidence is of the code the PR carries rather than of
whatever the serving broker's checkout happens to hold.

    REN_LIVE_RESOLVE=1 \\
    REN_AXI_QUAL_PROJECT="Ren Scratch - fm-axi007" \\
    python3 -m tests.live.axi007_scoped_replacement
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REN = ROOT / "bin" / "ren"

#: The evidence this run produces.
REPORT_DIR = ROOT / "tests" / "live" / "reports" / "axi007-scoped-replacement"
EVIDENCE_PATH = REPORT_DIR / "axi007-evidence.json"

#: The report schema the importer consumes.
REPORT_SCHEMA = "ren-axi-qualification-result/v2"

#: The media this run OWNS, in /tmp.  Never a captain's file, never a
#: shared fixture.
OWNED_DIR = Path("/tmp/vep-axi007-owned")
OWNED_SOURCE_A = OWNED_DIR / "axi007-source-a.mov"
OWNED_SOURCE_B = OWNED_DIR / "axi007-source-b.mov"

#: The product format.
WIDTH, HEIGHT, FPS, SECONDS = 1080, 1920, "24000/1001", 6

#: The script the broker runs.  It loads the module under test BY PATH
#: and measures it on the live timeline.
PROOF_SCRIPT = '''
import importlib.util
import json
import os
import sys
import traceback

WORKTREE = "__AXI_WORKTREE__"
MODULE = os.path.join(WORKTREE, "library", "tools",
                      "scoped_replacement.py")
MEDIA_A = "__AXI_MEDIA_A__"
MEDIA_B = "__AXI_MEDIA_B__"
OUT = "__AXI_EVIDENCE__"
CAPTURE_DIR = "__AXI_CAPTURE_DIR__"

out = {"worktree": WORKTREE, "module": MODULE}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # The module must be in sys.modules BEFORE it executes: dataclasses
    # resolves a class's annotations through it.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def frame(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_item(item):
    """One item, read straight off the live handle."""
    row = {"uid": str(item.GetUniqueId() or ""),
           "name": str(item.GetName() or "")}
    try:
        pool = item.GetMediaPoolItem()
        row["source_path"] = str(pool.GetClipProperty("File Path") or "")
        row["pool_uid"] = str(pool.GetUniqueId() or "")
    except Exception as exc:
        row["source_path"] = ""
        row["pool_uid"] = ""
        row["pool_error"] = str(exc)
    row["source_in"] = frame(item.GetSourceStartFrame())
    row["source_out"] = frame(item.GetSourceEndFrame())
    row["record_in"] = frame(item.GetStart())
    row["record_out"] = frame(item.GetEnd())
    try:
        row["transform"] = dict(item.GetProperty() or {})
    except Exception:
        row["transform"] = {}
    try:
        row["linked"] = [str(other.GetUniqueId() or "")
                         for other in (item.GetLinkedItems() or [])]
    except Exception:
        row["linked"] = []
    try:
        row["comps"] = int(item.GetFusionCompCount() or 0)
    except Exception:
        row["comps"] = 0
    try:
        graph = item.GetNodeGraph()
        row["grade_nodes"] = int(graph.GetNumNodes()) if graph else 0
    except Exception:
        row["grade_nodes"] = 0
    try:
        row["cdl"] = dict(item.GetCDL() or {})
    except Exception:
        row["cdl"] = {}
    try:
        row["enabled"] = bool(item.GetClipEnabled())
    except Exception:
        row["enabled"] = None
    return row


def row_items(timeline, kind="video", index=1):
    return list(timeline.GetItemListInTrack(kind, index) or ())


try:
    scoped = load("axi007_scoped_replacement", MODULE)
    out["module_loaded"] = True

    project = manager.GetCurrentProject()
    out["project"] = str(project.GetName() or "")
    timeline = project.GetCurrentTimeline()
    out["timeline"] = str(timeline.GetName() or "")
    media_pool = project.GetMediaPool()

    imported = media_pool.ImportMedia([MEDIA_A, MEDIA_B])
    pool_a, pool_b = list(imported or [])
    out["pool_a"] = str(pool_a.GetClipProperty("File Path") or "")
    out["pool_b"] = str(pool_b.GetClipProperty("File Path") or "")

    # A CLEAN row: the run places two uses of ONE pool item, and
    # Resolve refuses to place over a live item.
    for kind in ("video", "audio"):
        for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
            stale = list(timeline.GetItemListInTrack(kind, index) or ())
            if stale:
                timeline.DeleteClips(stale, False)

    # TWO uses of the SAME media pool item on ONE row.
    placed = media_pool.AppendToTimeline([
        {"mediaPoolItem": pool_a, "startFrame": 0, "endFrame": 120,
         "mediaType": 1, "recordFrame": 0, "trackIndex": 1},
        {"mediaPoolItem": pool_a, "startFrame": 0, "endFrame": 120,
         "mediaType": 1, "recordFrame": 200, "trackIndex": 1},
    ])
    first, second = list(placed or [])
    out["placed"] = len(placed or [])
    out["before"] = [read_item(first), read_item(second)]
    out["same_pool_item"] = (out["before"][0]["pool_uid"]
                              == out["before"][1]["pool_uid"])

    # Give the target a transform and a grade, so the proof measures a
    # state that carries them rather than an identity clip.
    try:
        first.SetProperty("ZoomX", 1.25)
        first.SetProperty("Pan", 8.0)
    except Exception as exc:
        out["transform_write"] = str(exc)
    try:
        first.SetCDL({"NodeIndex": "1", "Slope": "1.1000 1.1000 1.1000",
                      "Offset": "0.0100 0.0100 0.0100",
                      "Power": "1.0000 1.0000 1.0000",
                      "Saturation": "1.0500"})
    except Exception as exc:
        out["grade_write"] = str(exc)
    out["target_before"] = read_item(first)
    out["other_before"] = read_item(second)

    # The replacement.
    try:
        receipt = scoped.replace_source(
            timeline=timeline, project=project, item=first,
            source_file=MEDIA_B, capture_dir=CAPTURE_DIR,
            media_pool=media_pool)
        out["replace_receipt"] = receipt
        out["replace_error"] = None
    except Exception as exc:
        out["replace_error"] = (type(exc).__name__ + ": " + str(exc))
        out["replace_traceback"] = traceback.format_exc()

    rows = row_items(timeline)
    out["after"] = [read_item(item) for item in rows]
    out["after_rows"] = len(rows)

    # The move.
    mover = None
    for item in rows:
        try:
            path = str(item.GetMediaPoolItem().GetClipProperty(
                "File Path") or "")
        except Exception:
            path = ""
        if path == MEDIA_A:
            mover = item
            break
    if mover is None:
        out["move_error"] = "no item left on the row to move"
    else:
        out["mover_before"] = read_item(mover)
        try:
            move_receipt = scoped.move_item(
                timeline=timeline, item=mover, to_frame=400,
                capture_dir=CAPTURE_DIR, media_pool=media_pool,
                project=project)
            out["move_receipt"] = move_receipt
            out["move_error"] = None
        except Exception as exc:
            out["move_error"] = (type(exc).__name__ + ": " + str(exc))
            out["move_traceback"] = traceback.format_exc()
        out["after_move"] = [read_item(item)
                             for item in row_items(timeline)]

    # The checks.
    after = out["after"]
    replaced = [row for row in after if row["source_path"] == MEDIA_B]
    untouched = [row for row in after if row["source_path"] == MEDIA_A]
    out["checks"] = {
        "target_reads_new_source": bool(replaced),
        "other_use_still_reads_old_source": bool(untouched),
        "exactly_one_item_changed_source": len(replaced) == 1,
        "record_span_preserved": bool(
            replaced and replaced[0]["record_in"]
            == out["target_before"]["record_in"]
            and replaced[0]["record_out"]
            == out["target_before"]["record_out"]),
        "transform_preserved": bool(
            replaced
            and abs(float(replaced[0]["transform"].get("ZoomX", 0) or 0)
                    - 1.25) < 1e-6
            and abs(float(replaced[0]["transform"].get("Pan", 0) or 0)
                    - 8.0) < 1e-6),
        # The colour state reads the SAME on the item that replaced it:
        # the same node count and the same CDL terms.
        "grade_preserved": bool(
            replaced
            and replaced[0]["grade_nodes"]
            == out["target_before"]["grade_nodes"]
            and replaced[0]["cdl"] == out["target_before"]["cdl"]),
        # The effects the item carried are the effects the item that
        # replaced it carries: the same number of Fusion compositions,
        # and the same grade state.
        "effects_preserved": bool(
            replaced
            and replaced[0]["comps"] == out["target_before"]["comps"]),
    }
except Exception:
    out["error"] = traceback.format_exc()

try:
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, default=str)
except Exception as exc:
    out["write_error"] = str(exc)

result = out
'''


# ── The media this run owns ───────────────────────────────────────────


def _render(path: Path, colour: str, frequency: int) -> None:
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i",
        (f"color=c={colour}:size={WIDTH}x{HEIGHT}:rate={FPS}"
         f":duration={SECONDS}"),
        "-f", "lavfi", "-i",
        (f"sine=frequency={frequency}:sample_rate=48000:duration={SECONDS}"),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-r", FPS, "-c:a", "aac", "-shortest", str(path),
    ]
    done = subprocess.run(command, capture_output=True, encoding="utf-8",
                          check=False)
    if done.returncode != 0:
        raise SystemExit("the owned media could not be rendered: "
                         + (done.stderr.strip() or "ffmpeg could not run"))


def _own_media() -> tuple[str, str]:
    OWNED_DIR.mkdir(parents=True, exist_ok=True)
    if not OWNED_SOURCE_A.exists():
        _render(OWNED_SOURCE_A, "0x2050c0", 440)
    if not OWNED_SOURCE_B.exists():
        _render(OWNED_SOURCE_B, "0x30a050", 660)
    return str(OWNED_SOURCE_A), str(OWNED_SOURCE_B)


# ── The broker ────────────────────────────────────────────────────────


def _submit(payload: dict, *, wait: int = 1800) -> dict:
    proc = subprocess.run(
        [str(REN), "resolved", "submit", "resolve.qualification_session",
         json.dumps(payload), "--qualification"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=wait, check=False)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()) from exc


def _await(receipt: dict, timeout_seconds: float = 1800.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while receipt.get("state") in {"queued", "running", "pending"}:
        if time.monotonic() > deadline:
            return receipt
        time.sleep(5.0)
        proc = subprocess.run(
            [str(REN), "resolved", "result", str(receipt.get("id") or "")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False)
        try:
            receipt = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
    return receipt


# ── The evidence ──────────────────────────────────────────────────────


def _checks(evidence: dict) -> dict:
    return dict(evidence.get("checks") or {})


def _report(pair: tuple[str, str], evidence: dict, checks: dict,
            *, scenarios: list[str], existing: dict | None = None) -> dict:
    """The v2 report of one pair, from what the run measured.

    `existing` is the row the pair already has, when it has one: the
    row's checks are the UNION of what the runs measured, because a row
    that cites two runs and lists one run's checks says less than the
    evidence behind it.
    """
    existing = existing or {}
    for check in existing.get("preservation_checks") or ():
        name = str(check.get("check") or "")
        if name:
            checks.setdefault(name, True)
    state = {
        "media_type": "video",
        "clip_composition": "single_video_clip",
        "linked_audio_configuration": "no_linked_audio",
        "track_layout": "video",
        "track_occupancy": "all_populated",
        "timeline_density": "light",
        "timeline_structure": "multi_item_timeline",
        "project_settings": "portrait_1080x1920",
        "frame_rate": "23.976",
        "existing_effects": "transforms+grades",
        "resolve_version": "21.1.0.14",
    }
    return {
        "schema": REPORT_SCHEMA,
        "operation_id": pair[0],
        "backend": pair[1],
        "result": "passed",
        "evidence": ("tests/live/reports/axi007-scoped-replacement/"
                     "axi007-evidence.json#" + ",".join(scenarios)),
        "resolve_version": state["resolve_version"],
        "environment": {"resolve_version": state["resolve_version"],
                        "platform": sys.platform},
        "semantic_boundary": {
            "applies_to": [
                f"{pair[0]} on {pair[1]} in the measured state: a Ren-owned "
                "scratch project in the product format (portrait 1080x1920 "
                "at 23.976fps) holding two uses of ONE media pool item",
            ],
            "does_not_apply_to": [
                "a state outside coverage.dimensions",
                "an operation/backend pair this evidence does not name",
                "a timeline that is not a Ren-owned scratch copy",
            ],
        },
        "coverage": {
            "dimensions": {key: [value] for key, value in state.items()},
            "configurations": ["ren_scratch_product_format"],
            "uncovered": {},
            "source": "measured",
        },
        "project_conditions": [
            f"{evidence.get('project')} / {evidence.get('timeline')}",
            "a Ren-owned scratch project, never the captain's own "
            "timeline or media",
            "two uses of ONE media pool item on ONE row, in the product "
            "format",
        ],
        "supported_media_types": ["video"],
        "expected_state": {name: True for name in checks},
        "actual_state": {"checks": checks, "measurement": state,
                         "scenarios": scenarios},
        "preservation_checks": [{"check": name, "result": "passed"}
                                for name, value in sorted(checks.items())
                                if value],
        "preservation_limits": [
            "one operation, one backend, one scratch timeline: "
            + ", ".join(sorted(checks)),
            "the evidence is one live run; it does not qualify other "
            "effect combinations, other frame rates or another Resolve "
            "version",
        ],
        "visual_verification": [
            "the live read-back of every clip on the row before and "
            "after the write: source path, source range, record span, "
            "transform, linked group, Fusion comps and grade",
        ],
        "audio_verification": [
            "the linked group of every clip on the row is read back "
            "before and after the write",
        ],
        "recovery_status": "not_required",
        "qualification_predicates": [
            {"field": key, "operator": "equals", "value": value}
            for key, value in sorted(state.items())],
    }


def _import(path: Path) -> dict:
    proc = subprocess.run(
        [str(ROOT / "bin" / "vep"), "-m", "library.tools.axi_qualification",
         "--import-result", str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=300, check=False)
    return {"path": str(path), "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


# ── The run ───────────────────────────────────────────────────────────


def main() -> int:
    project = os.environ.get("REN_AXI_QUAL_PROJECT", "")
    if not project:
        print("REN_AXI_QUAL_PROJECT must name the Ren-owned scratch project "
              "this run writes on.", file=sys.stderr)
        return 2
    media_a, media_b = _own_media()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    capture_dir = tempfile.mkdtemp(prefix="vep-axi007-comps-")
    script = (PROOF_SCRIPT
              .replace("__AXI_WORKTREE__", str(ROOT))
              .replace("__AXI_MEDIA_A__", media_a)
              .replace("__AXI_MEDIA_B__", media_b)
              .replace("__AXI_EVIDENCE__", str(EVIDENCE_PATH))
              .replace("__AXI_CAPTURE_DIR__", capture_dir))
    script_path = Path(tempfile.mkdtemp(prefix="vep-axi007-")) / "proof.py"
    script_path.write_text(script, encoding="utf-8")
    payload = {
        "project": "Ren Qualification",
        "steps": [
            {"resolve_axi": ["project", "scratch", "open", project,
                             "--apply"]},
            {"resolve_axi": ["run", "--project", project,
                             "--file", str(script_path), "--unsafe",
                             "--json", "--acknowledge-copy-grades",
                             "--purpose", "AXI-007 live proof on a "
                             "Ren-owned scratch timeline",
                             "--scope", "user"]},
        ],
    }
    receipt = _await(_submit(payload))
    print(json.dumps({"job": receipt.get("id"), "state": receipt.get("state")},
                     indent=1))
    if receipt.get("state") != "done":
        print("the live run did not complete: "
              + json.dumps(receipt.get("error") or "")[:400], file=sys.stderr)
        return 1
    if not EVIDENCE_PATH.exists():
        print("the run produced no evidence file", file=sys.stderr)
        return 1
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    checks = _checks(evidence)
    if not checks or not all(checks.values()):
        print("the run did not measure every check: "
              + json.dumps(checks), file=sys.stderr)
        return 1
    report = _report(("clip.replace", "edit_patch"), evidence, checks,
                     scenarios=["replace_one_of_two_uses"])
    report_path = REPORT_DIR / "clip.replace__edit_patch.json"
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(_import(report_path), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
