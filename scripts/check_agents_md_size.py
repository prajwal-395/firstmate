#!/usr/bin/env python3
"""Gate the size of AGENTS.md to prevent unbounded growth."""

import re
import sys

def sections(text):
    out = {}
    ms = list(re.finditer(r'(?m)^## .*$', text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[re.sub(r'\s+', ' ', m.group(0)).strip()] = end - m.start() - len(m.group(0))
    return out

def main():
    # RATCHET: This ceiling may ONLY EVER MOVE DOWN, never up.
    # It exists to force condensation. Do not raise it to make a change fit.
    CEILING = 172348
    filename = sys.argv[1] if len(sys.argv) > 1 else 'AGENTS.md'
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"{filename} not found.")
        sys.exit(1)
        
    size = len(content)
    if size > CEILING:
        print(f"AGENTS.md size ({size} chars) exceeds the ceiling ({CEILING} chars).")
        print("This file only grows if something else shrinks.")
        print("DO NOT raise this ceiling to make your change fit; remove something instead.")
        print("\nLargest sections where the bulk is:")
        
        secs = sections(content)
        sorted_secs = sorted(secs.items(), key=lambda x: x[1], reverse=True)
        for name, sz in sorted_secs[:5]:
            print(f"  - {name}: {sz} characters")
            
        sys.exit(1)
        
    print(f"AGENTS.md size check passed: {size} <= {CEILING}")

if __name__ == "__main__":
    main()
