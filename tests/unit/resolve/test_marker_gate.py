"""The gate audits the promotion from outside its own report.

A promotion that destroyed two of the captain's clip markers printed
"carried x3, uncarried 0" - a FALSE ALL-CLEAR. The carry machinery
cannot report on a loss it never sees, so this gate re-reads the
live timeline after the rename, both planes, and diffs by identity
against a capture taken before it.

These fakes stand in for Resolve; the API surface they answer is
the one `promote_staged_reels` drives. Every failure assertion is
on marker TEXT and colour, never on counts alone: a gate that fails
on a number without naming the words is the count-only snapshot
that already destroyed a note here.
"""

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from library.tools import marker_carry, marker_gate
from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
)


@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)


def _clip_item(name, start, end, path, left=0, markers=None):
    pool_item = FakeMediaPoolItem(name)
    pool_item.SetClipProperty("File Path", path)
    return FakeTimelineItem(
        name,
        None,
        start=start,
        duration=end - start,
        left_offset=left,
        pool_item=pool_item,
        markers=markers,
    )


def _timeline(name, video=(), audio=(), markers=None, start=0):
    return FakeTimeline(
        name, start_frame=start, video=video, audio=audio, markers=markers
    )


FINAL = "Reel 29 - clip notes (final)"
MASTER = "Podcast - Synced"
TIMELINE_NOTE = "tighten this pause before the reveal"
CLIP_NOTE = "the lower third clips her chin here"


def _body(left=6505):
    return _clip_item("Archana A", 0, 600, "/f/LC4932.MXF", left=left)


def _retiring():
    card = _clip_item(
        "cta card",
        500,
        560,
        "/f/mg_cta.mov",
        left=0,
        markers={
            12: {
                "color": "Blue",
                "name": "feedback",
                "note": CLIP_NOTE,
                "duration": 1,
                "customData": "",
            }
        },
    )
    return _timeline(
        FINAL,
        video=[("Archana", [_body()]), ("Motion Graphics", [card])],
        markers={
            100: {
                "color": "Blue",
                "name": "feedback",
                "note": TIMELINE_NOTE,
                "duration": 1,
                "customData": "",
            }
        },
    )


def _staging(moved_card_to=570):
    card = _clip_item(
        "cta card", moved_card_to, moved_card_to + 60, "/f/mg_cta.mov", left=0
    )
    return _timeline(
        FINAL + " (rebuild staging)",
        video=[("Archana", [_body()]), ("Motion Graphics", [card])],
    )


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def _promote(project, project_dir, staged_to_final):
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    with patch(
        "library.tools.reel_build._connect_resolve_project", return_value=project
    ):
        return promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


def _captures(project_dir):
    capture_dir = project_dir / "pipeline_output" / "review" / "marker_captures"
    return sorted(capture_dir.glob("*.json")) if capture_dir.is_dir() else []


def test_a_promotion_that_carries_everything_passes_silently(project_dir):
    """Both planes carried: the gate files its capture and says nothing."""
    retired, staging = _retiring(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    promoted = _promote(project, project_dir, {FINAL: staging.GetName()})

    assert promoted["promoted"] == [FINAL]
    # The live inventory holds his words on both planes - TEXT, not
    # counts: a carry that kept the count while moving the words to
    # the wrong item is the defect, not the fix.
    live_t = marker_carry.read_markers(staging, FINAL)
    assert [m["note"] for m in live_t if m["color"] == "Blue"] == [TIMELINE_NOTE]
    live_c = marker_carry.read_clip_markers(staging, FINAL)
    assert [m["note"] for m in live_c] == [CLIP_NOTE]
    assert live_c[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert live_c[0]["source_frame"] == 12
    # And the capture was filed before the rename, holding them too.
    paths = _captures(project_dir)
    assert len(paths) == 1
    capture = marker_gate.read_capture(str(paths[0]))
    assert [m["note"] for m in capture["timeline"]] == [TIMELINE_NOTE]
    assert [m["note"] for m in capture["clip"]] == [CLIP_NOTE]


def test_a_reported_loss_is_owned_not_failed(project_dir):
    """The ambiguous clip anchor is reported by the carry, so the gate
    that re-reads the same live timeline must not fail it again."""
    retired = _timeline(
        FINAL,
        video=[
            (
                "Motion Graphics",
                [
                    _clip_item(
                        "doubled card",
                        100,
                        160,
                        "/f/mg_doubled.mov",
                        left=0,
                        markers={
                            20: {
                                "color": "Blue",
                                "name": "feedback",
                                "note": "this card flashes",
                                "duration": 1,
                                "customData": "",
                            }
                        },
                    )
                ],
            ),
        ],
    )
    staging = _timeline(
        FINAL + " (rebuild staging)",
        video=[
            (
                "Motion Graphics",
                [
                    _clip_item("doubled card", 100, 160, "/f/mg_doubled.mov", left=0),
                    _clip_item(
                        "doubled card encore", 160, 220, "/f/mg_doubled.mov", left=0
                    ),
                ],
            ),
        ],
    )
    project = FakeProject([_timeline(MASTER), retired, staging])

    promoted = _promote(project, project_dir, {FINAL: staging.GetName()})

    assert promoted["promoted"] == [FINAL]
    assert [m["note"] for m in promoted["markers"][FINAL]["clip_uncarried"]] == [
        "this card flashes"
    ]


def test_a_silent_clip_loss_refuses_by_name(project_dir):
    """The 2026-09-20 shape: the machinery's lists say carried while
    the live item holds nothing. The gate fails on the words."""
    retired, staging = _retiring(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    def _swallow_clip_markers(replacement, carried):
        return []  # claims success; places nothing

    with (
        patch.object(
            marker_carry, "place_clip_markers", side_effect=_swallow_clip_markers
        ),
        pytest.raises(ReelBuildError, match="MARKER GATE LOST") as lost,
    ):
        _promote(project, project_dir, {FINAL: staging.GetName()})

    message = str(lost.value)
    assert "Blue" in message
    assert CLIP_NOTE in message
    assert "mg_cta.mov" in message  # the alarm names where it lived
    # The timeline note survived, so it must NOT be named as lost.
    assert TIMELINE_NOTE not in message
    # The capture survives the refusal: recovery needs no archaeology.
    paths = _captures(project_dir)
    assert len(paths) == 1
    capture = marker_gate.read_capture(str(paths[0]))
    lost_clip = [m for m in capture["clip"] if m["note"] == CLIP_NOTE]
    assert len(lost_clip) == 1
    assert lost_clip[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert lost_clip[0]["anchor"]["source_frame"] == 12


def test_a_silent_timeline_loss_refuses_by_name(project_dir):
    """The same false-all-clear on the timeline plane."""
    retired, staging = _retiring(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    def _swallow_timeline_markers(timeline, carried):
        return []

    real_place_clip = marker_carry.place_clip_markers
    staging_card = staging.GetItemListInTrack("video", 2)[0]

    def _drop_clip_after_place(replacement, carried):
        failed = real_place_clip(replacement, carried)
        staging_card._markers.clear()  # gone after the plan vouched
        return failed

    with (
        patch.object(marker_carry, "place", side_effect=_swallow_timeline_markers),
        patch.object(
            marker_carry, "place_clip_markers", side_effect=_drop_clip_after_place
        ),
        pytest.raises(ReelBuildError, match="MARKER GATE LOST") as lost,
    ):
        _promote(project, project_dir, {FINAL: staging.GetName()})

    message = str(lost.value)
    assert TIMELINE_NOTE in message
    assert CLIP_NOTE in message


def test_a_fleet_shrink_on_an_untouched_reel_refuses(project_dir):
    """The backstop: a reel this promotion never touched reads back
    smaller, so the run stops even though the promoted reel is clean."""
    retired, staging = _retiring(), _staging()
    other = _timeline(
        "Reel 30 - bystander (final)",
        video=[("Archana", [_body()])],
        markers={
            10: {
                "color": "Blue",
                "name": "feedback",
                "note": "bystander note",
                "duration": 1,
                "customData": "",
            }
        },
    )

    reads = {"count": 0}
    real_get_markers = other.GetMarkers

    def _shrinking():
        reads["count"] += 1
        if reads["count"] == 1:
            return real_get_markers()
        return {}  # changed under the promotion, off this reel

    other.GetMarkers = _shrinking
    project = FakeProject([_timeline(MASTER), retired, staging, other])

    with pytest.raises(ReelBuildError, match="did not touch"):
        _promote(project, project_dir, {FINAL: staging.GetName()})


def test_a_fleet_addition_mid_run_only_reports(project_dir, capsys):
    """The captain adding a note to another reel mid-run is
    legitimate: noted on stdout, never a refusal."""
    retired, staging = _retiring(), _staging()
    other = _timeline("Reel 30 - bystander (final)", video=[("Archana", [_body()])])

    reads = {"count": 0}

    def _growing():
        reads["count"] += 1
        if reads["count"] == 1:
            return {}
        return {
            10: {
                "color": "Blue",
                "name": "feedback",
                "note": "typed mid-run",
                "duration": 1,
                "customData": "",
            }
        }

    other.GetMarkers = _growing
    project = FakeProject([_timeline(MASTER), retired, staging, other])

    promoted = _promote(project, project_dir, {FINAL: staging.GetName()})

    assert promoted["promoted"] == [FINAL]
    out = capsys.readouterr()
    assert "marker gate fleet note" in out.out
    assert "MARKER GATE" not in out.err


# ── The comparator from a separate process ──────────────────────
#
# An in-script GetMarkers once reported a marker on Reel 08 that did
# not exist, so the alarm half is also exercised across a process
# boundary: capture, live and accounted go to JSON files, and the
# module CLI diffs them in a fresh interpreter.


def _json_files(tmp_path, capture, live, accounted):
    paths = {}
    for name, payload in (
        ("capture", capture),
        ("live", live),
        ("accounted", accounted),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    return paths


def _capture_payload():
    return {
        "format": marker_gate.CAPTURE_FORMAT,
        "reel": FINAL,
        "captured_at": "2026-09-20T00:00:00+00:00",
        "timeline": [
            {
                "frame": 100,
                "color": "Blue",
                "name": "feedback",
                "note": TIMELINE_NOTE,
                "duration": 1,
                "custom_data": "",
                "anchor": ["/f/LC4932.MXF", 6605],
            }
        ],
        "clip": [
            {
                "plane": "clip",
                "frame": 512,
                "source_frame": 12,
                "color": "Blue",
                "name": "feedback",
                "note": CLIP_NOTE,
                "duration": 1,
                "custom_data": "",
                "anchor": {
                    "source_file": "/f/mg_cta.mov",
                    "track_type": "video",
                    "track_index": 2,
                    "clip_name": "cta card",
                    "timeline_start": 500,
                    "timeline_end": 560,
                    "source_start": 0,
                    "source_end": 60,
                    "source_frame": 12,
                },
                "unplaced_reason": "",
            }
        ],
    }


def _check(tmp_path, capture, live, accounted):
    paths = _json_files(tmp_path, capture, live, accounted)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "library.tools.marker_gate",
            "--check",
            paths["capture"],
            paths["live"],
            paths["accounted"],
            "--reel",
            FINAL,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def test_the_cli_fails_a_silent_loss_in_another_process(tmp_path):
    capture = _capture_payload()
    # The false all-clear: the live clip item holds nothing, and the
    # accounted lists claim nothing was lost.
    live = {"timeline": list(capture["timeline"]), "clip": []}
    accounted = {"timeline": [], "clip": []}

    completed = _check(tmp_path, capture, live, accounted)

    assert completed.returncode == 2
    assert "Blue" in completed.stderr
    assert CLIP_NOTE in completed.stderr
    assert TIMELINE_NOTE not in completed.stderr
