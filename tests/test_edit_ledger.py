"""The edit ledger: the K3 paint-over defect, caught per row.

Cluster K3 (`data/vep-ren-execution-frontier/report.md`): hands exist
but live outside the plan - resolve-axi can isolate voice and set a
LUT, and the next build paints every one of those over, because no
declared store carries them. Each test here names the defect it
catches: a row that cannot be recorded, cannot be replayed, survives
a spine change, or vanishes silently.
"""
import json

import pytest

from library.tools import captain_edits, edit_ledger
from library.tools.edit_ledger import EditLedgerError
from library.tools.project_layout import ProjectLayout


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _isolate(track=1, amount=60, reel="Reel 09 - hook",
             stated_by="requester",
             reason="remove the background noise from the host mic"):
    return {"op": "voice_isolation", "anchor": {"kind": "reel"},
            "reel": reel, "params": {"track": track, "amount": amount},
            "stated_by": stated_by, "reason": reason}


def _lut(phrase="ive quit every single day", reel="Reel 09 - hook",
         lut="Film Looks/Kodak 2383", node=1):
    return {"op": "clip_lut",
            "anchor": {"kind": "words", "phrase": phrase},
            "reel": reel, "params": {"lut": lut, "node": node},
            "stated_by": "requester",
            "reason": "kodak print feel on the hook"}


_GRADE_PROVENANCE = {
    "source": "Resolve Color page export",
    "authorised_by": "captain",
    "licence": "captain's own asset",
}


def _transcript(words, start=10.0, step=0.4):
    segs = []
    cursor = start
    for word in words:
        segs.append({"word": word, "start": cursor, "end": cursor + 0.3,
                     "timed": True})
        cursor += step
    return {"segments": [{"words": segs}]}


def test_releveling_hands_edits_replaces_stale_values(tmp_path):
    """Re-leveling the same isolation track or LUT node must replace the
    old value; otherwise a rebuild replays stale decisions first."""
    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(amount=60))
    edit_ledger.record_row(
        str(project), _lut(lut="Film Looks/Kodak 2383"))
    isolation80 = _isolate(amount=80)
    lut_revised = _lut(lut="Film Looks/Print 2383 Warm")
    _, action = edit_ledger.record_row(str(project), isolation80)
    assert action == "superseded"
    _, action = edit_ledger.record_row(str(project), lut_revised)
    assert action == "superseded"
    rows = edit_ledger.load_rows(str(project))
    assert isolation80 in rows
    assert lut_revised in rows
    assert _isolate(amount=60) not in rows
    assert _lut(lut="Film Looks/Kodak 2383") not in rows
    with pytest.raises(EditLedgerError, match="already in force"):
        edit_ledger.record_row(str(project), lut_revised)


def test_one_reels_rows_revert_alone(tmp_path):
    """Per-reel scoping (E2): dropping Reel 09's rows leaves Reel 28's
    intact - one reel's edits revert alone."""
    from library.tools.declaration_keys import edit_declaration

    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(reel="Reel 09 - hook"))
    edit_ledger.record_row(str(project), _isolate(reel="Reel 28 - nail"))
    with edit_declaration(str(project), "edit_ledger") as entries:
        for key in [k for k, row in entries.items()
                    if row.get("reel", "").startswith("Reel 09")]:
            del entries[key]
    rows = edit_ledger.load_rows(str(project))
    assert [r["reel"] for r in rows] == ["Reel 28 - nail"]
    scoped = edit_ledger.rows_for_reel(rows, "Reel 28 - nail (rebuild)")
    assert len(scoped) == 1
    assert edit_ledger.rows_for_reel(rows, "Reel 09 - hook") == []


def test_editing_one_reel_invalidates_only_its_build_digest():
    """The other reel's build must not be invalidated by one reel's
    ledger row, while the edited reel must rebuild to replay that row."""
    from library.tools import reel_rebuild_need as _need

    kwargs = dict(engine_code="eng", project_wide="wide",
                  plan_content_hash="plan", transcript_hash="tx",
                  master_digest="m", ranges=[(0.0, 5.0)],
                  placements_list=[], cards=[], caption_segments=[],
                  explainer_segments=[], semantic_segments=[],
                  overlay_placements=[], motion_record={}, ending={},
                  look={}, grade_cdl={}, grade_look={}, power_grade={})
    bare = _need.derivation_digest(reel_number=9, **kwargs)
    row = _isolate()
    one = _need.derivation_digest(reel_number=9, extra={"edit_ledger": [
        row]}, **kwargs)
    other = _need.derivation_digest(
        reel_number=28, extra={"edit_ledger": [row]}, **kwargs)
    assert one != bare
    assert one != other
    assert _need.derivation_digest(reel_number=9, extra={}, **kwargs) \
        == bare
    camera = {
        "op": "angle_plan", "anchor": {"kind": "reel"},
        "reel": "Reel 09 - hook",
        "params": {"camera": "Akshita", "min_shot_seconds": 2,
                   "lead_frames": 0},
        "stated_by": "requester", "reason": "show the host",
    }
    with_angle = _need.derivation_digest(
        reel_number=9, extra={"edit_ledger": [row, camera]}, **kwargs)
    assert with_angle != one


# ── The merged view: one store, every existing applier ───────────────

def test_recorded_plan_edits_reach_the_existing_replayers(tmp_path):
    """A hand-recorded framing hold must reach captain_edits' applier,
    or the next spine build paints it over (the K3 defect)."""
    project = _project(tmp_path)
    hold = {"op": "transform_override",
            "anchor": {"kind": "words", "phrase": "akshitas line"},
            "params": {"property": "Pan", "value": -8.75,
                        "recorded_draw_gain": 4.0},
            "stated_by": "captain", "reason": "hand move in inspector"}
    edit_ledger.record_row(str(project), hold)
    edit_ledger.record_row(str(project), _isolate())
    carrier = {"op": "angle_plan",
               "anchor": {"kind": "words", "phrase": "akshitas line"},
               "reel": "Reel 09 - hook",
               "params": {"camera": "close-up",
                          "min_shot_seconds": 3, "lead_frames": 12},
               "stated_by": "requester", "reason": "cut to the speaker"}
    edit_ledger.record_row(str(project), carrier)
    edits = captain_edits.load_edits(str(project))
    assert len(edits) == 1
    assert edits[0]["kind"] == "transform_override"
    assert edits[0]["property"] == "Pan"
    assert edits[0]["recorded_draw_gain"] == pytest.approx(4.0)
    captain_edits.validate_edits(edits)


# ── Replay onto fakes: the paint-over, closed ────────────────────────

class _Graph:
    def __init__(self, nodes=2):
        self.nodes = nodes
        self.luts = {}

    def GetNumNodes(self):
        return self.nodes

    def SetLUT(self, node, lut):
        self.luts[node] = lut
        return True

    def GetLUT(self, node):
        return self.luts.get(node, "")

    def ApplyGradeFromDRX(self, path, _mode):
        self.drx = path
        return True


class _Item:
    _MISSING = object()

    def __init__(self, graph=_MISSING):
        self._graph = _Graph() if graph is _Item._MISSING else graph

    def GetNodeGraph(self):
        return self._graph


class _Timeline:
    def __init__(self, audio_tracks=2):
        self.audio_tracks = audio_tracks
        self.isolation = {}

    def GetTrackCount(self, kind):
        assert kind == "audio"
        return self.audio_tracks

    def SetVoiceIsolationState(self, track, state):
        self.isolation[track] = dict(state)
        return True

    def GetVoiceIsolationState(self, track):
        return dict(self.isolation.get(track, {}))


def _spans():
    return [{"master": (10.0, 20.0)}, {"master": (20.0, 30.0)}]


def _speech():
    return _transcript(
        ["hello", "there", "ive", "quit", "every", "single", "day",
         "for", "years", "and", "back", "again"])


def test_k3_hands_edits_replay_with_resolve_readback(tmp_path):
    """The MX2.1/C1.3 edits must survive a rebuild, with Resolve
    read-back proving the track isolation and speaking-clip LUT."""
    timeline = _Timeline()
    items = [_Item(), _Item()]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [_isolate(), _lut()], _spans(), _speech(),
        timeline, item_for_span=items.__getitem__,
        reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert timeline.GetVoiceIsolationState(1) == {"isEnabled": True,
                                                 "amount": 60}
    assert items[0].GetNodeGraph().GetLUT(1) == "Film Looks/Kodak 2383"


def test_grade_row_replays_lut_on_each_picture_span():
    """A declared reel grade must be read by the rebuilt timeline, or
    a grade plan is painted over even though the ledger kept the row."""
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"lut": "Film Looks/Kodak 2383", "node": 1},
           "stated_by": "requester", "reason": "print grade"}
    timeline = _Timeline()
    items = [_Item(), _Item()]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [row], _spans(), _speech(), timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert all(item.GetNodeGraph().GetLUT(1) ==
               "Film Looks/Kodak 2383" for item in items)
    # Resolve's accepted write is not proof that pixels changed.
    assert all(applied["pixel_verification"] == edit_ledger.PIXELS_UNMEASURED
               for applied in report["applied"])


def test_power_grade_ledger_row_requires_authorisation(tmp_path):
    """A ledger row must use the same provenance gate as the project
    grade declaration, or it bypasses the captain's .drx ruling."""
    project = _project(tmp_path)
    path = project / "Podcast.drx"
    path.write_bytes(b"DRX")
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"drx": "Podcast.drx"},
           "stated_by": "requester", "reason": "podcast look"}

    with pytest.raises(EditLedgerError, match="without provenance"):
        edit_ledger.record_row(str(project), row)
    assert edit_ledger.load_rows(str(project)) == []

    row["params"]["provenance"] = dict(_GRADE_PROVENANCE)
    stored, action = edit_ledger.record_row(str(project), row)
    assert action == "recorded"
    assert stored["params"]["drx"] == str(path)
    assert edit_ledger.load_rows(str(project))[0]["params"]["drx"] \
        == str(path)

    # An authorised path that is gone stops before Resolve creates a
    # reel with the declared look missing.
    gone = {"op": "grade", "anchor": {"kind": "reel"},
            "params": {"drx": "gone.drx",
                       "provenance": dict(_GRADE_PROVENANCE)},
            "stated_by": "requester", "reason": "other look"}
    with pytest.raises(EditLedgerError, match="not on disk"):
        edit_ledger.record_row(str(project), gone)
    assert len(edit_ledger.load_rows(str(project))) == 1


def test_power_grade_row_requires_node_graph_readback():
    """A hand-set PowerGrade survives only when the rebuilt item reads
    back the grade nodes; a True API return is not enough."""
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"drx": "/looks/Podcast.drx",
                      "provenance": dict(_GRADE_PROVENANCE)},
           "stated_by": "requester", "reason": "podcast look"}
    timeline = _Timeline()
    item = _Item()
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [row], _spans(), _speech(), timeline,
        item_for_span=lambda _index: item, reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert all(row["write_readback"] == "fresh node graph read-back"
               for row in report["applied"])
    assert all(row["pixel_verification"] == edit_ledger.PIXELS_UNMEASURED
               for row in report["applied"])
    assert item.GetNodeGraph().drx == "/looks/Podcast.drx"


def test_grade_row_refuses_a_parameter_the_replayer_ignores():
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"lut": "Film Looks/Kodak 2383", "mix": 50},
           "stated_by": "requester", "reason": "half strength"}

    with pytest.raises(EditLedgerError, match="does not read"):
        edit_ledger.validate_rows([row])


def test_word_anchor_survives_a_spine_change():
    """The version-control ruling, proved: the spine re-times around
    the words (same speech, new seconds) and the row still grades the
    clip speaking them - because it holds words, not frames."""
    timeline = _Timeline()
    items = [_Item(), _Item()]
    before = _speech()
    after = _transcript(
        ["hello", "there", "ive", "quit", "every", "single", "day",
         "for", "years", "and", "back", "again"],
        start=48.0, step=0.5)
    moved_spans = [{"master": (48.0, 58.0)}, {"master": (58.0, 68.0)}]
    row = _lut(reel="Reel 09")
    first = edit_ledger.replay_on_timeline(
        "Reel 09", [row], _spans(), before, timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09")
    second = edit_ledger.replay_on_timeline(
        "Reel 09", [row], moved_spans, after, timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09")
    assert first["unreplayable"] == []
    assert second["unreplayable"] == []
    assert items[0].GetNodeGraph().GetLUT(1) == "Film Looks/Kodak 2383"


def test_unreplayable_rows_are_reported_by_name(tmp_path, capsys):
    """A row the build cannot replay - no such track, no such node,
    anchor spoken nowhere, carrier with no replayer yet - is REPORTED
    BY NAME, never dropped silently (the K3 defect back again)."""
    timeline = _Timeline(audio_tracks=1)
    rows = [_isolate(track=4),
            _lut(node=9),
            _lut(phrase="words nobody ever spoke"),
            _retime("ive quit", 80)]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", rows, _spans(), _speech(), timeline,
        item_for_span=lambda _i: _Item(),
        reel_name="Reel 09 - hook")
    assert report["applied"] == []
    # The retime is no carrier: it shaped the keep ranges before
    # placement (`rate_ranges`), so replay neither applies nor loses it.
    assert len(report["unreplayable"]) == 3
    names = " ".join(r["name"] for r in report["unreplayable"])
    assert "voice_isolation" in names
    assert "clip_lut" in names
    assert "retime" not in names
    out = capsys.readouterr().err
    assert out.count("UNREPLAYABLE LEDGER ROW") == 3

    # A clip with no node graph cannot hold a LUT - named, not claimed.
    report = edit_ledger.replay_on_timeline(
        "Reel 09", [_lut(reel="Reel 09")], _spans(), _speech(), _Timeline(),
        item_for_span=lambda _i: _Item(graph=None),
        reel_name="Reel 09")
    assert len(report["unreplayable"]) == 1
    assert "node graph" in report["unreplayable"][0]["reason"]


def _retime(phrase, percent, reel="Reel 09 - hook", **params):
    return {"op": "retime", "reel": reel,
            "anchor": {"kind": "words", "phrase": phrase},
            "params": {"percent": percent, **params},
            "stated_by": "requester", "reason": "pace the passage"}


def test_retime_is_durable_intent_on_the_keep_ranges():
    """Punch list 10: "make this passage 110% speed" re-derives on every
    rebuild - the passage, from its first word to its last, plays at
    its percent on the reel clock, inside its range (no new seam), and
    the placer lays it down at that rate with contiguous records."""
    from types import SimpleNamespace

    from library.tools import reel_clock
    from library.tools.reel_build import placements

    ranges, applied, lost = edit_ledger.rate_ranges(
        [(10.0, 15.0)], [_retime("quit every single day", 110)],
        _speech(), "Reel 09 - hook")
    assert lost == []
    assert applied[0]["passages"] == [[11.2, 12.7]]
    assert len(ranges) == 1 and tuple(ranges[0]) == (10.0, 15.0)
    assert [pytest.approx(piece) for piece in reel_clock.pieces(
        ranges[0])] == [(10.0, 11.2, 1.0), (11.2, 12.7, 1.1),
                        (12.7, 15.0, 1.0)]
    assert reel_clock.played_seconds(ranges[0]) == pytest.approx(
        1.2 + 1.5 / 1.1 + 2.3)

    clip = SimpleNamespace(timeline_start=0.0, timeline_end=60.0,
                           source_in=100.0, track_index=1, speaker="A",
                           track_type="video", source_file="/a.mov")
    placed = placements(ranges, [clip], 24.0)
    assert [p.get("rate", 1.0) for p in placed] == [1.0, 1.1, 1.0]
    middle = placed[1]
    assert middle["source_in"] == pytest.approx(111.2)
    assert round((middle["source_out"] - middle["source_in"]) * 24) == 36
    assert middle["record_frames"] == 33
    assert placed[2]["snapped_record"] == (middle["snapped_record"]
                                           + middle["record_frames"])
    assert (placed[2]["snapped_record"]
            + round((placed[2]["source_out"] - placed[2]["source_in"])
                    * 24)) == reel_clock.played_frames(ranges[0], 24.0)


def test_retime_reports_what_it_cannot_honour():
    """A retime the reel cannot play is reported by name and leaves the
    ranges alone; two retimes over one stretch of speech refuse."""
    ranges = [(10.0, 15.0)]
    rows = [_retime("words nobody ever spoke", 120),
            _retime("there ive", 120),
            _retime("and back", 90, segments=[{"percent": 90}])]
    rated, applied, lost = edit_ledger.rate_ranges(
        ranges, rows, _speech(), "Reel 09 - hook")
    assert rated == ranges and applied == []
    reasons = " ".join(record["reason"] for record in lost)
    assert "does not play those words" in reasons
    assert "'hello'" in reasons
    assert "stepped retime" in reasons
    with pytest.raises(EditLedgerError, match="one stretch of speech"):
        edit_ledger.rate_ranges(
            ranges, [_retime("quit every", 110), _retime("every single", 90)],
            _speech(), "Reel 09 - hook")


def test_anchor_typo_fails_at_record_not_next_build(tmp_path):
    """A word anchor the measured transcript never speaks is refused
    at record time (the captain_edits typo rule) - never stored to
    fail silently on the next build."""
    from library.tools import captain_edits as _edits

    project = _project(tmp_path)
    tpath = _edits.transcript_path(str(project))
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(json.dumps(_transcript(["hello", "there"])),
                     encoding="utf-8")
    row = _lut(phrase="words nobody ever spoke")
    with pytest.raises(Exception, match="spoken nowhere"):
        edit_ledger.record_row(str(project), row)
    assert edit_ledger.load_rows(str(project)) == []
