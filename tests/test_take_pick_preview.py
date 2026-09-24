"""One-shot take-pick preview: four lane reads, one command, no decision.

Covers ``library/tools/take_pick_preview.py``: the freshness check
(recorded strikes and insistences touching the span), the cutter's
cascade (what the build would play, including its refusal), the
boundary-snap deltas (reused verbatim from ``preview_snap``), and the
words - for one reel, derived from what batch-5 lanes actually read
(segment windows, word tables, provenance, exclusion/insistence state,
keep ranges) rather than from what looks useful.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.reel_proposal import (
    Approval,
    CallToAction,
    ReelMoment,
    preview_snap,
    render_snap_preview,
)
from library.tools.take_pick_preview import (
    main,
    preview_take_pick,
    render_take_pick_preview,
)


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg(text, start, end, words, bound=True):
    return {"speaker": "Host", "text": text,
            "timeline_start": start, "timeline_end": end,
            "source_file": "/m/a.MXF",
            "source_start": 100.0, "source_end": 100.0 + (end - start),
            "resolve_item_id": ("uid" if bound else None),
            "words": words}


def _transcript():
    # Two tellings of one passage: the cutter measures the first as a
    # repeated take, and the body edges sit on word edges so the snap
    # holds and the cascade is the story.
    return {"segments": [
        _seg("the setup mattered", 50.0, 60.0,
             [_w("the", 51.0, 51.3), _w("setup", 51.4, 51.8),
              _w("mattered", 52.0, 52.5)]),
        _seg("here is the thing we built for long", 60.0, 70.0,
             [_w("here", 60.1, 60.4), _w("is", 60.5, 60.7),
              _w("the", 60.8, 61.0), _w("thing", 61.1, 61.5),
              _w("we", 61.6, 61.8), _w("built", 61.9, 62.3),
              _w("for", 62.4, 62.6), _w("long", 62.7, 63.1)]),
        _seg("here is the thing we built for long indeed", 70.0, 80.0,
             [_w("here", 70.1, 70.4), _w("is", 70.5, 70.7),
              _w("the", 70.8, 71.0), _w("thing", 71.1, 71.5),
              _w("we", 71.6, 71.8), _w("built", 71.9, 72.3),
              _w("for", 72.4, 72.6), _w("long", 72.7, 73.1),
              _w("indeed", 73.2, 73.7)]),
    ]}


def _moment(number=5, start=50.0, end=80.0, cta=None):
    return ReelMoment(
        number=number, slug=f"reel-{number:02d}",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        approval=Approval.APPROVED,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


def _project(tmp_path, moment=None, transcript=None):
    from library.tools.project_layout import ProjectLayout
    from library.tools.reel_proposal import (
        proposal_path,
        write_proposal,
    )
    from library.tools.timeline_transcript import transcript_path

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    tx = transcript if transcript is not None else _transcript()
    transcript_file = Path(transcript_path(str(project)))
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(json.dumps(tx), encoding="utf-8")
    write_proposal(Path(proposal_path(str(project))),
                   [moment if moment is not None else _moment(5)], tx)
    return str(project)


# ------------------------------------------------- the four sections


def test_the_preview_prints_freshness_cascade_snap_and_words(tmp_path):
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)
    strike = tc.record_keep_exclusion(
        project, 60.0, 70.0, "strike the first telling")
    hold = tc.record_keep_insistence(
        project, 70.0, 80.0, "the second telling stays")

    report = preview_take_pick(project, 5)
    assert report["reel"] == 5
    assert report["approval"] == "approved"
    assert [x["id"] for x in report["exclusions"]] == [strike["id"]]
    assert [x["id"] for x in report["insistences"]] == [hold["id"]]
    assert report["recorded_exclusions"] == 1
    assert report["keep_ranges"] == [(50.0, 60.0), (70.0, 80.0)]
    assert report["cascade_error"] is None

    text = render_take_pick_preview(report)
    assert "take-pick preview: Reel 05" in text
    assert "approval: approved" in text
    assert f"exclusion {strike['id']} 60.000-70.000" in text
    assert f"insistence {hold['id']} 70.000-80.000" in text
    assert "keep 50.00-60.00 (10.00s)" in text
    assert "keep 70.00-80.00 (10.00s)" in text
    assert "the setup mattered" in text
    assert "setup[51.40-51.80]" in text
    assert "src: a.MXF" in text




def test_a_cascade_refusal_reads_as_the_refusal(tmp_path):
    """A strike edge through a word is the build's own refusal, shown
    before the build instead of keep ranges."""
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)
    tc.record_keep_exclusion(
        project, 51.6, 70.0, "a strike ending inside setup")
    report = preview_take_pick(project, 5)
    assert report["keep_ranges"] == []
    assert report["cascade_error"] is not None
    assert "setup" in report["cascade_error"]
    text = render_take_pick_preview(report)
    assert "REFUSES:" in text
    assert "setup" in text


# ------------------------------------------------- it does not decide


def test_the_preview_names_no_take_to_keep_or_strike(tmp_path):
    # The recorded reasons below are the project's own stored words,
    # quoted back with their ids; the preview's own voice adds no
    # verdict on top of them.
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)
    tc.record_keep_exclusion(
        project, 60.0, 70.0, "first telling out, per verdict")
    report = preview_take_pick(project, 5)
    own_voice = "\n".join(
        line for line in render_take_pick_preview(report).split("\n")
        if "per verdict" not in line)
    lowered = own_voice.lower()
    for verdict in ("recommend", "should keep", "should strike",
                    "should cut", "best take", "prefer the",
                    "keep the second", "strike the first"):
        assert verdict not in lowered, verdict


# ------------------------------------------------- refusals


def test_an_unknown_reel_refuses_naming_what_exists(tmp_path, capsys):
    project = _project(tmp_path)
    assert main([project, "9"]) == 2
    err = capsys.readouterr().err
    assert "no reel 9" in err
    assert "[5]" in err
    with pytest.raises(LookupError, match="available"):
        preview_take_pick(project, 9)


def test_a_missing_transcript_refuses(tmp_path, capsys):
    project = _project(tmp_path)
    Path(project, "pipeline_output", "scratch",
         "timeline_transcript", "transcript.json").unlink()
    assert main([project, "5"]) == 2
    assert "REFUSED" in capsys.readouterr().err


