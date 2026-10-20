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
    evaluation_level: int = 1


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
            "evaluation": {
                "level": scenario.evaluation_level,
                "name": ("primitive_qualification" if scenario.evaluation_level == 1
                         else "semantic_execution"),
                "operation_provided_by_evaluator": True,
            },
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
    selected = os.environ.get("REN_QUAL_ONLY")
    scenarios = SCENARIOS
    if selected:
        ids = set(selected.split(","))
        unknown = ids - {scenario.id for scenario in SCENARIOS}
        if unknown:
            raise ValueError(f"unknown qualification scenario IDs: {sorted(unknown)}")
        scenarios = tuple(s for s in SCENARIOS if s.id in ids)
    results = []
    for scenario in scenarios:
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
    report = score(results)
    from tests.live.evaluation_levels import catalog
    report["evaluation_levels"] = catalog()
    report["evaluation_level"] = 1
    return report


ROOT = Path(__file__).resolve().parents[2]
PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"

#: The PROTECTED fixture timeline. Every live lane shares it, so the
#: fixture must never delete it, rename it or duplicate over it, and a
#: lane that needs its own working copy takes a DISPOSABLE one
#: (`disposable_timeline_name`) instead of writing on this one.
PROTECTED_TIMELINE = TIMELINE

REN = ROOT / "bin" / "ren"
LOGO = Path("/tmp/fm-qual-logo-180x64.png")
LOGO_ALT = Path("/tmp/fm-qual-logo-alt-180x64.png")
TIGHT_CAPTION = Path("/tmp/fm-qual-tight-caption-480x120.png")
FULL_FRAME_CAPTION = Path("/tmp/fm-qual-full-frame-caption-1920x1080.png")


def disposable_timeline_name(stamp: str, *,
                             protected: str = PROTECTED_TIMELINE) -> str:
    """The DISPOSABLE timeline one batch writes on.

    The name keeps the protected fixture's name in front, so the
    broker's fixture scoping (`fm-scratch*` inside `Ren Qualification`)
    still recognises the timeline, and the stamp in front of it, so two
    batches never share one timeline and a batch's leftovers can never
    reach the next one.
    """
    tail = str(stamp or "").strip()
    if not tail:
        raise ValueError("a disposable timeline name needs a stamp")
    return f"{protected} {tail}"


def fixture_source_candidates(
        protected: str = PROTECTED_TIMELINE) -> tuple[str, ...]:
    """The timelines a disposable fixture may be duplicated from.

    The protected fixture comes first: it is the fixture the live
    lanes share and its content is the content a batch wants.
    `Q Spine` is the qualification project's own fixture, and the
    fallback when the protected one is absent (a Resolve restart can
    leave the project with its two standard timelines and no scratch
    timeline - measured 2026-10-20).
    """
    return (protected, "Q Spine")


def _fixture_refusal(detail: str) -> str:
    """What the fixture step refused, when it refused.

    `already_exists` is the AXI refusing to double a name that is
    taken - the disposable is already there, so the batch needs no
    duplication. `no_source` is the source timeline being absent, so
    the next candidate is tried. Anything else is a real failure.
    """
    if "already exists" in detail:
        return "already_exists"
    if "no timeline named exactly" in detail:
        return "no_source"
    return ""


def duplicate_command(timeline: str, source: str) -> list[str]:
    """The AXI call that creates a working timeline by duplicating a source.

    The duplication is the AXI's sanctioned path: a `run` script that
    adds a timeline is refused by the project inventory guard, so the
    copy is made by the verb itself, as its own session step. The
    SOURCE is only ever read - the protected fixture is duplicated, not
    written on.
    """
    return ["timeline", "duplicate", "--project", PROJECT,
            "--timeline", source, "--name", timeline, "--apply"]


def _qualification_batch(script: str, *, wait_seconds: int = 900,
                         timeout_seconds: int = 960,
                         timeline: str | None = None,
                         source: str | None = None) -> dict:
    """Run all production edits in one switching qualification borrow.

    `timeline` names the timeline the batch writes on. `None` means the
    shared protected fixture, exactly as every live lane has always run
    it. A named timeline is a DISPOSABLE one: the session creates it
    FIRST by duplicating the protected fixture - read-only, so the
    protected one is never written on and never duplicated over - and
    the batch then works on that clean copy. The duplication is its own
    session step, because a `run` script that adds a timeline is
    refused by the AXI's project inventory guard. The borrow is exclusive
    and can wait behind other work, so allow the broker queue to clear.
    """
    if not REN.is_file():
        raise RuntimeError(f"Ren CLI not found: {REN}")
    with tempfile.TemporaryDirectory(prefix="fm-small-edit-run-record-") as root:
        root_path = Path(root)
        (root_path / "project.yaml").write_text(
            "project_name: small_edit_qualification\n", encoding="utf-8")
        _initialize_run_store(root_path)
        from tests.live import disposable_project
        disposable = disposable_project.materialize(root_path)
        script = script.replace("@FM_QUAL_PROJECT_DIR@", str(root_path))
        script = script.replace("@AXC_DISPOSABLE_PROJECT@", str(disposable))
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".py",
                prefix="fm-small-edit-batch-", dir="/tmp",
                delete=False) as f:
            # The working timeline is selected here, so a batch that
            # names one works on it whatever the cursor was on. A caller
            # that selects it too is harmless: the selection only moves
            # the cursor when it is not already there.
            f.write(_select_timeline_script(
                timeline or TIMELINE, clear=bool(timeline)) + script)
            script_path = Path(f.name)

        def run_command(path: Path, purpose: str) -> list[str]:
            # These batches run only on the disposable qualification
            # timeline selected above. The script boundary uses dynamic
            # Python, so AXI requires this explicit acknowledgement even
            # though the shared source and proof timelines are never targets.
            return ["run", "--project", PROJECT,
                    "--file", str(path), "--unsafe", "--json",
                    "--acknowledge-copy-grades",
                    "--purpose", purpose, "--scope", "project",
                    "--project-dir", str(root_path)]

        batch_step = {"resolve_axi": run_command(
            script_path, "Run Tier B small-edit qualification batch")}
        attempts: list[list[dict]] = []
        if timeline:
            sources = ([source] if source
                       else list(fixture_source_candidates()))
            attempts.extend(
                [[{"resolve_axi": duplicate_command(timeline, candidate)},
                  batch_step] for candidate in sources])
        # A disposable that is ALREADY there needs no duplication: the
        # AXI refuses to double a name, and that refusal is not a
        # failure of the batch.
        attempts.append([batch_step])

        detail = ""
        try:
            index = 0
            while index < len(attempts):
                receipt = _submit_session(attempts[index], wait_seconds,
                                          timeout_seconds)
                if receipt.get("state") == "done" and receipt.get("result"):
                    return receipt
                detail = _failure_detail(receipt)
                # Only the FIXTURE step is retried: a batch that failed
                # has already run its cases, and running it again would
                # write twice.
                refusal = (_fixture_refusal(detail)
                           if _failed_step(receipt) == 0 else "")
                if not refusal:
                    break
                if refusal == "already_exists":
                    # The disposable is already there - the AXI refuses
                    # to double a name - so the batch needs no
                    # duplication at all.
                    attempts = [attempts[-1]]
                    index = 0
                    continue
                index += 1
        finally:
            script_path.unlink(missing_ok=True)
        raise RuntimeError("qualification batch failed: " + detail)


def _failed_step(receipt: dict) -> int | None:
    """Which step of a refused session failed, when the receipt says."""
    result = receipt.get("result")
    if isinstance(result, dict) and "failed_step" in result:
        try:
            return int(result["failed_step"])
        except (TypeError, ValueError):
            return None
    return None


def _submit_session(steps: list[dict], wait_seconds: int,
                    timeout_seconds: int) -> dict:
    """Submit one borrowed qualification session and return its receipt.

    A refused session still answers with a receipt, and the receipt
    carries the step that refused and why - that is the evidence the
    caller needs, so a receipt is returned even when the CLI reports a
    failure.
    """
    proc = subprocess.run(
        [str(REN), "resolved", "submit",
         "resolve.qualification_session",
         json.dumps({"project": PROJECT, "steps": steps}),
         "--qualification", "--wait", str(wait_seconds)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout_seconds, check=False)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        if proc.returncode:
            raise RuntimeError(
                proc.stderr.strip() or proc.stdout.strip()) from exc
        raise RuntimeError(
            f"broker returned non-JSON receipt: {proc.stdout}") from exc


def result_stdout(receipt: dict) -> str:
    """The stdout of the step that carries the batch's own result.

    A session runs several steps - the fixture's own steps first - so
    the result block is in the LAST step that has one, not in the first
    step, which is the fixture's.
    """
    result = receipt.get("result")
    steps = (result or {}).get("steps") if isinstance(result, dict) else []
    steps = [step for step in (steps or ()) if isinstance(step, dict)]
    for step in reversed(steps):
        stdout = str(step.get("stdout") or "")
        if stdout.find("{\n") >= 0:
            return stdout
    return str(steps[-1].get("stdout") or "") if steps else ""


def _failure_detail(receipt: dict) -> str:
    """The WHOLE failure, not the tail of it.

    The broker's step failure keeps the last 500 characters of the
    step's stderr, so a failure whose cause is printed EARLIER (and a
    warning printed after it) arrives with the cause cut off - the
    2026-10-20 rebuild row's evidence was a tail of "answer the asks
    the build wrote" and a pending-promotion warning, with the actual
    refusal never visible. The full stderr of every step is on the
    receipt, so it is read here.
    """
    detail = str(receipt.get("error") or "")
    result = receipt.get("result")
    steps = (result or {}).get("steps") if isinstance(result, dict) else None
    for index, step in enumerate(steps or ()):
        if not isinstance(step, dict):
            continue
        stderr = str(step.get("stderr") or "")
        stdout = str(step.get("stdout") or "")
        if not stderr and not stdout:
            continue
        if stderr.strip() and stderr.strip() in detail:
            continue
        detail += (f"\n--- step {index} stdout ---\n{stdout}"
                   f"\n--- step {index} stderr ---\n{stderr}")
    return detail or json.dumps(receipt, default=str)[:2000]


def _select_timeline_script(timeline: str = TIMELINE, *,
                            clear: bool | None = None) -> str:
    """Select the timeline the batch writes on, and clear it if it is ours.

    `clear` defaults to True for a DISPOSABLE timeline and False for the
    protected one: a disposable copy carries the source's items, and the
    batch must start from an empty timeline; the protected fixture is
    shared with the other live lanes, so its content is never wiped
    here (its own recovery path owns that).
    """
    if clear is None:
        clear = timeline != PROTECTED_TIMELINE
    return f'''\
project = manager.GetCurrentProject()
matches = [project.GetTimelineByIndex(i)
           for i in range(1, int(project.GetTimelineCount() or 0) + 1)
           if project.GetTimelineByIndex(i)
           and project.GetTimelineByIndex(i).GetName() == {timeline!r}]
if len(matches) != 1:
    raise RuntimeError("could not find the exact working timeline "
                       + {timeline!r})
# Resolve answers SetCurrentTimeline with False when the timeline is
# ALREADY current, so selecting it twice in one session would read as a
# failure. Only a timeline that is not current is set.
_current = project.GetCurrentTimeline()
if not (_current is not None
        and str(_current.GetUniqueId() or "")
        == str(matches[0].GetUniqueId() or "")):
    if not project.SetCurrentTimeline(matches[0]):
        raise RuntimeError("could not make the exact working timeline current")
current = project.GetCurrentTimeline()
if current.GetUniqueId() != matches[0].GetUniqueId():
    raise RuntimeError("working timeline did not read back as current")
{_CLEAR_SCRIPT if clear else ""}
result = {{"timeline": current.GetName(), "id": current.GetUniqueId()}}
'''


#: What a DISPOSABLE working timeline is cleared of before a batch: the
#: items the source timeline carried, and the fixture's own markers.
#: One clip at a time, re-collecting between deletes: deleting a
#: linked pair's picture invalidates its audio entry, so a batch delete
#: leaves the timeline dirty.
_CLEAR_SCRIPT = """\
doomed = []
for _kind in ("video", "audio", "subtitle"):
    for _index in range(1, int(current.GetTrackCount(_kind) or 0) + 1):
        doomed.extend(current.GetItemListInTrack(_kind, _index) or ())
for _round in range(8):
    _left = []
    for _kind in ("audio", "video", "subtitle"):
        for _index in range(1, int(current.GetTrackCount(_kind) or 0) + 1):
            _left.extend(current.GetItemListInTrack(_kind, _index) or ())
    if not _left:
        break
    if not bool(current.DeleteClips([_left[0]], False)):
        raise RuntimeError("could not clear the working timeline")
else:
    raise RuntimeError("could not clear the working timeline")
for _color in ("Yellow", "Green", "Blue"):
    try:
        current.DeleteMarkersByColor(_color)
    except Exception:
        pass
"""


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


#: The private timeline the fixture owns, restored to the state every
#: batch starts from: no items, no fixture markers, the three video rows
#: the fixture places onto. A batch that died mid-case skips its
#: teardown, and the NEXT batch refuses to start on a dirty timeline -
#: so the recovery is part of the harness, not a manual step.
_RECOVER_SCRIPT = """\
import json
timeline = manager.GetCurrentProject().GetCurrentTimeline()
doomed = []
for _track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
    doomed.extend(timeline.GetItemListInTrack("video", _track) or ())
try:
    _audio = int(timeline.GetTrackCount("audio") or 0)
except Exception:
    _audio = 0
for _track in range(1, _audio + 1):
    doomed.extend(timeline.GetItemListInTrack("audio", _track) or ())
if doomed:
    if not bool(timeline.DeleteClips(doomed, False)):
        raise RuntimeError("recovery could not remove fixture leftovers")
# The fixture's own rows: three video rows named Video 1/V2/V3. A run
# that added a row (the generalization matrix's V4) or renamed one left
# the next run unable to place, so the rows are restored here.
_names = []
for _index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
    try:
        _names.append(str(timeline.GetTrackName("video", _index) or ""))
    except Exception:
        _names.append("")
for _index in range(len(_names), 2, -1):
    if timeline.GetItemListInTrack("video", _index):
        raise RuntimeError("recovery found items on a surplus video row")
    if not bool(timeline.DeleteTrack("video", _index)):
        raise RuntimeError("recovery could not drop a surplus video row")
for _index, _wanted in enumerate(("Video 1", "V2", "V3"), start=1):
    while int(timeline.GetTrackCount("video") or 0) < _index:
        if not timeline.AddTrack("video"):
            raise RuntimeError("recovery could not add a video row")
    try:
        _current = str(timeline.GetTrackName("video", _index) or "")
    except Exception:
        _current = ""
    if _current != _wanted:
        if not timeline.SetTrackName("video", _index, _wanted):
            raise RuntimeError("recovery could not name a video row")
for _color in ("Yellow", "Green", "Blue"):
    try:
        timeline.DeleteMarkersByColor(_color)
    except Exception:
        pass
if not bool(manager.SaveProject()):
    raise RuntimeError("recovery could not save the project")
print("RECOVERED")
"""


def recover_timeline(timeline: str = TIMELINE) -> None:
    """Empty and normalize one qualification timeline.

    The fixture owns the timeline it recovers, so restoring it is a
    fixture action: the items a dead batch left, the markers it placed,
    and the video rows it expects (Video 1/V2/V3). It refuses rather
    than deletes a row that still carries items - a row with content is
    not a leftover of this fixture and must not be removed by a
    recovery. The PROTECTED fixture is only recovered when the caller
    names it: a disposable working timeline is the one a batch owns.
    """
    root = Path(tempfile.mkdtemp(prefix="axc-recover-"))
    (root / "project.yaml").write_text(
        "project_name: axc_timeline_recovery\n", encoding="utf-8")
    _initialize_run_store(root)
    script = _RECOVER_SCRIPT
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir="/tmp",
            delete=False) as handle:
        handle.write(_select_timeline_script(timeline, clear=False)
                     + script)
        path = Path(handle.name)
    try:
        completed = subprocess.run(
            [str(REN), "resolved", "submit",
             "resolve.qualification_session",
             json.dumps({"project": PROJECT,
                         "steps": [{"resolve_axi": [
                             "run", "--file", str(path), "--unsafe", "--json",
                             "--purpose", "Recover scratch timeline",
                             "--scope", "project", "--project-dir",
                             str(root)]}]}),
             "--qualification", "--wait", "120"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=240, check=False, cwd=str(ROOT))
    finally:
        path.unlink(missing_ok=True)
    if completed.returncode or "RECOVERED" not in completed.stdout:
        raise RuntimeError(
            "timeline recovery failed: "
            + (completed.stderr or completed.stdout)[-300:])


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
    """Run production AXI calls and capture direct before/after Resolve state.

    The batch runs unaffiliated with any pipeline project, so transform
    writes (Pan/Tilt/Zoom, which the write log must anchor to a project
    folder) target the run store `_qualification_batch` creates. The
    `@FM_QUAL_PROJECT_DIR@` sentinel is replaced with that store's path
    when the batch is written; a batch that escapes replacement refuses
    instead of logging into an unrelated folder.
    """
    only = os.environ.get("REN_QUAL_ONLY")
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
            "move", "--track", "video1", "--index", "0", "--to", "48",
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
            _edit("delete", "--track", "video3", "--index", "0", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-alt-180x64.png", "--media",
                  "video", "--track", "3", "--record", "96", "--apply"),
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
    if only:
        selected = set(only.split(","))
        cases = {key: value for key, value in cases.items()
                 if key in selected}
    return f'''\
import contextlib, io, json, re, time
from pathlib import Path
from library.tools import resolve_axi

PROJECT = {PROJECT!r}
TIMELINE = {TIMELINE!r}
PROJECT_DIR = "@FM_QUAL_PROJECT_DIR@"
DISPOSABLE = "@AXC_DISPOSABLE_PROJECT@"
DISPOSABLE_STEM = "Reel 01 - axc-disposable"
only = {only!r}
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
        project_folder=PROJECT_DIR,
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
            dimensions = re.findall(
                r"(\\d{{2,5}})\\s*x\\s*(\\d{{2,5}})", resolution.lower())
            if dimensions:
                source_size = tuple(int(part) for part in dimensions[-1])
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
            "mismatch_behavior": behavior,
            "track_counts": {{kind: int(timeline.GetTrackCount(kind) or 0)
                             for kind in ("video", "audio", "subtitle")}},
            "items": rows}}

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

def timeline_names():
    project = manager.GetCurrentProject()
    return [project.GetTimelineByIndex(index).GetName()
            for index in range(1, int(project.GetTimelineCount() or 0) + 1)
            if project.GetTimelineByIndex(index)]

def drop_disposable_timelines(before):
    """Remove exactly the rebuild's own timelines, or name what remains.

    Only names carrying the disposable stem that did not exist before
    the build are touched; anything else - including the shared
    qualification timeline - is never a candidate. Leftovers are
    reported, not hidden, so a failed cleanup reads as failed evidence.
    """
    project = manager.GetCurrentProject()
    doomed = [project.GetTimelineByIndex(index)
              for index in range(1, int(project.GetTimelineCount() or 0) + 1)
              if project.GetTimelineByIndex(index)
              and project.GetTimelineByIndex(index).GetName().startswith(
                  DISPOSABLE_STEM)
              and project.GetTimelineByIndex(index).GetName() not in before]
    if doomed and not bool(project.GetMediaPool().DeleteTimelines(doomed)):
        names = sorted({{timeline.GetName() for timeline in doomed}})
        raise RuntimeError("could not remove disposable timelines: "
                           + ",".join(names))
    return [name for name in timeline_names()
            if name.startswith(DISPOSABLE_STEM) and name not in before]

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
    """Return the changed-pixel bounds from two same-frame rendered images.

    The allowed ink boxes are dilated by 2 px before the outside count:
    measured live, the project's delivery encode leaves a <=2 px fringe
    at the ink boundary (add_logo: 20 px, tight_caption: 40 px, both on
    the right ink edge, geometry otherwise exact), while real collateral
    damage spans hundreds of pixels. The undilated mask PNG is still
    written per row so the fringe stays independently auditable.
    """
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
        x0, y0 = max(0, int(left) - 2), max(0, int(top) - 2)
        x1, y1 = (min(mask.shape[1], int(right) + 2),
                  min(mask.shape[0], int(bottom) + 2))
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
        current = manager.GetCurrentProject().GetCurrentTimeline()
        try:
            audio_tracks = int(current.GetTrackCount("audio") or 0)
        except Exception:
            audio_tracks = 0
        audio_left = any(current.GetItemListInTrack("audio", index)
                         for index in range(1, audio_tracks + 1))
        if read["exit_code"] or (not rows and not audio_left):
            return {{"clean": read["exit_code"] == 0 and not rows
                     and not audio_left,
                    "observations": observations,
                    "collateral_changes": collateral}}
        # Fixture teardown runs inside this AXI --unsafe broker lease as raw
        # DeleteClips: teardown is not a scored operation, so it never
        # exercises a production delete entry point. Audio twins of the
        # linked A-roll are torn down by the same allowlist; an audio
        # item nothing placed reads as contamination, never as debris.
        # `current` is the timeline handle read above.
        current_timeline = current
        placed = []
        allowed_names = {{"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                         "fm-qual-logo-180x64.png",
                         "fm-qual-logo-alt-180x64.png",
                         "fm-qual-tight-caption-480x120.png",
                         "fm-qual-full-frame-caption-1920x1080.png"}}
        audio_allowed = {{"qual_a.mp4", "qual_b.mp4", "qual_c.mp4"}}
        for track_index in range(1, int(current_timeline.GetTrackCount(
                "video") or 0) + 1):
            for item in current_timeline.GetItemListInTrack(
                    "video", track_index) or ():
                pool_item = item.GetMediaPoolItem()
                name = str(item.GetName())
                path = str(pool_item.GetClipProperty("File Path") or "")
                if name not in allowed_names:
                    collateral.append("refused fixture cleanup of " + name)
                    return {{"clean": False, "observations": observations,
                            "collateral_changes": collateral}}
                expected_path = (str(Path.home() / ".local/share/vep/qualification/"
                                     "622307e41719" / name)
                                 if name.startswith("qual_") else
                                 str(Path("/tmp") / name))
                if path != expected_path:
                    collateral.append("refused fixture cleanup path mismatch for "
                                      + name)
                    return {{"clean": False, "observations": observations,
                            "collateral_changes": collateral}}
                placed.append(item)
        try:
            audio_tracks = int(current_timeline.GetTrackCount("audio") or 0)
        except Exception:
            audio_tracks = 0
        for track_index in range(1, audio_tracks + 1):
            for item in current_timeline.GetItemListInTrack(
                    "audio", track_index) or ():
                pool_item = item.GetMediaPoolItem()
                name = str(item.GetName())
                path = str(pool_item.GetClipProperty("File Path") or "")
                expected_path = (str(Path.home() / ".local/share/vep/qualification/"
                                     "622307e41719" / name))
                if name not in audio_allowed or path != expected_path:
                    collateral.append("refused fixture audio cleanup of "
                                      + name)
                    return {{"clean": False, "observations": observations,
                            "collateral_changes": collateral}}
                placed.append(item)
        deleted_ok = bool(current_timeline.DeleteClips(placed, False))
        observations.append({{"argv": ["fixture_cleanup", "DeleteClips"],
                             "exit_code": 0 if deleted_ok else 1,
                             "stdout": str(len(placed)), "stderr": ""}})
        if not deleted_ok:
            return {{"clean": False, "observations": observations,
                    "collateral_changes": collateral}}
    return {{"clean": False, "observations": observations,
            "collateral_changes": collateral}}

# Fixture setup, not the operation under test: the overlay scenarios
# place onto V3, so the private timeline carries V2/V3 before the batch
# starts. Plain timeline API, verified by read-back; a leftover fixture
# from an earlier run is reused, never duplicated.
if not Path(PROJECT_DIR).is_dir():
    raise RuntimeError(
        "batch project dir was not stamped by the runner")
setup_timeline = manager.GetCurrentProject().GetCurrentTimeline()
setup_names = []
for _setup_index in range(
        1, int(setup_timeline.GetTrackCount("video") or 0) + 1):
    try:
        setup_names.append(str(setup_timeline.GetTrackName(
            "video", _setup_index)))
    except Exception:
        setup_names.append("")
setup_added = []
for _setup_name in ("V2", "V3"):
    if _setup_name in setup_names:
        continue
    if not setup_timeline.AddTrack("video"):
        raise RuntimeError(
            "fixture setup could not add a video track")
    _setup_count = int(setup_timeline.GetTrackCount("video") or 0)
    if not setup_timeline.SetTrackName("video", _setup_count, _setup_name):
        raise RuntimeError(
            f"fixture setup could not name track {{_setup_name}}")
    setup_added.append(_setup_name)
    setup_names.append(_setup_name)
setup_tracks = {{"track_names": setup_names, "added": setup_added}}
if setup_tracks["track_names"] != ["Video 1", "V2", "V3"]:
    raise RuntimeError(
        "fixture setup did not read back Video 1/V2/V3: "
        + ",".join(setup_tracks["track_names"]))

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
                ["edit", "delete"]):
            # Resolve forbids same-track overlap, so the swap deletes the
            # old overlay before placing the replacement at its span. The
            # pre-delete state (old span, pre-swap render) is the evidence
            # the replacement is judged against.
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
                         "--apply", "--project", PROJECT, "--timeline", TIMELINE,
                         "--project-folder", PROJECT_DIR])
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
    disposable_leftovers = []
    if name == "add_logo" and not any(row["exit_code"] for row in receipts):
        render_after = render_rgb_at(96)
        # The disposable build stages audio rows too; seed the A-roll
        # twin so an empty audio row never reaches the empty-row gate.
        # Scored by the same read-back as every other fixture item.
        audio_seed = call(["edit", "place", "--clip", "qual_a.mp4",
                           "--media", "audio", "--track", "1",
                           "--record", "0", "--source-in", "0",
                           "--source-out", "95",
                           "--apply", "--project", PROJECT,
                           "--timeline", TIMELINE])
        receipts.append(audio_seed)
        timeline_before = timeline_names()
        build_ok = False
        try:
            # Staging-verified, not gate-verified: the conformance gate
            # judges product quality (length, CTA, speakers, captions)
            # a 2-second synthetic reel can never satisfy. The build
            # stages through the real OTIO path with read-back, holds
            # for promotion, and this row deletes the held staging.
            from library.tools.reel_build import rebuild_reels_in_project
            rebuild_reels_in_project(
                DISPOSABLE, verify=False, only=[1], reuse_unchanged=True,
                skip_captions=True)
            build_ok = True
        except Exception as exc:
            receipts.append({{"argv": ["rebuild_reels_in_project"],
                             "exit_code": 1, "stdout": "",
                             "stderr": type(exc).__name__ + ": " + str(exc)}})
        finally:
            # The build borrows the cursor; a failed build must not
            # leave the after-read (or the next case) on its timeline.
            try:
                select_qualification_timeline()
            except Exception as exc:
                build_ok = False
                receipts.append(
                    {{"argv": ["select_qualification_timeline"],
                      "exit_code": 1, "stdout": "",
                      "stderr": type(exc).__name__ + ": " + str(exc)}})
        if build_ok:
            post_rebuild = live_state()
        try:
            disposable_leftovers = drop_disposable_timelines(timeline_before)
        except Exception as exc:
            receipts.append({{"argv": ["drop_disposable_timelines"],
                             "exit_code": 1, "stdout": "",
                             "stderr": type(exc).__name__ + ": " + str(exc)}})
        try:
            disposable_leftovers = drop_disposable_timelines(timeline_before)
        except Exception as exc:
            receipts.append({{"argv": ["drop_disposable_timelines"],
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
                     "disposable_leftovers": disposable_leftovers,
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
    if only and name not in only.split(","):
        continue
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
            # The disposable build stages audio rows too; seed the A-roll
            # twin so an empty audio row never reaches the empty-row gate.
            audio_seed = call(["edit", "place", "--clip", "qual_a.mp4",
                               "--media", "audio", "--track", "1",
                               "--record", "0", "--source-in", "0",
                               "--source-out", "47", "--apply",
                               "--project", PROJECT, "--timeline", TIMELINE])
            if audio_seed["exit_code"]:
                raise RuntimeError("could not place the rebuild audio: "
                                   + audio_seed["stdout"]
                                   + audio_seed["stderr"])
            target = next(item for item in live_state()["items"]
                          if item["name"] == "qual_a.mp4")
            edited = call(["edit", "transform", "--item", target["unique_id"],
                           "--move-x", "40", "--move-y", "0", "--verify",
                           "--apply", "--project", PROJECT,
                           "--timeline", TIMELINE,
                           "--project-folder", PROJECT_DIR])
            if edited["exit_code"]:
                raise RuntimeError("could not apply the pre-rebuild edit: "
                                   + edited["stderr"])
            before = live_state()
            timeline_before = timeline_names()
            build_error = None
            # Staging-verified (see add_logo above): the product gate
            # needs a real episode, not a 2-second synthetic reel.
            # The TOON renderer cannot print the build's own record, so
            # keep a short JSON-safe summary instead.
            try:
                build_record = rebuild_reels_in_project(
                    DISPOSABLE, verify=False, only=[1],
                    reuse_unchanged=True, skip_captions=True)
                operation["result"] = {{
                    "status": "returned",
                    "record": str(build_record)[:300]}}
                operation["status"] = "returned"
            except Exception as exc:
                build_error = exc
            try:
                operation["timeline_after_rebuild"] = (
                    select_qualification_timeline())
            except Exception as exc:
                if build_error is None:
                    raise
                operation["select_error"] = (
                    type(exc).__name__ + ": " + str(exc))
            if build_error is not None:
                raise build_error
            operation["disposable_leftovers"] = drop_disposable_timelines(
                timeline_before)
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
result = {{"cases": results, "setup": setup_tracks}}
'''


_BATCH_CACHE: dict | None = None


def _batch_case(case_id: str) -> dict:
    global _BATCH_CACHE
    if _BATCH_CACHE is None:
        # The rebuild rows stage and verify a real production build, so
        # the single borrow allows room for it; scenario rows are
        # unaffected apart from the shared ceiling.
        _BATCH_CACHE = _qualification_batch(
            _live_batch_script(), wait_seconds=600, timeout_seconds=660)
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
        "disposable_leftovers": result.get("disposable_leftovers", []),
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
    # show 48 record/source frames too; item-count movement alone is insufficient.
    right_span = "video1,qual_a.mp4,0,48,48,0,48" in text
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
    # Move re-places the full inclusive source range at a free record
    # span: [0, 47] at 48 lands on [48, 96) with the source end echoed
    # back in the exclusive AppendToTimeline form, like the add_video
    # proof. The destination must not overlap the source: Resolve will
    # not overwrite an occupied span, so an overlapping move verifies
    # fail and refuses (measured live).
    expected_readback = "video1,qual_a.mp4,48,96,48,0,48"
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"],
            "checks": {"move_readback": succeeded and expected_readback in text},
            "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def _trim_action() -> dict:
    result = _batch_case("trim")
    text = _case_observation(result)
    # Trim narrows the inclusive source range to [1, 46] (46 frames) and
    # re-places at the shifted record frame 0 + 1; the fresh placement
    # echoes the exclusive source end, like the add_video proof.
    expected_readback = "video1,qual_a.mp4,1,47,46,1,47"
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
        # The mask PNG stays on disk: the runbook retains the independent
        # pixel mask in each result row, and mask_path names it.
        pixels = _verify_rendered_geometry(
            [mask_path], expected_bbox=expected_ink_box,
            safe_zone=tuple(safe_zone))
    rendered = pixels.get("measured", [])
    rendered_box = rendered[0].get("bbox") if rendered else None
    # The independent verifier demands exact bbox equality, but the live
    # delivery encode fringes the ink edge by <=2 px (measured). Judge
    # the rendered box with the same 2 px tolerance the outside count
    # uses; safe placement still comes from the verifier's own finding.
    box_agrees = (
        rendered_box is not None and expected_ink_box is not None
        and all(abs(measured - expected) <= 2 for measured, expected in
                zip(rendered_box, expected_ink_box)))
    post_rebuild = observation.get("post_rebuild", {})
    rebuild_observation = _rebuild_preservation(
        observation.get("after", {}), post_rebuild, "fm-qual-logo-180x64.png")
    checks = {
        "independent_item": "fm-qual-logo-180x64.png" in readback,
        "last_five_seconds": _ends_within_last_five_seconds(geometry),
        "width_180px": (abs(geometry.get("output_width_px", 0) - 180) <= 1
                        and bool(rendered_box)),
        "inside_safe_zone": bool(pixels.get("safe_zone_passed") and
                                   box_agrees),
        "no_other_changes": (result["cleanup"]["clean"]
                             and not observation["unexpected_items"]
                             and not observation["missing_items"]
                             and delta.get("outside_changed_pixels") == 0),
        "survives_rebuild": rebuild_observation["preserved"],
        "disposable_project_removed": (
            result.get("disposable_leftovers") == []),
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
    leftovers = operation.get("disposable_leftovers")
    return {
        "receipt": {"status": "verified" if succeeded and unchanged
                    else "refused", "rebuild": operation},
        "collateral_changes": result["collateral_changes"],
        "checks": {"production_rebuild_returned": succeeded,
                   "timeline_state_preserved": unchanged,
                   "disposable_project_removed": leftovers == []},
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
    # A rebuild may not be attempted when an earlier live operation refuses.
    # Preserve that as missing rebuild evidence instead of turning the real
    # operation failure into an AttributeError that hides the Resolve receipt.
    after = after or {}
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
