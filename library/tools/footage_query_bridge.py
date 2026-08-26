#!/usr/bin/env python3
"""Bridge for LLM-driven footage queries. JSON in on stdin, JSON out.

PROTOTYPE. **No step calls this**, and `tests/test_footage_query_prototype.py`
fails if one starts to. It exists so the interface an LLM step WOULD use
is real and runnable, which is the only way to judge whether it is worth
wiring in later.

The shape follows `library/tools/sfx_query_bridge.py`, so an orchestrator
that already knows how to call the SFX bridge needs nothing new:

    echo '{"project_folder": "/path/to/001",
           "query": "he talks about finding a place to park",
           "top_k": 5,
           "mode": "hybrid",
           "filters": {"kind": "speech", "framing": "close-up"}}' \\
      | python3 -m library.tools.footage_query_bridge

Recognised keys:

    project_folder  required - the project to search
    index_dir       optional - defaults to <project>/pipeline_output/scratch/footage_index
    query           optional - omit to run filters alone
    top_k           optional - default 5
    mode            optional - hybrid (default) | dense | lexical
    filters         optional - any keyword FootageIndex.filter accepts
    action          optional - search (default) | detail | transcript | summary | tools
    segment_id      required for action=detail
    clip_id         required for action=transcript
"""

import json
import os
import sys

PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PILOT_ROOT not in sys.path:
    sys.path.insert(0, PILOT_ROOT)

from library.tools.analysis.footage_query import FootageIndex


def _handle(data: dict) -> dict:
    action = data.get("action", "search")
    if action == "tools":
        return {"tools": FootageIndex.get_tool_definitions()}

    project_folder = data.get("project_folder")
    if not project_folder:
        return {"error": "project_folder is required"}

    idx = FootageIndex(project_folder, index_dir=data.get("index_dir"))

    if action == "summary":
        return {"summary": idx.summary()}
    if action == "detail":
        return {"segment": idx.get_detail(data.get("segment_id", ""))}
    if action == "transcript":
        return {"transcript": idx.transcript(data.get("clip_id", ""))}
    if action != "search":
        return {"error": f"Unknown action {action!r}"}

    query = data.get("query", "")
    top_k = int(data.get("top_k", 5))
    mode = data.get("mode", "hybrid")
    filters = data.get("filters") or {}

    if query:
        results = idx.search_and_filter(query, top_k=top_k, mode=mode, **filters)
    else:
        results = idx.filter(**filters)[:top_k]
    return {"results": results}


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            json.dump({"error": "Empty input"}, sys.stdout)
            return
        json.dump(_handle(json.loads(raw)), sys.stdout, indent=2)
    except Exception as exc:  # noqa: BLE001 - the caller reads JSON, never a traceback
        json.dump({"error": f"{type(exc).__name__}: {exc}"}, sys.stdout)


if __name__ == "__main__":
    main()
