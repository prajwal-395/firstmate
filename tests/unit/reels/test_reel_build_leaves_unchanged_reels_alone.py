"""A build does not pay a Resolve pass for a reel nothing changed about.

The cost, MEASURED and already in the tree (`docs/RULE_EVIDENCE.md`,
"what it costs"): one reel's Resolve pass is 19.4-67.1 s, of which the
Fusion comp pass is 17.0-63.7 s and is FIXED overhead rather than
per-comp work.  `rebuild_reels_in_project` used to place every reel it
was asked for regardless, so a build of five reels where one changed
re-placed four identical timelines.

What these tests hold is the ROUND TRIP, not the digest arithmetic
(`tests/unit/reels/test_reel_rebuild_need.py` holds that): build once, and the
second build of the same state leaves every reel alone - while a reel
whose live timeline drifted, or whose engine or plan moved, is placed
again.  A round trip is the only shape that proves the recorded
signature and the recomputed one are the SAME computation; two
hand-written digests would agree by construction and prove nothing.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from library.tools.plan_provenance import read_provenance
from library.tools.reel_build import rebuild_reels_in_project
from tests.promotion_test_helpers import install_fake_timeline_snapshots
from tests.resolve_double import FakeProject

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in (1, 2, 3)]


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script, monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        f'resolve: {{project_name: "Mock Project", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


class FakeClip:
    """One placed item, as `carried_digest` reads it."""

    def __init__(self, pan=0.0):
        self.resolve_item_id = "id"
        self.track_type = "video"
        self.track_index = 1
        self.track_name = "Craig"
        self.speaker = "Craig"
        self.source_file = "/f/a.mov"
        self.source_in_frame = 0
        self.source_out_frame = 100
        self.source_frames = 1000
        self.timeline_start = 0.0
        self.timeline_end = 4.17
        self.name = "a.mov"
        self.transform = {"Pan": pan}
        # `placements` works in SECONDS off the master's own clips, so a
        # fake that carries only frames dies inside it - the same
        # PLAYED-versus-SOURCE distinction `TimelineClip` documents.
        self.source_in = 0.0
        self.source_out = 4.17

    @property
    def duration(self):
        return self.timeline_end - self.timeline_start


class FakeSnapshot:
    def __init__(self, name, clips):
        self.clips = tuple(clips)
        self.timeline_name = name
        self.project_name = "Mock Project"
        self.fps = 24000 / 1001
        self.reported_fps = 23.976
        self.width = 1080
        self.height = 1920
        self.start_frame = 0
        self.end_frame = 100


class World:
    """What each timeline CARRIES, so a test can move one reel's
    picture and nothing else.

    A transform read off a timeline that is NOT the current one comes
    back wrong.  That is Resolve's real behaviour, measured 2026-09-12
    (`reel_rebuild_need.carried_digest_live`): three untouched reels
    read four times, under four different current timelines, gave four
    different digests.  Modelled here with a single wrong term rather
    than the real four-way table, because what the build has to be
    immune to is the DEPENDENCE, not its exact shape.
    """

    def __init__(self, project=None):
        self.pan = {}
        self.project = project

    def snapshot(self, timeline, project_name):
        name = timeline.GetName()
        pan = self.pan.get(_final_of(name), 0.0)
        if self.project is not None:
            current = self.project.GetCurrentTimeline()
            if current is not None and current is not timeline:
                pan += MISREAD_WHEN_NOT_CURRENT
        return FakeSnapshot(name, [FakeClip(pan)])


MISREAD_WHEN_NOT_CURRENT = 1.0
"""How far a transform read off a non-current timeline lands out."""


def _final_of(name):
    """The approved name behind a container, staging suffix stripped."""
    from library.tools.reel_build import STAGING_SUFFIX
    return (name[: -len(STAGING_SUFFIX)]
            if name.endswith(STAGING_SUFFIX) else name)


@contextmanager
def _patched(resolve_project, world, moments=None):
    def _place(**kwargs):
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place) as placed, \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale."
                  "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=moments or [_moment(i + 1, name)
                                           for i, name
                                           in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=world.snapshot), \
            patch("library.tools.reel_conformance_verifier."
                  "run_verification", return_value=0):
        yield placed


def _build(project_dir, world, **kwargs):
    resolve_project = kwargs.pop("resolve_project")
    with _patched(resolve_project, world) as placed:
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed


# ── The round trip ───────────────────────────────────────────────────

def test_the_first_build_places_everything_and_records_its_signature(
        project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert sorted(record["timelines_built"]) == sorted(APPROVED)
    assert record["reels_left_alone"] == []
    assert placed.call_count == 3
    # Every decision says REBUILD, and every one says WHY.
    assert {d["action"] for d in record["rebuild_need"]} == {"rebuild"}
    assert all(d["reason"] for d in record["rebuild_need"])

    signatures = read_provenance(
        str(project / "pipeline_output" / "review"))["build_signatures"]
    assert sorted(signatures) == sorted(APPROVED)
    for entry in signatures.values():
        # BOTH halves - the derivation from the build and the carried
        # digest closed by the promotion.
        assert len(entry["derivation"]) == 64
        assert len(entry["carried"]) == 64


def test_the_second_build_of_the_same_state_places_nothing(project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)
    before = sorted(resolve_project.names())

    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert placed.call_count == 0, (
        "the second build placed a reel nothing changed about - the "
        "Resolve pass it was meant to save is the whole point")
    assert record["timelines_built"] == []
    assert sorted(record["reels_left_alone"]) == sorted(APPROVED)
    assert {d["action"] for d in record["rebuild_need"]} == {"leave_alone"}
    # Nothing was placed, nothing was renamed, nothing was deleted.
    assert sorted(resolve_project.names()) == before


def test_the_gate_is_not_called_with_an_empty_scope(project):
    """`verify_built_reels` refuses a scope of zero reels on purpose -
    a gate that passes having graded nothing reads as coverage. A build
    that placed nothing because nothing needed placing must not reach
    it."""
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)
    with _patched(resolve_project, world), \
            patch("library.tools.reel_build.verify_built_reels") as gate:
        rebuild_reels_in_project(str(project), organise=False)
    gate.assert_not_called()


def test_only_the_reel_whose_picture_drifted_is_placed_again(project):
    """One reel of three - the realistic build the profile priced."""
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    world.pan[APPROVED[1]] = 137.0            # a hand edit on one reel
    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert record["timelines_built"] == [APPROVED[1]]
    assert sorted(record["reels_left_alone"]) == sorted(
        [APPROVED[0], APPROVED[2]])
    assert placed.call_count == 1
    drifted = [d for d in record["rebuild_need"]
               if d["reel"] == APPROVED[1]][0]
    assert "drifted" in drifted["reason"]


def test_a_changed_plan_places_every_reel_again(project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    plan = project / "pipeline_output" / "review" / "reel_proposals_v2.json"
    plan.write_text(json.dumps([{"changed": True}]), encoding="utf-8")

    record, placed = _build(project, world,
                            resolve_project=resolve_project)
    assert placed.call_count == 3
    assert record["reels_left_alone"] == []


def test_a_per_reel_pin_store_does_not_place_the_other_reels(project):
    """A pin on one reel must cost one reel.

    The file is written with a pin scoped to a speaker this project
    has no caption for, so no reel's derivation changes and every one
    is still left alone - which is the half that proves the store is
    not being folded into the project-wide digest.
    """
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    external = project / "external"
    external.mkdir(exist_ok=True)
    (external / "caption_timing.json").write_text(json.dumps({
        "version": 1,
        "pins": [{"scope": {"speaker": "Nobody"},
                  "offset_frames": 3,
                  "reason": "a pin this project has no card for"}]}),
        encoding="utf-8")

    record, placed = _build(project, world,
                            resolve_project=resolve_project)
    assert placed.call_count == 0
    assert sorted(record["reels_left_alone"]) == sorted(APPROVED)


def test_a_reel_reads_the_same_however_the_run_entered(project):
    """Whichever timeline a run enters on, the same reels are skipped.

    A transform does not read back the same way twice: what Resolve
    returns depends on which timeline is CURRENT at the moment of the
    read (measured 2026-09-12 - three untouched reels, four current
    timelines, four digests: `reel_rebuild_need.carried_digest_live`).
    Promotion closed every record with the LAST promoted reel current
    and the next build compared them with the ENTRY timeline current,
    so every reel but a coincidentally-matching one read as drifted and
    was placed again - the fail-closed direction, and exactly the
    saving the decision exists for.

    So the read is taken with the reel ITSELF current, at both ends,
    and the run's entry point stops mattering.
    """
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    for entry in list(resolve_project.timelines):
        resolve_project.SetCurrentTimeline(entry)
        record, placed = _build(project, world,
                                resolve_project=resolve_project)
        assert placed.call_count == 0, (
            f"entering on {entry.GetName()!r} placed a reel nothing "
            f"changed about")
        assert sorted(record["reels_left_alone"]) == sorted(APPROVED)
