"""No GitHub-hosted minutes by default: the local CI owns automation.

Captain's ruling 2026-10-07: the project's CI runs on the captain's
Mac (ren-ci: `library/tools/local_ci/`, SOP docs/LOCAL_CI.md) and costs
zero Actions minutes. These tests fail any workflow that could spend a
hosted minute WITHOUT a human deliberately asking:

1. `ci.yml` is gone: its hosted PR CI is replaced by ren-ci commit
   statuses. Restoring the file fails loudly and forces this test's
   deliberate update - a restored workflow must never slip in as an
   unrelated file in a PR.
2. No workflow fires automatically: `pull_request` and branch `push`
   triggers are banned in every file. Automation nobody asked for is
   how the account burned 1,801 of 2,000 minutes by 5 September.
3. No AUTOMATIC job runs on a hosted runner: a job whose workflow can
   start itself must not name ubuntu/macos/windows runners. The one
   deliberate exception is tag-triggered releases (`.github/workflows/
   release.yml` on `v*` only): a human cuts a tag a handful of times a
   year, and the artifacts it builds are what `ren update` fetches -
   with no local replacement in scope, deleting it would break updates
   to save pennies. If release.yml ever gains an automatic trigger,
   rule 2 fails it.

`self-hosted` runners are not the answer either (docs/CI_LAYERS.md:
rejected - they compete with the fleet for cores, queue silently when
the Mac sleeps, and destroy the clean room), so no trigger set may
reach any runner at all except the deliberate tag release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="workflow validation uses PyYAML")

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

HOSTED = ("ubuntu-", "macos-", "windows-", "latest")


def _workflows():
    if not WORKFLOWS.is_dir():
        return {}
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted(WORKFLOWS.glob("*.yml"))}


def _triggers(workflow: dict) -> dict:
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, str):
        return {triggers: {}}
    if isinstance(triggers, list):
        return {name: {} for name in triggers}
    return dict(triggers or {})


def _is_automatic(triggers: dict) -> bool:
    if "pull_request" in triggers:
        return True
    push = triggers.get("push")
    if push is None:
        return False
    if isinstance(push, dict):
        # Tags-only (releases) is deliberate; branches (or no filter,
        # which is everything) is automatic.
        return "tags" not in push or "branches" in push or not push
    return True


def _runs_on(job: dict) -> list:
    runs_on = job.get("runs-on", "")
    if isinstance(runs_on, str):
        return [runs_on]
    return [str(item) for item in (runs_on or [])]


def test_pr_ci_workflow_is_gone():
    assert not (WORKFLOWS / "ci.yml").exists(), (
        "ci.yml is back: PR CI runs on ren-ci now (docs/LOCAL_CI.md) - "
        "restoring hosted PR jobs spends the minutes the captain retired. "
        "If this restoration is deliberate, update this test to say why.")


def test_no_workflow_fires_automatically():
    for name, workflow in _workflows().items():
        triggers = _triggers(workflow)
        assert not _is_automatic(triggers), (
            f"{name} fires automatically ({sorted(triggers)}): ren-ci "
            "owns automation now - an automatic workflow is unbilled "
            "minutes waiting to happen")


def test_no_automatic_job_can_reach_a_hosted_runner():
    for name, workflow in _workflows().items():
        triggers = _triggers(workflow)
        if not _is_automatic(triggers):
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            for runner in _runs_on(job or {}):
                assert not any(tag in runner for tag in HOSTED), (
                    f"{name}/{job_name} runs on hosted {runner!r}: "
                    "no automatic job may spend hosted minutes")


def test_tag_releases_stay_deliberate():
    release = WORKFLOWS / "release.yml"
    if not release.exists():
        pytest.skip("no release workflow to pin")
    workflow = yaml.safe_load(release.read_text(encoding="utf-8"))
    triggers = _triggers(workflow)
    assert not _is_automatic(triggers), (
        "release.yml gained an automatic trigger: tag releases are the "
        "one deliberate hosted exception - automate them and the "
        "exception mechanic fails open")
