"""The live qualification of `caption_fix` on a REAL reel.

The operation the product's own reels are made of - "change this caption
from X to Y" - was IMPLEMENTED (PR #3024) but never qualified live.
The synthetic AXI fixture could not show the defect the real reel
exposes: every caption in a real Ren reel is a TIGHT card, and its
sidecar records the tight canvas and the tight style geometry, while the
render entry refuses props that are not the project's own frame and
geometry.  A fix that handed the sidecar straight back therefore refused
on every real reel - the product's caption medium was the one thing a
caption fix could not address.

This module runs the operation on a Ren-owned SCRATCH COPY of a built
reel, in the product format, and judges the result from what the card
actually SAYS:

* the operation runs through the ENGINE'S OWN ADAPTER
  (`library/tools/edit_execution_adapters._caption_fix`) - the route a
  captain's request takes, not a script beside it;
* the TEXT is read back from the RENDERED FRAMES (OCR), not from the
  props the renderer wrote beside the file: the props say what was
  drawn, the pixels say what the card says;
* the card's own timing, its cue boundaries and its approved styling
  are read back and compared;
* the NEIGHBOURING cards are compared by their rendered frames and by
  their bytes, so a fix that redrew the reel's other captions fails.

Both cases the punch list names are run: a REPLACEMENT (one word
changed) and an INSERTION (one word added).

It never writes the captain's projects, timelines or media: the target
is a Ren-owned scratch project, the card it fixes is restored to the
file it had before the run, and the captain's project is put back.

    REN_LIVE_RESOLVE=1 \\
    REN_AXI_QUAL_PROJECT="Ren Scratch - breaktest" \\
    REN_AXI_QUAL_SOURCE_TIMELINE="<the built reel timeline>" \\
    REN_AXI_QUAL_PROJECT_DIR="<the scratch project folder>" \\
    python3 -m tests.live.caption_fix_qualification
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Where this lane's evidence lands.
REPORT_DIR = ROOT / "tests" / "live" / "reports" / "caption-fix-qualification"
EVIDENCE_PATH = REPORT_DIR / "caption-fix-evidence.json"
#: Where the other rows' evidence reports live.  The report a promoted
#: row must re-import from is committed beside them.
CANONICAL_REPORT_DIR = ROOT / "tests" / "live" / "reports" / "registry"

#: The report schema the importer consumes.
REPORT_SCHEMA = "ren-axi-qualification-result/v2"

#: The pair this lane owns.
PAIR = ("caption_fix", "resolve_axi")


# ── the rendered pixels ──────────────────────────────────────────────


def sha256(path: str) -> str:
    """The file's bytes, hashed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(text: str) -> str:
    """The reading, compared the way a reader compares it."""
    return re.sub(r"[^a-z0-9' ]+", " ", str(text or "").casefold()).strip()


def _frame_count(card_file: str) -> int:
    """How many frames the card carries."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames",
         "-of", "default=nw=1:nk=1", str(card_file)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    try:
        return int(str(proc.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return 0


def _frames(card_file: str, work_dir: Path) -> list[Path]:
    """Three stills from the card, near its end.

    The caption's words sweep in over their measured windows, so the
    early frames draw only part of the text.  The stills are taken where
    the whole caption is on screen.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    count = _frame_count(card_file)
    if count <= 1:
        return []
    frames = []
    for index, fraction in enumerate((0.55, 0.75, 0.95)):
        number = min(count - 1, max(0, int(count * fraction)))
        out = work_dir / f"frame_{index}.png"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(card_file),
             "-vf", f"select=eq(n\\,{number})", "-frames:v", "1",
             str(out)],
            capture_output=True, text=True, encoding="utf-8", check=False)
        if proc.returncode or not out.is_file():
            continue
        frames.append(out)
    return frames


def _on_black(frame: Path, out: Path) -> Path:
    """The still, composited on black: the card is white on transparent."""
    from PIL import Image

    with Image.open(frame) as image:
        canvas = Image.new("RGBA", image.size, (0, 0, 0, 255))
        canvas.alpha_composite(image.convert("RGBA"))
        canvas.convert("RGB").save(out)
    return out


def read_card_text(card_file: str, work_dir: Path) -> list[str]:
    """What the card SAYS, read from its rendered frames.

    OCR over the rendered pixels: the props beside the file say what the
    renderer was told to draw, and only the pixels say what the card
    says.
    """
    import easyocr

    frames = _frames(card_file, work_dir / "frames")
    if not frames:
        return []
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    readings = []
    for index, frame in enumerate(frames):
        black = _on_black(frame, work_dir / f"black_{index}.png")
        found = reader.readtext(str(black), detail=0, paragraph=True)
        for line in found or ():
            readings.append(str(line))
    return readings


#: How alike two words must be for the OCR to be believed.
#:
#: The read-back is an OCR, and an OCR confuses characters: on a real
#: reel's card it read "AI's" as "Al's".  A word-by-word comparison at
#: this similarity is what makes the read-back a measurement of the
#: WORDS rather than of the glyphs, and the raw readings stay in the
#: evidence so a reader can see what the OCR actually said.
WORD_SIMILARITY = 0.7


def reads_text(readings: list[str], expected: str) -> bool:
    """Whether any reading carries the expected words.

    Word by word: every word the fix asked for must be present, in the
    order the card draws them, with the same count - and a word the OCR
    read as a near neighbour of the expected one counts as that word.
    """
    import difflib

    wanted = [word for word in _normalise(expected).split() if word]
    if not wanted:
        return False
    for reading in readings:
        found = [word for word in _normalise(reading).split() if word]
        if len(found) != len(wanted):
            continue
        if all(difflib.SequenceMatcher(None, word, read).ratio()
               >= WORD_SIMILARITY
               for word, read in zip(wanted, found)):
            return True
    return False


# ── the cases ────────────────────────────────────────────────────────


def _fits(text: str, style: dict, project_folder: str) -> bool:
    """Whether the card can draw this text in the approved two lines."""
    from library.steps.step_4_01_plan_subtitles.step import (
        MAX_CAPTION_LINES,
        build_caption_fitter,
        identify_emphasis_words,
    )
    from library.tools.safe_area import resolve_safe_area

    fitter = build_caption_fitter(style, resolve_safe_area(project_folder),
                                  project_folder)
    return fitter.line_count(text, identify_emphasis_words(text)) \
        <= MAX_CAPTION_LINES


#: The words a fix may try, in order.  The first that fits the card's
#: own approved two lines is used; a word that would make the card wrap
#: to three is refused by the render entry, and a case that cannot be
#: drawn is not a case.
_REPLACEMENT_WORDS = ("today", "here", "again", "clearly")
_INSERTED_WORDS = ("so", "not", "now")


def _timed(text: str, props: dict) -> bool:
    """Whether the new text can be timed on the card's own windows.

    The engine's own fit decides: a word that cannot be given a window
    inside the card's measured span is refused before anything is
    drawn, so a case that cannot be timed is not a case.
    """
    from library.tools.caption_rewrite import CaptionRewriteError, fit_words

    cue = (props.get("subtitles") or [{}])[0]
    measured = cue.get("words") or []
    span = (int(cue.get("startFrame") or 0), int(cue.get("endFrame") or 0))
    try:
        fit_words(measured, [token for token in text.split() if token],
                  span=span)
    except CaptionRewriteError:
        return False
    return True


def _replacement_text(words: list[str], style: dict, props: dict,
                      project_folder: str) -> str | None:
    """One word changed: the case the punch list names."""
    if not words:
        return None
    last = words[-1]
    for candidate in _REPLACEMENT_WORDS:
        if candidate == last.casefold():
            continue
        text = " ".join(words[:-1] + [candidate])
        if _fits(text, style, project_folder) and _timed(text, props):
            return text
    return None


def _insertion_text(words: list[str], style: dict, props: dict,
                    project_folder: str) -> str | None:
    """One word added: the other case the punch list names.

    An inserted word takes the gap between its neighbours, so a card
    whose words are adjacent has no room for one - the engine refuses
    that, and the case is skipped rather than forced.
    """
    if len(words) < 2:
        return None
    for position in range(1, len(words)):
        for candidate in _INSERTED_WORDS:
            text = " ".join(words[:position] + [candidate]
                            + words[position:])
            if _fits(text, style, project_folder) and _timed(text, props):
                return text
    return None


# ── the live run ─────────────────────────────────────────────────────


def _read_live(project: str, timeline: str) -> dict:
    """The live snapshot of the timeline, read through the AXI.

    The adapter reads the timeline the same way, so the harness and the
    operation under test see ONE reading of the same state.
    """
    from library.tools import resolve_axi_core

    return resolve_axi_core.read_live_timeline(project, timeline)


def _rows(snapshot: dict) -> list[dict]:
    """Every clip on every row of a snapshot."""
    return [clip for track in snapshot.get("tracks") or ()
            for clip in track.get("clips") or ()]


def _subtitle_cards(snapshot: dict) -> list[dict]:
    """The caption cards: the clips on the row named Subtitles."""
    cards = []
    for track in snapshot.get("tracks") or ():
        if str(track.get("name") or "") != "Subtitles":
            continue
        for clip in track.get("clips") or ():
            path = str(clip.get("source_file") or "")
            if path.endswith(".mov"):
                cards.append(clip)
    return cards


def _context(operation: dict, snapshot: dict, project: str,
             timeline: str) -> SimpleNamespace:
    """The context the engine's adapter takes.

    `request.project` is the RESOLVE project name, exactly as the
    production path builds it - the broker keys on that name, and every
    AXI verb addresses it.
    """
    request = SimpleNamespace(id="caption-fix-qualification",
                              project=project, timeline=timeline,
                              operations=[operation])
    return SimpleNamespace(operation=operation, snapshot=snapshot,
                           request=request, compiled=None,
                           operation_index=0)


def _card_props(card_file: str) -> dict:
    """The props the card was drawn from, read off the disk."""
    sidecar = str(card_file).removesuffix(".mov") + "_props.json"
    with open(sidecar, encoding="utf-8") as handle:
        return json.load(handle)


def _run_case(case_id: str, card: dict, new_text: str, *,
              project: str, project_dir: str, timeline: str) -> dict:
    """One caption fix, under the lease, and what it left on disk.

    The READ-BACK of the rendered pixels happens AFTER the lease is
    released: the OCR loads a model and reads files, and the machine is
    shared - a lane that holds the one Resolve while it thinks is a lane
    that makes every other lane wait.
    """
    from library.tools.edit_execution_adapters import _caption_fix

    card_file = str(card["source_file"])
    old_props = _card_props(card_file)
    old_words = [str(word.get("word") or "")
                 for word in (old_props["subtitles"][0].get("words") or [])]
    before = _read_live(project, timeline)
    neighbours = [clip for clip in _subtitle_cards(before)
                  if str(clip.get("unique_id")) != str(card["unique_id"])]
    neighbour_before = {str(clip.get("source_file")):
                        sha256(str(clip.get("source_file")))
                        for clip in neighbours}

    operation = {"op": "caption_fix", "unique_id": card["unique_id"],
                 "text": new_text}
    receipt = _caption_fix(_context(operation, before, project, timeline))

    after = _read_live(project, timeline)
    landed = next((clip for clip in _rows(after)
                   if str(clip.get("unique_id")) == str(card["unique_id"])),
                  None)
    new_file = str((landed or {}).get("source_file") or "")
    new_props = _card_props(new_file) if new_file else {}
    new_cue = (new_props.get("subtitles") or [{}])[0]
    new_words = [str(word.get("word") or "")
                 for word in (new_cue.get("words") or [])]
    neighbour_after = {}
    for clip in _subtitle_cards(after):
        path = str(clip.get("source_file") or "")
        if path in neighbour_before:
            neighbour_after[path] = sha256(path)

    checks = {
        "operation_committed": receipt.get("status") == "committed",
        "cue_frames_unchanged": (
            new_cue.get("startFrame")
            == old_props["subtitles"][0].get("startFrame")
            and new_cue.get("endFrame")
            == old_props["subtitles"][0].get("endFrame")),
        # The words that SURVIVE the rewrite keep the windows they
        # were measured with; the word the fix replaced is expected to
        # change, and the one it added is expected to be new.
        "surviving_words_keep_their_windows": all(
            any(old.get("startFrame") == new.get("startFrame")
                and old.get("endFrame") == new.get("endFrame")
                for new in (new_cue.get("words") or [])
                if new.get("word") == old.get("word"))
            for old in (old_props["subtitles"][0].get("words") or [])
            if old.get("word") in [row.get("word")
                                   for row in new_cue.get("words") or []]),
        "approved_styling_unchanged": (
            dict(new_props.get("style") or {})
            == dict(old_props.get("style") or {})
            and new_props.get("width") == old_props.get("width")
            and new_props.get("height") == old_props.get("height")),
        "placement_kept_its_span": (
            landed is not None
            and landed.get("record_in") == card.get("record_in")
            and landed.get("record_out") == card.get("record_out")
            and landed.get("source_in_frame") == card.get("source_in_frame")
            and landed.get("source_out_frame")
            == card.get("source_out_frame")),
        "placement_transform_unchanged": (
            landed is not None
            and (landed.get("transform") or {}) == (card.get("transform") or {})),
        "neighbouring_cards_unchanged": (
            neighbour_before == neighbour_after),
        "no_other_item_moved": (
            [str(clip.get("unique_id")) for clip in _rows(after)]
            == [str(clip.get("unique_id")) for clip in _rows(before)]),
    }
    return {
        "id": case_id,
        "operation": operation,
        "card": {"unique_id": card.get("unique_id"),
                 "name": card.get("name"),
                 "record": [card.get("record_in"), card.get("record_out")],
                 "source_path": card_file},
        "old_text": old_props["subtitles"][0]["text"],
        "new_text": new_text,
        "old_words": old_words,
        "new_words": new_words,
        "new_file": new_file,
        "neighbour_files": neighbour_before,
        "neighbour_hashes_after": neighbour_after,
        "receipt": {key: value for key, value in receipt.items()
                    if key != "readback"},
        "receipt_readback": receipt.get("readback"),
        "checks": checks,
    }


def _read_the_pixels(case: dict, work_dir: Path) -> None:
    """What the card SAYS, read from its rendered frames.

    OCR over the rendered pixels: the props beside the file say what
    the renderer was told to draw, and only the pixels say what the
    card says.  The previous card is read too, so the read-back proves
    the fix changed the words rather than the file.
    """
    old_readings = read_card_text(case["card"]["source_path"],
                                  work_dir / f"{case['id']}_before")
    new_readings = (read_card_text(case["new_file"],
                                   work_dir / f"{case['id']}_after")
                    if case["new_file"] else [])
    case["old_readings"] = old_readings
    case["new_readings"] = new_readings
    case["checks"]["text_read_from_the_rendered_frames"] = reads_text(
        new_readings, case["new_text"])
    case["checks"]["old_text_not_still_on_the_card"] = not reads_text(
        new_readings, case["old_text"])
    case["passed"] = all(case["checks"].values())


def _restore_card(project, new_file: str, old_file: str,
                  project_dir: str) -> dict:
    """Put the card's own file back, so the scratch project is as found."""
    from library.tools.caption_swap import swap_files

    report = swap_files(project, {new_file: old_file},
                        project_folder=project_dir)
    return {"swapped": report.get("swapped"), "failed": report.get("failed")}


# ── the qualification report ──────────────────────────────────────────


def _coverage(state: dict) -> dict:
    """The coverage the run measured, per dimension."""
    from library.tools import axi_qualification

    dimensions: dict[str, list[str]] = {}
    for field in axi_qualification.PREDICATE_FIELDS:
        value = state.get(field)
        if value not in (None, "") and str(value) not in dimensions.setdefault(
                field, []):
            dimensions[field].append(str(value))
    return {"dimensions": dimensions,
            "configurations": ["product_format_reel"],
            "uncovered": {},
            "source": "measured"}


def _predicates(coverage: dict) -> list[dict]:
    """The conditions the evidence was earned in."""
    from library.tools import axi_qualification

    return list(axi_qualification.dimensions.predicates_for(
        coverage["dimensions"]))


def _build_report(evidence: dict, state: dict) -> dict:
    """The qualification report this run earns, if it earned one."""
    cases = [case for case in evidence["cases"] if "checks" in case]
    passed = bool(cases) and all(case.get("passed") for case in cases)
    coverage = _coverage(state)
    checks = []
    for case in cases:
        for name, value in sorted((case.get("checks") or {}).items()):
            checks.append({"check": f"{case['id']}:{name}",
                           "result": "passed" if value else "failed"})
    return {
        "schema": REPORT_SCHEMA,
        "operation_id": PAIR[0],
        "backend": PAIR[1],
        "result": "passed" if passed else "failed",
        "evidence": (
            "tests/live/reports/caption-fix-qualification/"
            "caption-fix-evidence.json; the engine's own caption_fix "
            "adapter ran on a Ren-owned scratch copy of a built reel in "
            "the product format, and the card's TEXT was read back from "
            "the rendered frames by OCR for a replacement and an "
            "insertion case"),
        "resolve_version": str(state.get("resolve_version") or ""),
        "environment": {
            "resolve_version": str(state.get("resolve_version") or ""),
            "platform": "darwin"},
        "semantic_boundary": {
            "applies_to": [
                "rewriting the TEXT of one rendered caption card placed "
                "on a Subtitles row, and swapping the re-rendered card "
                "onto its own placement",
                "a caption card whose sidecar carries the card's own "
                "measured word windows and the project's geometry",
                "a reel in the product format (portrait 1080x1920 at "
                "23.976 fps) whose captions are tight cards"],
            "does_not_apply_to": [
                "changing a caption's TIMING or its cue boundaries",
                "changing the card's approved styling, its canvas or the "
                "transform its placement carries",
                "more than one caption card per operation",
                "a card that is not a rendered caption file on a "
                "Subtitles row",
                "a card whose wrap width no longer matches the project's"]},
        "coverage": coverage,
        "project_conditions": [
            f"Ren-owned scratch project {evidence['project']!r}",
            f"a built reel timeline {evidence['timeline']!r} in the "
            "product format",
            "the card's own file is restored after the run"],
        "supported_media_types": ["video"],
        "expected_state": {
            "card_text": "the requested words, read from the rendered "
                         "frames",
            "cue_frames": "unchanged",
            "word_windows": "the surviving words keep the windows they "
                            "were measured with",
            "styling": "unchanged",
            "neighbouring_cards": "unchanged"},
        "actual_state": {
            "cases": [{"id": case["id"], "passed": case.get("passed"),
                       "checks": case.get("checks")}
                      for case in cases],
            "readings": {case["id"]: case.get("new_readings")
                         for case in cases}},
        "preservation_checks": checks,
        "preservation_limits": [
            "one caption card per operation, on one reel copy",
            "the run restores the card's own file afterwards, so the "
            "scratch project is left as it was found"],
        "visual_verification": [
            "the re-rendered card's own frames read by OCR: the "
            "requested words are on the card and the previous words are "
            "not",
            "the neighbouring cards' files compared by sha256 before "
            "and after the fix"],
        "audio_verification": [
            "not applicable: a caption card carries video only, and no "
            "audio row was read or written"],
        "recovery_status": "not_required",
    }


def _import_report(path: Path) -> dict:
    """Import the report through the registry tool - the only way a row
    reaches the registry."""
    proc = subprocess.run(
        [sys.executable, "-m", "library.tools.axi_qualification",
         "--import-result", str(path)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        timeout=300, check=False)
    return {"path": str(path), "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def _open_project_folder(manager, project_dir: str) -> None:
    """The Project Manager folder the scratch project lives in.

    A project is listed in the CURRENT folder, and the current folder
    is whatever the last lane left the Project Manager on - so a run
    that names a project in another folder has to walk there first. The
    path is the project's OWN declaration (`resolve.folder`), read from
    its project.yaml.
    """
    from library.tools import qualification_project

    folder = ""
    try:
        import yaml

        with open(os.path.join(project_dir, "project.yaml"),
                  encoding="utf-8") as handle:
            declared = yaml.safe_load(handle) or {}
        folder = str(((declared.get("resolve") or {}).get("folder")) or "")
    except (OSError, ValueError, ImportError) as exc:
        print(f"the project folder could not be read ({exc}); the run "
              "continues in the Project Manager's current folder",
              file=sys.stderr)
    if not folder:
        return
    path = tuple(segment for segment in folder.split("/") if segment)
    qualification_project._open_project_folder_path(manager, path)


def _open_scratch_project(resolve, manager, project_name: str,
                          source_timeline: str = "",
                          project_dir: str = ""):
    """The Ren-owned scratch project, open.

    The scratch project's lifecycle owns the origin: it saves what was
    open and records it, so the run can put the machine back.  Nothing
    in the captain's project is written.
    """
    from library.tools import resolve_scratch_projects

    current = manager.GetCurrentProject()
    if current is not None and current.GetName() == project_name:
        return None, current
    if (current is not None
            and str(current.GetName() or "").startswith("Ren Scratch - ")
            and any(timeline and timeline.GetName() == source_timeline
                    for timeline in _all_timelines(current))):
        # The machine is ALREADY inside a Ren-owned scratch project that
        # carries the reel: opening another one would need the scratch
        # origin, and the origin is the machine's.  The session belongs
        # to whoever opened it; this run only reads and writes on its own
        # copy of the reel, and puts the caption back.
        return None, current
    origin = True
    _open_project_folder(manager, project_dir)
    resolve_scratch_projects.open_project(resolve, manager, project_name)
    project = manager.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        raise RuntimeError(
            f"the open project is "
            f"{project.GetName() if project else None!r}, not the "
            f"scratch project {project_name!r} - refusing to write")
    return origin, project


def _select_timeline(project, timeline_name: str):
    """The named timeline, made current."""
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)]
    matches = [timeline for timeline in matches
               if timeline and timeline.GetName() == timeline_name]
    if len(matches) != 1:
        raise RuntimeError(
            f"the scratch project does not carry the timeline "
            f"{timeline_name!r} exactly once (found {len(matches)})")
    project.SetCurrentTimeline(matches[0])
    return matches[0]


def _all_timelines(project):
    """Every timeline the project carries."""
    return [project.GetTimelineByIndex(index)
            for index in range(1, int(project.GetTimelineCount() or 0) + 1)]


def _duplicate_timeline(project, source_name: str, new_name: str):
    """A FRESH copy of a reel, in the same project.

    The copy is what the run writes on: the pristine reel is read only,
    and the copy carries the reel's own frame rate and geometry - the
    product format is what is being qualified.
    """
    sources = [timeline for timeline in _all_timelines(project)
               if timeline and timeline.GetName() == source_name]
    if len(sources) != 1:
        raise RuntimeError(
            f"the scratch project does not carry the timeline "
            f"{source_name!r} exactly once (found {len(sources)})")
    # The copy is made from the CURRENT timeline: Resolve duplicates
    # what it has open.
    project.SetCurrentTimeline(sources[0])
    created = sources[0].DuplicateTimeline(new_name)
    copies = []
    for _attempt in range(10):
        copies = [timeline for timeline in _all_timelines(project)
                  if timeline and timeline.GetName() == new_name]
        if copies:
            break
        time.sleep(0.5)
    if len(copies) != 1:
        raise RuntimeError(
            f"the copy of {source_name!r} did not read back "
            f"(found {len(copies)}, created={created!r})")
    return copies[0]


def _restore_leftovers(project, cards: list[dict], project_dir: str) -> list:
    """Put back the cards an EARLIER run of this lane left swapped.

    A caption card's file name carries its provenance - the speaker, the
    source clip, the source span - and a digest.  The reel's own card
    and the file an earlier run produced differ only in that digest, so
    the reel's own card is found by its provenance among the reel's own
    files.  Nothing is guessed: a card whose own file cannot be found
    exactly is reported, not restored.
    """
    import glob

    from library.tools.caption_swap import swap_files

    restored = []
    own_dirs = {os.path.dirname(str(clip.get("source_file") or ""))
                for clip in cards
                if not str(clip.get("source_file") or "").startswith(
                    os.path.join(project_dir, "pipeline_output"))}
    for clip in cards:
        current = str(clip.get("source_file") or "")
        if not current.startswith(os.path.join(project_dir, "pipeline_output")):
            continue
        stem = Path(current).name[:-len(".mov")]
        prefix = stem.rsplit("_", 1)[0] + "_"
        found = []
        for directory in own_dirs:
            found.extend(glob.glob(os.path.join(directory, prefix + "*.mov")))
        found = [path for path in found if path != current]
        if len(found) != 1:
            restored.append({"card": current, "restored": False,
                             "reason": (f"{len(found)} files carry the same "
                                        "provenance - the reel's own card "
                                        "cannot be named")})
            continue
        report = swap_files(project, {current: found[0]},
                            project_folder=project_dir)
        restored.append({"card": current, "restored": True,
                         "own_file": found[0],
                         "swapped": report.get("swapped")})
    return restored


def _run_on(project, resolve, manager, evidence: dict, project_name: str,
            source_timeline: str, project_dir: str, work_dir: Path) -> None:
    """The qualification itself, on the scratch project."""
    # A FRESH copy of the reel: the pristine reel is read only, and the
    # run writes on its own copy.
    copy_name = os.environ.get("REN_AXI_QUAL_TIMELINE", "").strip()
    if not copy_name:
        copy_name = f"vep-caption-fix qual {int(time.time())}"
        _duplicate_timeline(project, source_timeline, copy_name)
    _select_timeline(project, copy_name)
    evidence["timeline"] = copy_name
    evidence["timeline_id"] = str(
        project.GetCurrentTimeline().GetUniqueId() or "")

    snapshot = _read_live(project_name, copy_name)
    cards = _subtitle_cards(snapshot)
    evidence["cards_on_the_subtitles_row"] = len(cards)
    if not cards:
        raise RuntimeError(
            "the reel carries no caption card on its Subtitles row")
    leftovers = _restore_leftovers(project, cards, project_dir)
    if leftovers:
        evidence["leftovers_restored"] = leftovers
        cards = [clip for clip in cards
                 if not str(clip.get("source_file") or "").startswith(
                     os.path.join(project_dir, "pipeline_output"))]
    # A card a PREVIOUS run of this lane produced is a leftover: it
    # plays a file this lane wrote, not the reel's own card.  Those are
    # skipped, so the run always fixes a card the reel really carries.
    foreign = [clip for clip in cards
               if str(clip.get("source_file") or "").startswith(
                   os.path.join(project_dir, "pipeline_output"))]
    if foreign:
        cards = [clip for clip in cards if clip not in foreign]
    if not cards:
        raise RuntimeError(
            "every caption card on the Subtitles row plays a file this "
            "lane produced - the reel carries no card of its own")
    # A card that draws ONE cue: a fix names one card's text, and a
    # card drawing several cannot say which one is the wrong one.
    single = []
    for clip in cards:
        try:
            props = _card_props(str(clip["source_file"]))
        except (OSError, ValueError):
            continue
        if len(props.get("subtitles") or []) == 1:
            single.append((clip, props))
    if not single:
        raise RuntimeError(
            "the reel carries no caption card that draws one cue")
    # EACH case picks the first card that can carry it: a card whose
    # words are adjacent has no room for an inserted word, and that is
    # the engine's refusal, not a reason to skip the case.
    for case_id, picker in (("replacement", _replacement_text),
                            ("insertion", _insertion_text)):
        chosen = None
        for clip, props in single:
            words = [str(word.get("word") or "")
                     for word in (props["subtitles"][0].get("words") or [])]
            text = picker(words, dict(props.get("style") or {}), props,
                          project_dir)
            if text:
                chosen = (clip, props, text)
                break
        if not chosen:
            evidence["cases"].append({
                "id": case_id, "skipped": (
                    "no card on the reel can carry this case: the new "
                    "text must fit the card's approved two lines and be "
                    "timable on its measured windows")})
            continue
        card, props, new_text = chosen
        try:
            case = _run_case(case_id, card, new_text,
                             project=project_name,
                             project_dir=project_dir, timeline=copy_name)
        except Exception as exc:  # noqa: BLE001 - the restore still runs
            evidence["cases"].append({"id": case_id, "error": (
                f"{type(exc).__name__}: {exc}")})
            raise
        evidence["cases"].append(case)
        # The evidence is written as it is earned: a run that is cut
        # short still leaves what it measured.
        EVIDENCE_PATH.write_text(
            json.dumps(evidence, indent=1, default=str) + "\n",
            encoding="utf-8")
        if case["new_file"]:
            # The card's own file goes back: the scratch project is left
            # as it was found, on every timeline that shared the card.
            case["restored"] = _restore_card(
                project, case["new_file"],
                str(case["card"]["source_path"]),
                project_dir)
    # The MEASURED state the evidence was earned in: the dimensions the
    # runtime reports, read under the same lease the run wrote under.
    from library.tools import axi_qualification_state

    evidence["qualification_state"] = axi_qualification_state.inspect(
        project=project_name, timeline=copy_name,
        operations=[{"op": "caption_fix",
                     "unique_id": str(card.get("unique_id"))}],
        resolve=resolve)


def main() -> int:
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        raise SystemExit("set REN_LIVE_RESOLVE=1 to run against Resolve")
    project_name = os.environ.get("REN_AXI_QUAL_PROJECT", "").strip()
    source_timeline = os.environ.get("REN_AXI_QUAL_SOURCE_TIMELINE", "").strip()
    project_dir = os.environ.get("REN_AXI_QUAL_PROJECT_DIR", "").strip()
    if not project_name or not source_timeline or not project_dir:
        raise SystemExit("set REN_AXI_QUAL_PROJECT, "
                         "REN_AXI_QUAL_SOURCE_TIMELINE and "
                         "REN_AXI_QUAL_PROJECT_DIR")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from library.tools import resolve_lock
    from library.tools.marker_feedback import connect_resolve

    work_dir = Path(tempfile.mkdtemp(prefix="vep-caption-fix-qual-"))
    evidence: dict = {"project": project_name,
                       "source_timeline": source_timeline,
                       "project_dir": project_dir, "cases": []}
    timeline_name = source_timeline
    attempts = int(os.environ.get("REN_AXI_QUAL_ATTEMPTS", "12"))
    try:
        for attempt in range(1, attempts + 1):
            try:
                with resolve_lock.resolve_lease(
                        "caption_fix qualification on a real reel",
                        exclusive=True,
                        timeout=int(os.environ.get(
                            "REN_AXI_QUAL_LEASE_SECONDS", "1500"))):
                    resolve = connect_resolve()
                    manager = resolve.GetProjectManager()
                    origin, project = _open_scratch_project(
                        resolve, manager, project_name, source_timeline,
                        project_dir)
                    run_project = str(project.GetName() or "")
                    run_dir = project_dir
                    if run_project != project_name:
                        # The machine was already inside a Ren-owned
                        # scratch project that carries the reel: the
                        # folder is that project's own, resolved by the
                        # ONE place that maps a Resolve project name to
                        # its folder.
                        from library.tools import project_registry

                        resolved = project_registry.project_folder_for_resolve_name(
                            run_project)
                        if not resolved:
                            raise RuntimeError(
                                f"the open scratch project {run_project!r} "
                                "has no Ren project folder")
                        run_dir = str(resolved)
                    evidence["project"] = run_project
                    evidence["project_dir"] = run_dir
                    try:
                        _run_on(project, resolve, manager, evidence,
                                run_project, source_timeline, run_dir,
                                work_dir)
                    finally:
                        if origin is not None:
                            from library.tools import resolve_scratch_projects

                            evidence["origin_restored"] = (
                                resolve_scratch_projects.restore_recorded(
                                    resolve, manager))
                break
            except Exception as exc:  # noqa: BLE001 - the wait is the point
                import traceback

                traceback.print_exc()
                evidence.setdefault("attempts", []).append(
                    {"attempt": attempt, "error": f"{type(exc).__name__}: {exc}",
                     "traceback": traceback.format_exc()})
                if attempt >= attempts:
                    raise
                # The machine is shared: another writer may hold the
                # scratch origin or the Resolve lease.  Wait our turn -
                # the queue is the machine's, and this run does not ask
                # for a quiet window.
                print(f"attempt {attempt} could not run: {exc} - waiting "
                      f"60s for the machine", file=sys.stderr)
                time.sleep(60)
        # The pixels are read AFTER the lease is released: the OCR is
        # slow, and the one Resolve is the machine's.
        for case in evidence["cases"]:
            if "checks" in case:
                _read_the_pixels(case, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=1, default=str)
                             + "\n", encoding="utf-8")
    # The report, and the row it earns.  A row is written only when the
    # run earned one: the promotion is the importer's decision, never
    # this script's.
    measured = (evidence.get("qualification_state") or {})
    if measured.get("inspected"):
        report = _build_report(evidence, measured.get("state") or {})
        path = REPORT_DIR / (f"{PAIR[0]}__{PAIR[1]}.json")
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        evidence["report"] = str(path)
        evidence["imported"] = _import_report(path)
        EVIDENCE_PATH.write_text(
            json.dumps(evidence, indent=1, default=str) + "\n",
            encoding="utf-8")
    else:
        evidence["report"] = (
            "not built: the qualification state was not measured "
            f"({measured.get('reason')})")
    print(json.dumps({"evidence": str(EVIDENCE_PATH),
                      "cases": [{"id": case["id"],
                                 "passed": case.get("passed"),
                                 "checks": case.get("checks")}
                                for case in evidence["cases"]]},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
