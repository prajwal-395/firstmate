"""AXI-014 live proof: a compound transaction on a Ren-owned scratch timeline.

The unit tests prove the RULES. This proves them on a live Resolve
timeline, on a scratch project Ren owns, through the serving broker.

The scenarios, in the order they run:

- **baseline** - one committed write, so the timeline has a VALIDATED
  generation: the newest generation a verified write left. Everything
  after this is measured against it.
- **compound_failure** - a compound of THREE writes with a failure
  INJECTED after the first of them. The proof is that the timeline reads
  as the validated generation afterwards, that no recovery hold is left,
  and that the writes were attempted exactly once (nothing was retried).
- **no_silent_retry** - the same transaction id submitted again after a
  possibly committed mutation: the second submission is REFUSED, not
  written.

The proof runs in two halves, and the split is the point:

- the DRIVER (`main`) runs in the lane's worktree. It creates the Ren
  project that owns the journal, copies the media out of the shared
  qualification pool (the pool is never edited in place), and submits
  the proof to the SERVING broker. It never starts a broker.
- the PROOF (`worker_proof`) runs INSIDE the broker's worker, on the
  LANE's own code - it puts the lane's checkout first on ``sys.path`` -
  so the code under test is the code the lane wrote.

Nothing here touches a captain's project, timeline or media: the
project is a Ren-owned scratch project, and the media are copies in
``/tmp``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
CONFIG_PATH = Path("/tmp/fm-axi014-config.json")

#: The Ren project that owns the journal for the scratch Resolve
#: project, and the scratch Resolve project's name.
REN_SLUG = "axi014-scratch"
RESOLVE_PROJECT = "Ren Scratch - axi014"
TIMELINE = "Ren Scratch - axi014 - Empty"

#: The shared qualification pool. Its media are COPIED, never edited in
#: place: a replacement writes to the pool item it re-points.
POOL_DIR = Path.home() / ".local" / "share" / "vep" / "qualification" / \
    "622307e41719"
OWNED_MEDIA = {
    "fm-axi014-a.mp4": POOL_DIR / "qual_a.mp4",
    "fm-axi014-b.mp4": POOL_DIR / "qual_b.mp4",
    "fm-axi014-c.mp4": POOL_DIR / "qual_c.mp4",
}

REPORT_MARKER = "AXI014_REPORT "


# ── the proof, inside the broker's worker ────────────────────────────


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


def _setup() -> dict:
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
        resolve_axi, ["ingest", "--project", RESOLVE_PROJECT, *media,
                      "--apply"])}
    existing = _clip_count()
    out["existing_clips"] = existing
    for index, name in enumerate(names):
        out[f"place_{name}"] = _call(resolve_axi, [
            "edit", "place", "--project", RESOLVE_PROJECT, "--timeline",
            TIMELINE, "--clip", name, "--media", "video", "--track", "1",
            "--record", str((existing + index) * 48), "--source-in", "0",
            "--source-out", "47", "--apply"])
    return out


def _clip_count() -> int:
    """How many clips the scratch timeline already holds."""
    from library.tools import resolve_axi_core

    try:
        live = resolve_axi_core.read_live_timeline(RESOLVE_PROJECT, TIMELINE)
    except Exception:  # noqa: BLE001 - an unread timeline places from zero
        return 0
    return len([clip for track in live.get("tracks") or ()
                for clip in track.get("clips") or ()])


def _install_lane_code(repo: str) -> list[str]:
    """Put the LANE's modules in front of the worker's own.

    The broker's worker runs the PRIMARY checkout's code, and those
    modules are already imported - so putting the lane's checkout first
    on ``sys.path`` is not enough. Replacing the modules in
    ``sys.modules`` (and on their package) makes the proof exercise the
    lane's code, which is the code under test.

    The three modules are the ones this lane changed: the transaction
    envelope, the compound rules, and the AXI core that asks them.
    """
    import importlib.util
    import library.tools

    installed = []
    for name in ("axi_transaction", "compound_transaction",
                 "resolve_axi_core"):
        path = Path(repo) / "library" / "tools" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(
            f"library.tools.{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"library.tools.{name}"] = module
        setattr(library.tools, name, module)
        spec.loader.exec_module(module)
        installed.append(name)
    return installed


def worker_proof(resolve=None) -> dict:
    """Run the three scenarios and return the report.

    The LANE's modules are installed in front of the worker's own (see
    ``_install_lane_code``), so the proof exercises the lane's code.
    `resolve` is the broker worker's own scripting client, so the proof
    never opens a second one.
    """
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    repo = str(config["repo"])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    report: dict = {}
    try:
        report["installed_modules"] = _install_lane_code(repo)
    except Exception as exc:  # noqa: BLE001 - the proof reports it
        return {"refused": f"the lane's code could not be installed: {exc}"}
    from library.tools import (axi_transaction, compound_transaction,
                               resolve_axi_core)
    from library.tools.edit_request import (EditOperation, EditRequest,
                                            EditTarget)
    from library.tools.resolve_lock import resolve_lease

    project = str(config["project"])
    timeline = str(config["timeline"])
    report: dict = {"project": project, "timeline": timeline,
                    "scenarios": []}
    report["setup"] = _setup()
    with resolve_lease("axi014-live-proof", exclusive=True,
                       owner="axi014-live-proof", project=project,
                       timeline=timeline):
        live = resolve_axi_core.read_live_timeline(project, timeline,
                                                   resolve=resolve)
        clips = [clip for track in live.get("tracks") or ()
                 for clip in track.get("clips") or ()
                 if str(clip.get("unique_id") or "")]
        clips.sort(key=lambda clip: int(clip.get("record_in") or 0))
        clips = clips[-3:]
        report["clips"] = [{"unique_id": clip["unique_id"],
                            "record_in": clip.get("record_in"),
                            "record_out": clip.get("record_out")}
                           for clip in clips]
        if len(clips) < 3:
            report["refused"] = (f"the scratch timeline holds {len(clips)} "
                                 "clips; the proof needs three")
            return report

        def build(operations, request_id):
            return EditRequest(
                id=request_id, project=project, timeline=timeline,
                operations=tuple(operations), idempotency_key=request_id,
                originator={"kind": "agent", "name": "axi014-live-proof"})

        def trim(unique_id, out_frame, effect):
            return EditOperation(
                op="clip.trim", target=EditTarget(unique_id=unique_id),
                params={"in_frame": 0, "out_frame": out_frame},
                expected_effect=effect)

        def remove(unique_id, effect):
            return EditOperation(
                op="clip.delete", target=EditTarget(unique_id=unique_id),
                params={}, expected_effect=effect)

        # ── the baseline: a committed write, so there is a validated
        # generation to be restored to.
        baseline_request = build(
            [trim(clips[0]["unique_id"], 23, "shorten the first clip")],
            f"axi014-baseline-{uuid.uuid4().hex[:8]}")
        baseline = _run(resolve_axi_core, baseline_request, resolve=resolve)
        report["scenarios"].append({"id": "baseline", **baseline})
        validated = compound_transaction.validated_generation(
            project, timeline)
        report["validated_generation"] = validated
        if validated is None:
            report["refused"] = ("the baseline write left no validated "
                                 "generation, so a restoration cannot be "
                                 "measured")
            return report

        # ── the compound, with the failure injected after the first
        # of three writes.
        compound_request = build(
            [trim(clips[1]["unique_id"], 23, "shorten the second clip"),
             trim(clips[2]["unique_id"], 23, "shorten the third clip"),
             remove(clips[0]["unique_id"], "remove the first clip")],
            f"axi014-compound-{uuid.uuid4().hex[:8]}")
        injected = _run(resolve_axi_core, compound_request, resolve=resolve,
                        inject_after=1)
        report["scenarios"].append({"id": "compound_failure", **injected})

        # ── the read-back: the timeline against the validated
        # generation, the journal's stages, and the holds.
        after = resolve_axi_core.read_live_timeline(project, timeline,
                                                    resolve=resolve)
        restoration = compound_transaction.verify_restored(
            project=project, timeline=timeline,
            generation=validated["generation"], live=after)
        report["restoration"] = restoration
        folder = _journal_folder(project)
        report["journal_folder"] = folder
        report["journal_stages"] = _stages(folder, compound_request.id)
        report["holds"] = axi_transaction.recovery_state(
            folder, project=project, timeline=timeline).as_dict()
        report["attempts"] = _attempts(folder, compound_request.id)

        # ── the same transaction id again: a possibly committed
        # mutation is never retried silently.
        retry = _run(resolve_axi_core, compound_request, resolve=resolve)
        report["scenarios"].append({"id": "no_silent_retry", **retry})
    return report


def _journal_folder(project: str) -> str:
    from library.tools import project_registry

    folder = project_registry.project_folder_for_resolve_name(project)
    return str(folder or "")


def _stages(folder: str, transaction_id: str) -> list[str]:
    from library.tools import event_log

    if not folder:
        return []
    return [str((event.get("fields") or {}).get("stage") or "")
            for event in event_log.read_events(folder)
            if str((event.get("fields") or {}).get("transaction_id") or "")
            == str(transaction_id)]


def _attempts(folder: str, transaction_id: str) -> dict:
    """How many times the transaction tried to write."""
    stages = _stages(folder, transaction_id)
    return {"prepared": stages.count("prepared"),
            "applying": stages.count("applying"),
            "applied": stages.count("applied"),
            "committed": stages.count("committed"),
            "recovered": stages.count("recovered"),
            "recovery_required": stages.count("recovery_required"),
            "abandoned": stages.count("abandoned")}


def _run(core, request, *, resolve=None, inject_after: int | None = None
        ) -> dict:
    """Run one request through the production semantic-edit path.

    With `inject_after`, the ADAPTER of that operation raises instead of
    writing: the failure is injected in the engine's own call, not in
    Resolve, so the engine's compensation and recovery are what answer
    it.
    """
    from library.tools import edit_execution_adapters

    plan, request, preparation, compiled, generation, snapshot = (
        core._prepare_request_plan(request, resolve=resolve))
    backend = str(plan.get("backend") or "")
    row: dict = {"backend": backend, "routing": plan.get("routing"),
                 "base_generation": generation.generation,
                 "operations": [dict(op) for op in compiled.operations]}
    edit_execution_adapters.register_builtin_edit_executors()
    if inject_after is not None:
        for index, operation in enumerate(compiled.operations):
            key = (str(operation["op"]), backend)
            original = core._EDIT_EXECUTORS.get(key)
            if original is None:
                row["injection"] = f"no adapter for {key}"
                continue
            position = index

            def failing(context, _original=original, _position=position):
                if _position == inject_after:
                    raise RuntimeError(
                        "AXI-014 injected failure after the first write")
                return _original(context)

            core.register_edit_executor(key[0], key[1], failing,
                                        replace=True)
    job = {"params": {"request": request.to_dict(),
                      "base_generation": generation.generation,
                      "backend": backend},
           "owner": "axi014-live-proof"}
    try:
        receipt = core.execute_semantic_edit_job(job, resolve=resolve)
    except Exception as exc:  # noqa: BLE001 - the refusal is the evidence
        row["raised"] = f"{type(exc).__name__}: {exc}"
        return row
    row["receipt"] = receipt
    row["status"] = receipt.get("status")
    row["recovery"] = receipt.get("recovery")
    row["refusal"] = receipt.get("refusal")
    return row


# ── the driver, in the lane's worktree ───────────────────────────────


def _own_media() -> list[str]:
    """Copy the pool media out of the shared pool. The pool is read-only."""
    names = []
    for name, source in OWNED_MEDIA.items():
        target = Path("/tmp") / name
        if not target.exists():
            shutil.copyfile(source, target)
        names.append(str(target))
    return names


def _ren_project() -> str:
    """The Ren project that owns the journal for the scratch project."""
    from library.tools import project_registry

    folder = project_registry.project_folder_for_resolve_name(RESOLVE_PROJECT)
    if folder:
        return folder
    subprocess.run(
        [str(_root() / "bin" / "ren"), "new", REN_SLUG, "--name",
         "AXI-014 scratch", "--resolve-name", RESOLVE_PROJECT],
        cwd=str(_root()), check=True, capture_output=True, encoding="utf-8")
    folder = project_registry.project_folder_for_resolve_name(RESOLVE_PROJECT)
    if not folder:
        raise RuntimeError("the Ren project was not created")
    return folder


def main() -> int:
    """Create the scratch project, place three clips, run the proof."""
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from library.tools.resolved import client

    if not client.serving():
        print("the serving broker is not answering; this lane never "
              "starts one")
        return 2
    folder = _ren_project()
    media = _own_media()
    CONFIG_PATH.write_text(json.dumps({
        "repo": str(root), "project": RESOLVE_PROJECT,
        "timeline": TIMELINE}), encoding="utf-8")
    proof = str(Path(__file__).resolve())

    def ax(*argv):
        return ["resolve_axi", list(argv)]

    proof_argv = [
        "run", "--project", RESOLVE_PROJECT, "--file", proof, "--unsafe",
        "--json", "--acknowledge-copy-grades", "--purpose",
        "AXI-014 live proof: a compound transaction recovers to the "
        "validated generation", "--scope", "project", "--project-dir",
        folder]
    report: dict = {}
    for attempt in range(1, 7):
        opened = client.submit(
            "resolve_axi",
            {"argv": ["project", "scratch", "open", RESOLVE_PROJECT,
                      "--apply"], "cwd": "/tmp", "caller": "axi014-live-proof"},
            owner="axi014-live-proof")
        client.result(opened["id"], wait=5.0, until_done=True, deadline=1800)
        job = client.submit(
            "resolve_axi",
            {"argv": proof_argv, "cwd": "/tmp", "caller": "axi014-live-proof"},
            owner="axi014-live-proof")
        done = client.result(job["id"], wait=5.0, until_done=True,
                             deadline=1800)
        stdout = str((done.get("result") or {}).get("stdout") or "")
        if f"not '{RESOLVE_PROJECT}'" in stdout:
            print(f"attempt {attempt}: another lane's session had the "
                  "project open; retrying", flush=True)
            continue
        print("step: run ->", done.get("state"), flush=True)
        report = _extract(done)
        break
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    Path("/tmp/fm-axi014-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return 0 if report.get("scenarios") else 1


def _extract(receipt: dict) -> dict:
    """The proof's own report, out of the session receipt."""
    result = receipt.get("result")
    steps = (result or {}).get("steps") if isinstance(result, dict) else []
    for step in reversed(steps or ()):
        stdout = str((step or {}).get("stdout") or "")
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith(REPORT_MARKER):
                return json.loads(line[len(REPORT_MARKER):])
    return {"error": "the proof printed no report",
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