"""CLI compatibility shim for the extracted Resolve AXI command core."""

from __future__ import annotations

import sys

from ren import hosts

from library.tools import resolve_axi_core_commands as _commands

if __name__ == "__main__":
    import os

    _HERE = os.path.abspath(__file__)
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )

    try:
        hosts.ensure_harness_output_env()
        raise SystemExit(_commands.main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - preserve the CLI result contract
        sys.exit(report_unexpected(exc))
else:
    # Existing Python integrations import `resolve_axi` and patch its command
    # surface in tests and adapters. Alias the module object so those patches
    # still reach the command implementations' globals.
    sys.modules[__name__] = _commands
