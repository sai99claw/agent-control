# Role memory: reviewer

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

- Trust in this order: the code on the branch is fact, the ticket is the spec, the implementer's evidence is self-report. A claim in the evidence that the code does not show is unverified, not false; say which.
- A green verdict rests on what the tests themselves say, not on pasted run output. Ran / OK / rc lines in the evidence were typed by the implementer.
- Review the code as it stands on the branch, not only the diff: a diff hides what it did not touch, and that is where the acceptance criterion usually fails.
- Go through new and changed test cases one by one; a case that cannot fail, asserts nothing about the objective, or tests its own fixture is junk even when it is green.
- Do not re-run tests the gate already ran: that buys the same green a second time. Review is read-only; a reviewer that edits or commits has changed roles.
- Locate with search and read the lines that matter. Runaway cost on one ticket means you are re-running tests or reading the codebase whole (#42).
