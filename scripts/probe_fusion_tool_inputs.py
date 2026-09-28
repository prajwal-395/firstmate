#!/usr/bin/env python3
"""Re-read every Fusion tool's input names off a running DaVinci Resolve.

Rewrites `library/tools/fusion/tool_inputs.json` in place. The diff is the
answer to "did this Resolve version change the vocabulary" - and the table
is a GATE, not documentation, so it must be re-taken deliberately rather
than drifting.

Needs Resolve running. Nothing is rendered, no project is opened and no
timeline is touched: it creates a throwaway Fusion comp (`Fusion.NewComp`),
adds one of each tool, reads `GetInputList()` and exits.

    python3 scripts/probe_fusion_tool_inputs.py

`TOOLS` is every tool type this engine writes into an authored comp, plus
the two mask/IO tools it may reach for. A tool that fails to add is
REPORTED and left out of the table - that is how `ChromaticAberration` was
found to not exist.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TOOLS = [
    "Background", "Blur", "BrightnessContrast", "Crop", "Defocus",
    "DirectionalBlur", "EllipseMask", "FilmGrain", "LensDistort",
    "Loader", "MediaIn", "MediaOut", "Merge", "RectangleMask", "Saver",
    "SoftGlow", "Transform", "XYPath",
]

TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "library", "tools", "fusion", "tool_inputs.json")


from library.tools.resolve_lock import under_lease


@under_lease("probe Fusion tool inputs")
def main() -> int:
    import DaVinciResolveScript as dvr
    from library.tools.resolve_locale import scriptapp_preserving_locale

    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    if resolve is None:
        print("Resolve is not running, or scripting is not enabled.")
        return 1
    comp = resolve.Fusion().NewComp()
    if comp is None:
        print("Fusion refused a new comp.")
        return 1

    tools: dict[str, list[str]] = {}
    for tool_id in TOOLS:
        tool = comp.AddTool(tool_id)
        if not tool:
            print(f"ABSENT: {tool_id} is not a registered Fusion tool.")
            continue
        ids = set()
        for handle in (tool.GetInputList() or {}).values():
            attrs = handle.GetAttrs() or {}
            if attrs.get("INPS_ID"):
                ids.add(attrs["INPS_ID"])
        tools[tool_id] = sorted(ids)
        print(f"{tool_id}: {len(ids)} inputs")

    payload = {
        "_meta": {
            "resolve_version": resolve.GetVersionString(),
            "probed": datetime.date.today().isoformat(),
        },
        "tools": tools,
    }
    with open(TABLE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"wrote {os.path.normpath(TABLE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
