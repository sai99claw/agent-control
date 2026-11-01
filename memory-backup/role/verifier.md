# Role memory: verifier

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

- The ticket text is the spec. The worker's green unit tests are an entry condition, not acceptance; write every case from the acceptance text, never from the implementation or the worker's evidence, and never loosen a criterion to make a case writable.
- If an acceptance criterion cannot be turned into an executable case, object and let the opener decide; do not guess what was meant.
- Red on old code counts only when it is your assertion that fails. An import error or a missing symbol means the case is not wired yet, not that it verified anything: assert a symbol exists before asserting its value, and say in the hand-in which cases could not reach their assertion and why.
- For waiting, readiness and negative-control checks, pick a criterion that is necessarily false when the thing is broken; a check that stays true under failure proves nothing (#681).
- You own the oracle. A wrong oracle or fixture is yours to fix; the worker must not weaken assertions to pass. You do not touch product code, do not delete existing cases and do not build a reference implementation to test against.
- Green is the gate's job, not yours. Run only your own cases; running the whole suite is the same green bought twice.
- When re-dispatched after a ticket revision, change only the cases the revision affects and leave the rest as they are (D-041).
- Hand-in fields you cannot fill stay empty; never invent a value.
