"""
Engagement Scorer for OpusClip-style hook and flow evaluation.
"""

def score_hook(segment, prosody_data, semantic_data):
    """
    Score how effectively the first 3-5 seconds grab attention (0-100).
    High hook: starts with high energy, question, or counter-intuitive statement.
    Low hook: starts with "um", filler, low-energy preamble.
    """
    score = 50
    text = (segment.get("text") or "").lower()
    
    # Prosody input
    energy = prosody_data.get("energy_rms", 0) if prosody_data else 0
    if energy > 0.8:
        score += 20
    elif energy < 0.3:
        score -= 20
        
    # Semantic input
    if text.startswith("um") or text.startswith("so") or text.startswith("well"):
        score -= 15
    if "?" in text[:50]:
        score += 20
    if "you" in text[:30] or "imagine" in text[:30]:
        score += 10
        
    return max(0, min(100, score))

def score_flow(segment, speech_sequence, semantic_data):
    """
    Score logical structure and progression (0-100).
    High flow: clear narrative arc, setup -> development -> payoff.
    Low flow: disconnected topics, unfinished thoughts.
    """
    score = 60
    # Dummy flow scoring logic for scaffolding
    text = (segment.get("text") or "").lower()
    if "first" in text or "then" in text or "finally" in text:
        score += 20
    if len(text.split()) < 5:
        score -= 30
    return max(0, min(100, score))

def score_value(segment, semantic_data):
    """
    Score relevance and substance of the content (0-100).
    High value: teaches something, shares a unique insight.
    Low value: generic filler, repetitive content.
    """
    score = 60
    text = (segment.get("text") or "").lower()
    if len(text.split()) > 20:
        score += 20
    if "important" in text or "key" in text or "insight" in text:
        score += 15
    return max(0, min(100, score))

def compute_engagement(segment, prosody_data, semantic_data, speech_sequence):
    hook = score_hook(segment, prosody_data, semantic_data)
    flow = score_flow(segment, speech_sequence, semantic_data)
    value = score_value(segment, semantic_data)
    
    composite = int(0.35 * hook + 0.30 * flow + 0.35 * value)
    
    rationale = f"Hook:{hook}, Flow:{flow}, Value:{value}"
    
    return {
        "hook": hook,
        "flow": flow,
        "value": value,
        "composite": composite,
        "rationale": rationale
    }
