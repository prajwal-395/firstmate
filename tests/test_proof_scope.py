"""Proof in proportion to the edit, where the build reads it.

The 2026-09-11 round earned its 35-still, full-census, double-build
proof: it was a brand new mechanism. As a standing requirement for
every small edit that burden costs 10-15 minutes a round. The rule:
a new mechanism earns the full burden; a re-run of an established
one earns stills for the regions that actually changed, and a census
only when something disagrees. The kind never scales - stills are
real pixels at every level; only how much is proven does.

Proven here as the build reads it: `scope_for_build` maps the
mechanical inputs (plan newness off provenance, declarations
attached, the pre-build census verdict) to a scope, and the rebuild
prints it and records it on its record. It is guidance, never a
gate - nothing here can fail a build.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from library.tools import proof_scope

REELS = ["Reel 01 - hook (final)", "Reel 02 - promise (final)"]


def _scope(**over):
    base = {"reels": REELS, "plan_is_new": False,
            "shared_declarations": [], "disagreement_reported": False,
            "reels_without_prior_proof": []}
    base.update(over)
    return proof_scope.scope_for_build(**base)


def test_a_new_plan_earns_the_full_burden():
    scope = _scope(plan_is_new=True)

    assert scope["level"] == "FULL"
    assert scope["stills"] == REELS
    assert scope["census"] == "full"
    assert any("no recorded build" in reason
               for reason in scope["reasons"])


def test_declarations_earn_the_full_burden():
    scope = _scope(
        shared_declarations=["2 keep insistence(s) recorded"])

    assert scope["level"] == "FULL"
    assert scope["census"] == "full"
    assert "keep insistence" in " ".join(scope["reasons"])


def test_a_census_disagreement_earns_the_full_burden():
    scope = _scope(disagreement_reported=True)

    assert scope["level"] == "FULL"
    assert scope["census"] == "full"


def test_a_clean_rerun_owes_stills_only_for_unproven_reels():
    scope = _scope(reels_without_prior_proof=[REELS[1]])

    assert scope["level"] == "REDUCED"
    assert scope["stills"] == [REELS[1]]
    assert scope["census"] == "touched-only"


def test_a_fully_proven_rerun_owes_no_post_build_census():
    scope = _scope()

    assert scope["level"] == "REDUCED"
    assert scope["stills"] == []
    assert scope["census"] == "pre-build-only"
    text = proof_scope.render_scope(scope)
    assert "no post-build census" in text or "pre-build report" in text


def test_the_scope_says_real_pixels_at_every_level():
    """The captain's standing rule restated where the rule is read:
    what scales is how much, never what counts as proof."""
    text = proof_scope.render_scope(_scope(plan_is_new=True))

    assert "real pixels" in text


def test_plan_newness_reads_provenance_not_memory(tmp_path):
    """`build_inputs` compares the plan hash against the provenance
    record: same hash means a re-run, whatever anyone remembers."""
    review = tmp_path / "review"
    review.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"reels": REELS}), encoding="utf-8")
    from library.tools.plan_provenance import (
        plan_content_hash, write_provenance)

    content_hash = plan_content_hash(str(plan))
    (review / "plan_provenance.json").write_text(json.dumps(
        {"plan_content_hash": content_hash,
         "built_reels": REELS,
         "caption_hashes": {REELS[0]: "abc"}}), encoding="utf-8")

    inputs = proof_scope.build_inputs(str(review), str(plan), REELS)

    assert inputs["plan_is_new"] is False
    assert inputs["reels_without_prior_proof"] == [REELS[1]]

    plan.write_text(json.dumps({"reels": REELS + ["more"]}),
                    encoding="utf-8")
    assert proof_scope.build_inputs(
        str(review), str(plan), REELS)["plan_is_new"] is True


def test_no_provenance_is_a_new_plan_not_a_pass(tmp_path):
    """No record of proof is not proof: an unreadable or absent
    record degrades to FULL, never to REDUCED."""
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")

    inputs = proof_scope.build_inputs(str(tmp_path / "absent"),
                                      str(plan), REELS)

    assert inputs["plan_is_new"] is True
    scope = _scope(plan_is_new=inputs["plan_is_new"],
                   reels_without_prior_proof=inputs[
                       "reels_without_prior_proof"])
    assert scope["level"] == "FULL"


# ── The wiring: the build prints it and records it ───────────────

@pytest.fixture
def build_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        'timeline_name: "Master"}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text("{}", encoding="utf-8")
    return root


def test_the_build_records_and_prints_its_proof_scope(
        build_project, capsys):
    """A fresh project has no provenance, so the plan is new and the
    record carries a FULL scope the run also printed."""
    from library.tools.reel_build import rebuild_reels_in_project

    class _FakeTimeline:
        _seq = 0

        def __init__(self, name):
            self._name = name
            _FakeTimeline._seq += 1
            self._uid = f"fake-timeline-{_FakeTimeline._seq}"

        def GetName(self):
            return self._name

        def GetUniqueId(self):
            # `resolve_lock.assert_current_timeline` reads the cursor
            # back by this after setting it, so a double without an
            # identity cannot satisfy the guard.
            return self._uid

    class _FakeProject:
        def __init__(self, names):
            self.timelines = [_FakeTimeline(name) for name in names]
            pool = MagicMock()
            pool.CreateEmptyTimeline.side_effect = self._create
            self._pool = pool

        def _create(self, name):
            timeline = _FakeTimeline(name)
            self.timelines.append(timeline)
            return timeline

        def GetMediaPool(self):
            return self._pool

        def GetName(self):
            return "Mock Project"

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

        # A Resolve project HAS a cursor. `assert_current_timeline`
        # READS it before setting it - what it was on arrival is the
        # only evidence a foreign writer moved it - so a double that
        # cannot be pointed anywhere cannot model the guard. None until
        # something sets it: a project nobody has pointed anywhere has
        # no cursor, and inventing one hands the entry-unit guard a
        # timeline nobody opened.
        def GetCurrentTimeline(self):
            return getattr(self, "_current", None)

        def SetCurrentTimeline(self, timeline):
            self._current = timeline
            return True

    resolve_project = _FakeProject(["Master"])
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 1
    moment.timeline_name = "Reel 01 - hook"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0

    def _place(**place_kwargs):
        resolve_project.GetMediaPool().CreateEmptyTimeline(
            place_kwargs.get("timeline_name"))
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[moment]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"):
        record = rebuild_reels_in_project(
            str(build_project), organise=False, verify=False)

    assert record["proof_scope"]["level"] == "FULL"
    assert record["proof_scope"]["stills"] == ["Reel 01 - hook"]
    out = capsys.readouterr().out
    assert "Proof owed by this build: FULL" in out
