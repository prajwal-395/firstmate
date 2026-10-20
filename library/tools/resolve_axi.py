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
        emit_refusal_result, report_unexpected,
    )
    from library.tools.ren_refusal import RenRefusal

    try:
        hosts.ensure_harness_output_env()
    except Exception as exc:  # noqa: BLE001 - preserve the CLI result contract
        sys.exit(report_unexpected(exc))
    # ONE boundary renders every refusal on this verb's path, in the same
    # shape as the front door. A typed refusal (a routing refusal among
    # them) leaves as exit 4 with its next action; an unexpected error
    # keeps its traceback and exit 1.
    try:
        sys.exit(_commands.main())
    except RenRefusal as refused:
        emit_refusal_result(refused)
        sys.exit(4)
    except Exception as exc:  # noqa: BLE001 - preserve the CLI result contract
        sys.exit(report_unexpected(exc))
else:
    # Existing Python integrations import `resolve_axi` and patch its command
    # surface in tests and adapters. Alias the module object so those patches
    # still reach the command implementations' globals.
    sys.modules[__name__] = _commands
