"""CI-10 shadow validation: re-run the full suite after a verdict lands.

A shadow run re-executes `scripts/full_suite_gate.sh` on the same tree
a pooled (or Tier-2) verdict already judged, when the machine is idle,
and diffs the outcomes against the incremental evidence's claim for
that tree: any regression the incremental evidence missed is recorded
as a test-intelligence defect (a missing edge - a selector fix or a
never-narrow rule). Shadows never block merges and never change
verdicts; they only write findings to their own ledger
(`shadow_findings.json`).

Trigger is manual first: `ren ci shadow <sha> --claim <file>`, where
the claim file is the incremental evidence's JSON
(`{"verdict": "green", "executed": [...], "reused": [...]}` - the test
files it executed vs the ones it counted green without executing).
Until CI-08 lands there is no automatic producer for that file, so the
operator supplies it (e.g. from the pooled verify step's consult
report); CI-08 will produce it. Idle-window automation waits on the
captain's C1 call (revive the ren-ci service or stay pooled-manual) -
this module schedules nothing by itself.

A shadow touches exactly two things on disk: its gate log under the
state dir's `logs/` and its findings ledger. It never writes the
queue, the checks, the main baselines, or the shared verification
cache - a matching-green shadow files nothing anywhere.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time

from library.tools.local_ci import config, hygiene, state

SHADOW_FINDINGS_FILENAME = "shadow_findings.json"

# How many findings the ledger keeps: the daemon judges one tree at a
# time, so this is headroom for the operator's history, not a working
# set - the same bound rationale as the main-baseline cache
# (state.MAIN_BASELINE_KEEP). Bounded so the ledger cannot grow with
# every shadow run.
SHADOW_FINDINGS_KEEP = 20


def _findings_path(state_dir: str) -> str:
    return os.path.join(state_dir, SHADOW_FINDINGS_FILENAME)


def read_findings(state_dir: str = "") -> list:
    """Every recorded shadow finding, oldest first. Empty when none."""
    if not state_dir:
        return []
    try:
        with open(_findings_path(state_dir), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = data.get("findings") if isinstance(data, dict) else None
    return list(entries) if isinstance(entries, list) else []


def record_finding(state_dir: str, finding: dict) -> dict:
    """Append one finding to the shadow ledger, bounded. Returns it."""
    stored = dict(finding)
    stored["recorded_at"] = time.time()
    entries = read_findings(state_dir)
    entries.append(stored)
    while len(entries) > SHADOW_FINDINGS_KEEP:
        entries.pop(0)
    os.makedirs(state_dir, exist_ok=True)
    path = _findings_path(state_dir)
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"findings": entries}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
    return stored


def check_claim(claim: dict | None) -> tuple:
    """Validate incremental evidence's claim, or refuse with the reason.

    Returns (True, normalized, "") or (False, {}, reason). A red claim
    needs no shadow: the evidence already spoke against the tree, so
    there is no green to validate. Normalized claims carry exactly
    verdict + the executed/reused file lists the diff reads.
    """
    if not isinstance(claim, dict):
        return False, {}, "shadow refused: the incremental claim is missing"
    if claim.get("verdict") != "green":
        return False, {}, (
            "shadow refused: the claim verdict is "
            f"'{claim.get('verdict') or 'missing'}', not green - a red "
            "claim needs no shadow, the evidence already spoke")
    executed = claim.get("executed", [])
    reused = claim.get("reused", [])
    if not isinstance(executed, list) or not isinstance(reused, list):
        return False, {}, (
            "shadow refused: the claim needs executed/reused file lists")
    if not executed and not reused:
        return False, {}, (
            "shadow refused: the claim names no evidence - a green "
            "claim with no executed or reused file says nothing to "
            "validate")
    return True, {"verdict": "green",
                  "executed": [str(path) for path in executed],
                  "reused": [str(path) for path in reused]}, ""


def machine_idle(state_dir: str = "") -> tuple:
    """Whether a shadow may run now: (True, "idle") or (False, reason).

    The idle-window convention, after the daemon's intake gate
    (`daemon.poll_once`'s `intake_idle` minus daemon internals a manual
    trigger has no use for): no Tier-1 job holds a slot, no full-suite
    gate holds the machine, and no queue member is mid-test. A shadow
    beside a pooled gate would fight it for cores; it waits instead.
    Reads only, through the lock's own jobs verb and the queue ledger.
    """
    root = state_dir or config.state_dir()
    jobs = hygiene.running_jobs(root)
    if jobs:
        owners = ", ".join(
            f"PR #{job['pr']}" if job.get("pr") else job.get("kind", "job")
            for job in jobs)
        return False, (f"shadow waits: Tier-1 holds the machine ({owners})")
    gates = hygiene.running_gate_jobs()
    if gates:
        return False, ("shadow waits: a full-suite gate holds the machine "
                       f"({len(gates)} grant(s) in flight)")
    testing = [entry for entry in state.read_queue(root)
               if entry.get("status") == state.ENTRY_TESTING]
    if testing:
        members = ", ".join(f"#{entry['pr']}" for entry in testing)
        return False, (f"shadow waits: Tier-2 is testing PRs {members}")
    return True, "idle"


def failed_files(outcomes: dict) -> list:
    """Test files the gate report marks red, sorted. Unknown is []."""
    return sorted(rel for rel, outcome in (outcomes or {}).items()
                  if isinstance(outcome, dict)
                  and outcome.get("failed", 0) > 0)


def diff_claim_against_gate(claim: dict, shadow: dict) -> dict:
    """Pure diff: the claim's green vs what the shadow gate judged.

    Returns {"judged": bool, "finding": dict | None}. A PASS shadow
    confirms the claim - judged, nothing filed. A FAIL shadow files
    the defect: `missed` names the red files the incremental evidence
    never executed (the missing edges - a selector fix or a
    never-narrow rule each); `executed_failed` names red files the
    evidence DID execute yet still claimed green (contradictory
    evidence, not a missed edge). Any other gate verdict (narrowed
    environment, crashed harness) judged nothing: no finding, because
    an incomplete machine is not a missed regression.
    """
    verdict = (shadow or {}).get("verdict", "")
    if verdict == "PASS":
        return {"judged": True, "finding": None}
    if verdict != "FAIL":
        return {"judged": False, "finding": None}
    failed = set(failed_files((shadow or {}).get("outcomes")))
    executed = set((claim or {}).get("executed", []))
    return {"judged": True, "finding": {
        "claimed": "green",
        "shadow_verdict": "FAIL",
        "verdict_line": (shadow or {}).get("verdict_line", ""),
        "missed": sorted(failed - executed),
        "executed_failed": sorted(failed & executed),
        "per_file": bool((shadow or {}).get("per_file", False)),
        "claim": {"verdict": "green",
                  "executed": sorted(executed),
                  "reused": sorted((claim or {}).get("reused", []))},
        "log": (shadow or {}).get("log", "")}}


@contextlib.contextmanager
def real_checkout(mirror: str, sha: str, state_dir: str):
    """The named commit as a disposable worktree, deleted on exit."""
    mirror = mirror or hygiene.ensure_mirror(
        f"https://github.com/{config.OWNER}/{config.REPO}.git", state_dir)
    hygiene.fetch_base(mirror, sha)
    with hygiene.disposable_checkout(mirror, sha, state_dir) as checkout:
        yield checkout


def real_gate(checkout: str, log_path: str, junit_path: str) -> dict:
    """The full gate on this checkout plus its per-file outcomes.

    Runs through `runner.run_gate`'s read-only `junit_out` handoff:
    per-file outcomes are SEEN, never filed - the shared verification
    cache is untouched, so a shadow manufactures no evidence for later
    incremental verdicts to reuse.
    """
    from library.tools.local_ci import result_cache, runner
    gate = runner.run_gate(checkout, log_path, junit_out=junit_path)
    outcomes = result_cache.parse_junit(junit_path)
    return {"gate": gate, "outcomes": outcomes}


def run_shadow(state_dir: str, sha: str, claim: dict | None,
               checkout_fn=None, gate_fn=None) -> dict:
    """Shadow one tree: idle-check, full gate, diff, file or nothing.

    `checkout_fn(mirror, sha, state_dir)` is a context manager yielding
    the checkout (default `real_checkout`); `gate_fn(checkout,
    log_path, junit_path)` returns {"gate": run_gate-shaped,
    "outcomes": parse_junit-shaped} (default `real_gate`). Fixtures
    script both; the gate script never runs under test.

    Returns {"ok": True, "finding": {...} | None, ...} once the shadow
    judged the tree - a filed defect or a confirmed green - and
    {"ok": False, "reason": ...} when it refused (no ledger, bad
    claim, busy machine) or judged nothing (narrowed/crashed gate).
    Only a judged FAIL writes, and only to the findings ledger: the
    queue, the checks, the baselines and the shared cache are never
    touched. Never raises on an operational failure.
    """
    if not state_dir:
        return {"ok": False, "reason": (
            "shadow refused: no state dir - a finding without a ledger "
            "files nothing")}
    if not sha:
        return {"ok": False, "reason": "shadow refused: no tree sha given"}
    valid, normalized, problem = check_claim(claim)
    if not valid:
        return {"ok": False, "reason": problem}
    idle, why = machine_idle(state_dir)
    if not idle:
        return {"ok": False, "reason": why}
    checkout_fn = checkout_fn or real_checkout
    gate_fn = gate_fn or real_gate
    log_path = os.path.join(
        state_dir, "logs", f"shadow-{sha[:12]}-{int(time.time())}.log")
    junit_path = log_path + ".junit.xml"
    try:
        os.makedirs(os.path.join(state_dir, "logs"), exist_ok=True)
        with checkout_fn("", sha, state_dir) as checkout:
            observed = gate_fn(checkout, log_path, junit_path)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"ok": False, "sha": sha,
                "reason": f"shadow could not gate {sha[:12]}: {exc}"}
    gate = (observed or {}).get("gate", {})
    outcomes = (observed or {}).get("outcomes", {})
    verdict = gate.get("verdict", "")
    from library.tools.local_ci import runner
    shadow = {"verdict": verdict,
              "verdict_line": runner.gate_verdict_line(
                  gate.get("log", log_path)),
              "outcomes": outcomes if isinstance(outcomes, dict) else {},
              "per_file": bool(outcomes),
              "log": gate.get("log", log_path)}
    compared = diff_claim_against_gate(normalized, shadow)
    if not compared["judged"]:
        return {"ok": False, "sha": sha, "verdict": verdict or "missing",
                "log": shadow["log"],
                "reason": (f"shadow judged nothing: gate verdict "
                           f"'{verdict or 'missing'}' is not a verdict on "
                           f"the tree - files nothing")}
    finding = compared["finding"]
    if finding is None:
        return {"ok": True, "sha": sha, "verdict": verdict,
                "finding": None, "log": shadow["log"]}
    finding["sha"] = sha
    stored = record_finding(state_dir, finding)
    return {"ok": True, "sha": sha, "verdict": verdict,
            "finding": stored, "log": shadow["log"]}


def summarize_findings(state_dir: str = "", limit: int = 5) -> list:
    """The newest findings for `ren ci status`, newest first."""
    return list(reversed(read_findings(state_dir or config.state_dir())))\
        [:max(0, limit)]
