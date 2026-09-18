"""The snap preview: cascades become a line of output beforehand.

D2: `snap_to_speech` widens outward to whole segments in a fixed-point
loop, and transcript segments overlap by ASR jitter - so a snap walks
from one segment into the next and keeps going.  The canary found a
9.1s closer move by looking at a finished 61s timeline.  This suite
pins the preview that reports, per moment, how far each boundary would
move and what words that pulls in or drops, flagging moves over about
two seconds as needing a decision BEFORE the build.

The snap itself is untouched: repair is still outward, still silent
where small, still idempotent (`test_reel_proposal_build_time_snap`).
A pin, when one is decided, goes through the captain's declaration
store (`captain_edits.record_edit`) with its provenance - the loop
the last test walks end to end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.reel_proposal import (
    SNAP_DECISION_SECONDS,
    Approval,
    CallToAction,
    ReelMoment,
    decision_lines,
    preview_snap,
    render_snap_preview,
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


def _cascade_transcript():
    """Three bound segments overlapping by ASR jitter, like the
    canary's: each starts just before the previous one ends."""
    return {"segments": [
        _seg("alpha beta", 10.0, 20.0,
             [_w("alpha", 10.5, 11.0), _w("beta", 12.0, 12.5)]),
        _seg("gamma delta", 19.5, 30.0,
             [_w("gamma", 20.0, 20.5), _w("delta", 22.0, 22.5)]),
        _seg("epsilon zeta", 29.5, 40.0,
             [_w("epsilon", 31.0, 31.5), _w("zeta", 33.0, 33.5)]),
    ]}


def _moment(number, start, end, cta=None):
    return ReelMoment(
        number=number, slug=f"reel-{number:02d}",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        approval=Approval.APPROVED,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


# ------------------------------------------------- the cascade, flagged


def test_a_snap_cascade_is_flagged_with_its_pulled_in_words():
    """The M16 shape: a body start at 19.7s walks two jittered joints
    and lands at 10.0s (-9.7s), pulling an unrelated preamble in."""
    report = preview_snap([_moment(16, 19.7, 25.0)],
                          _cascade_transcript())
    assert report["moved"] == 2
    assert report["flagged"] == 2
    by_boundary = {m["boundary"]: m
                   for m in report["moments"][0]["moves"]}
    start = by_boundary["body_start"]
    assert (start["was"], start["now"]) == (19.7, 10.0)
    assert start["delta"] == pytest.approx(-9.7)
    assert start["needs_decision"]
    assert [w["word"] for w in start["pulled_in"]] == ["alpha", "beta"]
    assert start["dropped"] == []
    end = by_boundary["body_end"]
    assert (end["was"], end["now"]) == (25.0, 40.0)
    assert end["needs_decision"]
    assert [w["word"] for w in end["pulled_in"]] == [
        "epsilon", "zeta"]


def test_the_render_names_the_move_and_the_words():
    text = render_snap_preview(preview_snap(
        [_moment(16, 19.7, 25.0)], _cascade_transcript()))
    assert "snap preview: 1 moment(s)" in text
    assert "2 need(s) a decision" in text
    assert "Reel 16: body_start 19.700s -> 10.000s (-9.700s)" in text
    assert "NEEDS DECISION" in text
    assert "alpha" in text and "beta" in text


# --------------------------------------- small moves stay quiet


def test_a_word_edge_nudge_is_reported_never_flagged():
    """The reel-5 shape: the stored end sits inside one word and the
    repair moves 0.179s onto its edge - a line of output, no
    decision."""
    tx = {"segments": [
        _seg("recommend you", 412.63, 413.85,
             [_w("recommend", 412.63, 412.99),
              _w("you", 413.01, 413.17),
              _w("yours", 413.59, 413.85)]),
        _seg("about", 412.77, 414.03, [_w("about", 412.77, 414.03)],
             bound=False),
    ]}
    report = preview_snap([_moment(5, 412.63, 413.851)], tx)
    assert report["moved"] == 1
    assert report["flagged"] == 0
    move = report["moments"][0]["moves"][0]
    assert move["boundary"] == "body_end"
    assert move["now"] == 414.03
    assert not move["needs_decision"]
    assert [w["word"] for w in move["pulled_in"]] == ["about"]
    text = render_snap_preview(report)
    assert "NEEDS DECISION" not in text
    assert "0 need(s) a decision" in text


def test_a_clean_moment_reads_as_one_quiet_line():
    # (10, 40) is the cascade transcript's fixed point: every edge
    # sits on a segment edge outside any word, so the snap holds.
    report = preview_snap([_moment(5, 10.0, 40.0)],
                          _cascade_transcript())
    assert report["moved"] == 0
    assert report["flagged"] == 0
    assert report["moments"][0]["moves"] == []
    assert "no boundary moves" in render_snap_preview(report)


def test_the_threshold_is_two_seconds_and_tunable():
    assert SNAP_DECISION_SECONDS == 2.0
    report = preview_snap([_moment(16, 19.7, 25.0)],
                          _cascade_transcript(), threshold=20.0)
    assert report["moved"] == 2
    assert report["flagged"] == 0


# --------------------------------- the build loop reads one spelling


def test_decision_lines_derive_from_a_raw_build_loop_move():
    """The build loop hands `decision_lines` the raw move (no word
    lists, no phrases): a flagged cascade still reads loud, a nudge
    still reads as nothing - so the beforehand report and the build
    agree."""
    tx = _cascade_transcript()
    loud = decision_lines(16, {"boundary": "body_start",
                               "was": 19.7, "now": 10.0,
                               "through": None}, tx)
    assert any("NEEDS DECISION" in line for line in loud)
    assert any("alpha" in line for line in loud)
    assert any("proposal" in line for line in loud)
    quiet = decision_lines(5, {"boundary": "body_end",
                               "was": 413.851, "now": 414.03,
                               "through": "about"}, tx)
    assert quiet == []


def test_a_flagged_closer_names_its_pin_route():
    """A closer start the snap would drag gets the record-closer
    command with both phrases, not a proposal pointer."""
    tx = {"segments": [
        _seg("here is the thing", 90.0, 101.0,
             [_w("here", 90.1, 90.4), _w("is", 90.5, 90.7),
              _w("the", 90.8, 91.0), _w("thing", 91.1, 91.5)]),
        _seg("welcome back everyone today we have words", 100.0, 120.0,
             [_w("welcome", 100.5, 100.9), _w("back", 101.0, 101.4),
              _w("everyone", 101.5, 102.0), _w("today", 103.0, 103.5),
              _w("we", 104.0, 104.3), _w("have", 104.4, 104.8),
              _w("words", 105.0, 105.4)]),
    ]}
    moment = _moment(6, 50.0, 80.0, cta=(100.0, 120.0))
    report = preview_snap([moment], tx)
    moves = {m["boundary"]: m for m in report["moments"][0]["moves"]}
    assert set(moves) == {"cta_start"}
    closer = moves["cta_start"]
    assert (closer["was"], closer["now"]) == (100.0, 90.0)
    assert closer["needs_decision"]
    assert closer["anchor_phrase"].startswith("welcome back")
    assert closer["snapped_phrase"].startswith("here is the thing")
    lines = decision_lines(6, closer, tx, "/projects/demo")
    assert any("record-closer" in line for line in lines)
    assert any(repr(closer["anchor_phrase"]) in line for line in lines)
    assert any(repr(closer["snapped_phrase"]) in line for line in lines)


# ----------------- the pin, recorded with provenance, applied


def _pin_transcript():
    return {"segments": [
        _seg("the setup mattered", 50.0, 80.0,
             [_w("the", 51.0, 51.3), _w("setup", 51.4, 51.8),
              _w("mattered", 52.0, 52.5)]),
        _seg("here is the thing", 90.0, 101.0,
             [_w("here", 90.1, 90.4), _w("is", 90.5, 90.7),
              _w("the", 90.8, 91.0), _w("thing", 91.1, 91.5)]),
        _seg("welcome back everyone today we have words", 100.0, 120.0,
             [_w("welcome", 100.5, 100.9), _w("back", 101.0, 101.4),
              _w("everyone", 101.5, 102.0), _w("today", 103.0, 103.5),
              _w("we", 104.0, 104.3), _w("have", 104.4, 104.8),
              _w("words", 105.0, 105.4)]),
    ]}


def test_a_decided_pin_is_recorded_with_provenance_and_applied(tmp_path):
    """The canary's M6 loop, closed: the preview's phrases become a
    `redraw_closer` declaration in the project store (with its
    `source`), and the build's pin pass redraws the snapped closer
    onto the ruled opening."""
    from library.tools import captain_edits
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    tx = _pin_transcript()
    transcript_file = (project / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(json.dumps(tx), encoding="utf-8")

    moment = _moment(6, 50.0, 80.0, cta=(100.0, 120.0))
    report = preview_snap([moment], tx)
    closer = {m["boundary"]: m
              for m in report["moments"][0]["moves"]}["cta_start"]
    assert closer["needs_decision"]

    edit, action = captain_edits.record_edit(
        str(project),
        {"kind": "redraw_closer",
         "anchor_phrase": closer["anchor_phrase"],
         "from_phrase": closer["snapped_phrase"],
         "reason": "snap preview: the closer would open 10s from "
                   "the ruled opening; pin it back"},
        "snap preview")
    assert action == "recorded"
    stored = json.loads(
        (project / "external" / "captain_edits.json").read_text(
            encoding="utf-8"))
    assert stored["source"] == "snap preview"
    assert captain_edits.load_edits(str(project)) == [edit]

    # The build snaps first, then applies pins: the snapped closer
    # opens on the from-phrase, and the pin redraws it onto the
    # anchor's words with the end fixed.
    from library.tools.reel_proposal import snap_moment_to_speech
    snapped, _ = snap_moment_to_speech(moment, tx)
    assert snapped.call_to_action.timeline_start == 90.0
    redrawn, applied, held, stale = (
        captain_edits.apply_closer_redraws([snapped], tx, [edit]))
    assert stale == [] and held == []
    assert [r["reel"] for r in applied] == [6]
    assert redrawn[0].call_to_action.timeline_start == 100.5
    assert redrawn[0].call_to_action.timeline_end == 120.0


def test_preview_snap_cli_reports_beforehand(tmp_path, capsys):
    """`preview-snap <project>` reads the stored proposal and the
    transcript and prints the report - the line the canary never
    had."""
    from library.tools.project_layout import ProjectLayout
    from library.tools.reel_proposal import (
        main as preview_main, proposal_path, write_proposal,
    )
    from library.tools.timeline_transcript import transcript_path

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    tx = _cascade_transcript()
    transcript_file = Path(transcript_path(str(project)))
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(json.dumps(tx), encoding="utf-8")
    write_proposal(Path(proposal_path(str(project))),
                   [_moment(16, 19.7, 25.0)], tx)

    assert preview_main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "snap preview: 1 moment(s)" in out
    assert "2 need(s) a decision" in out
    assert "NEEDS DECISION" in out
