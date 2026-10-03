"""Golden projects on the `edit_video` path: raw footage to a delivered file.

`tests/scenarios/golden.py` drives the reels path; this drives the main
pipeline - `run_pipeline.run_pipeline`, the runner `ren edit` execs - over
a tiny recipe-rendered project, to the `rough_cut_subtitles` target: scan,
catalog, vision, transcription, creative direction, music, speech, spine,
A-roll, B-roll, mix, rough-cut review, captions, manifest and the Resolve
build and render.

What runs here that the runner would run in a child process runs IN this
process (`in_process_steps`), so the test's seams reach the step bodies.
Answered, each at one seam:

- vision (step 1.03 `_analyse_missing`): the recipe's profiles;
- transcription (step 1.04 `transcribe_clips_batched`): the recipe's lines;
- the model: every LLM request answered from
  `golden_monologue_answers.json` through the file handshake
  (`llm_handshake.publish_request`), so each answer meets the step's own
  schema check and post-bridge exactly as a host's would;
- Remotion: `golden.CardRenderer`;
- Resolve: the canonical double; the Fusion pass (a child process that
  draws comps the double cannot hold) returns success, and the render
  child runs `resolve_render.main` in this process against the double's
  `render_engine`.

`docs/GOLDEN_PROJECTS.md` says which shapes exist and what they found.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from tests.scenarios import golden

ANSWERS = Path(__file__).with_name("golden_monologue_answers.json")

#: What the beat tracker hears in a steady tone: no rhythm.
NO_BEATS = {"method": "golden-recipe", "bpm": None, "beats": [],
            "downbeats": [], "downbeat_source": "detected",
            "tempo_curve": [], "beat_count": 0, "tempo_stable": True,
            "note": "golden recipe: a steady tone carries no beat"}


@dataclass(frozen=True)
class Clip:
    """One camera file: its name, frame, length, picture and what it shows."""

    name: str
    size: str
    seconds: float
    picture: str
    tone: int | None
    seen: dict


@dataclass(frozen=True)
class Monologue:
    """A phone walk-and-talk with B-roll and a music bed - project 001's
    shape: one portrait talking head, landscape cutaways, 30 fps."""

    name: str
    clips: tuple
    lines: tuple            # (start seconds on the talking clip, words)
    music_seconds: float
    target_seconds: float

    def key(self) -> str:
        recipe = {"clips": [(c.name, c.size, c.seconds, c.picture, c.tone)
                            for c in self.clips],
                  "music": self.music_seconds}
        return hashlib.sha256(
            json.dumps(recipe).encode("utf-8")).hexdigest()[:12]

    def words(self, clip: str) -> list:
        """The recipe's aligned segments for `clip`, in clip seconds."""
        if clip != self.clips[0].name:
            return []
        segments = []
        for start, text in self.lines:
            cursor, words = start, []
            for word in text.split():
                words.append({"word": word, "start": round(cursor, 3),
                              "end": round(cursor + golden.WORD_LENGTH, 3)})
                cursor += golden.WORD_SECONDS
            segments.append({"start": start, "end": words[-1]["end"],
                             "text": text, "words": words})
        return segments


MONOLOGUE = Monologue(
    name="Golden Monologue",
    clips=(
        Clip("talk", "540x960", 12.0, "testsrc2", 220, {
            "location": "sidewalk outside an office", "type": "outdoor",
            "mode": "selfie", "framing": "close-up",
            "content": "person_talking_to_camera",
            "label": "person talking to camera", "role": "primary_subject",
            "category": "person",
            "action": "The person speaks to the camera while walking."}),
        Clip("broll_1", "960x540", 5.0, "smptebars", None, {
            "location": "a desk with a paper planner", "type": "indoor",
            "mode": "handheld", "framing": "wide", "content": "b_roll",
            "label": "paper planner on a desk", "role": "primary_subject",
            "category": "object", "action": "A hand writes in a planner."}),
        Clip("broll_2", "960x540", 5.0, "mandelbrot", None, {
            "location": "a whiteboard with a written list", "type": "indoor",
            "mode": "handheld", "framing": "medium", "content": "b_roll",
            "label": "whiteboard list", "role": "primary_subject",
            "category": "object", "action": "A list on a whiteboard."}),
    ),
    lines=((0.5, "the one thing i learned this week is simple"),
           (4.5, "a plan you can see beats a plan you remember"),
           (8.5, "so write it down today")),
    music_seconds=15.0,
    target_seconds=7.5,
)


# ── Media and the project folder ─────────────────────────────────────

def ensure_media(recipe: Monologue) -> dict:
    """`{clip name | "bed": path}`, rendered once per machine."""
    directory = golden.media_root() / f"monologue-{recipe.key()}"
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for clip in recipe.clips:
        path = directory / f"{clip.name}.mov"
        if not path.exists():
            audio = (f"sine=frequency={clip.tone}:sample_rate=48000"
                     if clip.tone else "anullsrc=r=48000:cl=stereo")
            _render(path, ["-f", "lavfi", "-i",
                           f"{clip.picture}=size={clip.size}:rate=30",
                           "-f", "lavfi", "-i", audio, "-t", str(clip.seconds),
                           "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-c:a", "aac"])
        paths[clip.name] = path
    bed = directory / "bed.wav"
    if not bed.exists():
        _render(bed, ["-f", "lavfi", "-i",
                      (f"sine=frequency=330:sample_rate=48000:"
                       f"duration={recipe.music_seconds}")])
    paths["bed"] = bed
    return paths


def _render(path: Path, args: list) -> None:
    partial = path.with_name(f".{path.stem}.partial{path.suffix}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(partial)],
                   check=True, capture_output=True, encoding="utf-8")
    partial.rename(path)


def make_project_folder(root: Path, recipe: Monologue, media: dict) -> Path:
    """The project as `ren new` lays it out: footage in raw/, the bed in
    music/, a one-line brief, and no music search - the run's menu is the
    project's own folder, never the machine's library or the network."""
    folder = (root / "golden-monologue").resolve()
    (folder / "raw").mkdir(parents=True)
    (folder / "music").mkdir()
    for clip in recipe.clips:
        shutil.copy(media[clip.name], folder / "raw" / f"{clip.name}.mov")
    shutil.copy(media["bed"], folder / "music" / "bed.wav")
    (folder / "brief.md").write_text(
        "A daily walk-and-talk: one thought, said plainly, with two "
        "cutaways.\n", encoding="utf-8")
    (folder / "project.yaml").write_text(yaml.safe_dump({
        "name": recipe.name, "slug": "golden-monologue",
        "source": {"type": "mov", "resolution": "540x960", "fps": 30},
        "creative_brief": str(folder / "brief.md"),
        "target_duration_seconds": recipe.target_seconds,
        "pipeline": {"music_search": False},
        "resolve": {"project_name": recipe.name,
                    "timeline_name": recipe.name},
    }), encoding="utf-8")
    return folder


# ── The seams ────────────────────────────────────────────────────────

def in_process_steps(monkeypatch) -> None:
    """Run each step body the runner would spawn in THIS process: its
    cached module (`operations.load_step_module`) and `main()`, with the
    step's argv, stdin and stdout, so a patch on the module reaches it."""
    from library.processes.edit_video import run_pipeline
    from library.tools.operations import load_step_module

    def run(argv, inputs, label):
        entry = Path(argv[1])
        out, err = io.StringIO(), io.StringIO()
        code = 0
        saved = sys.stdin, sys.argv
        sys.stdin, sys.argv = io.StringIO(json.dumps(inputs)), [str(entry),
                                                                *argv[2:]]
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                try:
                    load_step_module(entry.parent.name, entry.name).main()
                except SystemExit as exit_:
                    code = (exit_.code if isinstance(exit_.code, int)
                            else int(exit_.code is not None))
                except Exception:  # noqa: BLE001 - a step failure, reported
                    import traceback
                    traceback.print_exc()
                    code = 1
        finally:
            sys.stdin, sys.argv = saved
        return code, out.getvalue(), err.getvalue()

    monkeypatch.setattr(run_pipeline, "_run_step_subprocess", run)


def install_seams(monkeypatch, recipe: Monologue, folder: Path) -> dict:
    """Vision, transcription, the model and Remotion answered; returns
    `{step_id: times answered}` so a test can say which steps asked."""
    from library.tools import llm_handshake, model_task
    from library.tools.operations import load_step_module

    by_stem = {clip.name: clip for clip in recipe.clips}

    def analyse(paths, base_cmd, analysis_dir, run=None):
        for path in paths:
            write_profile(recipe, by_stem[Path(path).stem], path,
                          analysis_dir)

    vision = load_step_module("step_1_03_semantic_analysis", "step.py")
    monkeypatch.setattr(vision, "_analyse_missing", analyse)

    temporal = load_step_module("step_1_04_temporal_index", "step.py")

    def hear(requests, whisper_model_size="large-v3", language="en"):
        catalog = json.loads((folder / "pipeline_output" / "steps"
                              / "1_02_catalog_footage" / "output.json")
                             .read_text(encoding="utf-8"))
        stems = {row["clip_id"]: Path(row["path"]).stem
                 for row in catalog["clip_catalog"]}
        heard = {"arm": "golden-recipe", "aligner": "golden-recipe",
                 "detected_language": "en", "method": "golden-recipe"}
        return {request["key"]: (
            temporal._regions_from_segments(
                recipe.words(stems.get(request["key"])),
                request.get("onsets"), heard["method"]), dict(heard))
            for request in requests}

    monkeypatch.setattr(temporal, "transcribe_clips_batched", hear)

    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    asked: dict = {}
    publish = llm_handshake.publish_request

    def answer(request, response, payload):
        publish(request, response, payload)
        step = payload.get("step_id")
        asked[step] = asked.get(step, 0) + 1
        if step in answers:
            Path(response).write_text(
                json.dumps(answers[step]).replace("{project}", str(folder)),
                encoding="utf-8")

    monkeypatch.setattr(llm_handshake, "publish_request", answer)
    monkeypatch.setattr(model_task, "_agent_sleep", lambda _seconds: None)

    captions = load_step_module("step_4_05_render_subtitles", "step.py")
    renderer = golden.CardRenderer()
    monkeypatch.setattr(captions, "_default_unit_engine",
                        lambda *_a, **_k: renderer)
    # The measurement models, answered with what the recipe's media
    # measures (a test pattern and a steady tone): no faces, no audio
    # events, no beats. Each is the narrowest seam its step reaches.
    from library.steps.step_1_04_temporal_index import vision_measure
    monkeypatch.setattr(
        vision_measure, "measure_clip_vision",
        lambda *_a, **_k: vision_measure.empty_vision_doc(
            "golden recipe: test-pattern footage, nothing to measure",
            vision_measure.VISION_SAMPLE_RATE_HZ))
    monkeypatch.setattr(temporal, "_panns_checkpoint_present", lambda: False)
    from library.tools.analysis import music_pipeline
    monkeypatch.setattr(music_pipeline, "analyze_tempo_beats",
                        lambda *_a, **_k: dict(NO_BEATS))
    import importlib
    for name in ("library.tools.qa.subtitle_qa", "tools.qa.subtitle_qa"):
        qa = importlib.import_module(name)
        monkeypatch.setattr(qa, "_vision_observation",
                            lambda *_a, **_k: "not observed: golden run")
    for variable in ("PIPELINE_MUSIC_LIBRARY", "PIPELINE_SFX_LIBRARY",
                     "PIPELINE_SHARED_ASSETS"):
        empty = folder.parent / variable.lower()
        empty.mkdir(exist_ok=True)
        monkeypatch.setenv(variable, str(empty))
    return asked


def write_profile(recipe: Monologue, clip: Clip, path: str,
                  analysis_dir: str) -> None:
    """The vision pass's v3 profile for one clip, as the recipe sees it."""
    width, height = (int(n) for n in clip.size.split("x"))
    speech = recipe.words(clip.name)
    seen = clip.seen
    span = [[0.0, clip.seconds]]
    profile = {
        "clip_id": clip.name, "file_path": path,
        "duration_s": clip.seconds, "fps": 30.0,
        "resolution": [width, height],
        "transcript": " ".join(s["text"] for s in speech),
        "scene": [{"start": 0.0, "end": clip.seconds,
                   "location": seen["location"], "type": seen["type"],
                   "lighting": "Daylight",
                   "notable_features": [seen["label"]]}],
        "camera": [{"start": 0, "end": clip.seconds, "mode": seen["mode"],
                    "framing": seen["framing"], "stability": "stable",
                    "movement": "stationary"}],
        "actions": [{"window": span[0], "actions": [{
            "start": 0.0, "end": clip.seconds, "action": seen["action"],
            "speech_cue": "", "body_language": ""}],
            "analysis_time_s": 0.0}],
        "objects": [{"label": seen["label"], "appearances": span,
                     "role": seen["role"], "category": seen["category"],
                     "readable_text": None}],
        "assessment": {
            "speech_present": bool(speech),
            "speech_coverage": 0.6 if speech else 0.0,
            "speech_coverage_method": "temporal_index",
            "camera_stability": "stable", "usable_ranges": span,
            "unusable_ranges": [], "usable_ranges_method": "deterministic_v1",
            "usable_ranges_signals": ["picture_sharpness"],
            "content_type": seen["content"],
            "primary_subject_visible": span},
        "analysis_metadata": {"pipeline_version": "v3",
                              "model": "golden-recipe"},
    }
    Path(analysis_dir, f"clip_profile_{clip.name}_v3.json").write_text(
        json.dumps(profile), encoding="utf-8")


def probe(path: str) -> dict:
    """`golden.probe`, plus the audio channel count the edit's build
    reads off every imported clip ("Audio Ch")."""
    found = golden.probe(path)
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-of", "json", str(path)],
        capture_output=True, encoding="utf-8", check=False)
    streams = json.loads(done.stdout or "{}").get("streams") or []
    if streams:
        found["Audio Ch"] = str(streams[0].get("channels", 1))
    return found


def resolve_world(monkeypatch, recipe: Monologue):
    """The double, empty, as the build finds Resolve; the Fusion and
    render children routed to it. Returns the double's project."""
    from library.tools import resolve_locale
    from library.tools.execution import resolve_render
    from tests import resolve_double as rd

    project = rd.make_project(recipe.name, width=1080, height=1920,
                              frame_rate="30")
    project.GetMediaPool().probe = probe
    project.render_engine = golden.render_engine
    resolve = rd.FakeResolve(project)
    connect = resolve_locale.scriptapp_preserving_locale

    def handshake(*_args, **_kwargs):
        return resolve

    # Modules that imported the handshake BY NAME before this test (step
    # 6.01's builder does, at import) hold the original; rebind those too.
    monkeypatch.setattr(resolve_locale, "scriptapp_preserving_locale",
                        handshake)
    for module in list(sys.modules.values()):
        if getattr(module, "scriptapp_preserving_locale", None) is connect:
            monkeypatch.setattr(module, "scriptapp_preserving_locale",
                                handshake)
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        argv = [str(part) for part in (cmd if isinstance(cmd, (list, tuple))
                                       else [cmd])]
        if any("apply_fusion_comps" in part for part in argv):
            # The double holds no Fusion page; the pass is Resolve's.
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if any(part.endswith("music_pipeline.py") for part in argv):
            # Step 2.06's child (stems, sections, chords): the recipe's
            # bed is one steady tone, and this is what it measures.
            track = Path(argv[2])
            out_dir = Path(argv[argv.index("--output-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "music_analysis.json").write_text(json.dumps(
                bed_analysis(track, recipe.music_seconds)),
                encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if any(part.endswith("resolve_render.py") for part in argv):
            out = io.StringIO()
            saved = sys.argv
            sys.argv = argv[1:]
            try:
                with contextlib.redirect_stdout(out):
                    resolve_render.main()
            except Exception as exc:  # noqa: BLE001 - the child's exit 1
                return subprocess.CompletedProcess(cmd, 1, "", repr(exc))
            finally:
                sys.argv = saved
            return subprocess.CompletedProcess(cmd, 0, out.getvalue(), "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return project


def bed_analysis(track: Path, seconds: float) -> dict:
    """Step 2.06's analysis of a steady tone: no beats, no key, one
    level section, nothing separated."""
    return {
        "file": str(track), "filename": track.name, "duration_s": seconds,
        "sample_rate": 22050, "tempo": dict(NO_BEATS),
        "key": {"method": None, "key": None, "scale": None,
                "note": "golden recipe: a tone has no key"},
        "structure": {"method": "golden-recipe", "section_count": 1,
                      "novelty_peaks": [],
                      "sections": [{"type": "verse", "start": 0.0,
                                    "end": seconds, "duration": seconds,
                                    "energy": 0.088,
                                    "relative_energy": 1.0}]},
        "section_grid": {"available": False, "method": "golden-recipe",
                         "sections": [],
                         "reason": "no downbeats: no bar ruler to snap to"},
        "energy_dynamics": {"energy_curve_1hz": [0.735] * int(seconds),
                            "builds": [], "drops": [],
                            "dynamics": {"dynamic_range_db": 0.0,
                                         "energy_arc": "flat"},
                            "build_count": 0, "drop_count": 0},
        "chords": {"method": None, "chord_progression": [],
                   "note": "golden recipe: a tone has no chords"},
        "stems": {"method": None, "stems": {},
                  "note": "golden recipe: nothing to separate"},
    }


def edit(folder: Path) -> dict:
    """`ren edit <project> --full-auto agent --target rough_cut_subtitles`."""
    from library.processes.edit_video import run_pipeline

    run_pipeline.run_pipeline(str(folder), full_auto="agent", llm_timeout=1,
                              target="rough_cut_subtitles")
    return json.loads((folder / "pipeline_data.json")
                      .read_text(encoding="utf-8"))
