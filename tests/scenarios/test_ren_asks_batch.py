"""LE-11 acceptance: a 5-reel batch of model asks, read through `ren asks`.

The cost this removes (plan section 4.5): a reels batch filed three asks per
reel and every one restated the same prompt, roster and schema - 31 reels
were 2.38 MB, 20% unique. Requests now carry shared text by reference and
`ren asks` prints each shared text once.

"Today's files" are produced by the SAME three writers with the request
written inline, exactly as they wrote it before LE-11 (`json.dump(request,
indent=2)`): `write_request_file` is swapped for that one line, so the
baseline is not a model of the old format but the old format.

Every project is built under `tmp_path`; no captain project, Resolve
timeline or broker is touched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import llm_handshake, pending_asks, reel_look
from library.tools import reel_semantic_visual as sem_vis
from library.tools.replay_bench import bench

REELS = range(1, 6)
WORDS = ("the price of the house went up because nobody was building "
         "anything in this town for twenty years and now we all pay "
         "for it every single month").split()


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # Firstmate's full-suite run executes inside a Claude Code session;
    # the fixture addresses its project by absolute path.
    for marker in ("PIPELINE_PROJECTS_ROOT", "PIPELINE_SFX_LIBRARY",
                   "PIPELINE_MUSIC_LIBRARY", "REN_OUTPUT"):
        monkeypatch.delenv(marker, raising=False)


class _Clip:
    def __init__(self, source_file):
        self.source_file = source_file
        self.track_type = "video"
        self.timeline_start = 0.0
        self.source_in = 0.0


def _write_batch(project: Path) -> None:
    """The three asks of five reels, through the three real writers."""
    for number in REELS:
        start = 10.0 * number
        words = [{"word": w, "start": start + 0.4 * i,
                  "end": start + 0.4 * i + 0.3, "timed": True}
                 for i, w in enumerate(WORDS)]
        seconds = 0.4 * len(WORDS)
        transcript = {"segments": [{
            "timeline_start": start, "timeline_end": start + seconds,
            "resolve_item_id": f"item{number}", "source_file": "clip_a.mov",
            "source_start": start, "source_end": start + seconds,
            "words": words}]}
        moment = SimpleNamespace(
            number=number, timeline_name=f"Reel {number:02d} - batch {number}",
            timeline_start=start, timeline_end=start + seconds)
        ranges = [(start, start + seconds)]
        assert sem_vis.write_request(moment, transcript, ranges,
                                     str(project), 30)
        assert sem_vis.write_span_request(moment, transcript, ranges,
                                          str(project), fps=30.0)
        # Two shots: a body shot the motion plan may move, then the
        # closing shot whose moves are locked.
        half = seconds / 2
        placements = [
            {"clip": _Clip(f"/m{number}.mxf"), "source_in": 0.0,
             "source_out": half, "record": 0.0, "snapped_record": 0,
             "speaker": "SpeakerOne"},
            {"clip": _Clip(f"/m{number}.mxf"), "source_in": half,
             "source_out": seconds, "record": half,
             "snapped_record": round(half * 24.0), "speaker": "SpeakerOne"}]
        spine = reel_look.motion_spine(placements, 24.0, [{
            "source_file": f"/m{number}.mxf", "timeline_start": 0.0,
            "timeline_end": seconds, "source_start": 0.0,
            "source_end": seconds,
            "words": [{"word": w["word"], "start": w["start"] - start,
                       "end": w["end"] - start} for w in words]}])
        assert reel_look.write_motion_request(
            number, moment.timeline_name, spine,
            [{"timeline_start": 0.0, "timeline_end": seconds,
              "text": " ".join(WORDS)}], str(project))


def _legacy_write(path, request, **_kwargs):
    """How the reel writers wrote a request before LE-11, verbatim."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
    return request


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "batch"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "project.yaml").write_text(
        'name: "Batch"\nslug: batch\n', encoding="utf-8")
    return project


def _requests(project: Path) -> Path:
    return project / "pipeline_output" / "llm_requests"


def _without_timestamp(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "timestamp"}


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """`(project, today)`: the batch written today's way, then LE-11's.

    `today` maps each request file name to the bytes the writers left
    before LE-11; the project then holds the same batch in wire form.
    """
    project = _project(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(llm_handshake, "write_request_file", _legacy_write)
        _write_batch(project)
    today = {path.name: path.read_bytes()
             for path in sorted(_requests(project).glob("*.json"))}
    assert len(today) == 15
    assert not llm_handshake.shared_dir(_requests(project)).exists()
    shutil.rmtree(_requests(project))
    _write_batch(project)
    return project, today


def test_ren_asks_reads_a_quarter_of_todays_bytes(batch):
    project, today = batch
    today_bytes = sum(len(data) for data in today.values())

    text = pending_asks.render_text(pending_asks.collect(project))
    asks_bytes = len(text.encode("utf-8"))

    assert asks_bytes <= 0.25 * today_bytes, (
        f"ren asks prints {asks_bytes:,} B against today's "
        f"{today_bytes:,} B ({1 - asks_bytes / today_bytes:.1%} less)")
    # Each distinct prompt is printed exactly once, for 15 asks.
    report = pending_asks.collect(project)
    assert report["pending"] == 15
    prompts = {d: e for d, e in report["shared"].items()
               if e["role"] == "prompt"}
    assert len(prompts) == 3
    for entry in prompts.values():
        assert entry["asks"] == 5
        assert text.count(entry["text"].rstrip("\n")) == 1
    assert [len(group["asks"]) for group in report["groups"]] == [5, 5, 5]


def test_every_request_rebuilds_byte_for_byte(batch):
    project, today = batch
    for name, legacy in today.items():
        rebuilt = llm_handshake.read_request(_requests(project) / name)
        old = json.loads(legacy)
        if "timestamp" in old:
            rebuilt["timestamp"] = old["timestamp"]
        # The writer's own serialization of the rebuilt request IS the
        # file it wrote before LE-11.
        assert json.dumps(rebuilt, indent=2).encode("utf-8") == legacy, name
        # And on disk it no longer restates the prompt.
        wire = json.loads((_requests(project) / name).read_text("utf-8"))
        assert "prompt" not in wire and "prompt_ref" in wire

    rebuilt = bench.rebuild_requests(_requests(project))
    assert rebuilt["total"] == 15
    assert rebuilt["rebuilt"] == 15, [
        row for row in rebuilt["rows"] if not row["rebuilt"]]


def test_identical_responses_validate_the_same(batch, tmp_path, monkeypatch):
    project, today = batch
    legacy_project = _project(tmp_path / "legacy")
    _requests(legacy_project).mkdir(parents=True)
    for name, data in today.items():
        (_requests(legacy_project) / name).write_bytes(
            data.replace(str(project).encode(), str(legacy_project).encode()))

    semantic = {"motion_graphics_plan": [{
        "element": "title_lockup", "subject": "the price",
        "anchor_phrase": "the price", "hold_seconds": 2.0,
        "anchor": "top_left", "row": 0, "copy": {"display": "THE PRICE"},
        "color": "#F5F5F0", "why": "names the subject"}]}
    span = {"span_visual_plan": [{
        "segment": 1, "shows": "a house", "anchor_phrase": "the house",
        "lead_seconds": 0.2, "why": "the noun"}]}
    motion = {"reel_motion_plan": [{
        "target_block_position": 0, "anchor": {"word": "price"},
        "anchor_end": {"word": "building", "edge": "end"},
        "move": "zoom_in", "params": {"zoom_start": 1.0, "zoom_end": 1.08},
        "why": "a slow push"}]}
    read = {}
    for root in (project, legacy_project):
        responses = root / "pipeline_output" / "llm_responses"
        responses.mkdir(parents=True, exist_ok=True)
        for number in REELS:
            (responses / f"{sem_vis.request_stem(number)}.json").write_text(
                json.dumps(semantic), encoding="utf-8")
            (responses / f"{sem_vis.span_request_stem(number)}.json"
             ).write_text(json.dumps(span), encoding="utf-8")
            bound = reel_look.bind_motion_answer(str(root), number, motion)
            (responses / f"{reel_look.motion_request_stem(number)}.json"
             ).write_text(json.dumps(bound), encoding="utf-8")
        read[root] = [
            (sem_vis.read_answer(str(root), n),
             sem_vis.read_span_answer(str(root), n),
             reel_look.read_motion_answer(str(root), n),
             json.loads((responses / f"{reel_look.motion_request_stem(n)}"
                                     f".json").read_text("utf-8"))
             [reel_look.MOTION_BASIS_KEY])
            for n in REELS]
    # The same answer, bound to the same fingerprint, reads the same.
    assert read[project] == read[legacy_project]
    assert all(row[0] and row[1] and row[2] for row in read[project])
    assert pending_asks.collect(project)["pending"] == 0


def test_a_legacy_bound_motion_answer_survives_the_upgrade(tmp_path,
                                                           monkeypatch):
    """A batch answered before LE-11 is not re-asked by the first rebuild."""
    project = _project(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(llm_handshake, "write_request_file", _legacy_write)
        _write_batch(project)
    responses = project / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True)
    plan = {"reel_motion_plan": [{
        "target_block_position": 0, "anchor": {"word": "price"},
        "move": "zoom_in", "params": {"zoom_start": 1.0, "zoom_end": 1.05},
        "why": "slow"}]}
    answer = responses / f"{reel_look.motion_request_stem(1)}.json"
    answer.write_text(json.dumps(reel_look.bind_motion_answer(
        str(project), 1, plan)), encoding="utf-8")

    _write_batch(project)   # the first post-LE-11 rebuild rewrites the asks

    assert answer.is_file()
    assert reel_look.read_motion_answer(str(project), 1) == \
        plan["reel_motion_plan"]


def test_ren_asks_cli_prints_text_and_json(batch, capsys):
    project, _today = batch
    assert pending_asks.main([str(project)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("ren asks: 15 ask(s) owed")
    assert "answer -> pipeline_output/llm_responses/reel_motion_01.json" in text
    assert "<<shared " in text

    assert pending_asks.main([str(project), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pending"] == 15
    ask = next(a for a in report["asks"] if a["id"] == "reel_semantic_01")
    # The JSON names every shared piece; together they rebuild the ask.
    full = llm_handshake.read_request(ask["request"])
    assert report["shared"][ask["prompt"]]["text"] == full["prompt"]
    shared = [report["shared"][ref["sha256"]]["text"]
              for ref in ask["context_shared"]]
    assert all(section in full["context"] for section in shared)
