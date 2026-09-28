#!/usr/bin/env python3
"""Diagnose a Fusion composition on a timeline clip.

Dumps the node graph, connections, and key values for debugging.

Usage:
    python diagnose_comp.py [clip_index] [track_index]

    # Or import:
    from diagnose_comp import diagnose_clip_comp
    info = diagnose_clip_comp(timeline, clip_index=0)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from library.tools.resolve_lock import under_lease

sys.path.insert(0, os.path.dirname(__file__))


@under_lease("inspect Fusion comp on Resolve timeline", exclusive=False)
def diagnose_clip_comp(timeline, clip_index=0, track_index=1):
    """Dump Fusion composition info for a clip.

    Args:
        timeline: Resolve timeline handle
        clip_index: 0-based index of clip in the track
        track_index: 1-based video track index

    Returns:
        dict with comp info, or None if no comp exists.
    """
    clips = timeline.GetItemListInTrack("video", track_index) or []
    if clip_index >= len(clips):
        print(f"  ✗ Clip index {clip_index} out of range (track has {len(clips)} clips)")
        return None

    clip = clips[clip_index]
    comp_names = clip.GetFusionCompNameList() or []

    if not comp_names:
        print(f"  Clip {clip_index}: No Fusion composition")
        return None

    info = {
        "clip_index": clip_index,
        "clip_name": clip.GetName(),
        "duration": clip.GetDuration(),
        "comp_count": len(comp_names),
        "comp_names": list(comp_names),
        "nodes": {},
    }

    # Get the active comp
    comp = clip.GetFusionCompByName(comp_names[0])
    if comp:
        tools = comp.GetToolList() or {}
        for tool_name, tool in tools.items():
            tool_info = {
                "type": tool.GetAttrs().get("TOOLS_RegID", "unknown"),
                "inputs": {},
            }

            # Get all input values
            input_list = tool.GetInputList() or {}
            for inp_name, inp in input_list.items():
                try:
                    val = inp[0] if isinstance(inp, (list, tuple)) else inp.GetValue()
                    if val is not None:
                        tool_info["inputs"][inp_name] = str(val)
                except (AttributeError, IndexError, KeyError, RuntimeError,
                        TypeError, ValueError) as exc:
                    tool_info["inputs"][inp_name] = (
                        f"<unreadable: {type(exc).__name__}: {exc}>")

            info["nodes"][tool_name] = tool_info

    # Print summary
    print(f"\n  Clip {clip_index}: {info['clip_name']} ({info['duration']} frames)")
    print(f"  Comps: {', '.join(comp_names)}")
    print(f"  Nodes: {len(info['nodes'])}")
    for name, node in info["nodes"].items():
        print(f"    {name} ({node['type']})")
        for k, v in list(node["inputs"].items())[:5]:
            print(f"      {k} = {v}")

    return info


if __name__ == "__main__":
    from connect_resolve import connect

    clip_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    track_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    with connect(exclusive=False) as (resolve, project, timeline):
        if timeline:
            diagnose_clip_comp(timeline, clip_idx, track_idx)
        else:
            print("No timeline open")
