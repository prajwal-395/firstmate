"""Golden projects: tiny controlled Ren projects, driven end to end.

A golden project is a recipe - who speaks, what they say and when, how the
captain's master timeline is laid out, what the model answers - rendered
into real media by ffmpeg and a real project folder, then driven through
the same entry points the captain's runs use:

    analyze   timeline_transcript.main      (the `ren analyze` transcript)
    plan      step 3.04's post-bridge + `ren propose` + the captain's approval
    build     run_reels.run                 (`ren build`: build, verify, promote)
    touch     reel_touchup.apply_touchup    (`ren touch`)
    rebuild   run_reels.run again
    deliver   reel_deliver.deliver_reel     (`ren deliver`)

Only what cannot run here is answered, each at ONE seam:

- the transcriber (`timeline_transcript.transcribe_audio`): the media is
  tone, so the recipe's own lines are what the microphone "heard";
- the caption renderer (step 4.05's `_default_unit_engine`): Remotion is
  replaced by an ffmpeg card of the size and length the props ask for;
- Resolve: the canonical double (`tests/resolve_double.py`) offline, or
  the live Qualification project (`test_golden_live.py`).

Assertions are STRUCTURAL and SEMANTIC - rows, spans, carried edits,
refusals and their names, the delivered file's length and frame - never
pixels. Which shapes exist, and why only these, is
`docs/GOLDEN_PROJECTS.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

#: The GEO podcast's rate. The reels path is built for 23.976 masters:
#: `reel_build` plans captions at 24000/1001 whatever the master says
#: (docs/GOLDEN_PROJECTS.md, "What the golden projects found").
FPS = 24000 / 1001
RATE = "23.976"
WORD_SECONDS = 0.3
WORD_LENGTH = 0.25
LEAD = 0.1


@dataclass(frozen=True)
class Line:
    """One spoken passage: who, when it starts on the master, the words."""

    speaker: str
    start: float
    text: str

    def words(self) -> list:
        cursor = self.start + LEAD
        timed = []
        for word in self.text.split():
            timed.append((word, round(cursor, 3),
                          round(cursor + WORD_LENGTH, 3)))
            cursor += WORD_SECONDS
        return timed

    @property
    def first(self) -> float:
        return self.words()[0][1]

    @property
    def last(self) -> float:
        return self.words()[-1][2]


@dataclass(frozen=True)
class Conversation:
    """A synced two-camera conversation master, the GEO podcast's shape:
    one picture row and one program-audio row per speaker, every angle
    running the whole master, the cut being the reel's to make."""

    name: str
    master: str
    seconds: float
    speakers: tuple
    lines: tuple
    tones: tuple = (440, 660)
    reels: tuple = field(default_factory=tuple)

    def key(self) -> str:
        recipe = {"seconds": self.media_seconds(), "speakers": self.speakers,
                  "tones": self.tones, "rate": RATE, "size": [640, 360]}
        return hashlib.sha256(
            json.dumps(recipe).encode("utf-8")).hexdigest()[:12]

    def frames(self) -> int:
        return round(self.seconds * FPS)

    def media_seconds(self) -> float:
        """A camera file runs past what the master uses of it."""
        return self.seconds + 4.0


CONVERSATION = Conversation(
    name="Golden Conversation",
    master="Golden - Synced",
    seconds=21.0,
    speakers=("Akshita", "Craig"),
    lines=(
        Line("Akshita", 0.0,
             "so what does an AI actually say about your brand today"),
        Line("Craig", 4.0, "it depends on what it read and where it read it"),
        Line("Akshita", 8.0,
             "which means the content you publish is the answer"),
        Line("Craig", 12.0,
             "right and most brands never check that answer at all"),
        Line("Akshita", 16.0,
             "so go check what it says about you the link is below"),
    ),
)

#: What the model answers at step 3.04 for `CONVERSATION`: one reel, the
#: exchange in lines 0-3, closing on Akshita's call to action (line 4).
CONVERSATION_ANSWER = {
    "moments": [{
        "start": CONVERSATION.lines[0].first,
        "end": CONVERSATION.lines[3].last,
        "slug": "check-the-answer",
        "reason": "the whole argument in one exchange",
        "cta": {"start": CONVERSATION.lines[4].first,
                "end": CONVERSATION.lines[4].last,
                "note": "the literal next step after the argument"},
    }],
}

REEL = "Reel 01 - check-the-answer"


# ── Media: rendered once per machine, keyed by the recipe ─────────────

def media_root() -> Path:
    from library.tools import qualification_project as qp
    return qp.media_dir().parent / "golden"


def ensure_media(recipe: Conversation) -> dict:
    """`{speaker: path}` - one camera file per speaker, tone audio."""
    directory = media_root() / recipe.key()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for speaker, tone in zip(recipe.speakers, recipe.tones):
        path = directory / f"{speaker.lower()}.mov"
        if not path.exists():
            partial = directory / f".{speaker.lower()}.partial.mov"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i",
                (f"testsrc2=size=640x360:rate=24000/1001:"
                 f"duration={recipe.media_seconds()}"),
                "-f", "lavfi", "-i",
                (f"sine=frequency={tone}:sample_rate=48000:"
                 f"duration={recipe.media_seconds()}"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(partial)],
                check=True, capture_output=True, encoding="utf-8")
            partial.rename(path)
        paths[speaker] = path
    return paths


def probe(path: str) -> dict:
    """What Resolve reads off a file it imports, read with ffprobe."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, encoding="utf-8", check=False)
    streams = (json.loads(done.stdout or "{}").get("streams") or [{}])
    stream = streams[0] if streams else {}
    if not stream.get("width"):
        return {}
    num, _, den = str(stream.get("r_frame_rate", "0/1")).partition("/")
    rate = float(num) / float(den or 1) if float(den or 1) else 0.0
    found = {"Resolution": f"{stream['width']}x{stream['height']}",
             "FPS": RATE if abs(rate - FPS) < 0.01 else f"{rate:g}"}
    if stream.get("nb_frames"):
        found["Frames"] = str(stream["nb_frames"])
    return found


# ── The project folder ───────────────────────────────────────────────

def make_project_folder(root: Path, recipe: Conversation,
                        resolve_name: str = "") -> Path:
    folder = root / "golden-conversation"
    folder.mkdir(parents=True)
    (folder / "project.yaml").write_text(yaml.safe_dump({
        "name": recipe.name,
        "slug": "golden-conversation",
        "source": {"type": "mov", "resolution": "640x360", "fps": 23.976},
        "resolve": {"project_name": resolve_name or recipe.name,
                    "timeline_name": recipe.master},
    }), encoding="utf-8")
    return folder


# ── The seams ────────────────────────────────────────────────────────

def heard(recipe: Conversation):
    """The transcriber seam answered from the recipe: per speaker, the
    words that speaker says, on that speaker's rebuilt (timeline-clock)
    track - the shape the hybrid transcriber returns."""
    def transcribe_audio(audio_path, label="", **_ignored):
        segments = []
        for line in recipe.lines:
            if line.speaker != label:
                continue
            words = [{"word": w, "start": s, "end": e}
                     for w, s, e in line.words()]
            segments.append({"start": line.first, "end": line.last,
                             "text": line.text, "words": words})
        return ({"segments": segments},
                {"arm": "golden-recipe", "label": label})
    return transcribe_audio


class CardRenderer:
    """Step 4.05's renderer seam: an opaque card in a clear frame, of the
    size and length the props ask for, in the lower half where step 4.05's
    QA expects a caption. Remotion draws the words; nothing
    golden asserts depends on which pixels spell them."""

    def __init__(self) -> None:
        self.rendered: list = []

    def render(self, props_path, overlay_path, sequence=False):
        if sequence:
            return False, "the golden card renderer draws video only"
        props = json.loads(Path(props_path).read_text(encoding="utf-8"))
        width, height = int(props["width"]), int(props["height"])
        frames = int(props["durationInFrames"])
        os.makedirs(os.path.dirname(overlay_path), exist_ok=True)
        done = subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            (f"color=c=black@0.0:size={width}x{height}:rate=24000/1001,"
             f"format=rgba"),
            "-vf", (f"drawbox=x={width // 4}:y={height * 5 // 8}:"
                    f"w={width // 2}:"
                    f"h={max(height // 8, 2)}:color=white@1:t=fill"),
            "-frames:v", str(frames), "-c:v", "qtrle", overlay_path],
            capture_output=True, encoding="utf-8", check=False)
        self.rendered.append(overlay_path)
        return done.returncode == 0, done.stderr


def render_engine(job: dict, timeline) -> bool:
    """The double's render queue made to write a file of the job's shape:
    the marked frames at the timeline's frame size, with audio."""
    width = int(timeline.GetSetting("timelineResolutionWidth"))
    height = int(timeline.GetSetting("timelineResolutionHeight"))
    reported = str(timeline.GetSetting("timelineFrameRate"))
    rate, fps = (("24000/1001", FPS) if reported == RATE
                 else (reported, float(reported)))
    frames = int(job["MarkOut"]) - int(job["MarkIn"]) + 1
    path = Path(job["TargetDir"]) / f"{job['OutputFilename']}.mp4"
    done = subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate={rate}",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
        "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", f"{frames / fps:.6f}", str(path)],
        capture_output=True, encoding="utf-8", check=False)
    return done.returncode == 0


# ── The Resolve world, offline ───────────────────────────────────────

def conversation_world(recipe: Conversation, media: dict):
    """The captain's synced master in the canonical double: a row per
    angle named for its speaker, a program-audio row per angle named for
    its STREAM (`Akshita CH1`), every angle covering the whole master."""
    from tests import resolve_double as rd

    project = rd.make_project(recipe.name, width=1080, height=1920,
                              frame_rate=RATE)
    pool = project.GetMediaPool()
    pool.probe = probe
    clips = dict(zip(recipe.speakers,
                     pool.ImportMedia([str(media[s])
                                       for s in recipe.speakers])))
    program = json.dumps({"track_mapping": {"1": {
        "channel_idx": [1], "mute": False, "type": "mono"}}})

    def angle(speaker, audio=False):
        return rd.FakeTimelineItem(
            clips[speaker].GetName(), None, start=0,
            duration=recipe.frames(), left_offset=0,
            pool_item=clips[speaker],
            source_audio_channel_mapping=program if audio else None)

    master = rd.FakeTimeline(
        recipe.master, project=project, frame_rate=RATE,
        settings={"timelineResolutionWidth": "1080",
                  "timelineResolutionHeight": "1920"},
        video=[(s, [angle(s)]) for s in recipe.speakers],
        audio=[(f"{s} CH1", [angle(s, audio=True)])
               for s in recipe.speakers])
    project.adopt(master)
    project.SetCurrentTimeline(master)
    project.render_engine = render_engine
    return rd.FakeResolve(project), project


def install_offline_seams(monkeypatch, resolve, recipe) -> CardRenderer:
    """Point every Resolve route at `resolve`; answer the two ML/render
    seams. Returns the card renderer, whose `rendered` list the test may
    read."""
    from library.tools import marker_feedback, reel_build, resolve_locale
    from library.tools.execution import resolve_render

    monkeypatch.setattr(resolve_locale, "scriptapp_preserving_locale",
                        lambda *_a, **_k: resolve)
    monkeypatch.setattr(reel_build, "_connect_resolve", lambda: resolve)
    monkeypatch.setattr(marker_feedback, "connect_resolve", lambda: resolve)
    monkeypatch.setattr(resolve_render, "_connect", lambda: resolve)
    return install_model_seams(monkeypatch, recipe)


def install_model_seams(monkeypatch, recipe) -> CardRenderer:
    """The two seams both backends answer: the transcriber and Remotion."""
    from library.tools import timeline_transcript
    from library.tools.operations import load_step_module

    monkeypatch.setattr(timeline_transcript, "transcribe_audio",
                        heard(recipe))
    renderer = CardRenderer()
    captions = load_step_module("step_4_05_render_subtitles", "step.py")
    monkeypatch.setattr(captions, "_default_unit_engine",
                        lambda *_a, **_k: renderer)
    return renderer


# ── The stages ───────────────────────────────────────────────────────

def analyze(folder: Path) -> dict:
    """`ren analyze`'s transcript: rebuild each speaker's audio off the
    master, hear it, bind it back. Returns the document written."""
    from library.tools import timeline_transcript

    assert timeline_transcript.main([str(folder)]) == 0
    return json.loads(timeline_transcript.transcript_path(str(folder))
                      .read_text(encoding="utf-8"))


def plan(folder: Path, answer: dict) -> Path:
    """Step 3.04's post-bridge over the model's answer, recorded as the
    step's output; `ren propose` publishes it. Returns the proposal."""
    from library.tools import reel_proposal, timeline_transcript
    from library.tools.operations import load_step_module
    from library.tools.project_layout import ProjectLayout

    post_bridge = load_step_module("step_3_04_select_reels",
                                   "post_bridge.py")
    transcript = json.loads(timeline_transcript.transcript_path(
        str(folder)).read_text(encoding="utf-8"))
    selection = post_bridge.resolve(
        answer, {"timeline_transcript": transcript,
                 "project_folder": str(folder)})
    output = Path(ProjectLayout(str(folder)).step_dir(
        "select_reels", "output.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selection), encoding="utf-8")
    return reel_proposal.write_from_step_output(str(folder), force=True)


def rule(proposal: Path, approval: str = "approved", **moment) -> dict:
    """The captain's edit of the proposal file: a ruling on every moment,
    and optionally a hand-moved field (`timeline_end=...`)."""
    document = json.loads(proposal.read_text(encoding="utf-8"))
    for entry in document["moments"]:
        entry["approval"] = approval
        entry.update(moment)
    proposal.write_text(json.dumps(document), encoding="utf-8")
    return document


def build(folder: Path, **options) -> int:
    """`ren build`: the reels process, every capability in order."""
    from library.processes.reels import run_reels

    parser = argparse.ArgumentParser()
    run_reels.add_arguments(parser)
    args = parser.parse_args([str(folder)])
    for key, value in options.items():
        setattr(args, key, value)
    return run_reels.run(str(folder), args)


def rows(timeline) -> dict:
    """`{(kind, name): [(start, end, enabled), ...]}` read off a timeline."""
    found = {}
    for kind in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            found[(kind, timeline.GetTrackName(kind, index))] = [
                (item.GetStart(), item.GetEnd(), item.GetClipEnabled())
                for item in timeline.GetItemListInTrack(kind, index)]
    return found


def timeline_names(project) -> list:
    """Every timeline's name, read through the API both backends answer."""
    return [project.GetTimelineByIndex(index).GetName()
            for index in range(1, (project.GetTimelineCount() or 0) + 1)]


def timeline(project, name: str):
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, (project.GetTimelineCount() or 0) + 1)
               if project.GetTimelineByIndex(index).GetName() == name]
    assert len(matches) == 1, (name, timeline_names(project))
    return matches[0]


# ── The Resolve world, live ──────────────────────────────────────────

def live_conversation_master(project, recipe: Conversation, media: dict):
    """The same master built in a live, EMPTY scratch project: the
    project's rate and frame set first (reel timelines inherit the
    rate), the angles imported, one picture and one program-audio row
    per speaker, every angle covering the whole master."""
    for key, value in (("timelineFrameRate", RATE),
                       ("timelineResolutionWidth", "1080"),
                       ("timelineResolutionHeight", "1920")):
        assert project.SetSetting(key, value), f"Resolve refused {key}"
    pool = project.GetMediaPool()
    clips = pool.ImportMedia([str(media[s]) for s in recipe.speakers])
    assert clips and len(clips) == len(recipe.speakers), "import refused"
    by_speaker = dict(zip(recipe.speakers, clips))
    master = pool.CreateEmptyTimeline(recipe.master)
    assert master, "Resolve would not create the master"
    assert project.SetCurrentTimeline(master)
    for kind in ("video", "audio"):
        while master.GetTrackCount(kind) < len(recipe.speakers):
            assert master.AddTrack(kind), f"Resolve would not add a {kind} row"
    start = master.GetStartFrame()
    specs = []
    for index, speaker in enumerate(recipe.speakers, 1):
        for media_type in (1, 2):
            specs.append({"mediaPoolItem": by_speaker[speaker],
                          "startFrame": 0, "endFrame": recipe.frames(),
                          "trackIndex": index, "recordFrame": start,
                          "mediaType": media_type})
    assert pool.AppendToTimeline(specs), "Resolve placed nothing"
    for index, speaker in enumerate(recipe.speakers, 1):
        assert master.SetTrackName("video", index, speaker)
        assert master.SetTrackName("audio", index, f"{speaker} CH1")
    return master
