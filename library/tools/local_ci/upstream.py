"""Persisted daily upstream polling and weekly engine intake schedule.

The local-CI daemon owns the clock. This module deliberately provides no
background thread or separate scheduler; a due job runs only when the daemon
has drained its verification and merge-queue work.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from library.tools.local_ci import config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = _REPO_ROOT / "library/tools/upstream_engines.json"
_DAILY_SECONDS = 24 * 60 * 60

# Driver crates own the pins; the manifest mirrors them. An engine whose
# crate has not landed yet (no file) is checked for SHA shape only.
_CRATE_PIN_FILES = {
    "filmcraft": (_REPO_ROOT / "native/ren-filmcraft/src/lib.rs",
                  "FILMCRAFT_REPOSITORY", "FILMCRAFT_REVISION"),
    "effectcraft": (_REPO_ROOT / "native/ren-effectcraft/src/lib.rs",
                    "UPSTREAM", "PINNED_EFFECTCRAFT_REV"),
}


def crate_pins() -> dict:
    """Read upstream URL and qualified SHA from the Ren driver crates."""
    pins = {}
    for name, (path, url_const, rev_const) in _CRATE_PIN_FILES.items():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        url = re.search(rf'{url_const}\s*:\s*&str\s*=\s*"([^"]+)"', text)
        rev = re.search(rf'{rev_const}\s*:\s*&str\s*=\s*"([^"]+)"', text)
        if not url or not rev:
            raise RuntimeError(
                f"cannot read {url_const}/{rev_const} from {path}")
        pins[name] = {"upstream_url": url.group(1),
                      "qualified_sha": rev.group(1)}
    return pins


def assert_pins_match_crates(manifest: dict) -> dict:
    """Refuse promotion when the manifest drifts from the driver crates."""
    pins = crate_pins()
    for name, pin in pins.items():
        engine = manifest["engines"].get(name)
        if engine is None:
            raise RuntimeError(
                f"intake manifest has no {name} entry for the driver crate pin")
        if (engine.get("upstream_url") != pin["upstream_url"]
                or engine.get("qualified_sha") != pin["qualified_sha"]):
            raise RuntimeError(
                f"intake manifest {name} pin differs from the driver crate "
                "pin; update upstream_engines.json to the crate constant")
    return pins


class IntakeDeferred(RuntimeError):
    """Qualification could not reserve every local-CI verification slot."""


def _state_path(state_dir: str) -> Path:
    return Path(state_dir) / "upstream-intake.json"


@contextmanager
def _state_lock(state_dir: str):
    """Serialize persistent intake-ledger updates across daemon processes."""
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "upstream-intake.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {"schema_version": 1, "last_daily_poll": 0,
                "weekly": {}, "engines": {}}
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError(f"unsupported upstream state schema: {path}")
    return value


def _write(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _manifest() -> dict:
    with _MANIFEST.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("engines"), dict):
        raise RuntimeError("invalid upstream engine pin manifest")
    for name, engine in data["engines"].items():
        if not name or not engine.get("upstream_url", "").startswith("https://"):
            raise RuntimeError(f"invalid upstream engine entry: {name}")
        for field in ("qualified_sha", "last_seen_upstream_sha"):
            sha = engine.get(field, "")
            if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise RuntimeError(f"{name}.{field} must be a full immutable SHA")
    return data


def _remote_observation(url: str) -> dict:
    refs = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD"],
                          check=True, capture_output=True, encoding="utf-8", timeout=60)
    head = ""
    branch = ""
    for line in refs.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[0].startswith("ref: ") \
                and fields[1] == "HEAD":
            branch = fields[0][5:]
        elif len(fields) == 2 and fields[1] == "HEAD":
            head = fields[0]
    if len(head) != 40:
        raise RuntimeError(f"could not resolve upstream default HEAD for {url}")
    tags_result = subprocess.run(["git", "ls-remote", "--tags", "--refs", url],
                                 check=True, capture_output=True, encoding="utf-8",
                                 timeout=60)
    tags = []
    for line in tags_result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[1].startswith("refs/tags/") \
                and len(fields[0]) == 40:
            tags.append((fields[0], fields[1].removeprefix("refs/tags/")))
    tags.sort()
    return {"head_sha": head, "default_branch_ref": branch, "tags": tags}


def poll(state_dir: str = "") -> dict:
    """Observe upstream heads and releases without building or changing pins."""
    state_dir = state_dir or config.state_dir()
    path = _state_path(state_dir)
    state = _read(path)
    manifest = _manifest()
    observations = {}
    now = time.time()
    for name, engine in sorted(manifest["engines"].items()):
        observed = _remote_observation(engine["upstream_url"])
        entry = state.setdefault("engines", {}).setdefault(name, {"observations": []})
        previous = entry.get("last_seen_upstream_sha", engine["last_seen_upstream_sha"])
        previous_tags = {tag for _sha, tag in entry.get("last_tags", [])}
        current_tags = {tag for _sha, tag in observed["tags"]}
        new_releases = sorted(current_tags - previous_tags)
        qualified = entry.get("qualified_sha", engine["qualified_sha"])
        if observed["head_sha"] != previous or new_releases:
            entry.setdefault("observations", []).append({
                "observed_at": now, "new_releases": new_releases, **observed})
        entry["last_seen_upstream_sha"] = observed["head_sha"]
        if observed["head_sha"] != qualified:
            entry["candidate_sha"] = observed["head_sha"]
        else:
            entry.pop("candidate_sha", None)
        entry["last_poll_at"] = now
        entry["last_upstream_sha"] = observed["head_sha"]
        entry["last_tags"] = observed["tags"]
        observations[name] = observed
    with _state_lock(state_dir):
        # Merge into the latest ledger because an expedited request or a
        # concurrent poll may have completed while network refs were read.
        state = _read(path)
        for name, observed in observations.items():
            engine = manifest["engines"][name]
            entry = state.setdefault("engines", {}).setdefault(
                name, {"observations": []})
            previous = entry.get("last_seen_upstream_sha",
                                 engine["last_seen_upstream_sha"])
            previous_tags = {tag for _sha, tag in entry.get("last_tags", [])}
            current_tags = {tag for _sha, tag in observed["tags"]}
            new_releases = sorted(current_tags - previous_tags)
            qualified = entry.get("qualified_sha", engine["qualified_sha"])
            if observed["head_sha"] != previous or new_releases:
                entry.setdefault("observations", []).append({
                    "observed_at": now, "new_releases": new_releases, **observed})
            entry["last_seen_upstream_sha"] = observed["head_sha"]
            if observed["head_sha"] != qualified:
                entry["candidate_sha"] = observed["head_sha"]
            else:
                entry.pop("candidate_sha", None)
            entry["last_poll_at"] = now
            entry["last_upstream_sha"] = observed["head_sha"]
            entry["last_tags"] = observed["tags"]
        state["last_daily_poll"] = now
        _write(path, state)
    return {"observed_at": now, "engines": observations}


def week_key(now: float | None = None) -> str:
    date = dt.datetime.fromtimestamp(now or time.time(), dt.UTC).date()
    iso = date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def due(state_dir: str = "", now: float | None = None) -> dict:
    """Return schedule work due now; cadence survives daemon restarts."""
    state = _read(_state_path(state_dir or config.state_dir()))
    now = now or time.time()
    weekly = state.get("weekly", {}).get(week_key(now))
    state_engines = state.get("engines", {})
    expedited = any(bool(engine.get("expedited"))
                    for engine in state_engines.values())
    intake_due = expedited or weekly is None or weekly.get("status") in {
        "failed", "interrupted"}
    if not expedited and weekly and weekly.get("status") == "failed":
        intake_due = now - float(weekly.get("failed_at", 0)) >= 6 * 60 * 60
    if not expedited and weekly and weekly.get("status") == "deferred":
        intake_due = now - float(weekly.get("deferred_at", 0)) >= 30 * 60
    return {"poll": now - float(state.get("last_daily_poll", 0)) >= _DAILY_SECONDS,
            "intake": intake_due}


def begin_weekly(state_dir: str = "", now: float | None = None) -> str | None:
    """Record an idempotency token before work; interrupted windows retry."""
    state_dir = state_dir or config.state_dir()
    path = _state_path(state_dir)
    with _state_lock(state_dir):
        state = _read(path)
        key = week_key(now)
        previous = state.setdefault("weekly", {}).get(key)
        expedited = any(bool(engine.get("expedited"))
                        for engine in state.get("engines", {}).values())
        if previous and previous.get("status") in {"running", "complete"} \
                and (previous.get("status") == "running" or not expedited):
            return None
        token = previous.get("token") if previous else str(uuid.uuid4())
        state["weekly"][key] = {"token": token, "status": "running",
                                "started_at": time.time(), "pid": os.getpid()}
        _write(path, state)
        return token


def finish_weekly(token: str, result: dict, state_dir: str = "",
                  now: float | None = None) -> None:
    state_dir = state_dir or config.state_dir()
    path = _state_path(state_dir)
    with _state_lock(state_dir):
        state = _read(path)
        key = week_key(now)
        current = state.setdefault("weekly", {}).get(key)
        if not current or current.get("token") != token:
            raise RuntimeError("weekly intake lease changed while qualification ran")
        current.update({"status": "complete", "finished_at": time.time(),
                        "result": result})
        for engine in state.get("engines", {}).values():
            engine.pop("expedited", None)
        _write(path, state)


def recover_weekly(state_dir: str = "") -> None:
    """Leave interrupted work retryable; never mark a failed window complete."""
    path = _state_path(state_dir or config.state_dir())
    state = _read(path)
    changed = False
    for current in state.setdefault("weekly", {}).values():
        if current.get("status") != "running":
            continue
        pid = int(current.get("pid", 0))
        try:
            os.kill(pid, 0)
        except (OSError, ValueError):
            current["status"] = "interrupted"
            current["interrupted_at"] = time.time()
            changed = True
    if changed:
        _write(path, state)


@contextmanager
def _machine_admission(owner: str):
    """Serialize an intake build against every other machine-heavy job."""
    from library.tools import heavy_work_lock
    heavy_work_lock.take_heavy_lock(owner, profile="machine")
    try:
        yield
    finally:
        heavy_work_lock.release_heavy_lock()


def _run(argv: list[str], cwd: Path, log, *, timeout: int = 7200) -> dict:
    started = time.monotonic()
    result = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                            encoding="utf-8", check=False, timeout=timeout)
    return {"argv": argv, "returncode": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3)}


def _qualify_engine(name: str, engine: dict, qualified_sha: str,
                    candidate_sha: str, workspace: Path, log,
                    prior_perf: dict | None = None) -> dict:
    from library.tools.local_ci import corpus as corpus_mod
    details = {"previous_qualified_sha": qualified_sha,
               "candidate_sha": candidate_sha, "patches": [], "steps": []}
    checkout = workspace / name
    # Candidates always come from recent daily-poll observations, so a
    # shallow clone of the upstream default branch contains them; deepen to
    # a full clone only when the SHA is not reachable yet. The clone goes
    # straight to the storytold URL: no fork, no floating branch.
    cloned = _run(["git", "clone", "--depth", "50", engine["upstream_url"],
                   name], workspace, log, timeout=600)
    details["steps"].append({"name": "clone_upstream", **cloned})
    if cloned["returncode"] != 0:
        details["error"] = f"{name} upstream clone failed"
        return details
    known = {"returncode": 1}
    for _ in range(3):
        known = _run(["git", "cat-file", "-e", f"{candidate_sha}^{{commit}}"],
                     checkout, log)
        if known["returncode"] == 0:
            break
        deepened = _run(["git", "fetch", "--deepen=100"], checkout, log,
                        timeout=600)
        details["steps"].append({"name": "deepen_clone", **deepened})
        if deepened["returncode"] != 0:
            break
    details["steps"].append({"name": "resolve_immutable_sha", **known})
    if known["returncode"] != 0:
        details["error"] = (
            f"{name} candidate {candidate_sha} is not reachable from "
            "the upstream clone")
        return details
    checkout_result = _run(["git", "checkout", "--detach", candidate_sha],
                           checkout, log)
    details["steps"].append({"name": "checkout", **checkout_result})
    if checkout_result["returncode"] != 0:
        details["error"] = f"{name} source checkout failed"
        return details
    started = time.monotonic()
    resolved = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout,
                              capture_output=True, encoding="utf-8", check=False,
                              timeout=30)
    actual_sha = resolved.stdout.strip()
    log.write(f"$ git rev-parse HEAD\n{actual_sha}\n")
    step = {"argv": ["git", "rev-parse", "HEAD"],
            "returncode": resolved.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "resolved_sha": actual_sha}
    details["steps"].append({"name": "verify_sha", **step})
    if resolved.returncode != 0 or actual_sha != candidate_sha:
        details["error"] = f"{name} checkout did not match the candidate SHA"
        return details
    patch_dir = _REPO_ROOT / "library/tools/upstream_patches" / name
    for patch_file in sorted(patch_dir.glob("*.patch")):
        checked = _run(["git", "apply", "--check", str(patch_file)], checkout, log)
        if checked["returncode"] == 0:
            checked = _run(["git", "apply", str(patch_file)], checkout, log)
        details["patches"].append({"path": str(patch_file), **checked})
        if checked["returncode"] != 0:
            details["error"] = f"{name} patch replay failed: {patch_file.name}"
            return details
    package = "filmcraft-cli" if name == "filmcraft" else "effectcraft-cli"
    built = _run(["cargo", "build", "--release", "-p", package, "-j", "4"],
                 checkout, log)
    details["steps"].append({"name": "cli_build", **built})
    if built["returncode"] != 0:
        details["error"] = f"{name} CLI build failed"
        return details
    tests = ([
        ["cargo", "test", "--release", "-p", "filmcraft-interchange", "-j", "4"],
        ["cargo", "test", "--release", "-p", "filmcraft-golden", "-j", "4"],
    ] if name == "filmcraft" else [
        ["cargo", "test", "-p", "effectcraft-effects", "--lib", "-j", "4"],
        ["cargo", "test", "-p", "effectcraft-cli", "-j", "4"],
    ])
    failed_test = False
    for test_argv in tests:
        tested = _run(test_argv, checkout, log)
        details["steps"].append({"name": "upstream_tests", **tested})
        failed_test = failed_test or tested["returncode"] != 0
    if failed_test:
        details["error"] = f"{name} relevant crate tests failed"
        return details
    cli_binary = checkout / "target" / "release" / package
    if not cli_binary.is_file():
        details["error"] = f"{name} CLI binary missing after build"
        return details
    corpus_work = workspace / f"{name}-corpus"
    corpus_work.mkdir(parents=True, exist_ok=True)
    try:
        details["corpus"] = corpus_mod.run_corpus(
            {name: str(cli_binary)}, corpus_work, log,
            prior_perf=prior_perf, only={name})
    except corpus_mod.CorpusRefused as exc:
        details["corpus"] = exc.result or {"status": "failed",
                                           "error": str(exc)}
        details["error"] = f"{name} render corpus failed: {exc}"
        return details
    details["steps"].append({"name": "headless_render_corpus",
                              "status": "passed",
                              "cases": sorted(details["corpus"]["cases"])})
    return details


def _latest_perf(state_dir: str) -> dict:
    """Wall/RSS baselines from the latest passed intake record, per workload."""
    record_dir = Path(state_dir) / "upstream-intake" / "records"
    latest, latest_at = None, -1.0
    if record_dir.is_dir():
        for path in record_dir.glob("*.json"):
            try:
                with path.open(encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                continue
            if record.get("status") != "passed":
                continue
            at = float(record.get("finished_at", 0) or 0)
            if at > latest_at:
                latest, latest_at = record, at
    baselines: dict = {}
    for engine_details in (latest or {}).get("engines", {}).values():
        for workload_id, workload in (engine_details.get("corpus", {})
                                      .get("perf", {}).get("workloads", {}).items()):
            baselines[workload_id] = {
                "wall_seconds": workload.get("wall_seconds"),
                "peak_rss_mb": workload.get("peak_rss_mb")}
    return baselines


def qualify(state_dir: str = "", now: float | None = None) -> dict:
    """Build, test, and render-qualify pending engine SHAs, then record.

    Only engines with a candidate newer than their qualified pin are built.
    The pin advances only when the engine build, its upstream crate tests,
    and the committed headless render corpus all pass; failures leave the
    qualified pin unchanged and are recorded with both SHAs and the log.
    """
    state_dir = state_dir or config.state_dir()
    manifest = _manifest()
    assert_pins_match_crates(manifest)
    state_path = _state_path(state_dir)
    state = _read(state_path)
    candidates = {name: state.get("engines", {}).get(name, {}).get(
        "candidate_sha", state.get("engines", {}).get(name, {}).get(
            "qualified_sha", engine["qualified_sha"]))
        for name, engine in manifest["engines"].items()}
    qualified = {name: state.get("engines", {}).get(name, {}).get(
        "qualified_sha", engine["qualified_sha"])
        for name, engine in manifest["engines"].items()}
    pending = sorted(name for name in manifest["engines"]
                     if candidates[name] != qualified[name])
    if not pending:
        return {"status": "no_candidates", "candidates": candidates}

    run_id = dt.datetime.fromtimestamp(now or time.time(), dt.UTC).strftime(
        "%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    record_dir = Path(state_dir) / "upstream-intake" / "records"
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / f"{run_id}.json"
    log_path = Path(state_dir) / "logs" / f"upstream-intake-{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "run_id": run_id,
              "started_at": time.time(), "status": "running",
              "engines": {}, "log": str(log_path), "record": str(record_path),
              "pins": {name: {"qualified_sha": qualified[name],
                              "candidate_sha": candidates[name],
                              "last_seen_upstream_sha": state.get("engines", {}).get(
                                  name, {}).get("last_seen_upstream_sha",
                                                engine["last_seen_upstream_sha"])}
                       for name, engine in manifest["engines"].items()},
              "expedited_requests": {name: state.get("engines", {}).get(name, {}).get(
                  "expedited") for name in manifest["engines"]
                  if state.get("engines", {}).get(name, {}).get("expedited")}}
    _write(record_path, record)
    workspace = Path(state_dir) / "upstream-intake" / "work" / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    prior_perf = _latest_perf(state_dir)
    try:
        from library.tools.local_ci import hygiene, load
        with _machine_admission(f"upstream-intake:{run_id}"), \
                ExitStack() as slots_stack, \
                log_path.open("w", encoding="utf-8") as log:
            slots = hygiene.Slots(state_dir, jobs=load.TIER1_MAX)
            for _ in range(load.TIER1_MAX):
                slot = slots_stack.enter_context(slots.claim(
                    {"kind": "upstream-intake", "run_id": run_id}))
                if slot is None:
                    raise IntakeDeferred(
                        "a PR verification holds a local-CI slot")
            for name in pending:
                record["engines"][name] = _qualify_engine(
                    name, manifest["engines"][name], qualified[name],
                    candidates[name], workspace, log, prior_perf)
            failures = {name: details["error"]
                        for name, details in record["engines"].items()
                        if details.get("error")}
            record["status"] = "failed" if failures else "passed"
            if failures:
                record["error"] = failures
    except IntakeDeferred as exc:
        record["status"] = "deferred"
        record["error"] = str(exc)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        # Persist failures; the daemon retries the open weekly window.
        record["status"] = "failed"
        record["error"] = str(exc)
    record["finished_at"] = time.time()
    if record["status"] == "passed":
        current = _read(state_path)
        for name in pending:
            entry = current.setdefault("engines", {}).setdefault(name, {})
            entry["qualified_sha"] = candidates[name]
            entry["last_qualification_record"] = str(record_path)
            entry.pop("candidate_sha", None)
        _write(state_path, current)
    _write(record_path, record)
    return record


def run_due(state_dir: str = "", *, idle: bool) -> dict:
    """Run due work once from the local-CI daemon's serialized main loop."""
    state_dir = state_dir or config.state_dir()
    schedule = due(state_dir)
    outcome = {"poll": None, "intake": None}
    if schedule["poll"]:
        outcome["poll"] = poll(state_dir)
    if schedule["intake"] and idle:
        token = begin_weekly(state_dir)
        if token:
            result = qualify(state_dir)
            if result.get("status") in {"no_candidates", "passed"}:
                finish_weekly(token, result, state_dir)
            elif result.get("status") == "deferred":
                path = _state_path(state_dir)
                current = _read(path)
                entry = current["weekly"][week_key()]
                entry.update({"status": "deferred", "deferred_at": time.time(),
                              "result": result})
                _write(path, current)
            else:
                # Failed gates are visible and retryable in this week.
                path = _state_path(state_dir)
                current = _read(path)
                entry = current["weekly"][week_key()]
                entry.update({"status": "failed", "failed_at": time.time(),
                              "result": result})
                _write(path, current)
            outcome["intake"] = result
    return outcome


def request_expedited(engine_name: str, reason: str, state_dir: str = "") -> dict:
    """Flag a security/correctness fix for the same qualification gate."""
    state_dir = state_dir or config.state_dir()
    manifest = _manifest()
    if engine_name not in manifest["engines"]:
        raise ValueError(f"unknown engine {engine_name!r}; expected one of "
                         f"{', '.join(sorted(manifest['engines']))}")
    reason = reason.strip()
    if not reason:
        raise ValueError("an expedited intake request needs a reason")
    poll(state_dir)
    path = _state_path(state_dir)
    state = _read(path)
    engine = state.setdefault("engines", {}).setdefault(
        engine_name, {"observations": []})
    if not engine.get("candidate_sha"):
        raise RuntimeError(f"{engine_name} has no unqualified upstream commit to intake")
    engine["expedited"] = {"reason": reason, "requested_at": time.time()}
    _write(path, state)
    return {"engine": engine_name, "candidate_sha": engine.get("candidate_sha"),
            "reason": engine["expedited"]["reason"]}


def pin_status(state_dir: str = "") -> dict:
    """Effective qualification and last-seen SHAs for operator visibility."""
    state = _read(_state_path(state_dir or config.state_dir()))
    manifest = _manifest()
    result = {}
    for name, engine in manifest["engines"].items():
        runtime = state.get("engines", {}).get(name, {})
        result[name] = {
            "upstream_url": engine["upstream_url"],
            "qualified_sha": runtime.get("qualified_sha", engine["qualified_sha"]),
            "last_seen_upstream_sha": runtime.get(
                "last_seen_upstream_sha", engine["last_seen_upstream_sha"]),
            "candidate_sha": runtime.get("candidate_sha"),
            "last_qualification_record": runtime.get(
                "last_qualification_record", engine["last_qualification_record"]),
        }
    return result
