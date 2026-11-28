# Project memory

This project's design reasons and invariants: why things are the way they are, and what must stay true.

## Tests and fixtures

- Test ids given to the test runner are module ids relative to the unittest roots configured in the board config (e.g. `test_issuer.Class`), never package-qualified (`tests_devloop.test_issuer.Class` reports one ImportError row instead of the cases). (rediscovered by verifiers on many tickets, #2 through #141)
- Of the unittest settings in the board config, only the base ids and the full suite feed devloop's own runs: a dead base id turns every worker's base run red, while the id map serves only the manual test selection and changes no run.
- The loop test base copies the agent's environment into the loop under test. Every DEVLOOP_* variable that switches behaviour (role, result, slot, ticket, group) must be stripped there, or a suite run from inside an agent exercises the nested or inherited path (the fake agent keys its plan and counter files on the inherited ticket; the ticket creator's revise cases go red on base) instead of the one under test. Before calling a red yours, rerun with those variables unset. (#3, #4, #11, #13, #137, g14 fix)
- The fake test command used by the loop tests answers a `run` only for case ids. A feature that reruns ids reported by the full suite or another stage must extend the fake's `run`, or that rerun is unmeasured in every loop test.
- With the fake commands a stage lasts about a tenth of a second, shorter than any reasonable poll interval. A loop test that must see a state lasting one fake step reads it from inside that step (a declared test command that copies the record), never by sampling from outside. (#108)
- The fake worker merges the cases branch at its start and commits whatever the merge leaves, conflict markers included: a change that moves the cases branch onto newer target content can turn a loop test's expected conflict into a clean merge with markers.
- The loop test harness writes a ticket with no group and passes unknown keyword arguments through as plain ticket fields, so naming a group there creates no membership: a fixture that needs a group writes the group record itself. The controller cancels a group with no members and no job, so a fixture group in any state needs a member ticket whose group field names it. (#118)
- A group with no `created` stamp sorts before every stamped group: a fixture that writes a group raw next to controller-made groups sets `created`, or the chain order quietly flips. (#122, g7 fix)
- The chain functions are also called with hand-built stand-in snapshots in several test modules: a new attribute a chain reader needs is read with a fallback, or every stand-in fixture breaks. (g11 fix)
- A liveness stamp a test fakes with a process must not be a child the test leaves unreaped: an ended child is a zombie that the liveness check in another process still reports alive, so whatever waits on it never sees it end. Start such a process detached.
- Tests and the pool inherit the machine's global git config, including commit signing through an external program; when that signer fails under load every git commit exits 128, which is an environment failure, not the branch's. (g9 fix)
- The full suite reads documentation and memory files too: guards scan every tracked file (absolute home paths, which memory files git tracks) and README cases match table rows by prefix and adjacency. A docs-only or memory-only change can turn regression red; "paths the tests never read" is a claim to check, not a given. (g9 fix)
- A guard forbids the earlier project's name in every non-markdown file, code, tests, fixtures and comments included. A ticket built from that project's material must tell the implementer and verifier to refer to it generically.
- Inbox page names, their `at` and revise run dirs are stamped to the second: several requests or revisions within one second overwrite each other's page or reuse a run dir (give each such run its own test method), and sorting pages by `at` does not find the newest - snapshot the inbox keys before the action and take the keys that are new. (#106)
- The served board caches the pool snapshot for a few seconds and drops it only on a POST; a server test writes every pool state its GETs read before starting the server, or starts a new server after writing. (#53)
- Code that measures the worktree root (disk usage, statvfs) must tolerate a root that does not exist yet: the controller creates it later, so in tests and on first run it is absent.

## Tickets and what judges them

- Agents see only the issuer's brief (id, title, objective, acceptance, outline, test_plan, needs_verifier, complexity, effort, notes). Anything an implementer or verifier needs must be in those fields, never only in token_basis, vision or removes.
- hard_block patterns match every added line of a ticket branch, tests and comments included. A ticket whose subject sits near a pattern (process handling, for instance) must tell the implementer and verifier which words to avoid, or it stops for the user before merge.
- What judges a change sits where the change cannot edit it: board/config.json (tests, hard_block) is read from the project root, not from the branch under test. A ticket that wants a new gate or rule has it take effect only once it has landed.
- The test interface runs the adapter from the main checkout, not from the tree under test: a change to the adapter or test interface governs red checks, case runs and regressions only once it has landed there, and its own cases' red check runs on the old adapter.
- Main is cheap to read and expensive to write: every writer of main other than landing (main-session docs, memory commits) sends each integration branch in flight through the main-moved path, a script merge, one of its fix rounds and one more regression.

## Issuer, rounds and revisions

- The issuer decides at round start whether the verifier runs, but a worker's revision can change the ticket (and clear its cases) mid-round. Anything that gates a merge must be re-read from the ticket after the hand-ins, never trusted from the round start. (#10)
- After a cases_wrong revision the next worker round starts while the verifier is still rewriting, and the old cases are still merged in the ticket branch; the worker sees the old failing case until the rewrite lands on the cases branch. (#50)
- A ticket branch that has merged its target cannot be moved by rebasing onto a new base: rebase drops the merge commit but replays every commit it brought in. The ticket's own commits are the first-parent, non-merge commits not reachable from its recorded base, its targets or its old cases head. (measured 2026-11-24)
- The issuer applies every revision not yet applied except those whose recorded source is the user: an answer from any new source (main session, board) must be recorded under that source or added to that skip, or the next issuer applies the same answer a second time.
- Every revision run records the opener's cost row on the ticket before its hand-in is applied, so "the ticket is unchanged" after a refused revision always means everything except history and cost. (#59, #127)
- The issuer writes no page when it stops a ticket for reason `decision`, because the escalating revise is expected to have written it. Any path that stops a ticket without going through revise (a worker handing in `blocked`) must write the page itself, or the user never hears of it.
- The agent CLI's JSON output is one result object whose result text is the agent's last message when `is_error` is false and the CLI's own error when it is true, and that text sits far into the line, after the usage figures. Anything that classifies agent logs reads `is_error` before any text. (measured 2026-11-28)
- Who acted is recorded only when the caller says so (`--by`); nothing infers it. A feature that must attribute every action passes the actor explicitly, or the history has no actor.

## Controller, groups and chain

- A ticket whose issuer is alive stays `running` whatever it waits for: the controller counts running tickets for max_parallel, keeps a group open while a member runs, and looks for dead issuers only among running tickets. A wait (quota, slot) is a mark on the ticket, never a new state.
- One ticket has one issuer. Two issuers share its worktree and branches: when one merges, its cleanup removes them under the other, whose next agent start fails and whose stale `stuck` then overwrites the merged or landed ticket. Nothing returns a running ticket to ready while its issuer lives. (measured 2026-11-24)
- A controller's jobs are its child processes until it exits. Stopping it by process tree also ends every regression, fixer and issuer it started (jobs an earlier controller started, since reparented, survive); a signal to the controller alone leaves them all running, and the next controller adopts them. Anything that stops or restarts a controller chooses between the two on purpose. (measured 2026-11-24)
- A controller judges its own children as child processes, but issuers it adopts only by the ticket's run stamp (pid, host, boot id). Liveness bugs therefore show only after a restart: a reproduction stops the controller alone and starts a new one.
- The controller's idle detection counts groups by state. Any new condition that makes a group wait (a gate, a chain, a member that cannot start) must be left out of what counts as busy, or a controller without --forever never ends.
- An open group holds the chain: any new rule that keeps a group open also stops every later group from landing. Check the chain effect before widening what keeps a group open. (#134)
- Group membership follows dependencies: a `together` ticket whose dependency is merged in an open group joins that group, and a ready ticket whose dependency is merged in a group that is not open (pr or stuck) waits until that group lands. A fix that a stuck group itself needs must not depend on that group's tickets; a fixture that wants a ready dependent of a merged ticket gives it `verify="alone"`. (#63)
- That dependency rule is written twice: dispatch places a ticket through its own group lookup, which repeats the rule the controller's joinable check applies when advancing groups. A rule about who may join a group must hold in both.
- The auto fixer keeps its fix worktree, with the integration branch checked out, between rounds. Anything that moves that branch with update-ref (controller merges, a reset) leaves the worktree stale, and the next fixer's `git add -A` commit silently reverts the move unless the round start syncs the worktree to the branch head.

## Pool, worktrees and machine limits

- Devloop runs nest: a worker or fixer opens a revision through the ticket creator, and every agent runs the test runner, inside its own run. Any machine-wide limit (a slot, a lock) must exempt runs nested in a holder, or the first nested call deadlocks at a limit of 1.
- Adding, removing and pruning git worktrees are not safe to run concurrently on one repository (a prune deletes another process's half-made worktree entry). All of them go through the project's git helper under its worktree lock; nothing calls `git worktree` directly. (g2 fix)
- A pool transaction writes files at once and has no rollback: undoing by git reset leaves new untracked files (a new group record) behind. A change that can be refused halfway runs first on an in-memory copy and only then on the real transaction. (#127)
- The dag file carries top-level keys besides the tickets (the first ticket id, group order): every writer rewrites it from the whole record it read, never from the tickets alone, or those keys are silently lost.
- The pool knows nothing of the project config: anything its report should do with project settings must be handed in at the one place devloop builds a pool.
