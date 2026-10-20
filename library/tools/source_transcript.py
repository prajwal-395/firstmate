"""What the SOURCE footage says, per speaker, when no Resolve timeline exists.

The gap this closes
-------------------
Step 3.04 (`select_reels`) reads one input no step made: the timeline
transcript `library/tools/timeline_transcript.py` writes. That module
rebuilds a speaker's audio from the clips a Resolve timeline PLAYS, so
"make 3 reels from this podcast" first needed the podcast placed on a
Resolve timeline by hand, Resolve open, and a module run outside the
pipeline. A person who only has the file could not get a proposal.

Nothing in the transcript needs a timeline except the clock it is
written on. The words are already measured per source: the per-source
memory's M1 slot (`library/tools/source_memory.py`) is a whole-source
word-timed hearing through the same hybrid seam
(`timeline_transcript.transcribe_audio` via
`library/tools/transcript_measurement.py`), stored by content digest and
reused while the file is unchanged. So this module writes the same
document `timeline_transcript` writes, to the same path, from M1 - with
the footage itself standing in for the cut.

The clock: a virtual timeline over the footage
----------------------------------------------
Each source with speech is laid end to end in footage order (clip id,
which is path order - `footage_identity.enumerate_footage`), so timeline
time is source time plus the source's start on that line. A single
podcast file therefore reads in its own seconds. Every row binds to its
source through `timeline_transcript.segments_for_speaker`, the same
binding the timeline path uses, against one `TimelineClip` per placed
source (`resolve_item_id` = `source:<clip_id>`, never a Resolve id).

A synced multicam shoot is ONE conversation, not several: two angles
of one podcast each heard both voices. The conversation clock's own
grouping (`conversation_clock.discover_edges` / `group_digests`, word
4-grams agreeing on one offset) finds such a group off M1 alone, and the
group is heard once, from the member with the most words - the clock's
own reference. The other members are recorded as its angles, not laid
on the line, so a reel is never proposed twice from two cameras.

Who speaks
----------
M1 carries words, not voices. The voices come from diarizing the
source's program track (`single_track_diarization.diarize_track`, under
the same eligibility rule the timeline path's single-track fallback
uses - `should_diarize_single_track`), or from the memory's M1b slot
when the identity lane already measured them. Each word is attributed
to the voice whose turn holds its midpoint; a row is cut where the
voice changes. Labels are the diarizer's generic `speaker_NN`: which
cluster is which person is not measured, so it is not guessed. A
declared one-voice project keeps one label - its declared name.

Reuse
-----
The document records each source's content digest. A second call while
the footage is unchanged returns the document on disk and hears
nothing; a changed or added file rebuilds the document, and M1 still
re-hears only the changed file. Diarized voices are carried forward by
digest too.

What it never does
------------------
It never replaces a transcript `timeline_transcript` wrote off a real
cut: the captain's timeline is the authoritative clock, and a document
with a timeline name is returned untouched. And a reel is never BUILT
from a source-clock transcript - its seconds are not a master
timeline's - which `refuse_build_from_source` says by name.

    python3 -m library.tools.source_transcript <project_folder>

`tests/unit/reels/test_source_transcript.py`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from library.tools import source_memory
from library.tools.ren_refusal import RenRefusal

KIND_SOURCE = "source"
"""`derived_from.kind` on a document this module wrote. A timeline-made
document carries no `kind` and a Resolve `timeline` name instead."""

LAYOUT = "sources end to end in footage order"

LOCK_OWNER = "source-transcript"

MEASUREMENT = (
    "Speech heard from the SOURCE footage itself - the per-source "
    "memory's M1 transcript (the hybrid seam through MFA), voices from "
    "diarizing each source's program track. No Resolve timeline exists "
    "or was read. Times are source seconds on a virtual timeline that "
    "lays each source with speech end to end in footage order; a synced "
    "multicam group is one stretch, heard from its member with the most "
    "words.")


class SourceTranscriptRefused(RenRefusal):
    """The footage could not give a transcript."""

    CODE = "REN_SOURCE_TRANSCRIPT_REFUSED"


class BuildNeedsATimeline(RenRefusal):
    """A reel build was handed a transcript on the source clock."""

    CODE = "REN_BUILD_NEEDS_A_TIMELINE"
    USER_MESSAGE = ("These reels were chosen straight from the footage, and "
                    "building them needs the episode on a Resolve master "
                    "timeline first.")


def is_source_derived(document) -> bool:
    """True when this module wrote the document."""
    return (isinstance(document, dict)
            and (document.get("derived_from") or {}).get("kind")
            == KIND_SOURCE)


def refuse_build_from_source(document: dict) -> None:
    """Raise when a reel build is about to read a source-clock transcript.

    Its seconds are source seconds laid end to end, not a master
    timeline's, so placing them on a timeline would cut the wrong
    picture. The proposal stands; what is missing is the cut.
    """
    if not is_source_derived(document):
        return
    raise BuildNeedsATimeline(
        "the reel proposals were selected from a transcript of the "
        "source footage, not of a Resolve master timeline",
        "a build places each reel's seconds on the master timeline, and "
        "these seconds are on the footage's own clock",
        "place the episode on the project's Resolve master timeline, run "
        "`python3 -m library.tools.timeline_transcript <project>` to hear "
        "the cut, then select the reels again with `ren edit <project> "
        "--rerun select_reels` before building")


# ── The footage ──────────────────────────────────────────────────────

def footage(project_folder: str) -> list[dict]:
    """Every footage file with its clip id and live fingerprint.

    The same enumeration step 1.01 and the catalog use, so clip ids
    agree with the rest of the pipeline. Refuses when there is none.
    """
    from library.tools import footage_identity

    try:
        files, _skipped = footage_identity.enumerate_footage(project_folder)
    except FileNotFoundError as missing:
        raise SourceTranscriptRefused(
            f"there is no footage to hear: {missing}",
            "reel selection reads what the footage says, and without a "
            "Resolve timeline transcript the footage is the only source",
            "put the episode under the project's raw/ folder, or declare "
            "where it lives with source.footage_root in project.yaml"
        ) from missing
    if not files:
        raise SourceTranscriptRefused(
            f"the footage folder {footage_identity.footage_root(project_folder)}"
            f" holds no video file",
            "reel selection reads what the footage says",
            "put the episode under the project's raw/ folder, or declare "
            "where it lives with source.footage_root in project.yaml")
    out = []
    for entry in files:
        identity = footage_identity.fingerprint(entry["path"])
        out.append({"clip_id": entry["clip_id"],
                    "source_file": entry["path"],
                    "content_digest": identity["content_digest"],
                    "size_bytes": identity["size_bytes"]})
    return out


def _identity(sources) -> list:
    return [(s["clip_id"], s["source_file"], s["content_digest"])
            for s in sources]


def is_current(document, project_folder: str,
               sources: list[dict] | None = None) -> bool:
    """A source-derived document still describes the footage on disk."""
    if not is_source_derived(document):
        return False
    recorded = (document.get("derived_from") or {}).get("sources") or []
    if sources is None:
        try:
            sources = footage(project_folder)
        except SourceTranscriptRefused:
            return False
    return _identity(recorded) == _identity(sources)


# ── Who speaks ──────────────────────────────────────────────────────

def _label(index: int) -> str:
    from library.tools.single_track_diarization import SPEAKER_LABEL_FORMAT

    return SPEAKER_LABEL_FORMAT.format(index)


def _voices_from_m1b(digest: str) -> list | None:
    """The identity lane's measured voices for this digest, if any."""
    from library.tools import source_memory

    record = source_memory.read_speakers(digest) or {}
    speakers = [s for s in record.get("speakers") or []
                if isinstance(s, dict) and s.get("spans")]
    if not speakers:
        return None
    return [{"label": _label(i + 1),
             "spans": [[float(a), float(b)] for a, b in s["spans"]]}
            for i, s in enumerate(speakers)]


def _measure_voices(project_folder: str, source: dict,
                    previous: dict[str, dict]) -> dict:
    """`{voices, basis}` for one placed source.

    Reused by digest from the previous document, then from M1b, and
    only then measured. A source the project says has one voice (or
    none) is not diarized: there is nothing to separate.
    """
    from library.tools import (
        footage_identity,
        single_track_diarization,
        source_primitives,
    )

    digest = source["content_digest"]
    prior = previous.get(digest)
    if prior and prior.get("voices") is not None:
        return {"voices": prior["voices"], "basis": prior.get("voice_basis")}
    eligible, reason, declared_k = (
        single_track_diarization.should_diarize_single_track(
            project_folder, [None]))
    if not eligible:
        roster = footage_identity.declared_speakers(project_folder) or []
        name = roster[0]["name"] if len(roster) == 1 else _label(1)
        return {"voices": [{"label": name, "spans": None}],
                "basis": f"single label: {reason}"}
    measured = _voices_from_m1b(digest)
    if measured:
        return {"voices": measured,
                "basis": "M1b speakers.json (identity lane)"}
    primitives = source_primitives.ensure(
        source["source_file"],
        footage_identity.declared_program_stream(project_folder))
    if primitives["program_wav"] is None:
        return {"voices": [{"label": _label(1), "spans": None}],
                "basis": "single label: the source has no program track"}
    try:
        diarization = single_track_diarization.diarize_track(
            str(primitives["program_wav"]), num_speakers=declared_k)
    except single_track_diarization.DiarizationUnavailable as exc:
        return {"voices": [{"label": _label(1), "spans": None}],
                "basis": f"single label: diarization is unavailable: {exc}"}
    return {"voices": [{"label": cluster.label,
                        "spans": [[float(a), float(b)]
                                  for a, b in cluster.turns]}
                       for cluster in diarization.clusters],
            "basis": f"diarized ({diarization.method}); {reason}"}


def _voice_of(word: dict, voices: list) -> str:
    """The voice whose turn holds this word's midpoint, else the nearest."""
    if len(voices) == 1 or not any(v.get("spans") for v in voices):
        return voices[0]["label"]
    mid = (float(word["start"]) + float(word["end"])) / 2.0
    best, best_gap = voices[0]["label"], None
    for voice in voices:
        for start, end in voice.get("spans") or []:
            gap = max(start - mid, mid - end, 0.0)
            if best_gap is None or gap < best_gap:
                best, best_gap = voice["label"], gap
            if gap == 0.0:
                return best
    return best


def _rows_by_voice(utterances: list, voices: list,
                   offset: float) -> dict[str, list]:
    """M1 utterances as aligned rows per voice, on the virtual timeline.

    A row is cut where the voice changes inside an utterance. An
    utterance one voice speaks keeps its own text; a cut one is
    re-read from its words.
    """
    rows: dict[str, list] = {}
    for utterance in utterances:
        words = [w for w in utterance.get("words") or []
                 if "start" in w and "end" in w]
        if not words:
            continue
        runs: list = []
        for word in words:
            label = _voice_of(word, voices)
            if runs and runs[-1][0] == label:
                runs[-1][1].append(word)
            else:
                runs.append((label, [word]))
        for label, run in runs:
            text = (utterance.get("text") if len(runs) == 1
                    else " ".join(str(w.get("word", "")) for w in run))
            moved = [{**w, "start": round(float(w["start"]) + offset, 3),
                      "end": round(float(w["end"]) + offset, 3)}
                     for w in run]
            rows.setdefault(label, []).append({
                "start": moved[0]["start"], "end": moved[-1]["end"],
                "text": str(text or "").strip(), "words": moved})
    return rows


# ── The document ─────────────────────────────────────────────────────

def _rate(stream: dict) -> float | None:
    raw = str((stream or {}).get("avg_frame_rate") or "")
    num, _, den = raw.partition("/")
    try:
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _hear(project_folder: str, sources: list[dict]) -> None:
    """M1 for every source that has no fresh one. Heavy, so locked."""
    from library.tools import footage_identity, source_memory

    stale = []
    for source in sources:
        _doc, status = source_memory.read_m1(source["content_digest"])
        fresh = status == "fresh" and source_memory.is_fresh(
            source_memory.read_m0(source["content_digest"]),
            source["source_file"])
        recorded = status.startswith("untranscribed")
        if not (fresh or recorded):
            stale.append(source)
    if not stale:
        return
    from library.tools.heavy_work_lock import heavy_work_lock

    declaration = footage_identity.declared_program_stream(project_folder)
    with heavy_work_lock(LOCK_OWNER):
        for source in stale:
            print(f"  hearing {os.path.basename(source['source_file'])} "
                  f"(per-source memory M1)", file=sys.stderr)
            source_memory.build_source(source["source_file"], declaration)


def _place(sources: list[dict]) -> list[dict]:
    """Which sources go on the line, collapsing multicam groups."""
    from library.tools import conversation_clock, source_memory

    docs, words = {}, {}
    for source in sources:
        doc, status = source_memory.read_m1(source["content_digest"])
        source["transcript_status"] = status
        if status == "fresh" and doc.get("utterances"):
            docs[source["content_digest"]] = doc
            words[source["content_digest"]] = doc.get("word_count") or 0
    edges = conversation_clock.discover_edges(docs) if len(docs) > 1 else {}
    reference_of = {}
    for members in conversation_clock.group_digests(sorted(docs), edges):
        reference = max(members, key=lambda d: (words.get(d, 0), d))
        for member in members:
            reference_of[member] = reference
    placed_digests = set()
    clip_of_digest = {}
    for source in sources:
        digest = source["content_digest"]
        if digest not in docs:
            source["placed"] = False
            source["why_not_placed"] = (
                f"no speech to hear: M1 reads {source['transcript_status']}")
            continue
        clip_of_digest.setdefault(digest, source["clip_id"])
        if reference_of.get(digest, digest) != digest:
            source["placed"] = False
            source["angle_of"] = reference_of[digest]
            source["why_not_placed"] = (
                "another angle of the same conversation (the conversation "
                "clock's word 4-grams agree on one offset); heard from the "
                "angle with the most words")
            continue
        if digest in placed_digests:
            source["placed"] = False
            source["why_not_placed"] = (
                f"the same file as {clip_of_digest[digest]} (one content "
                f"digest)")
            continue
        placed_digests.add(digest)
        source["placed"] = True
    return [s for s in sources if s.get("placed")]


@source_memory._project_scoped
def build(project_folder: str, sources: list[dict] | None = None,
          previous: dict | None = None) -> dict:
    """The transcript document, heard from the footage. Writes nothing."""
    from library.tools import source_memory, timeline_transcript
    from library.tools.timeline_ingest import TimelineClip, TimelineSnapshot

    sources = [dict(s) for s in (sources or footage(project_folder))]
    _hear(project_folder, sources)
    placed = _place(sources)
    if not placed:
        raise SourceTranscriptRefused(
            "no footage file has speech to select reels from: "
            + "; ".join(f"{s['clip_id']} {s.get('why_not_placed', '')}"
                        for s in sources),
            "a reel is chosen from what is said, and the per-source "
            "hearing found nothing said in any file",
            "check the episode has audio (`ren check <project>`), or "
            "declare its program track with source.program_stream in "
            "project.yaml")

    prior_by_digest = {
        s["content_digest"]: s
        for s in ((previous or {}).get("derived_from") or {}).get(
            "sources") or []
        if isinstance(s, dict) and s.get("content_digest")}

    clips, rows_by_speaker, heard_by = [], {}, {}
    cursor, fps, width, height = 0.0, None, 0, 0
    for source in placed:
        digest = source["content_digest"]
        m1, _status = source_memory.read_m1(digest)
        m0 = source_memory.read_m0(digest) or {}
        duration = float(m0.get("duration_seconds") or 0.0)
        utterances = m1.get("utterances") or []
        if duration <= 0.0:
            duration = max(float(u["end"]) for u in utterances)
        video = (m0.get("video_streams") or [{}])[0]
        if fps is None:
            fps = _rate(video)
            width, height = int(video.get("width") or 0), int(
                video.get("height") or 0)
        voices = _measure_voices(project_folder, source, prior_by_digest)
        source["voices"] = voices["voices"]
        source["voice_basis"] = voices["basis"]
        source["timeline_start"] = round(cursor, 3)
        source["timeline_end"] = round(cursor + duration, 3)
        rate = fps or 30.0
        clips.append(TimelineClip(
            resolve_item_id=f"source:{source['clip_id']}",
            track_type="video", track_index=1, track_name="source",
            speaker=None, source_file=source["source_file"],
            source_in=0.0, source_out=duration,
            source_in_frame=0, source_out_frame=round(duration * rate),
            source_frames=None, timeline_start=cursor,
            timeline_end=cursor + duration,
            name=os.path.basename(source["source_file"]), fps=rate))
        for label, rows in _rows_by_voice(utterances, voices["voices"],
                                          cursor).items():
            rows_by_speaker.setdefault(label, []).extend(rows)
            heard_by.setdefault(label, dict(m1.get("instrument") or {}))
        cursor += duration

    fps = fps or 30.0
    snapshot = TimelineSnapshot(
        project_name=os.path.basename(os.path.abspath(project_folder)),
        timeline_name="", fps=fps, reported_fps=fps, width=width,
        height=height, start_frame=0, end_frame=round(cursor * fps),
        clips=tuple(clips))
    merged = []
    for label, rows in rows_by_speaker.items():
        merged.extend(timeline_transcript.segments_for_speaker(
            {"segments": rows}, label, clips))
    merged.sort(key=lambda s: (s.timeline_start, s.timeline_end))
    document = timeline_transcript.transcript_document(
        snapshot, merged,
        transcription=timeline_transcript.transcription_record(heard_by),
        mic_bleed_resolution=[],
        diarization={"path": "source-diarization",
                     "reason": "; ".join(sorted({s["voice_basis"]
                                                 for s in placed}))},
        project_folder=project_folder)
    document["derived_from"].update({
        "kind": KIND_SOURCE, "timeline": None, "layout": LAYOUT,
        "sources": sources})
    document["measurement"] = MEASUREMENT
    from library.tools import transcript_corrections
    transcript_corrections.apply_to_document(document, project_folder)
    return document


@source_memory._project_scoped
def ensure(project_folder: str) -> dict:
    """The project's transcript, heard from source when it has none.

    A transcript a Resolve timeline produced is returned untouched. A
    source-derived one that still describes the footage is returned
    without hearing anything. Otherwise the document is built from the
    footage and written where `timeline_transcript.transcript_path`
    says - the one place every reader looks.
    """
    from library.tools import source_memory, timeline_transcript

    path = timeline_transcript.transcript_path(project_folder)
    existing = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if existing is not None and not is_source_derived(existing):
            return existing
    sources = footage(project_folder)
    if existing is not None and is_current(existing, project_folder,
                                           sources):
        return existing
    document = build(project_folder, sources, previous=existing)
    source_memory.write_json(Path(path), document)
    print(f"  transcript heard from source: {document['segment_count']} "
          f"segments, speakers {document['speakers']} -> {path}",
          file=sys.stderr)
    return document


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Transcribe a project's footage for reel selection, "
                    "with no Resolve timeline.")
    parser.add_argument("project_folder")
    args = parser.parse_args(argv)
    try:
        document = ensure(args.project_folder)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        return REFUSAL_EXIT_CODE
    print(json.dumps({"segments": document["segment_count"],
                      "speakers": document["speakers"],
                      "kind": (document.get("derived_from") or {}).get(
                          "kind") or "timeline"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
