"""Alternatives for the main edit - graph forks at a named region.

The gap this closes
-------------------
Every planner produces exactly one plan. The captain's example request -
"The opening feels slow. Give me three alternatives without changing the
argument" - cannot be served: there is no mechanism that produces N
comparable variants of a named region. The reel variant mechanism
(`variants.py`) is reel-only: keyed by `reel_number`, branches are
`variant/r<NN>-<slug>`, and `OUT_OF_VOCABULARY` excludes "a different
moment, span or approval". This module generalizes the pattern to the
edit graph (Gap 3).

An alternative is a FORK OF THE GRAPH at a named region with a declaration
of what differs. The engine produces N forks, each differing ONLY in the
declared dimension, and each preserving the argument (the intent model's
beats and claims are unchanged). The chosen fork is promoted; the others
are archived.

The declared dimensions
-----------------------
- `passage_order`: reorder the passages within a region. The region is a
  set of passage node ids; each variant is a different ordering.
- `broll_cover`: different B-roll covers for the same block. The region
  is a block position; each variant names a different clip.

What ships
----------
`produce_alternatives` produces N alternative graphs from a base graph,
an intent model, and N specs. Each alternative is a deep copy of the base
graph with the region's nodes modified according to the dimension and
value, the dependency edges re-derived, and the intent refs preserved.

`validate_alternatives` checks that the alternatives differ ONLY in the
declared dimension and preserve the argument. A test proves this.

This module imports nothing from steps or processes: it reads the graph
through `library/tools/edit_graph.py` and the intent model through
`library/tools/intent_model.py`, the same readers every consumer has.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from library.tools import edit_graph

# ── The declared dimensions ─────────────────────────────────────────

PASSAGE_ORDER = "passage_order"
"""Reorder the passages within a region."""

BROLL_COVER = "broll_cover"
"""Different B-roll covers for the same block."""

DIMENSIONS = (PASSAGE_ORDER, BROLL_COVER)
"""The declared dimensions an alternative may differ in."""


# ── The alternative spec ────────────────────────────────────────────


@dataclass(frozen=True)
class AlternativeSpec:
    """The declaration of what differs in one alternative.

    An alternative is a fork of the graph at a named region with a
    declaration of what differs. The spec carries the region (the node
    ids the alternative addresses), the dimension (what differs), the
    value (the variant's specific value for that dimension), and the
    trade-off (what the variant gives or takes).
    """

    region: tuple[str, ...]
    """The node ids this alternative addresses."""
    dimension: str
    """What differs - one of `DIMENSIONS`."""
    value: tuple
    """The variant's specific value for the dimension."""
    trade_off: str = ""
    """What this variant gives or takes - the trade-off statement."""

    def __post_init__(self):
        if self.dimension not in DIMENSIONS:
            raise ValueError(
                f"unknown dimension {self.dimension!r}; "
                f"known: {list(DIMENSIONS)}")
        if not self.region:
            raise ValueError(
                "region is empty - an alternative must address at least "
                "one node")

    def as_dict(self) -> dict:
        """The flat form for a log line or a hook payload."""
        return {
            "region": list(self.region),
            "dimension": self.dimension,
            "value": (list(self.value)
                      if isinstance(self.value, (list, tuple))
                      else self.value),
            "trade_off": self.trade_off,
        }


# ── Producing alternatives ──────────────────────────────────────────


def produce_alternatives(graph: dict, intent_model: dict,
                         specs: list[AlternativeSpec]) -> list[dict]:
    """Produce N alternative graphs from a base graph and N specs.

    Each alternative is a deep copy of the base graph with the region's
    nodes modified according to the dimension and value, the dependency
    edges re-derived, and the intent refs preserved. The intent model is
    carried alongside each alternative so a reader can verify the
    argument is unchanged.
    """
    alternatives = []
    for spec in specs:
        alternative = _produce_one(graph, intent_model, spec)
        alternatives.append(alternative)
    return alternatives


def _produce_one(graph: dict, intent_model: dict,
                 spec: AlternativeSpec) -> dict:
    """Produce one alternative graph."""
    alternative = copy.deepcopy(graph)
    if spec.dimension == PASSAGE_ORDER:
        _apply_passage_order(alternative, spec)
    elif spec.dimension == BROLL_COVER:
        _apply_broll_cover(alternative, spec)
    edit_graph._derive_edges(alternative)
    return {
        "graph": alternative,
        "intent_model": copy.deepcopy(intent_model),
        "spec": spec.as_dict(),
    }


def _apply_passage_order(graph: dict, spec: AlternativeSpec) -> None:
    """Reorder the passages within the region.

    The value is a permutation of the region's positions: `value[i]` is
    the position the passage at that index moves to. The passages' node
    ids are preserved (they are the graph's own addressing), so the
    ordering is the only thing that changes.
    """
    positions = list(spec.value)
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    for node_id in spec.region:
        node = nodes.get(node_id)
        if node and node["node_type"] == edit_graph.PASSAGE_SELECTION:
            current = node["payload"].get("position")
            if current in positions:
                node["payload"]["position"] = positions.index(current) + 1


def _apply_broll_cover(graph: dict, spec: AlternativeSpec) -> None:
    """Change the B-roll cover's clip.

    The value is a clip_id. Every B-roll cover in the region gets the
    new clip; the block it covers and the span it plays are unchanged.
    """
    clip_id = spec.value
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    for node_id in spec.region:
        node = nodes.get(node_id)
        if node and node["node_type"] == edit_graph.BROLL_COVER:
            node["payload"]["clip_id"] = clip_id


# ── Validation ──────────────────────────────────────────────────────


def validate_alternatives(alternatives: list[dict], base_graph: dict,
                          dimension: str) -> list[str]:
    """Every defect in the alternatives, as a list of problems. Empty is valid.

    Checks, per alternative:

    - the same nodes are present (same node ids, same node types);
    - the same edges are present (same `depends_on`);
    - the same intent refs are present (the argument is preserved);
    - the ONLY payload difference is in the declared dimension's field.

    A defect here is a fork that changed something it did not declare -
    which would make the comparison dishonest, because the captain
    chooses between alternatives on the strength of the declaration.
    """
    problems: list[str] = []
    for index, alternative in enumerate(alternatives):
        label = f"alternative {index}"
        problems.extend(
            _validate_one(alternative, base_graph, dimension, label))
    return problems


def _validate_one(alternative: dict, base_graph: dict, dimension: str,
                  label: str) -> list[str]:
    """Validate one alternative against the base graph."""
    problems: list[str] = []
    graph = alternative.get("graph", alternative)

    base_ids = {node["node_id"] for node in base_graph["nodes"]}
    alt_ids = {node["node_id"] for node in graph["nodes"]}
    if base_ids != alt_ids:
        problems.append(
            f"{label}: node set differs from the base graph "
            f"(missing: {sorted(base_ids - alt_ids)}, "
            f"extra: {sorted(alt_ids - base_ids)})")

    base_edges = {node["node_id"]: sorted(node["depends_on"])
                  for node in base_graph["nodes"]}
    alt_edges = {node["node_id"]: sorted(node["depends_on"])
                 for node in graph["nodes"]}
    if base_edges != alt_edges:
        problems.append(
            f"{label}: edges differ from the base graph - a fork that "
            f"re-wires dependencies changed more than the declared "
            f"dimension")

    base_intents = {node["node_id"]: list(node["intent_refs"])
                    for node in base_graph["nodes"]}
    alt_intents = {node["node_id"]: list(node["intent_refs"])
                   for node in graph["nodes"]}
    if base_intents != alt_intents:
        problems.append(
            f"{label}: intent refs differ from the base graph - the "
            f"argument changed, which 'without changing the argument' "
            f"forbids")

    base_nodes = {node["node_id"]: node for node in base_graph["nodes"]}
    alt_nodes = {node["node_id"]: node for node in graph["nodes"]}
    for node_id in base_ids & alt_ids:
        base_payload = base_nodes[node_id].get("payload", {})
        alt_payload = alt_nodes[node_id].get("payload", {})
        if base_payload == alt_payload:
            continue
        if not _difference_is_in_dimension(base_payload, alt_payload,
                                          dimension):
            problems.append(
                f"{label}: node {node_id} differs outside the declared "
                f"dimension {dimension!r}")
    return problems


def _difference_is_in_dimension(base_payload: dict, alt_payload: dict,
                               dimension: str) -> bool:
    """Whether the payload difference is only in the declared dimension."""
    if dimension == PASSAGE_ORDER:
        return _only_field_differs(base_payload, alt_payload, "position")
    if dimension == BROLL_COVER:
        return _only_field_differs(base_payload, alt_payload, "clip_id")
    return False


def _only_field_differs(base: dict, alt: dict, field: str) -> bool:
    """Whether the only difference between two dicts is in one field."""
    for key in set(base) | set(alt):
        if key == field:
            continue
        if base.get(key) != alt.get(key):
            return False
    return True


# ── Comparing alternatives ──────────────────────────────────────────


def compare_alternatives(alternatives: list[dict]) -> dict:
    """What differs between the alternatives, off the declarations.

    The comparison is read off the graphs, not the specs: two
    alternatives that declare the same dimension but produce the same
    graph are the same alternative, and the comparison says so. The
    answer is `{dimension, differ, agree}` - `differ` names the fields
    that vary across the alternatives, `agree` the fields that do not.
    """
    if not alternatives:
        return {"dimension": "", "differ": [], "agree": []}
    dimension = alternatives[0].get("spec", {}).get("dimension", "")
    differ: set = set()
    agree: set = set()
    first = alternatives[0].get("graph", alternatives[0])
    first_nodes = {node["node_id"]: node for node in first["nodes"]}
    for node_id, base_node in first_nodes.items():
        for alternative in alternatives[1:]:
            graph = alternative.get("graph", alternative)
            other_nodes = {node["node_id"]: node for node in graph["nodes"]}
            other = other_nodes.get(node_id)
            if other is None:
                differ.add("node set")
                continue
            for key in set(base_node.get("payload", {})) | set(
                    other.get("payload", {})):
                if (base_node.get("payload", {}).get(key)
                        != other.get("payload", {}).get(key)):
                    differ.add(key)
                else:
                    agree.add(key)
    return {
        "dimension": dimension,
        "differ": sorted(differ),
        "agree": sorted(agree),
    }


# ── Reel variants as edit variants ──────────────────────────────────
#
# A reel is a view onto the edit graph: it is cut from a moment - a span
# in the main edit's timeline. A reel variant is an edit variant of the
# region that moment maps to, plus a reel build (the timeline, the
# captions, the conformance). The two are one mechanism, not two: the
# reel variant's difference is declared as an edit variant dimension,
# produced by `produce_reel_variant`, and validated by
# `validate_alternatives` like any other fork.


@dataclass(frozen=True)
class ReelVariantSpec:
    """A reel variant expressed as an edit variant of its region.

    A reel is a view onto the edit graph. A reel variant is an edit
    variant of the region the reel was cut from, plus a reel build.
    This spec carries the reel-specific info (reel number, base
    timeline name, suffix) alongside the alternative spec's region,
    dimension and value.

    The reel-specific difference keys (`j_cut`, `cutaway`, `cover`,
    `declares`) are NOT carried here: they are the reel build's own
    vocabulary, applied after the fork is chosen. What this spec
    declares is the EDIT difference - the dimension and value the
    alternative is forked on.
    """

    reel_number: int
    """Which reel this variant is a variant of."""

    base_timeline_name: str
    """The approved timeline name the variation builds beside."""

    suffix: str
    """The variant suffix - trailing parens the timeline name carries."""

    region: tuple[str, ...]
    """The edit region (node ids) this variant forks."""

    dimension: str
    """What differs - one of `DIMENSIONS`."""

    value: tuple
    """The variant's specific value for the dimension."""

    trade_off: str = ""
    """What this variant gives or takes - the trade-off statement."""

    def as_dict(self) -> dict:
        """The flat form for a log line or a hook payload."""
        return {
            "reel_number": self.reel_number,
            "base_timeline_name": self.base_timeline_name,
            "suffix": self.suffix,
            "region": list(self.region),
            "dimension": self.dimension,
            "value": (list(self.value)
                      if isinstance(self.value, (list, tuple))
                      else self.value),
            "trade_off": self.trade_off,
        }


def region_for_moment(graph: dict, timeline_start: float,
                      timeline_end: float) -> tuple[str, ...]:
    """The graph nodes whose timeline span overlaps a moment's span.

    A reel is cut from a moment - a span in the main edit's timeline.
    The region is the set of graph nodes whose timeline span overlaps
    that span: the blocks, passages, B-roll covers and transitions the
    moment plays. This is the region a reel variant forks.

    Nodes without a timeline span (a grade correction, a mix window)
    are not part of any moment's region - they are properties of the
    whole edit, not of one span of it.

    Both timeline spellings are read: blocks, assignments and overlays
    carry `timeline_start`/`timeline_end`, while passages carry
    `start_time`/`end_time`. A passage is part of the moment's region
    either way - the two spellings are the same address.
    """
    region = []
    for node in graph["nodes"]:
        payload = node.get("payload", {})
        start = payload.get("timeline_start", payload.get("start_time"))
        end = payload.get("timeline_end", payload.get("end_time"))
        if start is None or end is None:
            continue
        if start < timeline_end and timeline_start < end:
            region.append(node["node_id"])
    return tuple(sorted(region))


def produce_reel_variant(graph: dict, intent_model: dict,
                        spec: ReelVariantSpec) -> dict:
    """Produce one reel variant as an edit variant of its region.

    The reel variant is an edit variant: a fork of the graph at the
    reel's region with a declaration of what differs. The reel build
    (timeline, captions, conformance) is the reel-specific part that
    happens after the fork is chosen - it is `variants.py`'s own
    vocabulary, applied to the fork this produces.

    Returns the alternative, the same shape `produce_alternatives`
    returns: the forked graph, the intent model carried alongside, and
    the spec as a flat dict.
    """
    alternative_spec = AlternativeSpec(
        region=spec.region,
        dimension=spec.dimension,
        value=spec.value,
        trade_off=spec.trade_off,
    )
    return _produce_one(graph, intent_model, alternative_spec)


def reel_variant_agrees_with_edit_variant(reel_variant: dict,
                                          edit_variant: dict) -> list[str]:
    """Whether a reel variant and an edit variant agree.

    They agree when they describe the same difference: the same region,
    the same dimension, the same value. The reel variant is an edit
    variant of its region, so a reel variant and an edit variant of the
    same region with the same dimension and value are the same fork.

    Returns a list of problems; empty is agreement. The check is on the
    SPECS, not the produced graphs: two specs that declare the same
    region, dimension and value produce the same fork, so the specs are
    where agreement lives.
    """
    problems: list[str] = []
    reel_spec = reel_variant.get("spec", {})
    edit_spec = edit_variant.get("spec", {})
    reel_region = list(reel_spec.get("region", ()))
    edit_region = list(edit_spec.get("region", ()))
    if reel_region != edit_region:
        problems.append(
            f"regions differ: reel variant names {reel_region}, edit "
            f"variant names {edit_region}")
    if reel_spec.get("dimension") != edit_spec.get("dimension"):
        problems.append(
            f"dimensions differ: reel variant declares "
            f"{reel_spec.get('dimension')!r}, edit variant declares "
            f"{edit_spec.get('dimension')!r}")
    reel_value = reel_spec.get("value")
    edit_value = edit_spec.get("value")
    if isinstance(reel_value, (list, tuple)):
        reel_value = list(reel_value)
    if isinstance(edit_value, (list, tuple)):
        edit_value = list(edit_value)
    if reel_value != edit_value:
        problems.append(
            f"values differ: reel variant carries {reel_value!r}, edit "
            f"variant carries {edit_value!r}")
    return problems
