#!/usr/bin/env python3
"""Gate a RESTRUCTURE of AGENTS.md: no rule-bearing token may vanish from the corpus.

Deliberately cruder and stricter than a normative-sentence extractor. It asks one
question - does every backticked identifier, numeric threshold and bold statement
in the original still appear SOMEWHERE the rule corpus - because a rule survives
rewording but never survives losing the name it is about.

    # condensation in place, the original single-file use
    python3 check_agents_md_preservation.py \
        --before <(git show origin/main:AGENTS.md) --after AGENTS.md

    # a MOVE: AGENTS.md keeps an index row, the detail lands in the module
    python3 check_agents_md_preservation.py \
        --before <(git show origin/main:AGENTS.md) \
        --after AGENTS.md library/tools/craft_role.py library/tools/sfx_level.py

Exit 0 when nothing rule-bearing vanished, 1 otherwise. Test-file references
(`tests/test_*.py`) are exempt: they are discoverable by search.

Why the corpus is ENUMERATED and never a glob
---------------------------------------------
The first `--after` path is the PRIMARY file; the rest are the claimed
destinations, listed one by one on the command line. Scanning the repository
instead would let a rule "survive" because its identifier happens to occur in an
unrelated file, and the guarantee would become a coincidence. The enumeration is
also the reviewable artifact: it IS the list of destinations the move claims,
and a reviewer reads it as such.

What a PASS means
-----------------
Not "all 605 present". The report is a MAPPING - which destination received
which tokens - so a reviewer can see that a colour rule went to the colour
module and not into an unrelated docstring that happened to contain the word.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

EXEMPT_PREFIXES = ("tests/test_",)

def identifiers(text):
    return set(re.findall(r'`([A-Za-z_][A-Za-z0-9_./\-]{3,})`', text))

def thresholds(text):
    return set(re.findall(
        r'(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|s\b|ms\b|dB\b|px\b|LUFS\b|frames?\b|characters?\b)', text))

def headings(text):
    """Every heading line, normalised. Section numbers are cross-referenced from
    code comments (`AGENTS.md 10.1`, `§10.5`) - 241 such citations exist in
    `library/` - so a deleted heading breaks them even when the prose beneath it
    has merely moved. Headings stay in the PRIMARY file; a destination cannot
    satisfy this one."""
    return [re.sub(r'\s+', ' ', h).strip()
            for h in re.findall(r'(?m)^(#{2,3} .*)$', text)]


# These two no longer FAIL a section on their own. They are the trigger for the
# rule that replaced them: a section that has shrunk this far must carry an index
# row naming a destination that GAINED content (`_index_row_rule`). Shrinking a
# section is the point of the restructure; shrinking it into silence is not.
MAX_SECTION_CUT = 0.70   # a section cut harder than this must point somewhere
MIN_SECTION_BODY = 150   # a section body smaller than this is effectively an index row


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


def section_bodies(text):
    """Same split as `sections`, but the TEXT of each body - what an index row
    is read out of."""
    out = {}
    ms = list(re.finditer(r'(?m)^## .*$', text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[re.sub(r'\s+', ' ', m.group(0)).strip()] = text[m.end():end]
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
    return {re.sub(r'\s+', ' ', x) for x in re.findall(r'\*\*(.{15,200}?)\*\*', text, re.DOTALL)}


def size_at_rev(path, rev):
    """Bytes of `path` at `rev`, or None when it did not exist there.

    A destination that did not exist at the baseline has GAINED by definition.
    A git failure RAISES rather than answering: a check that cannot measure
    growth must not report a pass on it.
    """
    if shutil.which("git") is None:
        raise RuntimeError("git is not on PATH; cannot measure destination growth")
    p = subprocess.run(["git", "show", f"{rev}:{path}"],
                       capture_output=True, check=False)
    if p.returncode == 0:
        return len(p.stdout)
    err = p.stderr.decode("utf-8", "replace").strip()
    if "does not exist in" in err or "exists on disk, but not in" in err:
        return None          # legitimately new at this revision - it has gained
    raise RuntimeError(f"git show {rev}:{path} failed, so growth is UNMEASURED: {err}")


_PATH_RE = re.compile(r'`?((?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+(?:\.py|\.md|\.json|\.yaml|\.toml)?)`?')

def paths_named_in(body):
    """Destination paths an index row points at."""
    return {m.group(1).rstrip('.,;:') for m in _PATH_RE.finditer(body)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True,
                   help="the original AGENTS.md (a file, e.g. a git show redirect)")
    p.add_argument("--after", required=True, nargs="+", metavar="PATH",
                   help="the rule corpus, ENUMERATED. First path is AGENTS.md "
                        "itself; the rest are the claimed destinations.")
    p.add_argument("--gained-since", default="origin/main", metavar="REV",
                   help="revision a destination's growth is measured against "
                        "(default: origin/main)")
    a = p.parse_args()

    with open(a.before, encoding="utf-8") as fh:
        before = fh.read()
    primary_path, dest_paths = a.after[0], a.after[1:]
    missing = [q for q in a.after if not os.path.exists(q)]
    if missing:
        print("corpus paths do not exist:", ", ".join(missing))
        return 1
    corpus = {}
    for q in a.after:
        with open(q, encoding="utf-8") as fh:
            corpus[q] = fh.read()
    primary = corpus[primary_path]

    # ---- where each rule-bearing token landed -------------------------------
    # A token still in the primary file has not moved and is not reported;
    # the mapping is about what LEFT AGENTS.md and where it went.
    landed = defaultdict(lambda: {"ids": [], "nums": [], "bold": []})
    def locate(tok, flat=False):
        hits = []
        for q in dest_paths:
            hay = re.sub(r'\s+', ' ', corpus[q]) if flat else corpus[q]
            if tok in hay:
                hits.append(q)
        return hits

    lost_ids, moved_ids = [], 0
    for i in sorted(identifiers(before)):
        if i.startswith(EXEMPT_PREFIXES) or i in primary:
            continue
        where = locate(i)
        if where:
            moved_ids += 1
            for q in where:
                landed[q]["ids"].append(i)
        else:
            lost_ids.append(i)

    lost_nums, moved_nums = [], 0
    prim_nums = thresholds(primary)
    for n in sorted(thresholds(before) - prim_nums, key=float):
        where = [q for q in dest_paths if n in thresholds(corpus[q])]
        if where:
            moved_nums += 1
            for q in where:
                landed[q]["nums"].append(n)
        else:
            lost_nums.append(n)

    primary_flat = re.sub(r'\s+', ' ', primary)
    lost_bold, moved_bold = [], 0
    for x in sorted(bold(before)):
        if x in primary_flat:
            continue
        where = locate(x, flat=True)
        if where:
            moved_bold += 1
            for q in where:
                landed[q]["bold"].append(x)
        else:
            lost_bold.append(x)

    head_b, head_a = headings(before), headings(primary)
    lost_head = [h for h in head_b if h not in head_a]

    dumps = []
    for q in a.after:
        for d in re.findall(r'\((?:M|Mentions): [^)]*\)', corpus[q]):
            dumps.append((q, d))

    new_text = synthesized(before, primary)

    # ---- the rule that REPLACED MAX_SECTION_CUT / MIN_SECTION_BODY ----------
    # A section may shrink to an index row. It may not shrink into silence: the
    # row must name a destination in the corpus, and that destination must have
    # gained content since the baseline.
    sec_b, sec_a = sections(before), sections(primary)
    bodies_a = section_bodies(primary)
    gained_cache = {}
    def gained(q):
        if q not in gained_cache:
            was = size_at_rev(q, a.gained_since)
            gained_cache[q] = (was is None) or (len(corpus[q].encode()) > was)
        return gained_cache[q]

    silent_sections = []
    pointed_sections = []
    for name, bsize in sec_b.items():
        asize = sec_a.get(name, 0)
        cut = (bsize - asize) / bsize if bsize else 0
        if not (cut > MAX_SECTION_CUT or (bsize >= MIN_SECTION_BODY and asize < MIN_SECTION_BODY)):
            continue
        named = paths_named_in(bodies_a.get(name, ""))
        pointing = sorted(q for q in named if q in dest_paths and gained(q))
        if pointing:
            pointed_sections.append((name, bsize, asize, pointing))
        else:
            silent_sections.append((name, bsize, asize, sorted(named)))

    # ---- report -------------------------------------------------------------
    print(f"corpus: {primary_path} (primary) + {len(dest_paths)} enumerated destination(s)")
    print(f"size (primary)   : {len(before):,} -> {len(primary):,} chars")
    print(f"identifiers lost : {len(lost_ids)}      ({moved_ids} moved to a destination)")
    print(f"thresholds lost  : {len(lost_nums)}      ({moved_nums} moved)")
    print(f"bold lost        : {len(lost_bold)}      ({moved_bold} moved)")
    print(f"headings lost    : {len(lost_head)}   ({len(head_b)} -> {len(head_a)})")
    print(f"sections shrunk  : {len(pointed_sections)} pointing at a destination, "
          f"{len(silent_sections)} silent")
    print(f"token dumps      : {len(dumps)}   (FAILS the check)")
    print(f"lines reworded   : {len(new_text)}   (informational - rewording is allowed)")

    if landed:
        print("\nWHERE THE MOVED RULES LANDED:")
        for q in sorted(landed, key=lambda k: -sum(len(v) for v in landed[k].values())):
            g = landed[q]
            print(f"  {q}")
            print(f"    {len(g['ids'])} identifiers, {len(g['nums'])} thresholds, "
                  f"{len(g['bold'])} bold statements")
            if g["ids"]:
                shown = ", ".join(f"`{x}`" for x in g["ids"][:10])
                more = f"  (+{len(g['ids'])-10} more)" if len(g["ids"]) > 10 else ""
                print(f"    {shown}{more}")
    if pointed_sections:
        print("\nSECTIONS THAT BECAME AN INDEX ROW (accepted):")
        for name, bs, as_, ptr in pointed_sections:
            print(f"  {name}\n    body {bs} -> {as_} chars, points at {', '.join(ptr)}")

    lines = before.splitlines()
    def where_line(tok):
        for n, l in enumerate(lines, 1):
            if tok in l:
                return n, re.sub(r'\s+', ' ', l).strip()[:200]
        return None, ""

    if lost_ids:
        print("\nIDENTIFIERS THAT VANISHED FROM THE WHOLE CORPUS (restore, move or enumerate the destination):")
        for i in lost_ids:
            n, src = where_line(i)
            print(f"  `{i}`  origin line {n}\n    > {src}")
    if lost_nums:
        print("\nTHRESHOLDS THAT VANISHED:", ", ".join(lost_nums))
    if lost_bold:
        print("\nBOLD NO LONGER VERBATIM ANYWHERE IN THE CORPUS:")
        for x in lost_bold:
            print(f"  * {x[:200]}")
    if lost_head:
        print("\nHEADINGS THAT VANISHED (section numbers are cross-referenced from code):")
        for h in lost_head:
            print(f"  {h}")
    if silent_sections:
        print("\nSECTIONS THAT SHRANK INTO SILENCE:")
        print("  A section may shrink to an index row. The row must NAME a destination")
        print("  that is in the enumerated corpus AND has gained content since "
              f"{a.gained_since}.")
        for name, bs, as_, named in silent_sections:
            print(f"  {name}\n    body {bs} -> {as_} chars")
            print(f"    paths named in what remains: {', '.join(named) if named else '(none)'}")
    if dumps:
        print("\nTOKEN DUMPS - a deleted rule with its identifiers pasted after it:")
        for q, d in dumps[:8]:
            print(f"  {q}: {d[:150]}")
        if len(dumps) > 8:
            print(f"  ... and {len(dumps)-8} more")
        print("  These satisfy every other check above while destroying the rule.")
        print("  Restore the SENTENCE the identifiers came from; delete the parenthetical.")

    failed = bool(lost_ids or lost_bold or lost_head or silent_sections or dumps)
    print("\nRESULT:", "FAIL - content vanished" if failed else "PASS")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
