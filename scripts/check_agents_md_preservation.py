#!/usr/bin/env python3
"""Gate a condensation of AGENTS.md: no rule-bearing token may vanish.

Deliberately cruder and stricter than a normative-sentence extractor. It asks one
question - does every backticked identifier, numeric threshold and bold statement
in the original still appear SOMEWHERE in the condensed file - because a rule
survives rewording but never survives losing the name it is about.

    python3 check_agents_md_preservation.py --before <(git show origin/main:AGENTS.md) --after AGENTS.md

Exit 0 when nothing rule-bearing vanished, 1 otherwise. Test-file references
(`tests/test_*.py`) are exempt: they are discoverable by search.
"""
import argparse, re, sys

EXEMPT_PREFIXES = ("tests/test_",)

def identifiers(text):
    return set(re.findall(r'`([A-Za-z_][A-Za-z0-9_./\-]{3,})`', text))

def thresholds(text):
    return set(re.findall(
        r'(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|s\b|ms\b|dB\b|px\b|LUFS\b|frames?\b|characters?\b)', text))

def headings(text):
    """Every heading line, normalised. Section numbers are cross-referenced from
    code comments (`AGENTS.md 10.1`, `§10.5`), so a deleted heading breaks them
    even when the prose beneath it survives."""
    return [re.sub(r'\s+', ' ', h).strip()
            for h in re.findall(r'(?m)^(#{2,3} .*)$', text)]


MAX_SECTION_CUT = 0.70   # a section cut harder than this lost content, not prose
MIN_SECTION_BODY = 150   # a section body smaller than this is effectively deleted


def sections(text):
    """Body size of each `## ` section. A section with no backticked token,
    threshold or bold statement can be deleted whole without any other check
    noticing - section 1 went 488 -> 28 chars that way."""
    out = {}
    ms = list(re.finditer(r'(?m)^## .*$', text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[re.sub(r'\s+', ' ', m.group(0)).strip()] = end - m.start() - len(m.group(0))
    return out


def is_subsequence(short, long_):
    """True when `short` can be formed from `long_` by DELETING characters only."""
    it = iter(long_)
    return all(c in it for c in short)


def synthesized(before, after):
    """Lines in `after` that were WRITTEN rather than TRIMMED.

    Condensation is deletion. A line that is not a character-subsequence of any
    original line is new text, which is how a token-preserving check gets gamed:
    delete the sentence, append `(M: tok, tok, tok)`, and every other check here
    still passes while the rule becomes unreadable. 210 lines went that way once.
    """
    originals = [l.strip() for l in before.splitlines() if l.strip()]
    by_head = {}
    for o in originals:
        by_head.setdefault(o[:12], []).append(o)
    out = []
    for line in after.splitlines():
        s = line.strip()
        if len(s) < 25 or s.startswith("#"):
            continue
        if s in originals:
            continue
        cands = by_head.get(s[:12]) or originals
        if not any(is_subsequence(s, o) for o in cands):
            out.append(s)
    return out


def bold(text):
    return {re.sub(r'\s+', ' ', x) for x in re.findall(r'\*\*(.{15,200}?)\*\*', text, re.S)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    a = p.parse_args()
    before = open(a.before).read()
    after = open(a.after).read()
    after_flat = re.sub(r'\s+', ' ', after)

    lost_ids = sorted(i for i in identifiers(before)
                      if i not in after and not i.startswith(EXEMPT_PREFIXES))
    lost_nums = sorted(thresholds(before) - thresholds(after), key=float)
    lost_bold = sorted(x for x in bold(before) if x not in after_flat)
    head_b, head_a = headings(before), headings(after)
    lost_head = [h for h in head_b if h not in head_a]
    import re as _re
    dumps = _re.findall(r'\((?:M|Mentions): [^)]*\)', after)
    new_text = synthesized(before, after)
    sec_b, sec_a = sections(before), sections(after)
    gutted = []
    for name, bsize in sec_b.items():
        asize = sec_a.get(name, 0)
        cut = (bsize - asize) / bsize if bsize else 0
        if cut > MAX_SECTION_CUT or (bsize >= MIN_SECTION_BODY and asize < MIN_SECTION_BODY):
            gutted.append((name, bsize, asize, cut))

    print(f"size: {len(before):,} -> {len(after):,} chars")
    print(f"identifiers lost : {len(lost_ids)}")
    print(f"thresholds lost  : {len(lost_nums)}")
    print(f"bold lost        : {len(lost_bold)}")
    print(f"headings lost    : {len(lost_head)}   ({len(head_b)} -> {len(head_a)})")
    print(f"sections gutted  : {len(gutted)}")
    print(f"token dumps      : {len(dumps)}   (FAILS the check)")
    print(f"lines reworded   : {len(new_text)}   (informational - rewording is allowed)")

    lines = before.splitlines()
    def where(tok):
        for n, l in enumerate(lines, 1):
            if tok in l:
                return n, re.sub(r'\s+', ' ', l).strip()[:200]
        return None, ""

    if lost_ids:
        print("\nIDENTIFIERS THAT VANISHED (restore or justify each):")
        for i in lost_ids:
            n, src = where(i)
            print(f"  `{i}`  origin line {n}\n    > {src}")
    if lost_nums:
        print("\nTHRESHOLDS THAT VANISHED:", ", ".join(lost_nums))
    if lost_bold:
        print("\nBOLD NO LONGER VERBATIM:")
        for x in lost_bold:
            print(f"  * {x[:200]}")

    if lost_head:
        print("\nHEADINGS THAT VANISHED (section numbers are cross-referenced from code):")
        for h in lost_head:
            print(f"  {h}")

    if gutted:
        print("\nSECTIONS CUT TO NOTHING (prose-only content is invisible to every check above):")
        for name, bsize, asize, cut in gutted:
            print(f"  {name}\n    body {bsize} -> {asize} chars ({cut*100:.1f}% cut)")

    if dumps:
        print("\nTOKEN DUMPS - a deleted rule with its identifiers pasted after it:")
        for d in dumps[:8]:
            print(f"  {d[:170]}")
        if len(dumps) > 8:
            print(f"  ... and {len(dumps)-8} more")
        print("  These satisfy every other check above while destroying the rule.")
        print("  Restore the SENTENCE the identifiers came from; delete the parenthetical.")

    failed = bool(lost_ids or lost_bold or lost_head or gutted or dumps)
    print("\nRESULT:", "FAIL - content vanished" if failed else "PASS")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
