# Semantic conflict detection (VC-005)

The captain's development punch list, 2026-10-20, item VC-005:

> - Compare affected source intervals, timeline regions, targets, and
>   property keys. - Distinguish nonconflicting independent edits from
>   overlapping modifications. - Auto-merge only where semantics and
>   preservation can be proven. - Require resolution when two changes
>   disagree about the same editorial decision.

This is the module that answers it: `library/tools/semantic_conflict.py`,
wired into the carry in `library/tools/patch_algebra.py`.

## The gap it closes

Ren could already say whether two PATCHES compose
(`library/tools/patch_algebra.py`: two writes MEET when one's key is a
prefix of the other's), and could carry a captain's hand edit through a
rebuild (`library/tools/editor_edit_carry.py`: what the editor's change
says in source terms). Neither could say whether a Ren edit and a hand
edit on the same clip AGREE, because the two vocabularies never met.

A key-prefix algebra answers "do these two writes meet?". A conflict is
a different question: it is a claim about the SAME thing.

    two changes that trim one clip to different windows
    two changes that move one clip to different places
    two changes that set one property on one clip to different values
    a hand edit that moves a clip and a Ren edit placed after it, whose
        frames the move invalidated

## The four axes

A change is read off a RECORDED state, never guessed, and it carries
four things:

| axis | what it is | where it is read |
|---|---|---|
| source interval | the passage of the source media the change claims | `source_in_frame`..`source_out_frame` |
| timeline region | the frames on the timeline the change claims | `record_in`..`record_out` |
| target | the item the change governs | the Resolve `unique_id`, or `timeline` |
| property key | the property the change writes, and the value | `transform.Pan`, `enabled`, `color.luts`, ... |

Three readers produce that shape, all from existing records:

- `edits_between(before, after)` - the difference between two recorded
  states of one timeline. A hand edit is read this way: the difference
  between the state before it and the state after it.
- `edits_from_operations(operations, base)` - a prepared `EditPatch`'s
  operations, read against the base they were planned on.
- `edits_from_carried_edits(records)` - the carried-edit ledger, whose
  entries already speak in source terms.

## The verdicts

| verdict | meaning | what happens |
|---|---|---|
| `independent` | the two changes claim different things | both land; `merge()` keeps both |
| `conflict` | the two claim the SAME thing differently | resolution required, and the report names BOTH versions |
| `unproven` | nothing here can show they are disjoint | resolution required: an absent measurement is not evidence of agreement |

The rules, in the order they are asked (`compare`):

1. the same TARGET, writing the same PROPERTY KEY with different values
   - a CONFLICT: the two disagree about the same editorial decision;
2. the same TARGET, one of them removing the item the other edits - a
   CONFLICT;
3. the same TARGET, both claiming a different source window or a
   different place - a CONFLICT;
4. the same TARGET, writing different axes - INDEPENDENT: a trim and a
   framing change are two decisions about one item, and both can hold;
5. different targets on the same ROW, both PLACING content over
   overlapping frames - a CONFLICT: one row holds one item at a frame;
6. otherwise - INDEPENDENT.

A removal does not place anything, so a removal's old frames are not
compared with what another change claims: reading them that way would
call every swap (remove one item, place another where it was) a clash.

### The coordinate frame

A change that moves a row's frames - an insert, a delete, a trim, a
move - invalidates the frames of everything after it on that row. A
change on that row past that point is UNPROVEN, not independent: it
may be independent, and nothing here can prove it.

### Who yields

A captain's edit and a Ren edit that conflict do not resolve by
strength. The captain's hand is the state the captain accepted, so the
report's `resolution` names the captain's edit as the one that stands
and Ren's edit as refused - and says so for every pair it reports.

## The wiring: the carry over a hand edit

A patch is planned against one recorded generation. When a hand edit
lands between that generation and the head, the patch has to move onto
the head over it. The old rule refused EVERY such carry:

> generation N is a change nobody recorded (a hand edit), so nothing
> can say what it meets

Now the hand edit is judged (`patch_algebra.judge_hand_edits`):

- a hand edit that DISAGREES with the patch refuses, and the refusal
  names both sides - the generation that carried the hand edit and the
  patch that would have overwritten it;
- a hand edit that is provably DISJOINT does not block the patch: the
  patch moves onto the head over that generation and the hand edit
  stands;
- a hand edit that cannot be shown to be disjoint still refuses, and an
  operation the comparison cannot describe is one thing it cannot judge
  - never read as agreement.

The moved patch is then validated on the head, and its preconditions
are judged there, exactly as before.

The Rust backend (`native/ren-core`) is untouched: its carry still
refuses an unattributed generation, and the parity harness
(`tests/unit/resolve/test_ren_core_parity.py`) still compares the two
implementations on the same corpus. The semantic judgement is made in
`patch_algebra.carry`, before either backend is asked, and the Python
carry is the one that moves a patch over a hand edit it proved
disjoint.

## The measurement

`scripts/benchmark_semantic_conflict.py` measures the rule change on
the RECORDED state of a Ren-owned scratch project in the machine's
shadow store - the store is opened READ-ONLY, because a measurement
must not write to the state it measures. The denominator is every
(hand edit, the next change after it) pair: under the old rule each of
them was a refusal, and under the new rule each is either a refusal
that names both sides or a carry that lands with the hand edit
standing.

    python3 scripts/benchmark_semantic_conflict.py
    python3 scripts/benchmark_semantic_conflict.py --timeline "fm-scratch qualify axi008c-1"

On the `Ren Qualification` scratch project (2026-10-21, the machine's
own store, 109 recorded timelines, 2,998 recorded changes, 2,234,720
pairs of changes):

    classified: 420,273 independent, 566,778 conflicting, 1,247,669 unproven
    carries over a hand edit (the next change after it): 1,705 pairs
      before: 0 of 1,705 allowed (every one refused)
      after:  653 of 1,705 allowed (provably disjoint), 1,052 refused
              (730 conflicts, 322 unproven)

On one pinned timeline (`fm-scratch qualify axi008c-1`, 11 recorded
changes), the same measurement is small enough to read:

    carries over a hand edit: 4 pairs
      before: 0 of 4 allowed
      after:  3 of 4 allowed, 1 refused (1 conflict)

The three named cases, out of the same corpus:

| case | what the detection says |
|---|---|
| two independent edits | `independent` - merged, both preserved |
| two overlapping edits | `conflict` - the report names both versions |
| a hand edit beside a Ren edit on the same clip | `conflict` - the captain's edit stands, Ren's is refused |

The corpus is the machine's LIVE scratch store, so the counts move as
other lanes work; the pinned timeline is the reproducible one.

## What this is not

- It is not a second edit algebra. The operations, their spans and
  their keys are still `library/tools/edit_algebra.py`'s; this reads
  them, and reads recorded states.
- It does not decide what a hand edit MEANT. The difference between two
  recorded states says what the timeline now holds, not why.
- It does not merge across different timelines, and it does not merge
  two changes that disagree. `merge()` raises, naming both sides.
- A change is not compared with a change it cannot be read against:
  an absent measurement is UNPROVEN, and UNPROVEN is not a merge.

`tests/unit/resolve/test_semantic_conflict.py`.
