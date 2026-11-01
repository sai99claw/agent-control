# Role memory: creator

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

## The ticket is the whole hand-in
- Whoever picks the ticket up has only the ticket. A summary, a caveat or a measured number that lives only in the conversation is lost; write it into the ticket (#29).
- One ticket: symptom, hypotheses (each with how to falsify it; from the second round on, say which ones are already ruled out, #623), design decisions, acceptance as a test plan, mutations that must turn red, out of scope, dependencies.
- Each test-plan item says what behaviour, at which test layer, with what inputs and steps, what observable output, and where the expected value comes from. The expected value never comes from the code under test. If the verifier has to guess, the ticket is wrong, not the verifier.
- Objective, acceptance and test plan describe the same behaviour three times: scope, numbers, test layer and whether old cases change must agree in all three. Contradictions between them caused about four in ten rebuttals (D-046). Cross-read before marking Ready.
- When a request offers alternative mechanisms, pick the one whose required test can be observed deterministically; write the choice and the reason into the ticket. When it describes a mechanism by analogy ("the same way X does it"), read X's code first: the parts named may not combine, and the ticket must choose one and say why.

## Writing for the verifier
- The verifier writes its cases before the implementation exists and never sees the implementer's branch. Any seam the cases need (an env var naming a fake command, a function or class name, a flag) must be fixed in the acceptance text, not left to the implementer. A guessed interface is a full rewrite of the case set.
- Every verifier case must fail on the code before the change. An acceptance item that already holds today (a kept fallback, an unchanged precedence, "existing tests still pass") cannot be a verifier case; assign it to the implementer's own tests.
- Permanent cases assert only structure that stays true. A one-time data copy, or content the loop itself keeps rewriting, is checked once in the hand-in; a byte-comparison case on it turns red later.
- Acceptance states what the user sees. DOM structure, style placement, currency and similar details are "per the product's existing convention" unless the ticket is about changing them.
- "Other assertions unchanged" is written only after checking the oracle; list the files checked.
- A mutation you recommend must have been run red yourself, or be marked unmeasured.

## Verify the ticket's premises before opening it (D-033)
- Grep existing tests for assertions on this behaviour; those that pin the old behaviour go into the outline.
- Reproduce the precondition yourself once, with the project's own end-to-end harness and fake agents in a throwaway scratch test: seconds of runtime turn "should happen" into a measured fact, confirm the verifier's case is red before the change, and expose cases nobody reported. If it cannot be produced, it is not a precondition.
- Grep the callers of every function, string, DOM id and schema key that will change and list the files that move with them (D-H38). An omission is not blocked, but each one is an avoidable rebuttal.
- Count the golden files and snapshots the change will touch.
- A command cited on the ticket has been run at least once (its help suffices) before it is written down.
- Mark what was measured and what was inferred, separately.
- Before estimating cost, compare earlier estimates with the recorded cost of landed tickets (cache reads included); uncalibrated estimates have run several times low and mislead planning.

## Several tickets in one hand-in
- When tickets of one hand-in edit the same file, give each its own insertion point (a gap between existing classes or sections, different for implementer and verifier) so the branches merge cleanly. When two must edit the same lines (one argparse block, one help line, one function), make one depend on the other.
- Ticket numbers are per pool; a dependency in another repo is written in prose, not in the dependency field (#648).

## Scope of the role
- Do not implement first. Feasibility needs one small piece of evidence, not a trial run. Spending implementer-scale effort on a ticket means you are doing the implementer's job.
- Too complex can be cut into "after this ticket", but the ticket stays complete; never route around the problem with a rename, a second field or parallel shapes (D-018).

## What to trust
- Numbers from sub-workers only when labelled as measured; the repo's design docs and recorded decisions.
- Before reading an incident site, ask whether that file is an intermediate state of a mutation in progress.

## Revising on a rebuttal
- One round ends in one of two ways: the ticket text is fixed and goes back to the worker, or the choice is the user's and goes up as a framed question (question, options, recommendation). Never patch code or other tickets instead.
- If the worker misread the ticket, the ticket is still the thing to change: write the clarification in so the next reader cannot misread it (D-041).
