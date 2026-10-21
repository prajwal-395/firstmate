"""A promotion resolves the timeline it replaces by IDENTITY.

Defect (Reel 26 audit §2.6): promotion confused names among staging
timelines. The field-test project holds six timelines named `Reel 26 -
write-for-the-question-your-customer-ask` and a crowd of `(rebuild
staging)` names, so a name cannot say which generation a promotion
replaces - and a promotion that cannot say which one it is replacing
must not rename any of them.

The rule: the NAME is an assertion and the Resolve unique ID is the
identity. An ambiguous name is resolved by the id the version ledger
recorded for the generation being replaced; an ambiguous name with no
recorded id, or an id that is missing or reused, refuses BEFORE
anything is renamed.
"""

from __future__ import annotations

import json

import pytest

from library.tools import reel_build

REEL = "Reel 26 - write-for-the-question-your-customer-ask"


class FakeTimeline:
    def __init__(self, name, unique_id):
        self._name = name
        self._unique_id = unique_id

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._unique_id


class FakeProject:
    def __init__(self, timelines):
        self._timelines = list(timelines)

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]


def _inventory(timelines):
    by_name: dict = {}
    by_id: dict = {}
    for timeline in timelines:
        entry = {"name": timeline.GetName(),
                 "unique_id": timeline.GetUniqueId()}
        by_name.setdefault(entry["name"], []).append(entry)
        by_id.setdefault(entry["unique_id"], []).append(entry)
    return by_name, by_id


def _ledger(tmp_path, timeline_id):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "pipeline_output" / "review" / "reel_versions.json").write_text(
        json.dumps({
            "format": "reel_versions/1",
            "reels": {REEL: [{
                "version": 23,
                "kind": "build",
                "rows": {},
                "rows_digest": "",
                "recovery_archive": {"timeline_id": timeline_id},
            }]},
        }), encoding="utf-8")
    return project


def test_an_ambiguous_name_is_resolved_by_the_recorded_resolve_id(tmp_path):
    """Six timelines, one name: the id decides."""
    project = _ledger(tmp_path, "the-live-one")
    timelines = [FakeTimeline(REEL, f"stale-{index}") for index in range(3)]
    timelines.append(FakeTimeline(REEL, "the-live-one"))
    by_name, by_id = _inventory(timelines)

    originals = reel_build._retiring_timelines_by_identity(
        FakeProject(timelines), str(project), [REEL], by_name, by_id)

    assert originals[REEL].GetUniqueId() == "the-live-one"


def test_an_ambiguous_name_with_no_recorded_id_refuses(tmp_path):
    """Nothing can say which one, so nothing is renamed."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    timelines = [FakeTimeline(REEL, f"stale-{index}") for index in range(3)]
    by_name, by_id = _inventory(timelines)

    with pytest.raises(reel_build.PromotionFailure):
        reel_build._retiring_timelines_by_identity(
            FakeProject(timelines), str(project), [REEL], by_name, by_id)


def test_a_recorded_id_that_is_reused_refuses(tmp_path):
    """An id used twice is not an identity."""
    project = _ledger(tmp_path, "reused")
    timelines = [FakeTimeline(REEL, "reused"),
                 FakeTimeline(f"{REEL} (rebuild staging)", "reused")]
    by_name, by_id = _inventory(timelines)

    with pytest.raises(reel_build.PromotionFailure):
        reel_build._retiring_timelines_by_identity(
            FakeProject(timelines), str(project), [REEL], by_name, by_id)


def test_an_unambiguous_name_is_resolved_by_name_and_its_id_agrees(tmp_path):
    """The ordinary case still holds: one name, one id, one timeline."""
    project = _ledger(tmp_path, "the-live-one")
    timelines = [FakeTimeline(REEL, "the-live-one"),
                 FakeTimeline(f"{REEL} (rebuild staging)", "staging-one")]
    by_name, by_id = _inventory(timelines)

    originals = reel_build._retiring_timelines_by_identity(
        FakeProject(timelines), str(project), [REEL], by_name, by_id)

    assert originals[REEL].GetUniqueId() == "the-live-one"


def test_the_recorded_id_is_read_from_the_version_ledger(tmp_path):
    """The identity comes from the ledger, not from a guess."""
    project = _ledger(tmp_path, "the-live-one")

    assert reel_build._recorded_timeline_id(str(project), REEL) == \
        "the-live-one"
