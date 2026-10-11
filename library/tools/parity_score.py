"""Print the FFrames parity score: one ``parity: N/12`` number.

Evaluates every probe through ``probe_status``
(``library/graphics_ir/parity_probes.py``) in-process - no renderer, no
Resolve, no pixel render - and reports each row as PASS, EXPECTED-FAIL
(failing today, marked by probe id in ``EXPECTED_FAIL_TODAY``) or FAIL
(unexpected: an unmarked probe failing, or a marked one now passing -
both raise with the repair step).

Usage: ``python3 -m library.tools.parity_score``. Exit 0 always: this
is a scoreboard, not a gate.
"""

from __future__ import annotations

from library.graphics_ir.parity_probes import PROBES, probe_status


def main() -> int:
    width = max(len(p.pid) + 1 + len(p.name) for p in PROBES)
    passes = 0
    for probe in PROBES:
        try:
            status, detail = probe_status(probe.pid)
        except AssertionError as exc:
            print(f"{probe.pid} {probe.name:<{width - 5}} FAIL {exc}")
        else:
            if status == "PASS":
                passes += 1
            print(f"{probe.pid} {probe.name:<{width - 5}} {status} {detail}")
    print(f"parity: {passes}/{len(PROBES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
