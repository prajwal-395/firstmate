"""One place for every local-CI constant: labels, slots, floors, markers.

A threshold here is a DECISION with a reason, not a tunable: each names
the failure it prevents, so a future edit changes the reason first.
"""

from __future__ import annotations

import os

# The GitHub repo this service integrates. The daemon never guesses it:
# every status post and merge names this owner/name pair.
OWNER = "prajwal-395"
REPO = "video_editing_pilot"

# The commit-status context PR checks post under. One context, one
# verdict per head SHA: a new push overwrites nothing, it supersedes by SHA.
STATUS_CONTEXT = "ren-ci/pr-checks"

# A PR enters the merge queue when its checks are green AND it carries
# this label (or `ren ci ready <pr>` puts it there). Labels are the
# captain-visible half; the command is the agent half.
READY_LABEL = "ren-ci-ready"

# Tier-1 is the quick check, not the verdict: the dependent half is
# bounded by TEST TIME, not file count, so one hub-module PR cannot
# bill half the suite per push. The budget is filled from historical
# per-test durations (`scripts/pytest_timing.py` data); above it the
# run keeps the highest-priority tests (direct first, then by import
# distance) and leaves breadth to the Tier-2 batched full suite, which
# already runs everything on the exact stacked tree before any merge.
#
# Chosen value, from the measured Tier-1 latency distribution
# (2026-10-10, 231 checks in `~/.ren-ci/checks.json`): elapsed median
# 364s, mean 541s, passing median 249s. The old 30s budget could not
# hold: the mandatory set alone cost 360s serial (contract_audit
# 301s), so every run was "over budget" before selecting anything.
# With the audit and the other slow files moved to Tier-2-only below,
# the mandatory core costs ~59s serial (~30s wall at -n 2), and the
# corpus median selection (48 files, mostly sub-second) costs
# seconds. 300s of serial dependent test time (~2-3 min wall at -n 2)
# keeps ordinary PRs fully covered - the budget binds only hub-PR
# breadth, which Tier-2 already judges before any merge. Cheapest-
# first fill at 300s keeps 398 of 469 measured files reachable, so
# the exclusion list below stays at the five files that dominate it.
TIER1_TIME_BUDGET_SECONDS = 300

# A dependent file with no measured duration still costs the budget
# something, estimated in this order: the median of its own
# directory's measured files (a new resolve test costs what resolve
# tests cost, `history.estimate_duration`), then the suite-wide
# median, then this constant. Never zero (zero would let unmeasured
# files bypass the budget entirely) and never infinite (which would
# starve new tests of a run). Roughly the suite's median per-file
# cost, kept as the last resort when no telemetry exists at all.
TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS = 2.0


def tier1_time_budget_seconds() -> float:
    """The Tier-1 dependent time budget, overridable per machine.

    The constant is the default; REN_CI_TIER1_BUDGET_SECONDS (a plain
    number of seconds) wins when set and positive, so a loaded service
    machine can tighten or loosen the budget without a code change.
    """
    try:
        override = float(
            os.environ.get("REN_CI_TIER1_BUDGET_SECONDS", "").strip())
    except (TypeError, ValueError):
        return float(TIER1_TIME_BUDGET_SECONDS)
    if override > 0:
        return override
    return float(TIER1_TIME_BUDGET_SECONDS)

# Tier-2 owns the machine alone for one full-gate run. Its timeout
# SCALES from the measured full-suite wall time times a load factor,
# the same way Tier-1 is budgeted from measured durations: an unloaded
# 700s suite killed at a fixed 1200s under agent load (98% complete,
# whole run wasted) must instead bill what the machine needs right
# now. Floor keeps today's bound when the measurement is missing or
# tiny; ceiling keeps a hung gate from holding the machine forever.
TIER2_TIMEOUT_SECONDS = 20 * 60
TIER2_TIMEOUT_FLOOR_SECONDS = 20 * 60
TIER2_TIMEOUT_CEILING_SECONDS = 60 * 60
TIER2_TIMEOUT_LOAD_FACTOR = 2.0

# Fallback full-suite wall time when neither Tier-2 logs nor timing
# telemetry can measure one: the unloaded October 2026 suite (~700s).
TIER2_MEASURED_FALLBACK_WALL_SECONDS = 700.0

# How long one Tier-1 pytest run may take before the slot is reclaimed.
# A dependent selection is small; 30 minutes is a hung run, not a slow
# one.
RUN_TIMEOUT_SECONDS = 30 * 60

# Markers the CI selection NEVER runs: real-model tests need the ML
# interpreter the service machine may not carry, and resolve_live tests
# drive the captain's open Resolve instance. (Firstmate spec 3.)
PYTEST_MARKERS = "not real_model and not resolve_live"

# The three qualification files that DRIVE the running Resolve are
# excluded BY NAME even where unmarked: CI has no Resolve, and a local
# gate has to remember it every time (scripts/full_suite_gate.sh).
RESOLVE_DRIVING = (
    "tests/qualification/test_marker_capture_against_resolve.py",
    "tests/qualification/test_marker_feedback_against_resolve.py",
    "tests/qualification/test_resolve_qualification.py",
)

# The mandatory selection is TWO TIERS, not one unconditional set
# (CI-03): a tiny universal half runs on every check, and the global
# contract suite runs only when the change signals structural reach
# (`selection.mandatory_for` owns the signal; the status/report line
# names which half ran and why, so a contracts-skipped run never reads
# as a contracts-passed run).
#
# The universal half is the compiles/parity/wiring files that cost
# seconds: measured serial durations on this machine's telemetry
# (`~/.cache/ren/pytest-file-durations.json`, 541 files, 2026-10-12)
# are lane_routing 3.7s, composer 1.6s, ren_core_parity 0.3s,
# resolve_guard_wiring 15.7s - ~21s serial together (~10s wall at
# -n 2), so they stay mandatory OUTSIDE the dependent time budget.
ALWAYS_RUN_UNIVERSAL = (
    "tests/tooling/test_parallel_lane_routing.py",
    "tests/unit/resolve/test_composer.py",
    "tests/unit/resolve/test_ren_core_parity.py",
    "tests/contracts/test_resolve_guard_wiring.py",
)

# The conditional half: the global contract suite - the
# registry/schema/capability/source-policy invariants over the whole
# engine no filename predicts (34% of the suite is global-invariant
# shape - docs/CI_LAYERS.md). It runs when the change signals
# structural reach, never unconditionally.
CONTRACTS_DIR = "tests/contracts"

# Path signals of structural reach: shared tool modules every
# subsystem imports, the worker-protocol boundary, the test-layout
# contract itself, and the capability/execution registries by name (a
# registry change names the registry in the reason, even though the
# `library/tools/` prefix below already covers those paths).
STRUCTURAL_PREFIXES = (
    "library/tools/",
    "native/ren-protocol/",
)
STRUCTURAL_FILES = (
    "tests/layers.py",
    "tests/contracts/",
)
STRUCTURAL_REGISTRIES = (
    "library/tools/capabilities.py",
    "library/tools/operations.py",
    "library/tools/dag_adapter.py",
    "library/tools/capability_outputs.py",
    "library/tools/execution/",
)

# The fan-out signal: a change the selector maps to this many `unit/`
# subsystems (or more) counts as structurally reaching even when no
# path above names it. Measured 2026-10-12 with
# `scripts/select_dependent_tests.py --loop`: all 507
# `library/tools/*.py` files reach a median of 2 subsystems (26 at 0,
# 136 at 1, 118 at 2, 75 at 3, 47 at 4, 26 at 5, 14 at 6, 64 at 7, one
# each at 8 and 9); pipeline step files reach 6-7; docs/config-only
# changes reach 0. Four subsystems is the top-30% tail: leaf-module
# changes (1-3) stay on the universal half alone, while step-level and
# shared-module breadth still pulls the contract suite.
STRUCTURAL_FANOUT_SUBSYSTEMS = 4

# Tier-1 never runs these files; the Tier-2 batched full suite always
# does. Each dominates the time budget alone (measured serial seconds
# in `~/.cache/ren/pytest-file-durations.json`, 2026-10-10: audit
# 301s, gate_smoke 232s, watchdog 127s, monologue 87s, conversation
# 75s - 822s together, 39% of the whole suite), so keeping any of
# them mandatory vetoes every budget under ~5 min, and keeping them
# selectable lets one hub-PR fan-out bill them per push. Tier-1 names
# them on its status line (`daemon.run_tier1`'s tier2-only marker) so
# a partial run never reads as full coverage, and the runner passes
# them as `--ignore` so the `tests/contracts` directory entry above
# does not re-admit the audit through directory expansion.
TIER1_TIER2_ONLY = (
    "tests/contracts/test_contract_audit.py",
    "tests/tooling/test_gate_smoke.py",
    "tests/unit/resolve/test_resolve_watchdog.py",
    "tests/scenarios/test_golden_monologue.py",
    "tests/scenarios/test_golden_conversation.py",
)

# Refuse a run when the checkout volume holds less than this: a full
# disk was one of the four failures that retired the scratch scripts
# (they filled the disk to 100% with never-cleaned checkouts).
DISK_FLOOR_BYTES = 10 * 1024 * 1024 * 1024

# A timed-out Tier-1 run judged nothing: the head re-queues automatically
# instead of staying red on a verdict about the machine, not the code.
# At most this many timeout retries per head, with exponential backoff
# (60s, then 120s): a hung machine gets two more chances, not forever.
TIER1_TIMEOUT_MAX_RETRIES = 2
TIER1_TIMEOUT_RETRY_BASE_SECONDS = 60

# New-head detection poll. This is DETECTION, not a batch window: no
# test waits for it, and a ready PR starts the moment a slot frees.
# GitHub offers no push channel to a Mac behind NAT without opening a
# port, so a cheap REST poll (zero Actions minutes) is the local
# optimum; a webhook receiver is the documented future upgrade.
POLL_SECONDS = 30

# A ripe cluster takes every queued entry: the full gate is ONE process
# whatever the cluster size, so cluster size does not change machine
# memory or CPU, and no measurement ever backed a cap (2026-10-10: the
# old BATCH_MAX=4 was justified by bisect cost alone). The window
# (load.targets) decides WHEN a ripe cluster runs; nothing caps how big
# it may grow while the window is open.


def state_dir() -> str:
    """Where the daemon keeps state: the machine, never the repo.

    A checkout the service tests must never contain the service's own
    ledger, or a test selection could read it and a merge could carry it.
    """
    override = os.environ.get("REN_CI_STATE", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".ren-ci")


def log_dir() -> str:
    """launchd stdout/stderr land here (Firstmate spec 5)."""
    return os.path.join(os.path.expanduser("~"), "Library", "Logs", "ren-ci")
