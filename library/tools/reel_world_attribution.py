"""VI-013b: a reel plan asks the world narrow speaker questions.

PR 3139 (VI-013) built the world-model query surface
(`library/tools/world_model_query.py`): interval and entity questions
answered off the canonical source-memory records at native timing, with
per-fact provenance, at 404x less context per planning call than the
whole per-second `world_model.json`. INFRA-003 made it searchable. What
neither lane did was consume it: no planning step asked the world
anything.

This module is the consumption, for reel planning. For every reel
candidate the planner proposes, it asks the source memory through the
query surface - "who speaks in these source seconds" - one narrow speech
question per source span, at the canonical records' own timing, and
joins the answer to the transcript's own speakers by source-time
overlap. The join is the VI-013 benchmark's own (`_speaker_for_track`):
the transcript's speaker labels and the source-side diarization are two
measurements of the same moment, joined on the overlap both carry.

It REPORTS; it never decides. Like `plan_span_verification` (VI-014),
the findings are written to the project's review area and the caller
decides what to do with them:

- a candidate whose world speaker SET matches the plan's own
  attribution is corroborated, not changed;
- a candidate where they differ is REPORTED with both shares, never
  re-attributed;
- a candidate no world can answer (no catalog to verify against, no
  recorded digest, no M7 speaking spans, no speech in the span, the
  budget ran out) is UNATTRIBUTED with the named reason - never a
  guess, and never a silent absence.

The wiring it lands on:

- `select_reels` declares the optional `clip_catalog` input, routed by
  the `catalog -> select_reels` DAG edge. The bridge already verifies
  candidate spans against the catalog before asking (`search_in_planning.
  verify_span`'s rule: a span nothing can place is refused where the
  evidence is in hand). The input is bridge-only - it is not in
  `context_fields`, so the model's prompt is byte-identical;
- `select_reels` post_bridge `resolve` calls `attribute_and_write`
  after the selection is published. The return value is untouched: the
  only write is the review-area report.

A question costs the question. The whole per-second view is never read
by this module: `World.facts` reads M1/M7 lazily per span, and the
report records both the whole-view bytes the question WOULD have cost
and the query bytes it did, so the saving is measured, not asserted.

    python3 -m library.tools.reel_world_attribution <project_folder>
        [--memory-root DIR] [--budget-seconds 120] [--out FILE]
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

FORMAT = "reel_world_attribution/1"
"""The report format, so a reader can refuse a file it does not know."""

FINDINGS_FILENAME = "reel_world_attribution.json"
"""The report's name, inside the project's review area - where the plan's
own decisions live (`plan_provenance`, `plan_span_verification`), and a
finding about the plan belongs beside them rather than in a step's output
directory."""

SHARE_WITHIN = 0.10
"""The reel-plan share agreement bar, the VI-013 benchmark's own: the
world's speaker share within 0.10 of the plan's counts as agreement."""


@dataclass
class CandidateAttribution:
    """What the world says about one reel candidate's speakers."""

    candidate: int
    start: float
    end: float
    plan_speakers: List[str] = field(default_factory=list)
    plan_share: Dict[str, float] = field(default_factory=dict)
    world_speakers: List[str] = field(default_factory=list)
    world_share: Dict[str, float] = field(default_factory=dict)
    speakers_match: Optional[bool] = None
    mean_abs_share_difference: Optional[float] = None
    facts: int = 0
    facts_with_provenance: int = 0
    lanes_used: List[str] = field(default_factory=list)
    lanes_absent: List[str] = field(default_factory=list)
    whole_view_bytes: Optional[int] = None
    query_bytes: int = 0
    query_o200k: Optional[int] = None
    status: str = "unattributed"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "plan_speakers": list(self.plan_speakers),
            "plan_share": dict(self.plan_share),
            "world_speakers": list(self.world_speakers),
            "world_share": dict(self.world_share),
            "speakers_match": self.speakers_match,
            "mean_abs_share_difference": self.mean_abs_share_difference,
            "facts": self.facts,
            "facts_with_provenance": self.facts_with_provenance,
            "lanes_used": list(self.lanes_used),
            "lanes_absent": list(self.lanes_absent),
            "whole_view_bytes": self.whole_view_bytes,
            "query_bytes": self.query_bytes,
            "query_o200k": self.query_o200k,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Report:
    """What the world said about a reel plan's speakers."""

    findings: List[CandidateAttribution] = field(default_factory=list)
    seconds: float = 0.0
    candidates_asked: int = 0
    candidates_attributed: int = 0
    budget_seconds: float = 0.0
    plan_digest: Optional[str] = None
    selected_candidates: List[int] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def speaker_set_match(self) -> str:
        matched = [f for f in self.findings if f.speakers_match is True]
        asked = [f for f in self.findings if f.speakers_match is not None]
        return f"{len(matched)}/{len(asked)}"

    def share_within(self) -> str:
        close = [f for f in self.findings
                 if f.mean_abs_share_difference is not None
                 and f.mean_abs_share_difference <= SHARE_WITHIN]
        asked = [f for f in self.findings
                 if f.mean_abs_share_difference is not None]
        return f"{len(close)}/{len(asked)}"

    def context_bytes(self) -> dict[str, int]:
        whole = sum(f.whole_view_bytes or 0 for f in self.findings)
        query = sum(f.query_bytes for f in self.findings)
        return {"whole_view_bytes": whole, "query_bytes": query}

    def context_o200k(self) -> dict[str, Optional[int]]:
        """Mean o200k tokens per call, or None where unmeasurable.

        `utf8_bytes` is exact and always present; `o200k_base` is a
        stated proxy and is ABSENT where this environment cannot run
        it - a count is never published without its tokenizer.
        """
        asked = self.candidates_asked or 0
        toks = [f.query_o200k for f in self.findings
                if f.query_o200k is not None]
        if not asked or len(toks) != len(self.findings):
            return {"query_o200k_per_call": None}
        return {"query_o200k_per_call": sum(toks) // asked}

    def to_dict(self) -> dict[str, Any]:
        context = self.context_bytes()
        asked = self.candidates_asked or 1
        body = {
            "format": FORMAT,
            "candidates_asked": self.candidates_asked,
            "candidates_attributed": self.candidates_attributed,
            "speaker_set_match": self.speaker_set_match(),
            "speaker_share_within_0.10": self.share_within(),
            "whole_view_bytes_per_call": whole_rounded(context, asked),
            "query_bytes_per_call": query_rounded(context, asked),
            "context_bytes": context,
            "plan_digest": self.plan_digest,
            "selected_candidates": list(self.selected_candidates),
            "budget_seconds": self.budget_seconds,
            "seconds": round(self.seconds, 3),
            "reasons": list(self.reasons),
            "findings": [f.to_dict() for f in self.findings],
        }
        body["context_o200k"] = self.context_o200k()
        return body


def whole_rounded(context: dict, asked: int) -> int:
    """Mean whole-view bytes per candidate question, rounded down."""
    return context["whole_view_bytes"] // asked


def query_rounded(context: dict, asked: int) -> int:
    """Mean query bytes per candidate question, rounded down."""
    return context["query_bytes"] // asked


def plan_digest_for(candidates: Sequence[Any]) -> str:
    """The plan's identity: what the world answers were compared against.

    The candidate rows the bridge published (start, end, speakers,
    shares) - so a later run can say whether the plan under the answers
    is the plan the answers were about.
    """
    from library.tools.stable_json import dumps_stable
    rows = []
    for candidate in candidates or ():
        if not isinstance(candidate, dict):
            continue
        rows.append({
            "start": candidate.get("start"),
            "end": candidate.get("end"),
            "speakers": candidate.get("speakers"),
            "seconds_by_speaker": candidate.get("seconds_by_speaker"),
            "share_by_speaker": candidate.get("share_by_speaker"),
        })
    return hashlib.sha256(
        dumps_stable(rows).encode("utf-8")).hexdigest()


def _overlapping_segments(transcript: dict, start: float,
                           end: float) -> List[dict]:
    """Bound transcript segments touching [start, end), in time order.

    The same bound segments the reel builder lays out
    (`reel_proposal.bound_segments`): a segment straddling a cut has no
    single ground truth and is excluded, the way the build excludes it.
    """
    from library.tools.reel_proposal import bound_segments
    rows = []
    for segment in bound_segments(transcript or {}):
        if not isinstance(segment, dict):
            continue
        try:
            seg_start = float(segment.get("timeline_start"))
            seg_end = float(segment.get("timeline_end"))
        except (TypeError, ValueError):
            continue
        if seg_end > start and seg_start < end:
            rows.append(segment)
    rows.sort(key=lambda row: (float(row.get("timeline_start") or 0.0),
                               float(row.get("timeline_end") or 0.0)))
    return rows


def _speaker_for_track(segments: Sequence[dict], track: Optional[str],
                       start: float, end: float) -> Optional[str]:
    """The transcript's speaker for a world track, by source-time overlap.

    The transcript's speaker labels and the source's face tracks are two
    measurements of the same moment; the join is the overlap in the
    SOURCE's clock, which both carry. The VI-013 benchmark's own join,
    unchanged: the moment the world attributes is the moment the plan
    attributed, so agreement is agreement and not a method difference.
    """
    if not track:
        return None
    best: Optional[str] = None
    best_overlap = 0.0
    for segment in segments:
        seg_start = segment.get("source_start")
        seg_end = segment.get("source_end")
        if seg_start is None or seg_end is None:
            continue
        try:
            overlap = (min(float(seg_end), end)
                       - max(float(seg_start), start))
        except (TypeError, ValueError):
            continue
        if overlap > best_overlap:
            best_overlap = overlap
            best = segment.get("speaker")
    return best


def _clips_by_source(catalog: Sequence[dict],
                     recorded: dict) -> Dict[str, dict]:
    """`{source_file: {clip_id, digest}}` for catalogued, fingerprinted clips.

    The digest is the RUNNER's record, never a fresh fingerprint: the
    planning read must not hear the footage, and a file the runner never
    fingerprinted has no identity to ask about.
    """
    rows: Dict[str, dict] = {}
    for clip in catalog or ():
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id")
        source_file = (clip.get("source_file") or clip.get("path") or "")
        if not clip_id or not source_file:
            continue
        entry = (recorded or {}).get(clip_id) or {}
        digest = entry.get("content_digest")
        if not digest:
            continue
        rows.setdefault(str(source_file), {
            "clip_id": clip_id, "digest": str(digest)})
    return rows


def _whole_view_bytes(digest: str, root: Optional[Path]) -> Optional[int]:
    """The whole per-second document's bytes, or None when never built.

    None is not zero: a world model nobody built cost no planning call
    anything, and reporting it as zero bytes would read as a free read.
    """
    from library.tools import source_memory
    path = (source_memory.source_dir(digest, root)
            / source_memory.SLOT_WORLD_MODEL)
    try:
        return path.stat().st_size
    except OSError:
        return None


def _world_share(world: Any, segments: Sequence[dict]) -> tuple:
    """`(share, facts, lanes_used, lanes_absent)` for one candidate span.

    One narrow speech question per source span: `World.facts` over the
    segment's own source seconds, joined to the transcript's speakers by
    source-time overlap. Seconds are clipped to the segment the words
    came from, so a world second is never counted where the plan heard
    no word.
    """
    facts: List[dict] = []
    for segment in segments:
        seg_start = segment.get("source_start")
        seg_end = segment.get("source_end")
        if seg_start is None or seg_end is None:
            continue
        try:
            seg_start_f, seg_end_f = float(seg_start), float(seg_end)
        except (TypeError, ValueError):
            continue
        if seg_end_f <= seg_start_f:
            continue
        for fact in world.facts(seg_start_f, seg_end_f, ["speech"]):
            # Tag the answering segment's bounds on the row: a fact
            # spanning a boundary is answered twice, and each half is
            # clipped to its own segment below. Stripped before the
            # rows leave this function, so the tag never reaches the
            # payload a planning call carries.
            if isinstance(fact, dict):
                fact["_segment"] = [seg_start_f, seg_end_f]
            facts.append(fact)
    share: Dict[str, float] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("kind") != "speaking":
            continue
        track = fact.get("entity")
        try:
            fact_start, fact_end = float(fact["start"]), float(fact["end"])
        except (TypeError, ValueError, KeyError):
            continue
        speaker = _speaker_for_track(segments, track, fact_start, fact_end)
        if not speaker:
            continue
        # Clipped to the segment whose query returned it, and SUMMED:
        # a fact spanning a segment boundary is answered by both
        # segments' queries, and both halves count. Bound segments are
        # disjoint, so no second is counted twice.
        owner = fact.get("_segment")
        try:
            seg_start = float(owner[0])
            seg_end = float(owner[1])
        except (TypeError, ValueError, IndexError):
            continue
        spoken = max(0.0, min(seg_end, fact_end)
                     - max(seg_start, fact_start))
        if spoken > 0.0:
            share[speaker] = share.get(speaker, 0.0) + spoken
    # The lanes THIS question consulted - m1's utterances and m7's turns -
    # and only those. Asking which lanes exist would materialise every
    # lane (M3's faces included) for a question about speech, which is
    # exactly the whole-view cost this lane removes.
    lanes_used = sorted({
        row["provenance"]["lane"] for row in facts
        if isinstance(row, dict) and isinstance(row.get("provenance"),
                                               dict)
        and row["provenance"].get("lane")})
    lanes_absent = sorted(
        lane for lane in ("m1", "m7") if world.lane(lane) is None)
    for fact in facts:
        if isinstance(fact, dict):
            fact.pop("_segment", None)
    return share, facts, lanes_used, lanes_absent


def _normalise(share: Dict[str, float]) -> Dict[str, float]:
    total = sum(share.values())
    if total <= 0.0:
        return {}
    return {name: round(value / total, 4)
            for name, value in share.items()}


def attribute_candidates(candidates: Sequence[Any],
                         transcript: dict,
                         project_folder: str,
                         catalog: Optional[Sequence[dict]],
                         *,
                         memory_root: Optional[str] = None,
                         budget_seconds: float = 120.0,
                         selection: Optional[Sequence[Any]] = None,
                         ) -> Report:
    """Ask the world who speaks in each candidate's source seconds.

    `candidates` are the bridge's `reel_candidates` rows; `transcript`
    is the timeline transcript they were measured from; `catalog` is
    the routed clip catalog (None when the run never catalogued - the
    attribution is then SKIPPED with the reason, never guessed).
    `selection` is the published moments, so the report can say which
    candidates the selector chose.

    The budget is spent in candidate order. A candidate not reached is
    UNATTRIBUTED with the reason - never attributed, and never wrong.
    """
    from library.tools import source_memory, world_model_query

    started = time.perf_counter()
    explicit_root = (Path(memory_root).expanduser()
                     if memory_root else None)
    report = Report(budget_seconds=float(budget_seconds))
    report.plan_digest = plan_digest_for(candidates)

    recorded = source_memory.load_recorded_fingerprints(project_folder or "")
    clips = _clips_by_source(catalog or (), recorded)
    if not catalog:
        reason = ("no clip catalog on this run: candidate spans "
                  "cannot be verified against the catalog, so "
                  "nothing was asked")
        report.reasons.append(reason)
        for index, candidate in enumerate(candidates or (), 1):
            if not isinstance(candidate, dict):
                continue
            report.candidates_asked += 1
            report.findings.append(CandidateAttribution(
                candidate=index,
                start=float(candidate.get("start") or 0.0),
                end=float(candidate.get("end") or 0.0),
                plan_speakers=list(candidate.get("speakers") or []),
                plan_share={name: round(float(value), 4) for name, value in
                            (candidate.get("share_by_speaker") or {}).items()},
                status="skipped_no_catalog",
                detail=reason))
        return report

    try:
        from library.tools import search_in_planning as _sip
    except ImportError:  # pragma: no cover - planning without the index
        _sip = None  # type: ignore[assignment]

    worlds: Dict[str, Any] = {}
    try:
        from library.tools import world_model_query as _wmq
        _roster = _wmq.person_index(project_folder or "")
    except Exception:  # noqa: BLE001 - attribution never depends on names
        _roster = {}
    rows = list(candidates or ())
    for index, candidate in enumerate(rows, 1):
        if not isinstance(candidate, dict):
            continue
        try:
            cand_start = float(candidate.get("start"))
            cand_end = float(candidate.get("end"))
        except (TypeError, ValueError):
            continue
        report.candidates_asked += 1
        finding = CandidateAttribution(
            candidate=index, start=cand_start, end=cand_end,
            plan_speakers=list(candidate.get("speakers") or []),
            plan_share={name: round(float(value), 4) for name, value in
                        (candidate.get("share_by_speaker") or {}).items()})

        remaining = float(budget_seconds) - (time.perf_counter() - started)
        if remaining <= 0.0:
            finding.status = "skipped_budget"
            finding.detail = ("the attribution budget ran out before "
                              "this candidate was asked")
            report.findings.append(finding)
            continue

        segments = _overlapping_segments(transcript or {}, cand_start,
                                         cand_end)
        if not segments:
            finding.status = "unattributed_no_speech"
            finding.detail = ("the transcript places no bound segment "
                              "in this candidate, so there is no claim "
                              "to attribute")
            report.findings.append(finding)
            continue

        per_source: Dict[str, List[dict]] = {}
        unverified: List[str] = []
        for segment in segments:
            source_file = str(segment.get("source_file") or "")
            row = clips.get(source_file)
            if row is None:
                unverified.append(
                    f"{source_file or '<no file>'} has no catalogued "
                    "clip with a recorded digest")
                continue
            seg_start = segment.get("source_start")
            seg_end = segment.get("source_end")
            if seg_start is None or seg_end is None:
                continue
            try:
                seg_start_f, seg_end_f = float(seg_start), float(seg_end)
            except (TypeError, ValueError):
                continue
            if _sip is not None:
                verdict = _sip.verify_span(row["clip_id"], seg_start_f,
                                          seg_end_f, list(catalog or ()))
                if not verdict.get("ok"):
                    unverified.append(
                        f"{row['clip_id']} span {seg_start_f:.3f}-"
                        f"{seg_end_f:.3f}s: {verdict.get('reason')}")
                    continue
            per_source.setdefault(row["digest"], []).append(segment)

        if unverified and not per_source:
            finding.status = "unverified_no_digest"
            finding.detail = "; ".join(unverified[:3])
            report.findings.append(finding)
            continue

        share: Dict[str, float] = {}
        facts: List[dict] = []
        lanes_used: List[str] = []
        lanes_absent: List[str] = []
        whole_bytes: List[int] = []
        for digest, owned in per_source.items():
            key = str(digest)
            entry = worlds.get(key)
            if entry is None:
                root = explicit_root or _resolve_root(project_folder, key)
                created = world_model_query.World(
                    key, root).with_names(_names_for_key(_roster, key))
                entry = (created, root)
                worlds[key] = entry
            world_obj, world_root = entry
            part, part_facts, used, absent = _world_share(world_obj, owned)
            for name, seconds in part.items():
                share[name] = share.get(name, 0.0) + seconds
            facts.extend(part_facts)
            lanes_used.extend(used)
            lanes_absent.extend(absent)
            size = _whole_view_bytes(key, world_root)
            if size is not None:
                whole_bytes.append(size)

        if not share:
            finding.status = "unattributed_no_speech"
            finding.detail = (
                "the world measured no speaking in these source "
                "seconds" + ("; " + "; ".join(unverified[:2]) if unverified
                              else ""))
            finding.facts = len(facts)
            finding.lanes_used = sorted(set(lanes_used))
            finding.lanes_absent = sorted(set(lanes_absent))
            (finding.query_bytes,
             finding.query_o200k) = _query_size(facts)
            report.findings.append(finding)
            continue

        finding.world_speakers = sorted(share)
        finding.world_share = _normalise(share)
        finding.speakers_match = (set(finding.world_share)
                                  == set(finding.plan_share))
        both = set(finding.world_share) | set(finding.plan_share)
        if both:
            finding.mean_abs_share_difference = round(sum(
                abs(finding.world_share.get(name, 0.0)
                    - finding.plan_share.get(name, 0.0)) for name in both)
                / len(both), 4)
        finding.facts = len(facts)
        finding.facts_with_provenance = sum(
            1 for row in facts if isinstance(row, dict)
            and isinstance(row.get("provenance"), dict)
            and row["provenance"].get("lane"))
        finding.lanes_used = sorted(set(lanes_used))
        finding.lanes_absent = sorted(set(lanes_absent))
        (finding.query_bytes,
         finding.query_o200k) = _query_size(facts)
        finding.whole_view_bytes = sum(whole_bytes) or None
        finding.status = "attributed"
        finding.detail = ("the world's speaker set "
                          + ("matches" if finding.speakers_match else
                             "differs from") + " the plan's")
        if unverified:
            finding.detail += "; " + "; ".join(unverified[:2])
        report.candidates_attributed += 1
        report.findings.append(finding)

    report.selected_candidates = _selected_candidates(rows, selection)
    report.seconds = round(time.perf_counter() - started, 3)
    return report


def _resolve_root(project_folder: str,
                  digest: str) -> Optional[Path]:
    """Where this digest's lanes are read from.

    The project's read root when it holds the digest's transcript
    lane, else the machine store: measurements taken before projects
    scoped their own still live there, and a private scope that holds
    only verdicts must not read as "this source was never measured".
    An explicit `--memory-root` (tests, benchmarks) bypasses this and
    is used as given.
    """
    from library.tools import source_memory
    candidates: List[Path] = []
    # Best-effort root discovery: a root that cannot be read is not a
    # root, and the loop below falls through to whatever remains.
    for probe in (lambda: source_memory.project_read_root(
            project_folder or ""),
                  source_memory.machine_memory_root):
        try:
            candidates.append(probe())
        except Exception:  # noqa: BLE001, S110 - unreadable is unusable
            continue
    for root in candidates:
        try:
            lane = (source_memory.source_dir(digest, root)
                    / source_memory.SLOT_TRANSCRIPT)
            if lane.is_file():
                return root
        except OSError:
            continue
    return candidates[0] if candidates else None


def _names_for_key(roster: dict, digest: str) -> Dict[str, str]:
    """The project's declared person names for one source's tracks.

    Names travel so a world fact about `face_001` can arrive carrying
    the name the project gave that person; a track nobody named stays
    unnamed rather than guessed. The roster is read once per sweep (a
    plan names the same few sources over and over); absent on any
    failure, because attribution never depends on the roster.
    """
    try:
        owners = (roster.get("track_owner") or {}).get(digest, {})
        names = roster.get("persons") or {}
        return {track: names[person] for track, person in owners.items()
                if names.get(person)}
    except Exception:  # noqa: BLE001 - unnamed tracks stay unnamed
        return {}


def _query_size(facts: Sequence[Any]) -> tuple:
    """What one candidate's world answer costs the planning call.

    `(utf8_bytes, o200k_or_None)`: bytes are exact and always
    present; the token count names its tokenizer and is None where
    this environment cannot run it.
    """
    try:
        payload = json.dumps({"facts": list(facts or [])},
                             ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return 0, None
    try:
        from library.tools import token_measure
        counts = token_measure.measure(payload)
        return (int(counts.get(token_measure.BYTES,
                               len(payload.encode("utf-8")))),
                counts.get(token_measure.O200K))
    except Exception:  # noqa: BLE001 - bytes are exact without it
        return len(payload.encode("utf-8")), None


def _selected_candidates(rows: Sequence[Any],
                         selection: Optional[Sequence[Any]]) -> List[int]:
    """The candidate indexes the published selection chose, 1-based.

    Matched by timeline overlap: a moment the selector chose sits
    inside the candidate window it was chosen from. A moment inside no
    window is reported by no candidate rather than guessed into one.
    """
    chosen: List[int] = []
    moments = [m for m in (selection or ()) if m is not None]
    for index, candidate in enumerate(rows, 1):
        if not isinstance(candidate, dict):
            continue
        try:
            cand_start = float(candidate.get("start"))
            cand_end = float(candidate.get("end"))
        except (TypeError, ValueError):
            continue
        for moment in moments:
            try:
                if isinstance(moment, dict):
                    m_start = float(moment.get("timeline_start",
                                               moment.get("start")))
                    m_end = float(moment.get("timeline_end",
                                             moment.get("end")))
                else:
                    m_start = float(moment.timeline_start)
                    m_end = float(moment.timeline_end)
            except (TypeError, ValueError, AttributeError):
                continue
            midpoint = (m_start + m_end) / 2.0
            if cand_start <= midpoint < cand_end and index not in chosen:
                chosen.append(index)
    return sorted(chosen)


def attribute_and_write(candidates: Sequence[Any],
                        transcript: dict,
                        project_folder: str,
                        catalog: Optional[Sequence[dict]],
                        *,
                        memory_root: Optional[str] = None,
                        budget_seconds: float = 120.0,
                        selection: Optional[Sequence[Any]] = None,
                        ) -> Optional[str]:
    """Attribute a plan's candidates and file the report; return its path.

    None when there is nowhere to file it (no project folder): the
    caller prints and continues, because world evidence never breaks
    selection.
    """
    if not project_folder:
        return None
    report = attribute_candidates(
        candidates, transcript, project_folder, catalog,
        memory_root=memory_root, budget_seconds=budget_seconds,
        selection=selection)
    return write_report(project_folder, report)


def report_path(project_folder: str) -> str:
    """Where the report is written, inside the project's review area."""
    from library.tools.project_layout import Area, ProjectLayout
    layout = ProjectLayout(project_folder)
    return str(layout.write_path(Area.REVIEW, FINDINGS_FILENAME))


def write_report(project_folder: str, report: Report) -> str:
    """Write the report where a reader can find it, and return its path.

    The write holds the project's own file lock, the way the other
    review-area records do: a selection and a verification can land at
    the same moment, and the file they share is written once.
    """
    path = report_path(project_folder)
    from library.tools.project_file_lock import lock_project_file
    with lock_project_file(path), open(path, "w",
                                        encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=1)
    return path


def read_report(project_folder: str) -> Optional[dict]:
    """The report, or None when none has been written."""
    path = report_path(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Attribute a project's reel candidates from the command line."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="reel_world_attribution",
        description="Ask the world who speaks in each reel candidate's "
                    "source seconds, and file the agreement with the plan.")
    parser.add_argument("project")
    parser.add_argument("--memory-root")
    parser.add_argument("--budget-seconds", type=float, default=120.0)
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    from library.tools import capability_outputs, source_memory
    state_path = Path(args.project).expanduser() / "pipeline_data.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no readable pipeline_data.json in {args.project}: {exc}")
        return 2
    candidates = (capability_outputs.value(
        state, "reel.candidates", "reel_candidates")
        or (state.get("capability_outputs") or {}).get(
            "reel_candidates") or [])
    transcript_path = (Path(args.project).expanduser() / "pipeline_output"
                       / "scratch" / "timeline_transcript" / "transcript.json")
    try:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no readable transcript at {transcript_path}: {exc}")
        return 2
    catalog = source_memory.load_catalog(str(Path(args.project).expanduser()))
    selection = (capability_outputs.value(
        state, "reel.select", "reel_selection") or {})
    moments = (selection.get("moments")
               if isinstance(selection, dict) else None)

    report = attribute_candidates(
        candidates, transcript, str(Path(args.project).expanduser()),
        catalog, memory_root=args.memory_root,
        budget_seconds=args.budget_seconds, selection=moments)
    if args.out:
        Path(args.out).expanduser().write_text(
            json.dumps(report.to_dict(), indent=1) + "\n", encoding="utf-8")
        print(args.out)
    else:
        path = write_report(str(Path(args.project).expanduser()), report)
        print(path)
    print(json.dumps({
        "candidates_asked": report.candidates_asked,
        "candidates_attributed": report.candidates_attributed,
        "speaker_set_match": report.speaker_set_match(),
        "speaker_share_within_0.10": report.share_within(),
        "context_bytes": report.context_bytes(),
        "seconds": report.seconds,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
