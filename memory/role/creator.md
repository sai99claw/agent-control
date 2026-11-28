# Role memory: creator

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

## The ticket is the whole hand-in
- Whoever picks the ticket up has only the ticket. A summary, a caveat or a measured number that lives only in the conversation is lost; write it into the ticket (#29).
- One ticket: symptom, hypotheses (each with how to falsify it; from the second round on, say which ones are already ruled out, #623), design decisions, acceptance as a test plan, mutations that must turn red, out of scope, dependencies.
- Each test-plan item says what behaviour, at which test layer, with what inputs and steps, what observable output, and where the expected value comes from. The expected value never comes from the code under test. If the verifier has to guess, the ticket is wrong, not the verifier. This includes an "optional extra case": name it with its full fixture, checked against the dispatch rules, or leave it out (#63).
- Objective, acceptance and test plan describe the same behaviour three times: scope, numbers, test layer and whether old cases change must agree in all three. Contradictions between them caused about four in ten rebuttals (D-046). Cross-read before marking Ready.
- When a request offers alternative mechanisms, pick the one whose required test can be observed deterministically; write the choice and the reason into the ticket. When it describes a mechanism by analogy ("the same way X does it"), read X's code first: the parts named may not combine, and the ticket must choose one and say why.
- When a request describes a result by its effect rather than by an algorithm, choose the algorithm whose output matches that wording over the generic textbook one, and pin it with fixtures whose expected values differ between the candidates.
- When a new command or stage must be optional, look first for an existing optional one's built-in no-op and mirror it; a new exit code or outcome status for "not declared" costs a branch in every caller.
- When a change makes a script wait (for quota, a slot, a lock), the ticket says which time limit the wait is charged to, what record a stop during the wait leaves, and that nothing shared is held while waiting; these are the points the implementer cannot guess and where waits break.
- Before turning a manual recovery step into a button or a mark, name the process that acts on what it writes and check it is alive at that moment in every run mode; a mark nobody reads is not an action.
- When a ticket makes a script open tickets by itself on a failure, give it a dedupe rule over the still-open tickets of that mechanism from the start: the fix ticket's own landing meets the same failure, and without the rule every landing opens the next ticket.
- When a request moves a decision from run time to creation time, list every run-time rule that relied on the decision still being open (finalizing, idle detection) and give members that can never proceed a way out; reproduce the stall on today's code first, where it shows as a loop that never ends.
- Before a ticket adds a second store of a fact the system already keeps (a stamp, a status, a timestamp), list the existing stores and say which reader uses which and that the old one stays; one fact read from two places drifts.
- When a request adds a key to a shared state file, list every writer of that file and check that each keeps keys it does not know; a writer that rebuilds the file from the parts it knows erases the new key silently, and the loss shows only later as a setting that undid itself.
- When one measurement (an agent run's cost) is attached to several records, choose the attribution explicitly (split, or one owner) and list every reader that sums or counts those rows; a copy per record inflates each reader.
- When a ticket moves code wholesale, give the mapping and mechanical checks in the ticket (a listing diff, a byte-identical comparison of each moved unit, a lost-lines count) and tell the reviewer to run them rather than read the moved bodies; the checks prove more for a fraction of the tokens.

## Writing for the verifier
- Where cases go follows the project's recorded convention when it has one; otherwise the feature's own test file or a new one. Either way the test plan names an exact spot, never "append to the end of" a catch-all file: in a shared module, a gap between two named existing classes, different for each ticket of the hand-in and for the implementer and the verifier of each. Tickets placed at the same spot conflict on every merge.
- The verifier writes its cases before the implementation exists and never sees the implementer's branch. Any seam the cases need (an env var naming a fake command, a function or class name, a flag) must be fixed in the acceptance text, not left to the implementer. A guessed interface is a full rewrite of the case set.
- Every verifier case must fail on the code before the change. An acceptance item that already holds today (a kept fallback, an unchanged precedence, "existing tests still pass") cannot be a verifier case; assign it to the implementer's own tests.
- A change that touches no product code (a test-only fix) has no verifier: no case can be red before it. Put the red-before evidence into the ticket as measured mutations for the implementer to run once by hand.
- A case that checks something is "still shown" must look for text rendered outside the part the ticket removes or hides; otherwise the change deletes the evidence and the case can never pass (#27).
- Permanent cases assert only structure that stays true. A one-time data copy, or content the loop itself keeps rewriting, is checked once in the hand-in; a byte-comparison case on it turns red later.
- Acceptance states what the user sees. DOM structure, style placement, currency and similar details are "per the product's existing convention" unless the ticket is about changing them.
- An "unchanged" item names every value it protects, per code path (which event each path writes, which cells keep their markup); an unnamed one gets filled from the ticket's vocabulary rather than from the code (#50). When a refused action must leave a record "unchanged", name what the comparison covers (which fields and state) and which run records still get written, so the verifier does not compare the whole row (#127). "Other assertions unchanged" is written only after checking the oracle; list the files checked.
- When an acceptance example applies a rule's predicate, check its edge fixtures (a lone member, an empty set of others) against the predicate's literal reading before writing the expected outcome: "all other members are done" is true when there are none (#136).
- When a ticket teaches a classifier to match failure text, that text also appears in the tests' own failing source lines and assertion messages: tell both workers to keep it in fixture constants and out of assertion lines, or a later genuine red is misclassified.
- A mutation you recommend must have been run red yourself, or be marked unmeasured.

## Verify the ticket's premises before opening it (D-033)
- Grep existing tests for every assertion on the behaviour, text or markup the ticket changes: positive literal matches (a cell template, a constant), negative ones (assertNotIn of a tag such as '<form' or '<meta'), and every call of a function whose default changes (a default view, tab, filter or first entry). Each one the change trips is listed in the acceptance item as an old case changed in place; search the test files, not only the test plans you remember (#40, #41, #49).
- When a change widens an action's side effects, the old cases it breaks include tests whose fixture relies on the newly touched state staying as it was, not only tests that assert on that state directly; grep for the fixture's names, not just for the asserted word (#123).
- Before a ticket changes a rule of how the system behaves, look the rule up in the project's recorded decisions, and re-read that record's recent history before handing in: a request can reverse a ratified rule without saying so, and a new ruling can be recorded while you are still working.
- When a request states today's behaviour as its premise ("X only does Y"), check it against the area's acceptance tests before designing: a wrong premise can turn a requested addition into the reversal of a shipped rule, and the ticket must then name the reversal, the old cases it changes and what cancelling it would mean.
- Re-read, right before handing in, the live state of anything the hand-in names as open (a group, a dependency): it can land or close while you work, and a hand-in that names it is refused.
- Reproduce the precondition yourself once, with the project's own end-to-end harness and fake agents in a throwaway test under the test roots of a scratch worktree: seconds of runtime turn "should happen" into a measured fact, confirm the verifier's case is red before the change, and expose cases nobody reported. If it cannot be produced, it is not a precondition; a premise that could not be run is marked not measured.
- When a premise can only be checked on a machine or OS this run cannot reach, build the ticket on a reading that already runs there and fails open (an unknown value changes nothing), and write into the ticket the exact commands that would check it on that machine.
- When a ticket needs an external tool's real output or error texts, take them from the installed tool: grep its binary for the message literals, and provoke once a failure that happens before any costly step (an invalid token, an unknown model). Clearing the environment does not guarantee the failure on a hosted machine, and a call that succeeds spends tokens. Label each text measured or read.
- When asked to find the path that breaks an invariant, reproduce it on today's code first and compare the reporter's date with the date the invariant shipped: the report can predate the fix, and the ticket then hardens the invariant at its only writer instead of hunting a path that no longer exists.
- When a request re-sends the scope of a cancelled ticket, start from that ticket, its run logs and its branches: the earlier failure cause and reusable commits are the cheapest facts to write in. Cherry-pick its cases onto main in a scratch worktree to measure that they still apply and are red.
- Grep the callers of every function, string, DOM id and schema key that will change and list the files that move with them (D-H38). An omission is not blocked, but each one is an avoidable rebuttal.
- Count the golden files and snapshots the change will touch.
- A command cited on the ticket has been run at least once (its help suffices) before it is written down.
- Mark what was measured and what was inferred, separately.
- Before estimating cost, compare earlier estimates with the recorded cost of landed tickets (cache reads included); uncalibrated estimates have run several times low and mislead planning.

## Several tickets in one hand-in
- When tickets of one hand-in edit the same file, give each its own insertion point (a gap between existing classes or sections). When they rewrite one file, let the first ticket lay it out with one stub per later ticket under its own marker comment. When two must edit the same statement (one argparse block, one help line, one row builder), make one depend on the other; parallel edits inside one long function conflict at every merge.
- The same holds for shared fixtures: each ticket's addition to a fake agent goes in its own branch of the fake with a comment on the line, not in the fake's shared docstring.
- Ticket numbers are per pool; a dependency in another repo is written in prose, not in the dependency field (#648).

## Scope of the role
- Do not implement first. Feasibility needs one small piece of evidence, not a trial run. Spending implementer-scale effort on a ticket means you are doing the implementer's job.
- Too complex can be cut into "after this ticket", but the ticket stays complete; never route around the problem with a rename, a second field or parallel shapes (D-018).

## What to trust
- Numbers from sub-workers only when labelled as measured; the repo's design docs and recorded decisions.
- Before reading an incident site, ask whether that file is an intermediate state of a mutation in progress.
- A user's ruling means what its words say. Before reading it more narrowly, trace the narrowed case through everything that depends on it and test it on the commonest configuration, not only on the ruling's own example (#134).

## Revising on a rebuttal
- One round ends in one of two ways: the ticket text is fixed and goes back to the worker, or the choice is the user's and goes up as a framed question (question, options, recommendation). Never patch code or other tickets instead.
- If the worker misread the ticket, the ticket is still the thing to change: write the clarification in so the next reader cannot misread it (D-041).
- When a worker blames the cases, read the cases at the head of the verifier's own branch, not the worker's copy (which may still hold cases already thrown out), and run them on the worker's branch with the proposed fix before accepting it; a diagnosis can be right while its one-line fix is still wrong (#39, #50).
