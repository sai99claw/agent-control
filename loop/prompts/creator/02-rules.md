# Rules for a ticket

- **One ticket = one session of work.** If an implementer cannot finish it, test it and hand it
  in within one sitting, split it. A ticket that is too big gets stuck after three rounds.
- `objective`: what is true when it is done, in one or two sentences.
- `acceptance`: a list of checkable statements. Each one must be something a test can assert.
- `outline`: where in the code the change goes, as far as you know it. A suggestion, not a fence.
- `test_plan`: which acceptance items the verifier writes cases for (the verifier owns the cases
  for every acceptance item unless you say the implementer does).
- `needs_verifier`: true unless the change is so small that the implementer's own tests are enough.
- `needs_review`: true only for a complex change where someone should read the code against the
  ticket once it is done.

# Rules for the DAG

Every ticket gets a DAG entry:

- `depends_on`: tickets whose code this one needs. Existing ids or the placeholders of tickets
  you open in this hand-in.
- `verify`: `"together"` (the default) means this ticket may be verified together with others
  in one integration branch. Use `"alone"` only for a systemic change (for example: replacing how
  the whole project builds, lands or stores data), and say why in `why_alone`.
