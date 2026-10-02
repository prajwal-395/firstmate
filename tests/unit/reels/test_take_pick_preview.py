"""One reel's take-pick preview, in a single read.

Each test names the defect it catches: a preview for the wrong reel,
an empty report that reads like a clean one, a second implementation
of the snap or the take scan drifting from the build, quiet edges
with no words, and a drifted trim the preview stays silent about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.reel_proposal import (
    SNAP_DECISION_SECONDS,
    Approval,
    ReelMoment,
    preview_snap,
    write_proposal,
)
from library.tools.take_pick_preview import (
    THRESHOLD_DEFAULT,
    TakePickPreviewError,
    boundary_words,
    main,
    preview_take,
    render_take_preview,
    resolve_moment,
)


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg(text, start, end, words):
    return {
        "speaker": "Host",
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/m/a.MXF",
        "source_start": 100.0,
        "source_end": 100.0 + (end - start),
        "resolve_item_id": "uid",
        "words": words,
    }


def _transcript():
    return {
        "segments": [
            _seg(
                "alpha beta",
                10.0,
                20.0,
                [_w("alpha", 10.5, 11.0), _w("beta", 12.0, 12.5)],
            ),
            _seg(
                "we launched the rocket",
                20.0,
                24.0,
                [
                    _w("we", 20.0, 20.2),
                    _w("launched", 20.3, 20.7),
                    _w("the", 20.8, 20.9),
                    _w("rocket", 21.0, 21.5),
                ],
            ),
            _seg(
                "gamma delta",
                19.5,
                30.0,
                [_w("gamma", 20.0, 20.5), _w("delta", 22.0, 22.5)],
            ),
            _seg(
                "we launched the rocket today",
                26.0,
                30.0,
                [
                    _w("we", 26.0, 26.2),
                    _w("launched", 26.3, 26.7),
                    _w("the", 26.8, 26.9),
                    _w("rocket", 27.0, 27.5),
                    _w("today", 27.6, 28.0),
                ],
            ),
            _seg(
                "epsilon zeta",
                29.5,
                40.0,
                [_w("epsilon", 31.0, 31.5), _w("zeta", 33.0, 33.5)],
            ),
        ]
    }


def _moment(number=7, start=19.7, end=33.6, slug="rocket-take"):
    return ReelMoment(
        number=number,
        slug=slug,
        reason="a complete exchange",
        timeline_start=start,
        timeline_end=end,
        approval=Approval.APPROVED,
    )


def _write_project(tmp_path, moments, transcript=None):
    """A scratch project holding a real proposal + transcript file."""
    from library.tools.reel_proposal import proposal_path
    from library.tools.timeline_transcript import transcript_path

    project = tmp_path / "project"
    transcript = _transcript() if transcript is None else transcript
    transcript_file = Path(transcript_path(str(project)))
    transcript_file.parent.mkdir(parents=True)
    transcript_file.write_text(json.dumps(transcript), encoding="utf-8")
    proposal_file = Path(proposal_path(str(project)))
    proposal_file.parent.mkdir(parents=True)
    write_proposal(proposal_file, moments, transcript)
    return project


# ------------------------------------------------- reuse, not rewrite


def test_the_snap_section_is_the_builds_own_preview(tmp_path):
    """`preview_take` narrows `preview_snap` to one moment rather than
    reimplementing it (byte-equal to the build's own call, at the snap
    bar), and its candidate cuts are `redundant_takes` over this reel's
    body - what the build will cut, not a second scan with its own bars."""
    from library.tools.reel_build import redundant_takes

    project = _write_project(tmp_path, [_moment(), _moment(8, 40.0, 45.0, "other")])
    report = preview_take(str(project), "7")
    assert THRESHOLD_DEFAULT == SNAP_DECISION_SECONDS
    assert report["snap"] == preview_snap(
        [_moment()], _transcript(), THRESHOLD_DEFAULT, tail_extend_authorizations={}
    )
    assert {m["reel"] for m in report["snap"]["moments"]} == {7}
    expected = redundant_takes(19.7, 33.6, _transcript())
    assert expected, "the fixture must hold a take or this pins nothing"
    assert [
        (t["dropped_start"], t["dropped_end"], t["kept_start"], t["kept_end"])
        for t in report["takes"]
    ] == [(c.dropped_start, c.dropped_end, c.kept_start, c.kept_end) for c in expected]


# ------------------------------------------------- addressing


def test_a_reel_answers_to_number_slug_and_timeline_name(tmp_path):
    """Number, slug and full timeline name all reach the same moment:
    a worker holding any one of the three spellings gets this reel."""
    project = _write_project(tmp_path, [_moment()])
    by_number = preview_take(str(project), "7")
    assert preview_take(str(project), "rocket-take")["slug"] == (by_number["slug"])
    assert (
        preview_take(str(project), "Reel 07 - rocket-take")["slug"]
        == (by_number["slug"])
    )
    _an_unknown_reel_refuses_rather_than_previewing_a_neighbour(tmp_path / "second")


def _an_unknown_reel_refuses_rather_than_previewing_a_neighbour(tmp_path):
    """A take-pick answered for the wrong reel is confidently wrong -
    worse than no answer - so an unknown reel is a refusal, and the
    CLI exits 2 rather than printing a neighbour's report."""
    tmp_path.mkdir()
    project = _write_project(tmp_path, [_moment()])
    with pytest.raises(TakePickPreviewError):
        resolve_moment([_moment()], "99")
    assert main([str(project), "--reel", "99"]) == 2
    # No proposal is not a reel with no takes, no drift and no snap: an
    # empty report would read as a clean one, so this refuses too.
    assert main([str(tmp_path / "empty"), "--reel", "7"]) == 2


# ------------------------------------------------- words once


def test_a_quiet_edge_still_names_its_words(tmp_path):
    """Edges the snap does not move get no pulled-in list - but the
    take-pick still needs the words there. Without this section the
    worker re-reads the whole transcript per quiet edge."""
    tx = {
        "segments": [
            _seg(
                "alpha beta gamma",
                10.0,
                20.0,
                [
                    _w("alpha", 10.5, 11.0),
                    _w("beta", 12.0, 12.5),
                    _w("gamma", 14.0, 14.5),
                ],
            ),
            _seg(
                "delta epsilon",
                20.0,
                30.0,
                [_w("delta", 21.0, 21.5), _w("epsilon", 23.0, 23.5)],
            ),
        ]
    }
    project = _write_project(tmp_path, [_moment(7, 10.0, 30.0)], tx)
    report = preview_take(str(project), "7")
    assert report["snap"]["moved"] == 0
    edges = {e["boundary"]: e for e in report["boundaries"]}
    assert [t["word"] for t in edges["body_start"]["words_after"]] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]
    assert [t["word"] for t in edges["body_end"]["words_before"]] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]
    text = render_take_preview(report)
    assert "words:" in text
    # The pivot walks one word stream: words at or before the edge land
    # before it, words after land after, nothing dropped at the joint.
    words = boundary_words(_transcript(), 20.0)
    assert [t["word"] for t in words["before"]][-3:] == ["alpha", "beta", "we"]
    assert [t["word"] for t in words["after"]][:2] == ["launched", "the"]


# ------------------------------------------------- freshness


def _write_edits(project, edits):
    from library.tools.captain_edits import edits_path

    path = Path(edits_path(str(project)))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"key": "captain_edits", "source": "test", "value": edits}),
        encoding="utf-8",
    )


def test_a_drifted_trim_is_loud_and_a_stale_one_named(tmp_path):
    """The Reel 17 shape: the head pin's anchor re-timed since it was
    recorded, and a second pin's words are spoken nowhere. A preview
    silent about either would send the worker to approve takes under
    trims the build will place elsewhere - or not at all."""
    tx = {
        "segments": [
            _seg(
                "alpha beta gamma",
                10.0,
                20.0,
                [
                    _w("alpha", 10.5, 11.0),
                    _w("beta", 12.0, 12.5),
                    _w("gamma", 14.0, 14.5),
                ],
            ),
            _seg(
                "delta epsilon",
                20.0,
                30.0,
                [_w("delta", 21.0, 21.5), _w("epsilon", 23.0, 23.5)],
            ),
        ]
    }
    project = _write_project(tmp_path, [_moment(7, 10.0, 30.0)], tx)
    _write_edits(
        project,
        [
            {
                "kind": "span_retime",
                "anchor_phrase": "alpha beta",
                "edge": "head",
                "recorded_edge": 12.0,
                "reason": "trim the head onto the opening",
            },
            {
                "kind": "span_retime",
                "anchor_phrase": "never spoken words",
                "edge": "tail",
                "reason": "a pin to nowhere",
            },
        ],
    )
    report = preview_take(str(project), "7")
    drifted = report["freshness"]["drifted"]
    stale = report["freshness"]["stale"]
    assert len(drifted) == 1
    assert drifted[0]["anchor_phrase"] == "alpha beta"
    assert any(s.get("anchor_phrase") == "never spoken words" for s in stale)
    text = render_take_preview(report)
    assert "DRIFTED" in text and "STALE" in text
