"""The reference rung of the decided-value ladder.

A project declares a reference video (video.yaml `reference`); a slot
that declares a ``reference_measurement`` reads its style from that
video's own pixels.  The precedence these tests pin: the project's
explicit preference outranks the reference's measurement, which
outranks the user's global taste profile - and an absent reference,
a slot with no declaration, or a reference nobody can find is ABSENT,
never filled in with a default (AGENTS.md 10.5).
"""
from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import decided_value as DV  # noqa: E402
from library.tools import reference_style as RS  # noqa: E402

HAS_FFMPEG = (shutil.which("ffmpeg") is not None
              and shutil.which("ffprobe") is not None)

SLOT = "pacing.target_asl_seconds"
MEASURED_ASL = 1.7


@pytest.fixture
def _isolate_user_taste_profile(tmp_path, monkeypatch):
    """A local user's profile must not change the ladder's unit tests."""
    config = tmp_path / "user-config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))


@pytest.fixture
def _measured_reference(monkeypatch):
    """The reference's own ASL, without encoding a video per test.

    The measurement itself is pinned by
    tests/unit/picture/test_reference_style.py; what this rung owns is
    the WIRING - that the declared reference reaches the slot and the
    source is recorded beside the value.
    """
    monkeypatch.setattr(RS, "measure_reference_asl",
                        lambda path: MEASURED_ASL)
    monkeypatch.setattr(RS, "reference_digest", lambda path: "abc123")
    return MEASURED_ASL


def _project_with_reference(tmp_path, reference):
    """A project declaring `reference`, with the file present.

    The measure itself is monkeypatched; the file only has to exist,
    because a reference nobody can find is absent, not an error -
    pinned separately below.
    """
    reference_file = tmp_path / reference
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_text("reference", encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        textwrap.dedent(f"""\
        reference: {reference}
        """), encoding="utf-8")
    return str(tmp_path)


# ── The rung ──────────────────────────────────────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile",
                         "_measured_reference")
class TestTheReferenceRung:

    def test_a_declared_reference_measures_into_the_slot(self, tmp_path):
        """The project's reference is a statement the slot can read.

        The failure it pins: a reference the project declares but no
        slot reads is decoration - the style never reaches the edit.
        """
        folder = _project_with_reference(tmp_path, "references/punchy.mp4")
        value, source = DV.stated_preference(SLOT, folder)
        assert value == MEASURED_ASL
        assert "references/punchy.mp4" in source

    def test_the_project_s_own_preference_outranks_the_reference(
            self, tmp_path):
        """An explicit preference is the project's own voice.

        The failure it pins: letting a reference's measurement
        override a preference the project states would make the
        reference the chooser (AGENTS.md 10.5).
        """
        (tmp_path / "project.yaml").write_text(
            "pipeline:\n"
            "  creative_preferences:\n"
            "    pacing:\n"
            "      target_asl_seconds: 3.5\n", encoding="utf-8")
        folder = _project_with_reference(tmp_path, "references/punchy.mp4")
        value, source = DV.stated_preference(SLOT, folder)
        assert value == 3.5
        assert "creative_preferences" in source

    def test_a_slot_with_no_declaration_is_absent_even_with_a_reference(
            self, tmp_path):
        """A reference reaches only the slots that declare a measure.

        The failure it pins: measuring a reference into a slot that
        declares no measurement would let one project declaration
        silently set values nobody asked it to set.
        """
        folder = _project_with_reference(tmp_path, "references/punchy.mp4")
        assert DV.slot("mix.speech_above_bed_db").reference_measurement \
            is None
        assert DV.stated_preference("mix.speech_above_bed_db", folder,
                                    scope="background") == (None, "")

    def test_a_reference_nobody_can_find_is_absent_not_an_error(
            self, tmp_path):
        """A declared reference that does not exist is not a value.

        The failure it pins: a missing file falling through to a
        default would ship a style the project never declared.
        """
        (tmp_path / "video.yaml").write_text(
            "reference: references/gone.mp4\n", encoding="utf-8")
        assert DV.stated_preference(SLOT, str(tmp_path)) == (None, "")

    def test_a_project_with_no_reference_falls_through(self, tmp_path):
        """No reference declared is not a reference measured.

        The failure it pins: a project with no reference getting a
        value from this rung would mean the engine invented a style.
        """
        assert DV.stated_preference(SLOT, str(tmp_path)) == (None, "")

    def test_a_non_numeric_measurement_is_refused(self, tmp_path,
                                                  monkeypatch):
        """A measure that returns no number decides nothing.

        The failure it pins: a measurement function returning a
        string or None would put a non-number in a slot whose units
        are seconds.
        """
        monkeypatch.setattr(RS, "measure_reference_asl",
                            lambda path: "fast")
        folder = _project_with_reference(tmp_path, "references/punchy.mp4")
        assert DV.stated_preference(SLOT, folder) == (None, "")

    def test_the_decision_record_names_the_reference_as_its_source(
            self, tmp_path):
        """A reader must tell a reference's pixels from a typed number.

        The failure it pins: a measured value whose source is not
        recorded cannot be reviewed - the captain cannot see that the
        pacing came from the reference's own cut pattern.
        """
        folder = _project_with_reference(tmp_path, "references/punchy.mp4")
        decision = DV.decide(SLOT, project_folder=folder,
                             model_answer={"value": 9.0, "why": "x"})
        assert decision.basis == DV.STATED
        assert decision.value == MEASURED_ASL
        assert "references/punchy.mp4" in decision.source


# ── The wiring: the representation reaches the step ────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_declared_reference_reaches_the_step_context(tmp_path):
    """The measured representation lands in state and projects.

    This test encodes media because the wiring only fires when a real
    reference video exists and is measurable - a declared path with no
    file is absent, which a separate test above pins.  The failure it
    pins: a representation measured but never landed, or landed but
    not projected into the step's context, is a style the run computed
    and nobody read.
    """
    import json
    import subprocess
    from library.tools.context_projector import project_fields
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        "reference: reference.mp4\n", encoding="utf-8")
    from library.processes.edit_video.run_pipeline import load_pipeline_state
    state = load_pipeline_state(str(tmp_path))
    assert "reference_style" in state
    style = state["reference_style"]
    assert style["shot_length_distribution"]["shot_count"] == 2
    assert len(style["palette"]["colours"]) == 2
    manifest = json.load(open(
        REPO / "library" / "steps" / "step_2_01_creative_direction"
        / "manifest.json"))
    context = project_fields(state, manifest["context_fields"])
    assert context["reference_style"]["pacing"]["cuts_per_minute"] == 30.0


def test_a_project_with_no_reference_lands_no_style(tmp_path):
    """No reference declared is not a style measured.

    The failure it pins: a run measuring a style the project never
    declared would invent constraints the captain never stated.
    """
    from library.processes.edit_video.run_pipeline import load_pipeline_state
    state = load_pipeline_state(str(tmp_path))
    assert "reference_style" not in state


# ── The review fix: measure once, reuse, and say when unmeasured ─────

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_second_run_with_an_unchanged_reference_does_not_measure_again(
        tmp_path, monkeypatch):
    """The stored measurement is reused; the reference is not decoded twice.

    The failure it pins: a run whose reference is unchanged decoding it
    again - once for the step context and again for every slot that
    reads it - pays an ffmpeg pass per run for a style that cannot
    have changed.  The stored value is keyed by the reference file's
    content digest, so an unchanged reference costs no decode.
    """
    import subprocess
    from library.processes.edit_video.run_pipeline import (
        load_pipeline_state, save_pipeline_state)
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        "reference: reference.mp4\n", encoding="utf-8")
    state = load_pipeline_state(str(tmp_path))
    assert "reference_style" in state
    assert "reference_style_digest" in state
    save_pipeline_state(str(tmp_path), state)
    monkeypatch.setattr(RS, "measure_reference_style",
                        lambda path: (_ for _ in ()).throw(
                            AssertionError("re-measured an unchanged "
                                           "reference")))
    second = load_pipeline_state(str(tmp_path))
    assert second["reference_style"]["pacing"]["cuts_per_minute"] == 30.0
    assert second["reference_style_digest"] == state["reference_style_digest"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_slot_reads_the_stored_measurement_without_decoding(
        tmp_path, monkeypatch):
    """The pacing slot reads the stored value, not a second decode.

    The failure it pins: the slot measuring the reference itself -
    an ffmpeg pass every time the slot is decided, on top of the
    runner's measurement for the step context.  This fails on the
    pre-fix head, where the slot always decodes.
    """
    import subprocess
    from library.processes.edit_video.run_pipeline import (
        load_pipeline_state, save_pipeline_state)
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        "reference: reference.mp4\n", encoding="utf-8")
    state = load_pipeline_state(str(tmp_path))
    save_pipeline_state(str(tmp_path), state)
    monkeypatch.setattr(RS, "measure_reference_asl",
                        lambda path: (_ for _ in ()).throw(
                            AssertionError("the slot decoded the reference")))
    value, source = DV.stated_preference(SLOT, str(tmp_path))
    assert value == state["reference_style"]["shot_length_distribution"][
        "asl_seconds"]
    assert "reference.mp4" in source


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_changed_reference_is_measured_again(tmp_path, monkeypatch):
    """A reference replaced by different bytes is re-measured.

    The failure it pins: a digest keyed on the file's name or mtime
    would reuse a stored measurement for a reference that was replaced
    - the style of a different video would be reported as this one's.
    """
    import subprocess
    from library.processes.edit_video.run_pipeline import (
        load_pipeline_state, save_pipeline_state)
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        "reference: reference.mp4\n", encoding="utf-8")
    state = load_pipeline_state(str(tmp_path))
    save_pipeline_state(str(tmp_path), state)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=green:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    second = load_pipeline_state(str(tmp_path))
    assert second["reference_style_digest"] != state["reference_style_digest"]
    assert "reference_style_error" not in second


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_an_unmeasurable_reference_is_said_not_silently_dropped(
        tmp_path):
    """A declared reference that fails to measure is recorded, not dropped.

    The failure it pins: a measurement that fails falling through to
    the next rung silently - the slot reports nothing, and the run
    proceeds as if no reference was declared.  The failure is recorded
    on the state and the slot's decision says so.
    """
    import subprocess
    from library.processes.edit_video.run_pipeline import (
        load_pipeline_state, save_pipeline_state)
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    (tmp_path / "video.yaml").write_text(
        "reference: reference.mp4\n", encoding="utf-8")
    state = load_pipeline_state(str(tmp_path))
    save_pipeline_state(str(tmp_path), state)
    video.unlink()
    second = load_pipeline_state(str(tmp_path))
    assert "reference_style" not in second
    assert "could not be read" in second["reference_style_error"]
    save_pipeline_state(str(tmp_path), second)
    decision = DV.decide(SLOT, project_folder=str(tmp_path),
                         model_answer={"value": 9.0, "why": "x"})
    assert decision.basis != DV.STATED
    assert any("unmeasured" in row["why"] and "reference" in row["why"]
               for row in decision.rungs_skipped)


# ── The subtitle slot's measured rung ──────────────────────────────

SUBTITLE_SLOT = "subtitle.max_words_per_card"


@pytest.mark.usefixtures("_isolate_user_taste_profile",
                         "_measured_reference")
class TestTheSubtitleSlotRung:

    def _project_with_stored_style(self, tmp_path, style):
        """A project whose state holds a measured representation.

        The measurement is stubbed: what this rung owns is that the slot
        READS the stored representation's words per caption, not that the
        representation was measured (pinned by
        tests/unit/picture/test_reference_style.py).  The reference file
        is present and its digest matches the stored one, so the stored
        path is the one under test.
        """
        import json
        from library.tools.project_layout import ProjectLayout
        (tmp_path / "reference.mp4").write_text(
            "reference", encoding="utf-8")
        state = {"reference_style": style,
                 "reference_style_digest": "abc123"}
        path = ProjectLayout(str(tmp_path)).pipeline_data_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        (tmp_path / "video.yaml").write_text(
            "reference: reference.mp4\n", encoding="utf-8")
        return str(tmp_path)

    def test_the_slot_reads_the_stored_words_per_caption(self, tmp_path):
        """The slot reads the representation, not a second decode.

        The failure it pins: the subtitle slot measuring the reference
        itself - an OCR pass every time the slot is decided, on top of
        the runner's measurement for the step context.
        """
        folder = self._project_with_stored_style(tmp_path, {
            "text": {"captions": {"mean_words": 4.0}},
        })
        value, source = DV.stated_preference(SUBTITLE_SLOT, folder)
        assert value == 4.0
        assert "reference.mp4" in source

    def test_a_reference_without_captions_is_absent_for_the_slot(
            self, tmp_path):
        """No captions is not a zero-word caption.

        The failure it pins: a reference without captions giving the
        slot a measured value of 0 - the plan would read 0 as the
        reference's word count and caption nothing.
        """
        folder = self._project_with_stored_style(tmp_path, {
            "text": {"captions": {"presence": 0.0, "count": 0}},
        })
        assert DV.stated_preference(SUBTITLE_SLOT, folder) == (None, "")

    def test_the_project_s_own_preference_outranks_the_reference(
            self, tmp_path):
        """An explicit preference is the project's own voice.

        The failure it pins: letting a reference's measurement
        override a preference the project states would make the
        reference the chooser (AGENTS.md 10.5).
        """
        (tmp_path / "project.yaml").write_text(
            "pipeline:\n"
            "  creative_preferences:\n"
            "    subtitle:\n"
            "      max_words_per_card: 8\n", encoding="utf-8")
        folder = self._project_with_stored_style(tmp_path, {
            "text": {"captions": {"mean_words": 4.0}},
        })
        value, source = DV.stated_preference(SUBTITLE_SLOT, folder)
        assert value == 8
        assert "creative_preferences" in source
