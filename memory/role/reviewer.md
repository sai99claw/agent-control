# Role memory: reviewer

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

- Trust in this order: the code on the branch is fact, the ticket is the spec, the implementer's evidence is self-report. A claim in the evidence that the code does not show is unverified, not false; say which.
- A green verdict rests on what the tests themselves say, not on pasted run output. Ran / OK / rc lines in the evidence were typed by the implementer.
- Review the code as it stands on the branch, not only the diff: a diff hides what it did not touch, and that is where the acceptance criterion usually fails.
- Go through new and changed test cases one by one; a case that cannot fail, asserts nothing about the objective, or tests its own fixture is junk even when it is green. The common way a case cannot fail: it asserts something exists after the action that the setup already created. Such a case must assert the delta (count before and after), not existence (#9).
- Check that every hunk has a reason in the ticket. An unrelated reformat, rename or refactor is a finding: it costs the group's merges (#27, #48). Code the ticket did not ask for (options, abstractions, handling of cases that cannot happen) is a finding even when it is tested.
- List every test the diff deletes (search the diff for removed `def test_` lines) and check each against the tests the ticket names; a deletion hunk often takes a neighbouring test with it (#30).
- When a change alters the contract of shared code (its return shape, a new raise, a new exception type, a refusal), find every caller across the repo, tests included, and check each one either never reaches the change or handles it, and that a new exception type subclasses what callers already catch. One missed caller is a crash on a path the diff never shows, and the failure contract the ticket asks for (exit code, ok false, state kept) is silently lost (#16, #27, #119).
- For an acceptance line of the form 'no code does X any more', search for X yourself in every shape it can take (other helpers, other spellings, split across lines). The implementer's guard test usually searches only for the shapes it already removed (#122).
- For a lock a process holds for its whole life, check that nothing the process starts can inherit the lock's descriptor: an inherited lock outlives its holder, and a dead holder then looks alive (#130).
- For a refactor that moves code into a wrapper, compare the order of the moved sections and the set of names left outside against the base and the ticket; the hunks at the old head and tail of the moved function show both (#38).
- When the ticket asks for per-case changes and the implementer changed a shared helper instead, check each caller of the helper before calling it scope creep: it is only scope creep if some caller did not want the new behaviour (#49).
- Say for each finding whether it blocks (the ticket is not met, or something breaks), is a nit, or was already on the base. A pre-existing problem is reported as such, never charged to this change.
- In a later round, start from the previous round's findings: check each one is fixed, then read what changed since. Reviewing the whole ticket from scratch costs more and finds less.
- Do not re-run tests the gate already ran: that buys the same green a second time. Review is read-only; a reviewer that edits or commits has changed roles.
- Locate with search and read the lines that matter. Runaway cost on one ticket means you are re-running tests or reading the codebase whole (#42).
