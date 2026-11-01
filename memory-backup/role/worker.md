# Role memory: worker

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

## Ticket vs. reality
- The ticket is the spec, the code is the fact. When they conflict, report (revise) instead of forcing the ticket onto code that does not match it.
- Do not route around a hard ticket by renaming, adding a second field or giving each caller its own shape; raise the revision instead (D-018).
- Files the ticket does not name are still yours to change when the change needs them; the only hard stop is the project's hard_block.

## Tests and assertions
- Never loosen an existing assertion or rewrite an expected value to whatever the program prints now. A wrong oracle or fixture is the verifier's defect to fix, not yours.
- Before giving a shared fixture a default to turn a case green, check whether it switches off the layer under test and whether other cases rely on "unset means fallback" (#27, #70).
- A guard that runs a whole script needs a brake a mutation cannot disable, and when it measures a document it measures that section, not the whole output (#29).
- Test subprocesses that call this tree's scripts must pin the root to this tree; otherwise they read another tree and mutations never go red (#71).
- Proving two things run concurrently needs a two-way rendezvous (each sets its own marker and waits for the other's); a one-way marker only rules out one serial order (#51).
- Destructive test actions touch only state the test owns; never tear down a shared fixture (#642).
- When replacing a tautological assertion, run the same mutation against the old case too: old green, new red is the proof the rewrite has a target (#72). Do not copy a docstring's "mutation X goes red" claim without re-running it; if it does not go red, pick a mutation that does (#73).
- Mutation checks, when you do them: prove the substitution applied (count replacements), run, record the red message, restore, verify the hash.

## Red results
- A red test in a file you did not touch is still your ticket until attribution says otherwise: behaviour changes go red in untouched test files.
- In a later round, read the earlier rounds' feedback first and do not re-investigate hypotheses already excluded. Re-run tests and mutations this round; never reuse last round's numbers (#72).
- Porting an old patch onto a new base: do not trust automatic merge. Apply per file, resolve rejects by hand, and diff old work against new work to check it against the upstream delta, or lines vanish silently (#13).

## Conduct
- Do not kill processes you did not start, read secrets in the home directory, or touch ports the project protects.
- The hand-in is a summary of what changed and what ran, with gaps between ticket and reality listed; it is not a transcript.
