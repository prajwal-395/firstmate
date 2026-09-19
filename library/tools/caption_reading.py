"""What a caption READS, after the case rule has run.

`effect.caption_case: lowercase` lowercases every caption by design - the
all-lowercase look is correct, not a defect. The captain's ruling of
2026-09-18 makes acronyms the exception to that style: "SEO and GEO are
capitalized ... across all timelines", "2.0" is digits rather than "two
point oh", and - answering the open scope question the same day - "AI"
capitalizes with them and spelled-out numbers become digits.

ONE named place. The acronym set and the numeral rule live HERE, as data,
and the caption path (`step_4_01_plan_subtitles`) reads them - never a
conditional scattered through the planner, the props generator or the
renderer. Answering a future scope question ("chatgpt joins the set", "a
new numeral shape") is an edit to the declarations below and nothing
else: adding "AI" was exactly one tuple entry.

What converts, and what deliberately does not
---------------------------------------------
Acronyms: a whole-word, case-insensitive match against
`CAPTION_ACRONYMS` is restored to its canonical case - "seo" reads
"SEO", "ai" reads "AI", and the possessive keeps its clitic ("ai's"
reads "AI's"). "chatgpt" is the same SHAPE and is NOT a member: the
captain did not name it, so it stays lowercase and is flagged for him
in the open-questions record, not decided here.

Numerals: a spelled-out number used as a QUANTITY, RATING, VERSION,
MEASURE or YEAR reads as digits - "twenty percent" reads "20 percent",
"nine times more" reads "9 times more", "three point five stars" reads
"3.5 stars", "two point oh" reads "2.0", "twenty twenty" (a year) reads
"2020". Left as words: a bare "one" (near-always a pronoun in this
footage - "one of the best ways", "another one", "see which one is"),
rank labels ("number one", "the top three" - undecided, flagged), and
ordinals ("first", "second" - never cardinals, never touched).

Timings are never invented. `apply_caption_reading` maps a word list to
a word list: a merged numeral ("two point oh" -> "2.0") spans from the
first word's start to the last word's end, so every emitted token is
timed and the `vep-transcript-row-with-no-words-reaches-the-caption-
planner` shape cannot come out of here.
"""

from __future__ import annotations

import re

#: The acronyms the captain named, in canonical case. Complete: a word
#: that reads uppercase on a caption is a member of this tuple. Adding
#: "AI" took appending one entry; "chatgpt" stays out until he names it.
#: 2026-09-19: "CEO", "CMO" and "CRM" join them - the captain's Reel 01,
#: Reel 07 and Reel 03 notes, each "needs to be properly reflected in
#: the subtitles". Same sanctioned path: a scope answer is an edit to
#: this declaration and nothing else.
CAPTION_ACRONYMS: tuple[str, ...] = ("SEO", "GEO", "AI", "CEO", "CMO",
                                     "CRM")

#: Rank labels are NOT decided. A cardinal directly after one of these
#: stays a word ("number one", "the top three") and is flagged for the
#: captain rather than converted. Complete for the footage measured
#: 2026-09-18; a new rank phrasing is a declaration edit, not a branch.
RANK_MARKERS: tuple[str, ...] = ("number", "top")

#: Words that parse as the integer beside them. Data, read by
#: `parse_number` - which is the only interpreter, so a new number word
#: lands here and reaches every pattern (bare, percent, times, star,
#: decimal, year) at once.
ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}

#: Single words that read as one decimal digit. "oh" is how the
#: transcriber writes 0 in a version ("two point oh"); both spellings
#: map, because they name the same digit.
DIGIT_WORDS = {
    "oh": "0",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _core(token: str) -> str:
    """The matchable centre of a caption token, lowercased.

    Surrounding punctuation ("percent.", '"two') is affix, not content -
    it is reattached after the conversion rather than matched through.
    """
    match = _WORD_RE.search(token.lower())
    return match.group(0) if match else ""


def _affixes(token: str) -> tuple[str, str]:
    """The (prefix, suffix) around `_core`, preserved verbatim."""
    lowered = token.lower()
    match = _WORD_RE.search(lowered)
    if not match:
        return "", token
    start, end = match.span()
    # Slice the ORIGINAL token so surviving case outside the core keeps
    # its shape; the core itself is always re-emitted canonically.
    return token[:start], token[end:]


def parse_number(cores: list[str], at: int) -> tuple[int, int] | None:
    """The integer spelled from `at`, and how many words it takes.

    "fifty" -> (50, 1); "twenty one" -> (21, 2); "a hundred" is (100, 1)
    at the "hundred" (the "a" is not a number word and never parses).
    None where no number starts. The one interpreter every numeral
    pattern below reads, so the word tables stay the single source.
    """
    if at >= len(cores):
        return None
    first = cores[at]
    if first in ONES:
        value, length = ONES[first], 1
    elif first in TENS:
        value, length = TENS[first], 1
    elif first in SCALES:
        value, length = SCALES[first], 1
    else:
        return None
    cursor = at + 1
    # A tens word followed by a one ("twenty one") adds; a scale word
    # multiplies what precedes it ("two hundred") or stands alone.
    if first in TENS and cursor < len(cores) and cores[cursor] in ONES:
        value += ONES[cores[cursor]]
        length += 1
        cursor += 1
    elif first in ONES and cursor < len(cores) and cores[cursor] in SCALES:
        value *= SCALES[cores[cursor]]
        length += 1
        cursor += 1
        if cursor < len(cores) and cores[cursor] in ONES:
            value += ONES[cores[cursor]]
            length += 1
            cursor += 1
        elif cursor < len(cores) and cores[cursor] in TENS:
            tens = TENS[cores[cursor]]
            length += 1
            cursor += 1
            if cursor < len(cores) and cores[cursor] in ONES:
                tens += ONES[cores[cursor]]
                length += 1
                cursor += 1
            value += tens
    return value, length


def _acronym_for(core: str) -> str | None:
    """The canonical acronym `core` names, clitic included, or None."""
    for acronym in CAPTION_ACRONYMS:
        lowered = acronym.lower()
        if core == lowered:
            return acronym
        if core == lowered + "'s":
            return acronym + "'s"
        # 2026-09-19: the transcript says "CMOs" (plural), and a
        # lowercase caption would read "cmos". A bare trailing "s" on
        # an acronym is its plural, the same shape the possessive
        # above already honours.
        if core == lowered + "s":
            return acronym + "s"
    return None


def _read_hyphenated_token(token: str) -> str | None:
    """`_read_hyphenated` with the token's own affixes preserved.

    The generic `_affixes` splitter reads only to the first word run,
    so it would treat the hyphen as a suffix ("five-star" -> "5-star"
    + "-star"). This strips the whole token's outer punctuation first,
    reads the middle, and reattaches.
    """
    prefix = re.match(r"^[^A-Za-z0-9]+", token)
    suffix = re.search(r"[^A-Za-z0-9]+$", token)
    pre = prefix.group(0) if prefix else ""
    suf = suffix.group(0) if suffix else ""
    middle = token[len(pre) : len(token) - len(suf) if suf else len(token)]
    read = _read_hyphenated(middle)
    return f"{pre}{read}{suf}" if read is not None else None


def _read_hyphenated(token: str) -> str | None:
    """A hyphenated token's reading, or None to leave it alone.

    Two shapes only, both ratings-shaped: every part a number word
    ("twenty-one" reads "21"), or a number word qualifying "star"
    ("five-star" reads "5-star"). Anything else ("content-heavy",
    "well-known") is not a numeral and passes through untouched.
    """
    parts = token.split("-")
    if len(parts) < 2 or any(not _core(part) for part in parts):
        return None
    cores = [_core(part) for part in parts]
    if all(part in ONES or part in TENS for part in cores):
        parsed = parse_number(cores, 0)
        if parsed is not None and parsed[1] == len(cores):
            return str(parsed[0])
        return None
    if len(cores) == 2 and cores[1] in ("star", "stars"):
        parsed = parse_number(cores, 0)
        if parsed is not None and parsed[1] == 1:
            return f"{parsed[0]}-{cores[1]}"
    return None


def _plan_tokens(cores: list[str]) -> list[tuple[str, int]]:
    """The caption's tokens, read. Each is (output, words consumed).

    The single decision procedure both entry points share: the word-list
    entry uses the consumption counts to merge timings, the text entry
    uses the outputs. One procedure, so the two can never disagree.
    """
    out: list[tuple[str, int]] = []
    i = 0
    while i < len(cores):
        core = cores[i]
        acronym = _acronym_for(core)
        if acronym is not None:
            out.append((acronym, 1))
            i += 1
            continue
        parsed = parse_number(cores, i)
        if parsed is None:
            out.append((core, 1))
            i += 1
            continue
        value, length = parsed
        after = cores[i + length] if i + length < len(cores) else ""
        # A decimal version or rating: "two point oh", "three point five".
        # The phrase owns the digit run after "point" and nothing past
        # it ("three point five stars" converts the version and keeps
        # the stars a word until the star pattern reads them).
        if after == "point":
            run = 0
            while (
                i + length + 1 + run < len(cores)
                and cores[i + length + 1 + run] in DIGIT_WORDS
            ):
                run += 1
            if run:
                digits = "".join(
                    DIGIT_WORDS[cores[i + length + 1 + j]] for j in range(run)
                )
                consumed = length + 1 + run
                out.append((f"{value}.{digits}", consumed))
                i += consumed
                continue
        # A year: two two-digit readings side by side ("twenty twenty").
        if 20 <= value <= 99 and length == 1 and i + 1 < len(cores):
            following = parse_number(cores, i + 1)
            if following is not None and following[1] == 1 and 1 <= following[0] <= 99:
                out.append((f"{value:02d}{following[0]:02d}", 2))
                i += 2
                continue
        if after == "percent":
            out.append((f"{value} percent", length + 1))
            i += length + 1
            continue
        if after == "times":
            out.append((f"{value} times", length + 1))
            i += length + 1
            continue
        if after in ("star", "stars"):
            out.append((f"{value} {after}", length + 1))
            i += length + 1
            continue
        # A bare cardinal reads as digits - except a lone "one", which
        # is near-always a pronoun in this footage ("one of the best
        # ways"), and except after a rank marker ("number one", "the
        # top three"), which the captain has not ruled on.
        previous = out[-1][0].lower() if out else ""
        if length == 1 and value == 1:
            out.append((core, 1))
            i += 1
            continue
        if previous in RANK_MARKERS:
            out.append((core, 1))
            i += 1
            continue
        out.append((str(value), length))
        i += length
    return out


def apply_caption_reading_text(text: str) -> str:
    """The text-level reading, for the plan's own contract check.

    Lowercases nothing and invents nothing: the planner lowercases first
    (`apply_caption_case`) and this restores what the declarations name.
    Idempotent - reading its own output changes nothing - which is what
    step 4.01 asserts every card with.
    """
    tokens = text.split()
    if not tokens:
        return text
    cores = [_core(token) for token in tokens]
    planned = _plan_tokens(cores)
    rebuilt = []
    cursor = 0
    for output, consumed in planned:
        first, last = tokens[cursor], tokens[cursor + consumed - 1]
        prefix, _ = _affixes(first)
        _, suffix = _affixes(last)
        if consumed == 1 and "-" in first and output != cores[cursor]:
            hyphen = _read_hyphenated_token(first)
            rebuilt.append(hyphen if hyphen else first)
        elif consumed == 1 and output == cores[cursor]:
            rebuilt.append(tokens[cursor])
        else:
            rebuilt.append(f"{prefix}{output}{suffix}")
        cursor += consumed
    return " ".join(rebuilt)


def apply_caption_reading(words: list[dict]) -> list[dict]:
    """The word-list reading: text fixed, timings kept, none invented.

    Each input word carries `word`, `start` and `end` and may carry
    more (speaker clocks, scores - everything unrecognised rides along
    untouched). A merged numeral spans its first word's start to its
    last word's end; every other word keeps its own span. The output is
    never longer in words than the input, and never carries a word with
    no span.
    """
    if not words:
        return words
    surfaces = [str(entry.get("word", "")) for entry in words]
    cores = [_core(surface) for surface in surfaces]
    planned = _plan_tokens(cores)
    hyphenated = [
        _read_hyphenated_token(surface) if "-" in surface else None
        for surface in surfaces
    ]
    rebuilt = []
    cursor = 0
    for output, consumed in planned:
        span = words[cursor : cursor + consumed]
        first, last = span[0], span[-1]
        prefix, _ = _affixes(str(first.get("word", "")))
        _, suffix = _affixes(str(last.get("word", "")))
        if consumed == 1 and hyphenated[cursor] is not None:
            text = hyphenated[cursor]
        elif consumed == 1 and output == cores[cursor]:
            text = str(first.get("word", ""))
        else:
            text = f"{prefix}{output}{suffix}"
        entry = dict(first)
        entry["word"] = text
        entry["start"] = first.get("start")
        entry["end"] = last.get("end")
        rebuilt.append(entry)
        cursor += consumed
    return rebuilt
