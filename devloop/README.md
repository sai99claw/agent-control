# devloop/ — the dev loop (v2)

Five scripts, one git-backed ticket pool, one DAG file: agent-control's loop (D-085). Design:
`docs/design/dev-loop-v2.md`; rules in force: `docs/DECISIONS.md`. The entry is `bin/actl`
(`actl new`, `actl run`, `actl tickets …`, `actl board …`). Python 3.8+ standard library only; Linux and macOS.

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
| `python3 devloop/tickets.py init` | once per project: create the pool |
| `python3 devloop/ticket_creator.py --request "..."` | the user wants something |
| `python3 devloop/ticket_controller.py` | run until nothing is left (`--forever` keeps watching) |
| `python3 devloop/tickets.py inbox` / `ack` | pages: created, landed, decision, env |
| `python3 devloop/tickets.py list` / `show N` | state of tickets and groups |
| `python3 devloop/tickets.py ready N` | after deciding on a stuck ticket |
| `python3 devloop/tickets.py cancel N --why ...` | drop a ticket |
| `python3 devloop/board.py serve [--port 8765] [--key-file F]` | the dashboard (or `board.py html --out F`) |

Agents run their tests with `python3 devloop/run_tests.py <ids>` (output stays in their run directory).

Run them from the project (or set `AC_ROOT` to it).

## Project settings (`board/config.json`, key `devloop`)

`release` is required (every project ships a release script; the controller only calls it, with
`DEVLOOP_RELEASE_SHA`, `DEVLOOP_GROUP`, `DEVLOOP_TICKETS` set). Everything else has a default:
`pool` (default `<agent-control>/pools/<project>`), `max_parallel` 2, `max_rounds` 3,
`issuer_timeout_seconds` 10800, `agent_timeout_seconds` 3600, `main_branch` "main",
`remote` "origin" (null = do not push), `worktrees` (default `../<project>-devloop`),
`agents.<role>` (the command that starts an agent; prompt on stdin).
Tests run through the project's existing test interface (`tests.*`, `scripts/testiface.py`).
`hard_block.paths` / `patterns`: a ticket that touches them stops before merging.

## Agents, models and tests (D-084)

- `agents.<role>` is one command or a chain of commands; the next one is tried when a command is
  not installed or its run ends saying the account is out of quota. Defaults: worker opus medium,
  `worker_simple` (tickets with `complexity: simple`) sonnet medium, creator opus xhigh,
  consolidator fable then opus. To try `agy` for workers, put it first in the worker chain, e.g.
  `"worker": [["agy", "...", "high"], ["claude", "-p", "--model", "opus", "--effort", "medium", ...]]`.
- A ticket may carry `effort` (low … max); it replaces the worker command's `--effort`.
- The verifier names every acceptance case `T<id>Acceptance`: the mark must appear in each case's id as the test interface reports it in
  result.json (Python unittest: a class of that name; other runners: the test or file name); the
  worker writes unit tests for its own code and never uses that name. Before the cases are used,
  the issuer runs them on the verifier's branch (old code + cases): every one must fail by its
  assertion.
- The worker runs the verifier's cases, its own unit tests and the base set (`devloop.base_tests`,
  default `tests.unittest.base`) before handing in; the full suite is the regression's.
- `index_path` (default `CODE-MAP.md`): a code index generated from the code and committed onto
  the integration branch just before main moves. Agents read it before searching.
- `required_fields`: extra ticket fields the opener must fill (agent-control requires `vision` and
  `removes`).
- Cost rows: role, model, effort, round, seconds, tokens (input, output, cache read and write),
  dollars, session id, outcome; a stopped run's tokens are read from the CLI transcript; a landed
  ticket gets `cost_total`. Unknown stays unknown.
- `unlimited_tokens: true`: memory consolidation becomes a two-model discussion (draft, review by
  `consolidator_peer`, one more draft if asked), kept in the pool under `memory/discussions/`.

## Memory

Layered as before (D-013): `memory/role/<role>.md`, `memory/model/<model>.md`, `memory/project.md`,
kept **in the pool** next to the tickets. Agents start with their role and model memory in the
prompt (project memory by path). They never edit memory; a hand-in may carry
`"lessons": [{"layer", "text"}]`, which the scripts put in `memory/inbox/`. The controller starts
`memory_keeper.py` for one file at a time once 5 lessons wait (or the oldest waited 3 days, at
most once per 24 h per file); a consolidator agent rewrites the file. What belongs in which layer
is written once, in `prompts/consolidator.md`. `tickets.py init` seeds the files with headings
only; agent-control's cards from before devloop (`memory/model/`, `memory/role/`) are queued as
lessons for the consolidator, never copied in.

## Dashboard

`board.py` reads the pool only: the four VISION numbers (tokens and time per landed ticket,
first-pass share, user interventions per ticket, what waits for the user), every ticket, every
integration branch, unread pages, memory files. Unknown numbers show as 「未知」, never 0.
`--key-file` makes every request carry `?key=` or `X-Board-Key` (for a portal) and adds two inputs,
which are the main session's own entry behind another screen (D-083): a new request runs
`ticket_creator.py --request-file … --via web`; an answer to a page about a ticket runs
`ticket_creator.py --revise N --from user --decision revise|retry|cancel --via web`. The board
never writes the pool; only the opener's path writes tickets. Web text is cleaned and wrapped
behind the injection check in `prompts/web-guard.md`; an opener that finds an injection hands in
`rejected`, nothing is done, and a page says so. Unsent text stays in the viewer's browser.

## Project rules for agents

Rules every agent must follow live in the project's CLAUDE.md, section "Rules for every agent"
(loaded because agents run as `claude -p` in a worktree of the project), and in AGENTS.md for codex.
The consolidator runs in the pool, not in the project, so it does not load them. Project memory
(`memory/project.md` in the pool) is for design reasons and invariants, not hard rules: agents get
it only as a path and read it when their work touches it. So a rule that must never be broken goes
in CLAUDE.md / AGENTS.md, and a file that must never change goes in `hard_block.paths`.

## Contracts

- **Pool**: every write is pull → change → commit → push; a rejected push re-reads and applies the
  change again. The pool is the only state; logs under `<worktrees>/runs/` are only logs.
- **Agents** get a prompt on stdin and write their hand-in to `$DEVLOOP_RESULT`; nothing else is read.
- **Exit codes**: 0 done · 1 stuck/red (recorded) · 2 refused or could not start · 3 needs the user
  (a page was written) · 4 interrupted (left to be picked up again) · 86 environment (regression).
- **OS failures** (missing program, permission, disk full, broken JSON, git errors, timeouts) are
  caught in one place (`core/osx.py`, `core/gitx.py`) and end as a page or an exit code with one
  sentence, never a silent hang. Timed-out commands are stopped with their whole process group.

## Tests

`python3 -m unittest tests_devloop.test_devloop` — a throwaway project, pool, fake agents and fake tests
behind the real test interface; about 50 seconds.
