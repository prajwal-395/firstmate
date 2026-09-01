#!/usr/bin/env python3
"""Extract normative statements from AGENTS.md for rule-preservation auditing.

Usage:
    # Extract rules from a single file:
    python3 scripts/extract_agents_md_rules.py AGENTS.md

    # Diff before and after:
    python3 scripts/extract_agents_md_rules.py --before <(git show origin/main:AGENTS.md) --after AGENTS.md

    # Using process substitution with a branch:
    python3 scripts/extract_agents_md_rules.py --before <(git show HEAD~1:AGENTS.md) --after AGENTS.md

Normative statements are lines containing:
- Modal verbs: never, always, must, required, shall
- Prohibitions: do not, does not, cannot, may not, is not
- Exclusives: only, exactly
- Numeric thresholds (digits followed by units or in context)
- Named contracts, files, steps, flags (identified by backtick-quoted names)
"""

import argparse
import re
import sys
from pathlib import Path


# Patterns that indicate a normative statement
NORMATIVE_PATTERNS = [
    # Modal verbs and prohibitions - word-boundary matched, case-insensitive
    r'\bnever\b',
    r'\balways\b',
    r'\bmust\b',
    r'\brequired\b',
    r'\bshall\b',
    r'\bdo not\b',
    r'\bdo NOT\b',
    r'\bdoes not\b',
    r'\bcannot\b',
    r'\bmay not\b',
    r'\bis not\b',
    r'\bis NOT\b',
    r'\bare not\b',
    r'\bonly\b',
    r'\bexactly\b',
    r'\brefuse[sd]?\b',
    r'\bforbidden\b',
    r'\bprohibit',
    r'\billegal\b',
    # Numeric thresholds
    r'\b\d+(\.\d+)?\s*(px|ms|seconds?|minutes?|frames?|fps|MiB|bytes?|chars?|characters?|%)\b',
    r'\b\d+x\b',
]

# Compile all patterns
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in NORMATIVE_PATTERNS]

# Additional pattern for UPPERCASE emphasis (common in this file for rules)
EMPHASIS_PATTERN = re.compile(r'\b[A-Z]{2,}\b')
EMPHASIS_WORDS = {
    'NEVER', 'ALWAYS', 'MUST', 'ONLY', 'NOT', 'DO', 'DOES', 'CANNOT',
    'REQUIRED', 'SHALL', 'EXACTLY', 'REFUSED', 'FORBIDDEN', 'PROHIBITED',
    'CHECKED', 'HARD', 'WITHHELD', 'WITHDRAWN', 'SEPARATE', 'SAME',
}


def extract_normative_statements(text: str) -> list[dict]:
    """Extract normative statements from AGENTS.md text.

    Returns a list of dicts with keys:
      - line_num: 1-based line number
      - section: current section header
      - text: the line text
      - triggers: which normative patterns matched
    """
    results = []
    current_section = "(preamble)"
    lines = text.split('\n')

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track section headers
        if stripped.startswith('#'):
            current_section = stripped.lstrip('#').strip()
            continue

        # Skip empty lines, pure links, and code fences
        if not stripped or stripped.startswith('```') or stripped == '---':
            continue

        # Check for normative patterns
        triggers = []
        for pattern in COMPILED_PATTERNS:
            if pattern.search(stripped):
                triggers.append(pattern.pattern)

        # Check for emphasized normative words
        emphasis_matches = EMPHASIS_PATTERN.findall(stripped)
        for word in emphasis_matches:
            if word in EMPHASIS_WORDS:
                triggers.append(f'EMPHASIS:{word}')

        if triggers:
            results.append({
                'line_num': i + 1,
                'section': current_section,
                'text': stripped,
                'triggers': triggers,
            })

    return results


def format_statement(stmt: dict) -> str:
    """Format a normative statement for display."""
    return f"L{stmt['line_num']:4d} [{stmt['section']}] {stmt['text']}"


def normalize_for_comparison(text: str) -> str:
    """Normalize a statement for fuzzy comparison.

    Strips markdown formatting, extra whitespace, and normalizes case
    to detect semantically equivalent statements even if they moved.
    """
    # Remove markdown bold/italic
    text = re.sub(r'\*+', '', text)
    # Remove backtick formatting
    text = re.sub(r'`[^`]*`', lambda m: m.group().strip('`'), text)
    # Remove [why] links
    text = re.sub(r'\[why\]\([^)]*\)', '', text)
    # Remove other markdown links but keep text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def diff_statements(before_stmts: list[dict], after_stmts: list[dict]) -> dict:
    """Diff two sets of normative statements.

    Returns dict with:
      - removed: statements in before but not after
      - added: statements in after but not before
      - preserved: statements in both (possibly moved)
    """
    before_normalized = {
        normalize_for_comparison(s['text']): s for s in before_stmts
    }
    after_normalized = {
        normalize_for_comparison(s['text']): s for s in after_stmts
    }

    before_set = set(before_normalized.keys())
    after_set = set(after_normalized.keys())

    removed = [before_normalized[t] for t in sorted(before_set - after_set,
               key=lambda t: before_normalized[t]['line_num'])]
    added = [after_normalized[t] for t in sorted(after_set - before_set,
             key=lambda t: after_normalized[t]['line_num'])]
    preserved = [before_normalized[t] for t in sorted(before_set & after_set,
                 key=lambda t: before_normalized[t]['line_num'])]

    return {
        'removed': removed,
        'added': added,
        'preserved': preserved,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Extract normative statements from AGENTS.md'
    )
    parser.add_argument(
        'file', nargs='?',
        help='Single file to extract from'
    )
    parser.add_argument(
        '--before',
        help='Before file for diffing'
    )
    parser.add_argument(
        '--after',
        help='After file for diffing'
    )
    parser.add_argument(
        '--format', choices=['text', 'count'], default='text',
        help='Output format'
    )

    args = parser.parse_args()

    if args.before and args.after:
        # Diff mode
        before_text = Path(args.before).read_text()
        after_text = Path(args.after).read_text()

        before_stmts = extract_normative_statements(before_text)
        after_stmts = extract_normative_statements(after_text)

        diff = diff_statements(before_stmts, after_stmts)

        before_chars = len(before_text)
        after_chars = len(after_text)
        reduction = before_chars - after_chars
        pct = (reduction / before_chars) * 100 if before_chars else 0

        print(f"Character count: {before_chars:,} -> {after_chars:,} "
              f"({reduction:,} removed, {pct:.1f}% reduction)")
        print(f"Under 150,000 limit: {'YES' if after_chars < 150000 else 'NO'}")
        print()
        print(f"Normative statements: {len(before_stmts)} before, "
              f"{len(after_stmts)} after")
        print(f"  Preserved: {len(diff['preserved'])}")
        print(f"  Removed:   {len(diff['removed'])}")
        print(f"  Added:     {len(diff['added'])}")

        if diff['removed']:
            print(f"\n{'='*80}")
            print("REMOVED normative statements (each must be accounted for):")
            print(f"{'='*80}")
            for stmt in diff['removed']:
                print(f"\n  {format_statement(stmt)}")

        if diff['added']:
            print(f"\n{'='*80}")
            print("ADDED normative statements:")
            print(f"{'='*80}")
            for stmt in diff['added']:
                print(f"\n  {format_statement(stmt)}")

        # Exit with error if removed statements exist (they need accounting)
        if diff['removed']:
            print(f"\n{len(diff['removed'])} removed statements need "
                  "accounting in condensation note.")
            sys.exit(0)  # Don't fail - just report

    elif args.file:
        # Single file mode
        text = Path(args.file).read_text()
        stmts = extract_normative_statements(text)

        if args.format == 'count':
            print(f"Total normative statements: {len(stmts)}")
        else:
            current_section = None
            for stmt in stmts:
                if stmt['section'] != current_section:
                    current_section = stmt['section']
                    print(f"\n## {current_section}")
                print(f"  {format_statement(stmt)}")
            print(f"\nTotal: {len(stmts)} normative statements")
    else:
        parser.error("Provide either a file or --before/--after for diffing")


if __name__ == '__main__':
    main()
