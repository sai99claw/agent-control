# Project memory

This project's design reasons and invariants: why things are the way they are, and what must stay true.

- Test ids given to the test runner are module ids relative to the unittest roots configured in the board config (e.g. `test_devloop.Class`), never package-qualified (`tests_devloop.test_devloop.Class` fails with ImportError). (#2, #4, #5, #7, #8, #12 verifiers)
- Devloop runs nest: a worker or fixer opens a revision through the ticket creator, and every agent runs the test runner, inside its own run. Any machine-wide limit (a slot, a lock) must exempt runs nested in a holder, or the first nested call deadlocks at a limit of 1.
- The loop test base copies the agent's environment into the loop under test. Every DEVLOOP_* variable that switches behaviour (slot, ticket, group) must be stripped there, or a suite run from inside an agent exercises the nested or inherited path (the fake agent keys its plan and counter files on the inherited ticket) instead of the one under test. Before calling a red yours, rerun with those variables unset. (#3, #4, #11, #13)
- Agents see only the issuer's brief (id, title, objective, acceptance, outline, test_plan, needs_verifier, complexity, effort, notes). Anything an implementer or verifier needs must be in those fields, never only in token_basis, vision or removes.
- hard_block patterns match every added line of a ticket branch, tests and comments included. A ticket whose subject sits near a pattern (process handling, for instance) must tell the implementer and verifier which words to avoid, or it stops for the user before merge.
- Main is cheap to read and expensive to write: every writer of main other than landing (main-session docs, memory commits) sends each integration branch in flight through the main-moved path, a script merge, one of its fix rounds and one more regression.
- The issuer decides at round start whether the verifier runs, but a worker's revision can change the ticket (and clear its cases) mid-round. Anything that gates a merge must be re-read from the ticket after the hand-ins, never trusted from the round start. (#10)
