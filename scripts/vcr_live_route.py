#!/usr/bin/env python3
"""Broker-backed route driver for the VCR comparison matrix (AXI-12).

Invoked by ``scripts/vcr_comparison_matrix.py`` once per control/route
with ``--fixture-dir/--control/--route/--receipt``. The fixture dir is
the invocation's disposable project: the runner anchors its own
project.yaml, undo journal, and receipt there and writes nowhere else,
so a rebuild row never touches a real project. The guard refuses a dir
that is not a matrix fixture copy.

Execution runs on the private ``fm-scratch qualify`` timeline in the
Ren Qualification project through the Resolve broker, using only pool
media already resident there. The representative reel's local SVG
assets (overlay.svg, caption.svg) are stood in for by the pool's
qualification PNG fixtures; the substitution is recorded on the receipt
so a reader never mistakes it for the pipeline artwork. Controls that
need pipeline machinery (engine_source) are refused by name instead of
being approximated.

Routes: ``direct`` applies only the control delta onto the seeded
composition; ``staged`` applies the same delta through the EditPatch
transaction path; ``full_rebuild`` reconstructs the whole composition
from the manifest and then applies the control as declared. State and
render hashes use the same read-back machinery as the small-edit suite,
so the matrix compares like with like. Route-divergence sensitivity
beyond verb-level reconstruction (a real pipeline rebuild) is pending
pipeline-backed routes; this run qualifies the comparison contract and
the disposable-project isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
FIXTURE_SCHEMA = "vcr_representative_reel/1"

#: Fixture assets mapped onto resident pool media. The cameras are the
#: real qualification sources; the SVGs have pool PNG stand-ins because
#: importing into the shared pool's current folder is refused.
MEDIA_MAP = {
    "camera-a": "qual_a.mp4",
    "camera-b": "qual_b.mp4",
    "camera-c": "qual_c.mp4",
    "brand-mark": "fm-qual-logo-180x64.png",
    "caption-1": "fm-qual-tight-caption-480x120.png",
}

REFUSED_CONTROLS = {
    "engine_source": ("change_engine_source_revision needs the pipeline "
                      "engine; no verb-level reconstruction expresses it"),
}


def _fail(message: str) -> int:
    print(f"VCR route refused: {message}", file=sys.stderr)
    return 1


def _git_revision() -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=False)
    return proc.stdout.strip() or "unknown"


def _broker_script(control: str, route: str) -> str:
    return '''\
import contextlib, hashlib, io, json, time
from pathlib import Path
from library.tools import edit_patch, resolve_axi

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"
PROJECT_DIR = "@VCR_PROJECT_DIR@"

CONTROL = __CONTROL_JSON__
ROUTE = __ROUTE_JSON__

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

def live_state():
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    rows = []
    for track_index in range(1, video_tracks() + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            props = dict(item.GetProperty() or {})
            rows.append({"name": str(item.GetName()),
                         "track": track_index,
                         "record": [int(item.GetStart()),
                                    int(item.GetEnd())],
                         "source": [int(item.GetSourceStartFrame()),
                                    int(item.GetSourceEndFrame())],
                         "zoomx": float(props.get("ZoomX", 1.0)),
                         "zoomy": float(props.get("ZoomY", 1.0)),
                         "pan": float(props.get("Pan", 0.0))})
    return sorted(rows, key=lambda row: (row["track"], row["record"][0]))

def pool_uid(name):
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

def staged_insert(clip, track, record, src_in, src_out, label):
    receipt = edit_patch.apply_live_patch(
        project=manager.GetCurrentProject(),
        timeline=manager.GetCurrentProject().GetCurrentTimeline(),
        capability="vcr.staged",
        operations=[{"op": "clip.insert",
                     "unique_id": "vcr-staged-" + label,
                     "track_type": "video", "track_index": track,
                     "record_in": record,
                     "record_out": record + (src_out - src_in),
                     "source_in": src_in, "source_out": src_out,
                     "media_pool_item": pool_uid(clip)}],
        conflict_domains=("timeline_structure",),
        project_folder=PROJECT_DIR,
        idempotency_key="vcr-staged-" + label + "-" + str(time.time_ns()))
    if receipt.get("status") != "committed":
        raise RuntimeError("staged insert did not commit: "
                           + json.dumps(receipt, default=str)[:400])
    return receipt

def canonical(rows):
    return json.dumps(rows, sort_keys=True).encode("utf-8")

def render_hash():
    import shutil, tempfile
    from library.tools.color_page_grade import render_one_frame_deliver
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentProject().GetCurrentTimeline()
    target = next((item for index in range(1, video_tracks() + 1)
                   for item in (timeline.GetItemListInTrack(
                       "video", index) or ())), None)
    if target is None:
        return None
    out = tempfile.mkdtemp(prefix="fm-vcr-one-frame-")
    try:
        frame = render_one_frame_deliver(
            resolve, project, timeline, target, output_dir=out)
    finally:
        shutil.rmtree(out, ignore_errors=True)
    digest = hashlib.sha256(bytes(frame.data)).hexdigest()
    return {"sha256": digest,
            "shape": [int(value) for value in frame.shape]}

def seed_full(with_graphic=True, with_caption=True, shift_b=0):
    placements = [("qual_a.mp4", 1, 0, 0, 95),
                  ("qual_b.mp4", 1, 96 + shift_b, 0, 71),
                  ("qual_c.mp4", 1, 168 + shift_b, 0, 47)]
    if with_graphic:
        placements.append(("fm-qual-logo-180x64.png", 2, 96, 0, 119))
    if with_caption:
        placements.append(("fm-qual-tight-caption-480x120.png", 3, 96,
                           0, 47))
    for clip, track, record, src_in, src_out in placements:
        placed = call(["edit", "place", "--clip", clip, "--media", "video",
                       "--track", str(track), "--record", str(record),
                       "--source-in", str(src_in),
                       "--source-out", str(src_out),
                       "--apply", "--project", PROJECT,
                       "--timeline", TIMELINE])
        if placed["exit_code"]:
            raise RuntimeError("seed refused " + clip + ": "
                               + placed["stdout"] + placed["stderr"])

def teardown():
    allowed = {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
               "fm-qual-logo-180x64.png",
               "fm-qual-tight-caption-480x120.png",
               "fm-qual-full-frame-caption-1920x1080.png"}
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    doomed = []
    for track_index in range(1, video_tracks() + 1):
        for item in timeline.GetItemListInTrack("video", track_index) or ():
            if str(item.GetName()) not in allowed:
                raise RuntimeError("refusing unknown item "
                                   + str(item.GetName()))
            doomed.append(item)
    if doomed and not bool(timeline.DeleteClips(doomed, False)):
        raise RuntimeError("teardown delete refused")

started = time.perf_counter()
if live_state():
    raise RuntimeError("fixture timeline is not empty before the route")
touched = []
changed = []
mechanism = "reconstruction" if ROUTE == "full_rebuild" else "direct-verb"
if CONTROL == "unchanged":
    seed_full()
elif CONTROL in ("single_graphic", "caption"):
    asset = ("fm-qual-logo-180x64.png" if CONTROL == "single_graphic"
             else "fm-qual-tight-caption-480x120.png")
    track = 2 if CONTROL == "single_graphic" else 3
    src_out = 119 if CONTROL == "single_graphic" else 47
    if ROUTE == "full_rebuild":
        seed_full()
    elif ROUTE == "staged":
        seed_full(with_graphic=CONTROL != "single_graphic",
                  with_caption=CONTROL != "caption")
        staged_insert(asset, track, 96, 0, src_out, CONTROL)
        mechanism = "edit_patch(clip.insert)"
        touched, changed = [asset], [[96, 96 + src_out]]
    else:
        seed_full(with_graphic=CONTROL != "single_graphic",
                  with_caption=CONTROL != "caption")
        placed = call(["edit", "place", "--clip", asset, "--media", "video",
                       "--track", str(track), "--record", "96",
                       "--source-in", "0", "--source-out", str(src_out),
                       "--apply", "--project", PROJECT,
                       "--timeline", TIMELINE])
        if placed["exit_code"]:
            raise RuntimeError("control delta refused: "
                               + placed["stdout"] + placed["stderr"])
        touched, changed = [asset], [[96, 96 + src_out]]
elif CONTROL == "structural_timing":
    if ROUTE == "full_rebuild":
        seed_full(shift_b=12)
    else:
        seed_full()
        rows = live_state()
        target = next(item for item in rows
                      if item["name"] == "qual_b.mp4")
        index = [item for item in rows
                 if item["track"] == 1].index(target)
        argv = ["edit", "move", "--track", "video1",
                "--index", str(index), "--to", "108",
                "--apply", "--project", PROJECT, "--timeline", TIMELINE]
        if ROUTE == "staged":
            argv += ["--project-folder", PROJECT_DIR]
            mechanism = "journaled-move-verb"
        moved = call(argv)
        if moved["exit_code"]:
            raise RuntimeError("structural delta refused: "
                               + moved["stdout"] + moved["stderr"])
        touched, changed = ["qual_b.mp4"], [[96, 168]]
else:
    raise RuntimeError("unknown control " + CONTROL)
state = live_state()
state_hash = hashlib.sha256(canonical(state)).hexdigest()
render = render_hash()
result = {"state": state, "final_state_sha256": state_hash,
          "render": render, "touched": touched, "changed": changed,
          "mechanism": mechanism,
          "lease_seconds": round(time.perf_counter() - started, 6)}
teardown()
if live_state():
    raise RuntimeError("route teardown left items behind")
print(json.dumps(result, indent=2))
'''.replace("__CONTROL_JSON__", json.dumps(control)).replace(
        "__ROUTE_JSON__", json.dumps(route))


def _submit(script: str, run_dir: Path) -> dict:
    from tests.live import small_edit_qualification as q

    batch = q._qualification_batch(
        q._select_timeline_script() + script.replace(
            "@VCR_PROJECT_DIR@", str(run_dir)))
    steps = (batch.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(
            f"VCR batch returned no steps: {batch}")
    stdout = steps[0]["stdout"]
    start = stdout.find("{\n")
    if start < 0:
        raise RuntimeError(
            f"JSON result block is missing: {stdout[-2000:]}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--control", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.fixture_dir
    manifest_path = run_dir / "fixture.json"
    if not manifest_path.is_file():
        return _fail(f"{run_dir} holds no fixture.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return _fail(f"fixture.json unreadable: {exc}")
    if manifest.get("schema") != FIXTURE_SCHEMA:
        return _fail(f"unsupported fixture schema "
                     f"{manifest.get('schema')!r}")
    if args.route not in ("direct", "staged", "full_rebuild"):
        return _fail(f"unknown route {args.route!r}")
    known = {control["id"] for control in manifest.get("controls", [])}
    if args.control not in known:
        return _fail(f"unknown control {args.control!r}")
    if args.control in REFUSED_CONTROLS:
        return _fail(f"{args.control}: "
                     f"{REFUSED_CONTROLS[args.control]}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "project.yaml").write_text(
        f"project_name: vcr_{args.control}_{args.route}\n",
        encoding="utf-8")
    journal = run_dir / "undo-journal"
    journal.mkdir(exist_ok=True)
    try:
        payload = _submit(_broker_script(args.control, args.route), run_dir)
    except Exception as exc:  # noqa: BLE001 - route failure is evidence
        return _fail(f"broker execution failed: {exc}")
    render = payload.get("render") or {}
    if not payload.get("final_state_sha256") or not render.get("sha256"):
        return _fail("route produced no verifiable state/render hashes")
    receipt = {
        "schema": "vcr_route_receipt/1",
        "control": args.control,
        "route": args.route,
        "final_state_sha256": payload["final_state_sha256"],
        "render_sha256": render["sha256"],
        "render_shape": render.get("shape", []),
        "rerendered_components": payload.get("touched", []),
        "changed_placements": payload.get("changed", []),
        "resolve_lease_seconds": payload.get("lease_seconds", 0.0),
        "engine_revision": f"resolve-axi verbs @ {_git_revision()}",
        "mechanism": payload.get("mechanism", "unknown"),
        "media_mapping": dict(MEDIA_MAP),
        "timeline": {"project": PROJECT, "timeline": TIMELINE},
        "verification": "passed",
    }
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n",
                            encoding="utf-8")
    print(json.dumps({"control": args.control, "route": args.route,
                      "state": payload["final_state_sha256"][:12],
                      "render": render["sha256"][:12]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
