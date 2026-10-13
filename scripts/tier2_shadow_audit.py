"""Prepare or complete the observational audit for a direct full gate."""

from __future__ import annotations

import json
import subprocess
import sys

from library.tools.local_ci import config, shadow


def main(argv: list[str]) -> int:
    action, checkout, *args = argv
    state_dir = config.state_dir()
    if action == "prepare":
        try:
            prepared = shadow.prepare_direct_gate_audit(checkout, state_dir)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError,
                TypeError, RuntimeError, ImportError, AttributeError) as exc:
            try:
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=checkout,
                    capture_output=True, encoding="utf-8", check=False,
                    timeout=30).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                head = ""
            prepared = {
                "head": head,
                "consulted": {"expanded": [], "reused": [], "total": 0},
                "preflight_error": type(exc).__name__}
        with open(args[0], "w", encoding="utf-8") as handle:
            json.dump(prepared, handle)
        return 0
    plan_path, junit_path, verdict = args
    try:
        with open(plan_path, encoding="utf-8") as handle:
            prepared = json.load(handle)
        record = shadow.complete_direct_gate_audit(
            checkout, state_dir, prepared, junit_path, verdict)
        if prepared.get("preflight_error"):
            record["preflight_error"] = prepared["preflight_error"]
        print("ren-ci CI-08 direct shadow: hypothetical "
              f"{record['incremental_verdict']}; full gate "
              f"{record['full_gate_verdict']}; disagreement="
              f"{record['disagreement']}; would skip "
              f"{record['skipped_tests']}/{record['total_tests']} tests")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError,
            ImportError, AttributeError) as exc:
        # Observational only; never changes the full-gate verdict.
        print(f"ren-ci CI-08 direct shadow audit unavailable: {exc}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
