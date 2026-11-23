# Dev loop v2 (`devloop/`) — five scripts, one pool, one DAG

Status: **the loop of agent-control** since D-085 (rules in force: `docs/DECISIONS.md`). The old loop
(`auto-fix.sh`, `apply.sh`, `gate.sh`, `review.sh`, `schedule.py`, `land.sh`) was removed at the
cut-over; it is in git history. This page replaces the earlier draft (one `actl run` + three stages), which the user
superseded with the plan below.

## 1. The user's plan (D-079, D-080; quoted in DECISIONS)

```
you -> main -> ticket_creator (prompt chain + reference ticket + opener) -> pool: tickets, dag.json
ticket_controller (no LLM): read DAG -> group -> open int/<group> -> ticket_issuer --id --target
ticket_issuer: worker + verifier -> reviewer (if asked) -> verifier's cases -> merge into int/<group>
int/<group> complete = one PR (local mark) -> regression -> red: auto_fixer on the branch
                                                         -> green: main + release -> back to DAG
```

Judging rule (D-078): clean loop logic and a simple interface come first; line counts do not
matter; when an exception appears, change the flow instead of adding a branch.

## 2. Who calls what

| Script | LLM | Called by | Does |
| --- | --- | --- | --- |
| `ticket_creator.py` | opener | main; a worker (`--revise`) | request → tickets + DAG entries, validated, one push; or revise one ticket; questions on direction, business logic, UX go to main |
| `ticket_controller.py` | none | main, once | the only driver: jobs, groups, dispatch, landing, release |
| `ticket_issuer.py` | worker, verifier, reviewer | controller | one ticket from running to merged into its integration branch |
| `regression.py` | none | controller | preflight + full suite on an integration branch |
| `auto_fixer.py` | fixer | controller | fixes a red integration branch on that branch; merges a moved main |
| `tickets.py` | none | main | init, list, show, ready, cancel, inbox, ack |
| `memory_keeper.py` | consolidator | controller | rewrites one memory file from the lessons waiting for it |
| `board.py` | none | main | the dashboard: reads the pool, writes nothing |
| `run_tests.py` | none | agents | runs test ids, output kept in the agent's run directory |

No script polls another; each records its outcome in the pool and exits.

## 3. State

- **Pool** = a git repository (`pools/<project>`, a sub repo of agent-control): `tickets/<id>.json`,
  `dag.json`, `groups/<gid>.json`, `inbox/<page>.json`. Read = sync first. Write = change, commit,
  push; a rejected push re-reads and applies the change again (a change is a function, never a saved
  diff). One machine: an flock serialises the shared working copy.
- **Ticket states**: ready → running → merged → landed; stuck (reason: rounds, timeout, decision,
  hard_block, env, error); cancelled.
- **Group states**: open → pr → (fixing) → verified → landed; stuck.
- **Memory** (D-088; the folder is the project setting `devloop.memory`, default `memory/` in the pool; see `devloop.memory` in the README): `memory/role|model/<name>.md`, `memory/project.md`; lessons from
  hand-ins wait in the pool's `memory/inbox/`; one consolidation at a time (5 lessons or 3 days, once per 24 h).
- **DAG** (`dag.json`): `depends_on` only. A ticket's group is not in the DAG: it is the ticket's
  `group` and the group's `members`, written together when the ticket is opened.

## 4. Grouping and dispatch

- A ticket's group is written when it is opened; the controller only follows it (issue #6 part 2).
  The tickets of one opener hand-in become the members of one new group at the end of the chain,
  in the same pool write as the tickets; the opening makes no git branch.
- A ready ticket starts only in its own group while that group is open, when every dependency is
  in its starting point: landed, or merged into the same group; with a landed one, the group's
  branch must contain main or not exist yet. The controller makes the branch from main's current
  sha right before the first start (the group's `base`). Issuers branch from the integration
  branch head, so a ticket sees the merged work of the tickets it depends on.
- A ready ticket with no open group (written before this change, or one that left its group) gets
  a group of its own at the end of the chain, in ticket order, once every dependency is landed,
  running, merged, or ready in an open group; a ticket whose dependency is stuck, cancelled or
  missing gets none and waits.
- An open group stays open while a member runs or a ready member can still start (every dependency
  landed, or ready, running or merged in this group or in an earlier group that is not cancelled).
  Otherwise its ready members leave it and it becomes the PR when a merged member remains, stuck
  when other members remain, cancelled when none remains. A group with no members left is
  cancelled. A stuck ticket does not hold its group; its dependents wait.
- Groups form one chain (issue #6, `core/chain.py`), in group-number order: a group in pr or
  fixing starts its regression, main-moved fix round or retried fix round only when
  every earlier group is landed or cancelled, so it merges main once and runs its regression once. A stuck group
  with no merged ticket holds no one; a stuck group with a merged ticket, or a paused group, holds
  the later ones. Following main goes on while a group waits; landing a verified group is not
  gated. A group that only waits is not work: a controller without `--forever` ends.

## 5. Merging and landing

- The worker's branch is merged into the integration branch by the issuer: take in the latest
  target (git merge; a conflict goes back to the worker, an LLM, as feedback), re-run the cases if
  that changed anything, then compare-and-swap the target ref. If another ticket moved it first,
  take it in again. The controller never merges anything that can conflict.
- Green regression → main fast-forwards to the verified sha (the checkout of main, or a ref
  compare-and-swap), push, then the project's release script (`devloop.release`, required) runs at
  that sha. Release failure: recorded, reported, not retried, not rolled back.

## 6. Exceptions and the flow change that covers each

| Case | Flow |
| --- | --- |
| tickets of one group depend on each other | issuer branches from the integration branch; dispatch waits for "merged into the same branch" |
| several workers merge into one branch at once | compare-and-swap; the loser takes the branch in and tries again |
| main moved during verification | only in `devloop.no_reverify_paths` (D-090): the controller merges main in itself (no conflict) and lands without re-verifying; otherwise the controller merges main in itself (#72) and verifies again without a fix round; only a conflict costs a fix round: auto_fixer merges main and its agent resolves it, regression again. Waiting integration branches (not landed, cancelled or verified, no job) take main the same way before their next regression; a conflict waits until the group is due for it |
| a ticket gets stuck | the rest of its group lands; it and its dependents go to main |
| environment unusable at regression (preflight rc 86) | not red: one "env" page and the group is paused (`paused` = reason env or unmeasured, at, text); without `--forever` the controller stops (exit 3), run it again after fixing; under `--forever` it keeps running other groups and leaves this one alone until `actl tickets rerun gN` (on the board: 環境修好了,重跑) |
| `devloop.worktree_setup` fails in a new worktree (rc != 0 or past 600 s) | not red: the worktree is removed (its branch stays); issuer: ticket stuck "env"; regression: status env, exit 86; fixer: "env" page, group back to "pr" without using a fix round, paused, controller exit 3, the rerun starts the fixer again (no regression first); release: release failed, decision page |
| auto_fixer ends (any exit, or an adopted job that ended) without having started the fixer agent (`fix_agent_round` != `fix_round`) | not red: the round is not counted, no regression starts; group back to "pr", paused, `fix_retry`; one "env" page with the last line of the fixer log (for exit 86 auto_fixer's own page); controller exit 3 without `--forever`; the rerun starts the fixer again |
| a fixer finds two tickets contradict | it hands in `decision` (where they contradict, which new ticket it proposes): a decision page, the group stops; merged tickets are frozen, the fixer only aligns cases to their current acceptance and lists them in `cases_changed` |
| a fixer ends with its worktree off the integration branch (detached or another branch) | its new commits go onto the branch by fast-forward, else by cherry-pick, with a group event saying how and how many; a merge commit among them or a failed cherry-pick keeps its HEAD under `refs/devloop/dropped/<gid>-r<round>`, writes a decision page naming that ref and stops the group (exit 3); the worktree is back on the branch either way |
| release fails after main moved | one decision page; no retry, no rollback |
| the regression after a fix round (it tested `fix_end_sha`) fails more cases than the red report the round started from | the round is dropped: its head kept under `refs/devloop/dropped/<gid>-r<round>`, `int/<gid>` compare-and-swapped back to `fix_start_sha` (lost swap: nothing reset), one group event with `dropped`, `ref`, `failing_before`, `failing_after`; the round counts and the next starts from the old report without a regression; at `devloop.max_rounds` stuck with a decision page naming the ref |
| controller / container dies | no state outside the pool and git; a running ticket whose issuer is dead goes back to ready |
| fix worktree left off `int/gN` (detached, another branch, unfinished merge, ref moved by update-ref) | each fix round starts by putting `fix-gN` back on `int/gN` at its head: operations aborted, `checkout --force`, `reset --hard`; if git refuses, the worktree is recreated; one group event names the old sha |
| an agent's last command hits the usage limit | the issuer waits for the reset named in the CLI's message (else `devloop.quota_retry_seconds`) holding no machine slot, then runs the chain again; no round, no feedback, no issuer time; the ticket stays running with `waiting` (board: 額度用完), cost outcome `quota`; auto_fixer (fixer) and memory_keeper (consolidator, consolidator_peer) wait the same way: the group stays `fixing` in the same fix round with `waiting` (board's integration-branch row: 額度用完), memory/state.json gets `waiting` with `path`, the `quota` row goes into the group's / run's cost; the opener does not wait |
| two processes edit one ticket | a rejected push re-reads and re-applies; no git conflict resolution in the pool |
| a ticket touches hard_block | the issuer stops before merging it; decision page; only the ticket's own change counts (a file or added line in both the target diff and the main diff), not what it merged in from main |
| why tickets get stuck | cases wrong → the worker can say so through `--revise` (cases are rewritten); too broad → the opener's rule is one ticket = one purpose stated in one sentence, and it estimates the tokens (`token_estimate`); hangs → 3 h per issuer, 1 h per agent call; needs a decision → main |

## 7. Not done yet

1. **Cut-over** (the old `board/board.py` and `scripts/memory.py` / role cards go with it; their
   still-valid role lessons are fed to the new memory as lessons): switch the main session to `devloop/`, delete the old loop scripts and the rules that
   describe them (CLAUDE.md, role cards, DISPATCH-TEMPLATE, WORKFLOW), migrate the open tickets.
2. **Pool remotes**: each pool becomes a submodule once a remote repository exists for it.
3. **T**: its config gains a `loop` section with its release script; its pool moves to
   `pools/tabby` and becomes a git repository.
4. A run with real agents on a small project, to check the prompts against a real model.
