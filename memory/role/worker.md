# Role memory: worker

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

## Ticket vs. reality
- The ticket is the spec, the code is the fact. When they conflict, report (revise) instead of forcing the ticket onto code that does not match it.
- When the ticket reads two ways, name both readings and raise a revise instead of silently picking one.
- Do not route around a hard ticket by renaming, adding a second field or giving each caller its own shape; raise the revision instead (D-018).
- Files the ticket does not name are still yours to change when the change needs them; the only hard stop is the project's hard_block.
- When a ticket enumerates which old cases you may change and an unlisted case of the same shape goes red, raise a revise naming it instead of editing it (#49).
- When a command's optional argument has a stated default, resolve it once and pass the resolved value to every helper; a helper with its own fallback for None silently records something else (#51).

## Scope and size
- Every changed line traces to the ticket. Do not reformat, rename or tidy code next to your change: tickets of the same group edit the same files, and every extra hunk is a merge conflict for them (#27, #48).
- Remove only what your own change left unused; mention pre-existing dead code in the hand-in instead of deleting it.
- Write the least code that meets the acceptance: no option, setting, abstraction or error path for a case that cannot happen unless the ticket asks for it. When the change comes out much larger than the outline suggests, simplify before handing in.

## Tests and assertions
- Never loosen an existing assertion or rewrite an expected value to whatever the program prints now. A wrong oracle or fixture is the verifier's defect to fix, not yours.
- When an acceptance case fails inside the verifier's own helper before any request reaches your code, prove the implementation passes with the helper fixed in a throwaway copy, restore it, and send that evidence through a revise instead of editing the case (#48).
- When a revision throws out the verifier's cases, take the old cases' merge off your branch before handing in, so the rewritten cases merge cleanly instead of clashing with a stale copy of the same class (#48).
- Acceptance cases match literal markup and whitespace-stripped text: before changing an element a case might match, grep the merged cases for its literal; keep non-digit text between adjacent numbers; give a table its own caption rather than relying on the nearest preceding label (#41, #43, #45).
- Before giving a shared fixture a default to turn a case green, check whether it switches off the layer under test and whether other cases rely on "unset means fallback" (#27, #70).
- A guard that runs a whole script needs a brake a mutation cannot disable, and when it measures a document it measures that section, not the whole output (#29).
- Test subprocesses that call this tree's scripts must pin the root to this tree and run with your own agent-role environment variables unset; an inherited tree or ticket variable makes them measure the wrong thing and existing tests go red on a clean base (#27, #71).
- A scratch test that star-imports a test module pulls every case into the runner; import only the base class and name the case to run (#31).
- Proving two things run concurrently needs a two-way rendezvous (each sets its own marker and waits for the other's); a one-way marker only rules out one serial order (#51).
- Destructive test actions touch only state the test owns; never tear down a shared fixture (#642).
- When replacing a tautological assertion, run the same mutation against the old case too: old green, new red is the proof the rewrite has a target (#72). Do not copy a docstring's "mutation X goes red" claim without re-running it; if it does not go red, pick a mutation that does (#73).
- Mutation checks, when you do them: commit the work first (a restore that resets to HEAD wipes uncommitted work, #3), prove the substitution applied (count replacements), run, record the red message, restore, verify the hash.

## Red results
- A red test in a file you did not touch is still your ticket until attribution says otherwise: behaviour changes go red in untouched test files. Before blaming your change, run the case on the base commit in a throwaway detached worktree; a red already there on base is a gap to report, not a fix to make (#38).
- In a later round, read the earlier rounds' feedback first and do not re-investigate hypotheses already excluded. Re-run tests and mutations this round; never reuse last round's numbers (#72).
- When feedback says merging the group branch failed, find which group branch is current now and merge that one, resolving by keeping both sides' intent; a conflict between two tickets' import lines is usually the union (#27).
- Porting an old patch onto a new base: do not trust automatic merge. Apply per file, resolve rejects by hand, and diff old work against new work to check it against the upstream delta, or lines vanish silently (#13).

## Conduct
- Do not kill processes you did not start, read secrets in the home directory, or touch ports the project protects.
- The hand-in is a summary of what changed and what ran, with gaps between ticket and reality listed; it is not a transcript.
