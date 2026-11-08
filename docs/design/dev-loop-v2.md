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
- **DAG** (`dag.json`): `depends_on`, `verify` = `together` (default) or `alone` (+ `why_alone`,
  only for a systemic change).

## 4. Grouping and dispatch

- A ready ticket may start when every dependency is in its starting point: landed, or merged into
  the same open integration branch. Issuers branch from the integration branch head, so a ticket
  sees the merged work of the tickets it depends on.
- `together` tickets share the open group until it has `max_group_size` members (default 10), then the next one opens another group; a group stays open while a ready ticket could still
  join it. `alone` tickets get their own group.
- An open group whose tickets are all merged or stuck becomes the PR. A stuck ticket does not hold
  its group; its dependents wait.

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
| main moved during verification | only in `devloop.no_reverify_paths` (D-090): the controller merges main in itself (no conflict) and lands without re-verifying; otherwise treated as red: auto_fixer merges main (plain merge first, LLM only on conflict), regression again |
| a ticket gets stuck | the rest of its group lands; it and its dependents go to main |
| environment unusable at regression (preflight rc 86) | not red: one "env" page and the group is paused (`paused` = reason env or unmeasured, at, text); without `--forever` the controller stops (exit 3), run it again after fixing; under `--forever` it keeps running other groups and leaves this one alone until `actl tickets rerun gN` (on the board: 環境修好了,重跑) |
| `devloop.worktree_setup` fails in a new worktree (rc != 0 or past 600 s) | not red: the worktree is removed (its branch stays); issuer: ticket stuck "env"; regression: status env, exit 86; fixer: "env" page, group back to "pr" without using a fix round, controller exit 3; release: release failed, decision page |
| a fixer finds two tickets contradict | it hands in `decision` (where they contradict, which new ticket it proposes): a decision page, the group stops; merged tickets are frozen, the fixer only aligns cases to their current acceptance and lists them in `cases_changed` |
| release fails after main moved | one decision page; no retry, no rollback |
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
