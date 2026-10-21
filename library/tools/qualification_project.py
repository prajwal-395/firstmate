"""Ren Qualification: the one Resolve project a live test may touch.

Live-Resolve tests used to run against whatever project was open - the
captain's. The broker now refuses a qualification job anywhere but
`QUALIFICATION_PROJECT` (`library/tools/resolved/jobs.py`); this module
is the other half: the project itself, built from synthetic media into
KNOWN timelines, so a test can assert exact frames and names.

    ren qualification media      # FREE: render the synthetic clips
    ren qualification ensure     # borrow Resolve, create ONLY what is missing
    ren qualification reset      # borrow Resolve, rebuild the project
    ren qualification qualify    # reset, then qualify ren-resolved live
    ren qualification workflow   # two full real-footage editing/export runs

Synthetic media
---------------
`MEDIA` is the recipe: ffmpeg test sources with a tone, rendered once per
machine into a directory keyed by the recipe's hash
(`media_dir()`), never into a checkout or a project. Frame counts are
exact because the rate and length are.

Known timelines
---------------
`TIMELINES` declares each timeline's clips and markers; `verify` reads
the built project back against it and names every difference. A rebuild
is DELETE + CREATE, so a reset can never inherit a previous run's edits.

Ensuring, not rebuilding
------------------------
`ensure` is the non-destructive half: it creates ONLY what is MISSING
in `QUALIFICATION_PROJECT` and never deletes, renames, replaces or
empties anything that exists. `reset` deletes the project and rebuilds
it, which is what a qualification run wants and what a project holding
the captain's proof timelines cannot have. `ensure` is for the case
where the project survives but its fixtures do not: the media pool
items, the `Q Spine` source and the empty scratch timeline the live
lane seeds. Every fixture it declines to touch is REPORTED, so a
missing one is named rather than assumed present.

WHICH project `ensure` works in is decided the way the live lane
decides it. Resolve resolves a project NAME inside the CURRENT Project
Manager folder, and the live lane opens `Ren Qualification` by name
from the folder it borrowed the captain's project from. Two projects
can answer to one name, and an ensure that opened the other one would
read a pool the lane never reads: the fixtures would read as PRESENT
while the lane still fails on the missing clip. So the current folder
wins, and an ambiguous name (several folders, none of them the
current one) is REFUSED rather than guessed.

Presence is decided by the LIVE media pool of the project being
ensured, read fresh and read back after the import. A rendered clip
that the pool does not hold is missing, however present it is on disk.

`tests/unit/resolve/test_qualification_project.py`,
`tests/unit/resolve/test_qualification_ensure.py`.

Borrowing Resolve
-----------------
The captain works in Resolve with his own project open. `borrowed`
records his open project and current timeline (by UNIQUE ID, since two
timelines may share a name), saves it, yields, and on the way out - also
when the body raised - reloads that project, makes that exact timeline
current, and READS BACK both. A restore that does not read back raises
`RestoreFailed` naming what to reopen by hand. Everything here runs
under the exclusive instance lease; nothing writes the captain's project
beyond the save.

A typed qualification broker job can call `borrowed()` under the same
exclusive lease. It records and saves the captain's current project,
marks the session for the watcher, switches to Qualification, and
restores the exact project and timeline by unique ID with read-back.
`qualification_only=True` remains available to jobs that require the
Qualification project to already be open and must not switch projects.

`tests/unit/resolve/test_qualification_project.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from library.tools.resolve_lock import under_lease
from library.tools.resolved.jobs import QUALIFICATION_PROJECT

FPS = 24
WIDTH, HEIGHT = 1920, 1080
FAILED_REPRODUCTION_TTL_DAYS = 7

#: The synthetic clips: name, seconds, ffmpeg video source, tone (Hz).
MEDIA = (
    ("qual_a", 4, "testsrc2", 440),
    ("qual_b", 3, "smptebars", 660),
    ("qual_c", 2, "color=c=0x2050c0", 880),
)

#: The known timelines. Clips go onto V1 in order; marker frames are
#: relative to the timeline's start.
TIMELINES = (
    {"name": "Q Spine", "clips": ["qual_a", "qual_b", "qual_c"],
     "markers": []},
    {"name": "Q Markers", "clips": ["qual_b"],
     "markers": [{"frame": 0, "color": "Blue", "name": "start"},
                 {"frame": 48, "color": "Green", "name": "mid"}]},
)

MEDIA_ROOT_ENV = "REN_QUALIFICATION_MEDIA"

#: The live lane's scratch timeline. The AXI fixtures seed it and
#: assert it was EMPTY before they did (tests/live/
#: editorial_live_adapter.py), so `ensure` creates it EMPTY and never
#: clears one that exists - it is a protected fixture name
#: (library/tools/proof_cleanup.py).
SCRATCH_TIMELINE = "fm-scratch qualify"

#: The timelines `ensure` guarantees, in order: the declared known
#: timeline whose SOURCE the live fixtures read, then the scratch
#: timeline the lane owns. `ensure` creates a missing one and restores
#: the declared source of an EMPTY one; a populated timeline is left
#: exactly as it is.
ENSURED_TIMELINES = ("Q Spine", SCRATCH_TIMELINE)


class QualificationError(RuntimeError):
    """The qualification environment could not be built or used."""


class RestoreFailed(QualificationError):
    """The captain's project or timeline did not read back as found."""


_BORROWED_ISOLATION: ContextVar[bool] = ContextVar(
    "qualification_project_borrowed_isolation", default=False)


@contextmanager
def _phase(layer: str, *, qualification_isolation: bool | None = None):
    """Attribute broker setup work without requiring a ledger in production."""
    from library.tools import perf_ledger
    isolated = (_BORROWED_ISOLATION.get() if qualification_isolation is None
                else qualification_isolation)
    with perf_ledger.span(layer, qualification_isolation=isolated):
        yield


# ── FREE: the media ─────────────────────────────────────────────────

def recipe_key() -> str:
    recipe = {"fps": FPS, "size": [WIDTH, HEIGHT], "media": MEDIA}
    return hashlib.sha256(json.dumps(recipe).encode("utf-8")).hexdigest()[:12]


def media_dir() -> Path:
    root = os.environ.get(MEDIA_ROOT_ENV) or str(
        Path.home() / ".local" / "share" / "vep" / "qualification")
    return Path(root) / recipe_key()


def real_media_dir() -> Path:
    """The immutable qualification copies of indexed source footage."""
    return media_dir() / "real_footage"


def copy_indexed_source(index_path: str | Path, query: str, *,
                        max_seconds: float = 300.0) -> dict:
    """Search a built footage index and copy its best short source file.

    Only the index is read to choose the source. The original media is
    opened read-only and copied byte-for-byte; an existing content-addressed
    copy is reused and never overwritten.
    """
    import hashlib

    from library.tools.analysis.footage_query import FootageIndex

    index_path = Path(index_path).expanduser().resolve()
    if not index_path.is_file():
        raise QualificationError(f"footage index is missing: {index_path}")
    index_dir = index_path.parent
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    project_folder = index_payload["project_folder"]
    index = FootageIndex(project_folder, index_dir=index_dir)
    result = index.search_report(query, top_k=20, mode="hybrid")
    if result.get("error"):
        raise QualificationError(
            f"footage index search failed: {result['error']}")

    by_clip = {}
    for row in index_payload["segments"]:
        by_clip.setdefault(row["clip_id"], row["source_file"])
    selected = None
    for hit in result["results"]:
        source = by_clip.get(hit["clip_id"])
        if not source:
            continue
        path = Path(source).expanduser()
        if not path.is_file():
            continue
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            check=False)
        if probe.returncode != 0:
            continue
        try:
            measured = json.loads(probe.stdout)
            duration = float(measured["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        stream_types = {row.get("codec_type")
                        for row in measured.get("streams", [])}
        if (0 < duration <= max_seconds
                and {"video", "audio"}.issubset(stream_types)):
            selected = (path, hit, duration)
            break
    if selected is None:
        raise QualificationError(
            f"the index search found no readable source at or below "
            f"{max_seconds:g} seconds")

    source, hit, duration = selected

    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    sha256 = file_sha256(source)
    target_dir = real_media_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}-{sha256[:12]}{source.suffix}"
    if target.exists():
        existing = file_sha256(target)
        if existing != sha256:
            raise QualificationError(
                f"content-addressed qualification copy changed: {target}")
    else:
        temporary = target.with_name(f".{target.name}.partial")
        try:
            shutil.copy2(source, temporary)
            if file_sha256(temporary) != sha256:
                raise QualificationError(
                    f"copied media hash did not match source: {source}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"index": str(index_path), "query": query,
            "clip_id": hit["clip_id"], "filename": source.name,
            "source_file": str(source), "qualification_copy": str(target),
            "duration_seconds": duration, "bytes": target.stat().st_size,
            "sha256": sha256,
            "search_hit": {key: hit[key] for key in
                           ("clip_id", "start", "end", "score", "dense")
                           if key in hit}}


def promotion_project_folder() -> Path:
    """The isolated file store for the typed live promotion qualification.

    This is deliberately beside, not inside, the engine checkout or a
    captain's project. The broker job addresses it only after Resolve
    reports the exact ``Ren Qualification`` project name.
    """
    return media_dir() / "promotion_project"


def drt_export_probe_project_folder() -> Path:
    """The isolated file store for non-current DRT export qualification."""
    return media_dir() / "drt_export_probe_project"


def frames(clip: str) -> int:
    return next(seconds for name, seconds, _, _ in MEDIA
                if name == clip) * FPS


def ensure_media() -> dict:
    """Render any missing clip; returns {clip name: path}."""
    directory = media_dir()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, seconds, source, tone in MEDIA:
        path = directory / f"{name}.mp4"
        if not path.exists():
            partial = directory / f".{name}.partial.mp4"
            command = [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i",
                (f"{source}{':' if '=' in source else '='}size={WIDTH}x"
                 f"{HEIGHT}:rate={FPS}:duration={seconds}"),
                "-f", "lavfi", "-i",
                f"sine=frequency={tone}:sample_rate=48000:duration={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-c:a", "aac", "-shortest", str(partial)]
            done = subprocess.run(command, capture_output=True,
                                  encoding="utf-8", check=False)
            if done.returncode != 0:
                raise QualificationError(
                    f"ffmpeg could not render {name}: {done.stderr.strip()}")
            partial.rename(path)
        paths[name] = path
    return paths


# ── RESOLVE: borrowing it from the captain ──────────────────────────

def _timeline_by_id(project, unique_id: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetUniqueId() == unique_id:
            return timeline
    return None


def _timeline_by_name(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    raise QualificationError(f"no timeline {name!r} in "
                             f"{project.GetName()!r}")


def _restored_receipt(found: dict) -> dict:
    """What the session actually put back, read back.

    A session that borrowed from a project Resolve cannot reopen (the
    untitled project it starts with) puts nothing back, and the receipt
    says that instead of naming a restore that never happened.
    """
    if not found.get("reopenable", True):
        return {"project": QUALIFICATION_PROJECT,
                "verified": False,
                "note": (f"the project Resolve had open ({found['project']!r}) "
                         "cannot be reopened by name, so the qualification "
                         "project was left open")}
    return {"project": found["project"],
            "timeline": found["timeline"],
            "page": found["page"],
            "verified": True}


def _reopenable(manager, name: str) -> bool:
    """Whether Resolve can reopen this project by name.

    The project Resolve opens when nothing is open - "Untitled Project"
    - is not in the project list: asking Resolve to reopen it is a load
    of a name that does not exist. A session that borrowed from a
    project like that cannot put it back, and says so instead of
    failing the run after the work.

    A manager that cannot answer the question at all is taken at its
    word: the restore is attempted, and a project that cannot be
    reopened fails the restore the way it always did.
    """
    try:
        projects = manager.GetProjectListInCurrentFolder()
    except Exception:  # noqa: BLE001 - an unanswerable manager is no evidence
        return True
    if projects is None:
        return True
    return name in list(projects)


@contextmanager
def borrowed(resolve, *, qualification_only: bool = False):
    """Use an open Qualification project, or borrow and restore the captain.

    The typed reel promotion job uses the ordinary borrow path, then
    switches to the exact Qualification project inside this context.
    `qualification_only=True` is for jobs that require Qualification to
    be open already and must not switch or restore a project or timeline.
    """
    from library.tools.resolve_lock import exclusive_held
    if not exclusive_held():
        raise QualificationError("borrowing Resolve needs the EXCLUSIVE "
                                 "instance lease")
    manager = resolve.GetProjectManager()
    if manager is None:
        raise QualificationError(
            "refusing to borrow Resolve: GetProjectManager() returned None "
            "- Resolve is mid-switch or not fully loaded; retry after it "
            "settles instead of switching projects under it")
    project = manager.GetCurrentProject()
    if not project:
        raise QualificationError("Resolve has no project open to return to")
    name = project.GetName()
    if qualification_only:
        if name != QUALIFICATION_PROJECT:
            raise QualificationError(
                f"qualification-only borrowing needs "
                f"{QUALIFICATION_PROJECT!r} open; refusing {name!r}")
        timeline = project.GetCurrentTimeline()
        found = {"project": name,
                 "timeline_id": timeline.GetUniqueId() if timeline else None,
                 "timeline": timeline.GetName() if timeline else None,
                 "page": resolve.GetCurrentPage(),
                 "qualification_only": True}
        begin_qualification_session(name)
        try:
            yield found
        finally:
            end_qualification_session()
        return
    if name == QUALIFICATION_PROJECT:
        raise QualificationError(
            f"Resolve is already on {QUALIFICATION_PROJECT!r}, so the "
            f"captain's project is unknown - reopen it by hand first")
    timeline = project.GetCurrentTimeline()
    reopenable = _reopenable(manager, name)
    with _phase("project_folder_capture", qualification_isolation=True):
        project_folder = _current_project_folder_path(manager)
    found = {"project": name,
             "project_folder": project_folder,
             "reopenable": reopenable,
             "timeline_id": timeline.GetUniqueId() if timeline else None,
             "timeline": timeline.GetName() if timeline else None,
             "page": resolve.GetCurrentPage()}
    with _phase("project_save_before_borrow",
                 qualification_isolation=True):
        # The save protects the captain's unsaved work from the reload
        # the restore does. A project Resolve cannot reopen is never
        # reloaded, so there is nothing to save either - and the untitled
        # project Resolve starts with refuses a save ("could not save
        # 'Untitled Project'; not borrowing", 2026-10-20), which is
        # what stopped every live qualification run from beginning.
        if reopenable and not manager.SaveProject():
            raise QualificationError(f"could not save {name!r}; not borrowing")
    token = _BORROWED_ISOLATION.set(True)
    try:
        begin_qualification_session(name)
    except BaseException:
        _BORROWED_ISOLATION.reset(token)
        raise
    try:
        yield found
    finally:
        try:
            with _phase("project_restore"):
                restore(resolve, found)
        finally:
            try:
                end_qualification_session()
            finally:
                _BORROWED_ISOLATION.reset(token)


#: The borrowed-session marker: a qualification session is open while
#: this file names a live borrower. The shadow watcher stands down
#: while it does - submitting `timeline.snapshot` jobs then would read
#: the wrong project and break borrowed observations, and the jobs'
#: cursor excursions would move a session they know nothing about.
#: A stale file (borrower dead) reads as no session, so a killed lane
#: never silences detection forever.
SESSION_MARKER_NAME = "ren_qualification_session.json"


def _session_marker_path() -> Path:
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / SESSION_MARKER_NAME


def begin_qualification_session(project: str) -> None:
    """Mark a borrowed session open. Called by `borrowed()` only."""
    path = _session_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(),
                                "since": time.time(),
                                "project": project}), encoding="utf-8")


def end_qualification_session() -> None:
    """Mark the borrowed session closed. Never raises."""
    try:
        _session_marker_path().unlink()
    except OSError:
        pass


def _borrower_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def qualification_session_active():
    """The live borrowed session, or None.

    Returns `{"pid", "since", "project"}` while the marker names a
    living borrower; None when unmarked, unreadable, or stale. Stale
    is a decision, not an error: the borrower died without restoring,
    and detection resumes rather than standing down forever.
    """
    try:
        record = json.loads(
            _session_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if not _borrower_alive(record.get("pid")):
        return None
    return {"pid": int(record["pid"]),
            "since": float(record.get("since", 0) or 0),
            "project": str(record.get("project", ""))}


def _run_session_step(job: dict, step: dict, index: int,
                      params: dict, resolve) -> dict:
    """Run one borrowed-session step; return its receipt, or raise."""
    from library.tools.resolved import jobs as broker_jobs
    from library.tools.resolved import ui_transaction
    from library.tools.resolved.jobs import JobRefused

    if "resolve_axi" in step:
        receipt = broker_jobs._run_resolve_axi(
            {"argv": step["resolve_axi"],
             "cwd": params.get("cwd") or os.getcwd()})
        shaped = {"runner": "resolve_axi",
                  "argv": step["resolve_axi"],
                  "exit_code": receipt["exit_code"],
                  "stdout": receipt["stdout"],
                  "stderr": receipt["stderr"]}
        if receipt["exit_code"] != 0:
            raise JobRefused(
                f"step {index} exited {receipt['exit_code']}: "
                f"{(receipt['stderr'] or receipt['stdout'])[-500:]}")
        return shaped
    subjob = {"id": f"{job['id']}-step-{index}",
              "params": step["ui_transaction"]}
    receipt = ui_transaction.run(subjob, resolve)
    shaped = {"runner": "ui_transaction",
              "receipt": broker_jobs._jsonable(receipt)}
    if receipt["status"] != "committed":
        raise JobRefused(
            f"step {index}: ui_transaction {receipt['status']}: "
            f"{receipt['result']}")
    return shaped


def run_qualification_session(job: dict, resolve) -> dict:
    """Run a submitted step list inside `borrowed()`, and return a receipt.

    The broker worker calls this with its persistent scripting
    connection - no step opens a handshake of its own, which is what
    the 2026-10-06 scripting-thread-shutdown crash forbids. Steps run in
    order inside one borrowed session: `resolve_axi` argv through the
    same runner broker `resolve_axi` jobs use, `ui_transaction` specs
    through the transaction runner with the native driver. The first
    failing step stops the run; the borrowed `finally` still restores
    (with read-back) before the refusal propagates, so a failed
    collection never strands Resolve in qualification.
    """
    from library.tools.resolved.jobs import JobRefused

    params = job["params"]
    receipts = []
    manager = resolve.GetProjectManager()
    current = manager.GetCurrentProject() if manager else None
    already_current = bool(
        current and current.GetName() == QUALIFICATION_PROJECT)
    try:
        with borrowed(resolve, qualification_only=already_current) as found:
            if not already_current:
                manager = resolve.GetProjectManager()
                switch_project(manager, QUALIFICATION_PROJECT)
            for index, step in enumerate(params["steps"]):
                try:
                    receipts.append(_run_session_step(
                        job, step, index, params, resolve))
                except JobRefused as exc:
                    # The steps that did run are evidence, not trivia: a
                    # refused collection still needs its pool inventory to
                    # diagnose. The worker records refusal results on the
                    # job, so the receipt survives the raise - and the
                    # borrowed finally still restores first.
                    raise JobRefused(str(exc), result={
                        "steps": receipts,
                        "failed_step": index,
                        "borrowed_from": found}) from exc
    except JobRefused:
        raise
    except Exception as exc:
        # The RESTORE failed after the steps ran. That is a fact about
        # the captain's project, not about the work: the steps' evidence
        # is what the run produced, and a restore that cannot put a
        # project back must not discard it (2026-10-20: every live
        # qualification run on the machine failed on the restore and
        # took its own evidence with it).
        raise JobRefused(
            f"{type(exc).__name__}: {exc}",
            result={"steps": receipts,
                    "borrowed_from": locals().get("found", {}),
                    "restore_failed": True}) from exc
    return {"steps": receipts,
            "borrowed_from": found,
            "restored": _restored_receipt(found)}


def _restore_page(resolve, found: dict) -> None:
    """Put the captain's project back on the page it was on.

    A UI action can land after the project is back (measured 2026-10-06:
    a run_command press flipped the captain's project to the media page
    post-restore), so the page is recorded on entry and read back here
    like the project and timeline are.
    """
    page = found.get("page")
    if page is None or resolve.GetCurrentPage() == page:
        return
    if not resolve.OpenPage(page) or resolve.GetCurrentPage() != page:
        raise RestoreFailed(f"the page did not read back as {page!r}; "
                            f"reopen it by hand")


def _require_live_manager(manager, where: str):
    """Refuse on a dead project-manager handle instead of switching."""
    if manager is None:
        raise QualificationError(
            f"refusing {where}: GetProjectManager() returned None - "
            f"Resolve is mid-switch or not fully loaded; retry after it "
            f"settles instead of issuing a LoadProject into the switch")


def _current_project_folder_path(manager) -> tuple[str, ...]:
    """Read the full Project Manager path and return to its starting folder.

    The leaf-to-root walk stops where Resolve reports no folder: a
    project at the Project Manager ROOT (and the untitled project
    Resolve starts with) has no folder to record and none to navigate
    back to. That is not an error - it is the root.
    """
    leaf_to_root = []
    while True:
        folder = manager.GetCurrentFolder()
        if not folder:
            # The Project Manager root has no name of its own - Resolve
            # answers it as None or as "" - so the project sits at the
            # top: there is no folder to record, and none to navigate
            # back to. Recording the empty name and then opening it is
            # the failure this replaces ("Resolve could not navigate to
            # project folder ''", measured 2026-10-20).
            break
        leaf_to_root.append(str(folder))
        if not manager.GotoParentFolder():
            break
    path = tuple(reversed(leaf_to_root))
    _open_project_folder_path(manager, path)
    return path


def _open_project_folder_path(manager, path: tuple[str, ...]) -> None:
    """Navigate from the Project Manager root to a recorded folder path."""
    if manager.GotoRootFolder() is False:
        raise QualificationError(
            "Resolve refused to navigate to the Project Manager root")
    for segment in path:
        folders = manager.GetFoldersInCurrentFolder() or []
        folders = (list(folders.values()) if isinstance(folders, dict)
                   else list(folders))
        if folders.count(segment) != 1 or manager.OpenFolder(segment) is False:
            raise QualificationError(
                f"Resolve could not navigate to project folder "
                f"{'/'.join(path)!r}")
        if manager.GetCurrentFolder() != segment:
            raise QualificationError(
                f"Resolve did not read back project folder {segment!r}")


def switch_project(manager, name: str, *, settle: float | None = None):
    """The ONE in-repo LoadProject path: already-open, live-check, settle.

    A project that is ALREADY the open one is not loaded again. The
    reload would cost a `LoadProject` and a settle window
    (`resolve_switch_guard.SETTLE_SECONDS`) to arrive at the state the
    caller already holds - and it churns the very scripting handles the
    crash-hardening discipline protects. The avoidance is counted
    (`project_switches_avoided`), so a run says how many switches it
    did NOT pay for.

    Otherwise: checks the manager handle is live before the switch,
    issues the single `LoadProject`, sleeps the switch settle, stamps
    the guard so the broker/worker keep their connections through the
    window, then reads the manager and the opened name back. A dead
    handle before or after, or a name that does not read back, raises
    instead of proceeding - on 2026-10-06 a dead handle
    (`GetProjectManager` -> None) preceded the scripting-thread-shutdown
    crash by seconds.
    """
    from library.tools import resolve_switch_guard
    _require_live_manager(manager, f"opening {name!r}")
    current = manager.GetCurrentProject()
    current_name = (current.GetName() or "") if current else ""
    if current is not None and current_name == name:
        from library.tools import perf_ledger
        perf_ledger.count_call(
            "project_switches_avoided", 1, from_project=current_name,
            to_project=name,
            edit_ids=list(perf_ledger.current_edit_ids()))
        return current
    started_at = time.time()
    try:
        with _phase("project_switch_load"):
            project = manager.LoadProject(name)
        resolve_switch_guard.note_project_switch()
        with _phase("project_switch_settle"):
            time.sleep(resolve_switch_guard.SETTLE_SECONDS
                       if settle is None else settle)
        live = None
        with _phase("project_switch_readback"):
            try:
                live = manager.GetCurrentProject()
            except Exception:  # noqa: BLE001 - a handle Resolve invalidated
                live = None
    finally:
        _record_project_switch(current_name, name, started_at)
    if live is None:
        raise QualificationError(
            f"refusing after opening {name!r}: the project manager reads "
            f"dead after the switch - Resolve may still be loading; wait "
            f"for it to settle and reopen {name!r} by hand")
    if not project or (live.GetName() or "") != name:
        raise QualificationError(
            f"{name!r} did not read back after the switch "
            f"(have {(live.GetName() or '')!r}); reopen it by hand")
    return project


def _record_project_switch(from_project: str, to_project: str,
                           started_at: float) -> None:
    """Record a switch that really happened, and how long it took.

    The count is of LOADED switches only: an avoided switch is recorded
    by `switch_project` itself as `project_switches_avoided`, so the
    ledger never claims a project was loaded when it was not.
    """
    from library.tools import perf_ledger

    perf_ledger.count_call(
        "project_switches", 1, from_project=from_project,
        to_project=to_project)
    perf_ledger.record(
        "project_switch", time.time() - started_at,
        started_at=started_at, from_project=from_project,
        to_project=to_project, switches=1,
        qualification_isolation=_BORROWED_ISOLATION.get(),
        edit_ids=list(perf_ledger.current_edit_ids()))


def restore(resolve, found: dict) -> None:
    """Reopen the found project and timeline, and read both back."""
    from library.tools.resolve_lock import assert_current_timeline
    manager = resolve.GetProjectManager()
    if manager is None:
        raise RestoreFailed(
            "GetProjectManager() returned None during restore - Resolve is "
            "mid-switch or not fully loaded; "
            f"reopen {found['project']!r} on timeline "
            f"{found['timeline']!r} by hand after it settles")
    if not found.get("reopenable", True):
        # The project Resolve had open is one Resolve cannot reopen (the
        # untitled project it starts with). There is nothing to put
        # back, and asking Resolve to load a name that does not exist
        # is the hang this avoids. The qualification project stays open
        # and the receipt says so.
        return
    current = manager.GetCurrentProject()
    if current and current.GetName() == QUALIFICATION_PROJECT:
        with _phase("project_save_before_restore"):
            if not manager.SaveProject():
                raise RestoreFailed(
                    f"could not save {QUALIFICATION_PROJECT!r} before restore")
    with _phase("project_folder_restore"):
        _open_project_folder_path(manager, found["project_folder"])
    if not current or current.GetName() != found["project"]:
        current = switch_project(manager, found["project"])
    reopen = (f"reopen {found['project']!r} on timeline "
              f"{found['timeline']!r} by hand")
    if not current or current.GetName() != found["project"]:
        raise RestoreFailed(f"{found['project']!r} did not reload; {reopen}")
    _restore_page(resolve, found)
    if found["timeline_id"] is None:
        return
    with _phase("project_timeline_restore"):
        timeline = _timeline_by_id(current, found["timeline_id"])
    if timeline is None:
        raise RestoreFailed(f"timeline id {found['timeline_id']} is gone "
                            f"from {found['project']!r}; {reopen}")
    assert_current_timeline(current, timeline)
    back = manager.GetCurrentProject()
    now = back.GetCurrentTimeline() if back else None
    if (not back or back.GetName() != found["project"] or not now
            or now.GetUniqueId() != found["timeline_id"]):
        raise RestoreFailed(f"the restore did not read back; {reopen}")


# ── RESOLVE: building the project ───────────────────────────────────

def _create_qualification_project(manager):
    """Replace Qualification in its canonical Project Manager folder.

    Resolve's project list is scoped to the current folder. The project
    we borrowed from can live several folders below the Project Manager
    root; checking there misses the existing Qualification project, and
    ``CreateProject`` can then return that stale project instead of a
    clean fixture. Its old ``Q Spine`` makes the first timeline create
    fail even though the borrow and project creation appeared to work.
    """
    if manager.GotoRootFolder() is False:
        raise QualificationError(
            "Resolve refused to navigate to the Project Manager root "
            "before rebuilding Ren Qualification")
    projects = manager.GetProjectListInCurrentFolder() or []
    if (QUALIFICATION_PROJECT in projects
            and not manager.DeleteProject(QUALIFICATION_PROJECT)):
        raise QualificationError(
            f"could not delete the old {QUALIFICATION_PROJECT!r}")
    if QUALIFICATION_PROJECT in (
            manager.GetProjectListInCurrentFolder() or []):
        raise QualificationError(
            f"the old {QUALIFICATION_PROJECT!r} still appears in the "
            "Project Manager root after deletion")
    project = manager.CreateProject(QUALIFICATION_PROJECT)
    if not project:
        raise QualificationError(f"could not create {QUALIFICATION_PROJECT!r}")
    current = manager.GetCurrentProject()
    if not current or current.GetName() != QUALIFICATION_PROJECT:
        raise QualificationError(
            f"created {QUALIFICATION_PROJECT!r}, but Resolve opened "
            f"{current.GetName() if current else None!r}")
    return project


def _declared_timeline_spec(name: str):
    """The `TIMELINES` declaration for `name`, or None."""
    return next((spec for spec in TIMELINES if spec["name"] == name), None)


def _place_declared_clips(project, pool, timeline, spec, items: dict) -> list:
    """Place a declared timeline's clips (and markers) onto it.

    ONE placement path: `rebuild` and `ensure` both land their clips
    here, so a fixture cannot be built two ways and read two ways.
    """
    from library.tools.resolve_lock import assert_current_timeline
    assert_current_timeline(project, timeline)
    if not pool.AppendToTimeline([items[c] for c in spec["clips"]]):
        raise QualificationError(f"could not place {spec['name']!r}")
    for marker in spec["markers"]:
        timeline.AddMarker(marker["frame"], marker["color"],
                           marker["name"], "", 1, "")
    return list(spec["clips"])


def rebuild(resolve, media: dict):
    """DELETE + CREATE the qualification project; returns it, open.

    Called inside `borrowed`, while the captain's project is still the
    open one - Resolve cannot delete the project it has open.
    """
    manager = resolve.GetProjectManager()
    project = _create_qualification_project(manager)
    for key, value in (("timelineFrameRate", str(FPS)),
                       ("timelineResolutionWidth", str(WIDTH)),
                       ("timelineResolutionHeight", str(HEIGHT))):
        if not project.SetSetting(key, value):
            raise QualificationError(f"could not set {key}={value}")
    pool = project.GetMediaPool()
    imported = pool.ImportMedia([str(media[name]) for name, *_ in MEDIA])
    by_name = {Path(item.GetName()).stem: item for item in imported or []}
    missing = [name for name, *_ in MEDIA if name not in by_name]
    if missing:
        raise QualificationError(f"Resolve did not import {missing}")
    for spec in TIMELINES:
        timeline = pool.CreateEmptyTimeline(spec["name"])
        if not timeline:
            raise QualificationError(f"could not create {spec['name']!r}")
        _place_declared_clips(project, pool, timeline, spec, by_name)
    if not manager.SaveProject():
        raise QualificationError(f"could not save {QUALIFICATION_PROJECT!r}")
    return project


def _timeline_problems(project, spec) -> list:
    """Every way one timeline differs from its declaration."""
    try:
        timeline = _timeline_by_name(project, spec["name"])
    except QualificationError as exc:
        return [str(exc)]
    problems = []
    items = timeline.GetItemListInTrack("video", 1) or []
    names = [Path(item.GetName()).stem for item in items]
    if names != spec["clips"]:
        problems.append(f"{spec['name']}: V1 holds {names}, "
                        f"declared {spec['clips']}")
    # GetEndFrame is EXCLUSIVE: measured live on Resolve 21.1
    # (2026-10-02), a 216-frame timeline read end - start = 216.
    length = timeline.GetEndFrame() - timeline.GetStartFrame()
    declared = sum(frames(clip) for clip in spec["clips"])
    if length != declared:
        problems.append(f"{spec['name']}: {length} frames, "
                        f"declared {declared}")
    marks = {int(frame): data for frame, data in
             (timeline.GetMarkers() or {}).items()}
    wanted = {m["frame"]: m for m in spec["markers"]}
    if sorted(marks) != sorted(wanted) or any(
            marks[f].get("name") != wanted[f]["name"] for f in wanted
            if f in marks):
        problems.append(f"{spec['name']}: markers {sorted(marks)}, "
                        f"declared {sorted(wanted)}")
    return problems


def verify(project) -> list:
    """Every way the open project differs from `TIMELINES`; [] is built."""
    problems = []
    for spec in TIMELINES:
        problems.extend(_timeline_problems(project, spec))
    return problems


# ── RESOLVE: ENSURING the missing fixtures, without destroying ────────

def _pool_items_by_stem(pool) -> dict:
    """Every media pool item's file stem -> the item, across folders.

    The pool is a TREE, and a qualification clip may sit in any bin:
    the name alone says the item exists, wherever it was filed. The
    pool's CURRENT folder is walked as well - that is where an import
    lands, and a read that could not see it would report a fixture
    that is really there as missing.
    """
    items = {}
    try:
        current = pool.GetCurrentFolder()
    except Exception:  # noqa: BLE001 - a pool that cannot say which
        current = None    # folder is current is walked without it
    stack = [pool.GetRootFolder()]
    if current is not None:
        stack.append(current)
    seen = set()
    while stack:
        folder = stack.pop()
        if folder is None or id(folder) in seen:
            continue
        seen.add(id(folder))
        for item in (folder.GetClipList() or ()):
            items.setdefault(Path(str(item.GetName())).stem, item)
        stack.extend(folder.GetSubFolderList() or ())
    return items


def _timeline_clip_names(timeline) -> list:
    """Every clip the timeline holds, on every video and audio track."""
    names = []
    for kind in ("video", "audio"):
        try:
            count = int(timeline.GetTrackCount(kind) or 0)
        except Exception:  # noqa: BLE001, S112 - a timeline that cannot
            # say is treated as holding nothing; the READ is what failed.
            continue
        for index in range(1, count + 1):
            for item in (timeline.GetItemListInTrack(kind, index) or ()):
                names.append(str(item.GetName()))
    return names


def _ensure_pool_items(pool, media: dict) -> dict:
    """Import ONLY the qualification media the pool does not hold.

    Presence is decided by the LIVE media pool of the project being
    ensured, read fresh here - never by a file on disk (a rendered
    clip that the pool does not hold is missing from the lane's point
    of view) and never by an inventory read earlier. A pool item
    already named like a qualification clip is LEFT ALONE: it may be
    the captain's own file, and this command never replaces what
    exists.

    The pool is READ BACK after the import: an import that did not
    land is not a fixture, and a report of an intention is not a
    report.
    """
    existing = _pool_items_by_stem(pool)
    present, imported = [], []
    for name, *_ in MEDIA:
        if name in existing:
            present.append(name)
            continue
        items = pool.ImportMedia([str(media[name])])
        if not items:
            raise QualificationError(
                f"Resolve did not import the missing qualification media "
                f"{name!r}")
        imported.append(name)
    live = _pool_items_by_stem(pool)
    missing = [name for name, *_ in MEDIA if name not in live]
    if missing:
        raise QualificationError(
            f"the qualification media {missing} is still absent from the "
            f"media pool after importing it")
    return {"present": present, "imported": imported, "items": live,
            "readback": sorted(name for name, *_ in MEDIA if name in live)}


def _find_timeline(project, name: str):
    """The project's timeline named exactly `name`, or None."""
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == name:
            return candidate
    return None


def _ensure_timeline(project, pool, name: str, items: dict) -> dict:
    """Create ONE missing fixture timeline; touch nothing that exists.

    A timeline that exists is never emptied, renamed or replaced. A
    declared timeline that exists but holds NO clips gets its declared
    SOURCE placed back - it held nothing, so nothing is replaced - and a
    populated one is left exactly as it is and reported.
    """
    spec = _declared_timeline_spec(name)
    timeline = _find_timeline(project, name)
    if timeline is None:
        timeline = pool.CreateEmptyTimeline(name)
        if not timeline:
            raise QualificationError(f"could not create {name!r}")
        if spec:
            placed = _place_declared_clips(project, pool, timeline, spec,
                                           items)
            return {"name": name, "status": "created", "clips": placed}
        return {"name": name, "status": "created", "clips": []}
    clips = _timeline_clip_names(timeline)
    if spec and not clips:
        placed = _place_declared_clips(project, pool, timeline, spec, items)
        return {"name": name, "status": "source_restored", "clips": placed}
    return {"name": name, "status": "present", "clips": clips}


def ensure_fixtures(project, media: dict) -> dict:
    """Create ONLY the missing qualification fixtures in this project.

    Never deletes, renames, replaces or empties anything that exists:
    a media pool item already named like a qualification clip is left
    alone, a populated timeline keeps every clip it has, and the scratch
    timeline is never cleared. Every fixture it did not create is
    REPORTED, so an absent one is named rather than assumed present.
    """
    pool = project.GetMediaPool()
    media_report = _ensure_pool_items(pool, media)
    timelines = []
    for name in ENSURED_TIMELINES:
        timelines.append(_ensure_timeline(project, pool, name,
                                          media_report["items"]))
    readback = []
    for name in ENSURED_TIMELINES:
        timeline = _find_timeline(project, name)
        readback.append({"name": name,
                         "clips": _timeline_clip_names(timeline)
                         if timeline is not None else None})
    return {"media": {"present": media_report["present"],
                      "imported": media_report["imported"],
                      "readback": media_report["readback"]},
            "timelines": timelines, "readback": readback}


def _project_locations(manager, name: str) -> list[tuple[str, ...]]:
    """EVERY Project Manager folder path that HOLDS a project called `name`.

    The project list is folder-scoped, so the same project name can live
    in two folders at once - and Resolve resolves `LoadProject(name)`
    inside the CURRENT folder, so the two are different projects that
    answer to one name. The live lane opens the qualification project by
    name from the folder it borrowed from; an ensure that picked the
    first folder in the tree could work on the OTHER one, decide
    presence against a pool the lane never reads, and report fixtures
    present that the lane cannot find.
    """
    locations: list[tuple[str, ...]] = []
    stack: list[tuple[str, ...]] = [()]
    while stack:
        path = stack.pop()
        _open_project_folder_path(manager, path)
        if name in (manager.GetProjectListInCurrentFolder() or []):
            locations.append(path)
        folders = manager.GetFoldersInCurrentFolder() or []
        folders = (list(folders.values()) if isinstance(folders, dict)
                   else list(folders))
        stack.extend(path + (folder,) for folder in folders)
    return sorted(locations)


def open_project_for_reading(manager, name: str, *, folder: str = ""):
    """Open an exact project for READING, and put the PM cursor back.

    The read-only half of the switch discipline. The project is opened
    by its EXACT name - never a near match, because a near match lands
    on somebody else's project - the Project Manager cursor is restored
    to where it was, and NOTHING is saved: a project opened for reading
    is not written.

    The name is looked up in `folder` when one is given, and in every
    Project Manager folder otherwise. A name that lives in more than one
    folder is REFUSED: Resolve resolves a name inside the CURRENT folder,
    so the two copies are different projects and the wrong one is not
    ours to open.

    This is the opening half of the reel-level export
    (`library/tools/reel_export.py`); it lives here because this module
    owns the ONE in-repo LoadProject path.

    `tests/unit/resolve/test_reel_export.py`.
    """
    _require_live_manager(manager, f"opening {name!r}")
    current_path = _current_project_folder_path(manager)
    try:
        if folder:
            path = tuple(segment for segment in
                         str(folder).replace("\\", "/").split("/")
                         if segment)
            _open_project_folder_path(manager, path)
            listed = manager.GetProjectListInCurrentFolder() or []
            if name not in listed:
                raise QualificationError(
                    f"no project named exactly {name!r} in the Project "
                    f"Manager folder {'/'.join(path) or '(the root)'!r}; "
                    f"Resolve lists {listed}")
        else:
            locations = _project_locations(manager, name)
            if not locations:
                raise QualificationError(
                    f"no project named exactly {name!r} in the Project Manager")
            if len(locations) > 1:
                where = ", ".join("/".join(item) or "(the root)"
                                  for item in locations)
                raise QualificationError(
                    f"{name!r} is filed in more than one Project Manager "
                    f"folder ({where}); name the folder to read it from")
            _open_project_folder_path(manager, locations[0])
        project = switch_project(manager, name)
        if project is None or (project.GetName() or "") != name:
            raise QualificationError(
                f"{name!r} did not read back after the switch")
        return project
    finally:
        _open_project_folder_path(manager, current_path)


def _qualification_project_folder(manager) -> tuple[str, ...]:
    """The folder the LIVE qualification project is filed in.

    The live lane opens `Ren Qualification` by name from the folder it
    borrowed the captain's project from, because that is how Resolve
    resolves a project name. An ensure that opened a DIFFERENT project
    of the same name would decide presence against a pool the lane
    never reads - the fixtures would read as present while the lane
    still fails on the missing clip.

    So the CURRENT Project Manager folder wins: it is the folder the
    live lane opens the name from. When the current folder does not
    hold the name, exactly one folder elsewhere may - and more than one
    is a REFUSAL, because the live one cannot be identified and
    guessing is the defect this replaces.
    """
    current = tuple(_current_project_folder_path(manager))
    locations = _project_locations(manager, QUALIFICATION_PROJECT)
    if not locations:
        raise QualificationError(
            f"{QUALIFICATION_PROJECT!r} is not in the Project Manager - "
            f"there is nothing to ensure fixtures in. Restore it first "
            f"(`ren qualification reset` rebuilds it from scratch).")
    if current in locations:
        return current
    if len(locations) == 1:
        return locations[0]
    where = ", ".join("/".join(path) or "(the Project Manager root)"
                      for path in locations)
    raise QualificationError(
        f"{QUALIFICATION_PROJECT!r} is filed in more than one Project "
        f"Manager folder ({where}) and the current folder "
        f"{'/'.join(current) or '(the root)'} does not hold it, so the "
        f"live one cannot be identified - rename or remove the other "
        f"copy before ensuring fixtures")


def _open_qualification_project(manager) -> tuple:
    """Open the LIVE `QUALIFICATION_PROJECT`; returns (project, folder)."""
    folder = _qualification_project_folder(manager)
    _open_project_folder_path(manager, folder)
    project = switch_project(manager, QUALIFICATION_PROJECT)
    if project is None or (project.GetName() or "") != QUALIFICATION_PROJECT:
        raise QualificationError(
            f"{QUALIFICATION_PROJECT!r} did not open from "
            f"{'/'.join(folder) or '(the Project Manager root)'}")
    return project, folder


@under_lease("ren qualification ensure")
def ensure() -> dict:
    """Borrow Resolve and create ONLY what is missing in Qualification.

    The non-destructive sibling of `reset`: the qualification project
    keeps every timeline it holds - the protected proof fixtures and
    the captain's own included - and only the absent fixtures are
    created. The captain's project and timeline are put back with a
    read-back, exactly as `borrowed` does for every other borrow.
    """
    from library.tools.marker_feedback import connect_resolve
    media = ensure_media()
    resolve = connect_resolve()
    manager = resolve.GetProjectManager()
    _require_live_manager(manager, "ensuring the qualification fixtures")
    current = manager.GetCurrentProject()
    already = bool(current and current.GetName() == QUALIFICATION_PROJECT)
    if already:
        with borrowed(resolve, qualification_only=True):
            project = current
            before = project.GetCurrentTimeline()
            report = ensure_fixtures(project, media)
            _restore_cursor(project, before)
        _save_qualification(manager)
        return {"project": QUALIFICATION_PROJECT,
                "project_folder": list(_current_project_folder_path(manager)),
                "restored": None, **report}
    with borrowed(resolve) as found:
        project, folder = _open_qualification_project(manager)
        before = project.GetCurrentTimeline()
        report = ensure_fixtures(project, media)
        _restore_cursor(project, before)
        _save_qualification(manager)
    return {"project": QUALIFICATION_PROJECT, "project_folder": list(folder),
            "restored": found, **report}


def _save_qualification(manager) -> None:
    """Persist the qualification project after an ensure.

    A fixture that was created but not saved is a fixture the next
    reader cannot find, so the save is part of the write, not a
    courtesy.
    """
    if not manager.SaveProject():
        raise QualificationError(f"could not save {QUALIFICATION_PROJECT!r}")


def _restore_cursor(project, timeline) -> None:
    """Put the cursor back on the timeline it was on before the ensure.

    Creating a fixture moves Resolve's cursor; the lane that runs next
    must find the timeline it left, not the one this command made.

    The switch goes through `resolve_lock.assert_current_timeline`, the
    ONE guarded site: it refuses without the exclusive instance lease,
    and it reads the cursor back. A caller that set the cursor itself
    moved state every other writer reads, under no guard at all.
    """
    from library.tools.resolve_lock import (
        UnguardedPlacementError,
        assert_current_timeline,
    )
    if timeline is None:
        return
    try:
        assert_current_timeline(project, timeline)
    except UnguardedPlacementError:
        # A refusal is not a read-back failure: it is the guard saying the
        # switch was never allowed. It is not swallowed.
        raise
    except Exception:  # noqa: BLE001 - a read-back failure is not a write
        return
    current = project.GetCurrentTimeline()
    if current is None or current.GetUniqueId() != timeline.GetUniqueId():
        raise QualificationError(
            f"the ensure left the cursor on {current.GetName() if current else None!r} "
            f"instead of {timeline.GetName()!r}; reopen it by hand")


@under_lease("ren qualification reset")
def reset() -> dict:
    """The one command: borrow Resolve, rebuild, verify, give it back."""
    from library.tools.marker_feedback import connect_resolve
    media = ensure_media()
    resolve = connect_resolve()
    with borrowed(resolve) as found:
        project = rebuild(resolve, media)
        problems = verify(project)
    if problems:
        raise QualificationError("the rebuilt project does not match its "
                                 "declaration: " + "; ".join(problems))
    return {"project": QUALIFICATION_PROJECT, "restored": found,
            "timelines": [spec["name"] for spec in TIMELINES]}


# ── RESOLVE: qualifying the broker ──────────────────────────────────

@contextmanager
def _broker(directory: Path):
    """A ren-resolved on its own socket, inheriting this process's lease."""
    from library.tools.resolved import client
    env = dict(os.environ, PIPELINE_RESOLVE_LOCK_DIR=str(directory),
               REN_SHADOW_DB=str(directory / "shadow.sqlite3"))
    process = subprocess.Popen(
        [sys.executable, "-m", "library.tools.resolved", "serve"], env=env,
        cwd=str(Path(__file__).resolve().parents[2]))
    path = directory / "ren-resolved.sock"
    try:
        deadline = time.time() + 30
        while client.ping(path) is None:
            if process.poll() is not None or time.time() > deadline:
                raise QualificationError("ren-resolved did not start")
            time.sleep(0.2)
        yield path
    finally:
        try:
            client.call({"op": "shutdown"}, path)
        except (ConnectionError, client.BrokerError, OSError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()             # the exact process we started
            process.wait(timeout=10)


def _check(results: list, name: str, ok: bool, detail) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def _finish_qualification_workspace(directory: Path, run_id: str, *,
                                   passed: bool, pinned: bool = False) -> dict:
    """Track the private broker files and retire them only after clean exit."""
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools.artifact_lifecycle import CLEANUP_JOB
    from library.tools.event_log import emit, emit_once

    owner = media_dir()
    from library.tools.project_layout import Area, ProjectLayout
    ProjectLayout(owner).write_dir(Area.LOGS)
    records = []
    for base, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            if (Path(base) / name).is_symlink():
                raise lifecycle.LifecycleRefused(
                    f"unknown broker workspace symlink: {Path(base) / name}")
        for name in files:
            path = Path(base) / name
            if path.suffix.lower() in {".drt", ".drp", ".otio"}:
                raise lifecycle.LifecycleRefused(
                    f"protected recovery archive in broker workspace: {path}")
            if path.is_symlink():
                raise lifecycle.LifecycleRefused(
                    f"unknown broker workspace symlink: {path}")
            identity = lifecycle.FileIdentity.capture(path)
            relative = path.relative_to(directory).as_posix()
            record = lifecycle.ArtifactRecord(
                artifact_id=lifecycle.stable_artifact_id(
                    "qualification-project", run_id, run_id,
                    "qualification_broker_working", relative),
                kind="qualification_broker_working",
                project_id="qualification-project", run_id=run_id,
                job_id=run_id, path=str(path),
                created_at=datetime.now(UTC).isoformat(), identity=identity,
                retention_class="temporary", dependencies=(),
                pinned=False,
                regenerable=True,
                regeneration_method="ren qualification qualify")
            lifecycle.register_artifact(str(owner), record)
            lifecycle.transition_artifact(
                str(owner), record.artifact_id, lifecycle.COMPLETED,
                reason="qualification broker exited")
            records.append(record)
    emit(str(owner), "qualification_run_lifecycle", task=run_id,
         run_id=run_id, job_id=run_id,
         state="completed" if passed else "failed",
         finished_at=datetime.now(UTC).isoformat(),
         workspace=str(directory), pinned=bool(pinned and not passed))
    if not passed:
        for record in records:
            lifecycle.transition_artifact(
                str(owner), record.artifact_id, lifecycle.FAILED,
                reason="qualification check failed")
            lifecycle.transition_artifact(
                str(owner), record.artifact_id, lifecycle.RECOVERY_HOLD,
                reason="failed qualification reproduction retained")
        return {"status": "recovery_hold", "workspace": str(directory),
                "artifacts": [record.artifact_id for record in records]}

    manifest_path = (owner / "qualification-workspaces"
                     / f"{run_id}-cleanup-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": run_id, "workspace": str(directory),
                "artifacts": [{"artifact_id": record.artifact_id,
                               "path": record.path,
                               "identity": record.identity.__dict__}
                              for record in records],
                "status": "planned"}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    lifecycle.schedule_cleanup_job(
        str(owner), event_id=f"qualification-project:{run_id}",
        project_id="qualification-project", run_id=run_id, job_id=run_id,
        reason="successful qualification broker workspace")
    for record in records:
        lifecycle.transition_artifact(
            str(owner), record.artifact_id, lifecycle.DISPOSABLE,
            reason="successful qualification broker workspace")
    manifest["status"] = "applying"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    removed = [lifecycle.purge_file(str(owner), record, str(directory))
               for record in records]
    for base, dirs, _ in os.walk(directory, topdown=False):
        if Path(base) == directory:
            continue
        Path(base).rmdir()
    directory.rmdir()
    manifest["status"] = "complete"
    manifest["removed"] = removed
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    emit_once(
        str(owner), CLEANUP_JOB,
        f"cleanup:qualification-project:{run_id}:complete",
        task=run_id,
        lifecycle_event_id=f"qualification-project:{run_id}",
        project_id="qualification-project", run_id=run_id, job_id=run_id,
        reason="all registered broker workspace files retired",
        state="complete")
    return {"status": "complete", "manifest": str(manifest_path),
            "removed": removed}


def set_qualification_project_pin(run_id: str, *, pinned: bool) -> dict:
    """Pin or unpin one exact failed qualification broker workspace."""
    from library.tools.event_log import emit, read_events

    owner = media_dir()
    run_events = [event for event in read_events(str(owner))
                  if event.get("type") == "qualification_run_lifecycle"
                  and event.get("fields", {}).get("run_id") == run_id]
    if not run_events:
        raise QualificationError("qualification run has no lifecycle record")
    latest = run_events[-1]["fields"]
    if latest.get("state") not in {"failed", "recovery_hold"}:
        raise QualificationError("only a failed or held run can be pinned")
    emit(str(owner), "qualification_run_lifecycle", task=run_id,
         run_id=run_id, job_id=run_id, state=latest["state"],
         workspace=latest.get("workspace"),
         finished_at=latest.get("finished_at"), pinned=bool(pinned),
         reason="explicit reproduction retention pin update")
    return {"run_id": run_id, "pinned": bool(pinned)}


def _expire_failed_qualification_workspaces() -> list[dict]:
    """Expire only registered, unpinned broker files after seven days."""
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools.event_log import emit, read_events

    owner = media_dir()
    latest_by_run = {}
    for event in read_events(str(owner)):
        if event.get("type") == "qualification_run_lifecycle":
            fields = event.get("fields", {})
            if fields.get("workspace"):
                latest_by_run[fields["run_id"]] = fields
    results = []
    for run_id, run in latest_by_run.items():
        if (run.get("state") != "failed" or run.get("pinned")
                or not run.get("finished_at")):
            continue
        finished = datetime.fromisoformat(run["finished_at"])
        if (datetime.now(UTC) - finished).total_seconds() \
                < FAILED_REPRODUCTION_TTL_DAYS * 86400:
            continue
        workspace = Path(run["workspace"]).resolve()
        if (workspace.parent != Path("/tmp") or not workspace.is_dir()
                or not workspace.name.startswith(f"rq-{run_id}-")):
            results.append({"run_id": run_id, "status": "refused",
                            "detail": "recorded workspace identity is invalid"})
            continue
        try:
            records = lifecycle._latest_artifacts(str(owner))
            owned = {artifact_id: row for artifact_id, row in records.items()
                     if row.get("run_id") == run_id
                     and row.get("kind") == "qualification_broker_working"}
            owned_paths = {Path(row["path"]).resolve()
                           for row in owned.values()}
            unknown = []
            for base, dirs, files in os.walk(workspace, followlinks=False):
                for name in dirs + files:
                    path = Path(base) / name
                    if (path.is_symlink()
                            or (path.is_file()
                                and path.resolve() not in owned_paths)):
                        unknown.append(str(path))
            if unknown:
                raise lifecycle.LifecycleRefused(
                    f"unregistered workspace resources: {unknown[:3]}")
            manifest_path = (owner / "qualification-workspaces"
                             / f"{run_id}-cleanup-manifest.json")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest = {"run_id": run_id, "workspace": str(workspace),
                        "artifacts": [{"artifact_id": artifact_id,
                                       "path": row["path"],
                                       "identity": row["identity"]}
                                      for artifact_id, row in owned.items()],
                        "status": "planned"}
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                                     encoding="utf-8")
            removed = []
            for artifact_id, row in owned.items():
                if row["state"] != lifecycle.RECOVERY_HOLD:
                    continue
                record_data = {key: value for key, value in row.items()
                               if key in lifecycle.ArtifactRecord.__dataclass_fields__}
                record_data["identity"] = lifecycle.FileIdentity(
                    **record_data["identity"])
                record_data["dependencies"] = tuple(record_data["dependencies"])
                record = lifecycle.ArtifactRecord(**record_data)
                lifecycle.transition_artifact(
                    str(owner), artifact_id, lifecycle.DISPOSABLE,
                    reason="failed qualification reproduction retention expired")
                removed.append(lifecycle.purge_file(
                    str(owner), record, str(workspace)))
            for base, _, _ in os.walk(workspace, topdown=False):
                if Path(base) != workspace:
                    Path(base).rmdir()
            workspace.rmdir()
            manifest["status"] = "complete"
            manifest["removed"] = removed
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                                     encoding="utf-8")
            emit(str(owner), "qualification_run_lifecycle", task=run_id,
                 run_id=run_id, job_id=run_id, state="expired",
                 finished_at=run["finished_at"], pinned=False,
                 reason="unpinning retention period elapsed")
            results.append({"run_id": run_id, "status": "expired",
                            "removed": removed})
        except Exception as exc:  # noqa: BLE001 - unknown state stays held
            results.append({"run_id": run_id, "status": "refused",
                            "detail": str(exc)})
    return results


def qualify_broker(resolve, project, socket: Path) -> list:
    """Each broker property, judged against the live qualification project."""
    from library.tools.resolve_lock import assert_current_timeline
    from library.tools.resolved import client
    from library.tools.resolved.jobs import JobRefused, prepare
    results = []
    spine = _timeline_by_name(project, "Q Spine")
    assert_current_timeline(project, spine)

    def submit(kind, params, **kw):
        return client.submit(kind, params, path=socket, **kw)

    def result(job_id):
        return client.result(job_id, wait=120, path=socket)

    # Coalescing: identical reads submitted while a grant holds Resolve.
    with client.grant({"purpose": "qualify: hold the queue"}, path=socket):
        snap = [submit("timeline.snapshot",
                       {"project": QUALIFICATION_PROJECT,
                        "timeline": "Q Markers"}) for _ in range(2)]
        axi = [submit("resolve_axi",
                      {"argv": ["items", "--timeline", "Q Spine",
                                "--refresh-live"],
                       "cwd": os.getcwd()}) for _ in range(2)]
    _check(results, "identical snapshots coalesce",
           snap[1] == {"id": snap[0]["id"], "coalesced": True}, snap)
    base = result(snap[0]["id"])
    _check(results, "snapshot records a generation",
           base["state"] == "done" and base["result"]["generation"] >= 1,
           base)
    if base["state"] != "done":
        return results          # every check below patches on this base
    axi_receipt = result(axi[0]["id"])
    _check(results, "identical resolve-axi reads coalesce",
           axi[1]["coalesced"] and axi_receipt["state"] == "done"
           and axi_receipt["result"]["exit_code"] == 0
           and "qual_a" in axi_receipt["result"]["stdout"],
           {"submits": axi, "receipt": axi_receipt})

    generation = base["result"]["generation"]
    patch = {"id": f"qualify-{int(time.time())}",
             "project": QUALIFICATION_PROJECT, "timeline": "Q Markers",
             "base_generation": generation, "capability": "reel.touchup",
             "affected_spans": [[24, 25]], "conflict_domains": ["markers"],
             "operations": [{"op": "marker.add", "frame": 24,
                             "color": "Yellow", "name": "qualify"}],
             "preconditions": [{"kind": "marker_absent", "frame": 24}],
             "postconditions": [{"kind": "duration_unchanged"}]}
    applied = result(submit("timeline.apply_patch", {"patch": patch})["id"])
    markers = _timeline_by_name(project, "Q Markers").GetMarkers() or {}
    _check(results, "a patch commits and reads back",
           applied["state"] == "done"
           and applied["result"]["status"] == "committed"
           and any(int(f) == 24 for f in markers),
           {"receipt": applied, "markers": sorted(int(f) for f in markers)})
    _check(results, "the broker puts the cursor back",
           project.GetCurrentTimeline().GetUniqueId() == spine.GetUniqueId(),
           project.GetCurrentTimeline().GetName())

    stale = dict(patch, id=patch["id"] + "-stale",
                 operations=[{"op": "marker.add", "frame": 24,
                              "color": "Red", "name": "stale"}])
    refused = result(submit("timeline.apply_patch", {"patch": stale})["id"])
    _check(results, "a stale patch is rejected with the head",
           refused["state"] == "rejected"
           and refused["result"]["refusal"] == "StalePatch",
           refused)

    try:
        with client.grant({"purpose": "qualify: a test section",
                           "project": QUALIFICATION_PROJECT},
                          qualification=True, wait=60, path=socket):
            granted = True
    except client.BrokerError as exc:
        granted = str(exc)
    _check(results, "a qualification grant runs on Ren Qualification",
           granted is True, granted)
    try:
        prepare("timeline.snapshot", {"project": "Podcast (field test)",
                                      "timeline": "Reel 01"},
                qualification=True)
        refused_user = False
    except JobRefused:
        refused_user = True
    _check(results, "a qualification job on a user project is refused",
           refused_user, None)

    receipts = client.call({"op": "list", "limit": 50}, socket)["jobs"]
    _check(results, "every finished job has a receipt with its costs",
           all(job["wait_seconds"] is not None
               and job["hold_seconds"] is not None for job in receipts
               if job["state"] in ("done", "rejected")),
           [{k: job[k] for k in ("kind", "state", "priority", "subscribers",
                                 "wait_seconds", "hold_seconds")}
            for job in receipts])
    return results


@under_lease("ren qualification qualify")
def qualify(*, pin_failure: bool = False) -> dict:
    """Reset the project, then qualify ren-resolved against it, live."""
    from library.tools.marker_feedback import connect_resolve
    media = ensure_media()
    expired_workspaces = _expire_failed_qualification_workspaces()
    run_id = uuid.uuid4().hex
    directory = Path(tempfile.mkdtemp(prefix=f"rq-{run_id}-", dir="/tmp"))
    from library.tools.event_log import emit
    emit(str(media_dir()), "qualification_run_lifecycle", task=run_id,
         run_id=run_id, job_id=run_id, state="active",
         workspace=str(directory), started_at=datetime.now(UTC).isoformat(),
         pinned=bool(pin_failure))
    found, problems, results = {}, [], []
    cleanup = {"status": "not_started"}
    try:
        resolve = connect_resolve()
        with borrowed(resolve) as found:
            project = rebuild(resolve, media)
            problems = verify(project)
            results = []
            if not problems:
                with _broker(directory) as socket:
                    results = qualify_broker(resolve, project, socket)
    finally:
        passed = not problems and bool(results) and all(
            result["ok"] for result in results)
        try:
            cleanup = _finish_qualification_workspace(
                directory, run_id, passed=passed,
                pinned=bool(pin_failure and not passed))
        except Exception as exc:  # noqa: BLE001 - ownership uncertainty holds
            cleanup = {"status": "recovery_hold", "detail": str(exc),
                       "workspace": str(directory)}
            emit(str(media_dir()), "qualification_run_lifecycle", task=run_id,
                 run_id=run_id, job_id=run_id, state="recovery_hold",
                 workspace=str(directory), pinned=bool(pin_failure),
                 finished_at=datetime.now(UTC).isoformat(),
                 reason="workspace cleanup could not prove safe retirement")
    return {"restored": found, "build_problems": problems,
            "checks": results,
            "run_id": run_id, "cleanup": cleanup,
            "expired_workspaces": expired_workspaces,
            "passed": not problems and bool(results)
            and all(r["ok"] for r in results)}


def main(argv=None) -> int:
    import argparse

    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "workflow":
        from library.tools.qualification_workflow import main as workflow_main
        return workflow_main(argv[1:])
    if argv and argv[0] == "pm-e2e":
        from library.tools.project_model_qualification import main as pm9_main
        return pm9_main(argv[1:])
    parser = argparse.ArgumentParser(
        prog="ren qualification",
        description=f"The {QUALIFICATION_PROJECT!r} project live tests use.")
    parser.add_argument("verb", choices=("media", "ensure", "reset", "qualify",
                                         "workflow", "pm-e2e"))
    parser.add_argument("--pin-failure", action="store_true",
                        help="retain failed qualification broker files")
    parser.add_argument("--unpin-failure",
                        help="release a failed broker workspace pin by run id")
    args = parser.parse_args(argv)
    if args.unpin_failure:
        try:
            result = set_qualification_project_pin(
                args.unpin_failure, pinned=False)
        except Exception as exc:  # noqa: BLE001 - command reports refusal
            print(json.dumps({"pinned": False, "error": str(exc)}, indent=2),
                  file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.verb == "media":
        report = {name: str(path) for name, path in ensure_media().items()}
    elif args.verb == "ensure":
        report = ensure()
    elif args.verb == "reset":
        report = reset()
    else:
        report = qualify(pin_failure=args.pin_failure)
    print(json.dumps(report, indent=2, default=str))
    return 0 if args.verb != "qualify" or report["passed"] else 1


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
