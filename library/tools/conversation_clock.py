"""Conversation clock (M6): per-source multicam offsets onto one shared clock.

Scout report `data/vep-long-footage-memory-scout/report.md` (in
firstmate's home, outside this checkout), §2.4/§3.1/§3.3, measured that
two angles of one conversation - LC4932 (Akshita) and LCATL0013 (Craig)
on the real geo-podcast project - agree on a constant offset for
99.6% of matched unique word 4-grams, and that without this clock a
gesture found on one angle cannot be matched to the other angle's
picture or to the words being said. This module is build-plan item 4
of that report's §5: the clock itself, read from the per-source memory's
M1 transcripts (`library/tools/source_memory.py`) and written into the
M6 slot (`docs/SOURCE_MEMORY.md`).

**How a group is found.** Two sources are the same multicam group when
unique word 4-grams between their M1 transcripts agree on a near-constant
offset for most of the matches that exist (`MIN_NGRAM_MATCHES`,
`MIN_AGREEMENT_FRACTION`). Nothing declares a group; it is measured per
pair, same as `footage_identity.fingerprint` measures identity rather
than trusting a filename. On geo-podcast this separates three real
pairs (fractions 0.987-0.996, hundreds to thousands of matches) from
every unrelated pair (fraction <= 0.42, a dozen matches at most) with
nothing in between - see `data/vep-conversation-clock/eval.md`.

**The clock.** Within a group, the member with the most transcribed
words is the reference; every other member's offset is the chain of
measured pairwise offsets back to it (`offset_to_reference_seconds`:
`this_source_time + offset_to_reference_seconds == reference_time`).
A group of more than two members (geo-podcast's LC4932 covers both
LCATL0013 and LCATL0014, different takes of the same camera) may need
more than one hop; `path_to_reference` says how many.

**The Resolve cross-check reads SAVED records only.** No Resolve call:
it reads the timeline transcript `timeline_transcript` already wrote
(`segments[].source_file/source_start/source_end/timeline_start/
timeline_end`), finds adjacent segments from two different sources
whose timeline gap is tight (`CUT_GAP_TOLERANCE_SECONDS` - a real
camera-switch cut, not a coincidental pause), and reads the implied
offset off the cut itself: at the switch, both sources name the same
real instant. The median across cuts is noisy (word boundaries, not
frame-exact cut points) but still lands within a second of the n-gram
offset on real data - reported, not hidden, as `median_absolute_
deviation_seconds`.

**Reaching a timeline item.** `segments[]` already carries
`resolve_item_id` beside `source_start`/`source_end` for the SAME
source this clock measures; a caller wanting the timeline item for a
moment on another angle first maps the moment through this clock onto
that angle's own source time (`map_time`), then looks up the segment
covering it - no new mapping is invented here.

**Scope.** Audio only, read-only: it reads M1 transcripts and (if
present) the project's saved timeline transcript. Nothing in Resolve is
opened and no project file is written; only the memory root's M6 slot
is written, per source.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from library.tools import source_memory
except ImportError:  # imported as `tools.*` from inside library/
    from tools import source_memory

NGRAM_SIZE = 4
"""Unique word n-grams to match. The scout's own measurement (§2.4)."""

AGREEMENT_TOLERANCE_SECONDS = 1.0
"""A matched n-gram's offset must land within this of the pair's median
to count as agreeing. Mechanical, not creative (AGENTS.md 10.5): wide
enough to absorb word-timing jitter, tight enough that the real pairs
(p90 width well under 0.2 s on geo-podcast) clear it by a wide margin
while unrelated pairs - whose "matches" are a handful of common short
phrases with no real correspondence - do not."""

MIN_NGRAM_MATCHES = 50
"""A pair needs at least this many agreeing unique n-grams to be called
a multicam group. Measured margin on geo-podcast: every real pair clears
it by 7x-150x; the largest unrelated pair reached 5."""

MIN_AGREEMENT_FRACTION = 0.9
"""...and at least this share of its candidate matches must agree.
Measured margin: every real pair reached >= 0.987; the largest
unrelated pair reached 0.42."""

CUT_GAP_TOLERANCE_SECONDS = 0.5
"""How tight a timeline gap between two different sources' segments
must be to read as one camera-switch cut rather than an unrelated
edit. A segment boundary marks where WORDS start/end, not the cut
itself, so this cross-check is read as approximate and reported with
its own spread (`median_absolute_deviation_seconds`), never silently
trusted to the second."""

SLOT_CLOCK = source_memory.SLOT_CLOCK  # M6 - this module's slot


def _load_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# ── Reading the M1 word stream ─────────────────────────────────────


def _word_stream(m1_doc: dict) -> List[Tuple[str, float]]:
    """`(word, start_seconds)` pairs in document order."""
    words: List[Tuple[str, float]] = []
    for utterance in m1_doc.get("utterances") or []:
        for word in utterance.get("words") or []:
            try:
                words.append((str(word["word"]), float(word["start"])))
            except (KeyError, TypeError, ValueError):
                continue
    return words


def _unique_ngrams(stream: List[Tuple[str, float]],
                   n: int = NGRAM_SIZE) -> Dict[tuple, float]:
    """`{ngram: start_time}` for n-grams occurring exactly once.

    A repeated phrase ("I think", "you know") would match several
    places with no way to tell which is the real correspondence, so
    only unique n-grams are kept - the same reasoning the scout's
    report used (§2.4, "matching unique word 4-grams").
    """
    counts: Dict[tuple, int] = {}
    first_time: Dict[tuple, float] = {}
    for i in range(len(stream) - n + 1):
        gram = tuple(word for word, _ in stream[i:i + n])
        counts[gram] = counts.get(gram, 0) + 1
        first_time.setdefault(gram, stream[i][1])
    return {gram: t for gram, t in first_time.items() if counts[gram] == 1}


def _percentile(sorted_values: List[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    floor_i = int(position)
    ceil_i = min(floor_i + 1, len(sorted_values) - 1)
    return (sorted_values[floor_i]
            + (sorted_values[ceil_i] - sorted_values[floor_i])
            * (position - floor_i))


def pairwise_offset(doc_a: dict, doc_b: dict, n: int = NGRAM_SIZE,
                    tolerance: float = AGREEMENT_TOLERANCE_SECONDS
                    ) -> Optional[dict]:
    """The measured offset between two M1 transcripts, or None.

    None means no unique n-gram matched at all - the two share no
    distinctive phrase, which is itself evidence they are unrelated.
    Otherwise: `offset_seconds` such that
    `a_time == b_time + offset_seconds`, from the AGREEING matches
    only (median, with the tolerance band above) - a handful of
    coincidental matches on a wildly different offset are visible in
    `total_candidate_matches` vs `agreeing_matches` rather than
    dragged into the number.
    """
    a = _unique_ngrams(_word_stream(doc_a), n)
    b = _unique_ngrams(_word_stream(doc_b), n)
    common = set(a) & set(b)
    if not common:
        return None
    diffs = sorted(a[gram] - b[gram] for gram in common)
    rough_median = statistics.median(diffs)
    agreeing = sorted(d for d in diffs if abs(d - rough_median) <= tolerance)
    if not agreeing:
        return None
    offset = statistics.median(agreeing)
    return {
        "ngram_size": n,
        "total_candidate_matches": len(common),
        "agreeing_matches": len(agreeing),
        "agreement_fraction": round(len(agreeing) / len(common), 4),
        "offset_seconds": round(offset, 3),
        "p5_seconds": round(_percentile(agreeing, 0.05), 3),
        "p95_seconds": round(_percentile(agreeing, 0.95), 3),
        "tolerance_seconds": tolerance,
    }


def _qualifies(edge: Optional[dict]) -> bool:
    return (edge is not None
            and edge["agreeing_matches"] >= MIN_NGRAM_MATCHES
            and edge["agreement_fraction"] >= MIN_AGREEMENT_FRACTION)


# ── Grouping sources into multicam groups ───────────────────────────


def discover_edges(docs_by_digest: Dict[str, dict]) -> Dict[Tuple[str, str], dict]:
    """Every qualifying pairwise edge, keyed `(lesser_digest, greater_digest)`.

    `offset_seconds` on an edge means `lesser_time == greater_time +
    offset_seconds` - the same convention `pairwise_offset` returns
    for `(doc_a, doc_b)` in that key order.
    """
    names = sorted(docs_by_digest)
    edges: Dict[Tuple[str, str], dict] = {}
    for i, da in enumerate(names):
        for db in names[i + 1:]:
            edge = pairwise_offset(docs_by_digest[da], docs_by_digest[db])
            if _qualifies(edge):
                edges[(da, db)] = edge
    return edges


def group_digests(digests: List[str],
                  edges: Dict[Tuple[str, str], dict]) -> List[List[str]]:
    """Connected components over the qualifying edges, size >= 2 only."""
    parent = {d: d for d in digests}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for da, db in edges:
        ra, rb = find(da), find(db)
        if ra != rb:
            parent[ra] = rb

    components: Dict[str, List[str]] = {}
    for d in digests:
        components.setdefault(find(d), []).append(d)
    return [sorted(members) for members in components.values()
            if len(members) > 1]


def _edge_between(edges: Dict[Tuple[str, str], dict], x: str, y: str
                  ) -> Optional[dict]:
    return edges.get((x, y) if x < y else (y, x))


def group_id(members: List[str]) -> str:
    """A stable id for a group, independent of discovery order."""
    blob = "|".join(sorted(members)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def clock_for_group(members: List[str], edges: Dict[Tuple[str, str], dict],
                    word_counts: Dict[str, int]
                    ) -> Dict[str, dict]:
    """`{digest: {reference, offset_to_reference_seconds, path, direct}}`.

    The reference is the member with the most transcribed words - the
    one most likely to anchor every other member directly, and a
    deterministic choice with no declaration involved. Every other
    member's offset is walked back to it over the qualifying edges
    (breadth-first, so the fewest hops win); a member more than one
    hop from the reference carries the hop count in `path_to_reference`
    so a reader can see the offset is transitive, not directly
    measured.
    """
    reference = max(members, key=lambda d: (word_counts.get(d, 0), d))
    offset_to_reference: Dict[str, float] = {reference: 0.0}
    path_to_reference: Dict[str, List[str]] = {reference: [reference]}
    queue = [reference]
    adjacency: Dict[str, List[str]] = {}
    for da, db in edges:
        if da in members and db in members:
            adjacency.setdefault(da, []).append(db)
            adjacency.setdefault(db, []).append(da)
    while queue:
        current = queue.pop(0)
        for neighbor in adjacency.get(current, []):
            if neighbor in offset_to_reference:
                continue
            edge = _edge_between(edges, current, neighbor)
            # edge["offset_seconds"]: lesser_time == greater_time + offset
            lesser, _greater = (current, neighbor) if current < neighbor \
                else (neighbor, current)
            # current_time == neighbor_time + hop, with sign per who is lesser
            hop = (edge["offset_seconds"] if current == lesser
                   else -edge["offset_seconds"])
            # reference_time == current_time + offset_to_reference[current]
            # current_time == neighbor_time + hop
            # => reference_time == neighbor_time + hop + offset_to_reference[current]
            offset_to_reference[neighbor] = (
                hop + offset_to_reference[current])
            path_to_reference[neighbor] = path_to_reference[current] + [neighbor]
            queue.append(neighbor)
    result = {}
    for member in members:
        result[member] = {
            "reference_digest": reference,
            "offset_to_reference_seconds": round(
                offset_to_reference[member], 3),
            # Read as "this digest, ..., reference digest" - the chain a
            # reader walks FROM this source TO the reference.
            "path_to_reference": list(reversed(path_to_reference[member])),
            "direct_measurement": (
                _edge_between(edges, member, reference)
                if len(path_to_reference[member]) == 2 else None),
        }
    return result


# ── The Resolve cross-check, reading saved records only ─────────────


def resolve_cut_offsets(segments: List[dict], source_file_a: str,
                        source_file_b: str,
                        gap_tolerance: float = CUT_GAP_TOLERANCE_SECONDS
                        ) -> List[float]:
    """Implied offsets from camera-switch cuts a saved timeline already made.

    At a tight-timeline-gap boundary between two different sources, both
    sides name the same real instant, so `a_source_time - b_source_time`
    at that instant is a Resolve-placed reading of the offset - noisier
    than the n-gram match (these are the nearest WORD to the cut, not
    the cut itself) but independent of it. Returns the raw list; the
    caller takes the median rather than trusting any one cut.
    """
    ordered = sorted(
        (s for s in segments
         if s.get("source_file") and s.get("source_start") is not None
         and s.get("source_end") is not None),
        key=lambda s: s["timeline_start"])
    diffs = []
    for cur, nxt in zip(ordered, ordered[1:]):
        if cur["source_file"] == nxt["source_file"]:
            continue
        gap = nxt["timeline_start"] - cur["timeline_end"]
        if abs(gap) > gap_tolerance:
            continue
        if (cur["source_file"] == source_file_a
                and nxt["source_file"] == source_file_b):
            # a ends, b begins: both name the cut instant.
            diffs.append(cur["source_end"] - nxt["source_start"])
        elif (cur["source_file"] == source_file_b
              and nxt["source_file"] == source_file_a):
            # b ends, a begins: same instant, other way round.
            diffs.append(nxt["source_start"] - cur["source_end"])
    return diffs


def cross_check_against_timeline(project_folder: str, source_file_a: str,
                                 source_file_b: str,
                                 ngram_offset_seconds: Optional[float]
                                 ) -> Optional[dict]:
    """Reads the project's SAVED timeline transcript, if one exists.

    Never calls Resolve and never builds a transcript: a project with
    no `timeline_transcript` output yet has nothing to cross-check
    against and this returns None, not a default.
    """
    from library.tools import timeline_transcript

    path = timeline_transcript.transcript_path(project_folder)
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return None
    diffs = resolve_cut_offsets(doc.get("segments") or [],
                                source_file_a, source_file_b)
    if not diffs:
        return {"timeline": (doc.get("derived_from") or {}).get("timeline"),
                "boundary_count": 0, "offset_seconds": None,
                "median_absolute_deviation_seconds": None,
                "agrees_with_ngram_offset": None}
    median = statistics.median(diffs)
    mad = (statistics.median([abs(d - median) for d in diffs])
           if len(diffs) > 1 else 0.0)
    agrees = (ngram_offset_seconds is not None
              and abs(median - ngram_offset_seconds) <= 1.0)
    return {
        "timeline": (doc.get("derived_from") or {}).get("timeline"),
        "boundary_count": len(diffs),
        "offset_seconds": round(median, 3),
        "median_absolute_deviation_seconds": round(mad, 3),
        "agrees_with_ngram_offset": agrees,
        "difference_from_ngram_offset_seconds": (
            round(median - ngram_offset_seconds, 3)
            if ngram_offset_seconds is not None else None),
    }


# ── Reading the clock back, and mapping a moment across sources ────


def read_clock(content_digest: str, root: Optional[Path] = None
              ) -> Optional[dict]:
    """A source's M6 record, or None when it has none (not in any
    measured group - never defaulted to "no offset")."""
    path = source_memory.source_dir(content_digest, root) / SLOT_CLOCK
    doc = _load_json(path)
    return doc if isinstance(doc, dict) else None


def map_time(content_digest: str, seconds: float,
            root: Optional[Path] = None) -> Dict[str, float]:
    """`{other_digest: other_source_time}` for every other member of
    this source's group, including the reference. Empty when this
    source has no clock record."""
    record = read_clock(content_digest, root)
    if record is None:
        return {}
    this_to_reference = record["offset_to_reference_seconds"]
    reference_time = seconds + this_to_reference
    out = {}
    for member in record.get("group_members") or []:
        if member == content_digest:
            continue
        other = read_clock(member, root)
        if other is None:
            continue
        # other_time + other_to_reference == reference_time
        out[member] = round(
            reference_time - other["offset_to_reference_seconds"], 3)
    return out


def timeline_item_for_source_time(project_folder: str, source_file: str,
                                  seconds: float) -> Optional[dict]:
    """The timeline segment covering one source's moment, or None.

    Reads the project's SAVED `timeline_transcript` output only - the
    source/timeline mapping it already records (`resolve_item_id`,
    `source_start`/`source_end`). No Resolve call, no new mapping: a
    caller with a moment on another angle maps it here first with
    `map_time`, onto THIS source's own time, then looks the result up
    with this function. None when the project has no saved transcript,
    or this source never plays at that moment (e.g. the moment falls
    before/after everything the timeline actually used from it).
    """
    from library.tools import timeline_transcript

    doc = _load_json(timeline_transcript.transcript_path(project_folder))
    if not isinstance(doc, dict):
        return None
    for segment in doc.get("segments") or []:
        if segment.get("source_file") != source_file:
            continue
        start, end = segment.get("source_start"), segment.get("source_end")
        if start is None or end is None:
            continue
        if start <= seconds <= end:
            return segment
    return None


# ── Building M6 ──────────────────────────────────────────────────────


def build_project(project_folder: str, root: Optional[Path] = None) -> dict:
    """Discover multicam groups across a project's catalog and write M6.

    Reads only: every catalog clip's fresh M1 transcript, and the
    project's saved timeline transcript if one exists. Writes
    `clock.json` under the memory root for every source placed into a
    group; a clip with no fresh M1, or in no group, gets no M6 record -
    absent, never defaulted (`docs/SOURCE_MEMORY.md`).
    """
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    digest_to_path: Dict[str, str] = {}
    docs_by_digest: Dict[str, dict] = {}
    word_counts: Dict[str, int] = {}
    skipped = []
    for clip in catalog:
        path = clip.get("source_file") or clip.get("path") or ""
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            skipped.append({"clip_id": clip.get("clip_id"),
                            "reason": "no-digest"})
            continue
        doc, status = source_memory.read_m1(digest, root)
        if status != "fresh":
            skipped.append({"clip_id": clip.get("clip_id"),
                            "content_digest": digest, "reason": f"m1-{status}"})
            continue
        digest_to_path[digest] = path
        docs_by_digest[digest] = doc
        word_counts[digest] = doc.get("word_count", 0)

    edges = discover_edges(docs_by_digest)
    groups = group_digests(list(docs_by_digest), edges)

    built_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    group_reports = []
    for members in groups:
        gid = group_id(members)
        clocks = clock_for_group(members, edges, word_counts)
        member_reports = []
        for digest in members:
            info = clocks[digest]
            reference = info["reference_digest"]
            cross_check = None
            if digest != reference:
                # `offset_to_reference_seconds` means reference_time -
                # this_time; pass (reference, this) so the cross-check's
                # `a - b` lands in the same convention, not its negation.
                cross_check = cross_check_against_timeline(
                    project_folder, digest_to_path[reference],
                    digest_to_path[digest],
                    info["offset_to_reference_seconds"])
            record = {
                "content_digest": digest,
                "source_file": digest_to_path[digest],
                "group_id": gid,
                "group_members": members,
                "reference_digest": reference,
                "offset_to_reference_seconds":
                    info["offset_to_reference_seconds"],
                "path_to_reference": info["path_to_reference"],
                "direct_measurement": info["direct_measurement"],
                "resolve_cross_check": cross_check,
                "ngram_size": NGRAM_SIZE,
                "built_at": built_at,
            }
            source_memory.write_json(
                source_memory.source_dir(digest, root) / SLOT_CLOCK, record)
            member_reports.append({
                "content_digest": digest,
                "source_file": digest_to_path[digest],
                "is_reference": digest == reference,
                "offset_to_reference_seconds":
                    info["offset_to_reference_seconds"],
                "direct_measurement": info["direct_measurement"],
                "resolve_cross_check": cross_check,
            })
        group_reports.append({
            "group_id": gid,
            "reference_digest": clocks[members[0]]["reference_digest"],
            "members": member_reports,
        })

    return {"project_folder": str(Path(project_folder).resolve()),
            "memory_root": str(source_memory.memory_root() if root is None
                               else root),
            "groups": group_reports,
            "skipped": skipped}


def status_report(project_folder: str, root: Optional[Path] = None) -> dict:
    """Per-clip clock state, reading only what `build_project` wrote."""
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    rows = []
    for clip in catalog:
        digest, basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        record = read_clock(digest, root) if digest else None
        rows.append({
            "clip_id": clip.get("clip_id"),
            "content_digest": digest,
            "digest_basis": basis,
            "in_group": record is not None,
            "group_id": record.get("group_id") if record else None,
            "is_reference": (record.get("reference_digest") == digest
                             if record else None),
            "offset_to_reference_seconds":
                record.get("offset_to_reference_seconds") if record else None,
        })
    return {"project_folder": str(Path(project_folder).resolve()), "clips": rows}


# ── CLI ────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="conversation_clock",
        description="Per-source multicam conversation clock (M6): "
                    "discover groups from M1 transcripts, offset them "
                    "onto one clock, cross-check against any saved "
                    "timeline transcript. Reads only; no Resolve, no "
                    "heavy decode.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="discover groups and write M6")
    p_build.add_argument("project")
    p_build.add_argument("--memory-root",
                         help="override the memory root for this run")

    p_status = sub.add_parser("status", help="per-clip clock state")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root",
                          help="override the memory root for this run")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project(args.project, root=root)
        print(json.dumps(report, indent=2))
        return 0
    print(json.dumps(status_report(args.project, root=root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
