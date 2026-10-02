"""The speaker lower third, and the two ways it is easy to get wrong.

The capability the captain asked for on 2026-09-12 has two properties
that are not obvious from the feature description, and both of them are
what this file exists to pin:

1. **A project that declares no speakers gets no lower thirds, and does
   not crash.** The engine serves a daily channel AND client work
   (AGENTS.md 14), so the four strings of one project may not reach
   library code and a second project must be able to declare a different
   cast - or none.
2. **A speaker who appears twice gets ONE graphic.** "on the first
   appearance" is the ask; a reel where Craig comes back six times must
   name him once.

Every project here is built under `tmp_path`. No test reaches a real
one (`tests/tooling/test_tests_never_reach_real_projects.py`).
"""
from __future__ import annotations
import pytest
from library.tools import speaker_identity as si
import itertools
import shutil
import subprocess
import types
from pathlib import Path
import numpy as np
from library.tools import single_track_diarization as std
from library.tools import timeline_transcript as tt
from library.tools.timeline_ingest import TimelineClip
from library.tools import conversation_clock as cc
from library.tools import source_memory


# ── Fixtures: projects that declare, and projects that do not ────────

def _project(tmp_path, name, pipeline=None, effect=None):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    config = {"name": name, "slug": name}
    if pipeline is not None:
        config["pipeline"] = pipeline
    if effect is not None:
        config["effect"] = effect
    import yaml
    (folder / "project.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8")
    return str(folder)


DECLARATION = {
    "anchor": "bottom_left",
    "hold_seconds": 3.0,
    "entrance": "draw",
    "exit": "fade",
    "speakers": {
        "Ada": {"name": "Ada Lovelace", "title": "Analyst",
                "colour": "#11FFAA"},
        "Bram": {"name": "Bram Stoker", "title": "Editor",
                 "colour": "#FF3366"},
    },
}


def _lines(*pairs):
    """`(speaker, reel_start)` pairs as `played_speech`'s shape."""
    return [{"speaker": who, "reel_start": at, "text": f"{who} line at {at}"}
            for who, at in pairs]


# ── 1. A project that declares nothing ───────────────────────────────

def _appearing_project(tmp_path, name):
    return _project(tmp_path, name,
                    effect={si.DECLARATION_KEY: DECLARATION})


def test_a_reel_with_nobody_to_name_gets_no_card_and_says_why(tmp_path):
    """No crash, no placeholder, no graphic - and the basis names which
    absence it was: a project that declares no cast (the generalisation
    case), a reel with no lines and nobody appearing, a reel whose only
    voice the project never declared, and an appearing voice the
    declaration does not list."""
    rows = [
        # (project declares?, lines, appearing, basis, declared)
        (False, _lines(("Ada", 1.0), ("Bram", 8.0)), (),
         si.NOT_DECLARED, False),
        (True, [], (), si.NO_LINES_IN_THE_REEL, True),
        (True, _lines(("Zoe", 1.0)), (), si.NO_DECLARED_SPEAKER_SPOKE, True),
        (True, [], ("Zoe",), si.NO_LINES_IN_THE_REEL, True),
    ]
    for n, (declares, lines, appearing, basis, declared) in enumerate(rows):
        folder = (_appearing_project(tmp_path, f"p{n}") if declares
                  else _project(tmp_path, f"p{n}"))
        plan = si.plan_for_reel(
            "R", lines, 60.0, folder, width=1080, height=1920,
            appearing_speakers=appearing)
        assert plan.entries == [], n
        assert plan.introductions == [], n
        assert plan.basis == basis, n
        assert plan.declared is declared, n


def test_a_second_project_declares_a_different_cast(tmp_path):
    """Two projects, two casts, one engine. If this can fail, the four
    strings of one project have reached library code."""
    first = _project(tmp_path, "first",
                     effect={si.DECLARATION_KEY: DECLARATION})
    other = dict(DECLARATION)
    other["speakers"] = {
        "Zoe": {"name": "Zoe Okafor", "title": "Head of Research",
                "colour": "#0055FF"}}
    second = _project(tmp_path, "second",
                      effect={si.DECLARATION_KEY: other})

    plan_one = si.plan_for_reel(
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, first, width=1080, height=1920)
    plan_two = si.plan_for_reel(
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, second, width=1080, height=1920)

    assert [i.name for i in plan_one.introductions] == ["Ada Lovelace"]
    assert [i.name for i in plan_two.introductions] == ["Zoe Okafor"]


def test_no_speaker_identity_string_is_anywhere_in_library():
    """The identities in this repository's own field-test project may
    not be in the code that serves every project - not as a constant,
    not as a docstring example, not as a fallback.

    Scoped to `library/`, which is the engine. `tests/` carries
    `Akshita` and `Craig` as FIXTURES across many files and renaming
    those is a separate, declared piece of work; a fixture in a test is
    not a value the engine can reach.
    """
    import pathlib

    root = pathlib.Path(si.__file__).resolve().parents[2] / "library"
    # The four strings from the captain's marker of 2026-09-12. Built
    # rather than written, so this file does not carry them either.
    forbidden = [" ".join(parts) for parts in (
        ("Craig", "Lucie"), ("CEO", "Lucie", "Content"),
        ("Akshita", "Gorti"), ("AI", "@", "Lucie", "Content"))]
    offenders = []
    for path in root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders.extend(f"{path}: {bad!r}"
                         for bad in forbidden if bad in source)
    assert not offenders, (
        "one project's speaker identities are in engine code: "
        + "; ".join(offenders))


# ── 2. Once per speaker per reel ─────────────────────────────────────

def test_a_speaker_appearing_twice_gets_exactly_one_graphic(tmp_path):
    """The other case the ask turns on."""
    folder = _project(tmp_path, "repeat",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel(
        reel_name="Reel 13",
        lines=_lines(("Ada", 0.5), ("Bram", 4.0), ("Ada", 9.0),
                     ("Ada", 17.0), ("Bram", 22.0), ("Ada", 30.0)),
        reel_seconds=45.0, project_folder=folder, width=1080, height=1920)
    speakers = [i.speaker for i in plan.introductions]
    assert speakers == ["Ada", "Bram"]
    assert len(plan.entries) == 2
    assert [e["start_seconds"] for e in plan.entries] == [0.5, 4.0]


# ── A speaker who appears but says nothing still gets a card ──────

def test_a_speaker_who_appears_but_says_nothing_still_gets_a_card(tmp_path):
    """The captain's 2026-09-30 ruling: the card identifies the person,
    not the sentence. Reel 05's stale `no_lines_in_the_reel` row named
    nobody although both speakers were in the reel's cast list.

    The card opens the reel - 0.0s is the reel's own start, and each
    further line-less speaker starts where the previous card's hold
    ends, in the proposal's speaker order. The hold is the project's,
    the order is the proposal's, so no timing is invented."""
    from library.tools.motion_graphics_plan import resolve_plan

    folder = _appearing_project(tmp_path, "lineless")
    plan = si.plan_for_reel(
        "Reel 05", [], 60.0, folder, width=1080, height=1920,
        appearing_speakers=("Ada", "Bram"))
    assert plan.declared is True
    assert plan.basis == si.SPEAKERS_INTRODUCED
    assert [i.speaker for i in plan.introductions] == ["Ada", "Bram"]
    assert all(i.line_less for i in plan.introductions)
    assert [i.says for i in plan.introductions] == ["", ""]
    assert [e["start_seconds"] for e in plan.entries] == [0.0, 3.0]
    assert plan.refused == []
    resolved = resolve_plan(plan.entries, timeline_duration=60.0, fps=24.0)
    assert not resolved.dropped, [d.reason for d in resolved.dropped]
    assert len(resolved.moments) == 2


def test_a_silent_appearing_speaker_joins_a_speaking_one(tmp_path):
    """Mixed reel: Bram is in the cast list but says nothing, Ada
    speaks at 5s. Bram's card opens the reel; Ada's lands on her
    line. Neither is truncated: 0 + 3.0 <= 5.0."""
    folder = _appearing_project(tmp_path, "mixed")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 5.0)), 60.0, folder, width=1080, height=1920,
        appearing_speakers=("Bram",))
    assert plan.basis == si.SPEAKERS_INTRODUCED
    assert [(i.speaker, i.at_seconds, i.line_less)
            for i in plan.introductions] == [
                ("Bram", 0.0, True), ("Ada", 5.0, False)]
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.0, 3.0), (5.0, 3.0)]


def test_a_card_that_cannot_be_drawn_is_refused_by_name(tmp_path):
    """Each refusal keeps the cards that can still be drawn:

    - speech wins a tie: Ada speaks at 0.0s and Bram appears silently,
      so the appearance-anchored card is shortened below the
      readability floor and refused;
    - hold-spaced openings walk off a short reel: the second 3.0s card
      of a 2.0s reel starts outside it;
    - there are no house looks: a speaker with no colour anywhere is
      refused, never given an engine-chosen one."""
    colourless = {**DECLARATION, "speakers": {"Ada": {"name": "Ada Lovelace"}}}
    rows = [
        (DECLARATION, _lines(("Ada", 0.0)), 60.0, ("Bram",),
         ["Ada"], [("Bram", si.TRUNCATED_BELOW_READABLE)]),
        (DECLARATION, [], 2.0, ("Ada", "Bram"),
         ["Ada"], [("Bram", si.OUTSIDE_THE_REEL)]),
        (colourless, _lines(("Ada", 1.0)), 40.0, (),
         [], [("Ada", si.NO_COLOUR_DECLARED)]),
    ]
    for n, (declaration, lines, seconds, appearing, introduced,
            refused) in enumerate(rows):
        folder = _project(tmp_path, f"r{n}",
                          effect={si.DECLARATION_KEY: declaration})
        plan = si.plan_for_reel(
            "R", lines, seconds, folder, width=1080, height=1920,
            appearing_speakers=appearing)
        assert [i.speaker for i in plan.introductions] == introduced, n
        assert [(r["speaker"], r["reason"])
                for r in plan.refused] == refused, n


# ── A declaration that cannot be read is refused, never completed ────


def test_a_key_nothing_reads_raises():
    with pytest.raises(si.SpeakerIdentityError) as why:
        si.declared_speakers({**DECLARATION, "opacity": 0.5})
    assert "opacity" in str(why.value)


# ── The plan reaches the renderer's own resolver ─────────────────────

def test_the_entry_resolves_through_step_4_06s_own_resolver(tmp_path):
    """The plan this module writes must be a plan `resolve_plan` accepts
    - otherwise the graphic is dropped at the door and the drop reason
    is the first anyone hears of it."""
    from library.tools.motion_graphics_plan import resolve_plan

    folder = _project(tmp_path, "resolves",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Ada", 2.0)), 40.0, folder, width=1080, height=1920)
    resolved = resolve_plan(plan.entries, timeline_duration=40.0, fps=24.0)
    assert not resolved.dropped, [d.reason for d in resolved.dropped]
    assert len(resolved.moments) == 1
    moment = resolved.moments[0]
    assert moment["element"] == "lower_third"
    assert moment["color"] == "#11FFAA"
    assert [run["text"] for run in moment["runs"]] == [
        "Ada Lovelace", "Analyst"]
    # What makes it the CONSTRUCTION rather than the flat panel.
    assert moment["data"]["construction"] == "staged_rule"


# ── One row, one moment: a card ends where the next begins ────────

HOLD_3_5 = {
    "anchor": "bottom_left",
    "hold_seconds": 3.5,
    "entrance": "draw",
    "exit": "fade",
    "speakers": {
        "Ada": {"name": "Ada Lovelace", "title": "Analyst",
                "colour": "#11FFAA"},
        "Bram": {"name": "Bram Stoker", "title": "Editor",
                 "colour": "#FF3366"},
        "Cy": {"name": "Cy Okonkwo", "title": "Producer",
               "colour": "#33AAFF"},
    },
}


def _held_project(tmp_path, name):
    return _project(tmp_path, name,
                    effect={si.DECLARATION_KEY: HOLD_3_5})


def test_a_card_ends_where_the_next_speakers_card_begins(tmp_path):
    """The Reel 06 overlap of 2026-09-12: 0.0s and 3.23s against a
    3.5s hold put both cards on screen together for 0.27s in one
    layout slot. Two things cannot occupy one row at one time, so the
    earlier card is truncated - mechanically, not by taste - and the
    hold itself is untouched."""
    folder = _held_project(tmp_path, "truncate")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 3.23)), 60.0, folder, width=1080, height=1920)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.0, 3.23), (3.23, 3.5)]
    first = plan.entries[0]
    assert first["data"]["truncated_for_next"] == {
        "hold_seconds": 3.5, "duration_seconds": 3.23,
        "next_starts_at": 3.23}
    assert "Truncated to 3.23s from 3.5s" in first["why"]
    assert "truncated_for_next" not in plan.entries[1]["data"]
    assert [i.speaker for i in plan.introductions] == ["Ada", "Bram"]


def test_a_card_truncated_below_the_readability_floor_is_refused(tmp_path):
    """A 0.3s card is not a name the viewer saw. Below the pipeline's
    own floor for timed on-screen text
    (`manifest_validator.MIN_CAPTION_DISPLAY_SECONDS`) the card is
    refused - with the gap, the hold and the floor all named, so the
    run says which line the project has to move - and its introduction
    goes with it, because the two lists are parallel."""
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS

    folder = _held_project(tmp_path, "too-short")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 0.3)), 60.0, folder, width=1080, height=1920)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.3, 3.5)]
    assert [i.speaker for i in plan.introductions] == ["Bram"]
    assert [(r["speaker"], r["reason"]) for r in plan.refused] == [
        ("Ada", si.TRUNCATED_BELOW_READABLE)]
    detail = plan.refused[0]["detail"]
    assert "0.3s" in detail and "3.5s" in detail
    assert str(MIN_CAPTION_DISPLAY_SECONDS) in detail
    assert si.TRUNCATED_BELOW_READABLE in si.REFUSALS


# ── Where it sits: measured, above the captions ──────────────────────


# ── What was DRAWN, not what was planned ─────────────────────────────

INSETS = {"top": 120, "right": 120, "bottom": 540, "left": 90}


def _measured(rows, cols, frame=(1080, 1920)):
    return {"frame": list(frame), "ink_pixels": 1000,
            "rows": list(rows), "cols": list(cols),
            "touches_frame_edge": []}


def test_a_render_measurement_is_judged_in_delivery_frame_pixels():
    """Ink on the caption row is refused; a tight-boxed overlay's
    extents are not delivery-frame coordinates, and reading them as if
    they were is how a placement check becomes a confident wrong answer."""
    findings = si.render_findings(
        _measured((1300, 1450), (90, 700)), INSETS, 1080, 1920)
    assert [(f["code"], f["severity"]) for f in findings] == [
        (si.INK_LEFT_THE_BOX, "error")]
    findings = si.render_findings(
        _measured((0, 120), (0, 400), frame=(420, 140)),
        INSETS, 1080, 1920)
    assert [(f["code"], f["severity"]) for f in findings] == [
        ("not_the_delivery_frame", "warning")]


# ── The record a check grades against ────────────────────────────────


def test_promotion_renames_the_staging_record_to_the_final_name(tmp_path):
    """A staged build records under `<final> (rebuild staging)`; the
    promotion must rename it, or `plan_for` answers None for a reel
    that really has a plan. Incident: docs/evidence/speaker_identity.md
    ("The staging record that was never renamed")."""
    folder = _project(tmp_path, "promote",
                      effect={si.DECLARATION_KEY: DECLARATION})
    staging = "Reel 07 (rebuild staging)"
    final = "Reel 07"
    si.write_plans(folder, [si.plan_for_reel(
        staging, _lines(("Ada", 1.0)), 40.0, folder, width=1080, height=1920)])
    assert si.plan_for(si.read_plans(folder), final) is None

    si.rename_plan_reels(folder, {staging: final})

    stored = si.read_plans(folder)
    assert {p["reel"] for p in stored["plans"]} == {final}
    assert si.plan_for(stored, final)["basis"] == si.SPEAKERS_INTRODUCED


def test_a_refused_staging_leaves_no_lower_third_record(tmp_path):
    """The gate-fail half: no record may survive for a container that
    is about to be deleted."""
    folder = _project(tmp_path, "refused",
                      effect={si.DECLARATION_KEY: DECLARATION})
    si.write_plans(folder, [
        si.plan_for_reel("Reel 07 (rebuild staging)",
                         _lines(("Ada", 1.0)), 40.0, folder, width=1080, height=1920),
        si.plan_for_reel("Reel 08", _lines(("Bram", 1.0)), 40.0, folder, width=1080, height=1920)])

    si.drop_plan_reels(folder, ["Reel 07 (rebuild staging)"])

    assert {p["reel"] for p in si.read_plans(folder)["plans"]} == {"Reel 08"}


# ── What the rebuild decision sees ───────────────────────────────────

def test_a_lower_third_that_moved_changes_the_rebuild_digest():
    """The file is CONTENT-KEYED, so two reels naming the same speaker
    for the same length share one `segment_id`. The id alone therefore
    cannot tell a graphic that moved from one that did not - which is
    exactly what a first appearance shifting does - so the row carries
    `timeline_start` as well.

    `reel_rebuild_need.decide` is fail-closed; this is the half that
    feeds it, and a digest that cannot see a change is a reel that
    silently keeps the old graphic.
    """
    from library.tools import reel_build as rb
    from library.tools import reel_rebuild_need as need

    base = dict(
        reel_number=23, engine_code="eng", project_wide="proj",
        plan_content_hash="plan", transcript_hash="tr", master_digest="m",
        ranges=[(0.0, 10.0)], placements_list=[], cards=[],
        caption_segments=[], explainer_segments=[], semantic_segments=[],
        overlay_placements=None, motion_record=None, ending=None,
        look=None, grade_cdl=None, grade_look=None, power_grade=None)
    common = {"extra_cuts": [], "insisted": []}

    def digest(segments):
        extra = dict(common)
        if segments:
            extra["lower_thirds"] = rb._lower_third_rows(segments)
        return need.derivation_digest(**base, extra=extra)

    same_id = "mg_project_abc123"
    at_start = [{"segment_id": same_id, "timeline_start": 0.3,
                 "total_frames": 84,
                 "measured_box": (90, 1070, 506, 1191)}]
    moved = [{**at_start[0], "timeline_start": 5.9}]
    redrawn = [{**at_start[0], "measured_box": (90, 900, 506, 1021)}]

    assert digest(at_start) != digest(None)
    assert digest(moved) != digest(at_start), (
        "a lower third that MOVED digests the same as one that did not")
    assert digest(redrawn) != digest(at_start), (
        "a lower third whose ink landed elsewhere digests the same")


# --------------------------------------------------------------------------
# From test_single_track_diarization.py
#
# Single-track diarization fallback: routing, turns, voice prints.
#
# Each test names a defect it would catch:
#
# - the fallback taken when per-ISO tracks exist (multi-path timelines
#   must keep the unchanged per-ISO path);
# - the fallback taken for a declared monologue, or a declared roster's
#   count not reaching the clusterer;
# - the fallback skipped by `--no-diarize-single-track`;
# - union-run seams double-claimed (the eval measured this before the
#   truncation existed);
# - the k search accepting a singleton collapse;
# - a segment voice print averaged over the wrong windows;
# - a rebind dropping or corrupting the new `voice_embedding` field.
#
# ffmpeg is real here; the fixtures are tiny generated tones. The ML
# stack is NOT: embeddings are small synthetic arrays and `diarize_track`
# is monkeypatched to raise when the path under test must not hear audio.

_SECTION_1_MARK = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required; CI installs them (AGENTS.md 9)")

FPS = 24000 / 1001


def _tone(path: Path, seconds: float, freq: int = 440) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True)
    return path


def _clip(source, src_in, src_out, tl_start, tl_end, uid="uid",
          speaker="A"):
    return TimelineClip(
        resolve_item_id=uid, track_type="video", track_index=1,
        track_name=speaker, speaker=speaker, source_file=str(source),
        source_in=src_in, source_out=src_out,
        source_in_frame=round(src_in * FPS),
        source_out_frame=round(src_out * FPS),
        source_frames=None,
        timeline_start=tl_start, timeline_end=tl_end, name="clip")


def _snapshot(clips):
    return types.SimpleNamespace(
        picture_clips=lambda: list(clips),
        clips=list(clips),
        project_name="probe",
        timeline_name="probe-timeline",
        fps=FPS,
        duration=max((c.timeline_end for c in clips), default=0.0))


def _aligned(text, start, end, words=4):
    step = (end - start) / words
    return {"segments": [{
        "text": text, "start": start, "end": end,
        "words": [{"word": f"w{i}", "start": start + i * step,
                   "end": start + (i + 1) * step, "timed": True}
                  for i in range(words)]}]}


def _refuse_diarize(*args, **kwargs):
    raise AssertionError("fallback taken when it should not be")


# ── routing: the fallback taken when it should not be ────────────────

def _refuse_missing(*args, **kwargs):
    raise std.DiarizationUnavailable("no checkout")


# (case, project.yaml, clip speakers, diarize flag, diarizer stub,
#  expected path, reason fragment)
SINGLE_LABEL_ROUTES = [
    # Two timeline speakers never reach the diarizer, even with no
    # declared roster: the per-ISO path owns multi-track timelines.
    ("two audio paths", None, ["Akshita", "Craig"], True, _refuse_diarize,
     "per-iso", None),
    # One path plus a one-name roster keeps the single label: diarizing a
    # declared monologue could only split one voice into invented speakers.
    ("declared monologue", "source:\n  speakers:\n    - {name: Craig}\n",
     [None], True, _refuse_diarize, "single-label", None),
    # `--no-diarize-single-track` holds even an eligible timeline.
    ("opt-out flag", None, [None], False, _refuse_diarize, "single-label",
     "no-diarize-single-track"),
    # No weights on the machine reads as single-label with the reason on
    # the record - never a crash, never silence.
    ("encoder unavailable", None, [None], True, _refuse_missing,
     "single-label", "unavailable"),
]


@_SECTION_1_MARK
def test_the_fallback_is_not_taken_where_it_must_not_be(tmp_path,
                                                        monkeypatch):
    for (case, yaml_text, speakers, diarize, stub, path,
         reason) in SINGLE_LABEL_ROUTES:
        folder = tmp_path / case.replace(" ", "_")
        folder.mkdir()
        if yaml_text:
            (folder / "project.yaml").write_text(yaml_text, encoding="utf-8")
        src = _tone(folder / "src.wav", 2.0)
        span = 2.0 / len(speakers)
        clips = [_clip(src, 0.0, span, i * span, (i + 1) * span,
                       uid=f"u{i}", speaker=who)
                 for i, who in enumerate(speakers)]
        monkeypatch.setattr(std, "diarize_track", stub)
        monkeypatch.setattr(
            tt, "transcribe_audio",
            lambda p, span=span, **kw: (
                _aligned("hello", 0.0, span, 2), {"arm": "test"}))
        document = tt.build_and_transcribe(
            str(folder), _snapshot(clips), diarize=diarize)
        assert document["diarization"]["path"] == path, case
        assert document["segments_with_voice_embedding"] == 0, case
        assert document["segment_count"] > 0, case
        if reason:
            assert reason in document["diarization"]["reason"], case
        if case == "two audio paths":
            assert sorted(document["speakers"]) == ["Akshita", "Craig"]


@_SECTION_1_MARK
def test_declared_roster_count_reaches_k(tmp_path, monkeypatch):
    """A two-name roster on one mic is the case the fallback exists for,
    and its declared count must reach the clusterer: estimating k when
    the project already said it would throw away the stronger signal."""
    (tmp_path / "project.yaml").write_text(
        "source:\n  speakers:\n    - {name: Akshita}\n    - {name: Craig}\n",
        encoding="utf-8")
    src = _tone(tmp_path / "src.wav", 2.0)
    clips = [_clip(src, 0.0, 2.0, 0.0, 2.0, uid="a", speaker=None)]
    asked = []

    def _diarize(*args, **kwargs):
        asked.append(kwargs.get("num_speakers"))
        return _canned_diarization()

    monkeypatch.setattr(std, "diarize_track", _diarize)
    monkeypatch.setattr(
        tt, "transcribe_audio",
        lambda path, **kw: (_aligned("hello", 0.0, 1.0, 2), {"arm": "test"}))
    document = tt.build_and_transcribe(str(tmp_path), _snapshot(clips))
    assert asked == [2]
    assert document["diarization"]["path"] == "single-track-fallback"


def _canned_diarization():
    return std.Diarization(
        clusters=[
            std.SpeakerCluster(label="speaker_01", turns=[(0.0, 1.0)],
                               centroid=(1.0, 0.0, 0.0, 0.0)),
            std.SpeakerCluster(label="speaker_02", turns=[(1.0, 2.0)],
                               centroid=(0.0, 1.0, 0.0, 0.0))],
        window_embeddings=np.array([[1.0, 0.0, 0.0, 0.0],
                                    [0.6, 0.8, 0.0, 0.0],
                                    [0.0, 1.0, 0.0, 0.0]]),
        window_starts=np.array([0.0, 0.5, 1.0]),
        window_labels=[0, 0, 1],
        speakers_estimated=2,
        inference_seconds=0.1,
        method="test",
        weights="test")


@_SECTION_1_MARK
def test_eligible_single_path_diarizes_and_prints_voices(tmp_path,
                                                         monkeypatch):
    """One path, no roster: clusters transcribe separately and every row
    carries its voice print for the person-entity task."""
    src = _tone(tmp_path / "src.wav", 2.0)
    clips = [_clip(src, 0.0, 2.0, 0.0, 2.0, uid="a", speaker=None)]
    monkeypatch.setattr(std, "diarize_track",
                        lambda *a, **k: _canned_diarization())

    def _hear(path, **kw):
        label = kw.get("label", "")
        if label == "speaker_01":
            return _aligned("first voice", 0.0, 1.0, 2), {"arm": "test"}
        return _aligned("second voice", 1.0, 2.0, 2), {"arm": "test"}

    monkeypatch.setattr(tt, "transcribe_audio", _hear)
    document = tt.build_and_transcribe(str(tmp_path), _snapshot(clips))
    assert document["diarization"]["path"] == "single-track-fallback"
    assert document["diarization"]["speakers_estimated"] == 2
    assert document["speakers"] == ["speaker_01", "speaker_02"]
    assert (document["segments_with_voice_embedding"]
            == document["segment_count"] > 0)
    first = next(s for s in document["segments"]
                 if s["speaker"] == "speaker_01")
    # Windows [0.0, 1.5] and [0.5, 2.0] overlap the (0, 1) span: the
    # print is their normalized mean, not one window's.
    assert first["voice_embedding"][:2] == pytest.approx([0.8944, 0.4472],
                                                         abs=0.01)
    assert len(first["voice_embedding"]) == 4


# ── turns: the seam a dropped window leaves ──────────────────────────

@_SECTION_1_MARK
def test_merge_runs_never_double_claims_a_seam():
    """A VAD-dropped window splits runs but both 1.5 s windows still
    cover the seam, and a speaker change overlaps the same way: without
    truncation the hypothesis claims the seam twice and every overlap
    counts double downstream. The earlier tail wins; the later turn
    starts where it ends."""
    cases = [
        ([0.0, 0.5, 1.0, 2.0, 2.5], [0, 0, 0, 0, 0],
         [(0.0, 2.5, 0), (2.5, 4.0, 0)]),
        ([0.0, 0.5, 1.0, 1.5], [0, 0, 1, 1],
         [(0.0, 2.0, 0), (2.0, 3.0, 1)]),
    ]
    for starts, labels, expected in cases:
        turns = std.merge_runs_to_turns(np.array(starts), labels)
        assert turns == expected
        for (first_start, first_end, _), (second_start, _, _) in (
                itertools.pairwise(turns)):
            assert first_start < first_end
            assert second_start >= first_end


# ── voice prints and the k search, without the ML stack ──────────────

@_SECTION_1_MARK
def test_k_search_skips_singleton_collapse():
    """Two tight voices plus a silhouette scan must return 2, not a
    k whose cluster holds one window: the guard is what makes k=3
    unparsable input rather than a wrong answer."""
    rng = np.random.RandomState(7)
    dim = 8
    center_a = np.eye(dim)[0]
    center_b = np.eye(dim)[1]
    embeddings = np.array(
        [center_a + rng.normal(0, 0.01, dim) for _ in range(4)]
        + [center_b + rng.normal(0, 0.01, dim) for _ in range(4)])
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1,
                                             keepdims=True)
    k, scores = std.estimate_speaker_count(embeddings, k_max=4)
    assert k == 2
    assert 3 not in scores


@_SECTION_1_MARK
def test_segment_embeddings_average_member_windows():
    """A span's print is the normalized mean of its overlapping
    windows; a span in a VAD gap takes the nearest cluster's print -
    never a zero vector, which would read as a voice."""
    diarization = _canned_diarization()
    prints = std.segment_voice_embeddings([(0.0, 1.0), (1.0, 2.0)],
                                          diarization)
    # 1.5 s windows overlap generously: span (0, 1) hears windows 0.0
    # and 0.5, span (1, 2) hears all three (window 0.0 still covers
    # [1.0, 1.5]). Both prints are the normalized mean of what actually
    # overlapped.
    assert prints[0][:2] == pytest.approx([0.8944, 0.4472], abs=0.01)
    assert prints[1][:2] == pytest.approx([0.6644, 0.7474], abs=0.01)
    # A span in a VAD gap takes the nearest cluster's print - the
    # speaker_02 turn (1, 2) outranks speaker_01's (0, 1) past 1.5 s.
    gapped = std.segment_voice_embeddings([(5.0, 6.0)], diarization)
    assert gapped == [diarization.clusters[1].centroid]


@_SECTION_1_MARK
def test_rebind_keeps_voice_prints(tmp_path):
    """A rebind must carry the measured prints through untouched: it
    re-derives bindings, never measurements. A row without one keeps
    None rather than gaining an empty tuple."""
    snapshot = _snapshot([])
    document = {
        "segments": [{
            "speaker": "speaker_01", "text": "hi",
            "timeline_start": 0.0, "timeline_end": 1.0,
            "source_file": "/m/a.MXF", "source_start": 0.0,
            "source_end": 1.0, "resolve_item_id": "clip-a",
            "words": [{"word": "hi", "start": 0.0, "end": 0.5,
                       "timed": True}],
            "read_from_words": False, "avg_logprob": None,
            "voice_embedding": [0.6, 0.8, 0.0, 0.0]},
            {"speaker": "speaker_01", "text": "old",
             "timeline_start": 2.0, "timeline_end": 3.0,
             "source_file": "/m/a.MXF", "source_start": 2.0,
             "source_end": 3.0, "resolve_item_id": "clip-b",
             "words": [{"word": "old", "start": 2.0, "end": 2.5,
                        "timed": True}],
             "read_from_words": False, "avg_logprob": None}],
        "transcription": {}, "mic_bleed_resolution": []}
    rebound = tt.rebind_document(document, snapshot)
    kept = {s["text"]: s for s in rebound["segments"]}
    assert kept["hi"]["voice_embedding"] == [0.6, 0.8, 0.0, 0.0]
    assert kept["old"]["voice_embedding"] is None


# --------------------------------------------------------------------------
# From test_conversation_clock.py
#
# The conversation clock (M6): offset recovery, grouping, cross-check.
#
# Every test builds its M1 documents and its memory under `tmp_path`/
# `memory_root`. No test reaches a real project (AGENTS.md §8) or runs
# voz/MFA: n-gram matching and the Resolve cross-check are pure
# functions over small synthetic word lists.

@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _m1_doc(words_with_times, word_count=None, digest="d") -> dict:
    words = [{"word": w, "start": t, "end": t + 0.4}
             for w, t in words_with_times]
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": source_memory.M1_STATUS_TRANSCRIBED,
        "utterance_cut": "hybrid-windows",
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "utterances": [{"start": words[0]["start"], "end": words[-1]["end"],
                        "text": " ".join(w for w, _ in words_with_times),
                        "words": words, "confidence": 0.0,
                        "method": "hybrid-mfa"}] if words else [],
        "utterance_count": 1 if words else 0,
        "word_count": word_count if word_count is not None else len(words),
        "speech_seconds": 0.0,
        "instrument": {"arm": "hybrid", "aligner": "mfa"},
    }


def _distinct_words(count, prefix="w"):
    """Every 4-gram over this vocabulary is unique by construction."""
    return [f"{prefix}{i:04d}" for i in range(count)]


# ── pairwise_offset ──────────────────────────────────────────────────


def test_pairwise_offset_recovers_the_shift_despite_outliers():
    """B transcribed `shift` seconds later than A is reported with that
    sign: `a_time == b_time + offset_seconds`. A contiguous run of
    matches on an unrelated offset must not drag the median -
    `agreeing_matches` says how many were used."""
    vocab = _distinct_words(80)
    a = _m1_doc([(w, float(i)) for i, w in enumerate(vocab)])
    shift = 12.5
    b = _m1_doc([(w, float(i) + shift) for i, w in enumerate(vocab)])
    result = cc.pairwise_offset(a, b)
    assert result["offset_seconds"] == pytest.approx(-shift, abs=0.01)
    assert result["agreement_fraction"] == 1.0
    assert result["agreeing_matches"] == len(vocab) - cc.NGRAM_SIZE + 1

    times_b = [float(i) + 5.0 for i in range(len(vocab))]
    times_b[-5:] = [t + 500 for t in times_b[-5:]]
    result = cc.pairwise_offset(a, _m1_doc(list(zip(vocab, times_b))))
    assert result["offset_seconds"] == pytest.approx(-5.0, abs=0.01)
    assert result["total_candidate_matches"] > result["agreeing_matches"]
    assert result["agreement_fraction"] < 1.0


# ── grouping and the shared clock ───────────────────────────────────


def test_discover_edges_only_keeps_qualifying_pairs():
    """C shares no word with A or B: there is nothing to match, so no
    edge - not a spurious 0 offset."""
    vocab = _distinct_words(80)
    a = _m1_doc([(w, float(i)) for i, w in enumerate(vocab)], digest="A")
    b = _m1_doc([(w, float(i) + 3.0) for i, w in enumerate(vocab)], digest="B")
    c = _m1_doc([(w, float(i)) for i, w in enumerate(_distinct_words(40, "z"))],
               digest="C")

    edges = cc.discover_edges({"A": a, "B": b, "C": c})

    assert set(edges) == {("A", "B")}
    assert cc.pairwise_offset(a, c) is None
    groups = cc.group_digests(["A", "B", "C"], edges)
    assert groups == [["A", "B"]]


def test_clock_for_group_chains_a_transitive_offset():
    """B only connects A and C; C's offset to A must walk through B."""
    edges = {
        ("A", "B"): {"offset_seconds": 2.0, "agreeing_matches": 100,
                    "agreement_fraction": 0.99},
        ("B", "C"): {"offset_seconds": -5.0, "agreeing_matches": 100,
                    "agreement_fraction": 0.99},
    }
    # A_time == B_time + 2; B_time == C_time + (-5) => B_time = C_time - 5
    # => A_time == C_time - 5 + 2 == C_time - 3
    word_counts = {"A": 10, "B": 5, "C": 1}  # A is the reference

    clocks = cc.clock_for_group(["A", "B", "C"], edges, word_counts)

    assert clocks["A"]["reference_digest"] == "A"
    assert clocks["A"]["offset_to_reference_seconds"] == 0.0
    assert clocks["B"]["offset_to_reference_seconds"] == pytest.approx(2.0)
    assert clocks["B"]["direct_measurement"] is not None
    assert clocks["C"]["offset_to_reference_seconds"] == pytest.approx(-3.0)
    assert clocks["C"]["path_to_reference"] == ["C", "B", "A"]
    # C-A was never measured directly - only through B.
    assert clocks["C"]["direct_measurement"] is None


# ── the Resolve cross-check reads saved records, direction-safe ────


def _segment(source_file, source_start, source_end, timeline_start,
            timeline_end):
    return {"source_file": source_file, "source_start": source_start,
           "source_end": source_end, "timeline_start": timeline_start,
           "timeline_end": timeline_end, "resolve_item_id": "x"}


def test_resolve_cut_offsets_is_antisymmetric_and_skips_loose_boundaries():
    """Swapping which source is named `a` negates the result - a cut's
    implied offset does not depend on which side you call A. A boundary
    gap wider than the tolerance is a real edit, not a camera-switch
    instant, and is not read as one."""
    segments = [
        _segment("A.mov", 10.0, 14.0, 0.0, 4.0),
        _segment("B.mov", 6.2, 20.0, 4.1, 17.9),
    ]
    a_minus_b = cc.resolve_cut_offsets(segments, "A.mov", "B.mov")
    b_minus_a = cc.resolve_cut_offsets(segments, "B.mov", "A.mov")
    assert a_minus_b == pytest.approx([14.0 - 6.2])
    assert b_minus_a == pytest.approx([6.2 - 14.0])

    loose = [
        _segment("A.mov", 10.0, 14.0, 0.0, 4.0),
        _segment("B.mov", 6.2, 20.0, 9.0, 22.8),  # 5 s gap
    ]
    assert cc.resolve_cut_offsets(loose, "A.mov", "B.mov") == []


# ── map_time ─────────────────────────────────────────────────────


def test_map_time_round_trips_through_the_reference(memory_root):
    source_memory.write_json(
        source_memory.source_dir("A") / cc.SLOT_CLOCK,
        {"content_digest": "A", "group_id": "g", "group_members": ["A", "B"],
         "reference_digest": "A", "offset_to_reference_seconds": 0.0,
         "path_to_reference": ["A"], "direct_measurement": None,
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})
    source_memory.write_json(
        source_memory.source_dir("B") / cc.SLOT_CLOCK,
        {"content_digest": "B", "group_id": "g", "group_members": ["A", "B"],
         "reference_digest": "A", "offset_to_reference_seconds": 3.8,
         "path_to_reference": ["B", "A"], "direct_measurement": {},
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})

    assert cc.map_time("B", 100.0) == {"A": pytest.approx(103.8)}
    assert cc.map_time("A", 103.8) == {"B": pytest.approx(100.0)}
    assert cc.map_time("unknown-digest", 5.0) == {}
