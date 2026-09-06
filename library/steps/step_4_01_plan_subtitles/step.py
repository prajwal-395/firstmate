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

Input:  {
    "audio_spine": { structure: [...] },
    "speech_sequence": { body_sequence (with word_timestamps) }
}
Output: { "subtitle_entries": [...], "total_subtitles": int }
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from library.tools.render_fonts import measurable_font_path
from library.tools.safe_area import resolve_safe_area
from library.tools.subtitle_segment_id import slug
from library.tools.subtitle_style import resolve_subtitle_style

# ── Minimum display duration (seconds) ──
# Matches Palmier Pro's AppTheme.Caption.minDisplayDuration.
# Subtitles shorter than this are extended; subsequent groups
# are shifted forward to prevent overlap.
MIN_DISPLAY_DURATION = 0.7

# The hard floor, below which a card flashes rather than reads.  Same
# number as `manifest_validator.MIN_CAPTION_DISPLAY_SECONDS` and
# `render_qa`'s `subtitle_too_short`, and it is here because the GROUPING
# is what sets it: a card is on screen until the next card's first word,
# so nothing downstream of the split can lengthen one.
MIN_CAPTION_FLASH_SECONDS = 0.5

# Below this a subtitle flashes rather than reads; clamping a block's
# entries to its bounds can leave a sliver, and a sliver is worth dropping.
MIN_VISIBLE_DURATION = 0.08


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
    """Extend short display groups and shift later ones to prevent overlap.

    Ported from Palmier Pro's CaptionBuilder.swift:
    - If a group is shorter than min_dur, extend its end time.
    - If extending causes overlap with the next group, shift the next
      group's start forward.
    - Repeat for the cascade.

    This prevents subtitle flicker where a 2-word group appears for
    only 0.2s — unreadable at normal playback speed.
    """
    if not groups:
        return groups

    for i, g in enumerate(groups):
        dur = g["end"] - g["start"]
        if dur < min_dur:
            g["end"] = round(g["start"] + min_dur, 3)

        # Prevent overlap with next group
        if i + 1 < len(groups):
            next_g = groups[i + 1]
            if next_g["start"] < g["end"]:
                next_g["start"] = round(g["end"], 3)
                # Ensure next group still has positive duration
                if next_g["end"] <= next_g["start"]:
                    next_g["end"] = round(next_g["start"] + min_dur, 3)

    return groups


def split_into_groups(
    words_with_times: list,
    fits_fn=None,
    max_words: int = 6,
    max_gap: float = 1.0,
    display_until: float = None,
    min_display: float = MIN_DISPLAY_DURATION,
) -> list:
    """
    Split a list of {word, start, end} dicts into display groups.

    The split is BALANCED, not greedy. A greedy fill packs each card to
    the width limit and leaves whatever is left over as the next card, so
    a run of words that wants four even cards comes out as three full ones
    and a runt - and a runt card is on screen only until the next card's
    first word, which is what makes it flash. On project 001 that is where
    76 of 96 cards under half a second came from. This chooses, among the
    partitions that all fit the caption box, the one with the fewest cards
    below `min_display`, breaking ties towards the longest shortest card
    and then towards ending cards on punctuation.

    A card's time on screen is the gap to the NEXT card's first word, so
    the partition is what sets it; `display_until` supplies the same
    number for the last card (the end of the block or segment it is drawn
    over). Without it the last card is measured to its own last word,
    which is very nearly zero.

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

    words = list(words_with_times)
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
        for k in range(i + 1, j):
            if words[k]["start"] - words[k - 1]["end"] > max_gap:
                return False
        return bool(fits_fn(" ".join(w["word"] for w in words[i:j])))

    def _room_until(j: int) -> float:
        """When the card ending before word j must be gone."""
        if j < n:
            return words[j]["start"]
        if display_until is not None:
            return float(display_until)
        return words[n - 1]["end"]

    def _time_on_screen(i: int, j: int) -> float:
        """How long words[i:j] is actually displayed for.

        This mirrors the per-block pass at the end of the step exactly: a
        card is on screen for as long as its own words last, and a card
        shorter than `min_display` is extended towards that floor but no
        further than the next card's first word. Optimising anything else
        optimises a number nobody renders.
        """
        start = words[i]["start"]
        span = words[j - 1]["end"] - start
        if span >= min_display:
            return span
        return max(span, min(min_display, _room_until(j) - start))

    # best[i] = (flashing, under_floor, -shortest, -punctuated_ends, j) for
    # the optimal partition of words[i:]. `flashing` counts cards below the
    # HARD floor - the one `manifest_validator` fails a build on - and
    # `under_floor` the softer one this step declares, so the partition is
    # chosen against the number that actually rejects a render first.
    # Solved from the end back.
    best = [None] * (n + 1)
    best[n] = (0, 0, 0.0, 0, None)
    for i in range(n - 1, -1, -1):
        chosen = None
        for j in range(i + 1, min(n, i + max_words) + 1):
            if not _feasible(i, j):
                continue
            tail = best[j]
            if tail is None:
                continue
            duration = _time_on_screen(i, j)
            flashing = tail[0] + (1 if duration < MIN_CAPTION_FLASH_SECONDS
                                  else 0)
            under = tail[1] + (1 if duration < min_display else 0)
            shortest = min(-tail[2], duration) if j < n else duration
            punctuated = tail[3] + (1 if _ends_a_thought(words[j - 1]["word"])
                                    else 0)
            candidate = (flashing, under, -shortest, -punctuated, j)
            if chosen is None or candidate[:4] < chosen[:4]:
                chosen = candidate
        best[i] = chosen

    groups = []
    i = 0
    while i < n and best[i] is not None:
        j = best[i][4]
        card = words[i:j]
        groups.append({
            "text": " ".join(w["word"] for w in card),
            "start": card[0]["start"],
            "end": card[-1]["end"],
            "word_count": len(card),
            "_words": [
                {"word": w["word"], "start": w["start"], "end": w["end"]}
                for w in card
            ],
        })
        i = j

    return groups


def _merge_target(group: list, entry: dict, kept: list):
    """The card an unviewable card's words join: the next, else the previous.

    Forward first, because a card clamped to nothing is almost always at
    the head of a block, and its words are the START of the sentence the
    next card continues.
    """
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
    """Words falling inside a block's source window, or a loud failure."""
    in_range = [
        w for w in words
        if w["source_end"] > src_in - 0.05
        and w["source_start"] < src_out + 0.05
    ]
    if not in_range:
        raise ValueError(
            f"Spine block {block['position']!r} carries "
            f"{len(words)} word timings but none fall inside its own "
            f"source window {src_in:.3f}-{src_out:.3f}s - the block's "
            f"timings belong to a different passage"
        )
    return in_range


def generate_subtitles(audio_spine: dict, caption_case: str = "lowercase",
                       brand_effect: dict = None,
                       brand_style: dict = None,
                       project_folder: str = "",
                       scope=None) -> dict:
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

    # ── The caption look, and the width it has to fit inside ──
    # Resolved once, at the top, and used for BOTH the grouping below and
    # the props at the bottom. It used to be resolved only at the bottom,
    # while the grouper looked for a `subtitle_style` key on the spine
    # that no producer wrote - so the measurement was dead and every
    # caption was grouped by a character count.
    style = resolve_subtitle_style(brand_effect, brand_style, project_folder)
    safe_area = resolve_safe_area(project_folder or None)
    fitter = build_caption_fitter(style, safe_area, project_folder)

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
                brand_effect, brand_style, project_folder, speaker=speaker)
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
                and w["start"] < block_end + 0.05
            ]
            groups = split_into_groups(
                timeline_words, fits_fn=fits_fn,
                display_until=block_end)

            for g in groups:
                entry_text = apply_caption_case(g["text"], caption_case).strip()
                subtitle_entries.append({
                    "id": _next_id(block["position"]),
                    "timeline_start": max(g["start"], block_start),
                    "timeline_end": min(g["end"], block_end),
                    "text": entry_text,
                    "emphasis_words": identify_emphasis_words(entry_text),
                    "spine_block_position": block["position"],
                    "speaker": block_speaker,
                    "word_count": g["word_count"],
                    "words": [
                        {
                            "word": apply_caption_case(w["word"], caption_case).strip(),
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
                    and w["start"] < seg_tl_end + 0.05
                ]
                groups = split_into_groups(
                timeline_words, fits_fn=fits_fn,
                display_until=block_end)

                for g in groups:
                    entry_text = apply_caption_case(g["text"], caption_case).strip()
                    subtitle_entries.append({
                        "id": _next_id(block["position"]),
                        "timeline_start": max(g["start"], seg_tl_start),
                        "timeline_end": min(g["end"], seg_tl_end),
                        "text": entry_text,
                        "emphasis_words": identify_emphasis_words(entry_text),
                        "spine_block_position": block["position"],
                        "speaker": block_speaker,
                        "word_count": g["word_count"],
                        "words": [
                            {
                                "word": apply_caption_case(w["word"], caption_case).strip(),
                                "start": w["start"],
                                "end": w["end"],
                            }
                            for w in g.get("_words", [])
                        ],
                    })
                
                # Advance timeline position for the next segment
                current_tl_pos += seg_tl_dur

    # ── Enforce minimum display duration PER BLOCK ──
    # Each block's subtitles are enforced independently so that
    # extending a short subtitle in one block never cascades into the
    # next block.  The last subtitle in each block is clamped to the
    # block's end time — it may be shorter than MIN_DISPLAY_DURATION,
    # but that's preferable to bleeding across the block boundary.
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

            for i in range(len(group)):
                curr = group[i]
                nxt = group[i + 1] if i + 1 < len(group) else None
                max_end = nxt["timeline_start"] if nxt else block_end
                dur = curr["timeline_end"] - curr["timeline_start"]
                if dur < MIN_DISPLAY_DURATION:
                    desired_end = curr["timeline_start"] + MIN_DISPLAY_DURATION
                    curr["timeline_end"] = round(min(desired_end, max_end), 3)

            # Clamp EVERY subtitle in the block to the block's range.
            # The min-duration cascade above can push more than one
            # trailing entry past the boundary, and clamping only the last
            # left the rest bleeding into the next block - which then made
            # that block's rendered overlay overlap its neighbour.
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
                        f"WARNING: dropped subtitle {entry.get('id', '?')} "
                        f"({entry.get('text', '')!r}) - clamping it to block "
                        f"{pos} left no visible duration, and it is the "
                        f"block's only card",
                        file=sys.stderr,
                    )
                    subtitle_entries.remove(entry)
                    continue
                _merge_entry(entry, target)
                subtitle_entries.remove(entry)
            block_groups[pos] = kept

    # ── Fit the cards a group split cannot fix ──
    # Grouping stops a caption being too WIDE, because a group can be
    # split. It cannot stop a single WORD being too wide: an inline block
    # does not wrap, so the frame clips it at both edges. "announcement"
    # at Montserrat 800/160px is 1303px in an 840px usable width, and
    # that is the card the audit photographed running off both sides of
    # the frame.
    #
    # The card shrinks; the STYLE does not. What size captions should be
    # is an open captain decision and this step has no business making
    # it - the job here is to make whatever size is chosen fit the frame.
    # A scale below 1.0 is worth reading as a signal that the chosen size
    # is too large for the footage's vocabulary, which is why it is
    # reported rather than applied quietly.
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

    # All text matches the configured caption case
    if caption_case != "as_written":
        for sub in subtitle_entries:
            assert sub["text"] == sub["text"].lower(), \
                f"Subtitle {sub.get('entry_id', sub.get('id', '?'))} is not lowercase: {sub['text']}"

    # Word count warnings
    for sub in subtitle_entries:
        if sub["word_count"] > 8:
            print(
                f"WARNING: Subtitle {sub.get('entry_id', sub.get('id', '?'))} has "
                f"{sub['word_count']} words",
                file=sys.stderr,
            )

    # Filter emphasis words not present in subtitle text
    for sub in subtitle_entries:
        sub["emphasis_words"] = [
            ew for ew in sub["emphasis_words"]
            if ew in sub["text"]
        ]

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
            # Only when the project declared any: an empty mapping in
            # every plan would read as a decision nobody made.
            **({"styles_by_speaker": styles_by_speaker}
               if styles_by_speaker else {}),
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
    # Every shipped template declares one. The `"lowercase"` written here
    # when the slot is absent entirely is LOAD-BEARING and left in place
    # deliberately: `EffectSlots.caption_case` defaults to it too, so
    # removing it here would only move the same decision one file over,
    # and a project running with no brand template at all must still
    # render captions. It is listed for the captain as a creative default
    # that survives this pass - see the PR that removed the rest.
    brand_effect = input_data.get("brand_effect", {})
    brand_style = input_data.get("brand_style", {})
    caption_case = brand_effect.get("caption_case", "lowercase")

    try:
        result = generate_subtitles(
            audio_spine, caption_case=caption_case,
            brand_effect=brand_effect, brand_style=brand_style,
            project_folder=input_data.get("project_folder", ""))
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
                       caption_case: str = "lowercase",
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
        assert_durations_preserved, splice_plan, splice_report,
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
