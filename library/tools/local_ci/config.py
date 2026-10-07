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

# Tier-1 parallelism is MEASURED per dispatch (`load.targets()`), not
# fixed: a quiet machine runs up to TIER1_MAX jobs (load.TIER1_MAX), a
# loaded one serialises to one. pytest -n per job likewise follows the
# machine (1..2). The one fixed bound is Tier-2: a full-gate cluster
# owns the machine alone, always.
TIER2_TIMEOUT_SECONDS = 20 * 60

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

# The dependent selection plus these, always: the cross-cutting suites
# no filename predicts (Firstmate spec 1: contracts always run because
# 34% of the suite is global-invariant shape - docs/CI_LAYERS.md).
ALWAYS_RUN = (
    "tests/contracts",
    "tests/tooling/test_parallel_lane_routing.py",
    "tests/unit/resolve/test_composer.py",
    "tests/unit/resolve/test_ren_core_parity.py",
    "tests/contracts/test_resolve_guard_wiring.py",
)

# Refuse a run when the checkout volume holds less than this: a full
# disk was one of the four failures that retired the scratch scripts
# (they filled the disk to 100% with never-cleaned checkouts).
DISK_FLOOR_BYTES = 10 * 1024 * 1024 * 1024

# How long one Tier-1 pytest run may take before the slot is reclaimed.
# A dependent selection is small; 30 minutes is a hung run, not a slow
# one.
RUN_TIMEOUT_SECONDS = 30 * 60

# New-head detection poll. This is DETECTION, not a batch window: no
# test waits for it, and a ready PR starts the moment a slot frees.
# GitHub offers no push channel to a Mac behind NAT without opening a
# port, so a cheap REST poll (zero Actions minutes) is the local
# optimum; a webhook receiver is the documented future upgrade.
POLL_SECONDS = 30

# A cluster holds at most this many PRs: beyond it the full-gate run
# and the bisect fan-out cost more than a second cluster. The window
# (load.targets) decides WHEN a ripe cluster runs; this caps how big it
# may grow while the window is open.
BATCH_MAX = 4


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
