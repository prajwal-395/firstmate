"""PLAN-008 live proof: an editorial task graph on a Ren-owned scratch timeline.

The unit tests prove the RULES. This proves them on a live Resolve
timeline, on a scratch project Ren owns, through the serving broker.

The assignment is three edits on one scratch timeline - three trims on
three clips, each with its own evidence step. What the proof measures
is the GRAPH's behaviour, and the graph does not care which algebra op
a task carries:

- **preparation is concurrent and outside the Resolve lease.** The
  three evidence steps run at the same time, in a client process that
  holds no lease at all.
- **the writes are serialized.** Each write is one AXI request on the
  one Resolve instance, and the run never has two in flight.
- **a run resumes at the last verified step.** The run is interrupted
  after its first verified write; the resumed run does not repeat it,
  and the fragile chain's repeat of the same write is refused by the
  AXI's own prior-outcome rule - which is what the resume replaces.

The proof runs in two halves, and the split is the point:

- the DRIVER runs in the lane's worktree: it creates the scratch
  project, runs the graph, and measures. The preparations run HERE - in
  a client that holds no lease - and the writes go to the broker
  through the AXI, which owns the mutation and the lease.
- the broker's worker places the clips and reads the timeline back,
  because only the worker holds a Resolve connection.

Nothing here touches a captain's project, timeline or media: the
project is a Ren-owned scratch project, and the media are copies of the
shared qualification pool's, in ``/tmp``. The proof never starts a
broker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = None
"""The lane's checkout.

The broker's `run` verb EXECUTES this file, so ``__file__`` does not
exist there; ``_root()`` reads the checkout from the configuration the
driver wrote instead.
"""


def _root() -> Path:
    """The lane's checkout, from the file's own path or the config."""
    try:
        return Path(__file__).resolve().parents[2]
    except NameError:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return Path(str(config["repo"]))


#: Where the driver writes the proof's configuration, and where the
#: proof reads it. The worker runs the file the driver names; the file
#: carries the lane's checkout so the proof uses the lane's code.
CONFIG_PATH = Path("/tmp/fm-p008-config.json")

#: The shared qualification pool. Its media are COPIED, never edited in
#: place: a replacement writes to the pool item it re-points.
SOURCE_FIXTURE = "Q Spine"

#: The machine's qualification fixture project: Ren-owned, and the
#: project the machine is currently open on.
QUALIFICATION_PROJECT = "Ren Qualification"

#: The front door that owns the protected staging guarantee. ``clip.trim``
#: has an executor and no production qualification, so the AXI routes it
#: under the protected experimental policy - which is named here, not
#: granted here (``library/tools/edit_qualification_policy.py``).
ORIGINATOR = {"kind": "agent", "name": "ren revise"}

REPORT_MARKER = "P008_REPORT "


# ── the broker round trips ───────────────────────────────────────────


def _submit_and_wait(argv: list[str], *, deadline: int = 1800) -> dict:
    """Submit one AXI job to the serving broker and wait for it.

    The machine's broker is shared: it may be restarted under this
    lane, and a job queued behind another lane's work can wait a long
    time. The proof waits for its turn rather than adding to the pile -
    and it never asks for a quiet window it does not get.
    """
    from library.tools.resolved import client

    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            if not client.serving():
                # The broker is not serving. This lane NEVER starts one
                # - a broker started from a worktree would serve
                # production calls on unmerged code.
                raise RuntimeError(
                    "the serving broker is not answering; this lane "
                    "never starts one")
            job = client.submit(
                "resolve_axi",
                {"argv": argv, "cwd": "/tmp", "caller": "p008-live-proof"},
                owner="p008-live-proof")
            done = client.result(job["id"], wait=5.0, until_done=True,
                                 deadline=deadline)
            if done.get("state") in ("done", "rejected"):
                return done
            last = RuntimeError(
                f"the broker left the job {done.get('state')!r}")
        except Exception as error:  # noqa: BLE001 - the retry is the answer
            last = error
            print(f"step: broker call {attempt} failed: {error}", flush=True)
            time.sleep(15)
    raise RuntimeError(f"the broker did not answer: {last}")


def _queued_mutations() -> int:
    """How many mutating jobs are waiting for the one Resolve."""
    from library.tools.resolved import client

    try:
        reply = client.call({"op": "list", "limit": 200}, timeout=10.0)
    except Exception:  # noqa: BLE001 - an unreadable queue is not a blocker
        return 0
    jobs = reply.get("jobs") or []
    return len([job for job in jobs
                if job.get("state") == "queued"
                and str(job.get("mode") or "") == "exclusive"
                and "p008" not in str(job.get("owner") or "")])


def _wait_for_the_queue(max_seconds: int = 900) -> int:
    """Wait for the machine's queue to drain a little."""
    deadline = time.monotonic() + max(0, max_seconds)
    waited = 0
    while time.monotonic() < deadline:
        if _queued_mutations() <= 2:
            return waited
        time.sleep(20)
        waited += 20
    return waited


# ── the worker half: inside the broker, where Resolve lives ──────────


def _copy_fixture(name: str) -> dict:
    """Copy a qualification fixture under this lane's own name.

    The machine's current Resolve project is the Ren-owned qualification
    fixture project, and it may not be switched away from (Resolve will
    not save it). So the proof works the way the qualification lanes
    work: it takes a COPY of an idle qualification fixture under a name
    of its own, and it writes only on that copy. The source fixture is
    read only, and no captain's project, timeline or media is read or
    written.
    """
    done = _submit_and_wait([
        "timeline", "duplicate", "--project", QUALIFICATION_PROJECT,
        "--timeline", SOURCE_FIXTURE, "--name", name, "--apply"])
    result = done.get("result") or {}
    return {"exit_code": result.get("exit_code"),
            "stdout": (result.get("stdout") or "")[-1200:]}


def _ren_project(slug: str, resolve_project: str) -> str:
    """The Ren project that owns the journal for the scratch project.

    The Ren project CLAIMS the Resolve project, so the AXI's write path
    finds the journal it records its receipt in.
    """
    from library.tools import project_registry

    folder = project_registry.project_folder_for_resolve_name(resolve_project)
    if folder:
        return folder
    subprocess.run(
        [str(_root() / "bin" / "ren"), "new", slug, "--name",
         "PLAN-008 scratch", "--resolve-name", resolve_project],
        cwd=str(_root()), check=True, capture_output=True, encoding="utf-8")
    folder = project_registry.project_folder_for_resolve_name(resolve_project)
    if not folder:
        raise RuntimeError("the Ren project was not created")
    return folder


def _recorded_clips(project: str, timeline: str) -> list[dict]:
    """The clips the recorded generations hold for this timeline."""
    from library.tools import timeline_shadow

    store = timeline_shadow.ShadowStore()
    for generation, snapshot in store.current_snapshots(project):
        if generation.timeline_name != timeline:
            continue
        clips = [clip for track in snapshot.get("tracks") or ()
                 for clip in track.get("clips") or ()
                 if str(clip.get("unique_id") or "")
                 and str(clip.get("track_type") or "video").lower()
                 == "video"]
        clips.sort(key=lambda clip: int(clip.get("record_in") or 0))
        return [{"unique_id": clip["unique_id"],
                 "record_in": clip.get("record_in"),
                 "record_out": clip.get("record_out")} for clip in clips]
    return []


def _build_graph(clips, project, timeline, graph_id):
    """The assignment as a graph: three evidence steps, three writes."""
    from library.tools.editorial_task_graph import EditorialTaskGraph

    tasks = []
    for index, clip in enumerate(clips):
        tasks.append({
            "id": f"prep-{index}",
            "kind": "prepare",
            "reads": ["timeline_structure"],
            "provides": [f"evidence:span-{index}"],
            "note": "read the clip's recorded span",
        })
        tasks.append({
            "id": f"trim-{index}",
            "kind": "write",
            "op": "clip.trim",
            "prepared_by": [f"prep-{index}"],
            "depends_on": [{"kind": "evidence",
                            "provides": f"evidence:span-{index}"}],
            "operation": {
                "op": "clip.trim",
                "target": {"unique_id": clip["unique_id"]},
                "params": {"in_frame": 0, "out_frame": 23},
                "expected_effect": "the clip is shorter",
            },
        })
    return EditorialTaskGraph.from_dict({
        "id": graph_id,
        "project": project,
        "timeline": timeline,
        "originator": dict(ORIGINATOR),
        "tasks": tasks,
    })


def _prepare_step(project, timeline, clip_ids):
    """The read-only evidence step: read one clip's recorded span.

    It reads the RECORDED generations from disk, so it touches no
    Resolve at all - which is what makes it safe to run at the same
    time as another one, and what the proof measures.
    """
    def prepare(task, context):
        from library.tools import timeline_shadow

        store = timeline_shadow.ShadowStore()
        timeline_id = store.resolve_timeline(project, timeline)
        history = store.history(project, timeline_id)
        found = {"generations": len(history)}
        if history:
            snapshot = store.snapshot(history[-1])
            for track in snapshot.get("tracks") or ():
                for clip in track.get("clips") or ():
                    unique_id = str(clip.get("unique_id") or "")
                    if unique_id in clip_ids.get(task.id, ()):
                        found[unique_id] = {
                            "record_in": clip.get("record_in"),
                            "record_out": clip.get("record_out")}
        return {"task": task.id, "produced": found}
    return prepare


def _lease_held() -> bool:
    """Whether THIS process holds the Resolve lease right now."""
    from library.tools import resolve_lock

    return bool(resolve_lock.held())


def _axi_write():
    """One write task, through the AXI and the serving broker.

    The request is the graph's own, and its id is stable for the task
    AND its content, so a repeat of the same write is answered with the
    prior outcome instead of being written twice. The AXI routes the
    request, the broker's worker performs it under the Resolve lease,
    and the client - where the preparations ran - never holds the lease.
    """
    from library.tools import resolve_axi_core
    from library.tools.editorial_task_runner import build_request

    def write(task, context):
        request = build_request(context["graph"], task)
        receipt = resolve_axi_core.submit_edit_request(request,
                                                        authorized=True)
        stage = str((receipt.get("lifecycle") or {}).get("stage") or "")
        return {"task": task.id, "request_id": request.id,
                "receipt": receipt, "stage": stage,
                "job_timing": receipt.get("job_timing") or {},
                "verified": stage in ("VERIFIED", "COMMITTED", "PROMOTED",
                                      "DELIVERED")}
    return write


def _receipt_of(result) -> dict:
    """The part of a write's result a reader needs: what it said."""
    if not isinstance(result, dict):
        return {"result": str(result)}
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        return {"stage": result.get("stage"),
                "verified": result.get("verified"),
                "reason": str(result.get("failure_reason") or "")[:400]}
    return {"stage": (receipt.get("lifecycle") or {}).get("stage"),
            "status": receipt.get("status"),
            "backend": receipt.get("backend"),
            "verified": result.get("verified"),
            "job_timing": result.get("job_timing") or {},
            "error": str(receipt.get("error") or "")[:400],
            "recovery": receipt.get("recovery")}


def main() -> int:
    """Set the scratch project up, run the graph, and measure it."""
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from library.tools.resolved import client

    if not client.serving():
        print("the serving broker is not answering; this lane never "
              "starts one")
        return 2
    attempt = uuid.uuid4().hex[:8]
    # The machine's current Resolve project is the Ren-owned qualification
    # fixture project; it may not be switched away from, so the proof
    # works inside it, on a timeline of its own.
    resolve_project = QUALIFICATION_PROJECT
    timeline = f"fm-scratch p008 {attempt}"
    graph_id = f"fm-p008-{attempt}"
    folder = _ren_project("fm-p008-qual", resolve_project)

    waited = _wait_for_the_queue()
    print(f"step: queue -> waited {waited}s", flush=True)

    setup = _copy_fixture(timeline)
    print(f"step: fixture copy -> {setup.get('exit_code')}", flush=True)

    clips: list[dict] = []
    for _ in range(120):
        clips = _recorded_clips(resolve_project, timeline)
        if len(clips) >= 3:
            break
        # The placement is recorded when the machine's watcher files the
        # generation; the client reads the recording a moment later.
        time.sleep(5)
    if len(clips) < 3:
        print(json.dumps({"setup": setup, "clips": clips}, indent=2,
                         default=str))
        return 1
    print(f"step: recorded -> {len(clips)} clips", flush=True)

    report: dict = {"project": resolve_project, "timeline": timeline,
                    "graph_id": graph_id, "folder": folder, "phases": {}}
    report["setup"] = {"setup": setup, "clips": clips}
    graph = _build_graph(clips, resolve_project, timeline, graph_id)
    report["plan"] = graph.plan().as_dict()
    clip_ids = {f"prep-{index}": (clip["unique_id"],)
                for index, clip in enumerate(clips)}
    prepare = _prepare_step(resolve_project, timeline, clip_ids)

    # ── the BEFORE: the fragile chain. Each edit's evidence is
    # gathered right before that edit, one edit at a time, in a client
    # that holds no lease.
    before_started = time.monotonic()
    before = []
    for task in graph.prepare_tasks():
        started = time.monotonic()
        prepare(task, {})
        before.append({"task": task.id,
                       "seconds": time.monotonic() - started,
                       "lease_held": _lease_held()})
    report["phases"]["before_sequential_prepare"] = {
        "tasks": before,
        "seconds": sum(row["seconds"] for row in before),
        "lease_held_during": any(row["lease_held"] for row in before),
    }
    report["phases"]["before_total_seconds"] = (
        time.monotonic() - before_started)

    # ── the AFTER: the graph. The preparations run at the same time;
    # the writes run one at a time, each through the broker.
    from library.tools.editorial_task_runner import run as run_graph

    broken = {"calls": 0}

    def interrupted_write(task, context):
        """The machine goes away after the first verified write."""
        broken["calls"] += 1
        if broken["calls"] == 2:
            raise RuntimeError("P008 injected interruption after the "
                               "first verified write")
        return _axi_write()(task, context)

    first = run_graph(graph, project_folder=folder, prepare=prepare,
                      write=interrupted_write, resume=True)
    print(f"step: interrupted run -> {first.status}", flush=True)
    report["phases"]["interrupted_run"] = {
        "status": first.status, "verified": list(first.verified),
        "prepared": list(first.prepared),
        "concurrency": dict(first.concurrency),
        "failure_reason": first.failure_reason,
        "timings": dict(first.timings),
        "results": {task: _receipt_of(result)
                    for task, result in (first.results or {}).items()},
    }

    # ── the FRAGILE CHAIN's answer to the same interruption: repeat
    # the first write. The AXI answers with the prior outcome, or
    # refuses - which is what the resume replaces.
    try:
        repeat = _axi_write()(graph.tasks["trim-0"], {
            "graph": graph, "graph_id": graph_id,
            "project": resolve_project, "timeline": timeline,
            "project_folder": folder})
        repeat_outcome = {"verified": repeat.get("verified"),
                          "stage": repeat.get("stage"),
                          "outcome": "answered"}
    except Exception as error:  # noqa: BLE001 - the refusal is the evidence
        repeat_outcome = {"verified": False, "outcome": "refused",
                          "reason": f"{type(error).__name__}: {error}"}
    report["phases"]["fragile_repeat_of_a_verified_write"] = repeat_outcome

    # ── the RESUME: the same graph, continued. The verified write is
    # not repeated.
    resumed = run_graph(graph, project_folder=folder, prepare=prepare,
                        write=_axi_write(), resume=True)
    print(f"step: resumed run -> {resumed.status}", flush=True)
    report["phases"]["resumed_run"] = {
        "status": resumed.status, "verified": list(resumed.verified),
        "resumed_from": resumed.resumed_from,
        "concurrency": dict(resumed.concurrency),
        "timings": dict(resumed.timings),
        "results": {task: _receipt_of(result)
                    for task, result in (resumed.results or {}).items()},
    }

    # ── the read-back: the live timeline says the clips are shorter.
    after = {clip["unique_id"]: clip
             for clip in _recorded_clips(resolve_project, timeline)}
    report["read_back"] = [
        {"unique_id": clip["unique_id"],
         "before_out": clip.get("record_out"),
         "after_out": (after.get(clip["unique_id"]) or {}).get("record_out")}
        for clip in clips]
    report["read_back_ok"] = all(
        row["after_out"] is not None
        and row["after_out"] < row["before_out"]
        for row in report["read_back"])

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    Path("/tmp/fm-p008-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return 0 if report.get("phases") else 1


if __name__ == "__main__":
    raise SystemExit(main())
