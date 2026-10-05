from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from tests.promotion_test_helpers import install_fake_timeline_snapshots
from tests.promotion_test_helpers import install_measured_draw_gain_probe
from library.tools.reel_build import rebuild_reels_in_project
import json
from contextlib import contextmanager
from library.tools.reel_build import (
    STAGING_SUFFIX,
)
from tests.resolve_double import FakeProject
from library.tools.reel_build import (
    ReelBuildError,
    assert_deletion_scope,
    timelines_to_replace,
)
from tests.resolve_double import FakeTimeline
from library.tools import staging_holds as holds
from library.tools.reel_build import (
    ReelVerificationRefused,
)
import io
from tests.resolve_double import FakeResolve
from types import SimpleNamespace
from library.steps.step_6_01_render.build_verification import (  # noqa: E402
    derive_verification_verdict,
    detect_unreachable_fusion_effects,
    format_fusion_drop_error,
)


MASTER = "GEO Podcast - Synced"

# These tests drive the builder against a STAND-IN Resolve project, which
# has no media pool to file. Organising is exercised where it can be:
# `tests/unit/resolve/test_resolve_organization.py` against a pool double that answers
# the way the API was measured to, and on the captain's own project.
ORGANISE = False

@pytest.fixture
def mock_dvr(stub_resolve_script, monkeypatch):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    install_fake_timeline_snapshots(monkeypatch)
    install_measured_draw_gain_probe(monkeypatch)
    yield


class _StagingTimeline:
    """A timeline whose name really changes when renamed."""

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class _ResolveProject:
    """A Resolve project whose pool really creates, renames and deletes."""

    def __init__(self, names):
        self.timelines = [_StagingTimeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def _create(self, name):
        timeline = _StagingTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]

    # A Resolve project HAS a cursor, and `resolve_lock`'s guard reads
    # it back by unique id - a fake without one cannot model the guard.
    def GetCurrentTimeline(self):
        # None until something sets it: a project that has not been
        # pointed anywhere has no cursor, and inventing one here would
        # hand the entry-unit guard a timeline nobody opened.
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


def _placing(resolve_project):
    """The placer mock, creating the container it was asked for.

    The real placer creates the timeline first and fills it after, so
    "what exists afterwards" is honest at the container level while
    every placed item stays mocked away.
    """
    def _place(**kwargs):
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}
    return _place

@pytest.fixture
def mock_project_env(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    
    # project.yaml
    (project_dir / "project.yaml").write_text('resolve: {project_name: "Mock Project", timeline_name: "GEO Podcast - Synced"}')
    
    # pipeline_output/review/reel_proposals_v2.json
    review_dir = project_dir / "pipeline_output" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "reel_proposals_v2.json").write_text("[]")
    
    # pipeline_output/scratch/timeline_transcript/transcript.json
    scratch_dir = project_dir / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "transcript.json").write_text('{"segments": []}')
    
    return project_dir


@pytest.mark.usefixtures("mock_dvr")
@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
def test_verifier_unavailable_fails(mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_project_env):
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]
    
    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    original_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "library.tools.reel_conformance_verifier":
            raise ImportError("Mocked import error")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(RuntimeError, match="Reel conformance verifier is unavailable"):
            rebuild_reels_in_project(str(mock_project_env),
                             organise=ORGANISE)


@pytest.mark.usefixtures("mock_dvr")
@patch("library.tools.project_registry.get_project")
@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_by_slug_resolves_the_project_folder(
        mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop,
        mock_resolve_proj, mock_scriptapp, mock_build, mock_get_project,
        mock_project_env):
    """A slug resolves through `ProjectConfig.project_root` (issue #895).

    The slug branch read `proj.root`, which `ProjectConfig` never had -
    wrong since #507 - so any slug call raised `AttributeError` while
    every caller and test passed an absolute folder and never touched
    the branch.  If this fails, the branch is reaching past the config's
    real attribute again.
    """
    from types import SimpleNamespace
    mock_get_project.return_value = SimpleNamespace(
        project_root=mock_project_env)

    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]

    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    mock_run_verif.return_value = 0

    rebuild_reels_in_project("some-slug", organise=ORGANISE)

    mock_get_project.assert_called_once_with("some-slug")
    mock_build.assert_called_once()
    assert mock_proj.names() == [MASTER, "Reel 01"]


@pytest.mark.usefixtures("mock_dvr")
def test_rebuild_reels_unknown_slug_is_refused():
    """An unknown slug raises `ValueError`, not `AttributeError`."""
    from unittest.mock import patch as _patch
    with _patch("library.tools.project_registry.get_project",
                return_value=None):
        with pytest.raises(ValueError, match="Unknown project"):
            rebuild_reels_in_project("no-such-slug", organise=ORGANISE)


# --------------------------------------------------------------------------
# From test_reel_build_gate_keeps_good_reel.py
#
# A gate-failing reel build must not overwrite the good timeline.
#
# The defect this pins
# --------------------
# `rebuild_reels_in_project` deleted the existing timelines FIRST, placed
# the rebuild, and ran the conformance verifier LAST. A build the gate
# refused - reel 5 of the 2026-09-08 rebuild, F17 (mixed-speaker card)
# plus F8 (end cuts SpeakerTwo mid-word through 'about') - exited 1 AFTER
# replacing the timeline, converting a live approved reel into
# verify-failed content with no code/content fix in hand
# (`docs/REEL_REBUILD_RUN_20260908_R3.md`).
#
# The fix is structural: the rebuild is staged into a separate container,
# the gate grades the staging, and the original is replaced only on a
# pass. On a fail the staging is removed and the approved timeline is
# still there. These tests read the survivors off a fake project whose
# pool really creates, renames and deletes - a mock that only records
# the calls cannot answer "is the good reel still there".
#
# Deliberately convention-free: this file names no staging convention,
# so it fails on the pre-fix code for the right reason (the original is
# gone) rather than on an import.

APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]
TARGET = "Reel 03 - moment-3"


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


def _drive(resolve_project, project_dir, gate_result, **kwargs):
    """Drive the build with the placer creating what it is asked for.

    The mocked `build_reel_timeline` creates the container it was given
    - the one honest half of the real placer for this question - so
    "what exists afterwards" is read off the project, not inferred.
    """
    with _patched_build(
            resolve_project, project_dir, gate_result) as (placed, gate):
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed, gate


@contextmanager
def _patched_build(resolve_project, project_dir, gate_result,
                   caption_planner=None, verifier=None, moments=None):
    """All patches for driving a build; yields (placed_mock, gate_mock).

    `_drive` covers the passing path; failing-path tests enter this
    directly so the gate mock survives the raise.
    """
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    caption_patch = (
        patch("library.tools.reel_build.reel_subtitle_segments",
              side_effect=caption_planner)
        if caption_planner is not None else
        patch("library.tools.reel_build.reel_subtitle_segments",
              return_value=[]))
    verifier_patch = (
        patch("library.tools.reel_conformance_verifier.run_verification",
              side_effect=verifier)
        if verifier is not None else
        patch("library.tools.reel_conformance_verifier.run_verification",
              return_value=gate_result))
    planned_moments = moments or [
        _moment(i + 1, name) for i, name in enumerate(APPROVED)]
    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place) as placed, \
            caption_patch as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=planned_moments), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  return_value=SimpleNamespace(
                      clips=(), fps=24000 / 1001)), \
            verifier_patch as gate:
        caps.return_value = []
        yield placed, gate


def _gate_failed_report(project_dir):
    """Reel 5's shape: planning findings, not placement ones.

    The rows name the STAGED container with its error count - the
    shape the real gate writes - so the refusal attributes to the
    reel that failed. A report with no rows attributes nothing and
    discards nothing.
    """
    path = (project_dir / "pipeline_output" / "review"
            / "conformance_report.json")
    path.write_text(json.dumps({
        "has_errors": True,
        "findings": [
            {"severity": "error", "finding_class": "F17",
             "message": "caption card 'recommend you or your brand.' "
                        "mixes speakers: SpeakerOne, SpeakerTwo"},
            {"severity": "error", "finding_class": "F8",
             "message": "END at 413.85s cuts SpeakerTwo mid-speech, "
                        "through the word 'about'"},
        ],
        "reels": [
            {"reel_name": TARGET + STAGING_SUFFIX,
             "reel_number": 3,
             "errors": 2,
             "warnings": 0,
             "captions": "0/0",
             "findings": [
                 {"finding_class": "F17", "severity": "error"},
                 {"finding_class": "F8", "severity": "error"},
             ]},
        ],
    }), encoding="utf-8")


@pytest.mark.usefixtures("mock_dvr")
def test_a_gate_failing_build_leaves_the_approved_timeline_in_place(project):
    """The defect, stated as the survivors.

    The gate refuses the rebuild (F17 + F8, reel 5's shape) and the
    build raises - but the approved timeline must still be there, and
    no failing content may sit under its name. Under the
    delete-first code this reads the replaced timeline: the original
    is gone and the assertion fails on the survivors, not on a mock.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)
    _gate_failed_report(project)
    original = next(t for t in resolve_project.timelines
                    if t.GetName() == TARGET)

    with pytest.raises(RuntimeError, match="defective timeline"):
        _drive(resolve_project, project, 1, only=[3])

    survivors = resolve_project.names()
    assert original in resolve_project.timelines, (
        f"{TARGET} was destroyed by a build the gate refused - the "
        "timeline under its name now is the failing rebuild, not the "
        "approved reel")
    assert survivors.count(TARGET) == 1
    assert sorted(survivors) == sorted([MASTER] + APPROVED), (
        "a refused build must leave every timeline exactly as it was: "
        f"{sorted(survivors)}")


@pytest.mark.usefixtures("mock_dvr")
def test_a_gate_failing_build_grades_the_staging_not_the_approved_reel(project):
    """The gate never sees the approved timeline: it is asked to grade
    the staging container the build placed, by its staging name."""
    resolve_project = FakeProject([MASTER] + APPROVED)
    _gate_failed_report(project)

    with _patched_build(resolve_project, project, 1) as (_, gate), \
            pytest.raises(RuntimeError, match="defective timeline"):
        rebuild_reels_in_project(str(project), organise=False, only=[3])

    assert gate.call_args[1]["only_reels"] == [TARGET + STAGING_SUFFIX]


@pytest.mark.usefixtures("mock_dvr")
def test_a_passing_build_replaces_the_target_and_reports_final_names(project):
    """The other direction: a clean gate still replaces, and the record
    names the reels the captain sees - no staging container leaks into
    the project or the record."""
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _drive(resolve_project, project, 0, only=[3])

    assert placed.call_count == 1
    assert record["timelines_built"] == [TARGET]
    assert record["staged_timelines"] == {}
    # The replaced timeline is DELETED by default: one timeline per
    # reel, nothing archived (`library/tools/reel_retirement.py`).
    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED)
    # The sidecar baselines followed the promotion: filed under the
    # final name the gate passed, with no staging key left behind.
    provenance = json.loads(
        (project / "pipeline_output" / "review"
         / "plan_provenance.json").read_text(encoding="utf-8"))
    assert TARGET in provenance["built_reels"]
    assert not [name for name in provenance["built_reels"]
                if name.endswith(STAGING_SUFFIX)]


@pytest.mark.usefixtures("mock_dvr")
def test_build_reels_cli_plans_reel15_from_cached_iso_and_f25_passes(
        project, monkeypatch, capsys):
    """The operator command consumes a pre-1465 transcript correctly.

    This drives manage_project's real parser, reels runner, operation
    registry, build step, and verifier caller. Only Resolve placement
    and the gate's Resolve read are replaced; caption planning and the
    played-word comparison are the production code. Everything lives
    under tmp_path and cannot address the captain's project.
    """
    import sys
    from pathlib import Path

    import manage_project
    from library.steps.step_4_05_render_subtitles import (
        generate_remotion_props,
    )
    from library.tools import (
        reel_build,
        requirements,
        subtitle_coverage,
        timeline_transcript,
    )
    from library.tools import reel_conformance_verifier as verifier
    from library.tools.reel_proposal import (
        Approval,
        ReelMoment,
        proposal_path,
        write_proposal,
    )
    from library.tools.transcript_corrections import spelling_corrections

    phrase = "Yeah so AI is actually better for small businesses"
    words = phrase.split()

    def _row(speaker, start, source_start):
        duration = 2.37
        width = duration / len(words)
        timed_words = [
            {"word": word, "start": start + i * width,
             "end": start + (i + 1) * width, "timed": True}
            for i, word in enumerate(words)
        ]
        return {
            "speaker": speaker, "text": phrase,
            "timeline_start": start, "timeline_end": start + duration,
            "source_file": "fixture.mxf",
            "source_start": source_start,
            "source_end": source_start + duration,
            "resolve_item_id": f"{speaker}-clip",
            "words": timed_words,
        }

    audio_dir = (project / "pipeline_output" / "scratch"
                 / "timeline_transcript")
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "speakertwo.wav").write_bytes(b"fixture SpeakerTwo ISO")
    (audio_dir / "speakerone.wav").write_bytes(b"fixture SpeakerOne ISO")
    rows = [_row("SpeakerTwo", 1200.57, 2716.084),
            _row("SpeakerOne", 1200.62, 2719.88775)]
    transcript_path = audio_dir / "transcript.json"
    transcript_path.write_text(json.dumps({
        "segments": rows,
        "segment_count": len(rows),
        "speakers": ["SpeakerTwo", "SpeakerOne"],
        "derived_from": {"duration_seconds": 1400.0},
    }), encoding="utf-8")
    monkeypatch.setattr(
        timeline_transcript, "_track_rms_dbfs",
        lambda path, _start, _end: (
            (-42.62 if Path(path).name == "speakertwo.wav" else -27.12), None),
    )

    moment = ReelMoment(
        number=15, slug="the-3d-nail-art-salon-beats-the-chains",
        reason="cached mic bleed regression",
        timeline_start=1200.57, timeline_end=1202.94,
        source_spans=({"source_file": "fixture.mxf",
                       "source_start": 2716.084,
                       "source_end": 2719.99775},),
        approval=Approval.APPROVED,
    )
    write_proposal(str(proposal_path(project)), [moment],
                   {"derived_from": {"duration_seconds": 1400.0}})
    state = {"project_folder": str(project), "step_outputs": {}}
    state[requirements._FORCE] = {
        "resolve_scripting": True,
        "face_detector": True,
        "reel_build_libraries": True,
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")

    monkeypatch.setattr(
        generate_remotion_props, "generate_subtitle_props_per_block",
        lambda *args, **kwargs: [],
    )
    planned = []
    actual_planner = reel_build.reel_subtitle_segments

    def _capture_plan(*args, **kwargs):
        result = actual_planner(*args, **kwargs)
        planned.append((result, args[1], list(args[2])))
        return result

    def _verify_with_f25(**kwargs):
        transcript = kwargs["transcript"]
        assert [row["speaker"] for row in transcript["segments"]] == [
            "SpeakerOne"]
        assert len(planned) == 1
        plan, planned_transcript, ranges = planned[0]
        assert planned_transcript == transcript
        audio_spans = [{
            "source_file": row["source_file"],
            "source_start": row["source_start"],
            "source_end": row["source_end"],
            "reel_start": reel_build.reel_time(
                row["timeline_start"], ranges),
        } for row in transcript["segments"]]
        played_result = subtitle_coverage.played_words_from_transcript(
            transcript["segments"], audio_spans)
        played = subtitle_coverage.read_words_for_comparison(
            played_result["words"], spelling_corrections(str(project)))
        captioned = []
        cards = []
        for entry in plan.caption_entries:
            cards.append({
                "card": entry["id"],
                "reel_start": entry["timeline_start"],
                "reel_end": entry["timeline_end"],
                "text_norms": sorted({
                    subtitle_coverage.normalize_word(token)
                    for token in entry["text"].split()
                    if subtitle_coverage.normalize_word(token)
                }),
            })
            for word in entry.get("words") or []:
                norm = subtitle_coverage.normalize_word(word["word"])
                if norm:
                    captioned.append({
                        "word": word["word"], "norm": norm,
                        "card": entry["id"],
                        "reel_start": word["start"],
                        "reel_end": word["end"],
                    })
        coverage = subtitle_coverage.check_word_coverage(
            played,
            captioned,
            cards,
            suppressed=verifier._suppressed_played_words(
                played, str(project)),
        )
        errors = [item for item in coverage["findings"]
                  if item["severity"] == "error"]
        assert errors == []
        assert " ".join(entry["text"]
                         for entry in plan.caption_entries).casefold() == (
                             phrase.casefold())
        return 0

    monkeypatch.setattr(manage_project, "preflight_check", lambda _cmd: None)
    monkeypatch.setattr(sys, "argv", [
        "manage_project.py", "build-reels", str(project),
        "--rebuild-all", "--only-reel", "15",
    ])
    resolve_project = FakeProject([MASTER] + APPROVED)
    with _patched_build(
            resolve_project, project, 0,
            caption_planner=_capture_plan,
            verifier=_verify_with_f25,
            moments=[moment]):
        manage_project.main()

    assert len(planned) == 1
    assert planned[0][0].caption_entries
    assert ("ISO mic 1200.57-1202.99s: keep SpeakerOne (-27.12 dBFS) over "
            "SpeakerTwo (-42.62 dBFS), 15.50 dB lead" in capsys.readouterr().err)


# --------------------------------------------------------------------------
# From test_reel_build_refused_files_debris.py
#
# A refused reel build must not strand its caption imports.
#
# The defect
# ----------
# `build_reel_timeline` puts every caption clip through
# `pool.ImportMedia`, which lands in whatever bin is CURRENT - on the
# field test, `Reels/Current plan`. On the pass path
# `promote_staged_reels(organise=True)` files those clips afterwards;
# on the refusal path nothing did. Reel 05 of the 2026-09-08 rebuild
# refused its gate (F17 + F8) and left its 26 caption renders loose in
# `Reels/Current plan` - the staging timeline was deleted and its
# sidecar entries dropped, but the pool items stayed where ImportMedia
# left them.
#
# The fix is in `discard_staged_reels` / `discard_staged_record`: when
# the master timeline is named, the pool is filed after the discard, so
# a refused build's imports land in `06 - Subtitle renders/Not placed on
# any timeline` instead of staying in the current bin. The gate still
# refuses - only the debris handling changes.
#
# This test drives `rebuild_reels_in_project` with a placer that stages
# a timeline AND imports a caption clip into the current bin, then fails
# the gate. It asserts the stray is filed, the staging is gone and the
# approved reel is untouched. Against the pre-fix code the stray stays
# in `Reels/Current plan` and the filing assertion fails.

TARGET_2 = "Reel 05 - the-audit-that-was-eye-opening"
STAGING = TARGET_2 + STAGING_SUFFIX


@pytest.fixture
def mock_dvr_2(stub_resolve_script, monkeypatch):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    install_measured_draw_gain_probe(monkeypatch)
    yield


@pytest.fixture
def project_dir(tmp_path):
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


def _moment_2():
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 5
    moment.timeline_name = TARGET_2
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _project():
    """Master and the approved reel, ImportMedia aimed at Current plan."""
    from library.tools import resolve_bin_layout as bins
    project = FakeProject("Mock Project", [MASTER, TARGET_2])
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    reels = pool.AddSubFolder(root, bins.REELS_BIN)
    current = pool.AddSubFolder(reels, bins.REEL_STATE_BINS["current"])
    subs = pool.AddSubFolder(root, bins.SUBTITLES_BIN)
    unplaced = pool.AddSubFolder(subs, bins.UNPLACED_BIN)
    pool.SetCurrentFolder(current)
    return project, current, unplaced


@pytest.mark.usefixtures("mock_dvr_2")
def test_refused_build_files_its_caption_imports(project_dir):
    """The defect: ImportMedia debris left in the current bin."""
    resolve_project, current, unplaced = _project()
    pool = resolve_project.GetMediaPool()

    stray_path = str(project_dir / "pipeline_output" / "steps"
                      / "4_05_render_subtitles" / "sub_reel-05-a.mov")

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name == STAGING, f"expected staging, got {name!r}"
        pool.CreateEmptyTimeline(name)
        # The caption import the real placer does - lands in CURRENT.
        pool.ImportMedia([stray_path])
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    def _gate_failed_report():
        path = (project_dir / "pipeline_output" / "review"
                / "conformance_report.json")
        path.write_text(json.dumps({
            "has_errors": True,
            "findings": [
                {"severity": "error", "finding_class": "F17",
                 "message": "mixed-speaker card"},
                {"severity": "error", "finding_class": "F8",
                 "message": "end cuts mid-word"},
            ],
            # The rows name the STAGED container with its error
            # count - the shape the real gate writes - so the
            # refusal attributes to the reel that failed.
            "reels": [
                {"reel_name": STAGING,
                 "reel_number": 5,
                 "errors": 2,
                 "warnings": 0,
                 "captions": "0/0",
                 "findings": [
                     {"finding_class": "F17", "severity": "error"},
                     {"finding_class": "F8", "severity": "error"},
                 ]},
            ],
        }), encoding="utf-8")

    _gate_failed_report()

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment_2()]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=1):
        with pytest.raises(RuntimeError, match="defective timeline"):
            rebuild_reels_in_project(str(project_dir), only=[5])

    names = resolve_project.names()
    assert TARGET_2 in names, "the approved reel must survive a refusal"
    assert STAGING not in names, "the refused staging must be gone"
    assert sorted(names) == sorted([MASTER, TARGET_2])

    # The debris is what this test is about: the imported caption clip
    # must be filed under the canonical unplaced bin, not left in the
    # current bin.
    current_names = [c.GetName() for c in current.GetClipList()]
    unplaced_names = [c.GetName() for c in unplaced.GetClipList()]
    assert "sub_reel-05-a.mov" not in current_names, (
        "a refused build stranded its caption import in the current bin")
    assert "sub_reel-05-a.mov" in unplaced_names, (
        "a refused build must file its caption imports under "
        "'Not placed on any timeline' once no timeline places them")


@pytest.mark.usefixtures("mock_dvr_2")
def test_fallback_draw_gain_refuses_before_staging(project_dir, monkeypatch):
    """An unavailable renderer measurement must stop the build before
    it places overlays using the fallback gain."""
    from library.tools import draw_gain_probe

    resolve_project, _, _ = _project()
    monkeypatch.setattr(
        draw_gain_probe, "calibrate",
        lambda *args, **kwargs: {
            "gain": 2.0,
            "source": "fallback",
            "disagrees_with_fallback": False,
            "warnings": ["gallery declined"],
            "probe": {},
        })

    with patch("library.tools.reel_build.build_reel_timeline") as place, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment_2()]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"):
        with pytest.raises(ReelBuildError,
                           match="draw-gain probe could not calibrate") as exc:
            rebuild_reels_in_project(str(project_dir), only=[5])

    assert "gallery declined" in str(exc.value)
    place.assert_not_called()
    assert sorted(resolve_project.names()) == sorted([MASTER, TARGET_2])


# --------------------------------------------------------------------------
# From test_reel_build_touches_only_its_own_timelines.py
#
# A build may only delete what it is actually rebuilding.
#
# The defect this pins
# --------------------
# `rebuild_reels_in_project` collected every timeline whose name began
# `"Reel "` and called `DeleteTimelines` on all of them, unconditionally,
# before placing anything.  On the field-test project that is nineteen
# approved timelines destroyed in order to write nineteen - and building
# ONE reel destroyed the other eighteen.  Nothing backed them up and
# nothing said so.  The captain, 2026-09-06: *"a refusal is cheap and a
# deleted timeline is not."*
#
# The other half of the pair was already right: `write_provenance` has
# MERGED rather than replaced since #568, because *"a partial rebuild must
# not delete the provenance of the reels it did not touch"*.  A partial
# rebuild could not happen, because the delete loop ran first and took
# everything.
#
# Both directions, because a guard that cannot fire is not a guard
# ----------------------------------------------------------------
# `assert_deletion_scope` is asked of the LIST ABOUT TO BE DELETED rather
# than recomputing the selection, so it can catch the selection being
# wrong.  These tests call it directly with a permitted list and with a
# refused one, and drive a whole build with an over-collecting selection
# to prove it fires where it is actually wired - not merely where it is
# defined.
#
# The survivors are asserted by NAME.  The fake media pool really removes
# what it is handed, so "the other eighteen are still there" is read off
# the project afterwards rather than inferred from a mock call.

"""Nineteen, the field-test project's own shape - the number that makes
the old loop's blast radius the point rather than a detail."""


@pytest.fixture
def mock_dvr_3(stub_resolve_script, monkeypatch):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    install_measured_draw_gain_probe(monkeypatch)
    yield


def _only_probe_and_backups_deleted(project, backups=()):
    """The build-time draw-gain probe creates and deletes its own
    scratch timeline (`draw_gain_probe.PROBE_TIMELINE_NAME`) on every
    build - that pair is the probe cleaning up after itself - and
    promotion deletes exactly the backups it replaced
    (`reel_retirement.delete_backups`). What these tests guard is that
    nothing ELSE is ever deleted."""
    from library.tools.draw_gain_probe import PROBE_TIMELINE_NAME

    allowed = {PROBE_TIMELINE_NAME} | set(backups or ())
    assert project.deleted, "expected the probe's own create-and-delete pair"
    for name in project.deleted:
        assert name in allowed, (
            f"the build deleted {name!r} - only the probe's scratch "
            f"timeline and the replaced backups may be deleted")
    for name in backups or ():
        assert name in project.deleted, (
            f"the replaced backup {name!r} was not deleted")


def _only_probe_deleted(project):
    """No reel was replaced, so no backup exists: only the probe's own
    scratch timeline may have been deleted."""
    _only_probe_and_backups_deleted(project, [])


def _run(resolve_project, project_dir, **kwargs):
    """Drive the ONE build path with Resolve, the verifier and the placer faked."""
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place) as placed, \
            patch("library.tools.reel_build.reel_subtitle_segments") as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_replace_guard.full_timeline_snapshot",
                  side_effect=lambda timeline, _project, _folder=None: {
                      "timeline": {"name": timeline.GetName(),
                                   "unique_id": str(timeline.GetUniqueId()),
                                   "settings": {}, "start_frame": 0,
                                   "end_frame": 0},
                      "items": [], "markers": []}), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0):
        caps.return_value = [{"overlay_path": "x.mov"}]
        # No media pool on the stand-in project, so filing is
        # exercised in tests/unit/resolve/test_resolve_organization.py instead.
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed, caps


# ── The question the captain asked ───────────────────────────────────

@pytest.mark.usefixtures("mock_dvr_3")
def test_building_one_reel_leaves_the_other_eighteen_present(project):
    """The whole defect, stated as the survivors.

    Reel 03 is legitimately replaced - it is the one being rebuilt, and
    promotion retires its original to a backup and moves the passing
    staging onto its name. The other EIGHTEEN and the master must still
    be there, and no staging or backup container may be left behind.
    Under the loop this replaces, this assertion reads
    `['GEO Podcast - Synced']`.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _run(resolve_project, project, only=[3])

    survivors = resolve_project.names()
    untouched = [name for name in APPROVED if name != "Reel 03 - moment-3"]
    assert MASTER in survivors
    for name in untouched:
        assert name in survivors, f"{name} was deleted by a build of reel 3"
    assert len(untouched) == 18
    # The one reel this build replaced leaves no second generation:
    # its backup is deleted by default, nothing is archived
    # (`library/tools/reel_retirement.py`).
    assert sorted(survivors) == sorted([MASTER] + APPROVED)
    _only_probe_and_backups_deleted(
        resolve_project,
        ["Reel 03 - moment-3 (pre-rebuild backup)"])
    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert record["staged_timelines"] == {}
    assert placed.call_count == 1
    assert placed.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3" + STAGING_SUFFIX)


# ── The guard, both directions ───────────────────────────────────────


@pytest.mark.usefixtures("mock_dvr_3")
def test_the_guard_refuses_a_timeline_that_was_never_planned():
    with pytest.raises(ReelBuildError) as refused:
        assert_deletion_scope(
            [FakeTimeline("Reel 03 - moment-3"), FakeTimeline("Reel 07 - other")],
            {"Reel 03 - moment-3"})
    message = str(refused.value)
    assert "Reel 07 - other" in message
    assert "REFUSING to build" in message
    assert "Reel 03 - moment-3" in message


@pytest.mark.usefixtures("mock_dvr_3")
def test_the_guard_is_wired_where_the_deletion_happens(project):
    """Fires on the REAL path, not merely where it is defined.

    The selection is made to over-collect - the shape the old loop had -
    and the build must refuse before anything is deleted or placed.
    Nothing is deleted before the gate passes any more, so the refusal
    now fires at the staging check: a debris container is never reused
    as this run's staging, whatever produced the list.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)
    everything = [t for t in resolve_project.timelines
                  if t.GetName().startswith("Reel ")]

    with patch("library.tools.reel_build.timelines_to_replace",
               return_value=everything):
        with pytest.raises(ReelBuildError, match="REFUSING to build"):
            _run(resolve_project, project, only=[3])

    assert resolve_project.names() == [MASTER] + APPROVED
    _only_probe_deleted(resolve_project)


@pytest.mark.usefixtures("mock_dvr_3")
def test_the_selection_matches_a_name_exactly_never_by_prefix():
    resolve_project = FakeProject(
        [MASTER, "Reel 03 - moment-3", "Reel 03 - moment-3 (old)",
         "Reel 30 - moment-30"])

    found = timelines_to_replace(resolve_project, {"Reel 03 - moment-3"})

    assert [t.GetName() for t in found] == ["Reel 03 - moment-3"]


# ── What the build was ASKED for ─────────────────────────────────────

@pytest.mark.usefixtures("mock_dvr_3")
def test_only_refuses_a_reel_the_plan_has_not_approved(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    with pytest.raises(ReelBuildError, match=r"build reel\(s\) \[77\]"):
        _run(resolve_project, project, only=[77])

    assert resolve_project.names() == [MASTER] + APPROVED
    # A refused build probes nothing: the probe runs after the plan
    # validation, so a build that never starts never touches Resolve.
    assert resolve_project.deleted == []


# ── What `only` accepts, and what it refuses ─────────────────────────


@pytest.mark.usefixtures("mock_dvr_3")
def test_reel_numbers_refuses_a_name_rather_than_guessing_a_moment():
    from library.tools.reel_build import reel_numbers

    with pytest.raises(ReelBuildError, match="must be reel NUMBERS"):
        reel_numbers(["Reel 03 - moment-3"])


# --------------------------------------------------------------------------
# From test_verify_refusal_scopes_discard.py
#
# A verify-refusing reel must not take its batch siblings down with it.
#
# The defect, measured 2026-09-20 on lane `dup-takes-rebuild-4`: nine
# placements to promote three reels - four refusals each discarded the
# clean siblings, and the lane closed three notes in six hours against
# `caption-text-wave`'s thirteen in seventy minutes on the same shape
# of work. Three coupled defects:
#
# 1. The `verify_reels` node's `except Exception` discarded ALL
#    `staged.values()` - not the reel that failed.
# 2. The in-process gate in `rebuild_reels_in_project` discarded all
#    `built_reel_names` on any gate refusal.
# 3. `staging_holds.json` takes and releases were unlocked
#    read-modify-write, so interleaved writers silently dropped holds.
#
# The fix: `verify_built_reels` raises `ReelVerificationRefused`
# carrying the refused staging names read off the report rows the gate
# wrote, and both discard paths remove exactly those - a refusal that
# names none, and any failure where the gate never graded, discards
# NOTHING. Holds take/release run under an exclusive file lock.
#
# These tests drive the real build and the real node against a fake
# Resolve project whose pool really deletes, and assert on the
# siblings' stagings EXISTING - not on a count - with their holds
# held, while the raise still names the failed reel.

TARGET_A = "Reel 03 - moment-3"
TARGET_B = "Reel 04 - moment-4"
STAGED_A = TARGET_A + STAGING_SUFFIX
STAGED_B = TARGET_B + STAGING_SUFFIX


def _write_gate_report(project_dir, rows):
    """The report shape the real gate writes: per-reel rows naming
    the GRADED (staging) containers with their error counts."""
    path = (project_dir / "pipeline_output" / "review"
            / "conformance_report.json")
    path.write_text(json.dumps({
        "has_errors": any(errors for _, _, errors in rows),
        "reels": [
            {"reel_name": name,
             "reel_number": number,
             "errors": errors,
             "warnings": 0,
             "captions": "0/0",
             "findings": [{"finding_class": "F8",
                           "severity": "error"}] if errors else []}
            for name, number, errors in rows
        ],
    }), encoding="utf-8")


def _drive_build(resolve_project, project_dir, rows, **kwargs):
    """Drive the real in-process build+gate over two reels.

    The placer really stages both containers; the gate stub writes
    the given per-reel report rows and returns 1 (refused)."""
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    def _gate(**_gate_kwargs):
        _write_gate_report(project_dir, rows)
        return 1

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(3, TARGET_A),
                                _moment(4, TARGET_B)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  side_effect=_gate):
        kwargs.setdefault("organise", False)
        return rebuild_reels_in_project(
            str(project_dir), only=[3, 4], **kwargs)


# --------------------------------- the in-process gate: blast radius one


@pytest.mark.usefixtures("mock_dvr")
def test_one_refusing_reel_leaves_its_sibling_staged_and_held(project_dir):
    """The batch case from the lane's own log: four reels in, one
    refuses, the clean siblings' verified stagings AND their holds
    must survive - while the raise still names the failed reel."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    with pytest.raises(ReelVerificationRefused) as refused:
        _drive_build(resolve_project, project_dir,
                     [(STAGED_A, 3, 1), (STAGED_B, 4, 0)])

    message = str(refused.value)
    assert STAGED_A in message, (
        "the raise must name the reel that refused")
    assert "untouched by this refusal" in message

    # Asserted on EXISTING, not on a count: the sibling's staging is
    # still a timeline in the project and still under hold.
    assert STAGED_B in resolve_project.names(), (
        "the clean sibling's staging was discarded with the refusal")
    assert STAGED_A not in resolve_project.names(), (
        "the refused staging must be gone")
    assert TARGET_A in resolve_project.names()
    assert TARGET_B in resolve_project.names(), (
        "the approved reels were never named and must all survive")

    held = holds.held_names(str(project_dir))
    assert STAGED_B in held, (
        "the clean sibling's hold went with the refusal - a future "
        "prune is now unprotected against a live staging")
    assert STAGED_A not in held


@pytest.mark.usefixtures("mock_dvr")
def test_an_unattributed_refusal_discards_nothing(project_dir):
    """The fail-closed direction: the gate failed but no report row
    carries error findings, so the refusal names no reel - and no
    staging may go. The stagings stay with their holds until an
    operator reads the report and discards deliberately."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    with pytest.raises(ReelVerificationRefused, match="NO staging"):
        _drive_build(resolve_project, project_dir,
                     [(STAGED_A, 3, 0), (STAGED_B, 4, 0)])

    assert STAGED_A in resolve_project.names()
    assert STAGED_B in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_A in held and STAGED_B in held


@pytest.mark.usefixtures("mock_dvr")
def test_an_all_passing_batch_is_unchanged(project_dir):
    """The other direction: a batch where every reel passes promotes
    both, leaves no staging container and no hold behind."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    def _gate(**_gate_kwargs):
        _write_gate_report(project_dir, [(STAGED_A, 3, 0),
                                         (STAGED_B, 4, 0)])
        return 0

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(3, TARGET_A),
                                _moment(4, TARGET_B)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  side_effect=_gate):
        record = rebuild_reels_in_project(
            str(project_dir), organise=False, only=[3, 4])

    assert sorted(record["timelines_built"]) == sorted([TARGET_A, TARGET_B])
    assert sorted(resolve_project.names()) == sorted(
        [MASTER, TARGET_A, TARGET_B])
    assert holds.held_names(str(project_dir)) == set()


# ------------------------- the verify_reels node: blast radius one


def _verify_step_module():
    from library.tools.operations import load_step_module

    return load_step_module("step_7_02_verify_reels", "step.py")


@contextmanager
def _noop_lease(*_args, **_kwargs):
    yield


def _drive_node(project_dir, resolve_project, gate_effect):
    """Drive the real node with the real discard underneath.

    Only the Resolve connection and the lease are stubbed - the
    discard that runs deletes real fake timelines and releases real
    holds, so "the sibling still exists" is read off the project."""
    module = _verify_step_module()
    with patch("library.tools.reel_build.verify_built_reels",
               side_effect=gate_effect), \
            patch("library.tools.reel_build._connect_resolve_project",
                  return_value=resolve_project), \
            patch("library.tools.reel_build.resolve_lease",
                  side_effect=_noop_lease), \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value=str(
                      project_dir / "pipeline_output" / "scratch"
                      / "timeline_transcript" / "transcript.json")):
        return module.verify_reels({
            "project_folder": str(project_dir),
            "reel_build": {
                "timelines_built": [STAGED_A, STAGED_B],
                "staged_timelines": {TARGET_A: STAGED_A,
                                     TARGET_B: STAGED_B},
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": str(project_dir / "plan.json"),
            },
            "timeline_transcript": {"segments": []},
        })


@pytest.mark.usefixtures("mock_dvr")
def test_the_node_discards_only_the_reel_the_gate_named(project_dir):
    """The node's half of the lane log: the gate refuses reel A, the
    node discards A's staging through the REAL discard - and B's
    staging still exists, still held."""
    resolve_project = FakeProject(
        [MASTER, TARGET_A, TARGET_B, STAGED_A, STAGED_B])
    holds.take_hold(str(project_dir), STAGED_A, awaiting=TARGET_A,
                    taken_by="test")
    holds.take_hold(str(project_dir), STAGED_B, awaiting=TARGET_B,
                    taken_by="test")

    with pytest.raises(ReelVerificationRefused) as refused:
        _drive_node(project_dir, resolve_project,
                    ReelVerificationRefused(
                        f"F8 end cuts mid-word on {STAGED_A}",
                        failed_reels=[STAGED_A]))

    assert STAGED_A in str(refused.value)
    assert STAGED_B in resolve_project.names(), (
        "the clean sibling's staging was discarded with the refusal")
    assert STAGED_A not in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_B in held
    assert STAGED_A not in held


@pytest.mark.usefixtures("mock_dvr")
def test_the_node_discards_nothing_when_the_gate_never_graded(project_dir):
    """A connect failure is not a verdict: both stagings stay, both
    holds stay, and the failure propagates naming that."""
    resolve_project = FakeProject(
        [MASTER, TARGET_A, TARGET_B, STAGED_A, STAGED_B])
    holds.take_hold(str(project_dir), STAGED_A, awaiting=TARGET_A,
                    taken_by="test")
    holds.take_hold(str(project_dir), STAGED_B, awaiting=TARGET_B,
                    taken_by="test")

    with pytest.raises(RuntimeError, match="Resolve connection refused"):
        _drive_node(project_dir, resolve_project,
                    RuntimeError("Resolve connection refused"))

    assert STAGED_A in resolve_project.names()
    assert STAGED_B in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_A in held and STAGED_B in held


@pytest.mark.usefixtures("mock_dvr")
def test_scoped_verification_promotes_its_reel_and_keeps_an_unrelated_hold(
        project_dir):
    """A stale Reel 11 identity must not block Reel 03's real promotion.

    The conformance read is stubbed, but the verify node and promotion run
    against fake Resolve timelines. Reel 11's staging and hold represent an
    unrelated pending promotion and must survive untouched.
    """
    from library.processes.reels import run_reels
    from library.tools import capability_outputs
    from tests.promotion_test_helpers import no_a_roll_track_plans

    module = _verify_step_module()
    final_a = "Reel 03 - moment-3"
    final_b = "Reel 11 - held"
    staged_a = final_a + STAGING_SUFFIX
    staged_b = final_b + STAGING_SUFFIX
    resolve_project = FakeProject(
        [MASTER, final_a, final_b, staged_a, staged_b])
    timeline_ids = {
        staged_a: next(t for t in resolve_project.timelines
                       if t.GetName() == staged_a).GetUniqueId(),
        staged_b: next(t for t in resolve_project.timelines
                       if t.GetName() == staged_b).GetUniqueId(),
    }
    holds.take_hold(str(project_dir), staged_a, awaiting=final_a,
                    taken_by="test")
    holds.take_hold(str(project_dir), staged_b, awaiting=final_b,
                    taken_by="test")
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json")\
        .write_text(json.dumps({"built_reels": [staged_a, staged_b]}),
                    encoding="utf-8")
    shared = {
        "resolve_project_name": "Mock Project",
        "master_timeline_name": MASTER,
        "plan_path": str(project_dir / "plan.json"),
    }
    run_reels.record_project_output(
        str(project_dir), "reel.build",
        {"reel_build": {
            **shared,
            "timelines_built": [staged_b],
            "staged_timelines": {final_b: staged_b},
            "staged_timeline_ids": {staged_b: timeline_ids[staged_b]},
            "track_plans": no_a_roll_track_plans(
                {final_b: staged_b}),
        }})
    run_reels.record_project_output(
        str(project_dir), "reel.build",
        {"reel_build": {
            **shared,
            "timelines_built": [staged_a],
            "staged_timelines": {final_a: staged_a},
            "staged_timeline_ids": {staged_a: timeline_ids[staged_a]},
            "track_plans": no_a_roll_track_plans(
                {final_a: staged_a}),
        }},
        only_reels=[3],
    )
    state = json.loads((project_dir / "pipeline_data.json").read_text(
        encoding="utf-8"))
    data = {
        "project_folder": str(project_dir),
        "only_reels": [3],
        "timeline_transcript": {"segments": []},
        "reel_build": capability_outputs.node_output(
            state, "build_reels")["reel_build"],
    }

    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build._connect_resolve_project",
                  return_value=resolve_project), \
            patch("library.tools.reel_build._record_reel_versions",
                  return_value=[]), \
            patch("library.tools.reel_build.sweep_all_reels_informational"), \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value=str(project_dir / "transcript.json")):
        record = module.verify_reels(data)

    assert gate.call_args.kwargs["only_reels"] == [staged_a]
    assert record["reel_verification"]["timelines_verified"] == [final_a]
    assert final_a in resolve_project.names()
    assert staged_a not in resolve_project.names()
    assert staged_b in resolve_project.names()
    assert holds.held_names(str(project_dir)) == {staged_b}


@pytest.mark.usefixtures("mock_dvr")
def test_verified_suffix_build_promotes_to_its_approved_base_reel(project_dir):
    """The DAG grades the suffix staging, then promotes it to the plan name."""
    from library.tools.reel_proposal import (
        Approval, ReelMoment, proposal_path, write_proposal)
    from tests.promotion_test_helpers import no_a_roll_track_plans

    suffix = " (whole-take rebuild)"
    final = "Reel 03 - moment-3"
    scratch_final = final + suffix
    staged = scratch_final + STAGING_SUFFIX
    plan_path = proposal_path(project_dir)
    moment = ReelMoment(
        number=3, slug="moment-3", reason="verified edit",
        timeline_start=10.0, timeline_end=40.0,
        approval=Approval.APPROVED)
    write_proposal(plan_path, [moment], {"derived_from": {}})

    resolve_project = FakeProject([MASTER, final, staged])
    staged_id = next(
        timeline.GetUniqueId() for timeline in resolve_project.timelines
        if timeline.GetName() == staged)
    holds.take_hold(str(project_dir), staged, awaiting=final,
                    taken_by="test")
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json")\
        .write_text(json.dumps({"built_reels": [staged]}), encoding="utf-8")
    module = _verify_step_module()

    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build._connect_resolve_project",
                  return_value=resolve_project), \
            patch("library.tools.reel_build.sweep_all_reels_informational"), \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value=str(project_dir / "transcript.json")):
        result = module.verify_reels({
            "project_folder": str(project_dir),
            "only_reels": [3],
            "timeline_transcript": {"segments": []},
            "reel_build": {
                "timelines_built": [staged],
                "staged_timelines": {scratch_final: staged},
                "staged_timeline_ids": {staged: staged_id},
                "track_plans": no_a_roll_track_plans(
                    {scratch_final: staged}),
                "name_suffix": suffix,
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": str(plan_path),
            },
        })

    assert gate.call_args.kwargs["only_reels"] == [staged]
    assert result["reel_verification"]["timelines_verified"] == [final]
    assert final in resolve_project.names()
    assert staged not in resolve_project.names()
    assert scratch_final not in resolve_project.names()
    assert holds.held_names(str(project_dir)) == set()


@pytest.mark.usefixtures("mock_dvr")
def test_the_pending_report_reconciles_a_deleted_staging_as_stale(
        project_dir, capsys):
    """A hold whose timeline is gone reads STALE, not pending.

    The verify node's pending report is the last word before promotion.
    Unreconciled, it read a deleted staging as awaiting a decision the
    captain cannot make - the same defect `staging_holds` fixed for the
    build's own census, unrepaired in this consumer.
    """
    from library.tools.reel_proposal import (
        Approval, ReelMoment, proposal_path, write_proposal)
    from tests.promotion_test_helpers import no_a_roll_track_plans

    suffix = " (whole-take rebuild)"
    final = "Reel 03 - moment-3"
    scratch_final = final + suffix
    staged = scratch_final + STAGING_SUFFIX
    ghost = "Reel 09 - gone (rebuild staging)"
    live = "Reel 11 - held (rebuild staging)"

    plan_path = proposal_path(project_dir)
    moment = ReelMoment(
        number=3, slug="moment-3", reason="verified edit",
        timeline_start=10.0, timeline_end=40.0,
        approval=Approval.APPROVED)
    write_proposal(plan_path, [moment], {"derived_from": {}})

    resolve_project = FakeProject([MASTER, final, staged, live])
    staged_id = next(
        timeline.GetUniqueId() for timeline in resolve_project.timelines
        if timeline.GetName() == staged)
    holds.take_hold(str(project_dir), staged, awaiting=final,
                    taken_by="test")
    holds.take_hold(str(project_dir), ghost, awaiting="Reel 09 - gone",
                    taken_by="test")
    holds.take_hold(str(project_dir), live, awaiting="Reel 11 - held",
                    taken_by="test")
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json")\
        .write_text(json.dumps({"built_reels": [staged]}), encoding="utf-8")
    module = _verify_step_module()

    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build._connect_resolve_project",
                  return_value=resolve_project), \
            patch("library.tools.reel_build.sweep_all_reels_informational"), \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value=str(project_dir / "transcript.json")), \
            patch.object(module, "_resolve_live_project",
                         return_value=resolve_project):
        module.verify_reels({
            "project_folder": str(project_dir),
            "only_reels": [3],
            "timeline_transcript": {"segments": []},
            "reel_build": {
                "timelines_built": [staged],
                "staged_timelines": {scratch_final: staged},
                "staged_timeline_ids": {staged: staged_id},
                "track_plans": no_a_roll_track_plans(
                    {scratch_final: staged}),
                "name_suffix": suffix,
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": str(plan_path),
            },
        })

    err = capsys.readouterr().err
    assert "STALE HOLDS: 1 hold(s)" in err
    assert ghost in err
    unpromoted, _, _stale = err.partition("STALE HOLDS")
    assert ghost not in unpromoted
    assert "UNPROMOTED STAGING: 1" in err
    assert live in unpromoted


# --------------------------------------------------------------------------
# From test_verify_scopes_to_built_reels.py
#
# A build grades what it placed, not the whole project.
#
# The defect this pins
# --------------------
# `rebuild_reels_in_project` learned to build ONE reel add-only
# (`only=[3]` deletes nothing it is not about to place), but its closing
# gate - `verify_built_reels` -> `run_verification` - still enumerated
# every `Reel *` timeline in the Resolve project. With 49 timelines
# present, building one reel graded 49; the authorised 19-reel rebuild
# would have graded 50, then 51, then 52 - quadratic, on the captain's
# own machine.
#
# Worse than slow: the untouched timelines are graded against the
# CURRENT plan, which describes different reels, so a clean single-reel
# build failed on findings from timelines it never touched - 94 of 121
# errors in `data/vep-rebuild-verify/report.md` in the firstmate home, 3.5,
# and 28
# `NO-REFERENCE` + `PLAN-MISMATCH` errors on previous batches' reels in
# `data/vep-harvest/report.md` in the firstmate home.
#
# Which side is wrong
# -------------------
# The CHECK, not the builder. The builder's own record already promises
# the scoped reading: `build_reels` returns `timelines_built`, and the
# `verify_reels` node returned `timelines_verified` from that same list
# while the code underneath graded everything. The record was the
# contract; the verifier was not keeping it. Nothing is loosened - the
# same findings fail when they are on a timeline the build placed, and
# an empty scope is REFUSED rather than passed.
#
# Both directions, per AGENTS.md 10.4: a full build still grades all it
# placed, and grading nothing never passes.

def _staging(final):
    return final + STAGING_SUFFIX


def _run_build(resolve_project, project_dir, **kwargs):
    """Drive the ONE build path with Resolve, the placer and captions faked.

    Returns the build record and the mocked verifier gate, so tests read
    what the gate was ASKED to grade rather than inferring it.
    """
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {
            "track_plan": {
                "video_tracks": [], "audio_tracks": [], "material": {},
            },
        }

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments") as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0) as gate:
        caps.return_value = [{"overlay_path": "x.mov"}]
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, gate


# ── The builder hands the gate its own scope ─────────────────────────

@pytest.mark.usefixtures("mock_dvr")
def test_a_partial_build_grades_only_the_timeline_it_placed(project):
    """Building reel 3 of 19 must ask the verifier for exactly one name -
    the staging container it placed, never the approved timeline.

    Before the fix the gate was called with no scope at all, so it
    graded all nineteen - the call the quadratic cost and the 94
    foreign errors both came from.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project, only=[3])

    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert record["staged_timelines"] == {}
    # Two verifications: the refusing gate and the informational sweep,
    # both scoped to this single-reel lane.
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging("Reel 03 - moment-3")]
    assert gate.call_args_list[1][1]["only_reels"] == [
        "Reel 03 - moment-3"]
    # Promoted: the final name is back and no staging is left
    # behind - and the timeline it replaced is DELETED by default, so
    # no archived generation stands beside it (`reel_retirement`).
    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED)


@pytest.mark.usefixtures("mock_dvr")
def test_a_full_build_grades_everything_it_placed(project):
    """The other direction: a full rebuild must not silently narrow.

    Scoping to the build's own output still grades all nineteen when
    nineteen were placed - the explicit sweep is preserved, now as a
    consequence of what was built rather than as a project-wide scan.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project)

    assert record["timelines_built"] == APPROVED
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging(name) for name in APPROVED]
    assert gate.call_args_list[1][1]["only_reels"] is None


@pytest.mark.usefixtures("mock_dvr")
def test_a_suffixed_rebuild_grades_scratch_then_promotes_to_base(project):
    """A suffixed scratch is graded before it replaces the base reel."""
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project, only=[3],
                               name_suffix=" (whole-take rebuild)")

    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging("Reel 03 - moment-3 (whole-take rebuild)")]
    assert gate.call_args_list[1][1]["only_reels"] == [
        "Reel 03 - moment-3"]
    assert "Reel 03 - moment-3" in resolve_project.names()
    assert "Reel 03 - moment-3 (whole-take rebuild)" not in \
        resolve_project.names()


# ── The verifier honours the scope ───────────────────────────────────

def _zeroed_result(name, number):
    from library.tools.reel_conformance_verifier import ReelResult

    return ReelResult(
        reel_name=name, reel_number=number, plan_seconds=0.0,
        plan_frames=0.0, actual_frames=0, items_expected=0,
        items_actual=0, one_frame_holes=0, big_holes=[],
        captions_expected=0, captions_actual=0, speech_seconds=0.0,
        uncaptioned_seconds=0.0, uncaptioned_pct=0.0, short_captions=0,
        edge_cuts=0, bad_take_cuts=0, markers=0, findings=[])


def _run_scoped_verification(names, only_reels):
    """Drive `run_verification` against a stand-in Resolve project.

    Master plus three reels; snapshots and the per-reel check are
    faked so the test measures SCOPE - which timelines were read and
    graded - rather than any finding class.
    """
    from library.tools.reel_conformance_verifier import (
        ReelTimeline, run_verification)

    master = FakeTimeline(names[0], settings={
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    })
    fake = FakeProject(
        [master] + [FakeTimeline(name) for name in names[1:]],
        current=master)

    FPS = 24000 / 1001

    def fake_snapshot(timeline, project_name):
        from library.tools.timeline_ingest import TimelineSnapshot

        return TimelineSnapshot(
            project_name=project_name,
            timeline_name=timeline.GetName(),
            fps=FPS, reported_fps=23.976,
            width=1080, height=1920,
            start_frame=0, end_frame=240, clips=())

    graded = []

    def fake_verify(plan, timeline, **kwargs):
        graded.append(plan.reel_name)
        number = 0
        for index, name in enumerate(names):
            if name == plan.reel_name:
                number = index
        return _zeroed_result(plan.reel_name, number)

    out = io.StringIO()
    with patch("library.tools.marker_feedback.connect_resolve") as connect, \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=fake), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=fake_snapshot) as snap_fn, \
            patch("library.tools.timeline_ingest.snapshot_to_dict",
                  side_effect=lambda snap: {
                      "timeline": snap.timeline_name}), \
            patch("library.tools.reel_conformance_verifier._snapshot_to_reel_timeline",
                  side_effect=lambda snap, **kwargs: ReelTimeline(
                      reel_name=snap.timeline_name, fps=FPS, total_frames=240,
                      video_items=(), audio_items=(), caption_items=())), \
            patch("library.tools.reel_conformance_verifier.verify_reel",
                  side_effect=fake_verify):
        connect.return_value = FakeResolve()
        code = run_verification(
            project_name="Mock Project",
            master_name=MASTER,
            transcript={"segments": []},
            only_reels=only_reels,
            out=out)
    return code, graded, snap_fn, out.getvalue()


@pytest.mark.usefixtures("mock_dvr")
def test_scoped_verification_reads_and_grades_one_timeline():
    """The cost pin: one scoped reel snapshots master + 1, not master + 3.

    Before the fix `run_verification` took no scope, so this call
    raised `TypeError` - and without the argument the same build read
    every timeline in the project.
    """
    names = [MASTER, "Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]

    code, graded, snap_fn, output = _run_scoped_verification(
        names, ["Reel 02 - b"])

    assert code == 0
    assert graded == ["Reel 02 - b"]
    # Master plus the one scoped reel, each snapshotted twice (the
    # read-only proof re-reads what was graded) - the snapshot count IS
    # the cost, and it no longer grows with the project's reel count.
    assert snap_fn.call_count == 4
    assert "Reel 02 - b" in output


@pytest.mark.usefixtures("mock_dvr")
def test_unscoped_verification_still_grades_everything():
    """`None` is the deliberate sweep: no scope grades every reel."""
    names = [MASTER, "Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]

    code, graded, snap_fn, _ = _run_scoped_verification(names, None)

    assert code == 0
    assert sorted(graded) == ["Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]
    assert snap_fn.call_count == 8


@pytest.mark.usefixtures("mock_dvr")
def test_a_scoped_name_matching_nothing_is_fatal_not_a_smaller_pass():
    """A misspelled scope that quietly graded the rest would be the gate
    that cannot fail wearing a filter. It returns 2 and grades nothing."""
    names = [MASTER, "Reel 01 - a", "Reel 02 - b"]

    code, graded, _, _ = _run_scoped_verification(
        names, ["Reel 09 - typo"])

    assert code == 2
    assert graded == []


# ── An empty scope never passes ──────────────────────────────────────

@pytest.mark.usefixtures("mock_dvr")
def test_verifying_zero_timelines_is_refused_not_passed():
    """`verify_built_reels` with an empty scope must raise rather than
    report success on nothing graded."""
    from library.tools.reel_build import verify_built_reels

    with pytest.raises(RuntimeError, match="zero reel timelines"):
        verify_built_reels(
            project_folder="/nonexistent",
            resolve_project_name="Mock Project",
            master_timeline_name=MASTER,
            plan_path="/nonexistent/plan.json",
            transcript_path="/nonexistent/transcript.json",
            only_reels=[])


@pytest.mark.usefixtures("mock_dvr")
def test_the_step_refuses_a_build_record_naming_nothing():
    """The `verify_reels` node with an empty `timelines_built` refuses
    instead of falling back to grading the whole project."""
    module = _verify_step_module()

    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        with pytest.raises(module.ReelVerifyRefused, match="no timelines_built"):
            module.verify_reels({
                "project_folder": "/tmp/project",
                "reel_build": {
                    "timelines_built": [],
                    "resolve_project_name": "Mock Project",
                    "master_timeline_name": MASTER,
                    "plan_path": "/tmp/project/plan.json",
                },
                "timeline_transcript": {"segments": []},
            })
        assert not gate.called


@pytest.mark.usefixtures("mock_dvr")
def test_the_step_forwards_the_build_record_as_its_scope():
    """The node's own return record already claimed `timelines_verified`
    from `timelines_built` - this pins the code to the same promise."""
    module = _verify_step_module()

    built = ["Reel 03 - moment-3"]
    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build.promote_staged_reels") as promote, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        record = module.verify_reels({
            "project_folder": "/tmp/project",
            "reel_build": {
                "timelines_built": built,
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": "/tmp/project/plan.json",
            },
            "timeline_transcript": {"segments": []},
        })

    assert gate.call_args[1]["only_reels"] == built
    assert not promote.called
    assert record["reel_verification"]["timelines_verified"] == built


@pytest.mark.usefixtures("mock_dvr")
def test_the_step_promotes_staged_timelines_only_after_the_gate_passes():
    """The DAG path: the build staged, this node grades the staging and
    promotes on a pass - returning the final names, not the staging."""
    module = _verify_step_module()

    final = "Reel 03 - moment-3"
    staged = _staging(final)
    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build.promote_staged_reels",
                  return_value={"promoted": [final],
                                "organised": None}) as promote, \
            patch("library.tools.reel_build."
                  "sweep_all_reels_informational") as sweep, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        record = module.verify_reels({
            "project_folder": "/tmp/project",
            "reel_build": {
                "timelines_built": [staged],
                "staged_timelines": {final: staged},
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": "/tmp/project/plan.json",
            },
            "timeline_transcript": {"segments": []},
        })

    assert gate.call_args[1]["only_reels"] == [staged]
    assert promote.call_args[0][1:4] == (
        "Mock Project", MASTER, {final: staged})
    assert record["reel_verification"]["timelines_verified"] == [final]
    # The whole-project sweep runs after promotion, on the promoted
    # project - and it is informational, so it is a separate call the
    # gate's scope never narrows.
    assert sweep.call_count == 1
    assert sweep.call_args[1]["project_folder"] == "/tmp/project"
    assert sweep.call_args[1]["plan_path"] == "/tmp/project/plan.json"


# --------------------------------------------------------------------------
# From test_verification_verdict.py
#
# The build's verification verdict is DERIVED from its QA stations, and a
# planned Fusion effect whose label was never placed is detected, not
# silently dropped. History of both defects: docs/evidence/build_verification.md.

def _report(station: str, passed: bool):
    """A minimal QA report stub with the .passed attribute the function reads."""
    return SimpleNamespace(station=station, passed=passed)


def test_the_verdict_is_derived_from_the_stations():
    """One failing station fails the build (the old hardcoded True passed
    it regardless); no evidence of failure is not a failure."""
    reports = [
        _report("clip_placement", True),
        _report("fusion_comps", False),
    ]
    assert derive_verification_verdict(reports) is False
    assert derive_verification_verdict([]) is True


# Representative pmk_default Fusion look parameters
_PMK_LOOK = {
    "grade_contrast": 0.10,
    "glow_gain": 0.12,
    "glow_threshold": 0.78,
    "glow_size": 3.5,
    "film_grain": True,
    "film_grain_power": 0.18,
    "film_grain_size": 1.5,
    "vignette": True,
    "vignette_blend": 0.16,
    "vignette_soft": 0.35,
    "vignette_color": [0.0, 0.0, 0.0],
}


def test_only_an_unplaced_label_is_a_dropped_effect():
    """The pass walks V1 and V2 (`library/tools/execution/fusion_tracks.py`),
    so placed B-roll is a normal hit; what is caught is a label that was
    never placed - and a build that placed nothing drops everything."""
    per_clip = {label: _PMK_LOOK for label in (
        "a_roll_0", "a_roll_1", "broll_1", "broll_4", "broll_8")}
    placed = {
        1: {"a_roll_0", "a_roll_1"},
        2: {"broll_1", "broll_4", "broll_8"},
    }
    assert detect_unreachable_fusion_effects(per_clip, placed) == []

    dropped = detect_unreachable_fusion_effects(
        {"a_roll_0": _PMK_LOOK, "broll_9": _PMK_LOOK},
        {1: {"a_roll_0"}, 2: {"broll_1"}})
    assert len(dropped) == 1
    assert dropped[0]["label"] == "broll_9"
    assert dropped[0]["track"] == "unplaced"
    assert dropped[0]["params"] == _PMK_LOOK
    assert dropped[0]["label"] in dropped[0]["detail"]

    dropped = detect_unreachable_fusion_effects({"a_roll_0": _PMK_LOOK}, {})
    assert [d["label"] for d in dropped] == ["a_roll_0"]


def test_format_error_message_names_clips_and_params():
    """The error message must be actionable: clip labels and parameters."""
    per_clip = {"phantom": {"glow_gain": 0.12, "film_grain": True}}
    dropped = detect_unreachable_fusion_effects(
        per_clip, {1: set(), 2: set()})

    msg = format_fusion_drop_error(dropped)
    assert "phantom" in msg
    assert "glow_gain" in msg
    assert "cannot reach" in msg
