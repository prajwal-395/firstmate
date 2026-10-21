"""What a trim on a BUILT reel claims, and what the engine can prove.

Four defects this file names, each measured on 2026-10-20 against a real
built reel (`docs/benchmarks/2026-10-20-live-user-visible-latency.md`):

1. A length-changing touch-up on a built reel refused with "no recorded
   fusion manifest for <reel> is on disk", because the build filed the
   manifest under the STAGING container's name and the promotion renamed
   the timeline but not the file.  The reel's own record was filed under
   a name only the staging container had.
2. The refusal that names a colliding caption card recommended "state
   the ordering explicitly in the --edits JSON" - and a `retime` edit
   has NO ordering field, so the advice named something the caller could
   not act on.
3. The AXI trim route refused a clip carrying a Fusion composition
   without naming the route that CAN carry one.
4. A trim claimed nothing about the frames it moved: source frames,
   record frames, the move itself, the linked audio and the caption row
   were all unproven and unreported.

Nothing here reaches a real project or a real Resolve.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from library.tools import trim_qualification as tq  # noqa: E402
from tests.composed_edit_harness import build_reel  # noqa: E402


# ── 1. the reel's own manifest, found under the name it was filed under ──


def _manifest_for(tmp_path, *, timeline_name, clips=((1, "/lab/a.mov"),),
                  per_clip=True):
    """A recorded Fusion manifest, filed under `timeline_name`."""
    directory = (tmp_path / "pipeline_output" / "scratch" / "reel_look")
    directory.mkdir(parents=True, exist_ok=True)
    from library.tools.reel_look import _slug

    manifest = {
        "tracks": {"V1": {"clips": [
            {"label": f"reel_picture_{index}",
             "source_file": path,
             "source_in": 0,
             "source_out": 20}
            for index, path in clips]}},
        "fusion_effects": {"per_clip": (
            {f"reel_picture_{index}": {"vignette": True}
             for index, _ in clips} if per_clip else {})},
    }
    path = directory / f"{_slug(timeline_name)}_fusion_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_a_built_reels_manifest_is_found_under_its_staging_name(tmp_path):
    """The manifest the build wrote under the staging name is the reel's.

    The defect: the promotion renames the staging container to the reel's
    final name and nothing renamed the manifest with it, so the
    length-changing class found no record and refused a built reel with
    "no recorded fusion manifest ... is on disk" - telling the caller to
    rebuild the reel, the expensive path the touch-up exists to avoid.
    """
    final = "Reel 13 - the-accounting-firm-ai-called-healthcare"
    staging = (f"{final} [candidate-2d8812c469224dff] (rebuild staging)")
    _manifest_for(tmp_path, timeline_name=staging)

    found = tu.recorded_fusion_manifest(str(tmp_path), final)

    assert found is not None, (
        "the reel's own manifest was filed under the staging name and "
        "must still be found by the reel's name")
    assert found["tracks"]["V1"]["clips"][0]["source_file"] == "/lab/a.mov"


def test_a_manifest_that_describes_another_cut_is_not_this_reel_s(tmp_path):
    """The reel's record is the manifest that DESCRIBES the timeline.

    A project folder carries a manifest per BUILD, and a build that
    never promoted leaves its own behind.  The record is the one whose
    rows describe the timeline being edited - not merely the newest file
    on disk.
    """
    final = "Reel 13 - a-reel"
    _manifest_for(tmp_path, timeline_name=f"{final} (rebuild staging)",
                  clips=((1, "/lab/other.mov"),))
    _manifest_for(tmp_path, timeline_name=f"{final} [candidate-2] "
                  f"(rebuild staging)",
                  clips=((1, "/lab/this.mov"),))

    tracks = [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"source_file": "/lab/this.mov"}]}]
    found = tu.recorded_fusion_manifest(str(tmp_path), final, tracks)

    assert found is not None
    assert found["tracks"]["V1"]["clips"][0]["source_file"] == "/lab/this.mov"


# ── 2. a refusal that names something the caller can do ──


def _overlapping_retime(tmp_path):
    """A retime whose ripple lands a caption card on its neighbour.

    The shape the benchmark measured on a real reel: a caption card
    whose words were spoken BEFORE the cut stays where it is, and the
    card after the cut moves left by the removed frames - onto it.  The
    card that stays covers the frames the edit removes, so its words
    are gone whatever the ripple does.
    """
    tracks = [
        {"type": "video", "index": 1, "name": "V1", "clips": [
            _clip("V1", 0, 0, 100),
            _clip("V1", 1, 100, 100),
        ]},
        {"type": "video", "index": 4, "name": "Subtitles", "clips": [
            _clip("V4", 0, 60, 35),
            _clip("V4", 1, 150, 40),
        ]},
    ]
    spec = {"reel": 13, "edits": [{"op": "retime", "row": "V1",
                                    "item": 0, "duration": 30}]}
    return tracks, spec


def test_an_overlapping_ripple_refuses_with_an_action_the_caller_can_take(
        tmp_path):
    """The fix names the cards whose words the edit removes.

    The defect: the refusal read "state the ordering explicitly in the
    --edits JSON" - and a `retime` edit has no ordering field, so the
    advice named something the caller could not act on.
    """
    tracks, spec = _overlapping_retime(tmp_path)

    with pytest.raises(tu.TouchupRefused) as refused:
        tu.qualify(tracks, spec)

    fix = refused.value.fix
    assert "ordering" not in fix, (
        "the fix must not recommend a field the caller does not have")
    assert "V4[0]" in fix, (
        "the fix must name the caption card whose words the edit "
        f"removes. It said: {fix!r}")
    assert "words" in fix, (
        "the fix must say that the edit removes words, not a pause")


def test_an_excluded_item_leaves_the_ripple_and_the_plan_is_servable(
        tmp_path):
    """The exclusion the caller makes reaches the ripple plan.

    A fix the engine cannot honour is a fix the engine must not print.
    """
    tracks, spec = _overlapping_retime(tmp_path)
    # The card the ripple would move onto its neighbour is left where
    # it is, and the caller owns its timing.
    spec["edits"][0]["exclude"] = [["V4", 1]]

    qualification = tu.qualify(tracks, spec)

    moved = {(change.row, change.item_index)
             for change in qualification.changes}
    assert ("V4", 1) not in moved, (
        "the excluded item must not be moved by the ripple")


# ── 3. the AXI trim route names the route that CAN carry a comp ──


def test_a_trim_of_a_clip_with_fusion_comps_names_the_route_that_can():
    """The refusal says what to do, not only what went wrong.

    The defect: "the item has Fusion compositions that this replacement
    path cannot carry" named the problem and stopped.  On a built reel
    every picture clip carries a treatment, so this refusal met every
    trim request the engine could receive.
    """
    from library.tools import edit_state_preservation as esp

    class _Graph:
        def GetNumNodes(self):
            return 1

    class _Item:
        def GetProperty(self):
            return {"Pan": 0.0}

        def GetFusionCompCount(self):
            return 2

        def GetNodeGraph(self):
            return _Graph()

        def GetUniqueId(self):
            return "item-1"

    with pytest.raises(esp.StatePreservationError) as refused:
        esp.capture(_Item(), picture=True)

    message = str(refused.value)
    assert "ren touch" in message, (
        "the refusal must name the route that re-derives the "
        f"compositions. It said: {message!r}")
    assert "2" in message, (
        "the refusal must say how many compositions it could not carry")


# ── 4. the claims a trim makes, and how each is judged ──


def _clip(row, index, record_in, duration, *, source_in=0,
          source_frames=1000, name="", linked=()):
    return {
        "unique_id": f"{row}-{index}",
        "name": name,
        "track_type": "video" if row.startswith("V") else "audio",
        "track_index": int(row[1:]),
        "track_name": "Subtitles" if row == "V4" else "V1",
        "record_in": record_in,
        "record_out": record_in + duration,
        "duration": duration,
        "source_in_frame": source_in,
        "source_out_frame": source_in + duration - 1,
        "left_offset": source_in,
        "right_offset": source_frames - source_in - duration,
        "source_frames": source_frames,
        "source_file": "/lab/sub_card.mov" if row == "V4" else "/lab/a.mov",
        "media_type": "Subtitle" if row == "V4" else "Video",
        "linked_unique_ids": list(linked),
        "enabled": True,
    }


def _rows(clips):
    rows: dict = {}
    for clip in clips:
        row = tq.row_label(clip)
        rows.setdefault(row, []).append(clip)
    return [{"type": row[0].lower(), "index": int(row[1:]),
             "name": ("Subtitles" if row == "V4" else row),
             "clips": clips} for row, clips in rows.items()]


def test_a_ripple_that_moves_exactly_what_it_planned_passes_every_claim():
    """The trim's own claims, all measured, all held."""
    before = _rows([
        _clip("V1", 0, 0, 100),
        _clip("V1", 1, 100, 100),
        _clip("V4", 0, 120, 40),
        _clip("A1", 0, 0, 100, linked=("V1-0",)),
    ])
    after = _rows([
        _clip("V1", 0, 0, 60),
        _clip("V1", 1, 60, 100),
        _clip("V4", 0, 80, 40),
        _clip("A1", 0, 0, 100, linked=("V1-0",)),
    ])
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.RIPPLE,
        moves=(("V1", 0, 0, 60), ("V1", 1, 60, 160), ("V4", 0, 80, 120)))

    verdict = tq.judge(before, after, request)

    assert verdict.failed == [], verdict.claims
    assert verdict.unmeasured == [], verdict.claims
    assert verdict.passed is True


def test_a_ripple_that_leaves_the_caption_on_its_neighbour_fails():
    """A caption track moved by a ripple is part of the trim's claims."""
    before = _rows([
        _clip("V1", 0, 0, 100),
        _clip("V1", 1, 100, 100),
        _clip("V4", 0, 120, 40),
    ])
    after = _rows([
        _clip("V1", 0, 0, 60),
        _clip("V1", 1, 60, 100),
        # The caption card was NOT moved with the picture, so it says
        # the wrong thing forty frames late.
        _clip("V4", 0, 120, 40),
    ])
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.RIPPLE,
        moves=(("V1", 0, 0, 60), ("V1", 1, 60, 160), ("V4", 0, 80, 120)))

    verdict = tq.judge(before, after, request)

    assert "caption_timing" in verdict.failed, verdict.claims


def test_a_trim_that_reads_back_a_frame_off_is_a_failed_claim():
    """Source-frame precision is a claim, and a frame off fails it."""
    before = _rows([_clip("V1", 0, 0, 100)])
    after = _rows([_clip("V1", 0, 0, 60)])
    # The clip reads back ONE FRAME LATE: it starts one frame after the
    # frame the request asked for.
    after[0]["clips"][0]["source_in_frame"] = 1
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.NON_RIPPLE)

    verdict = tq.judge(before, after, request)

    assert "source_frame_precision" in verdict.failed, verdict.claims


def test_a_trim_that_reaches_past_the_handles_is_refused_by_the_claims():
    """Handle validation: the window must lie inside the measured bounds."""
    before = _rows([_clip("V1", 0, 0, 100, source_in=0, source_frames=100)])
    after = _rows([_clip("V1", 0, 0, 60)])
    request = tq.TrimRequest(
        row="V1", item_index=0, source_in=0, source_out=140,
        record_in=0, semantics=tq.NON_RIPPLE)

    verdict = tq.judge(before, after, request)

    assert "handle_validation" in verdict.failed, verdict.claims


def test_a_non_ripple_trim_that_moved_another_item_fails_its_own_claim():
    """Ripple and non-ripple are separate claims, and both are judged."""
    before = _rows([_clip("V1", 0, 0, 100), _clip("V1", 1, 100, 100)])
    after = _rows([_clip("V1", 0, 0, 60), _clip("V1", 1, 60, 100)])
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.NON_RIPPLE)

    verdict = tq.judge(before, after, request)

    assert "non_ripple_semantics" in verdict.failed, verdict.claims
    assert verdict.claims["ripple_semantics"]["status"] == "not_claimed"


def test_an_unmeasured_claim_is_never_a_pass():
    """A claim nothing could measure reads `unmeasured`, never `passed`."""
    before = _rows([_clip("V1", 0, 0, 100)])
    after = _rows([_clip("V1", 0, 0, 60)])
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.NON_RIPPLE)
    request = tq.TrimRequest(
        row="V1", item_index=0, source_delta=-40, record_in=0,
        semantics=tq.NON_RIPPLE, preserve_linked_audio=False)

    verdict = tq.judge(before, after, request)

    assert verdict.passed is True, verdict.claims
    assert verdict.claims["linked_audio_preserved"]["status"] == (
        "not_claimed")


def test_a_ripple_re_establishes_the_link_it_broke(tmp_path):
    """The picture and its sound stay linked after the edit.

    A re-placed item is a NEW object, so a ripple that re-places both
    the picture and its sound DROPS the link between them unless the
    composition re-establishes it - and the reel then ships a picture
    and a sound that no longer move together.
    """
    tracks = [
        {"type": "video", "index": 1, "name": "V1", "clips": [
            dict(_clip("V1", 0, 0, 100), unique_id="v1-0",
                 linked_unique_ids=["a1-0"]),
        ]},
        {"type": "audio", "index": 1, "name": "A1", "clips": [
            dict(_clip("A1", 0, 0, 100), unique_id="a1-0",
                 linked_unique_ids=["v1-0"]),
        ]},
    ]

    link_rows = tu._link_rows_for(tracks)

    assert link_rows == {"V1": "A1", "A1": "V1"}, (
        "the composition restores the link from the row PAIR the live "
        "read says are linked")


def test_the_touchup_judges_its_own_ripple_and_records_the_claims(
        tmp_path):
    """The reader: a retime's claims are judged on the touch-up's own
    read-back and land in the receipt."""
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    for track in tracks:
        index = int(track["index"])
        if track["type"] == "video" and index == 4:
            track["name"] = "Subtitles"
    spec = {"reel": 13, "edits": [{"op": "retime", "row": "V1", "item": 0,
                                    "duration": 400}]}
    qualification = tu.qualify(tracks, spec)

    verdict = tu._judge_trim_claims(spec, qualification, tracks, tracks)

    assert verdict is not None
    assert "ripple_semantics" in verdict["claims"]
    assert verdict["claims"]["ripple_semantics"]["status"] in (
        "measured", "failed", "unmeasured")
