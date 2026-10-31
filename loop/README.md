# loop/ — the dev loop (v2)

Five scripts, one git-backed ticket pool, one DAG file. Design and decisions: `docs/design/dev-loop-v2.md`,
D-078, D-079, D-080. Python 3.8+ standard library only; Linux and macOS.

```
you -> main session -> ticket_creator.py -> opener (LLM) -> pool: tickets + dag.json
ticket_controller.py (no LLM, the only driver)
  reads the DAG, groups tickets, opens integration branch int/<group> from main,
  starts ticket_issuer.py --id N --target int/<group> (parallelism decided here)
ticket_issuer.py
  worker + verifier -> (reviewer) -> verifier's cases -> worker's branch merged into int/<group>
  failing step = feedback for the next round (max 3 rounds, 3 hours)
  worker thinks the ticket is wrong -> it runs ticket_creator.py --revise itself
group complete (= one PR, a local mark) -> regression.py (preflight + full suite)
  red -> auto_fixer.py on the integration branch (max 3 rounds) -> regression again
  green -> main fast-forwards to the branch, the project's release script runs
```

## What the main session runs

| Command | When |
| --- | --- |
| `python3 loop/tickets.py init` | once per project: create the pool |
| `python3 loop/ticket_creator.py --request "..."` | the user wants something |
| `python3 loop/ticket_controller.py` | run until nothing is left (`--forever` keeps watching) |
| `python3 loop/tickets.py inbox` / `ack` | pages: created, landed, decision, env |
| `python3 loop/tickets.py list` / `show N` | state of tickets and groups |
| `python3 loop/tickets.py ready N` | after deciding on a stuck ticket |
| `python3 loop/tickets.py cancel N --why ...` | drop a ticket |

Run them from the project (or set `AC_ROOT` to it).

## Project settings (`board/config.json`, key `loop`)

`release` is required (every project ships a release script; the controller only calls it, with
`LOOP_RELEASE_SHA`, `LOOP_GROUP`, `LOOP_TICKETS` set). Everything else has a default:
`pool` (default `<agent-control>/pools/<project>`), `max_parallel` 2, `max_rounds` 3,
`issuer_timeout_seconds` 10800, `agent_timeout_seconds` 3600, `main_branch` "main",
`remote` "origin" (null = do not push), `worktrees` (default `../<project>-loop`),
`agents.<role>` (the command that starts an agent; prompt on stdin).
Tests run through the project's existing test interface (`tests.*`, `scripts/testiface.py`).
`hard_block.paths` / `patterns`: a ticket that touches them stops before merging.

## Contracts

- **Pool**: every write is pull → change → commit → push; a rejected push re-reads and applies the
  change again. The pool is the only state; logs under `<worktrees>/runs/` are only logs.
- **Agents** get a prompt on stdin and write their hand-in to `$LOOP_RESULT`; nothing else is read.
- **Exit codes**: 0 done · 1 stuck/red (recorded) · 2 refused or could not start · 3 needs the user
  (a page was written) · 4 interrupted (left to be picked up again) · 86 environment (regression).
- **OS failures** (missing program, permission, disk full, broken JSON, git errors, timeouts) are
  caught in one place (`core/osx.py`, `core/gitx.py`) and end as a page or an exit code with one
  sentence, never a silent hang. Timed-out commands are stopped with their whole process group.

## Tests

`python3 -m unittest tests_loop.test_loop` — a throwaway project, pool, fake agents and fake tests
behind the real test interface; about 50 seconds.
