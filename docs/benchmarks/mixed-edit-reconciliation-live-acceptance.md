# Mixed human and Ren edit reconciliation: live acceptance recipe

This recipe is intentionally written but not run. Run it only after the
offline contract `tests/contracts/test_timeline_reconciliation.py` passes
and only in an authorized Resolve qualification window.

## Safety envelope

- Use only the exact Resolve project `Ren Qualification`.
- All Resolve access is a broker job that borrows the project through
  `qualification_project.borrowed()` and reads the project name back before
  acting. Never connect to Resolve from the test process.
- Do not submit the job while the broker is busy or a lease is held. Never
  start, stop, or restart the shared broker.
- Use a unique disposable timeline and media created for this run. Do not
  touch an existing timeline. Do not use `--accept-editor-changes` or
  `--allow-drop`.
- Keep the Ren proposed patch unsubmitted until the expected patch or
  creator question has been inspected. Cleanup removes only the disposable
  timeline created by this run and verifies that the original current
  timeline is restored.

## Procedure

1. Through one broker qualification session, borrow `Ren Qualification`,
   verify the exact project name, capture the current timeline identity, and
   create a uniquely named scratch timeline from qualification media. Record
   its initial shadow generation as the approved base and save a read-back
   snapshot.
2. End that lease. In a later broker job, borrow the same project, select
   only the scratch timeline, move one test clip and trim another. Read the
   timeline back and record the change as an `observed` shadow generation.
   Restore the user's original current timeline before returning from the
   borrow.
3. Offline, call `timeline_reconciliation.reconcile_generations` with the
   approved generation and a proposed Ren transform on a third clip. Confirm
   the external generation is attributed, both hand edits are unchanged in
   the latest snapshot, and the returned patch is based on the current head.
4. On a fresh fixture timeline, have the human and Ren proposal both move the
   same clip. Confirm the observed position remains in the current snapshot,
   the Ren operation appears only in the uncommitted `Ren proposal` variant,
   and the exact creator question is returned. Do not apply that variant.
5. Re-read the scratch timeline through the broker and compare it to the
   recorded current generation. If a patch is approved for a non-conflicting
   operation, submit only that returned patch through the broker's
   `timeline.apply_patch` job with its current generation. Read back the
   receipt and prove the human move and trim are still present.
6. Delete the scratch timeline through the broker, restore and read back the
   original current timeline, and verify no other timeline changed. Save the
   before/after snapshots and broker receipts with the qualification run.

Acceptance requires exact agreement between the shadow generations, the
returned attribution, the patch/question output, and the broker read-back.
Any generation mismatch or unexpected timeline change ends the run without
submitting a patch.
