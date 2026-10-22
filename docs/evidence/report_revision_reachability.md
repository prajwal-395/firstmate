# The report that named a revision nobody had

Test: `tests/contracts/test_report_provenance_contract.py` -
`test_every_committed_report_names_a_revision_the_repository_has`.

Two committed reports named a revision that the branch they landed on
does not carry: the AXI qualification registry named the tip of the
measuring lane's branch, and the finished-video benchmark named the
tip of another. The lane measured on its branch and the merge was a
squash, so the branch's commits never became part of the branch the
report landed on - and the report claimed to have been produced from a
revision a reader of `main` cannot reach.

The check that should have caught it asked the wrong question. It
asked whether the object was in the git OBJECT DATABASE, and the
answer depends on which refs the clone happens to hold: the machine
that measured kept the lane's branch, and the machine that ran the
suite did not. The same `main` read green in one place and red in
another, which is why PR 3143's "fixed" was never fixed.

**What it cost.** A reader of the branch could not check the revision
a committed report claimed, and the gate that says so passed on the
machine that produced the report and failed on the machine that read
it - so the same commit was green and red depending on where it ran.

**The rule.** A committed report names a revision the LANDING BRANCH
carries, and the gate asks for REACHABILITY (`git merge-base
--is-ancestor`), not presence in the object database. A lane that
measures on a branch and lands a report names the revision the landing
branch carries - the one a reader can check the file against
(`report_provenance.commit_for_path` is that revision's own reading).

**The same repair, further on.** PR #3164 rewrote three committed
reports and the registry, and the rewrite dropped the provenance and
the trail: the registry carried no provenance at all, the
ordinary-request measurement carried none, and the E2E workflow
assessment was replaced IN PLACE by a copy of the 2026-10-20
measurement - the very replacement the contract exists to refuse
("Newer does not mean superseding"). The registry carries its
provenance again, the ordinary-request measurement carries one that
names what it measured (4 of 10, the six failures being rows whose
fixture could not be seeded), and the assessment is restored to the
file that held it, so both measurements stand side by side. Nothing is
lost: the measurement that replaced the assessment is the 2026-10-20
one, and that file still holds it. The registry's trail now lives ON
each row - every routing row and every retained row names the report
its evidence came from - because the `imported` list named the import
BEFORE the one that produced the row, and so was history rather than
the row's own story.
