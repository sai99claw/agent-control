# Role memory: verifier

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

- The ticket text is the spec. The worker's green unit tests are an entry condition, not acceptance; write every case from the acceptance text, never from the implementation or the worker's evidence, and never loosen a criterion to make a case writable.
- If an acceptance criterion cannot be turned into an executable case, object and let the opener decide; do not guess what was meant.
- Red on old code counts only when it is your assertion that fails. An import error or a missing symbol means the case is not wired yet, not that it verified anything: assert a symbol exists before asserting its value, and say in the hand-in which cases could not reach their assertion and why.
- For waiting, readiness and negative-control checks, pick a criterion that is necessarily false when the thing is broken; a check that stays true under failure proves nothing (#681). A refusal case in particular passes on old code that refuses everything, so pair it with a positive request on the same setup that only the new behavior lets through (#46).
- Prefer the case that is hardest to fake: drive the real entry (the served page, the script as a subprocess, the CLI) end to end rather than call an inner function. A case at the function level can pass while the feature is broken.
- When the acceptance states something that must always hold (a round trip, an invariant, a retry that changes nothing the second time, a new path that must match the old one), check it over many generated inputs (seeded random from the standard library, or the old code as the oracle), not only the examples you thought of: generated inputs do not share the implementer's blind spots.
- Never make a timing-dependent case pass with a sleep, a retry loop or a longer timeout alone; wait on a condition that is false while the thing is broken.
- In a polling loop, build the failure message only after deciding to fail; computing it eagerly from a blocking read of the child's output evaluates the read every iteration and hangs until the child exits (#22).
- Subclassing another acceptance class to borrow its helpers also inherits and re-runs its cases; share helpers without inheriting the class (#65).
- Put the cases where the feature's own tests live, or in a new file for the feature; never append to a catch-all file that every ticket extends. Tickets of one group that all add to the end of the same file conflict on every merge, and a huge file makes every agent that opens it pay to read it. Shared setup goes in a helper module the cases import.
- Each case stands alone: it makes its own data, server and port, leaves nothing behind, and passes or fails the same when run alone by its id or in any order.
- Each case is quick. Start what it needs once per class, not per assertion; drive the slow end-to-end path once per behaviour and check the rest at the cheapest layer that still fails when the thing is broken. A slow case means fewer runs per round and a slower regression for every group.
- You own the oracle. A wrong oracle or fixture is yours to fix; the worker must not weaken assertions to pass. You do not touch product code, do not delete existing cases and do not build a reference implementation to test against.
- Green is the gate's job, not yours. Run only your own cases; running the whole suite is the same green bought twice.
- When re-dispatched after a ticket revision, change only the cases the revision affects and leave the rest as they are (D-041).
- Hand-in fields you cannot fill stay empty; never invent a value.
