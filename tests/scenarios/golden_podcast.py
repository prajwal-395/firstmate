"""The golden podcast: one file, two voices, a topic said three times.

The golden conversation's shape (two voices, one exchange after another)
as ONE file, the way a person hands Ren an episode. Two scenarios drive
it: `test_podcast_to_reels.py` (T14, the runner directly) and
`test_carefree_transcript.py` (T8, a chat host driving `ren` verbs).

Only what cannot run here is answered, each at one seam
(`docs/GOLDEN_PROJECTS.md`):

- the transcriber (`timeline_transcript.transcribe_audio`): the media is
  test pattern and tone, so `LINES` are what the file says;
- the diarizer (`single_track_diarization.diarize_track`): `LINES`' turns
  are who said them;
- the model, through the file handshake: step 3.04's double READS the
  request off the context it was sent - it fails unless the count and
  topic arrived as briefing answers - and chooses the stretches whose
  words say the topic; step 3.05's double reads nothing.

Resolve is never reachable: every route to it raises.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from tests.scenarios import golden

TOPIC = "pricing"

LINES = (
    golden.Line("A", 0.0, "welcome back today we talk about pricing and "
                          "why it scares founders"),
    golden.Line("B", 4.0, "pricing scared me for years honestly"),
    golden.Line("A", 7.0, "so how did you finally raise your pricing"),
    golden.Line("B", 10.0, "we doubled the price and nobody left"),
    golden.Line("A", 13.0, "that is wild let us talk about hiring next"),
    golden.Line("B", 16.0, "hiring is a whole other story"),
    golden.Line("A", 19.0, "back to pricing what would you tell a founder"),
    golden.Line("B", 22.5, "charge more than feels comfortable and then "
                           "a little more"),
)
SECONDS = 27.0
VOICE = {"A": "speaker_01", "B": "speaker_02"}


def media(directory: Path) -> Path:
    """The episode as one small real file in `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "episode.mov"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={SECONDS}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(path)],
        check=True, capture_output=True, encoding="utf-8")
    return path


def install(monkeypatch, folder) -> dict:
    """The seams for the project at `folder` (a path, or a callable that
    returns one once the project exists); returns counters a test reads."""
    from library.tools import (
        llm_handshake,
        model_task,
        reel_request,
        resolve_locale,
        single_track_diarization,
        timeline_ingest,
        timeline_transcript,
    )

    calls = {"heard": 0, "diarized": 0, "asked": {}}
    project = folder if callable(folder) else (lambda: folder)

    def no_resolve(*_a, **_k):
        raise AssertionError("the golden podcast must not reach Resolve")

    monkeypatch.setattr(resolve_locale, "scriptapp_preserving_locale",
                        no_resolve)
    monkeypatch.setattr(timeline_ingest, "connect", no_resolve)

    def transcribe_audio(audio_path, label="", **_ignored):
        calls["heard"] += 1
        segments = [{"start": line.first, "end": line.last,
                     "text": line.text,
                     "words": [{"word": w, "start": s, "end": e}
                               for w, s, e in line.words()]}
                    for line in LINES]
        return ({"segments": segments},
                {"arm": "golden-recipe", "aligner": "golden-recipe"})

    monkeypatch.setattr(timeline_transcript, "transcribe_audio",
                        transcribe_audio)

    def diarize_track(wav_path, num_speakers=None, **_ignored):
        calls["diarized"] += 1
        clusters = []
        for who in ("A", "B"):
            turns = [(round(line.first - 0.05, 3), round(line.last + 0.05, 3))
                     for line in LINES if line.speaker == who]
            clusters.append(single_track_diarization.SpeakerCluster(
                label=VOICE[who], turns=turns, centroid=(1.0,)))
        return single_track_diarization.Diarization(
            clusters=clusters, window_embeddings=np.zeros((0, 1)),
            window_starts=np.zeros(0), window_labels=[],
            speakers_estimated=2, inference_seconds=0.0,
            method="golden-recipe", weights="", device="cpu")

    monkeypatch.setattr(single_track_diarization, "diarize_track",
                        diarize_track)

    publish = llm_handshake.publish_request

    def answer(request, response, payload):
        publish(request, response, payload)
        step = payload.get("step_id")
        calls["asked"][step] = calls["asked"].get(step, 0) + 1
        if step == "select_reels":
            body = choose(payload, Path(project()))
        elif step == "judge_reels":
            body = read(payload)
        else:
            return
        Path(response).write_text(json.dumps(body), encoding="utf-8")

    monkeypatch.setattr(llm_handshake, "publish_request", answer)
    monkeypatch.setattr(model_task, "_agent_sleep", lambda _seconds: None)
    calls["count_question"] = reel_request.COUNT_QUESTION
    return calls


def choose(payload: dict, folder: Path) -> dict:
    """The model double for 3.04: the REQUEST tells it how many and about
    what, and it picks the exchanges whose words say the topic."""
    from library.tools import reel_request, timeline_transcript

    context = payload.get("context") or ""
    assert reel_request.COUNT_QUESTION in context, (
        "the count never reached step 3.04's context")
    assert reel_request.TOPIC_QUESTION in context, (
        "the topic never reached step 3.04's context")
    count = int(context.split(reel_request.COUNT_QUESTION, 1)[1]
                .split("Said:", 1)[1].split()[0])
    topic = (context.split(reel_request.TOPIC_QUESTION, 1)[1]
             .split("Said:", 1)[1].splitlines()[0].strip())
    transcript = json.loads(timeline_transcript.transcript_path(folder)
                            .read_text(encoding="utf-8"))
    segments = transcript["segments"]
    moments = []
    used = -1
    for index, segment in enumerate(segments):
        if len(moments) == count:
            break
        if index <= used or topic not in segment["text"]:
            continue
        reply = next((s for s in segments[index + 1:]
                      if s["speaker"] != segment["speaker"]), None)
        if reply is None:
            continue
        used = segments.index(reply)
        moments.append({
            "start": segment["timeline_start"], "end": reply["timeline_end"],
            "slug": f"{topic}-{len(moments) + 1}",
            "reason": f"an exchange that says {topic}"})
    return {"reel_selection": {"moments": moments, "considered": [],
                               "undetermined": []}}


def read(payload: dict) -> dict:
    """The model double for 3.05: a reading of each reel it was sent,
    every quote taken word for word from that reel's own lines."""
    readings = []
    for row in (payload.get("context") or "").splitlines():
        match = re.match(r"\s*(\d+),([\d.]+),`(\[.*\])`\s*$", row)
        if not match:
            continue
        lines = json.loads(match.group(3))
        first, last = lines[0]["says"], lines[-1]["says"]
        readings.append({
            "reel": int(match.group(1)), "claim_quote": first,
            "claim": first, "opening_quote": first, "closing_quote": last,
            "closing_asks_for": "nothing", "takeaway_quote": last,
            "takeaway": last, "assumes_known": [], "claim_parts": [],
            "stops_developing_at": float(match.group(2)),
            "rank": len(readings) + 1, "basis": "the reel's own lines"})
    return {"readings": readings}


def said_between(transcript: dict, start: float, end: float) -> str:
    """Every transcript line that overlaps `[start, end)`, joined."""
    return " ".join(s["text"] for s in transcript["segments"]
                    if s["timeline_start"] < end and s["timeline_end"] > start)
