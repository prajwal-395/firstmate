"""The editor's trims and moves are carried through a rebuild, or refused.

Reel 7's shape again, with the two edits Resolve has no verb for: the
editor trimmed 37 frames off the tail of the Craig passage and closed
the gap (picture and sound), and dragged a Semantic graphic to sit over
a different moment of the Akshita shot. A rebuild restores the full
passage and the graphic's planned place. Carrying them goes through
`composed_edit` - delete and re-place - against the fake Resolve that
models what the composition defends against
(`tests/composed_edit_harness.py`), and the promoted timeline must
play what the edited one played, item for item.
"""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from tests.composed_edit_harness import (
    covering_window,
    frames_of,
    item,
    pool_clip,
    rows_timeline,
)
from tests.resolve_double import FakeProject
from tests.promotion_test_helpers import no_a_roll_track_plans

from library.tools.reel_build import ReelBuildError, promote_staged_reels

FINAL = "Reel 07 - number-one-on-google-invisible-to-ai"
STAGING = FINAL + " (rebuild staging)"
MASTER = "Podcast - Synced"
NAMES = {"V1": "Speakers", "V3": "Semantic", "A1": "Dialogue"}


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    (root / "pipeline_output" / "review" / "plan_provenance.json"
     ).write_text(json.dumps({"built_reels": [STAGING],
                              "plan_content_hash": "plan-v1"}),
                  encoding="utf-8")
    return root


def Timeline(name, rows):
    """A Reel 7 timeline with the field test's row names."""
    return rows_timeline(name, rows, track_names=NAMES)


def Project(timelines):
    """The live project, the master timeline current."""
    return FakeProject("Mock Project", timelines, current=timelines[0])


AKSHITA = pool_clip("/media/akshita.mov", frames=100_000)
CRAIG = pool_clip("/media/craig.mov", frames=100_000)
CARD = pool_clip("/media/semantic-card.mov", frames=200)
LATE = pool_clip("/media/semantic-late.mov", frames=200)


def reel(name, *, craig=437, late_at=700, card_on=True, comps=False):
    """V1 picture with A1 sound, and two Semantic graphics on V3."""
    shift = 437 - craig
    passages = [(AKSHITA, 22_232, 116, 0), (CRAIG, 22_257, craig, 116),
                (AKSHITA, 25_557, 194, 553 - shift),
                (AKSHITA, 60_745, 234, 747 - shift)]

    def picture(mpi, left, duration, start):
        frames = frames_of(mpi)
        windows = ([{"MediaSource": "Timeline", "GlobalIn": -left,
                     "GlobalOut": frames - left - 1,
                     "ClipTimeStart": -left,
                     "ClipTimeEnd": frames - left - 1,
                     "MediaID": "", "AudioTrack": "Timeline Audio"}]
                   if comps else [])
        return item(mpi, start, duration, left, comp_windows=windows)

    timeline = Timeline(name, {
        "V1": [picture(*p) for p in passages],
        "V3": [item(CARD, 20, 40, 0, enabled=card_on),
               item(LATE, late_at, 30, 0)],
        "A1": [item(mpi, start, duration, left)
               for mpi, left, duration, start in passages],
    })
    return timeline


def edited():
    """The editor's cut: Craig 37 frames shorter with the gap closed,
    the card off, and the late graphic dragged earlier (600, not the
    rippled 663) over Akshita source frame 25,641."""
    return reel(FINAL, craig=400, late_at=600, card_on=False)


def played(timeline):
    return {key: [(i.GetMediaPoolItem().GetName(), i.GetLeftOffset(),
                   i.GetDuration(), i.GetStart(), i.GetClipEnabled())
                  for i in items]
            for key, items in sorted(timeline.rows.items())}


def promote(project, project_dir):
    with ExitStack() as stack:
        stack.enter_context(patch(
            "library.tools.resolve_locale.scriptapp_preserving_locale"))
        stack.enter_context(patch(
            "library.tools.reel_build.resolve_project_exactly",
            return_value=project))
        stack.enter_context(patch(
            "library.tools.reel_disabled_clip_carry.carry_disabled_state",
            return_value={"unchanged_unmatched": [], "safe_replacements": [],
                          "carried": [], "refused": [], "matched": []}))
        stack.enter_context(patch(
            "library.tools.marker_feedback.read_notes", return_value=[]))
        stack.enter_context(patch(
            "library.tools.marker_gate.verify_promotion"))
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, {FINAL: STAGING},
            organise=False,
            track_plans=no_a_roll_track_plans({FINAL: STAGING}))


def provenance(project_dir):
    return json.loads((project_dir / "pipeline_output" / "review"
                       / "plan_provenance.json").read_text(encoding="utf-8"))


def test_a_rippled_trim_and_a_moved_graphic_are_carried(project_dir):
    live, staging = edited(), reel(STAGING)
    wanted = played(live)
    project = Project([Timeline(MASTER, {}), live, staging])

    promoted = promote(project, project_dir)

    assert promoted["promoted"] == [FINAL]
    assert staging.GetName() == FINAL
    assert played(staging) == wanted
    # The re-placed items kept their treatment: the reference copy is
    # gone and every grade came across.
    assert sorted(project.names()) == sorted([MASTER, FINAL])
    edits = provenance(project_dir)["carried_editor_edits"][FINAL]
    kinds = sorted((edit["kind"], edit["row"]) for edit in edits)
    assert kinds == [("enabled", "video:Semantic"),
                     ("move", "video:Semantic"),
                     ("trim", "audio:Dialogue"),
                     ("trim", "video:Speakers")]
    trim = next(edit for edit in edits if edit["kind"] == "trim"
                and edit["row"] == "video:Speakers")
    assert trim["ripple"] is True
    assert (trim["after"]["head"], trim["after"]["tail"]) == (0, 37)
    assert trim["wording"].startswith("Trim 'craig.mov' source 22,257..")
    move = next(edit for edit in edits if edit["kind"] == "move")
    assert move["after"]["anchor_source_frame"] == 25_641
    assert move["before"] == {"record_in": 700}


def test_the_next_rebuild_trims_and_moves_again_from_the_ledger(
        project_dir):
    live, staging = edited(), reel(STAGING)
    wanted = played(live)
    project = Project([Timeline(MASTER, {}), live, staging])
    promote(project, project_dir)

    again = project.adopt(reel(STAGING))
    doc = provenance(project_dir)
    doc["built_reels"] = [STAGING]
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json"
     ).write_text(json.dumps(doc), encoding="utf-8")
    promoted = promote(project, project_dir)

    assert promoted["promoted"] == [FINAL]
    assert played(again) == wanted


def test_a_trim_of_a_comp_bearing_clip_with_no_manifest_refuses_unwritten(
        project_dir):
    live, staging = edited(), reel(STAGING, comps=True)
    before = played(staging)
    project = Project([Timeline(MASTER, {}), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote(project, project_dir)

    assert "recorded fusion manifest" in str(refused.value)
    assert played(staging) == before
    assert sorted(project.names()) == sorted([MASTER, FINAL, STAGING])


def test_a_trim_under_another_rows_item_refuses_unwritten(project_dir):
    live, staging = edited(), reel(STAGING)
    # A graphic straddling the end of the Craig passage on both sides.
    for timeline, start in ((live, 500), (staging, 500)):
        timeline.add_item("video", 3, item(CARD, start, 80, 0))
        timeline.rows["V3"].sort(key=lambda placed: placed.GetStart())
    before = played(staging)
    project = Project([Timeline(MASTER, {}), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote(project, project_dir)

    message = str(refused.value)
    assert "a rippled trim under another row's item is not carried" in message
    assert "'semantic-card.mov' at record 500..580" in message
    assert played(staging) == before


def test_a_comp_bearing_trim_reruns_the_comp_pass_on_its_kept_range(
        project_dir):
    """The pass gets the recorded manifest with the trimmed spec moved."""
    staging = reel(STAGING, comps=True)
    live = reel(FINAL, craig=400, late_at=600, card_on=False, comps=True)
    wanted = played(live)
    project = Project([Timeline(MASTER, {}), live, staging])
    sources = [i.GetMediaPoolItem().GetClipProperty("File Path")
               for i in staging.rows["V1"]]
    manifest = {
        "fusion_effects": {"per_clip": {"craig": {"zoom": 1.2}}},
        "tracks": {"V1": {"clips": [
            {"label": f"clip{n}", "source_file": path,
             "source_in": 10.0, "source_out": 10.0 + 437 / 30}
            if path.endswith("craig.mov") else
            {"label": f"clip{n}", "source_file": path}
            for n, path in enumerate(sources)]}},
    }
    manifest["tracks"]["V1"]["clips"][1]["label"] = "craig"
    scratch = project_dir / "pipeline_output" / "scratch" / "reel_look"
    scratch.mkdir(parents=True)
    (scratch / "reel_07_number_one_on_google_invisible_to_ai_rebuild_staging"
     "_fusion_manifest.json").write_text(json.dumps(manifest),
                                         encoding="utf-8")
    passes = []

    def comp_pass(given, _folder, _project, timeline_name, **_kw):
        passes.append((given, timeline_name))
        for placed in project.GetCurrentTimeline().rows["V1"]:
            placed.comps = item(
                placed.GetMediaPoolItem(), placed.GetStart(),
                placed.GetDuration(), placed.GetLeftOffset(),
                comp_windows=[covering_window(
                    placed.GetDuration(), placed.GetLeftOffset(),
                    frames_of(placed.GetMediaPoolItem()))]).comps
        return True

    with patch("library.tools.reel_look.apply_comps", side_effect=comp_pass):
        promoted = promote(project, project_dir)

    assert promoted["promoted"] == [FINAL]
    assert played(staging) == wanted
    (given, timeline_name), = passes
    assert timeline_name == STAGING
    craig = given["tracks"]["V1"]["clips"][1]
    assert craig["source_in"] == pytest.approx(10.0)
    assert craig["source_out"] == pytest.approx(10.0 + 400 / 30)
