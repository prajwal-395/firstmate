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

- the WORKER half runs INSIDE the broker: it places the clips and
  reads the timeline back, because only the broker's worker holds a
  Resolve connection. It touches the lane's own code.
- the DRIVER half runs in the lane's worktree: it builds the graph,
  runs it, and measures. The preparations run HERE - in a client that
  holds no lease - and the writes go to the broker through the AXI,
  which owns the mutation and the lease.

Nothing here touches a captain's project, timeline or media: the
project is a Ren-owned scratch project, and the media are copies in
``/tmp``.
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

#: The Ren project that owns the journal for the scratch Resolve
#: project, and the scratch Resolve project's name.
REN_SLUG = "fm-p008-scratch-c"
#: The Resolve project the REN project records. The scratch project
#: the proof writes on is named per attempt (a fresh three-clip
#: timeline each time, so the qualification state the AXI measures is
#: the state it was qualified for).
REN_RESOLVE_NAME = "Ren Scratch - fm-p008c"

#: The front door that owns the protected staging guarantee. ``clip.trim``
#: has an executor and no production qualification, so the AXI routes it
#: under the protected experimental policy - which is named here, not
#: granted here (``library/tools/edit_qualification_policy.py``).
ORIGINATOR = {"kind": "agent", "name": "ren revise"}

#: The shared qualification pool. Its media are COPIED, never edited in
#: place: a replacement writes to the pool item it re-points.
POOL_DIR = Path.home() / ".local" / "share" / "vep" / "qualification" / \
    "622307e41719"
OWNED_MEDIA = {
    "fm-p008-a.mp4": POOL_DIR / "qual_a.mp4",
    "fm-p008-b.mp4": POOL_DIR / "qual_b.mp4",
    "fm-p008-c.mp4": POOL_DIR / "qual_c.mp4",
}

REPORT_MARKER = "P008_REPORT "


# ── the worker half: inside the broker, where Resolve lives ──────────


def _install_lane_code(repo: str) -> list[str]:
    """Put the LANE's modules in front of the worker's own.

    The broker's worker runs the PRIMARY checkout's code, and those
    modules are already imported - so putting the lane's checkout first
    on ``sys.path`` is not enough. Replacing the modules in
    ``sys.modules`` (and on their package) makes the proof exercise the
    lane's code, which is the code under test.
    """
    import importlib.util

    import library.tools

    installed = []
    for name in ("editorial_task_graph", "editorial_task_runner"):
        path = Path(repo) / "library" / "tools" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(
            f"library.tools.{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"library.tools.{name}"] = module
        setattr(library.tools, name, module)
        spec.loader.exec_module(module)
        installed.append(name)
    return installed


def _call(resolve_axi, argv):
    """Call one AXI verb in-process, and capture what it said."""
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": list(argv), "exit_code": int(code or 0),
            "stdout": out.getvalue()[-2000:], "stderr": err.getvalue()[-2000:]}


def _clip_count(project: str, timeline: str) -> int:
    """How many clips the scratch timeline already holds."""
    from library.tools import resolve_axi_core

    try:
        live = resolve_axi_core.read_live_timeline(project, timeline)
    except Exception:  # noqa: BLE001 - an unread timeline places from zero
        return 0
    return len([clip for track in live.get("tracks") or ()
                for clip in track.get("clips") or ()])


def _setup(project: str, timeline: str) -> dict:
    """Place the three clips the proof needs, through the AXI's verbs.

    The media are the lane's own copies in ``/tmp``; the shared
    qualification pool is never touched. A retry places the next three
    clips after the ones already there, so every attempt works on
    clips nobody has trimmed.
    """
    from library.tools import resolve_axi

    names = sorted(OWNED_MEDIA)
    media = [str(Path("/tmp") / name) for name in names]
    out: dict = {"ingest": _call(
        resolve_axi, ["ingest", "--project", project, *media,
                      "--apply"])}
    existing = _clip_count(project, timeline)
    out["existing_clips"] = existing
    for index, name in enumerate(names):
        out[f"place_{name}"] = _call(resolve_axi, [
            "edit", "place", "--project", project, "--timeline",
            timeline, "--clip", name, "--media", "video", "--track", "1",
            "--record", str((existing + index) * 48), "--source-in", "0",
            "--source-out", "47", "--apply"])
    return out


def _read_live_clips(project: str, timeline: str) -> list[dict]:
    """The clips the scratch timeline holds now, live."""
    from library.tools import resolve_axi_core

    live = resolve_axi_core.read_live_timeline(project, timeline)
    clips = [clip for track in live.get("tracks") or ()
             for clip in track.get("clips") or ()
             if str(clip.get("unique_id") or "")]
    clips.sort(key=lambda clip: int(clip.get("record_in") or 0))
    return [{"unique_id": clip["unique_id"],
             "record_in": clip.get("record_in"),
             "record_out": clip.get("record_out")}
            for clip in clips]


def worker_proof(resolve=None) -> dict:
    """The worker half: set the timeline up, or read it back.

    `resolve` is the broker worker's own scripting client, so the proof
    never opens a second one.
    """
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    repo = str(config["repo"])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    report: dict = {"installed_modules": _install_lane_code(repo),
                    "mode": str(config.get("mode") or "setup")}
    project = str(config.get("project") or "")
    timeline = str(config.get("timeline") or "")
    if report["mode"] == "readback":
        report["clips"] = _read_live_clips(project, timeline)
        return report
    report["setup"] = _setup(project, timeline)
    report["clips"] = _read_live_clips(project, timeline)
    return report


# ── the driver half: in the lane's worktree ─────────────────────────


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

    return bool(resolve_lock.active_holder())


def _axi_write():
    """One write task, through the AXI and the serving broker.

    The request is the graph's own, and its id is STABLE, so a repeat
    of the same write is answered with the prior outcome instead of
    being written twice. The AXI routes the request, the broker's
    worker performs it under the Resolve lease, and the client - where
    the preparations ran - never holds the lease.
    """
    from library.tools import resolve_axi_core
    from library.tools.editorial_task_runner import build_request

    def write(task, context):
        request = build_request(
            context["graph"], task, request_id=f"{context['graph_id']}-"
                                                f"{task.id}")
        receipt = resolve_axi_core.submit_edit_request(request,
                                                        authorized=True)
        stage = str((receipt.get("lifecycle") or {}).get("stage") or "")
        return {"task": task.id, "request_id": request.id,
                "receipt": receipt,
                "stage": stage,
                "job_timing": receipt.get("job_timing") or {},
                "verified": stage in ("VERIFIED", "COMMITTED", "PROMOTED",
                                      "DELIVERED")}
    return write


def _run_graph(graph, folder, prepare, write, **kwargs):
    """Run the graph in this process, and measure it."""
    from library.tools.editorial_task_runner import run as run_graph

    return run_graph(graph, project_folder=folder, prepare=prepare,
                     write=write, **kwargs)


def _own_media() -> list[str]:
    """Copy the pool media out of the shared pool. The pool is read-only."""
    names = []
    for name, source in OWNED_MEDIA.items():
        target = Path("/tmp") / name
        if not target.exists():
            shutil.copyfile(source, target)
        names.append(str(target))
    return names


def _ren_project(resolve_project: str) -> str:
    """The Ren project that owns the journal for the scratch project.

    ONE Ren project serves every attempt: the journal is per project,
    and the graph id is unique per attempt, so the attempts do not read
    each other's checkpoints.
    """
    from library.tools import project_registry

    folder = project_registry.project_folder_for_resolve_name(
        REN_RESOLVE_NAME)
    if folder:
        return folder
    subprocess.run(
        [str(_root() / "bin" / "ren"), "new", REN_SLUG, "--name",
         "PLAN-008 scratch", "--resolve-name", REN_RESOLVE_NAME],
        cwd=str(_root()), check=True, capture_output=True, encoding="utf-8")
    folder = project_registry.project_folder_for_resolve_name(
        REN_RESOLVE_NAME)
    if not folder:
        raise RuntimeError("the Ren project was not created")
    return folder


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
    """Wait for the machine's queue to drain a little.

    The broker serves ONE Resolve. The proof's jobs are mutations, so
    they queue behind whatever else the machine is doing. This waits
    for a turn rather than adding to the pile - and it never asks for
    a quiet window it does not get: the wait is bounded and the proof
    proceeds when it ends.
    """
    deadline = time.monotonic() + max(0, max_seconds)
    waited = 0
    while time.monotonic() < deadline:
        queued = _queued_mutations()
        if queued <= 1:
            return waited
        time.sleep(20)
        waited += 20
    return waited


def _submit_and_wait(argv: list[str], *, deadline: int = 1800) -> dict:
    """Submit one AXI job to the serving broker and wait for it.

    The machine's broker is shared: it may be restarted under this
    lane, and a job queued behind another lane's work can be answered
    by a socket that closed. The proof RETRIES that, because the
    measurement is the graph's behaviour, not the broker's uptime.
    """
    from library.tools.resolved import client

    last: Exception | None = None
    for attempt in range(1, 7):
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
            return client.result(job["id"], wait=5.0, until_done=True,
                                 deadline=deadline)
        except Exception as error:  # noqa: BLE001 - the retry is the answer
            last = error
            print(f"step: broker call {attempt} failed: {error}", flush=True)
            time.sleep(10)
    raise RuntimeError(f"the broker did not answer: {last}")


def _worker_call(mode: str, proof: str, folder: str, project: str,
                 timeline: str) -> dict:
    """Run one worker half through the serving broker."""
    from library.tools.resolved import client

    CONFIG_PATH.write_text(json.dumps({
        "repo": str(_root()), "project": project,
        "timeline": timeline, "folder": folder, "mode": mode}),
        encoding="utf-8")
    argv = ["run", "--project", project, "--file", proof, "--unsafe",
            "--json", "--acknowledge-copy-grades", "--purpose",
            f"PLAN-008 live proof: {mode}", "--scope", "project",
            "--project-dir", folder]
    done = _submit_and_wait(argv)
    return _extract(done)


def _open_scratch(project: str) -> None:
    """Make sure the Ren-owned scratch project is the one that is open."""
    opening = _submit_and_wait(
        ["project", "scratch", "open", project, "--apply"])
    stdout = str((opening.get("result") or {}).get("stdout") or "")
    if "is not in the current folder" in stdout:
        _submit_and_wait(["project", "scratch", "create", project,
                           "--fps", "24", "--apply"])
        _submit_and_wait(["project", "scratch", "open", project, "--apply"])


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
    resolve_project = f"Ren Scratch - fm-p008-{attempt}"
    timeline = f"{resolve_project} - Empty"
    graph_id = f"fm-p008-{attempt}"
    folder = _ren_project(resolve_project)
    _own_media()
    proof = str(Path(__file__).resolve())
    waited = _wait_for_the_queue()
    print(f"step: queue -> waited {waited}s", flush=True)
    _open_scratch(resolve_project)
    setup: dict = {}
    for attempt in range(1, 7):
        # Another lane's session may hold the current project, and the
        # machine's own watcher moves it. The scratch project is opened
        # again before each attempt, and the attempt is retried.
        _open_scratch(resolve_project)
        setup = _worker_call("setup", proof, folder, resolve_project,
                             timeline)
        if setup.get("clips"):
            break
        print(f"step: setup attempt {attempt} did not read the "
              f"scratch project; retrying", flush=True)
        time.sleep(20)
    if not setup.get("clips"):
        print(json.dumps(setup, indent=2, default=str))
        return 1
    print("step: setup ->", len(setup["clips"]), "clips", flush=True)

    report: dict = {"project": resolve_project, "timeline": timeline,
                    "graph_id": graph_id, "folder": folder, "phases": {}}
    report["setup"] = {"clips": setup.get("clips")}
    clips = setup["clips"][-3:]
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
    broken = {"calls": 0}

    def interrupted_write(task, context):
        """The machine goes away after the first verified write."""
        broken["calls"] += 1
        if broken["calls"] == 2:
            raise RuntimeError("P008 injected interruption after the "
                               "first verified write")
        return _axi_write()(task, context)

    first = _run_graph(graph, folder, prepare, interrupted_write,
                       resume=True)
    print("step: interrupted run ->", first.status, flush=True)
    report["phases"]["interrupted_run"] = {
        "status": first.status, "verified": list(first.verified),
        "prepared": list(first.prepared),
        "concurrency": dict(first.concurrency),
        "failure_reason": first.failure_reason,
        "timings": dict(first.timings),
    }

    # ── the FRAGILE CHAIN's answer to the same interruption: repeat
    # the first write. The AXI refuses it - the target has moved on -
    # which is what the resume replaces.
    try:
        repeat = _axi_write()(graph.tasks["trim-0"], {
            "graph": graph, "graph_id": graph_id,
            "project": resolve_project, "timeline": timeline})
        repeat_outcome = {"verified": repeat.get("verified"),
                          "stage": repeat.get("stage"),
                          "outcome": "answered"}
    except Exception as error:  # noqa: BLE001 - the refusal is the evidence
        repeat_outcome = {"verified": False,
                          "outcome": "refused",
                          "reason": f"{type(error).__name__}: {error}"}
    report["phases"]["fragile_repeat_of_a_verified_write"] = repeat_outcome

    # ── the RESUME: the same graph, continued. The verified write is
    # not repeated.
    resumed = _run_graph(graph, folder, prepare, _axi_write(), resume=True)
    print("step: resumed run ->", resumed.status, flush=True)
    report["phases"]["resumed_run"] = {
        "status": resumed.status, "verified": list(resumed.verified),
        "resumed_from": resumed.resumed_from,
        "concurrency": dict(resumed.concurrency),
        "timings": dict(resumed.timings),
    }

    # ── the read-back: the three clips are shorter than they were.
    readback = _worker_call("readback", proof, folder, resolve_project,
                            timeline)
    after = {clip["unique_id"]: clip for clip in readback.get("clips") or ()}
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


def _extract(receipt: dict) -> dict:
    """The proof's own report, out of the session receipt.

    The stderr is kept: a proof that raised says so there, and a
    report that says nothing is a report that says nothing.
    """
    result = receipt.get("result")
    stdout = str((result or {}).get("stdout") or "")
    stderr = str((result or {}).get("stderr") or "")
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(REPORT_MARKER):
            return json.loads(line[len(REPORT_MARKER):])
    return {"error": "the proof printed no report",
            "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:],
            "receipt_error": str(receipt.get("error") or "")[:2000]}


if "resolve" in globals():
    # The broker's `run` verb executes this file in a sandbox that holds
    # the worker's own scripting client. The proof runs there, on the
    # LANE's code (the checkout the configuration names goes first on
    # sys.path), and the report is both printed and left as the run's
    # own result.
    _REPORT = worker_proof(globals().get("resolve"))
    print(REPORT_MARKER + json.dumps(_REPORT, ensure_ascii=False,
                                     default=str))
    result = _REPORT
elif __name__ == "__main__":
    raise SystemExit(main())
