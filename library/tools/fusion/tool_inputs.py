"""Which inputs a Fusion tool really has, read off Resolve itself.

An input name Fusion does not have is a SILENT no-op. `SetInput` on one
returns None and changes nothing, and a `.comp` file carrying one loads
without a warning: the tool takes its registry default for whatever the
author meant to drive, and the picture is whatever that default draws.
That is this repository's dominant bug class (AGENTS.md 10.1) at the one
boundary where nothing could catch it - the comp is text, and the reader
is a closed-source application.

Three defects found this way on 2026-09-10, all of them shipped and all
of them on the captain's own timeline:

* ``EllipseMask`` has ``Invert``. The vignette wrote ``Inverted``, so the
  mask stayed solid INSIDE the ellipse and the black Background it gated
  drew as a disc in the middle of the frame instead of falling off at the
  corners. AGENTS.md 5 stated the wrong name in prose, and
  ``FusionNode._validate`` REQUIRED the wrong name, so every guard the
  repository had agreed with the bug.
* ``Crop`` has ``XOffset``/``YOffset``/``XSize``/``YSize``. The old-TV
  switch animation wrote ``CropTop``/``CropBottom``, so ``XSize``/
  ``YSize`` fell to their 1920x1080 registry defaults at offset (0, 0) -
  and Fusion's origin is BOTTOM-LEFT, so a 3840x2160 source was cropped
  to its bottom-left quadrant on every reel's first and last picture
  clip. The animation itself never drew once.
* ``FilmGrain`` has ``MasterStrength``/``MasterXSize``/``MasterYSize``
  and ``LensDistort`` has no bare ``Distortion``. Both wrote names that
  do not exist, so both drew nothing.

And one tool ID that does not exist at all: ``ChromaticAberration``.

So the table is not documentation - it is a GATE. `FusionNode._validate`
refuses an authored node whose tool type is not here, or whose input this
tool does not have. Foreign comps (`FusionComp(authored=False)`, the
parser's path) are exempt, exactly as the other authorship rules are:
DaVinci's own shipped macros are free to carry whatever they like.

Provenance
----------
``tool_inputs.json`` beside this module is a verbatim dump of
``tool.GetInputList()[i].GetAttrs()["INPS_ID"]`` for each tool, taken from
the Resolve build named in its ``_meta``. Re-take it with
``scripts/probe_fusion_tool_inputs.py`` against a running Resolve; it
rewrites the file in place, and the diff is the answer to "did this
Resolve version change the vocabulary".

A tool absent from the table is not a claim that it does not exist - it is
a claim that nobody probed it. Add it to the probe script's list and
re-run rather than guessing.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

_TABLE_PATH = os.path.join(os.path.dirname(__file__), "tool_inputs.json")


def _load() -> tuple[dict, dict[str, frozenset[str]], frozenset[str]]:
    with open(_TABLE_PATH, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    tools = {
        name: frozenset(inputs)
        for name, inputs in raw.get("tools", {}).items()
    }
    return raw.get("_meta", {}), tools, frozenset(raw.get("absent", ()))


PROBE_META, TOOL_INPUTS, ABSENT_TOOLS = _load()


class UnknownFusionInput(ValueError):
    """A node drives a name the Fusion tool does not have.

    Raised rather than warned: a silent no-op is what shipped an inverted
    vignette and a bottom-left crop past every gate this repository had.
    """


class UnknownFusionTool(ValueError):
    """A node names a tool type Resolve's Fusion does not register."""


def is_known_tool(tool_type: str) -> bool:
    """True when this tool type was probed off a real Resolve."""
    return tool_type in TOOL_INPUTS


def is_absent_tool(tool_type: str) -> bool:
    """True when the probe ASKED for this tool and Resolve had none.

    Distinct from "not in the table", which only means nobody probed it.
    A refusal invented from ignorance is worse than the silence it
    replaces, so only a measured absence refuses.
    """
    return tool_type in ABSENT_TOOLS


def unknown_inputs(tool_type: str, names: Iterable[str]) -> list[str]:
    """The given input names this tool does not have, sorted.

    An unprobed tool type answers `[]`: the table cannot say what it does
    not know, and inventing a refusal from ignorance is worse than the
    silence it replaces. `is_known_tool` is the question to ask first.
    """
    known = TOOL_INPUTS.get(tool_type)
    if known is None:
        return []
    return sorted(n for n in names if n not in known)


def describe_provenance() -> str:
    """One line naming the Resolve the table was read from."""
    version = PROBE_META.get("resolve_version", "unknown build")
    probed = PROBE_META.get("probed", "unknown date")
    return (f"Fusion tool inputs probed from Resolve {version} on "
            f"{probed} ({len(TOOL_INPUTS)} tools)")
