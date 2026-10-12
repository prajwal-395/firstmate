"""Adaptive batch grouping by expected cost (CI-11).

Take-everything (`queue.plan_cluster`) stays window-only: the full gate
is one process whatever the cluster size, and no measurement ever
backed a cap (`config` records the removed BATCH_MAX=4 the same way).
This module does NOT cap or exclude - it prices a ripe cluster so the
pool step groups with eyes open:

    expected cost = verification + failure-diagnosis + merge overhead
        + waiting,

estimated from the dependency graph (the CI-06 hybrid selector: two
PRs whose static selections intersect, or whose changed files share a
subsystem, raise the split-risk term) and from measured latency
(`history.full_suite_wall_seconds` for the verification term,
ledger `enqueued_at` ages for the waiting term).

Overlapping PRs are never excluded, only grouped with eyes open: the
pooled candidate (one gate over everything) is always priced alongside
the clustered candidate (one gate per overlap-connected risk cluster,
independents pooled freely), and the cheaper expectation wins. With no
data the pooled candidate wins trivially - extra gates, no measured
risk delta - so this ships as grouping ADVICE printed by the pool step
and hardens into grouping logic only with data. No fixed threshold
replaces the removed four-cap: the comparison is a pure cost ordering,
and a single-PR queue verifies immediately (no grouping tax on the
small case).

Unknown files are conservative: a PR whose changed files cannot be
read is assumed to overlap everything (grouped with eyes open, never
dropped). `real_model`/`resolve_live` markers stay excluded from every
new selection path - this module reads file lists, never test bodies.
"""

from __future__ import annotations

# Merge overhead per PR in seconds: a local merge plus the status
# post and label removal the queue performs per member. Reasoned, not
# measured (no ledger times merges); it cancels across candidates -
# every candidate merges the same PRs - so it can never flip the
# pooled-vs-clustered decision, only complete the reported breakdown.
MERGE_SECONDS_PER_PR = 5.0

# Waiting prices at face value: a second of captain-visible queue
# latency counts as a second. Every candidate covers the same PRs, so
# the term orders groups (oldest first) far more often than it flips
# the split decision; it is reported per group either way.
WAITING_WEIGHT = 1.0


def subsystem_of(path: str) -> str:
    """The subsystem label for a repo-relative path.

    First two segments when nested (`library/tools`,
    `tests/unit`), the top segment when shallow (`scripts`, `ren`).
    Coarse by design: overlap only ever adds an eyes-open note or a
    priced candidate, never an exclusion, so a wide label errs toward
    caution, never toward a wrong kick.
    """
    parts = [piece for piece in str(path or "").split("/") if piece]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[:2])


def pr_subsystems(files: list) -> set:
    """Subsystem labels reached by these changed files."""
    return {label for label in (subsystem_of(path) for path in files or [])
            if label}


def overlap_reason(files_a: list, files_b: list,
                   select=None) -> str:
    """Why two PRs overlap, or "" when they are independent.

    Subsystem overlap (shared label) is checked first; selection
    overlap (the CI-06 graph selects a shared test for both diffs)
    second. Either side with no files is UNKNOWN overlap, never
    independence: a PR the pool cannot see is grouped with eyes open.
    `select` is the `select_dependent_tests.select` callable
    (changed -> [tests]); None skips the graph half, which only ever
    narrows toward independence, never toward overlap.
    """
    if not files_a or not files_b:
        return "unknown files - assuming overlap"
    shared_labels = pr_subsystems(files_a) & pr_subsystems(files_b)
    if shared_labels:
        return ("share subsystem(s) "
                + ", ".join(sorted(shared_labels)))
    if select is not None:
        try:
            tests_a = set(select(list(files_a)))
            tests_b = set(select(list(files_b)))
        except (OSError, RuntimeError, ValueError):
            return "selection unreadable - assuming overlap"
        shared = tests_a & tests_b
        if shared:
            return ("share selected test(s) "
                    + ", ".join(sorted(shared)[:5]))
    return ""


def overlap_graph(entries: list, select=None) -> dict:
    """PR number -> {overlapping PR number: reason}.

    Files come from `entry["files"]` when the entry carries them
    (scripted worlds, ledger-filled entries); missing means unknown,
    which overlaps everything with eyes open. The graph half runs one
    selection per PR (not per pair): shared selected tests overlap
    even across subsystems.
    """
    numbers = [entry.get("pr") for entry in entries]
    files_of = {entry.get("pr"): list(entry.get("files") or [])
                for entry in entries}
    selected: dict = {}
    if select is not None:
        for number in numbers:
            files = files_of[number]
            if not files:
                continue
            try:
                selected[number] = set(select(list(files)))
            except (OSError, RuntimeError, ValueError):
                selected[number] = None
    graph: dict = {number: {} for number in numbers}
    for index, left in enumerate(numbers):
        for right in numbers[index + 1:]:
            # Subsystem half inline (select=None): the graph half
            # below reuses the one selection per PR computed above
            # instead of re-running the selector per pair.
            reason = overlap_reason(files_of[left], files_of[right])
            if (not reason and left in selected and right in selected
                    and selected[left] is not None
                    and selected[right] is not None):
                shared = selected[left] & selected[right]
                if shared:
                    reason = ("share selected test(s) "
                              + ", ".join(sorted(shared)[:5]))
            if (not reason and select is not None
                    and (selected.get(left) is None
                         or selected.get(right) is None)
                    and files_of[left] and files_of[right]):
                reason = "selection unreadable - assuming overlap"
            if reason:
                graph[left][right] = reason
                graph[right][left] = reason
    return graph


def risk_clusters(numbers: list, graph: dict) -> list:
    """Connected components of the overlap graph, in first-seen order.

    Isolated PRs (no overlap edges) are NOT clusters: independents
    pool freely into one shared group, so they are returned apart.
    """
    seen: set = set()
    clusters: list = []
    for number in numbers:
        if number in seen or not graph.get(number):
            seen.add(number)
            continue
        stack = [number]
        component: set = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            seen.add(current)
            stack.extend(peer for peer in graph.get(current, {})
                         if peer not in component)
        clusters.append(sorted(component))
    return clusters


def per_pr_red_probability(count: int, partners: int,
                           base: float) -> float:
    """One PR's red probability given its overlapping partners.

    Each overlapping partner is one more independent interaction
    chance at the base rate: p = 1 - (1-base)^(1+partners). Crude and
    documented as such - the shadow record (CI-10) is what calibrates
    it; until then it only ever prices candidates, never judges a PR.
    Cold (base <= 0) is zero: with no measured failure data the pool
    assumes green and says so.
    """
    if not 0.0 < base < 1.0:
        return 0.0
    return 1.0 - (1.0 - base) ** (1 + max(0, partners))


def group_red_probability(members: list, graph: dict,
                          base: float) -> float:
    """Probability a group needs diagnosis: 1 - Π(1 - p_i)."""
    sure = 1.0
    for member in members:
        partners = len(graph.get(member, {}))
        sure *= 1.0 - per_pr_red_probability(len(members), partners,
                                             base)
    return 1.0 - sure


def expected_bisect_gates(count: int) -> int:
    """Worst-case full-gate runs to diagnose a red group of `count`.

    `resolve_batch` halves recursively and re-runs the full gate per
    node: whole, halves, quarters... 2*count-1 at worst. Conservative
    (worst, not mean) matches the queue's err-wide contract; CI-12's
    failing-test-focused resolver will shrink this term when it lands.
    """
    return max(1, 2 * max(1, count) - 1)


def expected_cost(members: list, *, wall_s: float, graph: dict,
                  base: float, oldest_age_s: float = 0.0) -> dict:
    """The four-term expected cost of verifying one group, in seconds."""
    members = list(members)
    verification = max(0.0, float(wall_s))
    red = group_red_probability(members, graph, base)
    diagnosis = (red * expected_bisect_gates(len(members))
                 * verification)
    merge = len(members) * MERGE_SECONDS_PER_PR
    waiting = max(0.0, float(oldest_age_s)) * WAITING_WEIGHT
    return {"verification": verification, "split_risk": red,
            "diagnosis": diagnosis, "merge": merge,
            "waiting": waiting,
            "total": verification + diagnosis + merge + waiting}


def plan_groups(entries: list, *, wall_s: float, base: float,
                now: float, select=None) -> dict:
    """Price a ripe cluster: pooled vs clustered, cheaper wins.

    Returns {"groups", "candidates", "graph", "advice"}: `groups` is
    the winning partition (list of PR-number lists, oldest-group
    first), `candidates` names both pricings with their term
    breakdowns, `advice` is the printable lines. A single PR (or an
    empty queue) verifies immediately: one group, no grouping tax.
    Every member appears in exactly one group - grouping never
    excludes, it only orders and prices.
    """
    queued = [dict(entry) for entry in entries]
    numbers = [entry.get("pr") for entry in queued]
    graph = overlap_graph(queued, select=select)
    ages = {entry.get("pr"): max(0.0, now - float(
        entry.get("enqueued_at") or now)) for entry in queued}
    by_pr = {entry.get("pr"): entry for entry in queued}

    def cost_of(cluster: list) -> dict:
        oldest = max((ages.get(number, 0.0) for number in cluster),
                     default=0.0)
        return expected_cost(cluster, wall_s=wall_s, graph=graph,
                             base=base, oldest_age_s=oldest)

    pooled = [sorted(numbers)] if numbers else []
    pooled_cost = cost_of(numbers) if numbers else None
    clusters = risk_clusters(numbers, graph)
    if clusters:
        independents = sorted(number for number in numbers
                              if not graph.get(number))
        clustered = [sorted(cluster) for cluster in clusters]
        if independents:
            clustered.append(independents)
    else:
        clustered = [sorted(numbers)] if numbers else []
    clustered_costs = [cost_of(cluster) for cluster in clustered]
    clustered_total = sum(item["total"] for item in clustered_costs)

    cold = not 0.0 < base < 1.0
    if (pooled_cost is not None
            and clustered != pooled
            and clustered_total < pooled_cost["total"]):
        groups, decision = clustered, "clustered"
    else:
        groups, decision = pooled, "pooled"
    # Oldest group first: the group holding the longest-waiting PR
    # verifies first.
    groups = sorted(groups,
                    key=lambda cluster: min(
                        float(by_pr[number].get("enqueued_at") or now)
                        for number in cluster)) if groups else []

    pairs = sorted(
        {(left, right) for left in graph for right in graph[left]
         if left < right})
    advice = format_advice(numbers=len(numbers), pairs=pairs,
                            graph=graph, pooled_cost=pooled_cost,
                            clustered=clustered,
                            clustered_costs=clustered_costs,
                            decision=decision, cold=cold)
    return {"groups": groups,
            "candidates": {"pooled": pooled_cost,
                           "clustered_total": clustered_total,
                           "clustered": clustered_costs},
            "graph": {number: dict(peers)
                      for number, peers in graph.items()},
            "advice": advice}


def format_advice(*, numbers: int, pairs: list, graph: dict,
                  pooled_cost: dict | None, clustered: list,
                  clustered_costs: list, decision: str,
                  cold: bool) -> list:
    """Printable grouping advice with the cost terms shown."""
    if numbers <= 1:
        return [("ren-ci grouping: single-PR cluster verifies "
                "immediately - no grouping tax")]
    lines = []
    if not pairs:
        lines.append("ren-ci grouping: no subsystem/selection overlap "
                     f"across {numbers} PRs - pooling freely "
                     f"(expected {pooled_cost['total']:.0f}s = "
                     f"verify {pooled_cost['verification']:.0f}s + "
                     f"diagnose {pooled_cost['diagnosis']:.0f}s + "
                     f"merge {pooled_cost['merge']:.0f}s + "
                     f"wait {pooled_cost['waiting']:.0f}s)")
        return lines
    for left, right in pairs:
        lines.append(f"ren-ci grouping: PR #{left} x PR #{right} "
                     f"{graph[left][right]}")
    pooled_line = (f"pooled {pooled_cost['total']:.0f}s "
                   f"(verify {pooled_cost['verification']:.0f}s + "
                   f"diagnose {pooled_cost['diagnosis']:.0f}s @ "
                   f"split-risk {pooled_cost['split_risk']:.2f} + "
                   f"merge {pooled_cost['merge']:.0f}s + "
                   f"wait {pooled_cost['waiting']:.0f}s)")
    clustered_line = " + ".join(
        f"{clustered[index]} {clustered_costs[index]['total']:.0f}s"
        for index in range(len(clustered)))
    lines.append(f"ren-ci grouping: {pooled_line} vs clustered "
                 f"{sum(item['total'] for item in clustered_costs):.0f}s "
                 f"[{clustered_line}] -> {decision}"
                 + (" (cold: no failure data, pooling)" if cold else ""))
    return lines


def advise_for_cluster(entries: list, *, state_dir: str = "",
                       now: float, select=None,
                       base: float | None = None) -> list:
    """Grouping advice for a ripe cluster, measured where possible.

    Production entry point: the verification term comes from
    `history.full_suite_wall_seconds` (Tier-2 logs, else timing
    telemetry, else the documented fallback - never zero), the base
    red rate from recorded failure rates when present (else cold).
    Never raises: advice must not fail a verdict that already passed.
    """
    try:
        from library.tools.local_ci import history
        wall = history.full_suite_wall_seconds(state_dir)
        if base is None:
            rates = history.failure_rates()
            values = [rate for rate in rates.values()
                      if isinstance(rate, (int, float))
                      and 0.0 < float(rate) < 1.0]
            base = (sum(float(rate) for rate in values) / len(values)
                    if values else 0.0)
        plan = plan_groups(entries, wall_s=wall, base=base, now=now,
                           select=select)
        return plan["advice"]
    except (OSError, ValueError, TypeError, KeyError):
        return [("ren-ci grouping: advice unavailable "
                "(cost inputs unreadable) - verifying as one cluster")]
