"""A recorded trim whose anchor re-times must be loud before a build proceeds.

History: docs/evidence/resolve_test_history.md#test_retime_drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import captain_edits
from library.tools.project_layout import ProjectLayout

FPS = 24000 / 1001

RECORDED_SO = 1407.830
RETIMED_SO = 1407.970


def _words(tokens, start):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": round(cursor, 3),
                      "end": round(cursor + 0.32, 3), "timed": True})
        cursor = round(cursor + 0.4, 3)
    return words


def _transcript(so_start):
    return {"segments": [{
        "text": "so for small business owners the first step",
        "timeline_start": 1400.0, "timeline_end": 1420.0,
        "source_start": 3119.0, "source_file": "/v/craig.mov",
        "words": _words(
            ["so", "for", "small", "business", "owners",
             "the", "first", "step"],
            start=so_start)}]}


def _pin(recorded_edge=RECORDED_SO):
    edit = {"kind": "span_retime",
            "anchor_phrase": "So for small business",
            "edge": "head",
            "reason": "opens where judged, recorded 2026-09-18"}
    if recorded_edge is not None:
        edit["recorded_edge"] = recorded_edge
    return edit


def _spans():
    return [{"master": (1400.0, 1420.0)}]


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_transcript(project, transcript):
    path = (project / "pipeline_output" / "scratch"
            / "timeline_transcript" / "transcript.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript), encoding="utf-8")


# ── The offline reproduction: the snap follows the transcript ────────

def test_match_follows_retimed_words_to_1407_97():
    matched, held, stale = captain_edits.match_span_retimes(
        _spans(), _transcript(RETIMED_SO), [_pin()])
    assert not stale and not held and len(matched) == 1
    assert matched[0]["new_edge"] == pytest.approx(RETIMED_SO)


# ── Outcome 2: resolves to a different place ─────────────────────────

def test_freshness_names_drifted_held_and_stale_anchors_before_a_build():
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), _transcript(RETIMED_SO), [_pin()], fps=FPS)
    assert not stale
    assert len(drifted) == 1
    record = drifted[0]
    assert record["anchor_phrase"] == "So for small business"
    assert record["edge"] == "head"
    assert record["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert record["resolved_edge"] == pytest.approx(RETIMED_SO)
    assert record["frames_moved"] == 4
    assert record["span_index"] == 0

    # A fresh anchor reports nothing.
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), _transcript(RECORDED_SO), [_pin()], fps=FPS)
    assert drifted == [] and stale == []

    # A previous build already followed the words: the span edge sits
    # on them (HELD, nothing to trim), but they are not where the trim
    # was recorded - still drift, still loud.
    spans = [{"master": (RETIMED_SO, 1420.0)}]
    drifted, stale = captain_edits.check_span_retime_freshness(
        spans, _transcript(RETIMED_SO), [_pin()], fps=FPS)
    assert stale == []
    assert len(drifted) == 1
    assert drifted[0]["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert drifted[0]["resolved_edge"] == pytest.approx(RETIMED_SO)
    assert drifted[0]["frames_moved"] == 4

    # Outcome 1, through the same check: a reworded anchor no longer
    # resolves, and is stale rather than drifted.
    reworded = {"segments": [{
        "text": "well for tiny business owners the first step",
        "timeline_start": 1400.0, "timeline_end": 1420.0,
        "source_start": 3119.0, "source_file": "/v/craig.mov",
        "words": _words(
            ["well", "for", "tiny", "business", "owners",
             "the", "first", "step"],
            start=RECORDED_SO)}]}
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), reworded, [_pin()], fps=FPS)
    assert drifted == []
    assert len(stale) == 1
    assert stale[0]["anchor_phrase"] == "So for small business"


# ── Outcome 1: no longer resolves ────────────────────────────────────

def test_drifted_is_loud_on_stderr(capsys):
    drifted, _ = captain_edits.check_span_retime_freshness(
        _spans(), _transcript(RETIMED_SO), [_pin()], fps=FPS)
    lines = captain_edits.report_drifted(drifted)
    assert len(lines) == 1
    assert "So for small business" in lines[0]
    assert "1407.830" in lines[0] and "1407.970" in lines[0]
    captured = capsys.readouterr()
    assert "DRIFTED EDIT" in captured.err


# ── The write side stamps where the anchor resolved ──────────────────

def test_record_stamps_the_resolved_edge(tmp_path):
    project = _project(tmp_path)
    _write_transcript(project, _transcript(RECORDED_SO))
    edit, action = captain_edits.record_edit(
        str(project),
        {"kind": "span_retime",
         "anchor_phrase": "So for small business",
         "edge": "head",
         "reason": "opens where judged"},
        "captain, test")
    assert action == "recorded"
    assert edit["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert captain_edits.load_edits(str(project)) == [edit]


def test_record_leaves_an_ambiguous_anchor_unstamped(tmp_path):
    project = _project(tmp_path)
    doubled = {"segments": [
        {"text": "so for small business cheers",
         "timeline_start": 1400.0, "timeline_end": 1410.0,
         "source_start": 3119.0, "source_file": "/v/a.mov",
         "words": _words(["so", "for", "small", "business", "cheers"],
                         start=1400.0)},
        {"text": "so for small business again",
         "timeline_start": 1500.0, "timeline_end": 1510.0,
         "source_start": 3219.0, "source_file": "/v/b.mov",
         "words": _words(["so", "for", "small", "business", "again"],
                         start=1500.0)},
    ]}
    _write_transcript(project, doubled)
    edit, _ = captain_edits.record_edit(
        str(project),
        {"kind": "span_retime",
         "anchor_phrase": "So for small business",
         "edge": "head",
         "reason": "ambiguous but spoken"},
        "captain, test")
    assert "recorded_edge" not in edit


def test_recorded_edge_must_be_a_real_second(tmp_path):
    project = _project(tmp_path)
    bad = dict(_pin(), recorded_edge="about there")
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.record_edit(str(project), bad)
    assert "recorded_edge" in str(exc.value)


# ── The build checks before it applies, and files what it found ─────

def _derive_project(tmp_path, edits):
    project = _project(tmp_path)
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    (directory / "captain_edits.json").write_text(
        json.dumps({"key": "captain_edits", "source": "captain, test",
                    "value": edits}),
        encoding="utf-8")
    return str(project)


def test_derive_files_drift_before_applying(tmp_path, capsys):
    from types import SimpleNamespace

    from library.tools import reel_build as build

    transcript = {"segments": [{
        "timeline_start": 10.0, "timeline_end": 14.0,
        "source_file": "clip_a.mov", "source_start": 100.0,
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "plays", "start": 11.0, "end": 11.4,
             "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0,
             "timed": True}]}]}
    pin = {"kind": "span_retime", "anchor_phrase": "plays",
           "edge": "head", "recorded_edge": 10.5,
           "reason": "opens on plays"}
    project = _derive_project(tmp_path, [pin])
    moment = SimpleNamespace(
        number=9, timeline_name="Reel 09",
        timeline_start=10.0, timeline_end=14.0)
    clips = [SimpleNamespace(
        timeline_start=0.0, timeline_end=30.0, source_in=90.0,
        track_index=1, track_type="video", speaker="X",
        source_file="clip_a.mov")]
    cuts, insisted = build.moment_cuts_and_insistences(
        moment, transcript, [], [])
    trims: dict = {}
    ranges, cards, ending = build.derive_reel_ranges_and_cards(
        moment, transcript, clips, project, FPS, "Reel 09",
        cuts, insisted, card_declarations=[], look_decl=None,
        reel_width=1080, reel_height=1920, collect_trims=trims)
    # Enumerable: the drift is filed beside applied/held/stale.
    assert len(trims["drifted"]) == 1
    assert trims["drifted"][0]["anchor_phrase"] == "plays"
    assert trims["drifted"][0]["recorded_edge"] == pytest.approx(10.5)
    assert trims["drifted"][0]["resolved_edge"] == pytest.approx(11.0)
    assert trims["stale"] == []
    # Loud: stderr says so before the trim below lands.
    captured = capsys.readouterr()
    assert "DRIFTED EDIT" in captured.err
    # And the trim still applies - drift is a state, not a refusal.
    assert len(trims["applied"]) == 1
    assert ranges == [(11.0, 14.0)]
    assert cards == [] and ending is None
