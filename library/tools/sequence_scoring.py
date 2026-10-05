"""Global ordering optimization for the speech sequence.

Gap 9: speech sequencing is one-pass with no global optimization. The LLM
produces an ordering in one call; this module is the deterministic second
pass that scores the ordering and re-orders on a low score.

The scoring pass is O(passages^2) - every pair of passages is compared
once. For 20 passages that is 190 comparisons, well under 10 ms.

Scoring dimensions:
- Redundancy: two passages saying the same thing (content-word overlap)
- Setup/payoff: a setup passage should be adjacent to its payoff
- Continuity: passages that flow well together
- Variety: not over-using one clip

This module imports nothing from steps or processes: it reads the
passages and intent model a state carries, the same reader every
consumer has.
"""
from __future__ import annotations

import re

# Content words are the vocabulary that carries meaning - stopwords are
# stripped so "the cat sat on the mat" and "a cat sat on a mat" read as
# the same content.
_STOPWORDS = frozenset("""
a an the and or but so if of to in on at by for with from as is it its
that this these those be was were been are am do does did doing have has
had you your yours we our i my me they them their he she his her not no
just like really about into over than then there here what which who whom
when where why how all any some more most very can could will would shall
should may might must
""".split())

# Two passages whose content vocabulary coincides above this fraction
# are redundant - they say the same thing.
REDUNDANCY_THRESHOLD = 0.65

# A setup passage should be immediately before its payoff passage.
# This is the adjacency the fidelity test checks.
SETUP_PAYOFF_ADJACENCY = 1

# A clip contributing more than this fraction of passages is over-used.
VARIETY_FRACTION = 0.5


def _content_words(text: str) -> set:
    """The content words of a passage, lowercased and stopword-free."""
    words = re.findall(r"[a-z0-9']+", str(text).lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def content_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity of two passages' content words.

    1.0 means identical content vocabulary, 0.0 means no overlap.
    """
    wa, wb = _content_words(text_a), _content_words(text_b)
    if not wa or not wb:
        return 0.0
    shared = len(wa & wb)
    return shared / len(wa | wb)


def _passage_text(passage: dict) -> str:
    """The text of a passage, or empty string."""
    return str(passage.get("text", ""))


def _passage_clip_id(passage: dict) -> str:
    """The clip_id of a passage, or empty string."""
    return str(passage.get("clip_id", ""))


def _passage_serves_beats(passage: dict) -> list:
    """The beats a passage serves, as a list of beat_id strings."""
    beats = passage.get("serves_beats", [])
    if not isinstance(beats, list):
        return []
    return [str(b) for b in beats if b]


def _passage_source_start(passage: dict):
    """The source_start of a passage, or None."""
    val = passage.get("source_start")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_redundancy(passages: list) -> tuple:
    """Score the ordering for redundant adjacent passages.

    Returns `(score, problems)` where score is 0-1 (1 = no redundancy)
    and problems is a list of human-readable descriptions.
    """
    if len(passages) < 2:
        return 1.0, []
    problems = []
    penalties = []
    for i in range(len(passages) - 1):
        a, b = passages[i], passages[i + 1]
        sim = content_similarity(_passage_text(a), _passage_text(b))
        if sim >= REDUNDANCY_THRESHOLD:
            penalties.append(sim)
            problems.append(
                f"passages {i} and {i + 1} are redundant "
                f"(content similarity {sim:.2f})"
            )
    if not penalties:
        return 1.0, []
    # Each redundant pair costs proportionally to its similarity
    penalty = sum(penalties) / (len(passages) - 1)
    return max(0.0, 1.0 - penalty), problems


def score_setup_payoff(passages: list, intent_model: dict) -> tuple:
    """Score the ordering for setup/payoff adjacency.

    For each setup/payoff link in the intent model, the passage serving
    the setup beat should be immediately before the passage serving the
    payoff beat.

    Returns `(score, problems)` where score is 0-1 (1 = all setups
    adjacent to their payoffs) and problems is a list of descriptions.
    """
    if not intent_model:
        return 1.0, []
    from library.tools import intent_model as im

    links = im.setup_payoff_links(intent_model)
    if not links:
        return 1.0, []

    # Map each beat to the indices of passages serving it
    beat_to_indices = {}
    for i, passage in enumerate(passages):
        for beat_id in _passage_serves_beats(passage):
            beat_to_indices.setdefault(beat_id, []).append(i)

    problems = []
    penalties = []
    for beat_id, role, other_beat_id in links:
        setup_indices = beat_to_indices.get(beat_id, [])
        payoff_indices = beat_to_indices.get(other_beat_id, [])
        if not setup_indices or not payoff_indices:
            continue
        # Check if any setup passage is immediately before any payoff passage
        adjacent = any(
            payoff_idx - setup_idx == SETUP_PAYOFF_ADJACENCY
            for setup_idx in setup_indices
            for payoff_idx in payoff_indices
        )
        if not adjacent:
            penalties.append(1.0)
            problems.append(
                f"setup beat {beat_id!r} is not adjacent to its payoff "
                f"beat {other_beat_id!r}"
            )
    if not penalties:
        return 1.0, []
    penalty = sum(penalties) / len(links)
    return max(0.0, 1.0 - penalty), problems


def score_continuity(passages: list) -> tuple:
    """Score the ordering for continuity.

    Passages from the same clip should be in source order. Passages that
    share content words flow better than those that do not.

    Returns `(score, problems)` where score is 0-1 (1 = good continuity).
    """
    if len(passages) < 2:
        return 1.0, []
    problems = []
    penalties = []
    for i in range(len(passages) - 1):
        a, b = passages[i], passages[i + 1]
        clip_a, clip_b = _passage_clip_id(a), _passage_clip_id(b)
        if clip_a and clip_b and clip_a == clip_b:
            start_a = _passage_source_start(a)
            start_b = _passage_source_start(b)
            if start_a is not None and start_b is not None:
                if start_b < start_a:
                    penalties.append(0.5)
                    problems.append(
                        f"passages {i} and {i + 1} are from clip "
                        f"{clip_a!r} but out of source order"
                    )
    if not penalties:
        return 1.0, []
    penalty = sum(penalties) / (len(passages) - 1)
    return max(0.0, 1.0 - penalty), problems


def score_variety(passages: list) -> tuple:
    """Score the ordering for variety.

    A clip contributing more than VARIETY_FRACTION of passages is
    over-used.

    Returns `(score, problems)` where score is 0-1 (1 = good variety).
    """
    if not passages:
        return 1.0, []
    clip_counts = {}
    for passage in passages:
        clip_id = _passage_clip_id(passage)
        if clip_id:
            clip_counts[clip_id] = clip_counts.get(clip_id, 0) + 1
    if not clip_counts:
        return 1.0, []
    max_count = max(clip_counts.values())
    fraction = max_count / len(passages)
    if fraction <= VARIETY_FRACTION:
        return 1.0, []
    # Penalty proportional to how much the fraction exceeds the threshold
    penalty = (fraction - VARIETY_FRACTION) / (1.0 - VARIETY_FRACTION)
    over_used = [c for c, n in clip_counts.items() if n == max_count]
    problems = [
        f"clip {c!r} contributes {max_count}/{len(passages)} passages "
        f"({fraction:.0%}), over the {VARIETY_FRACTION:.0%} variety bar"
        for c in over_used
    ]
    return max(0.0, 1.0 - penalty), problems


def score_ordering(passages: list, intent_model: dict = None) -> dict:
    """Score an ordering of passages.

    Returns a dict with the total score (0-1, higher is better), the
    per-dimension scores, and a list of human-readable problems.

    The total is the product of the HARD dimensions (redundancy and
    setup/payoff) - a zero in either means the ordering has a real
    defect. The soft dimensions (continuity, variety) are reported
    but do not affect the total, so a good ordering is not re-ordered
    because of a soft concern.
    """
    redundancy_score, redundancy_problems = score_redundancy(passages)
    setup_payoff_score, setup_payoff_problems = score_setup_payoff(
        passages, intent_model)
    continuity_score, continuity_problems = score_continuity(passages)
    variety_score, variety_problems = score_variety(passages)

    total = redundancy_score * setup_payoff_score

    return {
        "total": round(total, 4),
        "redundancy": round(redundancy_score, 4),
        "setup_payoff": round(setup_payoff_score, 4),
        "continuity": round(continuity_score, 4),
        "variety": round(variety_score, 4),
        "problems": (redundancy_problems + setup_payoff_problems +
                     continuity_problems + variety_problems),
    }


def _try_swap(passages: list, i: int, j: int, intent_model: dict) -> float:
    """Score the ordering if passages i and j were swapped."""
    swapped = list(passages)
    swapped[i], swapped[j] = swapped[j], swapped[i]
    return score_ordering(swapped, intent_model)["total"]


def _try_move(passages: list, i: int, j: int, intent_model: dict) -> float:
    """Score the ordering if passage i were moved to position j."""
    moved = list(passages)
    passage = moved.pop(i)
    moved.insert(j, passage)
    return score_ordering(moved, intent_model)["total"]


def reorder_passages(passages: list, intent_model: dict = None,
                     max_iterations: int = 10) -> tuple:
    """Re-order passages to improve the score.

    A local search: try all adjacent swaps and single-passage moves,
    keep the best improvement, repeat until no improvement or
    max_iterations.

    Returns `(reordered_passages, score_before, score_after)`.
    """
    if len(passages) < 2:
        return list(passages), 1.0, 1.0

    current = list(passages)
    score_before = score_ordering(current, intent_model)["total"]
    best_score = score_before

    for _ in range(max_iterations):
        best_move = None
        best_move_score = best_score

        # Try all adjacent swaps
        for i in range(len(current) - 1):
            score = _try_swap(current, i, i + 1, intent_model)
            if score > best_move_score + 1e-9:
                best_move_score = score
                best_move = ("swap", i, i + 1)

        # Try all single-passage moves
        for i in range(len(current)):
            for j in range(len(current)):
                if i == j:
                    continue
                score = _try_move(current, i, j, intent_model)
                if score > best_move_score + 1e-9:
                    best_move_score = score
                    best_move = ("move", i, j)

        if best_move is None:
            break

        if best_move[0] == "swap":
            i, j = best_move[1], best_move[2]
            current[i], current[j] = current[j], current[i]
        else:
            i, j = best_move[1], best_move[2]
            passage = current.pop(i)
            current.insert(j, passage)

        best_score = best_move_score

    score_after = score_ordering(current, intent_model)["total"]
    return current, score_before, score_after


def optimize_ordering(passages: list, intent_model: dict = None,
                      threshold: float = 0.99) -> dict:
    """Score and re-order a passage ordering if the score is low.

    Returns a dict with the original and optimized orderings, the
    scores, and whether a re-ordering was made.
    """
    score_before = score_ordering(passages, intent_model)
    if score_before["total"] >= threshold:
        return {
            "passages": list(passages),
            "score_before": score_before,
            "score_after": score_before,
            "reordered": False,
        }
    optimized, _, score_after_val = reorder_passages(passages, intent_model)
    score_after = score_ordering(optimized, intent_model)
    return {
        "passages": optimized,
        "score_before": score_before,
        "score_after": score_after,
        "reordered": True,
    }
