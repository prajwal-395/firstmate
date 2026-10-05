#!/usr/bin/env python3
"""
Step 4.1: Generate Subtitles

Converts speech segments from the audio spine into styled, timed subtitle
entries. Uses word-level timestamps from WhisperX forced alignment (produced
by Step 1.04, enriched via Bridge 2.02) to assign precise on/off times —
NOT proportional estimation.

Each display group (3-6 words) appears when its first word is spoken and
disappears when the next group appears.

Classification: Deterministic / Data Transformation
Archetype: Data Transformation
Idempotent: Yes

Input:  {"audio_spine": {structure: [...]}, "project_fps": number}
Output: { "subtitle_entries": [...], "total_subtitles": int }
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from library.schemas.brand_template import DEFAULT_CAPTION_CASE
from library.tools.caption_reading import (
    apply_caption_reading,
)
from library.tools.render_fonts import measurable_font_path
from library.tools.safe_area import resolve_safe_area
from library.tools.subtitle_segment_id import slug
from library.tools.subtitle_style import resolve_subtitle_style

# ── Minimum display duration (seconds) ──
# Matches Palmier Pro's AppTheme.Caption.minDisplayDuration.
# Subtitles shorter than this are extended. Any resulting overlap is
# trimmed or merged later without shifting a card away from its first word.
MIN_DISPLAY_DURATION = 0.7

# The hard floor, below which a card flashes rather than reads. Same
# number as `manifest_validator.MIN_CAPTION_DISPLAY_SECONDS` and
# `render_qa`'s `subtitle_too_short`. After block clamping, the completed
# plan holds a short card only through an uncaptioned gap, or merges it
# with a compatible same-block neighbour. It never emits a card below it.
MIN_CAPTION_FLASH_SECONDS = 0.5
DEFAULT_CAPTION_FPS = 24000 / 1001

# A caption also has to stay on screen long enough for its text to be read
# at the QA ceiling. `MIN_DISPLAY_DURATION` remains the normal target; this
# is the maximum rate the partitioner is allowed to plan around.
MAX_CHARACTERS_PER_SECOND = 25.0

# Below this a subtitle has no visible time; clamping a block's entries
# to its bounds can leave a sliver that needs a safe hold or merge.
MIN_VISIBLE_DURATION = 0.08

#: Words per caption card when neither the request nor brand template
#: declares a count (rung 7, finding 31).
DEFAULT_MAX_WORDS_PER_CARD = 6


def resolve_max_words_per_card(brand_effect: dict = None, *,
                               audio_spine: dict = None) -> int:
    """The grouping's word ceiling, and whose number it is.

    An explicit request carried on the spine wins; otherwise a brand
    declaration wins, followed by the grouping default. A stated value
    must be a whole number >= 1; malformed counts refuse instead of
    silently falling through to a different ceiling.
    """
    requested = (audio_spine or {}).get("max_words")
    if requested is not None:
        if (isinstance(requested, bool) or not isinstance(requested, int)):
            raise ValueError(
                f"audio_spine.max_words is {requested!r}: it must be a "
                "whole number of words per card")
        if requested < 1:
            raise ValueError(
                "audio_spine.max_words must be at least 1 word per card")
        return requested
    stated = (brand_effect or {}).get("caption_words_per_card", None)
    if stated is None:
        return DEFAULT_MAX_WORDS_PER_CARD
    if isinstance(stated, bool) or not isinstance(stated, int):
        raise ValueError(
            f"Brand template effect.caption_words_per_card is "
            f"{stated!r}: it must be a whole number of words per card "
            f"(or omitted for the default "
            f"{DEFAULT_MAX_WORDS_PER_CARD}).")
    if stated < 1:
        raise ValueError(
            f"Brand template effect.caption_words_per_card is "
            f"{stated}: a card must carry at least one word.")
    return stated


# ── Caption case transformation ──
# Controlled by the brand template's effect.caption_case setting.

CAPTION_CASES = ("lowercase", "as_written")


class UnknownCaptionCase(ValueError):
    """A template named a caption case that does not exist.

    Raised rather than defaulted. Every unrecognised value used to fall
    through to `text.lower()` "for safety", so a template that typed
    `as-written` or `Lowercase` got every caption on screen lowercased and
    nothing said so. Which case the copy is set in is the template's
    decision; a typo is not a licence to make it here.
    """


def apply_caption_case(text: str, mode: str) -> str:
    """Apply the configured caption case transformation.

    Args:
        text: Raw caption text.
        mode: One of "lowercase" or "as_written".
    """
    if mode == "as_written":
        return text
    if mode == "lowercase":
        return text.lower()
    raise UnknownCaptionCase(
        f"Unknown caption_case {mode!r}. A brand template's "
        f"effect.caption_case must be one of: {', '.join(CAPTION_CASES)}."
    )


# ── Caption fitting, measured in pixels ──
#
# This measurement existed before this change and had never once run. It
# was switched on by `audio_spine["subtitle_style"]["font_path"]`, and no
# producer anywhere in the tree wrote `subtitle_style` into the spine, so
# `fits_fn` was None on every run of the pipeline's life and grouping fell
# back to a literal `max_chars = 18`. That literal was correct for the
# 58px caption default it was written against; at the 160px style this
# same step now resolves from the brand template, an 18-character group is
# 1663px wide in a 1080px frame.
#
# The style is resolved HERE now, from the brand template, exactly as it
# is for the props at the end of the step - there is no spine key to
# forget to write. The width it must fit inside comes from
# `library/tools/safe_area.py`, which is the same enumeration the render
# side positions against; if the safe area lived only on the render side
# the captions would be lifted clear of the platform's UI and still be
# clipped left and right.

# Layout constants read off the composition that draws the caption.
# Changing one here without changing it there measures a caption nobody
# renders.
WORD_GAP_EM = 0.24        # AnimatedWord.tsx: marginRight "0.24em"

# The caption BOX, not one line, is what a card has to fit inside.
# `SubtitleOverlay/index.tsx` draws the words in a `flexWrap: "wrap"` box
# bounded by `captionMaxWidth`, so a group wider than one line becomes two
# and is drawn in full - `CaptionFitter.widest_word_width` says as much,
# and `fit_scale` exists only for the single word that cannot be wrapped.
# Grouping against ONE line is therefore stricter than the render, and it
# is what halved the words on every card: the pre-measurement `max_chars =
# 18` grouping was about seventeen characters, and one line at the 160px
# style holds roughly eight.
#
# Three, not two, and the number is measured rather than chosen. At two,
# project 001 still lands 11 of 52 cards under half a second - a two-line
# card at 160px holds about 17 characters, and this speaker delivers 17
# characters in well under half a second several times. At three it is 2
# of 41, and both survivors are the block-final case no partition can
# reach. A three-line card is 576px of a 1920-row frame sitting on a
# 320px bottom inset, so it stays in the lower third and clear of the
# speaker. Raising it further buys nothing: four lines removes one of the
# two, and neither is a grouping fault.
# See docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line.
MAX_CAPTION_LINES = 3

EMPHASIS_SCALE = 1.14     # AnimatedWord.tsx: EMPHASIS_SCALE

# Mean advance of Montserrat's lowercase alphabet plus space, at weight
# 800, measured off the bundled file at 100px: 0.6048em. Used ONLY when
# no font file can be opened (an accepted system face has no path this
# side of the render - see library/tools/render_fonts.py). It is a
# per-character estimate and it is still measured in pixels, which the
# character count it replaces was not.
MEAN_ADVANCE_EM = 0.60

_font_cache = {}


def _load_font(font_path, font_size, font_weight=None):
    """Load a TrueType font, caching for reuse. Returns None if unavailable.

    A variable font must have its weight axis SET. The bundled Montserrat
    covers 100-900 and its default instance is 100, so measuring without
    this reports the width of Thin while the render draws ExtraBold.
    """
    key = (font_path, font_size, font_weight)
    if key not in _font_cache:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(font_path, font_size)
            if font_weight:
                try:
                    font.set_variation_by_axes([float(font_weight)])
                except (OSError, AttributeError, ValueError):
                    # A static face has no axes to set; its own weight is
                    # whatever the file is.
                    pass
            _font_cache[key] = font
        except (OSError, ImportError):
            _font_cache[key] = None
    return _font_cache[key]


class CaptionFitter:
    """How wide a caption really draws, and whether it fits.

    One instance per resolved caption style. ``usable_width`` is the ink
    budget: the safe area's centred usable width less the outline, which
    a text shadow paints outside the glyph box on both sides.
    """

    def __init__(self, font_path, font_size, font_weight, usable_width,
                 outline_width=0, emphasis_words=None):
        self.font_path = font_path
        self.font_size = int(font_size)
        self.font_weight = font_weight
        self.outline_width = int(outline_width or 0)
        self.usable_width = float(usable_width) - 2 * self.outline_width
        self.font = _load_font(font_path, self.font_size, font_weight) \
            if font_path else None
        self.measured = self.font is not None

    def word_width(self, word: str) -> float:
        """Rendered width of one word, in pixels."""
        if self.font is not None:
            return self.font.getlength(word)
        return len(word) * MEAN_ADVANCE_EM * self.font_size

    def text_width(self, text: str) -> float:
        """Rendered width of a whole caption laid out on ONE line.

        Words are inline-blocks with a 0.24em right margin, so the gaps
        are part of the measurement rather than a space glyph.
        """
        words = text.split()
        if not words:
            return 0.0
        gap = WORD_GAP_EM * self.font_size
        return (sum(self.word_width(w) for w in words)
                + gap * (len(words) - 1))

    def fits(self, text: str) -> bool:
        """Whether the caption fits the usable width on one line."""
        return self.text_width(text) <= self.usable_width

    def line_count(self, text: str) -> int:
        """How many lines the caption box wraps this text onto.

        Greedy, because that is what the flex container does: words are
        inline-blocks laid out left to right and the first one that does
        not fit starts a new line.
        """
        words = text.split()
        if not words:
            return 0
        lines, current = 1, []
        for word in words:
            trial = current + [word]
            if current and self.text_width(" ".join(trial)) > self.usable_width:
                lines += 1
                current = [word]
            else:
                current = trial
        return lines

    def fits_in_box(self, text: str, max_lines: int = MAX_CAPTION_LINES) -> bool:
        """Whether the caption fits the box within `max_lines` wrapped lines."""
        return self.line_count(text) <= max_lines

    def widest_word_width(self, text: str) -> float:
        """The widest single word, which is what cannot be wrapped away.

        The caption box wraps, so a group that is too wide becomes two
        lines. A single WORD wider than the box is an unbreakable inline
        block and is clipped at both frame edges instead - which is
        exactly what happened to "announcement" at 160px.
        """
        words = text.split()
        return max((self.word_width(w) for w in words), default=0.0)

    def fit_scale(self, text: str, emphasis_words=None) -> float:
        """How far this caption must shrink for its widest word to fit.

        1.0 when nothing needs shrinking. Below 1.0 the render draws THIS
        card smaller; it does not change the style's font size, which is
        an open captain decision this step has no business making.
        """
        widest = self.widest_word_width(text)
        if emphasis_words:
            emphasised = {_normalise_word(e) for e in emphasis_words}
            for word in text.split():
                if _normalise_word(word) in emphasised:
                    widest = max(widest, self.word_width(word) * EMPHASIS_SCALE)
        if widest <= 0 or widest <= self.usable_width:
            return 1.0
        return math.floor(self.usable_width / widest * 1000) / 1000.0


def _normalise_word(word: str) -> str:
    """Lower-cased, punctuation-stripped, matching AnimatedWord's rule."""
    return re.sub(r"^[^\w']+|[^\w']+$", "", word.lower())


def build_caption_fitter(style: dict, safe_area, project_folder="") -> CaptionFitter:
    """The fitter for one resolved caption style.

    `style` is what `library/tools/subtitle_style.resolve_subtitle_style`
    returns, so the face measured here is the face the render loads.
    """
    font_path = measurable_font_path(
        style.get("fontFamily", ""),
        style.get("fontFile"),
        project_folder or None,
    )
    return CaptionFitter(
        font_path=font_path,
        font_size=style.get("fontSize", 160),
        font_weight=style.get("fontWeight", 800),
        usable_width=safe_area.centered_usable_width,
        outline_width=style.get("outlineWidth", 0),
    )


def enforce_min_duration(groups, min_dur=MIN_DISPLAY_DURATION):
    """Extend short cards without moving later cards off their speech.

    The cards' start times are anchored to their first word. Extending an
    earlier card can therefore overlap the next one; the later overlap
    pass trims or merges that pair while keeping its spoken start. Shifting
    the next start to preserve a reading hold can put its first word
    entirely before the card, producing a reversed karaoke window.
    """
    if not groups:
        return groups

    for group in groups:
        duration = group["end"] - group["start"]
        if duration < min_dur:
            group["end"] = round(group["start"] + min_dur, 3)

    return groups


#: Overlaps at or below this are float dust, not two cards on one instant.
#: F6 (`reel_conformance_verifier.check_caption_overlaps`) flags anything
#: over 0.001s, so this pass resolves everything that gate would flag and
#: nothing it would not - the two can never disagree about whether two
#: cards overlap.
OVERLAP_EPSILON_SECONDS = 0.001


def _ends_sentence(word: str) -> bool:
    """Whether the word ends a sentence: terminal punctuation, last.

    Narrower than the `_ends_a_thought` tiebreak the optimizer reads
    (commas and colons split clauses a card routinely carries - Reel
    12's "things to say, which" - while a period ends what the reader
    may see). A card break is invisible timing; a period mid-card is a
    visible defect (Reel 17, 2026-09-20), so the rule fires on sentence
    terminals only and accepts the extra breaks it makes at
    abbreviations ("e.g.") - each is handled by the same duration
    remedy every short card already gets.
    """
    return bool(re.search(r"[.!?]$", word or ""))


def _ends_sentence_text(text: str) -> bool:
    """`_ends_sentence` on a card's last token, the only one that can
    end what the card says."""
    tokens = (text or "").rstrip().split()
    return bool(tokens) and _ends_sentence(tokens[-1])


def resolve_caption_overlaps(entries: list) -> dict:
    """One track, one card at a time - across the whole plan, not per block.

    Blocks can overlap in time: a reel spine keeps a real talk-over on
    both mics by design (`reel_spine._drop_bleed` drops only same-word
    bleed; different words at the same instant are two people talking
    over each other, and what to draw is this step's decision).  The
    per-block pass plans each block independently and clamps every card
    to its own block, so the two sides of a talk-over come out covering
    the same seconds - and two cards covering the same seconds cannot
    coexist on one V3 track.  Resolve trims the later one's head,
    shifting it off its planned start, which reads downstream as F14
    "planned and never placed" beside F6's overlap (reel 15, 2026-09-08:
    43 frames, segment 5 planned at 14.93s and never placed).

    The earlier card yields: it ends where the next card's first word
    starts.  That is this step's own model of a card's time on screen
    (`split_into_groups`: "a card's time on screen is the gap to the
    NEXT card's first word"), so the resolution is the grouping rule
    applied across the boundary the grouping cannot see.  The later
    card's timing never moves - its start is its first word, and pushing
    it would detach speech from any card (F5).

    A trim that would leave the earlier card flashing (under
    `MIN_CAPTION_FLASH_SECONDS`, the floor P6 fails a build on - and a
    trimmed card no longer ends with its block, so the exemption cannot
    save it) merges it into the next card instead, words preserved: the
    same merge the per-block pass already applies to a card clamped to
    nothing.  The later card keeps its timing exactly and only gains
    words, so one forward pass suffices - trimming shrinks an end and
    merging never moves a start, and neither can open a new overlap
    with a card already walked past.  The backward merge is the one
    exception with the same property argued above: the joined card is
    trimmed to the next card's start, removing exactly the overlap, so
    the walk never revisits a pair.

    If an earlier card's measured word now falls inside the later card,
    that word moves with its timing and text to the later card. A trim
    must not leave a word whose highlight runs after its card ends. A
    word sharing the same measured start as one already in the later
    card stays with its original sentence: adjacent transcript rows can
    stamp their boundary words onto the same onset with slightly
    different end times, and moving one would reverse their measured
    order.

    Returns what it did, so a run says so rather than doing it
    invisibly - `merged_backward` counts the sentence-final runts that
    joined the card before them.  A no-op on any plan whose cards
    already abut - which is every master and every reel without a
    talk-over.
    """
    ordered = sorted(entries,
                     key=lambda e: (e["timeline_start"], e["timeline_end"]))
    trimmed = 0
    merged = 0
    merged_backward = 0
    reassigned = 0
    doomed: set = set()
    index = 0
    while index < len(ordered) - 1:
        earlier, later = ordered[index], ordered[index + 1]
        index += 1
        if id(earlier) in doomed:
            continue
        overlap = earlier["timeline_end"] - later["timeline_start"]
        if overlap <= OVERLAP_EPSILON_SECONDS + 1e-9:
            continue
        earlier["timeline_end"] = round(later["timeline_start"], 3)
        trimmed_duration = (earlier["timeline_end"]
                            - earlier["timeline_start"])
        will_merge = trimmed_duration < MIN_CAPTION_FLASH_SECONDS
        previous = ordered[index - 2] if index >= 2 else None
        merge_backward = (
            will_merge
            and previous is not None
            and id(previous) not in doomed
            and _ends_sentence_text(earlier.get("text", ""))
            and previous.get("speaker") == earlier.get("speaker")
            and (later["timeline_start"] - previous["timeline_start"]
                 >= MIN_CAPTION_FLASH_SECONDS)
        )
        # A sentence-final runt may merge back into the preceding card.
        # Move any word already fully inside the next card first, or the
        # merged card would keep a highlight beyond its new end.
        if ((not will_merge or merge_backward) and earlier.get("speaker")
                and earlier.get("speaker") == later.get("speaker")):
            earlier_words = list(earlier.get("words") or [])
            moved = []
            kept_words = []
            later_words = list(later.get("words") or [])
            for word in earlier_words:
                start = float(word["start"])
                end = float(word["end"])
                shares_measured_start = any(
                    abs(start - float(candidate["start"])) <= 1e-6
                    for candidate in later_words
                )
                if (start >= later["timeline_start"]
                        and end <= later["timeline_end"]
                        and not shares_measured_start):
                    moved.append(word)
                else:
                    kept_words.append(word)
            if moved:
                earlier["words"] = kept_words
                later["words"] = sorted(
                    list(later.get("words") or []) + moved,
                    key=lambda word: (word["start"], word["end"]))

                def refresh_entry(entry):
                    words = sorted(entry.get("words") or [],
                                   key=lambda word: (word["start"],
                                                     word["end"]))
                    entry["words"] = words
                    entry["text"] = " ".join(
                        str(word["word"]) for word in words).strip()
                    entry["word_count"] = len(words)
                    entry["emphasis_words"] = identify_emphasis_words(
                        entry["text"])

                refresh_entry(earlier)
                refresh_entry(later)
                reassigned += len(moved)
                if not kept_words:
                    doomed.add(id(earlier))
        if (earlier["timeline_end"] - earlier["timeline_start"]
                >= MIN_CAPTION_FLASH_SECONDS):
            trimmed += 1
            continue
        # A card that ends a thought must not join the sentence after
        # it: forward-merging "the time." into "like what ..." puts
        # the period mid-card (Reel 17, 2026-09-20). It joins the card
        # before it instead, whose end is already at or before this
        # card's start - so trimming the joined card to the next
        # card's start removes exactly the overlap and opens no new
        # one behind it. Only where that trim leaves a readable card
        # and the voices match; otherwise the forward merge below.
        if merge_backward:
            previous["words"] = sorted(
                list(previous.get("words", []))
                + list(earlier.get("words", [])),
                key=lambda word: (word["start"], word["end"]))
            previous["text"] = (
                f"{previous['text']} {earlier['text']}".strip())
            previous["timeline_end"] = round(later["timeline_start"], 3)
            previous["word_count"] = len(previous["text"].split())
            previous["emphasis_words"] = identify_emphasis_words(
                previous["text"])
            doomed.add(id(earlier))
            merged += 1
            merged_backward += 1
            continue
        later["words"] = sorted(
            list(earlier.get("words", []))
            + list(later.get("words", [])),
            key=lambda word: (word["start"], word["end"]))
        later["text"] = f"{earlier['text']} {later['text']}".strip()
        later["word_count"] = len(later["text"].split())
        later["emphasis_words"] = identify_emphasis_words(later["text"])
        # A card covers the words it carries: the merged card opens
        # where its first word does - measured 2026-09-20 on Reel
        # 06, where "Not at all." joined the next card but the card
        # kept the later start and "Not" played with no caption
        # over it. Same speaker only: one voice mistimed across two
        # blocks is alignment slop, and its card is one voice. A
        # mixed card keeps the later start, and the gates refuse
        # the collision one track cannot serialize. Two cards from the
        # same speech block are one voice even when diarization supplied
        # no speaker label; preserve the first word's onset in that case.
        same_block = (
            earlier.get("spine_block_position") is not None
            and earlier.get("spine_block_position")
            == later.get("spine_block_position")
        )
        same_speaker = (
            earlier.get("speaker") is not None
            and earlier.get("speaker") == later.get("speaker")
        )
        if same_block or same_speaker:
            later["timeline_start"] = min(later["timeline_start"],
                                          earlier["timeline_start"])
        doomed.add(id(earlier))
        merged += 1
    if doomed:
        entries[:] = [entry for entry in entries if id(entry) not in doomed]
    return {"trimmed": trimmed, "merged": merged,
            "merged_backward": merged_backward,
            "reassigned": reassigned}


def _clamp_stretched_words(words: list, min_display: float) -> list:
    """A word spanning longer than any genuine word keeps its onset only.

    WhisperX stretches one word across the silence before the next
    speaker resumes - reel 10's 34.13s 'audits', reel 24's 3.03s
    'concise,'. The onset is the measured edge (the stretch runs
    forward over trailing silence) while the end is known-bad, and
    rendering the full span draws a card that hangs seconds past the
    speech it belongs to (F15). Dropping the word is not the answer
    either: it was spoken, and a caption must still show it.

    So the word keeps its onset and is shown for the legibility floor -
    exactly what `enforce_min_duration` gives a genuine short word at
    the same onset, so no duration enters the system grouping could not
    already produce. Clamping the WORD rather than the card also splits
    the group where the fake adjacency was: the silence a stretched
    tail bridged becomes a gap wider than `max_gap`, and no card spans
    it.

    The bound is `MAX_WORD_SECONDS`, imported rather than restated: the
    same rule F5's speech measure reads, so the span the coverage gate
    declines to count as speech is the span no card is drawn over. The
    clamp is SAID, naming each word, because a timing this step rewrote
    and did not report is a measurement it falsified quietly.
    """
    from library.tools.reel_conformance_verifier import MAX_WORD_SECONDS

    out = []
    clamped = []
    for word in words:
        span = float(word["end"]) - float(word["start"])
        if span > MAX_WORD_SECONDS:
            narrowed = dict(word)
            narrowed["end"] = round(float(word["start"]) + min_display, 3)
            clamped.append((word.get("word", ""), word["start"],
                            word["end"]))
            out.append(narrowed)
        else:
            out.append(word)
    if clamped:
        listed = ", ".join(
            f"{text!r} {start:.2f}-{end:.2f}s"
            for text, start, end in clamped[:5])
        if len(clamped) > 5:
            listed += f", +{len(clamped) - 5} more"
        print(
            f"NOTE: {len(clamped)} word(s) span longer than "
            f"{MAX_WORD_SECONDS:.1f}s - the aligner bridging silence, "
            f"not speech - so each keeps its onset and is shown for "
            f"the {min_display:.1f}s legibility floor: {listed}",
            file=sys.stderr,
        )
    return out


def split_into_groups(
    words_with_times: list,
    fits_fn=None,
    max_words: int = DEFAULT_MAX_WORDS_PER_CARD,
    max_gap: float = 1.0,
    display_until: float = None,
    min_display: float = MIN_DISPLAY_DURATION,
) -> list:
    """
    Split a list of {word, start, end} dicts into display groups.

    The partition and timing are chosen together. Each group is anchored
    to word boundaries, then scheduled after the previous group with at
    least `min_display` seconds and enough time to stay at or under the
    reading-speed ceiling. A card that cannot fit before `display_until`
    is clamped there; the completed plan then holds or merges any
    sub-floor card.

    Args:
        fits_fn: callable(text) -> bool, deciding whether the text fits
                 the caption box at the resolved caption style. It is
                 REQUIRED. It used to be optional with a `max_chars = 18`
                 fallback, and because nothing ever supplied it, that
                 literal is what grouped every caption the pipeline has
                 ever made - a character count with no relation to
                 pixels. `build_caption_fitter` makes one; there is no
                 longer a way to group blind.

    Returns groups with precise start/end times from word-level timestamps.
    """
    if fits_fn is None:
        raise ValueError(
            "split_into_groups needs a fits_fn to know how wide a caption "
            "draws. Build one with build_caption_fitter(style, safe_area); "
            "see library/tools/safe_area.py for the width it fits inside.")
    if not words_with_times:
        return []

    # A stretched tail fakes adjacency across silence; narrow the words
    # before anything measures a gap off them.
    words = _clamp_stretched_words(list(words_with_times), min_display)
    n = len(words)

    def _ends_a_thought(word: str) -> bool:
        return bool(re.search(r'[.!?,;:—–]$', word))

    def _feasible(i: int, j: int) -> bool:
        """Whether words[i:j] may be one card."""
        if j - i > max_words:
            return False
        if j - i == 1:
            # A single word is always its own card even when it is wider
            # than the box: it cannot be wrapped away, and `fit_scale`
            # draws that card smaller. This is the base case that makes a
            # partition always exist.
            return True
        # A card ends where a thought ends, or mid-thought - never
        # mid-new-thought. A word ending a sentence must end its card,
        # so no partition the optimizer can choose carries a period
        # mid-card (Reel 17, 2026-09-20). The runt this forces ("the
        # time." at 0.21s) is the duration remedy's business, not a
        # reason to straddle: `enforce_min_duration` extends it and the
        # overlap pass joins it backward. Only sentence terminals
        # constrain - commas split clauses a card routinely carries.
        for k in range(i, j - 1):
            if _ends_sentence(words[k]["word"]):
                return False
        return bool(fits_fn(" ".join(w["word"] for w in words[i:j])))

    # A state is a word boundary, the last card's end (a tie-break only),
    # the accumulated quality score and the chosen groups. Card starts are
    # fixed by their first words; overlap repair below handles reading holds
    # without delaying those spoken anchors.
    frontiers = [[] for _ in range(n + 1)]
    frontiers[0].append({
        "tail_end": float("-inf"),
        "score": (0, 0, 0, 0, 0, 0, 0, 0),
        "groups": [],
    })

    def _dominates(left, right) -> bool:
        """Whether left leaves no worse timing or partition for a suffix."""
        return (
            left["tail_end"] <= right["tail_end"] + 1e-9
            and all(a <= b for a, b in zip(left["score"], right["score"]))
        )

    def _add_state(index: int, candidate: dict) -> None:
        frontier = frontiers[index]
        if any(_dominates(old, candidate) for old in frontier):
            return
        frontier[:] = [old for old in frontier
                       if not _dominates(candidate, old)]
        frontier.append(candidate)

    for i in range(n):
        if not frontiers[i]:
            continue
        for state in frontiers[i]:
            for j in range(i + 1, min(n, i + max_words) + 1):
                if not _feasible(i, j):
                    continue

                card = words[i:j]
                text = " ".join(w["word"] for w in card)
                card_start = float(card[0]["start"])
                last_word_end = float(card[-1]["end"])
                desired_duration = max(
                    float(min_display),
                    len(text) / MAX_CHARACTERS_PER_SECOND,
                )
                desired_end = max(
                    last_word_end, card_start + desired_duration)
                card_end = (
                    min(desired_end, float(display_until))
                    if display_until is not None else desired_end
                )
                card_start_for_display = card_start
                if display_until is not None:
                    card_start_for_display = min(
                        card_start_for_display, float(display_until))
                card_start_for_display = round(card_start_for_display, 3)
                card_end = max(card_start_for_display, card_end)
                card_end = round(card_end, 3)
                duration = card_end - card_start_for_display
                cps = (len(text) / duration) if duration > 0 else float("inf")
                too_short = duration < MIN_CAPTION_FLASH_SECONDS
                too_fast = cps > MAX_CHARACTERS_PER_SECOND
                late = any(
                    card_start_for_display >= float(word["end"]) - 1e-9
                    for word in card
                )
                long_gaps = sum(
                    words[k]["start"] - words[k - 1]["end"] > max_gap
                    for k in range(i + 1, j)
                )
                under_target = duration < min_display
                # A card that would only meet the rate after its final word
                # has already been spoken is not a usable partition either.
                violates_readability = too_short or too_fast or late
                punctuated = _ends_a_thought(card[-1]["word"])

                # Long pauses are costly, but not forbidden: a merge across
                # one is justified when it removes a readability violation.
                # Keep the pause penalty behind the hard readability counts
                # so a viable card wins over a flash or an over-rate card.
                score = state["score"]
                next_score = (
                    score[0] + int(violates_readability),
                    score[1] + int(too_short),
                    score[2] + int(too_fast),
                    score[3] + int(late),
                    score[4] + long_gaps,
                    score[5] + int(under_target),
                    score[6] + 1,
                    score[7] - int(punctuated),
                )
                groups = state["groups"] + [{
                    "text": text,
                    "start": card_start_for_display,
                    "end": card_end,
                    "word_count": len(card),
                    "_words": [
                        {"word": w["word"], "start": w["start"],
                         "end": w["end"]}
                        for w in card
                    ],
                }]
                _add_state(j, {
                    "tail_end": card_end,
                    "score": next_score,
                    "groups": groups,
                })

    if not frontiers[n]:
        return []
    chosen = min(
        frontiers[n],
        key=lambda state: (*state["score"], state["tail_end"]),
    )
    return chosen["groups"]


def _merge_target(group: list, entry: dict, kept: list):
    """The card an unviewable card's words join: the next, else the previous.

    Forward first, because a card clamped to nothing is almost always at
    the head of a block, and its words are the START of the sentence the
    next card continues. Backward for a card that ends a thought:
    forward-joining "time." onto "like what ..." puts the period
    mid-card (Reel 17, 2026-09-20), while the previous card is the
    sentence it closes.
    """
    if kept and _ends_sentence_text(entry.get("text", "")):
        return kept[-1]
    index = group.index(entry)
    for later in group[index + 1:]:
        if later.get("timeline_end", 0) - later.get("timeline_start", 0) \
                >= MIN_VISIBLE_DURATION:
            return later
    return kept[-1] if kept else None


def _merge_entry(entry: dict, target: dict) -> None:
    """Fold `entry`'s words into `target`, in spoken order."""
    before = target["timeline_start"] >= entry["timeline_start"]
    if before:
        target["text"] = f"{entry['text']} {target['text']}".strip()
        target["words"] = entry.get("words", []) + target.get("words", [])
        target["timeline_start"] = min(
            target["timeline_start"], entry["timeline_start"])
    else:
        target["text"] = f"{target['text']} {entry['text']}".strip()
        target["words"] = target.get("words", []) + entry.get("words", [])
        target["timeline_end"] = max(
            target["timeline_end"], entry["timeline_end"])
    target["word_count"] = len(target["text"].split())
    target["emphasis_words"] = identify_emphasis_words(target["text"])


def _enforce_caption_duration_floor(entries: list, structure: list,
                                    fps: float) -> dict:
    """Hold or merge every visible card to the F7 duration floor.

    Per-block clamping can undo a minimum-duration hold on a block's final
    card. The card may use an uncaptioned gap after that block, but not
    another speech block or caption. If the gap is too short, the card may
    extend backward into caption-free silence, or join an adjacent
    same-speaker card when doing so keeps the sentence boundary intact. A
    short boundary fragment may also join the nearest same-speaker card in
    the immediately neighboring speech block, even when non-speech structure
    rows make their numeric positions skip.
    An impossible plan fails here instead of emitting a card the F7 gate
    will reject.
    """
    floor = MIN_CAPTION_FLASH_SECONDS
    fps = float(fps)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"Caption planning fps must be positive, got {fps!r}")
    floor_frames = math.ceil(floor * fps - 1e-9)
    ordered = sorted(
        entries, key=lambda entry: (entry["timeline_start"],
                                    entry["timeline_end"]))
    speech_blocks = [
        block for block in structure
        if block["block_type"] in ("hook", "speech")
    ]
    speech_index_by_position = {
        block["position"]: block_index
        for block_index, block in enumerate(speech_blocks)
    }
    plan_end = max(
        (float(block["timeline_end"]) for block in structure),
        default=float("inf"),
    )
    extended = 0
    extended_backward = 0
    merged = 0
    index = 0

    while index < len(ordered):
        entry = ordered[index]
        start = float(entry["timeline_start"])
        end = float(entry["timeline_end"])
        duration_frames = round((end - start) * fps)
        if (end - start >= floor - 1e-9
                and duration_frames >= floor_frames):
            index += 1
            continue

        # The renderer rounds each card's duration to frames. At rates
        # such as 25fps, a 0.5s card rounds below ceil(0.5 * fps), so
        # raise the target to the first millisecond that reaches the gate.
        target_duration = floor_frames / fps
        target_end = math.ceil(
            (start + target_duration) * 1000 - 1e-9) / 1000
        while round((target_end - start) * fps) < floor_frames:
            target_end = round(target_end + 0.001, 3)
        next_card_start = (
            float(ordered[index + 1]["timeline_start"])
            if index + 1 < len(ordered) else float("inf"))
        safe_end = min(plan_end, next_card_start)

        # Do not hold this card across another speech block, including a
        # talk-over whose first word has no separate card yet.
        position = entry["spine_block_position"]
        for block in speech_blocks:
            if block["position"] == position:
                continue
            block_start = float(block["timeline_start"])
            block_end = float(block["timeline_end"])
            if block_start <= start < block_end:
                safe_end = min(safe_end, start)
            elif start < block_start < target_end:
                safe_end = min(safe_end, block_start)

        if target_end <= safe_end + 1e-9:
            entry["timeline_end"] = target_end
            extended += 1
            index += 1
            continue

        previous = ordered[index - 1] if index else None
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        candidates = (
            [(previous, True), (following, False)]
            if _ends_sentence_text(entry.get("text", ""))
            else [(following, False), (previous, True)]
        )
        target = None
        target_is_previous = False
        for candidate, is_previous in candidates:
            if candidate is None:
                continue
            candidate_position = candidate["spine_block_position"]
            same_block = candidate_position == position
            same_speaker = candidate.get("speaker") == entry.get("speaker")
            entry_block_index = speech_index_by_position.get(position)
            candidate_block_index = speech_index_by_position.get(
                candidate_position)
            neighboring_speech_block = (
                entry_block_index is not None
                and candidate_block_index is not None
                and abs(candidate_block_index - entry_block_index) == 1
            )
            current_block = (
                speech_blocks[entry_block_index]
                if entry_block_index is not None else None
            )
            at_block_edge = (
                current_block is not None
                and (
                    (is_previous and abs(
                        start - float(current_block["timeline_start"])
                    ) <= 1 / fps + 1e-9)
                    or (not is_previous and abs(
                        end - float(current_block["timeline_end"])
                    ) <= 1 / fps + 1e-9)
                )
            )
            cross_block_fragment = (
                end - start < MIN_VISIBLE_DURATION
                and neighboring_speech_block
                and at_block_edge
                and entry.get("speaker") is not None
                and same_speaker
            )
            if not (same_block and same_speaker or cross_block_fragment):
                continue
            if cross_block_fragment and not same_block:
                candidate_edge = float(
                    candidate["timeline_end"] if is_previous
                    else candidate["timeline_start"])
                entry_edge = start if is_previous else end
                gap = max(0.0, entry_edge - candidate_edge)
                if gap > floor + 1e-9:
                    continue
            # Keep sentence terminals at the end of a card, as the
            # grouping and overlap passes already require.
            if is_previous and _ends_sentence_text(candidate.get("text", "")):
                continue
            if not is_previous and _ends_sentence_text(entry.get("text", "")):
                continue
            target = candidate
            target_is_previous = is_previous
            break

        if target is not None:
            _merge_entry(entry, target)
            entries.remove(entry)
            ordered.remove(entry)
            merged += 1
            if target_is_previous:
                index = max(0, index - 1)
            continue

        # A clipped word at the very end of a reel can have no safe room
        # after it, even though the preceding interval is genuine silence.
        # Existing same-speaker merging gets first refusal. The card may
        # then appear early enough to reach the same frame floor, but only
        # when the full held interval is free of speech and every other card.
        target_start = round(end - target_duration + 1e-9, 3)
        while round((end - target_start) * fps) < floor_frames:
            target_start = round(target_start - 0.001, 3)
        speech_free = all(
            float(block["timeline_end"]) <= target_start + 1e-9
            or float(block["timeline_start"]) >= start - 1e-9
            for block in speech_blocks
        )
        cards_free = all(
            other is entry
            or float(other["timeline_end"]) <= target_start + 1e-9
            or float(other["timeline_start"]) >= end - 1e-9
            for other in entries
        )
        if (target_start >= 0.0 and target_start < start - 1e-9
                and speech_free and cards_free):
            entry["timeline_start"] = target_start
            extended += 1
            extended_backward += 1
            index += 1
            continue

        raise ValueError(
            f"Caption {entry.get('id', '?')} {entry.get('text', '')!r} "
            f"would remain under the {floor:.3f}s readability floor "
            f"({end - start:.3f}s, {duration_frames} frames; needs "
            f"{floor_frames} at {fps:.3f}fps): the next safe hold ends at "
            f"{safe_end:.3f}s, and no adjacent same-speaker card in this "
            f"or a neighboring speech block can absorb it without moving "
            f"a sentence boundary"
        )

    return {"extended": extended, "extended_backward": extended_backward,
            "merged": merged}


def _readability_issues(entries: list) -> list:
    """Name final cards below the readable-duration or reading-speed floor."""
    issues = []
    for entry in entries:
        duration = max(
            0.0, float(entry["timeline_end"]) - float(entry["timeline_start"]))
        text = entry["text"]
        cps = len(text) / duration if duration > 0 else None
        reasons = []
        if duration < MIN_CAPTION_FLASH_SECONDS:
            reasons.append("duration_below_0.5_seconds")
        if cps is None or cps > MAX_CHARACTERS_PER_SECOND:
            reasons.append("reading_speed_over_25_characters_per_second")
        words = entry["words"]
        if words and float(entry["timeline_start"]) > max(
                float(word["end"]) for word in words) + 1e-9:
            reasons.append("caption_starts_after_its_last_word")
        if reasons:
            issues.append({
                "card_id": entry["id"],
                "spine_block_position": entry["spine_block_position"],
                "text": text,
                "duration_seconds": round(duration, 3),
                "characters_per_second": round(cps, 2) if cps is not None
                else None,
                "reasons": reasons,
            })
    return issues


def _same_caption_voice(left: dict, right: dict) -> bool:
    """Whether two neighboring cards can share one caption without mixing voices."""
    same_block = (
        left.get("spine_block_position") is not None
        and left.get("spine_block_position")
        == right.get("spine_block_position")
    )
    same_speaker = (
        left.get("speaker") is not None
        and left.get("speaker") == right.get("speaker")
    )
    return same_block or same_speaker


def _enforce_caption_reading_speed(entries: list, structure: list) -> dict:
    """Try to repair fast cards without dropping words or refusing a plan.

    ``split_into_groups`` gives a card enough time for its text, but the
    single-track overlap pass can then end it at the next card's spoken
    onset.  A final pass accounts for that resulting duration.  It uses a
    safe uncaptioned gap first, then re-splits at word boundaries, then joins
    a neighboring card from the same speech block or speaker.  A passage that
    still cannot fit remains in the plan for subtitle QA to report; valid
    speech must not make planning fail.
    """
    if not entries:
        return {"extended": 0, "merged": 0, "split": 0}

    extended = 0
    merged = 0
    split = 0
    unresolved = set()
    speech_blocks = [
        block for block in structure
        if block["block_type"] in ("hook", "speech")
    ]
    plan_end = max(
        (float(block["timeline_end"]) for block in structure),
        default=float("inf"),
    )

    while True:
        ordered = sorted(
            entries, key=lambda entry: (entry["timeline_start"],
                                        entry["timeline_end"]))
        fast = next((entry for entry in ordered
                     if id(entry) not in unresolved
                     and len(entry["text"])
                     / max(1e-9, float(entry["timeline_end"])
                           - float(entry["timeline_start"]))
                     > MAX_CHARACTERS_PER_SECOND), None)
        if fast is None:
            break

        start = float(fast["timeline_start"])
        end = float(fast["timeline_end"])
        target_duration = len(fast["text"]) / MAX_CHARACTERS_PER_SECOND
        target_end = math.ceil((start + target_duration) * 1000 - 1e-9) / 1000
        safe_end = plan_end
        index = ordered.index(fast)
        if index + 1 < len(ordered):
            safe_end = min(
                safe_end, float(ordered[index + 1]["timeline_start"]))

        position = fast["spine_block_position"]
        for block in speech_blocks:
            if block["position"] == position:
                continue
            block_start = float(block["timeline_start"])
            block_end = float(block["timeline_end"])
            if block_start <= start < block_end:
                safe_end = min(safe_end, start)
            elif start < block_start < target_end:
                safe_end = min(safe_end, block_start)

        if (target_end > end + 1e-9
                and target_end <= safe_end + 1e-9):
            fast["timeline_end"] = target_end
            extended += 1
            unresolved.clear()
            continue

        # The overlap pass can collapse a previously readable group onto a
        # later word onset.  Repartition its measured words when each new
        # card can fit wholly before the next card and still clear the same
        # visible-duration floor enforced earlier in the plan.
        words = list(fast.get("words") or [])
        if len(words) > 1:
            # Every between-word boundary is eligible. Sentence endings are
            # especially safe places to split, because the group keeps its
            # terminal punctuation at the end.
            boundaries = range(1, len(words))

            # Dynamic programming keeps the split as coarse as possible,
            # preferring the fewest cards and then the most balanced rates.
            split_options = {len(words): (0, 0.0, [])}
            for left in range(len(words) - 1, -1, -1):
                best = None
                for right in range(left + 1, len(words) + 1):
                    if right < len(words) and right not in boundaries:
                        continue
                    group_words = words[left:right]
                    group_text = " ".join(
                        str(word["word"]) for word in group_words).strip()
                    group_start = (
                        start if left == 0
                        else float(group_words[0]["start"]))
                    available_end = (
                        float(words[right]["start"])
                        if right < len(words) else safe_end
                    )
                    group_end = min(available_end, safe_end)
                    duration = group_end - group_start
                    cps = (len(group_text) / duration
                           if duration > 0 else float("inf"))
                    tail = split_options.get(right)
                    if (tail is None
                            or cps > MAX_CHARACTERS_PER_SECOND
                            or duration < MIN_CAPTION_FLASH_SECONDS):
                        continue
                    cost = (
                        tail[0] + 1,
                        tail[1] + cps,
                        [(left, right)] + tail[2],
                    )
                    if best is None or cost[:2] < best[:2]:
                        best = cost
                if best is not None:
                    split_options[left] = best

            choice = split_options.get(0)
            if choice is not None and choice[0] > 1:
                block_entries = [
                    entry for entry in entries
                    if entry.get("spine_block_position")
                    == fast.get("spine_block_position")
                ]
                next_index = max(
                    (int(entry.get("card_index", -1))
                     for entry in block_entries), default=-1) + 1
                identifier_prefix = str(fast.get("id", "subtitle_"))
                identifier_prefix = identifier_prefix.rsplit("_", 1)[0]
                replacement = []
                for ordinal, (left, right) in enumerate(choice[2]):
                    part_words = words[left:right]
                    part_text = " ".join(
                        str(word["word"]) for word in part_words).strip()
                    part = dict(fast)
                    part.update({
                        "id": (fast["id"] if ordinal == 0 else
                               f"{identifier_prefix}_{next_index:03d}"),
                        "card_index": (
                            fast.get("card_index", next_index)
                            if ordinal == 0 else next_index),
                        "timeline_start": (
                            start if left == 0
                            else float(part_words[0]["start"])),
                        "timeline_end": min(
                            float(words[right]["start"])
                            if right < len(words) else safe_end,
                            safe_end,
                        ),
                        "text": part_text,
                        "word_count": len(part_words),
                        "words": [dict(word) for word in part_words],
                        "emphasis_words": identify_emphasis_words(part_text),
                    })
                    replacement.append(part)
                    if ordinal:
                        next_index += 1
                entry_index = entries.index(fast)
                entries[entry_index:entry_index + 1] = replacement
                split += len(replacement) - 1
                unresolved.clear()
                continue

        terminal = _ends_sentence_text(fast.get("text", ""))
        directions = (-1,) if terminal else (1, -1)
        candidates = []
        for priority, direction in enumerate(directions):
            neighbor_index = index + direction
            if not 0 <= neighbor_index < len(ordered):
                continue
            neighbor = ordered[neighbor_index]
            if not _same_caption_voice(fast, neighbor):
                continue
            if direction < 0 and _ends_sentence_text(neighbor.get("text", "")):
                continue
            if direction > 0 and terminal:
                continue

            combined_text = (
                f"{neighbor['text']} {fast['text']}" if direction < 0
                else f"{fast['text']} {neighbor['text']}"
            ).strip()
            combined_start = min(
                float(fast["timeline_start"]),
                float(neighbor["timeline_start"]))
            combined_end = max(
                float(fast["timeline_end"]),
                float(neighbor["timeline_end"]))
            combined_duration = combined_end - combined_start
            combined_cps = (
                len(combined_text) / combined_duration
                if combined_duration > 0 else float("inf"))
            candidates.append((combined_cps, priority, neighbor))

        if not candidates:
            unresolved.add(id(fast))
            continue

        passing = [candidate for candidate in candidates
                   if candidate[0] <= MAX_CHARACTERS_PER_SECOND]
        if passing:
            _, _, neighbor = min(
                passing, key=lambda candidate: (candidate[1], candidate[0]))
        else:
            _, _, neighbor = min(
                candidates, key=lambda candidate: (candidate[0],
                                                   candidate[1]))
        _merge_entry(fast, neighbor)
        entries.remove(fast)
        merged += 1
        unresolved.clear()

    return {"extended": extended, "merged": merged, "split": split}


def identify_emphasis_words(text: str) -> list:
    """
    Identify keywords that should receive visual emphasis (scale bump).
    """
    skip_words = {
        "the", "a", "an", "is", "was", "are", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "nor", "not", "so", "yet", "if", "than", "that",
        "this", "it", "its", "i", "me", "my", "we", "our", "you",
        "your", "he", "she", "they", "them", "their", "just", "like",
        "um", "uh", "really", "very", "also", "then",
    }
    emphasis = []
    for word in text.split():
        clean = re.sub(r'[^a-z]', '', word.lower())
        if clean and len(clean) >= 4 and clean not in skip_words:
            emphasis.append(clean)
    return emphasis[:2]


def _require_word_timestamps(block: dict) -> list:
    """The block's word timings, or a loud failure.

    Every speech and hook block carries populated `word_timestamps` under
    the spine contract (library/tools/spine_contract.py). Estimating the
    timings proportionally when they are absent is what let unaligned
    blocks reach the timeline looking correct.
    """
    words = block["word_timestamps"]
    if not words:
        raise ValueError(
            f"Spine block {block['position']!r} ({block['block_type']}) has "
            f"empty word_timestamps - the spine contract requires word "
            f"timings on every speech block, so subtitles cannot be timed"
        )
    return words


def _words_in_source_window(
    block: dict, words: list, src_in: float, src_out: float,
) -> list:
    """Measured words overlapping the block's half-open source window."""
    in_range = [
        w for w in words
        # Preserve the existing head tolerance: transcript and clip in
        # points can differ by a few frames, and a word just before the
        # declared source start may still be part of the spoken lead-in.
        if w["source_end"] > src_in - 0.05
        and w["source_start"] < src_out
    ]
    if not in_range:
        raise ValueError(
            f"Spine block {block['position']!r} carries "
            f"{len(words)} word timings but none fall inside its own "
            f"source window {src_in:.3f}-{src_out:.3f}s - the block's "
            f"timings belong to a different passage"
        )
    return in_range


def _remove_placement_dust(words: list[dict]) -> list[dict]:
    """Do not draw a word span F25 cannot count as played speech.

    F25 uses `MIN_SPAN_OVERLAP_SECONDS` when it intersects measured words
    with placed audio. A word no wider than that floor is placement dust,
    even when its entire transcript interval sits inside the placed span.
    Apply the same floor after phrase corrections, which may combine
    measured subtokens into one readable word.
    """
    from library.tools.subtitle_coverage import MIN_SPAN_OVERLAP_SECONDS

    return [word for word in words
            if float(word["end"]) - float(word["start"])
            > MIN_SPAN_OVERLAP_SECONDS]


def _reading_corrections(project_folder: str):
    """Recorded spellings the caption reading restores, or None.

    `transcript_corrections.spelling_corrections`: the store the planner
    enforces on the words after the reading runs. Never refuses - an
    unreadable store reads as absent, and the caption plan must survive
    it the way `apply_to_words` does.
    """
    if not project_folder:
        return None
    try:
        from library.tools import transcript_corrections as _tc
        corrections = _tc.spelling_corrections(project_folder)
    except Exception:  # noqa: BLE001 - caption plan must survive
        return None
    return corrections or None


def _apply_transcript_corrections(timeline_words: list,
                                    speaker: str | None,
                                    project_folder: str) -> list:
    """Recorded corrections onto timeline words. Never refuses.

    `transcript_corrections.apply_to_words` respells (phrase-aware)
    and suppresses (display-only) with the project's store; without a
    project folder, or with nothing recorded, the words pass through
    untouched. What moved is said on stderr with the learning ids, so
    a caption that changed names its reason on the run that planned
    it. A word list emptied entirely (a block of nothing but "um")
    plans no cards - the audio still plays them, and silence captions
    nothing.
    """
    if not project_folder:
        return timeline_words
    from library.tools import transcript_corrections as _tc
    words, report = _tc.apply_to_words(
        timeline_words, speaker, project_folder)
    if report["replacements"] or report["suppressed"]:
        print(f"  transcript corrections on caption words"
              f"{f' ({speaker})' if speaker else ''}: "
              f"{report['replacements']} replacement(s), "
              f"{report['suppressed']} suppressed "
              f"{[a['id'] for a in report['applied']]}",
              file=sys.stderr)
    return words


def generate_subtitles(audio_spine: dict, caption_case: str = "lowercase",
                       brand_effect: dict = None,
                       brand_style: dict = None,
                       project_folder: str = "",
                       scope=None,
                       reel_name: str = "",
                       fps: float = DEFAULT_CAPTION_FPS) -> dict:
    """
    Generate subtitle entries from the spine's own word-level timestamps.

    Speech and hook blocks must carry populated word_timestamps; a block
    that does not fails the step rather than being timed by guesswork.

    `scope` narrows WHICH blocks are planned, and nothing else.  At
    REGION scope only the blocks the region touches are planned, and the
    result is a partial plan meant for `subtitle_splice.splice_plan` -
    never for writing over a whole one.  The grouping, the timing and the
    fitting are identical either way: every block is planned from its own
    `timeline_start`/`timeline_end` and its own words, so a block plans
    the same whether its neighbours are present or not.  Measured on
    project 001 - a region-scoped plan of block 10 reproduces that
    block's five entries byte for byte, ids included.

    That is only true because caption ids are BLOCK-LOCAL (increment 1).
    While they came from a run-global counter, planning a region
    renumbered every card after it, which is why the operation refused
    REGION scope until now.

    `reel_name` prefers that reel's declared caption row
    (`external/declarations/reel_caption_row.json`) over the project value when the
    style is resolved below - the reel path passes the timeline name it
    builds; every other caller leaves it empty and reads today's
    answer exactly.

    Args:
        audio_spine: The audio spine with structure blocks.
        caption_case: "lowercase" or "as_written". Controls
            whether subtitle text is lowercased or left as the source
            transcript produced it.
        project_folder: Resolves the delivery format, and through it the
            safe area the captions are grouped and positioned against.
    """
    structure = audio_spine.get("structure", [])

    # The ONLY thing scope changes: which blocks are planned.  The block
    # list is narrowed here and every line below is untouched, so a
    # region-scoped plan cannot drift from a whole-project one.
    if scope is not None and scope.is_region:
        from library.tools.spine_contract import blocks_overlapping
        span = scope.region_span
        structure = blocks_overlapping(structure, span.start, span.end)
        if not structure:
            raise ValueError(
                f"region {span} touches no spine block, so there is nothing "
                f"to plan. The spine runs to "
                f"{audio_spine.get('total_estimated_duration_seconds', '?')}s."
            )

    subtitle_entries = []

    # ── Stretched-speech warning, before a caption is planned ──
    #
    # Reel 12 (field test, 2026-09-19): the transcription dropped "pull
    # from there" and stuttered "probably" into two, MFA stretched
    # "hallucinate" across the gap to 1.55s, and this step captioned the
    # defective list faithfully - every downstream check comparing
    # played against captioned then agreed with itself. The two duration
    # outliers in that row WERE the two complaints, so the row's own
    # words are scored against its own local rate here, before grouping
    # draws a card over them
    # (`library/tools/transcript_duration_anomaly.py`). Advisory ONLY:
    # a flagged row is a prompt to listen, never a refusal - this pass
    # cannot tell stretched silence from slow speech, and words the
    # transcription never produced have no duration to score. Said on
    # the run, like every other repair in this step.
    try:
        from library.tools.transcript_duration_anomaly import (
            flag_duration_anomalies,
        )
        _anomaly_rows = [
            {"position": block.get("position"),
             "speaker": block.get("speaker"),
             "words": block.get("word_timestamps") or []}
            for block in structure
            if block.get("block_type") in ("hook", "speech")
        ]
        _anomalies = flag_duration_anomalies(_anomaly_rows)
        _flagged = _anomalies["warnings"]
        if _flagged:
            _listed = ", ".join(
                f"block {w['position']} {w['word']!r} "
                f"{w['duration_seconds']:.2f}s "
                f"(local median {w['local_median_seconds']:.2f}s, "
                f"z {w['modified_z']:.1f})"
                + (f" [{w['speaker']}]" if w.get("speaker") else "")
                for w in _flagged[:5])
            if len(_flagged) > 5:
                _listed += f", +{len(_flagged) - 5} more"
            print(
                f"NOTE: {len(_flagged)} word(s) run far longer than "
                f"their own block's speaking rate - possible "
                f"aligner stretch across dropped speech, listen before "
                f"trusting these cards: {_listed}",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001 - advisory, never refuses
        print(f"WARNING: duration-anomaly check could not run ({exc}); "
              f"continuing without it.", file=sys.stderr)

    # ── A caption id is BLOCK-LOCAL, and that is what makes a region
    #    splice provable ──
    #
    # This used to be one counter across the whole timeline, so an id was
    # the card's ordinal position in the finished video.  Measured on
    # project 001: forcing one block to produce five more cards renumbered
    # 13 entries in blocks that had not changed.  Nothing reads the id
    # today, so that was harmless - right up until a region-scoped re-plan
    # has to PROVE it changed only the region it was given, which it does
    # by comparing the entries either side of it.  Under a global counter
    # that comparison reports churn that is not there, and a proof that
    # cries wolf is the gate AGENTS.md 10.4 warns about from the other
    # direction.
    #
    # Numbering within the block instead makes an id stable under every
    # change outside its own block, and it is the same component
    # `subtitle_segment_id` already names a rendered overlay by - so
    # `sub_10_003` reads against `sub_<timeline>_<speaker>_10_<span>_<hash>`
    # without a lookup.
    sub_counters = {}

    def _next_id(block_position) -> str:
        token = slug(block_position, "noblock")
        sub_counters[token] = sub_counters.get(token, 0) + 1
        return f"sub_{token}_{sub_counters[token]:03d}"

    # ── A card's ordinal within its block, and what it names ──
    #
    # Step 4.05 renders one segment per CARD, not one per block
    # (`generate_remotion_props.generate_subtitle_props_per_block`):
    # a timeline caption clip holds the cards the craft put together,
    # and the craft's finest unit is one card. The segmenter groups by
    # `(spine_block_position, card_index)`, so the index is emitted
    # here, beside the id, from a counter incremented at exactly the
    # same two sites - an id ordinal and a card index that can drift
    # apart are two names for one card, which is how a card ends up in
    # the wrong segment.
    card_counters = {}

    def _next_card_index(block_position) -> int:
        token = slug(block_position, "noblock")
        index = card_counters.get(token, 0)
        card_counters[token] = index + 1
        return index

    # ── The caption look, and the width it has to fit inside ──
    # Resolved once, at the top, and used for BOTH the grouping below and
    # the props at the bottom. It used to be resolved only at the bottom,
    # while the grouper looked for a `subtitle_style` key on the spine
    # that no producer wrote - so the measurement was dead and every
    # caption was grouped by a character count.
    style = resolve_subtitle_style(brand_effect, brand_style, project_folder,
                                   reel_name=reel_name or None)
    safe_area = resolve_safe_area(project_folder or None)
    fitter = build_caption_fitter(style, safe_area, project_folder)
    # Recorded spellings the reading restores ("google" reads "Google"):
    # the same store enforced on the words after the reading runs, so a
    # card the corrections respelled still reads as the caption path
    # renders it (2026-09-19: every reel carrying "Google" refused its
    # build on correct output). None where the store is absent, which
    # reads exactly as before. See library/tools/caption_reading.py.
    reading_corrections = _reading_corrections(project_folder)

    # A project may caption each speaker differently - the styling IS the
    # diarization signal when two people are talking. A per-speaker style
    # can change the font SIZE, so it needs its own fitter: grouping
    # captions at one size and rendering them at another is how a card
    # ends up wider than the safe area. Resolved per speaker on first
    # sight and reused. A project that declares no speaker styles gets
    # exactly this shared pair, so nothing changes for it.
    # See library/tools/subtitle_style.SPEAKER_STYLE_KEYS.
    styles_by_speaker = {}
    fitters_by_speaker = {}

    def _for_speaker(speaker):
        if not speaker:
            return style, fitter
        if speaker not in styles_by_speaker:
            speaker_style = resolve_subtitle_style(
                brand_effect, brand_style, project_folder, speaker=speaker,
                reel_name=reel_name or None)
            styles_by_speaker[speaker] = speaker_style
            fitters_by_speaker[speaker] = (
                fitter if speaker_style == style
                else build_caption_fitter(speaker_style, safe_area,
                                          project_folder))
        return styles_by_speaker[speaker], fitters_by_speaker[speaker]

    if not fitter.measured:
        print(
            f"WARNING: captions in {style.get('fontFamily')!r} cannot be "
            f"measured - no font file this side of the render (see "
            f"library/tools/render_fonts.py). Falling back to a "
            f"{MEAN_ADVANCE_EM}em mean-advance estimate.",
            file=sys.stderr,
        )
    # The box, not one line: the overlay wraps (MAX_CAPTION_LINES).
    fits_fn = fitter.fits_in_box

    # Rung 7 (finding 31): the grouping's word ceiling is a plan value
    # now - the brand template's `effect.caption_words_per_card` when
    # the series states one, else the default above. Resolved once for
    # the whole plan, so every block groups at the same ceiling.
    max_words_per_card = resolve_max_words_per_card(
        brand_effect, audio_spine=audio_spine)

    for block in structure:
        block_type = block["block_type"]
        if block_type not in ("hook", "speech"):
            continue
        # Absent on a spine the pipeline built itself; present on one
        # measured off a real timeline, where each speaker has their own
        # track (library/tools/timeline_ingest.py). None means the shared
        # style, never a guessed one.
        block_speaker = block.get("speaker")
        block_style, block_fitter = _for_speaker(block_speaker)
        fits_fn = block_fitter.fits_in_box

        content = block["content"]
        block_start = block["timeline_start"]
        block_end = block["timeline_end"]

        if block_type == "hook":
            text = content["text"]
            if not text:
                continue

            word_ts = _require_word_timestamps(block)

            # The spine's own source range is the V1 clip's range.
            v1_src_in = block["source_start"]
            v1_src_out = block["source_end"]
            offset = block_start - v1_src_in

            # Filter words to the V1 clip's source window
            in_range = _words_in_source_window(block, word_ts,
                                               v1_src_in, v1_src_out)

            timeline_words = [
                {
                    "word": w["word"],
                    "start": round(w["source_start"] + offset, 3),
                    "end": round(w["source_end"] + offset, 3),
                }
                for w in in_range
            ]
            # Clip to the block's timeline window
            timeline_words = [
                w for w in timeline_words
                if w["end"] > block_start - 0.05
                and w["start"] < block_end
            ]
            # Case, then reading, BEFORE grouping: the fit is measured
            # on what is drawn, so "SEO 2.0" groups at its own width
            # rather than at "seo two point oh"'s. See
            # library/tools/caption_reading.py. Corrections run AFTER
            # the transforms, not before: six active CAPTAIN-said
            # spellings (lc-0001, lc-0084..lc-0088) order their casing
            # "in every caption", and conforming them to house style
            # first would obey the brand over the captain's stated
            # preference (AGENTS.md 10.5). The reading restores them -
            # it takes the same store - so the contract below still
            # holds on what is drawn.
            timeline_words = [
                {**w, "word": apply_caption_case(w["word"], caption_case)}
                for w in timeline_words
            ]
            timeline_words = apply_caption_reading(
                timeline_words, corrections=reading_corrections)
            # Recorded transcript corrections, enforced on the timed
            # words the transcript text pass cannot reach: the spine's
            # words are a second ASR product (step 1.04 over source
            # audio), so a "C RMs" fixed in the transcript still
            # captions "c rms" without this. Spelling merges phrases
            # across entries without inventing timings; suppression
            # drops pronounced-but-unread tokens (audio and spine
            # untouched - entries are partitioned, never retimed).
            # Runs BEFORE grouping so the fit is measured on what is
            # drawn, for the same reason case and reading run first.
            timeline_words = _apply_transcript_corrections(
                timeline_words, block_speaker, project_folder)
            timeline_words = _remove_placement_dust(timeline_words)
            groups = split_into_groups(
                timeline_words, fits_fn=fits_fn,
                max_words=max_words_per_card,
                display_until=block_end)

            for g in groups:
                entry_text = g["text"].strip()
                subtitle_entries.append({
                    "id": _next_id(block["position"]),
                    "card_index": _next_card_index(block["position"]),
                    "timeline_start": max(g["start"], block_start),
                    "timeline_end": min(g["end"], block_end),
                    "text": entry_text,
                    "emphasis_words": identify_emphasis_words(entry_text),
                    "spine_block_position": block["position"],
                    "speaker": block_speaker,
                    "word_count": g["word_count"],
                    "words": [
                        {
                            "word": w["word"].strip(),
                            "start": w["start"],
                            "end": w["end"],
                        }
                        for w in g.get("_words", [])
                    ],
                })

        elif block_type == "speech":
            # Under the spine contract a speech block is exactly one
            # passage, carrying its own source range and word timings.
            segments = [{
                "text": content["text"],
                "start_time": block["source_start"],
                "end_time": block["source_end"],
                "word_timestamps": _require_word_timestamps(block),
            }]

            current_tl_pos = block_start

            for seg in segments:
                seg_text = seg["text"]
                if not seg_text:
                    continue

                word_ts = seg["word_timestamps"]
                seg_source_start = seg["start_time"]
                seg_source_end = seg["end_time"]
                source_dur = seg_source_end - seg_source_start

                # Each segment plays at 1x speed, so its timeline duration is its source duration.
                # Clamp to the block's overall timeline_end just in case.
                seg_tl_start = current_tl_pos
                seg_tl_end = min(current_tl_pos + source_dur, block_end)
                seg_tl_dur = seg_tl_end - seg_tl_start

                # mesh_spine syncs a block's duration to its speech, so a
                # source span far longer than the timeline span means the
                # spine is inconsistent and word timings cannot be mapped.
                # Say so instead of quietly spreading the text evenly.
                if source_dur > 0 and seg_tl_dur > 0 and source_dur > seg_tl_dur * 1.5:
                    raise ValueError(
                        f"Spine block {block['position']!r} spans "
                        f"{source_dur:.3f}s of source but only "
                        f"{seg_tl_dur:.3f}s of timeline - word timings "
                        f"cannot be mapped onto a block that was jump-cut "
                        f"after the spine was built"
                    )

                # The block's own source range is the V1 clip's range.
                v1_src_in = seg_source_start
                v1_src_out = seg_source_end

                # Filter words to the V1 clip's source window
                in_range_words = _words_in_source_window(
                    block, word_ts, v1_src_in, v1_src_out)

                # Offset: V1 source_in → segment's timeline_start
                offset = seg_tl_start - v1_src_in

                # 2.02 may restore a partial sentence's lead-in at the
                # source edge. Keep every restored token in this timed
                # stream: grouping and card starts must follow the same
                # measured words the spine now plays, rather than timing
                # the passage text as a separate, untimed string.
                timeline_words = [
                    {
                        "word": w["word"],
                        "start": round(w["source_start"] + offset, 3),
                        "end": round(w["source_end"] + offset, 3),
                    }
                    for w in in_range_words
                ]
                # Clip to segment's timeline window
                timeline_words = [
                    w for w in timeline_words
                    if w["end"] > seg_tl_start - 0.05
                    and w["start"] < seg_tl_end
                ]
                # Case, then reading, BEFORE grouping - the same reason
                # as the hook branch above: the fit is measured on what
                # is drawn (library/tools/caption_reading.py). Corrections
                # run after the transforms there too, so captain-said
                # casing survives into the captions it was ordered into.
                timeline_words = [
                    {**w, "word": apply_caption_case(w["word"], caption_case)}
                    for w in timeline_words
                ]
                timeline_words = apply_caption_reading(
                    timeline_words, corrections=reading_corrections)
                timeline_words = _apply_transcript_corrections(
                    timeline_words, block_speaker, project_folder)
                timeline_words = _remove_placement_dust(timeline_words)
                groups = split_into_groups(
                timeline_words, fits_fn=fits_fn,
                max_words=max_words_per_card,
                display_until=block_end)

                for g in groups:
                    entry_text = g["text"].strip()
                    subtitle_entries.append({
                        "id": _next_id(block["position"]),
                        "card_index": _next_card_index(block["position"]),
                        "timeline_start": max(g["start"], seg_tl_start),
                        # The duration planner schedules against the block's
                        # real display boundary. Clamping back to the speech
                        # segment here would discard that time and recreate a
                        # fast or flashing card before the block pass can see
                        # it.
                        "timeline_end": min(g["end"], block_end),
                        "text": entry_text,
                        "emphasis_words": identify_emphasis_words(entry_text),
                        "spine_block_position": block["position"],
                        "speaker": block_speaker,
                        "word_count": g["word_count"],
                        "words": [
                            {
                                "word": w["word"].strip(),
                                "start": w["start"],
                                "end": w["end"],
                            }
                            for w in g.get("_words", [])
                        ],
                    })
                
                # Advance timeline position for the next segment
                current_tl_pos += seg_tl_dur

    # ── Enforce the normal hold PER BLOCK ──
    # Each block's subtitles are extended independently so one block
    # never pushes another block's caption. The clamp can leave a short
    # final card; the plan-wide floor pass below handles it after overlap
    # repair, using only a safe gap or a sentence-safe same-speaker merge.
    if subtitle_entries:
        # Group entries by spine block position
        block_groups = {}
        for entry in subtitle_entries:
            pos = entry.get("spine_block_position")
            block_groups.setdefault(pos, []).append(entry)

        # Build block-range lookup from spine
        block_range_lookup = {
            blk["position"]: (blk["timeline_start"], blk["timeline_end"])
            for blk in structure
        }

        for pos, group in block_groups.items():
            block_start, block_end = block_range_lookup.get(
                pos, (float("-inf"), float("inf"))
            )

            # `enforce_min_duration` extends a short card's end but keeps
            # every card anchored to its first word. The following overlap
            # pass trims or merges the extended spans without losing words.
            # It reads `start`/`end`, so entries are projected onto those
            # keys and written back.
            spans = [
                {"start": entry["timeline_start"],
                 "end": entry["timeline_end"]}
                for entry in group
            ]
            enforce_min_duration(spans)
            for entry, span in zip(group, spans):
                entry["timeline_start"] = span["start"]
                entry["timeline_end"] = span["end"]

            # Clamp EVERY subtitle in the block to the block's range.
            # Extension can carry more than one trailing entry past the
            # boundary, and clamping only the last would leave the rest
            # bleeding into the next block and overlapping its neighbour.
            for entry in group:
                entry["timeline_start"] = round(
                    max(entry["timeline_start"], block_start), 3)
                entry["timeline_end"] = round(
                    min(entry["timeline_end"], block_end), 3)

            # A card clamped to nothing used to be DELETED, and its words
            # with it. That was survivable while grouping was blind and
            # cards were long; measured grouping makes short cards, and a
            # short card at a block boundary is exactly the one that
            # clamps to zero - so the deletion started eating whole words
            # ("and so" off the head of a block). Merge it into its
            # neighbour instead: the same duration, the same reading, and
            # every spoken word still on screen.
            kept = []
            for entry in group:
                visible = (entry["timeline_end"] - entry["timeline_start"]
                           >= MIN_VISIBLE_DURATION)
                if visible:
                    kept.append(entry)
                    continue
                target = _merge_target(group, entry, kept)
                if target is None:
                    print(
                        f"NOTE: retained sub-{MIN_VISIBLE_DURATION:.3f}s "
                        f"subtitle {entry.get('id', '?')} "
                        f"({entry.get('text', '')!r}) from block {pos} "
                        f"for the plan-wide duration pass",
                        file=sys.stderr,
                    )
                    continue
                _merge_entry(entry, target)
                subtitle_entries.remove(entry)
            block_groups[pos] = kept

    # ── One track, one card at a time, ACROSS blocks ──
    # The pass above plans each block independently; blocks that overlap
    # in time (a reel's talk-over) leave cards covering the same seconds,
    # which no single track can place.  The earlier card yields to the
    # next card's first word, or joins it where the trim would flash -
    # `resolve_caption_overlaps` is that rule.  Said on the run, like
    # every other repair in this step.
    overlap_fix = resolve_caption_overlaps(subtitle_entries)
    if (overlap_fix["trimmed"] or overlap_fix["merged"]
            or overlap_fix.get("reassigned")):
        print(
            f"NOTE: resolved {overlap_fix['trimmed']} overlapping caption "
            f"card(s) by ending them at the next card's first word and "
            f"merged {overlap_fix['merged']} - "
            f"{overlap_fix.get('merged_backward', 0)} of them backward "
            f"into the sentence they close - two cards cannot cover "
            f"the same seconds on one track; moved "
            f"{overlap_fix.get('reassigned', 0)} timed word(s) onto "
            f"the card that covers them",
            file=sys.stderr,
        )

    # A block's final caption may have been clamped shorter than the
    # readability floor. Use only a free gap after that block, or merge
    # with a compatible neighbour. A sub-floor card is refused rather
    # than passed to the renderer and F7.
    duration_fix = _enforce_caption_duration_floor(
        subtitle_entries, structure, fps)
    if duration_fix["extended"] or duration_fix["merged"]:
        print(
            f"NOTE: held {duration_fix['extended']} short caption card(s) "
            f"through safe silence ({duration_fix['extended_backward']} "
            f"backward) and merged {duration_fix['merged']} into "
            f"compatible same-speaker card(s) to meet the "
            f"{MIN_CAPTION_FLASH_SECONDS:.1f}s readability floor",
            file=sys.stderr,
        )

    # --- Verification ---

    # No overlapping subtitles
    sorted_subs = sorted(subtitle_entries, key=lambda s: s["timeline_start"])
    for i in range(len(sorted_subs) - 1):
        if sorted_subs[i]["timeline_end"] > sorted_subs[i + 1]["timeline_start"] + 0.01:
            print(
                f"WARNING: Subtitle overlap: {sorted_subs[i].get('entry_id', sorted_subs[i].get('id', '?'))} "
                f"ends at {sorted_subs[i]['timeline_end']} but "
                f"{sorted_subs[i + 1].get('entry_id', sorted_subs[i + 1].get('id', '?'))} starts at "
                f"{sorted_subs[i + 1]['timeline_start']}",
                file=sys.stderr,
            )

    # Every card reads as the caption path renders it: the card is a
    # fixed point of the declared reading. Acronyms ("SEO"), digits
    # ("2.0") and recorded spellings ("Google") are uppercase by
    # captain's ruling, so a bare lowercase assert would fail correct
    # output - and lowercasing first would even destroy the evidence:
    # a card corrected from heard "lucy" reads "Lucie", whose
    # lowercase ("lucie") was never the heard form, so only the card
    # as drawn round-trips. The reading takes the same store the
    # planner enforced above, or the check grades against an older
    # answer than the cards carry (see
    # library/tools/caption_reading.py).
    if caption_case != "as_written":
        # Every drawn word went through the declared reading, checked
        # the way the planner applies it: to each block's whole word
        # stream BEFORE grouping, with words as the unit. The reading
        # is context-sensitive - a rank marker keeps "top three"
        # words while a bare "three" reads "3" - so checking a card's
        # text, or one word, in isolation fails correct output
        # wherever a rank phrase sits on or splits across cards
        # (measured 2026-09-20 on Reel 08: "the top three" planned,
        # then refused on isolated "three"). Each card is therefore
        # re-read with its own block-predecessor's last word as left
        # context, and only the card's own words are compared.
        # Merged-away cards are already out of this list, so the
        # predecessor is exact. One residual: a cross-block overlap
        # merge glues two blocks' words onto one card, and the seam is
        # adjacency the reading never saw - if that ever refuses, the
        # card it names is the whole diagnosis.
        prev_last_word: dict = {}
        for sub in subtitle_entries:
            words = sub.get("words", [])
            context = prev_last_word.get(sub.get("spine_block_position"), "")
            sequence = (
                ([{"word": context, "start": 0.0, "end": 0.0}] if context
                 else [])
                + [{"word": w["word"], "start": w.get("start", 0.0),
                    "end": w.get("end", 0.0)} for w in words]
            )
            reread = apply_caption_reading(
                sequence, corrections=reading_corrections)
            card_reread = reread[1:] if context else reread
            assert [w["word"] for w in card_reread] == \
                [w["word"] for w in words], \
                f"Subtitle {sub.get('entry_id', sub.get('id', '?'))} " \
                f"is not the declared reading of itself: {sub['text']}"
            drawn = [w["word"] for w in words]
            if drawn:
                prev_last_word[sub.get("spine_block_position")] = drawn[-1]

    # Filter emphasis words not present in subtitle text
    for sub in subtitle_entries:
        sub["emphasis_words"] = [
            ew for ew in sub["emphasis_words"]
            if ew in sub["text"]
        ]

    # ── The captain's edits: caption fixes, text only ──
    # A caption_fix names spoken words and what the caption should read
    # instead, and it applies on EVERY rebuild here - after grouping, so
    # the fix lands on finished cards and survives regeneration. Timings
    # are never touched (that half is `apply_caption_fixes`' contract:
    # text changes must never move picture or sound), and an anchor that
    # matches no card reports STALE loudly rather than vanishing
    # (library/tools/captain_edits.py). Drops are NOT applied here: the
    # speech is cut upstream in mesh_spine's post-bridge, so by the time
    # this plans cards the passage is already gone from the spine.
    captain_applied, captain_stale = [], []
    try:
        from library.tools import captain_edits as _edits
        _all = _edits.load_edits(project_folder) if project_folder else []
        if any(e.get("kind") == "caption_fix" for e in _all):
            subtitle_entries, captain_applied, captain_stale = \
                _edits.apply_caption_fixes(subtitle_entries, _all)
            for record in captain_applied:
                print(
                    f"NOTE: captain edit: {record['cards_touched']} "
                    f"caption card(s) now read "
                    f"{record['replacement']!r} where the speech says "
                    f"{record['anchor_phrase']!r} - {record['reason']}",
                    file=sys.stderr,
                )
            if captain_stale:
                _edits.report_stale(captain_stale)
    except Exception as exc:  # noqa: BLE001 - an edit pass must never
        # refuse captions: unapplied edits are reported, not fatal.
        print(f"WARNING: captain caption edits could not apply ({exc}); "
              f"continuing without them.", file=sys.stderr)

    speed_fix = _enforce_caption_reading_speed(
        subtitle_entries, structure)
    if (speed_fix["extended"] or speed_fix["merged"]
            or speed_fix["split"]):
        print(
            f"NOTE: extended {speed_fix['extended']} caption card(s) through "
            f"safe silence, split {speed_fix['split']} fast card(s), and "
            f"merged {speed_fix['merged']} neighboring card(s) to meet the "
            f"{MAX_CHARACTERS_PER_SECOND:.0f} characters/second limit",
            file=sys.stderr,
        )

    readability_issues = _readability_issues(subtitle_entries)
    # An unsplittable or speech-overlapped card remains in the plan with its
    # measured issue. Subtitle QA owns the verdict; planning must not reject
    # valid speech just because no synced one-track repair exists.

    # A speed repair may merge cards, so measure and report the final plan,
    # not the groups that existed before the repair.
    shrunk = []
    for sub in subtitle_entries:
        scale = fitter.fit_scale(sub["text"], sub.get("emphasis_words"))
        sub["fit_scale"] = scale
        if scale < 1.0:
            shrunk.append((sub["id"], sub["text"], scale))
    if shrunk:
        print(
            f"NOTE: {len(shrunk)} of {len(subtitle_entries)} caption cards "
            f"carry a word wider than the {fitter.usable_width:.0f}px usable "
            f"width at {style['fontSize']}px and are drawn smaller: "
            + ", ".join(f"{i} {t!r} x{s}" for i, t, s in shrunk[:5])
            + ("..." if len(shrunk) > 5 else ""),
            file=sys.stderr,
        )

    for sub in subtitle_entries:
        if sub["word_count"] > 8:
            print(
                f"WARNING: Subtitle {sub.get('entry_id', sub.get('id', '?'))} has "
                f"{sub['word_count']} words",
                file=sys.stderr,
            )

    if readability_issues:
        print(
            f"WARNING: {len(readability_issues)} subtitle card(s) still "
            f"miss the readable-duration or 25 characters/second limit: "
            + "; ".join(
                f"{issue['card_id']} {issue['text']!r} "
                f"({issue['duration_seconds']:.3f}s, "
                f"{issue['characters_per_second']} cps; "
                f"{', '.join(issue['reasons'])})"
                for issue in readability_issues
            ),
            file=sys.stderr,
        )

    # C4 fix: Wrap output under subtitle_plan key to match manifest contract.
    # Manifest declares output as 'subtitle_plan', and DAG edge
    # plan_subtitles -> render_subtitles maps subtitle_plan -> subtitle_plan.
    # `style` is the one resolved at the top of this function - the same
    # object the grouping above measured against. Emitted here so step
    # 4.05 has something to serialise into the Remotion props: the props
    # generator used to supply a hardcoded Montserrat/58px default because
    # this key never existed, and every template's typography, palette and
    # subtitle_style reached nothing. See library/tools/subtitle_style.py.
    return {
        "subtitle_plan": {
            "subtitle_entries": subtitle_entries,
            "total_subtitles": len(subtitle_entries),
            "style": style,
            # Rung 7: the word ceiling every card above was grouped
            # at - the plan value, not the literal it replaced.
            "max_words": max_words_per_card,
            # Card-level constraints after all timing, overlap and captain
            # edit passes. Empty means the plan has no readability misses.
            "readability_issues": readability_issues,
            # Only when the project declared any: an empty mapping in
            # every plan would read as a decision nobody made.
            **({"styles_by_speaker": styles_by_speaker}
               if styles_by_speaker else {}),
            # What the captain's deltas did on this build, or [].
            "captain_edits_applied": captain_applied,
            "captain_edits_stale": captain_stale,
        }
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data.get("audio_spine")

    if not audio_spine:
        print(json.dumps({
            "error": "Missing required input: audio_spine",
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    # Read caption case from brand template's effect slot.
    #
    # Every shipped template declares one. The fallback when the slot is
    # absent entirely is LOAD-BEARING and left in place deliberately:
    # it is `DEFAULT_CAPTION_CASE`, the one constant
    # `library/schemas/brand_template.py` owns and `EffectSlots`
    # defaults to, so a project running with no brand template at all
    # still renders captions. It is listed for the captain as a
    # creative default that survives this pass - see the PR that
    # removed the rest.
    brand_effect = input_data.get("brand_effect", {})
    brand_style = input_data.get("brand_style", {})
    caption_case = brand_effect.get("caption_case", DEFAULT_CAPTION_CASE)

    try:
        result = generate_subtitles(
            audio_spine, caption_case=caption_case,
            brand_effect=brand_effect, brand_style=brand_style,
            project_folder=input_data.get("project_folder", ""),
            fps=input_data.get("project_fps") or DEFAULT_CAPTION_FPS)
    except (ValueError, AssertionError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()


# ── Region-scoped re-plan, and putting it back ──────────────────────

def splice_region_plan(audio_spine: dict, stored_plan: dict, scope,
                       caption_case: str = DEFAULT_CAPTION_CASE,
                       brand_effect: dict = None,
                       brand_style: dict = None,
                       project_folder: str = "") -> dict:
    """Re-plan the blocks a region touches and splice them into `stored_plan`.

    The captain's worked example ends here: *"splice the refreshed
    subtitles back in."*  Planning the region is `generate_subtitles` with
    a scope; this is the half that has to leave the rest of the timeline
    alone, and prove it did.

    Returns `{"subtitle_plan": ..., "splice": <report>}`.  The report
    carries `outside_unchanged`, MEASURED rather than asserted - a splice
    that reports its own success without checking is the vacuous gate
    this repository keeps removing.

    Refuses, rather than doing something surprising:

    - a region touching no block, so a typo redoes nothing quietly;
    - a fresh plan carrying a block the region did not ask for;
    - a splice that would change any touched block's DURATION, because
      `mesh_spine`'s post-bridge lays blocks end to end and a longer
      block moves every one after it.  That refusal lives in
      `subtitle_splice` and is the boundary between a correction and a
      re-plan.
    """
    from library.tools.spine_contract import blocks_overlapping
    from library.tools.subtitle_splice import (
        assert_durations_preserved,
        splice_plan,
        splice_report,
    )

    span = scope.region_span
    structure = audio_spine.get("structure", [])
    touched = blocks_overlapping(structure, span.start, span.end)
    if not touched:
        raise ValueError(
            f"region {span} touches no spine block, so there is nothing to "
            f"splice.")
    positions = [b["position"] for b in touched]

    # The durations either side of the splice are the SAME blocks here -
    # a re-plan of captions cannot change a block's length - so this
    # reads as a tautology today and is not one tomorrow: the same
    # function is what `transcript.splice` upstream has to satisfy, and
    # keeping the check on this path means a caller who re-indexed first
    # cannot skip it by coming in through captions.
    assert_durations_preserved(structure, touched)

    fresh = generate_subtitles(
        audio_spine, caption_case=caption_case, brand_effect=brand_effect,
        brand_style=brand_style, project_folder=project_folder,
        scope=scope)["subtitle_plan"]

    stored_entries = stored_plan.get("subtitle_entries", [])
    merged = splice_plan(stored_entries, fresh["subtitle_entries"], positions)
    report = splice_report(stored_entries, merged, positions)

    plan = dict(stored_plan)
    plan["subtitle_entries"] = merged
    plan["total_subtitles"] = len(merged)
    # The style comes from the fresh pass: it is resolved from the brand
    # template every time and is not a per-region value, so taking the
    # stored one would let a template change reach the region's cards and
    # not the record of what style they were drawn at.
    plan["style"] = fresh["style"]
    return {"subtitle_plan": plan, "splice": report}
