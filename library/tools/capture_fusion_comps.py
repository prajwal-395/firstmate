"""READ-ONLY: read Fusion comps off a live timeline. Never AddTool.

Kept in the pipeline (moved out of a per-project captures/ drop zone):
reading every comp tool-by-tool off an arbitrary named timeline is
reusable diagnosis tooling with no project constants baked in.
"""
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if os.environ.get("REPO"):
    sys.path.insert(0, os.environ["REPO"])
elif str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import DaVinciResolveScript as dvr
from library.tools.resolve_locale import scriptapp_preserving_locale

TARGET, OUT = sys.argv[1], sys.argv[2]
r = scriptapp_preserving_locale(dvr, "Resolve")
p = r.GetProjectManager().GetCurrentProject()
tl = next(p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount()+1)
          if p.GetTimelineByIndex(i).GetName() == TARGET)

def safe(fn, *a):
    try: return fn(*a)
    except Exception as e: return "<error: %s>" % e

out = []
for tt in ("video",):
    for idx in range(1, (tl.GetTrackCount(tt) or 0)+1):
        for it in (tl.GetItemListInTrack(tt, idx) or []):
            n = safe(it.GetFusionCompCount)
            if not isinstance(n, int) or n < 1:
                continue
            entry = {"track": f"{tt}{idx}", "item": safe(it.GetName),
                     "record_start": safe(it.GetStart), "comps": []}
            for ci in range(1, n+1):
                comp = safe(it.GetFusionCompByIndex, ci)
                if comp is None or isinstance(comp, str):
                    entry["comps"].append({"index": ci, "error": str(comp)}); continue
                tools = safe(comp.GetToolList, False) or {}
                c = {"index": ci, "tools": []}
                if isinstance(tools, dict):
                    for _k, tool in sorted(tools.items()):
                        td = {"name": safe(tool.GetAttrs, "TOOLS_Name"),
                              "regid": safe(tool.GetAttrs, "TOOLS_RegID"),
                              "img_w": safe(tool.GetAttrs, "TOOLI_ImageWidth"),
                              "img_h": safe(tool.GetAttrs, "TOOLI_ImageHeight"),
                              "inputs": {}}
                        il = safe(tool.GetInputList) or {}
                        if isinstance(il, dict):
                            for _ik, inp in il.items():
                                try:
                                    iid = inp.GetAttrs("INPS_ID")
                                    v = tool.GetInput(iid)
                                    if isinstance(v, (int, float, str, bool)) or v is None:
                                        td["inputs"][iid] = v
                                except Exception:
                                    pass
                        c["tools"].append(td)
                entry["comps"].append(c)
            out.append(entry)

json.dump(out, open(OUT, "w", encoding="utf-8"), indent=2, sort_keys=True, default=str)
print("wrote", OUT, "items with comps:", len(out))
