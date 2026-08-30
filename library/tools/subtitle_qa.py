from library.tools.render_qa import RenderQAResult
from library.tools.transition_carriers import block_reaches_v1
from library.tools.bookends import block_bookend
from typing import Any, Dict, List

# How loud each check is WHEN IT FAILS.  A check with nothing to report
# is `info`, which is what `render_qa` has always done
# (`severity="error" if not passed else "info"`) and what this module used
# not to do: every result here carried its failing severity whether or not
# it had failed, so a report saying "No overlapping subtitles" arrived
# labelled `error`.  A reader cannot tell an advisory note from a real one
# if a clean check shouts, so the severity moves with the verdict.  The
# thresholds themselves are untouched.
FAILING_SEVERITY = {
    "subtitle_overflow": "error",
    "subtitle_overlap": "error",
    "subtitle_too_short": "warning",
    "subtitle_too_long": "warning",
    "subtitle_gaps": "info",
    "subtitle_read_speed": "warning",
}


def _severity(metric: str, passed: bool) -> str:
    return "info" if passed else FAILING_SEVERITY[metric]


def speech_spans(spine_blocks) -> List[tuple]:
    """The stretches of timeline a caption could cover, off the spine.

    A caption covers speech.  Everything else - a B-roll breath, an
    intro card, a planned silence - is time the plan chose to have no
    words in, and an uncaptioned second there is the plan working.
    """
    spans = []
    for block in spine_blocks or []:
        if not isinstance(block, dict):
            continue
        if not block_reaches_v1(block) or block_bookend(block):
            continue
        start = block.get("timeline_start")
        end = block.get("timeline_end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            if float(end) > float(start):
                spans.append((float(start), float(end)))
    return sorted(spans)


def _seconds_inside(spans, start: float, end: float) -> float:
    """How much of [start, end) falls inside any span."""
    total = 0.0
    for a, b in spans:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += hi - lo
    return total


def verify_subtitle_timing(subtitle_data: List[Dict[str, Any]],
                           total_duration: float = None,
                           spine_blocks=None) -> List[RenderQAResult]:
    """Check subtitle entries for timing issues.

    `spine_blocks` is the plan's own structure, and `subtitle_gaps` is
    measured against it.  Without it the gap check has no idea the spine
    HAS non-speech blocks: on project 001 its four reported gaps were
    3.0, 3.5, 3.0 and 3.0 seconds - exactly the four B-roll breaths the
    plan wrote at spine blocks 1, 4, 6 and 9 - reported to a creative
    step (#332 gave the findings a reader) as a fault, carrying a legend
    that blamed the aligner.  Caption coverage INSIDE the speech blocks
    was 33.80s of 40.10s = 84.3%, and the uncaptioned stretches there ran
    0.40-1.12s: real pauses in the speech.

    A caller with no spine gets the old whole-timeline measurement and
    the result SAYS so, rather than reading as a measurement against the
    plan.
    """
    results = []
    speech = speech_spans(spine_blocks)
    gaps_measured_against = (
        "speech blocks" if speech else "the whole timeline (no spine "
        "supplied, so a planned non-speech beat counts as a gap)")
    
    # Sort by start time to make checks easier
    sorted_subs = sorted(subtitle_data, key=lambda s: s.get('timeline_start', 0))
    
    overlaps = []
    too_short = []
    too_long = []
    gaps = []
    too_fast = []
    
    for i, sub in enumerate(sorted_subs):
        start = sub.get('timeline_start', 0)
        end = sub.get('timeline_end', 0)
        dur = end - start
        text = sub.get('text', '')
        text_len = len(text)
        
        # 1. Overlapping time ranges
        if i < len(sorted_subs) - 1:
            next_start = sorted_subs[i+1].get('timeline_start', 0)
            if end > next_start + 0.05: # Add small tolerance
                overlaps.append((i, i+1))
                
        # 2. Duration too short (< 0.5s, unreadable)
        if dur > 0 and dur < 0.5:
            too_short.append(i)
            
        # 3. Duration too long (> 5s for shortform, stale)
        if dur > 5.0:
            too_long.append(i)
            
        # 4. Uncaptioned time between consecutive cards. Only the part of
        #    it that falls inside a SPEECH block is dead caption time; the
        #    rest is a beat the plan wrote.
        if i < len(sorted_subs) - 1:
            next_start = sorted_subs[i+1].get('timeline_start', 0)
            gap = next_start - end
            uncovered = (_seconds_inside(speech, end, next_start)
                         if speech else gap)
            if uncovered > 2.0:
                gaps.append((i, i + 1, round(uncovered, 3)))
                
        # 5. Text length vs duration (> 25 chars/sec = too fast to read)
        if dur > 0:
            cps = text_len / dur
            if cps > 25:
                too_fast.append((i, cps))
                
        # 6. Exceeds total duration
        if total_duration is not None and end > total_duration:
            results.append(RenderQAResult(
                metric="subtitle_overflow",
                passed=False,
                value=end,
                threshold=total_duration,
                severity=_severity("subtitle_overflow", False),
                detail=f"Subtitle at index {i} ends at {end}s but timeline is {total_duration}s"
            ))
                
    results.append(RenderQAResult(
        metric="subtitle_overlap",
        passed=len(overlaps) == 0,
        value=overlaps,
        threshold=0,
        severity=_severity("subtitle_overlap", len(overlaps) == 0),
        detail=f"Found {len(overlaps)} overlapping subtitles" if overlaps else "No overlapping subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_too_short",
        passed=len(too_short) == 0,
        value=too_short,
        threshold=0.5,
        severity=_severity("subtitle_too_short", len(too_short) == 0),
        detail=f"Found {len(too_short)} subtitles shorter than 0.5s" if too_short else "No overly short subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_too_long",
        passed=len(too_long) == 0,
        value=too_long,
        threshold=5.0,
        severity=_severity("subtitle_too_long", len(too_long) == 0),
        detail=f"Found {len(too_long)} subtitles longer than 5s" if too_long else "No overly long subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_gaps",
        passed=len(gaps) == 0,
        value=gaps,
        threshold=2.0,
        severity=_severity("subtitle_gaps", len(gaps) == 0),
        detail=(
            f"Found {len(gaps)} uncaptioned stretches longer than 2s, "
            f"measured against {gaps_measured_against}"
            if gaps else
            f"No dead caption gaps, measured against {gaps_measured_against}")
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_read_speed",
        passed=len(too_fast) == 0,
        value=too_fast,
        threshold=25.0,
        severity=_severity("subtitle_read_speed", len(too_fast) == 0),
        detail=f"Found {len(too_fast)} subtitles too fast to read (>25 chars/sec)" if too_fast else "Subtitle reading speed is OK"
    ))
    
    return results

