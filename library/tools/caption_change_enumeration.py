"""The changed-card enumeration a PR body pastes, built from records.

The captions fix (PR 1209) enumerated 134 before-and-after caption lines
in its PR body BY HAND - model time spent transcribing something a
machine already knows, and it went wrong exactly the way hand
transcription does: the same body claimed the change covered 32
timelines when the project has 30 live reels, collapsed four pairs of
distinct same-text cards into one line each (Reels 11, 16, 16, 29), and
counted 121 ledger-bound files where the ledger references 116. The
captain reads those enumerations - he asked to see every changed line
rather than trust a summary - so they must stay complete and they must
be right, and no lane should ever type one again.

This module builds the same thing a reader gets today - every changed
card, before and after, grouped by reel - from the render ledger and
the render records (the props sidecars beside each file), never from
the worker's memory of what it did. Timeline counts, card counts and
reel names all come from the records, or they do not appear at all.

How a before meets its after
----------------------------
A re-render names its superseded generations on its ledger entry, but
those are same-STEM siblings (every card of one block shares the
provenance stem - speaker, clip, source span - and differs only in
digest), not the card's own previous generation. Pairing by ledger
linkage alone would wed a new card to another card's old text. The
pairing is by TEXT DERIVATION, in order, inside the entry's own
superseded set:

1. the caption reading (`library/tools/caption_reading.py`): the old
   text whose re-reading IS the new text ("seo two point oh" reads
   "SEO 2.0"). Old cards predate the reading, so exactly one old text
   derives each new one - and a re-render that changed no text (a
   canvas or carriage change) derives nothing and is skipped, which is
   what scopes one change out of the ledger's whole accumulated
   history without any date filter;
2. word-subsequence containment: every word of the old card appears in
   order in the new one, strictly fewer (the transcript-splice shape:
   "the link's bio." grew "in our").

Zero or several derivations is not a pairing - it is a diagnostic. An
unpaired or ambiguous entry is reported on stderr with its reason and
kept OUT of the enumeration, because a silently misp aired line is the
defect this module exists to remove. Measured on the PR 1209 change:
129 reading pairs, 8 subsequence pairs, 0 unpaired, 0 ambiguous.

What the ledger cannot say
--------------------------
A file the ledger never bound (no entry names it as its overlay path -
superseded-array membership carries no binding) still needs placement
records to place it on a reel: which timelines hold it, and whether
those timelines are live. Those come from a Resolve read-back the lane
already does for placement verification, passed as `--placements`
(JSON mapping timeline name to placed file paths). Without it the tool
enumerates the ledger-bound changes and reports the unbound new-style
files as diagnostics - counted, never silently dropped, never
invented onto reels.

The live-vs-historical split is the same shape: `--live-timelines`
names the timelines that exist (or `@path` to read them). Without it no
live claim appears anywhere in the output.

For a future motion-graphics fix: the pairing core and the renderer
below are kind-agnostic - they operate on `ChangeRecord`s. Only the
supplier (`collect_caption_changes`) knows captions; an overlay kind
with its own ledger and text records earns its own supplier and reuses
`render_enumeration` unchanged.

A lane runs, from the worktree::

    python3 -m library.tools.caption_change_enumeration \\
        --project /path/to/project --live-timelines @timelines.json \\
        --placements placements.json

and pastes stdout into the PR body.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _words(text: str) -> list[str]:
    """The matchable words of a card text, lowercased."""
    return _WORD_RE.findall(text.lower())


def card_text_from_props(props_path: str) -> str | None:
    """One card's drawn text, read off its render record.

    A caption props file carries `subtitles`, one entry per karaoke
    card the segment holds; the enumeration quotes them joined, so a
    multi-card segment reads as one line rather than vanishing. None
    when the record is absent or unreadable - the caller says why, so
    a missing record is a diagnostic rather than an empty string that
    would pair against everything.
    """
    try:
        with open(props_path, encoding="utf-8") as handle:
            props = json.load(handle)
    except (OSError, ValueError):
        return None
    subtitles = props.get("subtitles")
    if not isinstance(subtitles, list):
        return None
    return " | ".join(
        str(entry.get("text", ""))
        for entry in subtitles
        if isinstance(entry, dict)
    )


def props_path_for(overlay_path: str) -> str:
    """The render record beside a rendered file. Never guessed twice."""
    stem, _ = os.path.splitext(overlay_path)
    return stem + "_props.json"


def _reading_derives(old: str, new: str) -> bool:
    """Whether the caption reading turns `old` into `new`, exactly."""
    from library.tools.caption_reading import (
        apply_caption_reading_text,
    )

    return apply_caption_reading_text(old) == new


def _subsequence_derives(old: str, new: str) -> bool:
    """Whether `new` is `old` with words added, order kept.

    The transcript-splice shape: nothing reworded, words only added
    ("the link's bio." -> "the link's in our bio."). Strictly fewer
    words required, so an identical text never derives itself here -
    that case belongs to the text-identical skip, not to a pairing.
    """
    old_words, new_words = _words(old), _words(new)
    if len(old_words) < 2 or not len(old_words) < len(new_words):
        return False
    return all(word in new_words for word in old_words)


def pair_predecessor(
    new_text: str, old_texts: set[str]
) -> tuple[str | None, str]:
    """The old text `new_text` derives from, and how, or why not.

    Returns (before, basis) with basis one of `reading`,
    `subsequence`, `ambiguous` or `unpaired`. Several derivations is
    not a pairing: the caller reports it rather than picking one,
    because a picked predecessor is a hand transcription wearing a
    tool's clothes.
    """
    readers = [old for old in old_texts if _reading_derives(old, new_text)]
    if len(readers) == 1:
        return readers[0], "reading"
    if len(readers) > 1:
        return None, "ambiguous"
    sequels = [old for old in old_texts if _subsequence_derives(old, new_text)]
    if len(sequels) == 1:
        return sequels[0], "subsequence"
    if len(sequels) > 1:
        return None, "ambiguous"
    return None, "unpaired"


@dataclass
class ChangeRecord:
    """One changed card, ready for the enumeration."""

    timeline: str
    before: str
    after: str
    segment_id: str = ""
    overlay_path: str = ""
    basis: str = ""
    source: str = "ledger"


@dataclass
class Diagnostic:
    """Something the enumeration left out, and why."""

    reason: str
    detail: str


@dataclass
class ChangeSet:
    """What the records said, split into printable and reportable."""

    changes: list[ChangeRecord] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def files(self) -> set[str]:
        """Distinct rendered files changed. A count from records."""
        return {change.overlay_path for change in self.changes if change.overlay_path}

    @property
    def timelines(self) -> list[str]:
        """Distinct binding names, sorted. Names from records."""
        return sorted({change.timeline for change in self.changes})


def _read_ledger(ledger_path: str) -> list[dict]:
    """Ledger entries, or a loud refusal. An absent ledger is not empty."""
    try:
        with open(ledger_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read render ledger at {ledger_path}: {exc}")
    except ValueError as exc:
        raise SystemExit(f"render ledger at {ledger_path} is not JSON: {exc}")
    overlay = data.get("subtitle_overlay") if isinstance(data, dict) else None
    segments = overlay.get("segments") if isinstance(overlay, dict) else None
    if not isinstance(segments, list):
        raise SystemExit(
            f"render ledger at {ledger_path} holds no subtitle_overlay.segments list"
        )
    return [entry for entry in segments if isinstance(entry, dict)]


def _binding_timeline(entry: dict) -> str:
    binding = entry.get("binding")
    if isinstance(binding, dict):
        return str(binding.get("timeline") or "")
    return ""


def collect_caption_changes(
    asset_dir: str,
    ledger_path: str,
    *,
    placements: dict[str, list[str]] | None = None,
) -> ChangeSet:
    """Pair every re-rendered ledger entry with its previous text.

    The predecessor search space is the entry's OWN superseded list -
    the merge drops superseded generations per placing, so another
    placing's old files are never candidates. A new text identical to
    every old text is a canvas re-render, not a changed card, and is
    skipped (counted on stderr, not in the enumeration).

    With `placements` (timeline -> placed file paths from a Resolve
    read-back), ledger-unbound new-style files that are actually
    placed join the enumeration with `source="placement"`; their
    predecessors are same-stem older files on disk. Without it they
    are reported as diagnostics - the placed/live half needs Resolve,
    and the tool says so rather than placing them by guess.
    """
    found = ChangeSet()
    entries = _read_ledger(ledger_path)
    for entry in entries:
        superseded = entry.get("superseded") or []
        if not superseded:
            continue
        overlay_path = str(entry.get("overlay_path") or "")
        if not overlay_path:
            continue
        new_text = card_text_from_props(props_path_for(overlay_path))
        if new_text is None:
            found.diagnostics.append(
                Diagnostic(
                    "new-props-unreadable",
                    f"{entry.get('segment_id', '?')}: "
                    f"{props_path_for(overlay_path)} cannot be read",
                )
            )
            continue
        olds: set[str] = set()
        for old_path in sorted(set(superseded)):
            if not isinstance(old_path, str):
                continue
            old_text = card_text_from_props(props_path_for(old_path))
            if old_text is None:
                found.diagnostics.append(
                    Diagnostic(
                        "old-props-unreadable",
                        f"{entry.get('segment_id', '?')}: "
                        f"{props_path_for(old_path)} cannot be read",
                    )
                )
                continue
            if old_text != new_text:
                olds.add(old_text)
        if not olds:
            continue  # a canvas re-render: same text, new pixels
        before, basis = pair_predecessor(new_text, olds)
        if before is None:
            found.diagnostics.append(
                Diagnostic(
                    basis,
                    f"{entry.get('segment_id', '?')} on "
                    f"{_binding_timeline(entry) or '?'}: {len(olds)} "
                    f"candidate predecessor(s) for {new_text!r}",
                )
            )
            continue
        found.changes.append(
            ChangeRecord(
                timeline=_binding_timeline(entry),
                before=before,
                after=new_text,
                segment_id=str(entry.get("segment_id") or ""),
                overlay_path=overlay_path,
                basis=basis,
                source="ledger",
            )
        )
    if placements is not None:
        _collect_unbound_placed(asset_dir, entries, placements, found)
    else:
        _report_unbound(asset_dir, entries, found)
    return found


def _ledger_referenced_paths(entries: list[dict]) -> set[str]:
    return {
        str(entry.get("overlay_path"))
        for entry in entries
        if entry.get("overlay_path")
    }


def _stem_matches(name: str, mine: str, my_tight: bool) -> bool:
    """Whether a directory filename is a same-card sibling. Never raises:
    an unparsable name pairs nothing, and refusing the run over one
    unreadable filename would trade the whole enumeration for a stray file."""
    if not name.endswith(".mov"):
        return False
    try:
        from library.tools.caption_asset_gc import card_key

        card, tight = card_key(name[:-4])
    except Exception:  # noqa: BLE001 - skip, never refuse the run
        return False
    return card == mine and tight == my_tight


def _same_stem_olds(asset_dir: str, overlay_path: str) -> set[str]:
    """Older same-STEM files' texts, from disk. The stem is the block:
    every card of one block shares it, so this is the same search
    space `_superseded_generations` records per placing - siblings,
    not guesses from other blocks."""
    from library.tools.caption_asset_gc import card_key

    base = os.path.basename(overlay_path)
    stem, _ = os.path.splitext(base)
    try:
        mine, my_tight = card_key(stem)
    except Exception:  # noqa: BLE001 - an unparsable name pairs nothing
        return set()
    olds: set[str] = set()
    try:
        names = os.listdir(asset_dir)
    except OSError:
        return set()
    for name in names:
        if name == base:
            continue
        if not _stem_matches(name, mine, my_tight):
            continue
        text = card_text_from_props(
            os.path.join(asset_dir, name[:-4] + "_props.json")
        )
        if text is not None:
            olds.add(text)
    return olds


def _collect_unbound_placed(
    asset_dir: str,
    entries: list[dict],
    placements: dict[str, list[str]],
    found: ChangeSet,
) -> None:
    """Ledger-unbound placed files, paired off same-stem disk siblings.

    Only files the read-back actually places join: an unbound file
    nobody places is an intermediate generation, not a changed card,
    and enumerating it would credit work the timeline never shows.
    """
    referenced = _ledger_referenced_paths(entries)
    for timeline in sorted(placements):
        for placed_path in sorted(set(placements[timeline])):
            if placed_path in referenced:
                continue
            new_text = card_text_from_props(props_path_for(placed_path))
            if new_text is None:
                found.diagnostics.append(
                    Diagnostic(
                        "new-props-unreadable",
                        f"placed file {placed_path} on {timeline}: "
                        f"{props_path_for(placed_path)} cannot be read",
                    )
                )
                continue
            olds = {
                old
                for old in _same_stem_olds(asset_dir, placed_path)
                if old != new_text
            }
            if not olds:
                continue
            before, basis = pair_predecessor(new_text, olds)
            if before is None:
                found.diagnostics.append(
                    Diagnostic(
                        basis,
                        f"placed file {placed_path} on {timeline}: "
                        f"{len(olds)} candidate predecessor(s) for "
                        f"{new_text!r}",
                    )
                )
                continue
            found.changes.append(
                ChangeRecord(
                    timeline=timeline,
                    before=before,
                    after=new_text,
                    segment_id=os.path.splitext(os.path.basename(placed_path))[0],
                    overlay_path=placed_path,
                    basis=basis,
                    source="placement",
                )
            )


def _report_unbound(
    asset_dir: str, entries: list[dict], found: ChangeSet
) -> None:
    """Count the ledger-unbound new-style files without placing them.

    Without a read-back there is no record of which unbound files a
    timeline holds, so they stay out of the enumeration - and they
    stay COUNTED, because an enumeration that silently drops them is
    the undercount this tool exists to remove.
    """
    referenced = _ledger_referenced_paths(entries)
    unbound = 0
    try:
        names = os.listdir(asset_dir)
    except OSError:
        return
    for name in names:
        if not name.endswith(".mov"):
            continue
        full = os.path.join(asset_dir, name)
        if full in referenced:
            continue
        text = card_text_from_props(os.path.join(asset_dir, name[:-4] + "_props.json"))
        if text is None:
            continue
        olds = {old for old in _same_stem_olds(asset_dir, full) if old != text}
        if not olds:
            continue
        before, _basis = pair_predecessor(text, olds)
        if before is not None:
            unbound += 1
    if unbound:
        found.diagnostics.append(
            Diagnostic(
                "ledger-unbound",
                f"{unbound} changed file(s) on disk are named by no "
                f"ledger entry; pass --placements from a Resolve "
                f"read-back to place them on reels",
            )
        )


def _short_digest(overlay_path: str) -> str:
    """The file's content digest, for disambiguating same-text cards."""
    base = os.path.basename(overlay_path)
    stem, _ = os.path.splitext(base)
    match = re.search(r"_([0-9a-f]{8})$", stem)
    return match.group(1) if match else stem[-8:]


def _quote(text: str) -> str:
    """The PR-body quoting: single quotes unless the text holds one."""
    if "'" in text and '"' not in text:
        return f'"{text}"'
    return f"'{text}'"


def render_enumeration(
    changes: list[ChangeRecord],
    *,
    live_timelines: set[str] | None = None,
) -> str:
    """The paste-ready markdown. Every number derives from `changes`.

    One line per changed placement, grouped by binding name; a card
    file shared across reels repeats per reel, exactly as the captain
    reads it. Two DISTINCT files carrying the same text on one reel
    each get their line, the second disambiguated by short digest -
    collapsing them is the undercount PR 1209's hand enumeration made
    (Reels 11, 16, 29). With `live_timelines` given, bindings outside
    it are marked historical and the totals split; without it no live
    claim appears, because a count with no record behind it does not
    appear at all.
    """
    by_reel: dict[str, list[ChangeRecord]] = {}
    for change in changes:
        by_reel.setdefault(change.timeline, []).append(change)
    files = {change.overlay_path for change in changes if change.overlay_path}
    lines = [
        (
            f"TOTAL changed cards: {len(changes)} placement(s) "
            f"across {len(files)} file(s) on {len(by_reel)} timeline(s)"
        )
    ]
    if live_timelines is not None:
        live = sum(
            1 for change in changes if change.timeline in live_timelines
        )
        lines[0] += f" ({live} live, {len(changes) - live} historical)"
    lines.append("")
    for reel in sorted(by_reel):
        entries = sorted(by_reel[reel], key=lambda change: (change.before, change.after))
        header = f"### {reel} ({len(entries)})"
        if live_timelines is not None and reel not in live_timelines:
            header += " (no live timeline)"
        lines.append(header)
        seen: dict[tuple[str, str], int] = {}
        for change in entries:
            key = (change.before, change.after)
            seen[key] = seen.get(key, 0) + 1
            suffix = (
                f" [{_short_digest(change.overlay_path)}]"
                if seen[key] > 1 and change.overlay_path
                else ""
            )
            lines.append(
                f"- BEFORE {_quote(change.before)}  ->  "
                f"AFTER {_quote(change.after)}{suffix}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_live_timelines(value: str) -> set[str]:
    """A comma list, or `@path` to a JSON list. Names from a record."""
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot read live timelines at {path}: {exc}")
        if not isinstance(data, list):
            raise SystemExit(f"live timelines at {path} is not a JSON list")
        return {str(name) for name in data}
    return {name.strip() for name in value.split(",") if name.strip()}


def _parse_placements(value: str) -> dict[str, list[str]]:
    """A JSON object mapping timeline name to placed file paths."""
    try:
        with open(value, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read placements at {value}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"placements at {value} is not a JSON object")
    return {
        str(timeline): [str(path) for path in paths]
        for timeline, paths in data.items()
        if isinstance(paths, list)
    }


def _resolve_paths(args) -> tuple[str, str]:
    """The asset dir and ledger the run reads. A project, or both."""
    if args.project:
        from library.tools.project_layout import Area, ProjectLayout

        layout = ProjectLayout(args.project)
        return (
            str(layout.read_dir(Area.SUBTITLE_SEGMENTS)),
            str(layout.step_dir("render_subtitles") / "render_ledger.json"),
        )
    if not args.asset_dir or not args.ledger:
        raise SystemExit("pass --project, or both --asset-dir and --ledger")
    return args.asset_dir, args.ledger


def main(argv: list[str] | None = None) -> int:
    """Build the changed-card enumeration from the render records."""
    parser = argparse.ArgumentParser(
        description="Enumerate changed caption cards before/after per reel "
        "from the render ledger and props sidecars - the PR-body "
        "enumeration no lane types by hand. Counts and names come from "
        "the records or do not appear.",
    )
    parser.add_argument(
        "--project",
        default="",
        help="project folder; the caption asset dir and render ledger "
        "are resolved from the project layout",
    )
    parser.add_argument("--asset-dir", default="", help="caption step dir")
    parser.add_argument("--ledger", default="", help="render_ledger.json path")
    parser.add_argument(
        "--live-timelines",
        default="",
        help="timelines that exist: a comma list, or @path to a JSON "
        "list. Without it no live/historical claim appears",
    )
    parser.add_argument(
        "--placements",
        default="",
        help="JSON object mapping timeline name to placed file paths "
        "from a Resolve read-back; without it ledger-unbound files "
        "are counted, not enumerated",
    )
    parser.add_argument("--out", default="", help="write markdown here (default stdout)")
    args = parser.parse_args(argv)
    asset_dir, ledger_path = _resolve_paths(args)
    live = (
        _parse_live_timelines(args.live_timelines) if args.live_timelines else None
    )
    placements = _parse_placements(args.placements) if args.placements else None
    found = collect_caption_changes(asset_dir, ledger_path, placements=placements)
    output = render_enumeration(found.changes, live_timelines=live)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output)
    else:
        sys.stdout.write(output)
    for diagnostic in found.diagnostics:
        print(f"{diagnostic.reason}: {diagnostic.detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
