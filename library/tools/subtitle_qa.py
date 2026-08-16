from library.tools.render_qa import RenderQAResult
from typing import List, Dict, Any

def verify_subtitle_timing(subtitle_data: List[Dict[str, Any]], total_duration: float = None) -> List[RenderQAResult]:
    """Check subtitle entries for timing issues."""
    results = []
    
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
            
        # 4. Gap between consecutive subtitles (> 2s = dead caption time)
        if i < len(sorted_subs) - 1:
            next_start = sorted_subs[i+1].get('timeline_start', 0)
            gap = next_start - end
            if gap > 2.0:
                gaps.append((i, i+1, gap))
                
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
                severity="error",
                detail=f"Subtitle at index {i} ends at {end}s but timeline is {total_duration}s"
            ))
                
    results.append(RenderQAResult(
        metric="subtitle_overlap",
        passed=len(overlaps) == 0,
        value=overlaps,
        threshold=0,
        severity="error",
        detail=f"Found {len(overlaps)} overlapping subtitles" if overlaps else "No overlapping subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_too_short",
        passed=len(too_short) == 0,
        value=too_short,
        threshold=0.5,
        severity="warning",
        detail=f"Found {len(too_short)} subtitles shorter than 0.5s" if too_short else "No overly short subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_too_long",
        passed=len(too_long) == 0,
        value=too_long,
        threshold=5.0,
        severity="warning",
        detail=f"Found {len(too_long)} subtitles longer than 5s" if too_long else "No overly long subtitles"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_gaps",
        passed=len(gaps) == 0,
        value=gaps,
        threshold=2.0,
        severity="info",
        detail=f"Found {len(gaps)} gaps longer than 2s" if gaps else "No dead caption gaps"
    ))
    
    results.append(RenderQAResult(
        metric="subtitle_read_speed",
        passed=len(too_fast) == 0,
        value=too_fast,
        threshold=25.0,
        severity="warning",
        detail=f"Found {len(too_fast)} subtitles too fast to read (>25 chars/sec)" if too_fast else "Subtitle reading speed is OK"
    ))
    
    return results

