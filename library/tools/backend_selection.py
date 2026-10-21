"""The ONE enumeration of the places that choose an execution backend.

AXI-002: a semantic request must have ONE auditable routing decision.
That is only true if the places that decide which execution backend runs
are known, and only one of them decides.  This module is that knowledge:
it names every site, says what each one decides, and says which of them
is the selector.

The sites before this lane (measured 2026-10-21, the numbers in
`docs/AXI_002_BACKEND_SELECTION.md`):

1. `library/tools/execution_router.py:route_compiled_edit` - the
   selector.  It chose the route.
2. `library/tools/execution_router.py:_route_compiled_edit` - a SECOND
   Resolve AXI arm inside the selector, repeating the same eligibility
   judgment and adding the compound-shape check on top.  Unreachable:
   every backend row of a declared operation is compilable, so the first
   arm had already taken every request the second could have taken.
3. `library/tools/resolve_axi_core.py:_execute_registered_backend` - a
   second eligibility judgment (its own two backend frozensets) and a
   second physical-path choice (which adapter runs the request).
4. `library/tools/resolve_axi_core.py:submit_edit_request_async` - a
   third backend-name set, deciding which broker job carries the route.
5. `library/tools/resolve_axi_core.py:register_edit_executor` - a fourth
   set, deciding which backend an executor may be registered on.
6. `library/tools/resolve_axi_core.py:_plan_transaction_patch` - a fifth:
   the legacy typed EditPatch path declared its route outside the
   router.
7. `library/tools/resolved/jobs.py:prepare` - a sixth: the broker's own
   submission gate decided which routes a semantic job may carry.
8. `library/tools/feedback_patch_apply.py:apply` - a seventh: the front
   door's guarded path decided which routes it accepts.

After this lane the selector is the only one that DECIDES; every other
site reads the router's declared table.  The sites below are therefore
`selector`, `guard`, `record` or `switch` - and only a `selector`
decides.

The Rust/Python computational switch (`library/tools/edit_backend.py:
use_rust`) is a different axis - it picks which implementation of the
algebra runs, not which execution backend a request is routed to - and
it was already a single switch, so it is named here as a `switch`.
"""

from __future__ import annotations

from dataclasses import dataclass

from library.tools import execution_router


@dataclass(frozen=True)
class SelectionSite:
    """One place that answers a question about an execution backend.

    `kind` says what the site does with the answer:

    * `selector` - it DECIDES the route.  There is exactly one.
    * `guard` - it checks a route somebody else decided, and refuses.
    * `fixed_route` - it names ONE route, and never compares routes.
    * `record` - it names the route that ran, in a receipt.
    * `switch` - it picks an implementation of something else entirely.
    """

    site_id: str
    module: str
    function: str
    kind: str
    decides: str

    def as_dict(self) -> dict:
        return {"site_id": self.site_id, "module": self.module,
                "function": self.function, "kind": self.kind,
                "decides": self.decides}


#: The sites, in the order a request meets them.
SELECTION_SITES: tuple[SelectionSite, ...] = (
    SelectionSite(
        site_id="router",
        module="library/tools/execution_router.py",
        function="route_compiled_edit",
        kind="selector",
        decides=("which route runs the request: no-op, EditPatch, the "
                 "Resolve AXI adapter, the staged touch-up, a structural "
                 "composition, the incremental diff or the full rebuild")),
    SelectionSite(
        site_id="router.transport",
        module="library/tools/execution_router.py",
        function="transport_for",
        kind="guard",
        decides=("which broker job carries the route - the router's own "
                 "table, read by the dispatcher, the broker's submission "
                 "gate and the front door")),
    SelectionSite(
        site_id="router.admission",
        module="library/tools/execution_router.py",
        function="route_admission_reasons",
        kind="guard",
        decides=("whether the recorded decision authorizes an operation "
                 "on the route - the ONE eligibility judgment, read from "
                 "the same matrix the router routes from")),
    SelectionSite(
        site_id="dispatcher",
        module="library/tools/resolve_axi_core.py",
        function="_execute_registered_backend",
        kind="guard",
        decides=("that the route it was handed is the route the router "
                 "selected, and that the named adapter runs it - it does "
                 "not choose either")),
    SelectionSite(
        site_id="broker.gate",
        module="library/tools/resolved/jobs.py",
        function="prepare",
        kind="guard",
        decides=("that a semantic job carries a route that runs as one - "
                 "read from the router's transport table")),
    SelectionSite(
        site_id="front.door",
        module="library/tools/feedback_patch_apply.py",
        function="guarded_apply",
        kind="guard",
        decides=("that the guarded feedback path accepts the route the "
                 "router selected - read from the router's transport "
                 "table")),
    SelectionSite(
        site_id="legacy.patch",
        module="library/tools/resolve_axi_core.py",
        function="_plan_transaction_patch",
        kind="fixed_route",
        decides=("the route of the legacy typed EditPatch transaction - a "
                 "FIXED route, not a choice: it names one route and never "
                 "compares routes")),
    SelectionSite(
        site_id="algebra.switch",
        module="library/tools/edit_backend.py",
        function="use_rust",
        kind="switch",
        decides=("which implementation of the edit algebra computes - the "
                 "Rust extension or the Python one - a different axis from "
                 "the execution backend a request is routed to, and "
                 "already a single switch")),
)


def selection_sites() -> tuple[SelectionSite, ...]:
    """Every site, in the order a request meets them."""
    return SELECTION_SITES


def selectors() -> tuple[SelectionSite, ...]:
    """The sites that DECIDE a route.  There is one."""
    return tuple(site for site in SELECTION_SITES if site.kind == "selector")


def route_names() -> frozenset[str]:
    """The route vocabulary the router declares.

    A route name is a backend id with a meaning.  A module that builds a
    set or a mapping out of these names is making a decision about
    routes - which is how the seven sets AXI-002 deleted were found.
    """
    return frozenset(execution_router.ROUTE_TRANSPORT)


def transport_of(route: str) -> str:
    """Which broker job carries this route."""
    return execution_router.transport_for(route)
