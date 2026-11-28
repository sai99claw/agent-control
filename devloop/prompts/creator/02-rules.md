# Rules for a ticket

- **One ticket = one purpose you can state in one sentence.** If saying what the ticket is for
  needs "and", it is two tickets. That sentence is the `objective`.
- `objective`: the one sentence: what is true when it is done.
- `token_estimate`: your estimate of the tokens the implementer and verifier together will spend
  on it (a whole number, e.g. 400000). Say how you got it in `token_basis` (files to read, size of
  the change, tests to write). It is an estimate for planning, not a limit.
- `acceptance`: a list of checkable statements. Each one must be something a test can assert.
- `outline`: where in the code the change goes, as far as you know it. A suggestion, not a fence.
- `test_plan`: which acceptance items the verifier writes cases for (the verifier owns the cases
  for every acceptance item unless you say the implementer does).
- Acceptance and test_plan never ask the worker or the verifier to run the full suite: the full suite is the regression's.
  The worker runs only the verifier's cases, its own unit tests and the base set.
  Every check you give the worker or the verifier must finish well within one agent call (see "Time limits" below).
- `needs_verifier`: true unless the change is so small that the implementer's own tests are enough.
- `complexity`: `"simple"` or `"complex"`. A complex ticket is reviewed once it is done (someone
  reads the code against the ticket); a simple ticket goes to a cheaper implementer model.
- `effort` (optional): `"low"`, `"medium"`, `"high"`, `"xhigh"` or `"max"` - only when this ticket
  needs a different reasoning effort from the implementer's default.

# Rules for the DAG

Every ticket gets a DAG entry with two fields:

- `depends_on`: tickets whose code this one needs. Existing ids or the placeholders of tickets
  you open in this hand-in.
- `group`: the group this ticket joins (see `## Groups`). An open group whose tickets are logically
  closest to this one or least likely to conflict with it; when no open group fits, a new group
  `"new-g1"` (`"new-g2"`, ... for more than one; tickets naming the same placeholder share one new
  group, made at the end of the chain). The DAG orders the groups: a group comes after every
  group holding a ticket its own tickets depend on, so a choice must never make the group order a
  cycle. Say in the ticket's `notes` why this group. A fix ticket the controller asked for (a request that starts with "Flaky cases in group" or "Release red at landing") goes into the earliest open group in the chain whose tickets it does not conflict with; open a new group for it only when none fits, since every group costs one full regression.
