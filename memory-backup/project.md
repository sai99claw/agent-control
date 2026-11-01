# Project memory

This project's design reasons and invariants: why things are the way they are, and what must stay true.

- Test ids given to the test runner are module ids relative to the unittest roots configured in the board config (e.g. `test_devloop.Class`), never package-qualified (`tests.test_devloop.Class` fails with ImportError). (#2, #5, #7 verifiers)
- Devloop runs nest: a worker or fixer opens a revision through the ticket creator, and every agent runs the test runner, inside its own run. Any machine-wide limit (a slot, a lock) must exempt runs nested in a holder, or the first nested call deadlocks at a limit of 1.
- Agents see only the issuer's brief (id, title, objective, acceptance, outline, test_plan, needs_verifier, complexity, effort, notes). Anything an implementer or verifier needs must be in those fields, never only in token_basis, vision or removes.
