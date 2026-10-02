# `library.tools.fusion.parser` - the builtin-preset incident

Moved verbatim from the module docstring of `tests/unit/resolve/test_fusion_builtin_presets.py`
(2026-10-02 test consolidation). The tests keep the invariant; this is the story.

```text
DaVinci's own 143 shipped presets must survive our Fusion harness.

56 of the 143 `.setting` files under `library/presets/resolve-builtin/`
used to fail a parse + serialize round-trip, and the failure was read as
"the effect crashes". It was ours in every case:

- 38 were our own authorship rules (FusionNode._validate) refusing
  Blackmagic's macros. 20 built-ins carry a Background with no GlobalOut,
  14 an EllipseMask with no Invert, 4 an ApplyMode on Merge. Those rules
  describe comps THIS pipeline writes; a foreign file is not bound by them.
- 14 were an anonymous nested table (`Curves = { { Points = ... } }`) whose
  opening brace `_parse_table` swallowed instead of recursing into, so the
  matching `}` closed the parent table early and the desync cascaded.
- 4 were a namespaced Fuse plugin tool type (`Fuse.RealFastNoiseFuse`),
  where the identifier stopped at the dot and left a bare '.' that
  `_parse_number` then tried to read as a number.

Worse, the 87 that "passed" were quietly corrupted by the same desync:
ambient_occlusion was losing 19 of its 30 nodes and reporting success.

No test parsed a single real `.setting` file, which is why none of this
was visible. These do.
```
