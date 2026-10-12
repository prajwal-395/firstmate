"""`ren worker manifest`: this machine's Ren Fabric worker advertisement, as JSON.

The spec's worker block (machine, resolve, capabilities, plugins, assets,
state) is assembled here from the probes Ren ALREADY runs rather than new
ones:

* machine   - `ren doctor`'s own checks (`run_checks`), which read the OS,
              unified memory and free disk.
* resolve   - the facts the broker records (`fusion.registry_summary`,
              cached per machine by `resolvefx_effects`), plus the
              scripting/edition verdict from doctor's Resolve probe.
* capabilities - `machine_needs.availability` over every capability id,
              given the needs doctor's checks report as failing and the
              needs those checks could not determine (UNKNOWN, not
              UNAVAILABLE - a check that could not run is not a missing
              need).
* models    - `ren doctor`'s HuggingFace/torch checkpoint probes.
* fonts     - `render_fonts`' bundled and accepted-system families.
* state     - whether the broker reports a running job.

Read-only: the manifest changes nothing on this machine and starts nothing.
It is the advertisement half of the Fabric; the scheduler that matches a
task's requirements against it is `worker_requirements` (the matcher), and
the cross-machine registry is the scheduler item's.

    ren worker manifest            print this worker's manifest as JSON
    ren worker manifest --check    exit 1 when a required need is unmet
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import sys
from pathlib import Path


def _worker_id() -> str:
    """This worker's identity: the machine's hostname, namespaced."""
    try:
        host = socket.gethostname()
    except OSError:  # pragma: no cover - a machine with no hostname
        host = "unknown"
    return f"ren-worker-{host}" if host else "ren-worker-unknown"


def _doctor():
    from ren import doctor
    return doctor


def _machine_section() -> dict:
    """OS, architecture, unified memory and free disk, from doctor's probes."""
    doctor = _doctor()
    os_name = platform.system()
    os_detail = ""
    if os_name == "Darwin":
        os_detail = f"macOS {platform.mac_ver()[0]}"
    elif os_name:  # pragma: no cover - Ren runs on macOS only
        os_detail = f"{os_name} {platform.release()}"
    memory_gb = doctor._total_memory_gb()
    home = str(Path.home())
    disk_free = doctor.free_bytes(Path(home))
    return {
        "os": os_detail or os_name,
        "arch": platform.machine(),
        "memory_gb": round(memory_gb, 1) if memory_gb is not None else None,
        "disk_free_bytes": disk_free,
    }


def _resolve_section(checks: list, resolve_cache: dict | None) -> dict:
    """Resolve version and plugins from the broker's cache; scripting and
    edition from doctor's own probe checks.

    The cache is the broker's recorded fact and reads offline; the two
    needs are the live probe's verdict, so a machine with Resolve down
    still advertises the version it last recorded and says scripting is
    false rather than inventing a connection.
    """
    version = ""
    plugins: list = []
    if resolve_cache:
        version = str(resolve_cache.get("resolve_version") or "")
        for entry in resolve_cache.get("entries", []):
            if isinstance(entry, dict) and entry.get("name"):
                plugins.append({
                    "id": entry.get("id") or "",
                    "name": entry.get("name") or "",
                })

    scripting = False
    edition = "unknown"
    for check in checks:
        if check.need == "resolve.scripting":
            scripting = check.ok
        elif check.need == "resolve.studio":
            if check.ok:
                edition = "Studio"
            elif scripting:
                edition = "free"
            else:
                edition = "unknown"
    return {
        "version": version,
        "edition": edition,
        "scripting": scripting,
        "plugins": plugins,
    }


def _capability_section(failing: set, undetermined: set) -> dict:
    """Every capability id and its verdict, from `machine_needs.availability`.

    The undetermined needs travel with the failing ones: a required need
    no verdict could be reached for makes the capability UNKNOWN, not
    UNAVAILABLE - the advertisement must not claim a worker lacks a need
    its checks could not read.
    """
    from library.tools import machine_needs
    return {
        capability: machine_needs.availability(
            capability, failing, undetermined)[0]
        for capability in sorted(machine_needs.CAPABILITY_NEEDS)
    }


def _model_section(cache: Path | None = None) -> dict:
    """Each model Ren fetches, from doctor's own checkpoint probes.

    Complete means a snapshot holds a real weights file and no download
    is left half-finished (`ren doctor.hf_model_state`); the bytes are
    the cache's measured size. An absent model is reported incomplete
    with its size, never omitted - a scheduler has to see the gap.
    """
    doctor = _doctor()
    if cache is None:
        cache = doctor.hf_hub_cache()
    models: dict = {}
    for label, _reader, ident, _need in doctor.MODELS:
        complete, size, _why = doctor.hf_model_state(ident, cache)
        models[label] = {"complete": complete, "bytes": size}

    # The footage-search embedder doctor checks outside MODELS (optional by
    # design: lexical-only search remains when it cannot load).
    embedder = "sentence-transformers/all-MiniLM-L6-v2"
    complete, size, _why = doctor.hf_model_state(embedder, cache)
    models["all-MiniLM-L6-v2"] = {"complete": complete, "bytes": size}

    # The optional models doctor checks outside the HuggingFace cache:
    # beat_this (torch checkpoint) and PANNs (shared_environment).
    bt_path = doctor.torch_checkpoints() / "beat_this-final0.ckpt"
    models["beat_this-final0.ckpt"] = {
        "complete": bt_path.is_file(),
        "bytes": bt_path.stat().st_size if bt_path.is_file() else 0,
    }
    from library.tools import shared_environment
    panns_ok, _ = shared_environment.panns_available()
    panns_path = shared_environment.panns_checkpoint()
    models["PANNs-Cnn14-DLM"] = {
        "complete": panns_ok,
        "bytes": panns_path.stat().st_size if panns_path.is_file() else 0,
    }
    return models


def _font_section() -> list:
    """The typefaces this worker can draw: the bundled family plus the
    accepted system families (`render_fonts`' one enumeration)."""
    from library.tools import render_fonts
    fonts = [render_fonts.BUNDLED_FONT_FAMILY]
    fonts += sorted(render_fonts.ACCEPTED_SYSTEM_FONTS)
    return fonts


def _broker_running() -> bool:
    """Whether the broker reports a job currently running.

    Read-only and best-effort: a broker that will not answer is not a
    running job, so the manifest still reports a state.
    """
    try:
        from library.tools.resolved import client
        if not client.serving():
            return False
        listed = client.call({"op": "list", "limit": 1})
        return bool(listed.get("running"))
    except Exception:  # noqa: BLE001 - the manifest must never crash on state
        return False


def produce_manifest(
    *,
    checks: list | None = None,
    resolve_cache: dict | None = None,
    broker_running: bool | None = None,
    model_cache: Path | None = None,
) -> dict:
    """Assemble this worker's manifest from the probes Ren already runs.

    Every probe is injectable so a test can pin the manifest against a
    fixture machine; the defaults are the live probes.
    """
    if checks is None:
        checks = _doctor().run_checks()
    if resolve_cache is None:
        from library.tools import resolvefx_effects
        resolve_cache = resolvefx_effects._read_cache()
    if broker_running is None:
        broker_running = _broker_running()

    failing = {check.need for check in checks
               if not check.ok and not check.undetermined and check.need}
    undetermined = {check.need for check in checks
                    if check.undetermined and check.need}
    return {
        "worker": _worker_id(),
        "machine": _machine_section(),
        "resolve": _resolve_section(checks, resolve_cache),
        "capabilities": _capability_section(failing, undetermined),
        "models": _model_section(model_cache),
        "fonts": _font_section(),
        "state": "busy" if broker_running else "idle",
    }


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren worker",
        description="This machine's Ren Fabric worker: its manifest, and "
                    "whether a task's requirements match it.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    manifest = verbs.add_parser(
        "manifest", help="print this worker's manifest as JSON")
    manifest.add_argument(
        "--check", action="store_true",
        help="exit 1 when a required need is unmet (for a scheduler's gate)")
    args = parser.parse_args(argv)

    if args.verb == "manifest":
        result = _manifest_main(args)
        return result
    parser.error(f"unknown verb {args.verb!r}")
    return 2


def _manifest_main(args) -> int:
    manifest = produce_manifest()
    print(json.dumps(manifest, indent=2, sort_keys=True))

    if args.check:
        from library.tools import machine_needs
        unavailable = [cap for cap, verdict in manifest["capabilities"].items()
                       if verdict == machine_needs.UNAVAILABLE]
        if unavailable:
            print(f"ren worker manifest: {len(unavailable)} capabilities "
                  f"unavailable: {', '.join(unavailable)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
