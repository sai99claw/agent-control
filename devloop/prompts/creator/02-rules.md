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
- `needs_verifier`: true unless the change is so small that the implementer's own tests are enough.
- `complexity`: `"simple"` or `"complex"`. A complex ticket is reviewed once it is done (someone
  reads the code against the ticket); a simple ticket goes to a cheaper implementer model.
- `effort` (optional): `"low"`, `"medium"`, `"high"`, `"xhigh"` or `"max"` - only when this ticket
  needs a different reasoning effort from the implementer's default.

# Rules for the DAG

Every ticket gets a DAG entry:

- `depends_on`: tickets whose code this one needs. Existing ids or the placeholders of tickets
  you open in this hand-in.
- `verify`: `"together"` (the default) means this ticket may be verified together with others
  in one integration branch. Use `"alone"` only for a systemic change (for example: replacing how
  the whole project builds, lands or stores data), and say why in `why_alone`.
