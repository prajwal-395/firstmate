"""Tier B live qualification scenario contract and scoring helpers.

The runner is intentionally a thin layer over Ren's production edit entry
points. Scenario actions are supplied by a live adapter so this module can
also be imported by the report collector without importing Resolve.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
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


def _live_batch_script() -> str:
    """Catch each production CLI refusal and clean only our private V rows."""
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
                  "--track", "1", "--record", "95", "--source-in", "0",
                  "--source-out", "71", "--apply"),
            _edit("place", "--clip", "fm-qual-logo-180x64.png", "--media",
                  "video", "--track", "3", "--record", "47", "--apply"),
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
    receipts = []
    for argv in commands:
        receipt = call(argv)
        receipts.append(receipt)
        if receipt["exit_code"]:
            break
    expected_items = sum(
        receipt["argv"][:2] == ["edit", "place"] for receipt in receipts)
    cleaned = cleanup(expected_items)
    results.append({{"id": name, "commands": receipts, "cleanup": cleaned,
                    "collateral_changes": cleaned["collateral_changes"],
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
    return json.dumps({
        "first_refusal": refused,
        "readback": item_reads[-1] if item_reads else "",
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
    return {"receipt": {"status": "committed" if succeeded else "refused"},
            "collateral_changes": result["collateral_changes"], "checks": {
                "independent_item": "fm-qual-logo-180x64.png" in
                json.loads(text)["readback"],
                "last_five_seconds": False, "width_180px": False,
                "inside_safe_zone": False,
                "no_other_changes": result["cleanup"]["clean"],
                "survives_rebuild": False,
            }, "attempts": 1, "elapsed_seconds": result["elapsed_seconds"],
            "observed": text}


def current_main_actions() -> dict[str, Callable[[], dict]]:
    """Adapters reachable on this main revision; absent routes score fail."""
    actions = {"add_video": _add_video_action,
               "move_clip": _move_clip_action,
               "trim": _trim_action,
               "add_logo": _add_logo_action}
    return actions


def main() -> int:
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        raise SystemExit("set REN_LIVE_RESOLVE=1 to run against Resolve")
    report = run_batch(current_main_actions())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
