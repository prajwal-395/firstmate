"""Compare each layer against the one it derives from, and name both.

A subtitle reading "Lucie" over a transcript reading "lucy" is
detectable, and until this module nothing detected it. This is the
forced consultation the depth router needs (`library/tools/
edit_depth.py`): run it over a project and every layer that
disagrees with its source is reported with both values named.

What is checked, per class
--------------------------
- `wording`: every active `transcript_spelling` correction's `heard`
  form is scanned for across the source transcript (a hit there is
  the guarantee half failing - the loudest row) and across every
  display the pipeline regenerates (subtitle plans and overlays,
  motion-graphic payloads, reel proposals, pipeline step outputs).
  The correction record itself, prompt echoes (`llm_requests/`,
  `llm_responses/`) and history (`backups/`) are not divergence -
  they are the machinery and the audit trail, and scanning them
  would fail every corrected project forever.
- `clip_timing`, `picture_position`, `audio_levels`, `assets`: every
  recorded pin's anchor is matched against the transcript on file.
  A pin matching nothing is a decision the next rebuild reports
  STALE - which is obedience only if somebody reads it. Here it is
  read, every run, before the build says it.
- `assets`: every declared card's media must exist on disk, and a
  card declared but absent from the latest manifest is named.
- `overlay_position`: every declared intent target must be complete
  (the reader refuses partials); application is per-build and
  provenance-recorded, so staleness here is anchor match only.
- `look_grade`, `mg_content`, `structure`, `marker_feedback`:
  reported by the runs that own them (render QA, replace-guard
  diff, proposal gate, routing report) - this module does not
  re-judge them. See `NOT_COVERED` for what would be needed.

Timeline-derived measurements (`timeline_captures/`, the repo's
`captures/`) are listed INFORMATIONALLY and never fail the run:
they describe the live Resolve project a parallel lane owns, and
this module neither touches that project nor grades it.

Read-only. This module never writes to the project - a check that
edits what it inspects cannot be run on the captain's data.

`python3 -m library.tools.layer_coherence <project_folder>`
prints every divergence and exits 2 while any owned-layer
divergence stands, 0 when the layers agree.

`tests/test_layer_coherence.py`.
"""

from __future__ import annotations

import json
import os
import re
import sys

from library.tools.project_layout import Area

#: Checks this module declines, with what would be needed. A check
#: that cannot read real state is worse than no check (AGENTS.md
#: 10.4), so these name their missing reader instead of guessing.
NOT_COVERED = {
    "look_grade": "applied Color-page nodes live in Resolve; comparing "
                  "them to the declared look needs a live timeline "
                  "read (parallel lane owns the project). Predicate: "
                  "every clip's node graph must contain the declared "
                  ".drx still OR the 5.01 CDL values within epsilon.",
    "mg_content": "authored MG copy is judged by the wording scan "
                  "where it quotes speech; copy invented outright has "
                  "no source to compare against - that judgement is "
                  "the 4.06 review gate's, not a diff's.",
    "structure": "approved reels are frozen by the guard and judged "
                 "by the replace-guard diff at promote time; "
                 "re-judging spans here would second-guess approval.",
    "marker_feedback": "resolution lives in marker_resolution's "
                       "ledger; unanswered notes are already loud in "
                       "ROUTED-NOTES.md. No second ledger here.",
    "timeline_vs_data": "whether the live timeline matches the latest "
                        "manifest needs Resolve open; the "
                        "timeline_captures below are shown, not graded.",
}


def _whole_word(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![\w\u00c0-\u024f\u1e00-\u1eff])"
                      + re.escape(pattern)
                      + r"(?![\w\u00c0-\u024f\u1e00-\u1eff])",
                      re.IGNORECASE)


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


#: Timestamped snapshots (`*_20260911T042844Z.json`) are history, not
#: layers: the base name is the live file, the stamped ones are the
#: audit trail, and scanning them fails every project once and then
#: forever after.
_HISTORY_RE = re.compile(r"_20\d{6}T\d+Z\.json$")


#: Where this module's own findings come to rest. A reels build
#: records this report into the pipeline's state file under
#: `step_outputs.build_reels.reel_build.coherence`
#: (`library/tools/reel_build.py`), and `check_wording` scans
#: `pipeline_data.json` because regenerated step outputs are a display
#: like any other. Each half is right alone; together they are a
#: measurement that measures itself, so the report is lifted out of
#: the document before the scan reads it.
OWN_REPORT_ROUTE = ("step_outputs", "build_reels", "reel_build",
                    "coherence")


def _without_own_report(document):
    """`document` with this module's own stored findings removed.

    The same rule as the `transcript.json` correction stamp in
    `check_wording`: a record of the pass HAVING RUN is machinery, not
    divergence. Measured on the captain's `geo-podcast` 2026-09-16 -
    28,245,754 of 28,245,899 wording rows were this scan reading its
    own previous output, 145 were real, and `pipeline_data.json`
    doubled on every build, 7,062,699 bytes to 12,196,676,126 over ten.
    docs/RULE_EVIDENCE.md, `the-scan-that-measured-itself`.

    The removal is BY ROUTE, not by filename: 116 of those 145 real
    rows were found inside `pipeline_data.json` too, in other step
    outputs, and dropping the file wholesale would have lost them.

    Only the spine down to the report is copied, never the document:
    the file this matters on is measured in gigabytes.
    """
    if not isinstance(document, dict):
        return document
    pruned = dict(document)
    cursor = pruned
    for key in OWN_REPORT_ROUTE[:-1]:
        branch = cursor.get(key)
        if not isinstance(branch, dict):
            return document
        branch = dict(branch)
        cursor[key] = branch
        cursor = branch
    if OWN_REPORT_ROUTE[-1] not in cursor:
        return document
    cursor.pop(OWN_REPORT_ROUTE[-1])
    return pruned


def _iter_json_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("backups", "llm_requests",
                                    "llm_responses", "exports",
                                    "timeline_captures")]
        for filename in filenames:
            if not filename.endswith(".json"):
                continue
            if _HISTORY_RE.search(filename):
                continue
            yield os.path.join(dirpath, filename)


def _scan_text(obj, heard_re: re.Pattern, out: list, path: str,
               location: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            _scan_text(value, heard_re, out, path,
                       f"{location}/{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _scan_text(value, heard_re, out, path,
                       f"{location}/{index}")
    elif isinstance(obj, str):
        for match in heard_re.finditer(obj):
            start = max(0, match.start() - 60)
            out.append({
                "class": "wording",
                "layer_file": path,
                "location": location,
                "found": match.group(0),
                "context": obj[start:match.end() + 60].replace(
                    "\n", " "),
            })


def check_wording(project_folder: str, corrections: list) -> list:
    """Every `heard` form surviving in source or regenerated displays."""
    divergences = []
    roots = []
    # Captain-side subtitle directories are named by their owner, never
    # spelled here: they are INPUT the layout reconciles, and a second
    # spelling is a second writer the day either is renamed
    # (tests/test_project_layout.py).
    for sub in ("pipeline_output", Area.SUBTITLE_PLANS.value,
                Area.SUBTITLE_OVERLAYS.value, "transcripts", "external",
                "compositions"):
        candidate = os.path.join(str(project_folder), sub)
        if os.path.isdir(candidate):
            roots.append(candidate)
    pipeline_data = os.path.join(str(project_folder),
                                 "pipeline_data.json")
    files = []
    for root in roots:
        files.extend(_iter_json_files(root))
    if os.path.isfile(pipeline_data):
        files.append(pipeline_data)
    for correction in corrections:
        heard = correction.get("heard", "")
        correct = correction.get("correct", "")
        if not heard:
            continue
        heard_re = _whole_word(heard)
        for path in files:
            if os.path.basename(path) == "learnings.json":
                continue
            document = _load_json(path)
            if document is None:
                continue
            # The correction's own stamp on the transcript is the
            # proof the pass ran, not a divergence.
            if os.path.basename(path) == "transcript.json":
                document = {k: v for k, v in document.items()
                            if k != "transcript_corrections_applied"}
            # And this module's own findings, wherever a build stored
            # them, are the same kind of thing: the proof this scan
            # ran, never something it may find again.
            document = _without_own_report(document)
            found: list = []
            _scan_text(document, heard_re, found, path, "")
            for row in found:
                divergences.append({**row, "should_be": correct,
                                    "correction_id":
                                        correction.get("id", "")})
    return divergences


def _load_transcript(project_folder: str):
    path = os.path.join(str(project_folder), "pipeline_output",
                        "scratch", "timeline_transcript",
                        "transcript.json")
    if not os.path.isfile(path):
        return None
    return _load_json(path)


def check_pin_anchors(project_folder: str) -> list:
    """Every recorded pin still matches the speech on file.

    Covers `clip_timing` (span_retime), `picture_position`
    (transform_override), `audio_levels` (mix_intent) and the
    structural deltas (keep exclusions, redraw_closer): a pin whose
    anchor the transcript no longer speaks is STALE at the next
    rebuild, and stale must be read, not filed.
    """
    from library.tools import captain_edits
    from library.tools import mix_intent
    from library.tools import transcript_corrections

    divergences = []
    transcript = _load_transcript(project_folder)
    if transcript is None:
        return [{"class": "pins", "layer_file": "<none>",
                 "location": "transcript",
                 "found": "no transcript on file",
                 "should_be": "transcribe before judging pins",
                 "context": "pin anchors cannot be matched without "
                            "measured speech"}]
    try:
        edits = captain_edits.load_edits(project_folder)
    except captain_edits.CaptainEditError as exc:
        return [{"class": "pins",
                 "layer_file": "external/captain_edits.json",
                 "location": "store",
                 "found": f"unreadable: {exc}",
                 "should_be": "a readable decision store",
                 "context": "a recorded decision the build cannot "
                            "read must refuse, never build past"}]
    stream = captain_edits._word_stream(transcript)
    for edit in edits:
        phrases = [edit.get("anchor_phrase", "")]
        if edit.get("kind") == "redraw_closer":
            phrases.append(edit.get("from_phrase", ""))
        for phrase in phrases:
            if phrase and not captain_edits._run_starts(stream, phrase):
                divergences.append({
                    "class": ("structure" if edit.get("kind")
                              in ("redraw_closer", "drop_fragment")
                              else "clip_timing" if edit.get("kind")
                              == "span_retime" else "picture_position"),
                    "layer_file": "external/captain_edits.json",
                    "location": edit.get("kind", "?"),
                    "found": f"anchor {phrase!r} matches nothing",
                    "should_be": "words the transcript still speaks",
                    "context": (edit.get("reason", "") or "")[:120],
                })
    try:
        pins = mix_intent.load_intent(project_folder)
    except mix_intent.MixIntentError as exc:
        divergences.append({
            "class": "audio_levels",
            "layer_file": "external/mix_intent.json",
            "location": "store",
            "found": f"unreadable: {exc}",
            "should_be": "a readable intent file",
            "context": "a declared level the build cannot read must "
                       "refuse, never mix past"})
        pins = []
    _matched, stale = mix_intent.match_pins(transcript, pins)
    for pin in stale:
        divergences.append({
            "class": "audio_levels",
            "layer_file": "external/mix_intent.json",
            "location": "pin",
            "found": f"anchor {pin.get('anchor_phrase')!r} matches "
                     f"nothing",
            "should_be": "words the transcript still speaks",
            "context": (pin.get("reason", "") or "")[:120],
        })
    for exclusion in transcript_corrections.keep_exclusions(
            project_folder):
        divergences.extend(_check_exclusion_bounds(
            exclusion, transcript))
    return divergences


def _check_exclusion_bounds(exclusion: dict, transcript: dict) -> list:
    """A keep exclusion must lie inside measured territory, or it cuts
    nothing. A strike over silence is legitimate (room tone goes too) -
    only a strike over seconds the transcript never measured is a
    decision about nothing, and is named as one."""
    start, end = exclusion["start"], exclusion["end"]
    covered = False
    for segment in transcript.get("segments") or ():
        try:
            seg_start = float(segment.get("timeline_start",
                                          segment.get("start", 0)))
            seg_end = float(segment.get("timeline_end",
                                        segment.get("end", 0)))
        except (TypeError, ValueError):
            continue
        if seg_end > start and seg_start < end:
            covered = True
            break
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if start - 1e-6 <= word_start < end - 1e-6:
                covered = True
                break
    if covered:
        return []
    return [{
        "class": "structure",
        "layer_file": "learned_context/learnings.json",
        "location": exclusion.get("id", "?"),
        "found": f"exclusion {start:.2f}..{end:.2f}s overlaps no "
                 f"measured speech at all",
        "should_be": "a strike over seconds the transcript measures",
        "context": (exclusion.get("reason", "") or "")[:120],
    }]


def check_declared_assets(project_folder: str) -> list:
    """Declared cards exist on disk and reach the latest manifest."""
    from library.tools import placed_assets

    divergences = []
    try:
        assets = placed_assets.load_assets(project_folder)
    except placed_assets.PlacedAssetError as exc:
        return [{
            "class": "assets",
            "layer_file": "external/placed_assets.json",
            "location": "store",
            "found": f"unreadable: {exc}",
            "should_be": "a readable declaration",
            "context": "a declared card the compile cannot read must "
                       "refuse, never compile past"}]
    if not assets:
        return []
    manifest = _load_json(os.path.join(str(project_folder),
                                       "pipeline_data.json"))
    labels: set = set()
    if isinstance(manifest, dict):
        for track in ((manifest.get("compile_manifest") or {})
                      .get("tracks", {}) or {}).values():
            for clip in (track or {}).get("clips", []) or []:
                if isinstance(clip, dict) and clip.get("label"):
                    labels.add(str(clip["label"]))
    for asset in assets:
        path = asset.get("asset", "")
        if not os.path.isfile(path):
            divergences.append({
                "class": "assets",
                "layer_file": "external/placed_assets.json",
                "location": asset.get("label", path),
                "found": f"media {path!r} not on disk",
                "should_be": "a file the compile can place",
                "context": (asset.get("reason", "") or "")[:120],
            })
    return divergences


def check_project(project_folder: str) -> dict:
    """Every divergence across the project, by class. Read-only."""
    from library.tools import transcript_corrections

    corrections = transcript_corrections.spelling_corrections(
        project_folder)
    wording = check_wording(project_folder, corrections) if corrections else []
    pins = check_pin_anchors(project_folder)
    assets = check_declared_assets(project_folder)
    informational = _timeline_derived(project_folder, corrections)
    return {"wording": wording, "pins": pins, "assets": assets,
            "informational": informational,
            "not_covered": dict(NOT_COVERED)}


def _timeline_derived(project_folder: str, corrections: list) -> list:
    """What the live-timeline measurements say. Shown, never graded."""
    rows = []
    captures = os.path.join(str(project_folder), "timeline_captures")
    if not os.path.isdir(captures):
        return rows
    for path in _iter_json_files(captures):
        document = _load_json(path)
        if document is None:
            continue
        for correction in corrections:
            heard = correction.get("heard", "")
            if not heard:
                continue
            found: list = []
            _scan_text(document, _whole_word(heard), found, path, "")
            for row in found[:5]:
                rows.append({**row,
                             "should_be": correction.get("correct",
                                                         ""),
                             "note": "timeline measurement - parallel "
                                     "lane owns the timeline"})
    return rows


def _deep_path_for(row_class: str) -> str:
    """The owning-layer route for a divergence row, or "".

    Every flagged row names both values (found/should-be at the call
    site); this names what KEEPS the fix, so the flag is a routing and
    not just a complaint. `pins` is the shared anchor class for
    clip_timing/picture_position/audio_levels/structure pins - the
    route is re-anchoring to words the transcript still speaks.
    """
    from library.tools import edit_depth

    if row_class in edit_depth.DEEP_PATH:
        return edit_depth.DEEP_PATH[row_class]
    if row_class == "pins":
        return ("re-anchor the pin to words the transcript still "
                "speaks, or re-record it - a span_retime, "
                "transform_override, mix_intent or redraw_closer pin "
                "naming seconds nothing says any more (see "
                "edit_depth.DEEP_PATH per class).")
    return ""


def main(argv=None) -> int:
    """`python3 -m library.tools.layer_coherence <project_folder>`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.layer_coherence",
        description="Compare each layer against the one it derives "
                    "from, naming both values on divergence.")
    parser.add_argument("project_folder")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    report = check_project(args.project_folder)
    total = 0
    for key in ("wording", "pins", "assets"):
        rows = report[key]
        total += len(rows)
        for row in rows:
            print(f"[{row.get('class', key)}] {row['layer_file']}"
                  f"{row.get('location', '')}: found "
                  f"{row['found']!r}, should be "
                  f"{row.get('should_be', '')!r}")
            if row.get("context"):
                print(f"    {row['context']}")
            deep = _deep_path_for(str(row.get("class", key)))
            if deep:
                print(f"    deep path: {deep}")
    if report["informational"]:
        print(f"--- timeline-derived (informational, not graded): "
              f"{len(report['informational'])} ---")
        for row in report["informational"]:
            print(f"[timeline] {row['layer_file']}: found "
                  f"{row['found']!r}, should be "
                  f"{row.get('should_be', '')!r}")
    print(f"{total} owned-layer divergence(s), "
          f"{len(report['informational'])} informational.")
    return 2 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
