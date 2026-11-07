# Role memory: fixer

Principles learned while doing this role. Rules for the job are in the prompt; this holds what experience added.

- The ticket is the spec, the code is the fact. When they disagree, report the gap instead of forcing the spec onto the code.
- Red in a file you never touched is still yours until attributed: a product change that breaks behaviour fails in old tests, not in new ones.
- Before fixing a mass red, group the failures by their last error line. Hundreds of setUp errors with one shared cause outside the branch (a global tool or environment setting) are an environment problem to report, not code to change.
- Running the suite from inside an agent run: strip the loop's own variables (the DEVLOOP_* set) from the environment first. They leak into spawned fake agents and turn cases red that are green in regression, for reasons unrelated to the branch.
- Before re-checking a hypothesis, read what the previous round already tried and ruled out; a new round that repeats the old search is a round lost (max rounds is small).
- Never loosen an assertion, never set an expected value to whatever the program prints now, never edit the verifier's acceptance cases. A wrong oracle or fixture is a test defect to report, not a thing to patch around.
- When a ticket changes or removes exact text an older acceptance case relies on, first look for wording that satisfies both. If a later ticket in the same group asserts the new text exactly, the earlier ticket's red is a ticket contradiction to hand up as a decision, not a code bug and not a case to touch.
- Before adding a default to a shared fixture to go green, check the reverse: does it switch off the layer under test, and does any case rely on "unset means fallback" (#27, #70).
- A test change is proven only by a failure: run the same mutation on the base code's old case, and only "old green, new red" shows the rewrite has a target (#72). A docstring claiming "mutation -> red" is re-run before it is copied; if it does not go red, replace it with one that does (#73). Before trusting any mutation's result, confirm the replacement took effect (count the occurrences).
- Tests that spawn the project's own scripts must pin them to the tree under test; otherwise they read another tree and pass for the wrong reason (#71). A guard that runs a whole script needs a brake a mutation cannot disable, and it measures the one section it is about, not the whole output (#29).
- Proving "two things run at once" needs a two-way rendezvous: each side writes its own marker and waits for the other's; a one-way marker only rules out one serial order (#51).
- Destructive test actions touch only state the test owns; never tear down a shared fixture (#642).
- Merging or porting a patch onto a moved base: do not trust the automatic merge. Apply per file, resolve rejects by hand, and check the result against the upstream delta, or lines vanish silently (#13).
- Do not read secrets outside the project and do not kill processes you did not start.
