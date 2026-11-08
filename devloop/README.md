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
| `python3 devloop/tickets.py lessons --layer L --name N --file F` | queue every bullet line of F as a lesson for that memory file (a project's own older memory) |
| `python3 devloop/ticket_creator.py --request "..."` | the user wants something; `--revise N --from user --decision D [--page P] [--by NAME]` is the user's answer, recorded first as a 'user answer' event (decision, text, by, page) and refused for a running ticket |
| `python3 devloop/ticket_controller.py` | run until nothing is left (`--forever` keeps watching); a run that ends idle with tickets or groups left undone writes one idle page (ref `controller`, the same lines on stderr) saying what each waits for |
| `python3 devloop/tickets.py inbox` / `ack P [--by NAME]` | pages: created, landed, decision, env, cancelled, idle; `--by` sets acked_by and, for a page whose ref is a ticket or group, writes a 'page acknowledged' event on it (`ack --all --by NAME` only sets acked_by) |
| `python3 devloop/tickets.py list` / `show N` | state of tickets and groups; a merged ticket shows as 功能完成 and a landed one as 驗證完成, while the pool keeps merged / landed |
| `python3 devloop/tickets.py ready N [--by NAME] [--page P]` | after deciding on a stuck ticket |
| `python3 devloop/tickets.py ready gN` `[--note "..."] [--by NAME] [--page P] [--via main|web]` | a stuck group whose branch and merged tickets are still there: verify it again (fix rounds back to 0); the `--note` is stored on the group and every later fixer of it gets it as the user's ruling (`--via web` marks a note sent from the board: the fixer gets it cleaned and wrapped behind the injection check, like web text for the opener); `--by` names who gave the answer on the events and `--page` acks that page in the same write |
| `python3 devloop/tickets.py cancel N --why ... [--by NAME] [--page P]` | drop a ticket |
| `python3 devloop/tickets.py release gN [--again]` | re-run a landed group's failed release once (`--again` also when it was ok); the outcome is a page |
| `python3 devloop/tickets.py cancel gN --why ... [--by NAME] [--page P]` | close a stuck or open group with no running member: its ready and merged tickets are cancelled, the branch is kept; one `cancelled` page lists them (it names `--by`); `--page` acks that page in the same write |
| `python3 devloop/tickets.py rerun gN [--why …] [--by NAME] [--page P]` | the environment of a paused group (regression env or unmeasured, or its fixer's worktree setup failed) is fixed: the paused mark is removed, an 'environment fixed: verify again' event is written and `--page` is acked, in one write; a `--forever` controller picks the group up at its next tick; without `--forever`, run the controller again. Refused (exit 2, nothing written) unless the group is pr or fixing without a job |
| `python3 devloop/board.py serve [--port 8765] [--key-file F]` | the dashboard (or `board.py html --out F [--query Q]`) |

Agents run their tests with `python3 devloop/run_tests.py <ids>` (output stays in their run directory).

Run them from the project (or set `AC_ROOT` to it).

## Project settings (`board/config.json`, key `devloop`)

`release` is required (every project ships a release script; the controller only calls it, with
`DEVLOOP_RELEASE_SHA`, `DEVLOOP_GROUP`, `DEVLOOP_TICKETS`, `DEVLOOP_RELEASE_DIR` set; it runs in a throwaway checkout that is removed afterwards, so anything it produces goes to `$DEVLOOP_RELEASE_DIR` = `<worktrees>/runs/release/<group>/`, which devloop never empties; its whole output is kept in `<worktrees>/runs/release/<group>.log`, named on the group as `release_log` and in the page) and is stopped after `release_timeout_seconds` (default 3600). Everything else has a default:
`pool` (default `<agent-control>/pools/<project>`), `max_parallel` 2, `max_group_size` 10 (a `together` ticket opens a new integration branch once the open one has this many members; no ticket that is not yet a member joins a group that is full, whichever path chose it), `max_rounds` 3,
`issuer_timeout_seconds` 10800, `agent_timeout_seconds` 3600, `main_branch` "main",
`remote` "origin" (null = do not push), `worktrees` (default `../<project>-devloop`),
`agents.<role>` (the command that starts an agent; prompt on stdin),
`memory` (D-088: the folder of the memory files; default `<pool>/memory`, a relative path is against the project root; see Memory).
Tests run through the project's existing test interface (`tests.preflight`, `tests.run`, `tests.full`; `scripts/testiface.py`).
`hard_block.paths` / `patterns`: a ticket whose own change touches them stops before merging (what it merged in from main does not count).

`devloop.no_reverify_paths` (D-090; default `["memory/", "docs/DECISIONS.md", "docs/HANDOFF.md"]`,
a project's list replaces it, `[]` turns it off): when main moved after a group was verified and
every file main changed since (`git diff --name-only --no-renames <merge-base> <main>`) is one of
these paths or under one of these folders, the controller merges main into the integration branch
itself (a `--no-ff` merge commit in a throwaway worktree) and lands that merge without a second
regression and without a fix round. A move that touches anything else is merged the same way
(#72, below) and the merged branch is verified again without a fix round; only a conflict or any
other merge failure costs a fix round: auto_fixer merges main, then the regression runs again.

The controller follows main itself (#72): before a group's next regression, every integration
branch whose group is not landed, cancelled or verified, has no job running and exists gets main
merged in - moved straight to main when it has no commits of its own, otherwise a `--no-ff` merge
commit made in the throwaway tree `main-<group>` and compare-and-swapped onto the branch. One group
event names main's sha; a clean merge changes neither state nor fix round. A conflict leaves the
branch as it was, is not retried for the same main sha (`main_tried` on the group), and starts
nothing until the group is due for its regression (pr or fixing, no job, not paused under
`--forever`): then auto_fixer runs with a main-moved report instead of the regression, counted as a
fix round (at `devloop.max_rounds`: stuck "main kept moving" and a decision page).

`devloop.worktree_setup` (D-091 item 10; default null = nothing runs; otherwise a non-empty shell
command): run with `sh -c` right after devloop creates a worktree for a ticket (`t<id>`), its cases
(`t<id>-cases`), a red check (`red-t<id>`), a regression (`regression-<branch>`), a fixer
(`fix-<group>`) or a release (`release-<group>`), with cwd = that worktree and `AC_ROOT` = the
project root. A worktree devloop reuses (it already existed) is not set up again; the controller's
own throwaway trees (`nrv-<group>`, `index-<group>`, `main-<group>`) are never set up. The output is kept in
`<worktrees>/runs/setup/<folder name>.log`; a setup still running after 600 s is stopped with its
process group and counts as failed. A failed setup is an environment problem, never a red: the
worktree is removed again (a branch it was on stays, so the next run creates and sets it up again),
and then for an issuer the ticket is stuck with reason `env` (an "env" page); for a regression the
result is status `env`, exit 86 (an "env" page, the group is paused and the controller stops with
exit 3, or under `--forever` keeps running); for a fixer an "env" page for the group, the group goes
back to "pr" with its fix round not counted, is paused, and the controller stops with exit 3 (under
`--forever` it keeps running), and the rerun (or the next controller run) starts the fixer again
instead of a regression; for a release the release failed (a decision page; not retried, not
rolled back). Every such text contains `devloop.worktree_setup`.
The same holds for any fix round in which auto_fixer ended before starting the fixer agent (the
group's `fix_agent_round`, set right before the agent starts, is not its `fix_round`): no round is
counted, no regression starts, the group is paused with one "env" page saying why, and the rerun
starts the fixer again.

`devloop.prices` (default null; `{}` is the same): maps a model name (the row's `model`: the value
after `--model`, else the command's file name) to `{"input": n, "cached_input": n, "output": n}`,
US dollars per million tokens, each a number >= 0. A codex agent run's cost row sums the
`turn.completed` usage of its `exec --json` events: `input_tokens` is the sum of input minus the sum
of cached input (codex counts cached input inside input), `cache_read_tokens` the cached sum,
`output_tokens` the output sum, and `usd` is computed from the model's prices, or stays unknown
(null) when there is no table or no entry for the model. A claude row keeps the CLI's own
`total_cost_usd`. A malformed table makes the config fail with a text containing `devloop.prices`.

## Machine slots

`machine_slots` (default null = no limit) caps the heavy runs on the machine at N, across every
devloop process that shares the project's worktrees root. What takes a slot: every agent run
(every role, every command of its chain, from just before it starts until its process exits), the
issuer's red check and each case run, and a regression (from before preflight until `full` ends).
A slot is a `flock` on `<worktrees>/slots/slot-<i>` (i = 0..N-1); the files are created when
missing and never deleted. The devloop process that started the run holds the lock, so a
background process an agent leaves behind keeps no slot, and a process that dies frees its slot.
An agent and an issuer wait for a slot without a limit: the wait does not count against their
own limit (the agent's timeout, `issuer_timeout_seconds`), whose time counts from when the slot
is taken. A regression's wait still counts against `regression_timeout_seconds`
("unmeasured" when no slot comes free in time).
Nested runs use their parent's slot: agents get `DEVLOOP_SLOT` (their slot file) in the
environment, and a process with it set takes no slot (an agent's `run_tests.py`, a worker's
`ticket_creator.py --revise`).

`regression_exclusive` (default false) makes a regression take the whole machine: it runs only
while no other slot is held, and no new slot is taken while it waits or runs. It
needs `machine_slots` (exclusivity is counted in slots; true without a slot count stops every devloop
script). The mark is a `flock` LOCK_EX on `<worktrees>/slots/exclusive`, held by the regression
from the moment it starts waiting until it has released every slot; with it held, the regression
takes every `slot-<i>`, waiting for each, then runs preflight and `full`. Every other taker needs a
LOCK_SH on that file, only while it takes its slot; its wait for it is a slot wait (no limit for
an agent or an issuer, its time limits count from when the slot is taken). The whole wait counts against `regression_timeout_seconds` ("unmeasured" when the
machine is not free in time), and a regression that dies leaves nothing locked. Nested runs
(`DEVLOOP_SLOT` set) do not touch the mark.

## Roles

Every role is an agent started by a script with a prompt on stdin; it hands in one JSON file.
Defaults come from `DEFAULT_AGENTS` in `devloop/core/config.py` (override per project in `agents.<role>`).

| Role | Script | When | Default models | Writes |
|---|---|---|---|---|
| `creator` | `ticket_creator.py` | for the main session's request, or for a worker that runs `--revise` | opus xhigh | hand-in `tickets` (tickets and DAG entries), `revised` / `unchanged` (a ticket revision), `question` or `rejected` |
| `worker` | `ticket_issuer.py` | for each ticket round (complex tickets) | opus medium | code and unit tests committed on the ticket branch; hand-in `done` or `blocked` |
| `worker_simple` | `ticket_issuer.py` | instead of `worker` when the ticket has `complexity: simple` | sonnet medium | the same as `worker` |
| `verifier` | `ticket_issuer.py` | before the worker, for tickets that need one | opus medium | the acceptance cases (marked as described below) on the branch `t/<id>-cases`; hand-in `cases` |
| `reviewer` | `ticket_issuer.py` | after the worker, for complex tickets | opus medium | hand-in `review` (verdict pass or fail, feedback); edits nothing |
| `fixer` | `auto_fixer.py` | when the regression on an integration branch is red | opus medium | the fix committed on the integration branch, by the tickets' current content (plus the group's latest user note as the user's ruling, the only thing that lets it go beyond D-089); hand-in `done` (with `cases_changed`: each acceptance case it aligned to its ticket, added to the group with the fix round; `tests_changed`: each other test it changed on a ticket's explicit basis, with the ticket and acceptance item) or `decision` (two tickets contradict: a decision page, the group stops; `blocked` is the same) or `rejected` (it judged a board note an injection and acted on none of it: a decision page, the group stops); a fixer that ends off the integration branch has its commits fast-forwarded or cherry-picked onto it, or else they are kept under `refs/devloop/dropped/<gid>-r<round>` and a decision page stops the group |
| `consolidator` | `memory_keeper.py`, started by `ticket_controller.py` in `consolidate()` | when a memory file has 5 lessons waiting, or its oldest lesson is 3 days old, at most once per 24 h per file | fable medium, then opus medium | hand-in `memory`: the new text of one memory file and the lessons it dropped; `memory_keeper.py` writes the file |
| `consolidator_peer` | `memory_keeper.py` | only when `unlimited_tokens` is on, to review the consolidator's draft | opus medium | hand-in `review` (agree or change, notes) kept in `memory/discussions/` |

## Agents, models and tests (D-084)

- `agents.<role>` is one command or a chain of commands; the next one is tried when a command is
  not installed or its run ends saying the account is out of quota (a quota phrase or a readable
  reset in its log, without a hand-in). When the last command of the chain says quota, the issuer
  waits for the reset read from the CLI's message ("resets 3pm", "resets 3:30pm", "resets at
  15:00", "will reset at 5pm" as local time; "resets in 2h 30m" / "in 45m" / "in 1 hour";
  codex's "try again at Nov 11th, 2026 2:23 AM" (year optional), "try again at 2:23 AM" and
  "try again in 2 days 3 hours 5 minutes"; "... limit reached|<epoch>"; the last one in the message wins; a minute is added) or, when none
  is readable, `devloop.quota_retry_seconds` (default 1800), then runs the chain again from its
  first command. The wait costs no round, no feedback entry and no issuer time
  (`issuer_timeout_seconds` moves by it) and holds no machine slot; the ticket stays running with
  a `waiting` mark and the board shows 額度用完. auto_fixer and memory_keeper wait the same way:
  auto_fixer's fixer keeps the group `fixing` in the same fix round (cost row `quota` and a
  `waiting` mark on the group, events "waiting for quota" / "quota wait over"; the board's
  integration-branch row shows 額度用完), and every agent of memory_keeper (consolidator and
  consolidator_peer) puts `waiting` (with `path`) in memory/state.json and its `quota` row in the
  run's cost; a SIGTERM during the wait keeps that row once and drops `waiting`. The opener
  (ticket_creator) does not wait: its run ends as before. Defaults: worker opus medium,
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
- devloop calls only `tests.preflight`, `tests.run` and `tests.full` (it does not use `select` or
  `regression`). `full` is the regression and must run every layer the project has (unit tests, a
  separate verification layer, every browser engine). `run <id>...` must accept every id that `full`
  reports in result.json, because the issuer re-runs the verifier's cases by id (red check, case
  runs). The verifier writes its cases into existing test modules of the layer that covers the
  change, never one new file per ticket.
- `index_path` (default `CODE-MAP.md`): a code index generated from the code and committed onto
  the integration branch just before main moves. Agents read it before searching.
- `required_fields`: extra ticket fields the opener must fill (agent-control requires `vision` and
  `removes`).
- Cost rows: role, model, effort, round, seconds, tokens (input, output, cache read and write),
  dollars, session id, outcome; a stopped run's tokens are read from the CLI transcript; when an
  issuer is interrupted (SIGTERM/SIGINT) every run of the interrupted round is recorded too, the
  ones it stopped with outcome `interrupted`; a run whose last command hit the usage limit gets
  its row with outcome `quota` when the wait starts (the commands before it in its chain are in
  `fell_back_from` with outcome `out of quota`); a landed ticket gets `cost_total`. Unknown stays unknown.
  The opener's runs (role `creator`) are recorded too: an opening run's row is split over the tickets
  it opened (`split_over` lists their ids; token counts `// N`, the lowest id gets the remainder; usd and
  seconds `/ N`), a revising run's row goes on the revised ticket whatever its outcome, and a request
  whose runs opened no ticket keeps its rows unsplit on the decision page it wrote. The Token tab counts
  the shares of one split run as one run.
- `unlimited_tokens: true`: memory consolidation becomes a two-model discussion (draft, review by
  `consolidator_peer`, one more draft if asked), kept in the pool under `memory/discussions/` (not in the memory folder).

## Memory

Layered as before (D-013): `memory/role/<role>.md`, `memory/model/<model>.md`, `memory/project.md`.
Where they live is the project setting `devloop.memory` (D-088): null (default) is `memory/` in the
pool; a folder (relative = against the project root) puts the files there, so a project can keep its
memory in its own repository. In a git checkout `memory_keeper.py` writes a consolidated file as one
commit on the checked-out branch, fast-forwarded and pushed to the remote when there is one; a failed
push is a decision page. The working state stays in the pool: `memory/inbox/` (waiting lessons),
`memory/state.json` (last consolidation, runs and tokens) and `memory/discussions/`.
Agents start with their role and model memory in the prompt (project memory by path). They never
edit memory; a hand-in may carry `"lessons": [{"layer", "text"}]`, which the scripts put in
`memory/inbox/`. The controller starts `memory_keeper.py` for one file at a time once 5 lessons wait
(or the oldest waited 3 days, at most once per 24 h per file); the consolidator agent rewrites the file. What
belongs in which layer is written once, in `prompts/consolidator.md`. `tickets.py init` seeds the
folder with the files it lacks, never overwriting: a role or model file starts as agent-control's memory/
file of that name when it has one, else with a heading only; `project.md` starts with its heading. So a
new project starts from agent-control's memory/ and grows its own. A project's own older memory is
imported with `tickets.py lessons --layer L --name N --file F` (every bullet line of F becomes a
waiting lesson).

## Live records

While an opener, an issuer, an agent, a regression, a fixer or a release runs, one JSON file under `<worktrees>/runs/live/` describes
it (`core/inflight.py`). Every record has `kind` and `stamp` (devloop's liveness stamp: `pid`, `host`,
`boot`, `started`).

- `opener-<pid>.json` (`ticket_creator.py`, kind `opener`): `title` (the request's first heading or
  first line, at most 120 characters; null for a revision), `revise` (the ticket id, or null), `via`
  (`main` or `web`), `model`, `effort` (the creator command's), `result` (null). Removed when
  ticket_creator returns, whatever its exit code.
- `issuer-<id>.json` (`ticket_issuer.py`, kind `issuer`): `ticket`, `target`, `round` (rewritten at every
  round start). Written right after the claim; removed when the issuer exits (merged, stuck or
  stopped by SIGTERM).
- `agent-<16 hex digits of sha1 of the run dir>.json` (every `agents.start`, kind `agent`): `state`
  (`slot` while waiting for a machine slot and `quota` while waiting for a quota reset, both with the
  starter's pid in the stamp; `running` with the command's pid), `parent` (the starter's pid), `role`
  (the chain role, e.g. `worker_simple`), `ticket`, `group`, `round`, `run_dir`, `log`, `command`,
  `model`, `effort`, `result` (the run's result.json), `since` (when this state began), `until` (the
  end of a quota wait, else null). A second command in the same run dir replaces it. Removed when
  `Pending.finish()` returns or `Pending.stop()` runs.
- `regression-<branch with / as ->.json` (`regression.py`, kind `regression`): `group` (the branch
  without its leading `int/`), `branch`, `sha` (of the branch when it started), `exclusive`
  (`devloop.regression_exclusive`), `stage` (`slot` waiting for a machine slot, `machine` an exclusive
  regression waiting for the whole machine, `preflight`, `full`; without `machine_slots` it starts at
  `preflight`), `since` (when the stage began), `result` (the `--out` file). The stamp is the regression
  process. Removed when regression.py exits, whatever the code.
- `fixer-<group>.json` (`auto_fixer.py`, kind `fixer`): `group`, `round` (the group's `fix_round` when it
  started, 0 when missing), `max_rounds` (`devloop.max_rounds`), `result` (null). The stamp is the
  auto_fixer process; its agent has its own agent record. Removed when auto_fixer exits.
- `release-<group>.json` (`core/release.py`, kind `release`): `group`, `sha`, `log` (the release output
  file), `result` (null). The stamp is the release command itself (the `sh -c` process). Written right
  after the spawn, removed when `release.run` returns; the controller's landing and
  `tickets.py release` both go through it.

A process stopped hard leaves its record behind. `inflight.status(record)` answers `running` (the
stamp's process lives), `handed_in` (it is gone and its `result` file exists) or `ended`. Writing a
record deletes any other that is not running and started more than a day ago. These records are for
readers only; the pool's ticket `run` and group `job` stamps are what the controller acts on.

## Host samples

While `ticket_controller.py` runs, a background thread appends one JSON line to
`<worktrees>/runs/host/<hostname>.jsonl` (the hostname with path separators as `_`) when it starts and
then every `devloop.host_sample_seconds` (default 60; null = off; anything else must be a whole number
>= 1). Each line has exactly: `at` (UTC, the pool's time format), `host`, `load1` / `load5` (load
average, null when unavailable), `cpus`, `mem_available` (bytes: cgroup limit minus use, else
`MemAvailable`, else macOS `vm_stat` - the unittest adapter's function in `scripts/hostinfo.py`, with
its `AC_CGROUP_ROOT` / `AC_MEMINFO` / `AC_VM_STAT` / `AC_SYSCTL` overrides; null when unknown),
`disk_free` (bytes free on the worktree root's filesystem), `agents`, `slots_used` and `slots`
(`devloop.machine_slots`; both null when that is null). `agents` is the number of distinct
`DEVLOOP_RUN_DIR` values among processes whose environment has `DEVLOOP_ROLE` (from
`/proc/<pid>/environ`, or `ps -A -E -ww -o command=` where there is no `/proc`; null when `ps` fails).
`slots_used` probes each slot file with a non-blocking `LOCK_EX` on a fresh fd closed at once: a slot
whose lock is refused is in use. Pruning keeps 7 days: when the file's first line is more than 8 days
old (or unreadable) the file is rewritten with only the lines from the last 7 days before the new
line goes in, so it is rewritten at most about once a day. A failed sample prints one line to the
controller's stderr and sampling carries on; the thread stops with the controller.

## Dashboard

`board.py` reads the pool only: the four VISION numbers (tokens and time per landed ticket,
first-pass share, user interventions per ticket, what waits for the user), every ticket, every
integration branch, unread pages, memory files. Unknown numbers show as 「未知」, never 0.
The page has six tabs, chosen by `?tab=`: 要你判 (`?tab=decide`), 進行中 (`?tab=live`), 票
(`?tab=tickets`; everything above but the four numbers, which every tab shows), DAG (`?tab=dag`),
Token (`?tab=tokens`) and 主機 (`?tab=host`); `board.py html --query tab=dag` writes one tab. A page
with no known tab opens 票 when the query has one of 票's filters (`ticket`, `group`, `state`, `cat`,
`q`, `done`), else 要你判 whenever something waits for the user, else 進行中; the number of cards is on the 要你判 tab ("要你判 (3)")
and in front of the browser's title ("(3) devloop · …"), never in the page heading.

進行中 reads the live records (`<worktrees>/runs/live/`, read only; it creates nothing) and shows
one row per running opener, agent, agent-less issuer, regression, fixer and release, oldest start
first, plus a 結束了,沒有交件 row (after all others) for a record whose process ended without handing
in; a handed-in record has no row. Each row shows what runs (開題 and its title or 改票 #N; an
agent's role, ticket and title or group, and round; an issuer's ticket and round; a regression's
group and whether it is the first run or the one after fix round N; a fixer's group and round of
max; a release's group and short sha), its state (執行中, 等機器名額, 等整台機器, 額度用完 until when,
the regression's stage, 沒有 agent 在跑), the command, model and effort, the local start time
(`MM-DD HH:MM:SS`), how long it has run (`<m> 分`, `<h> 小時 <m> 分`), the run dir and the last 20
log lines, and with `machine_slots` set the slots in use (`名額 1/2`). An opener's or fixer's own
agent is shown on that row, not as a row of its own; an issuer whose agent runs has no row (the
agent's row stands for it). A release command may print lines `devloop-stage: <text>`; the row
shows the text of the last such line as its step. The tab has a 重新整理 link; the page reloads
every 30 s anyway when nobody types. When no record is running the tab says in one sentence
(`p.why`, `data-why`) why, the first that holds of: `none` (no work left, nothing waits for the user),
`decision` (only cards for the user are left), `controller` (no live `controller.lock`), `deps` (every
ready ticket waits for an unfinished dependency), `dispatch` (the controller picks it up next round).

Every row that waits for a machine slot (state `slot`; a regression waiting for the whole machine
is not one) shows its place in the queue, `等機器名額:第 k 個(共 n 個在等)` (`data-queue="k/n"`, ordered by
the `since` of the record that waits, oldest first, ties by record name). While a regression with
`exclusive` runs, those rows also say whom they give way to: `,讓位給 <group> 的獨占 regression`
(`data-yield`). After the process rows come the ticket rows, one per ready ticket (`data-kind="ticket"`):
`待派`, or `等 #<id>(<state>)、… 落地` naming every dependency that is neither landed nor merged into an
open group; then the decision rows, one per 要你判 card (`data-kind="decision"`), showing what the card
asks and a link to that tab; the 結束了 rows stay last. Ticket and decision rows have no pid and no start.

要你判 shows one card per thing that waits: each unread `decision` or `env` page (one card per page,
even when its ticket is also stuck); each stuck ticket and each stuck or paused group (the
env-pause mark `paused`) that no such page is about; and each running ticket or fixing group whose
quota wait (`waiting.reason` `quota`) is more than 300 s past its `waiting.until`. A page is about
the ticket or group its `ref` names; created, landed, cancelled and idle pages, acked pages and younger
quota waits make no card. Cards run newest first by when the wait began (the page's `at`,
`stuck.at`, `paused.at`, a stuck group's last history event, the quota `until`; no time sorts last)
and carry one line saying what is asked, the stuck category, the last feedback or regression
text, the page's text and a link to the ticket's or group's timeline. Every link and form action is
relative and the server routes by the last path segment only, so the board works under a path
prefix (for example behind a reverse proxy at `/board/`).
`--key-file` accepts the key once as `?key=` and trades it for an HttpOnly, SameSite=Strict cookie
with a redirect to the page without it; scripts send header `X-Board-Key`; the key never appears
in a URL (or a page). It also adds inputs,
which are the main session's own entry behind another screen (D-083): a new request on 票 runs
`ticket_creator.py --request-file … --via web`, and every 要你判 card except quota cards gets a text
box and buttons (POST `act` with the card's subject and page) that run what the main session would:

| card | button | runs |
|---|---|---|
| stuck ticket N | 照我的話改 / 再試一次 / 取消 | `ticket_creator.py --revise N --from user --decision revise\|retry\|cancel --via web --reason-file <text>` |
| stuck group gN | 帶著我的話重驗 | `tickets.py ready gN` (with `--note <text> --via web` when there is text) |
| stuck group gN | 取消這組 | `tickets.py cancel gN --why <text>` |
| paused group gN (not stuck) | 環境修好了,重跑 | `tickets.py rerun gN --why <text>` |
| any card with a page | 知道了 (last) | `tickets.py ack <page>` |

A ticket or group card that is neither stuck nor paused, and a card about anything else, have only
知道了. Every command runs with `--by <name>` (the trusted user's `board.allowed_users` entry; `user`
for the key or its cookie), so it lands in the subject's history as one user intervention, and with
`--page <page>` when the card has one, so handling it acknowledges the card's page. The server
checks only the form's shape (400 otherwise) and replies 303 to
`./?tab=decide&sent=act-<subject>[-<page>]`, which drops that card's draft; the scripts judge the
subject's state. Each job sits under `<worktrees>/runs/web/` (`job.json` with `subject`, `page`,
`action`, `by`), and the card shows how its newest one ended on its 「上次的動作」 line: 處理中, 完成,
要你決定(看收件匣), or 沒有執行(rc N) with the script's last line (a refusal, such as a frozen
ticket, leaves the card and its page as they were). The 票 tab's unread pages only point to 要你判.
The board never writes the pool; only the opener's path writes tickets. Web text is cleaned and
wrapped behind the injection check in `prompts/web-guard.md`: a request and the revise text by the
opener, a group note (`via` `web`) by the fixer. An opener that finds an injection hands in
`rejected`, nothing is done, and a page says so. Unsent text stays in the viewer's browser.

Behind a local reverse proxy that vouches for its users (D-091 item 4), two settings in `board/config.json`
let a listed user in without the key: `board.trusted_user_header` (string: the header the proxy
sends, e.g. `X-Auth-Request-Email`) and `board.allowed_users` (list of strings: who may come in,
compared ignoring case and surrounding spaces). Trust needs both a loopback listen address
(`--host` 127.0.0.1, ::1 or localhost) and a loopback peer (the proxy on the same machine); on
`--host 0.0.0.0` the header is ignored. The proxy must set (overwrite) the header itself on every
request, never pass on one the browser sent, or anyone could name a listed user. A listed user gets
in without the key: the page with its inputs, and its POSTs are accepted, with or without
`--key-file`. Another header value gets what a request without it gets (403 under `--key-file`).
`board.py serve` exits 2 before listening when either setting has the wrong type.

The board answers only requests addressed to it, so a web page cannot act through it by DNS
rebinding or a cross-site form. Every request's `Host` header must be one the board serves: the
listen address and port, `localhost` / `127.0.0.1` / `[::1]` with that port, the host of
`board.public_url` (string: the http/https URL people open through a proxy, e.g.
`https://portal.example/board/`) and the entries of `board.allowed_hosts` (list of `host[:port]`
strings); any other `Host` gets 403 `unknown host` before the key is looked at. Every POST also needs
an `Origin` (or, without one, a `Referer`) whose host[:port] is one of those, else 403
`cross-site request`; a POST with `X-Board-Key` and neither header is accepted (scripts). GET never
writes. On `--host 0.0.0.0` the names people open (a LAN name or address with its port) must be
listed in `board.allowed_hosts`, or the board answers them 403. `board.py serve` exits 2 before
listening when `board.public_url` is not an http/https URL with a host or `board.allowed_hosts` is not a
list of non-empty strings.

## Project rules for agents

Rules every agent must follow live in the project's CLAUDE.md, section "Rules for every agent"
(loaded because agents run as `claude -p` in a worktree of the project), and in AGENTS.md for codex.
The consolidator runs in the pool, not in the project, so it does not load them. Project memory
(`project.md` in the memory folder) is for design reasons and invariants, not hard rules: agents get
it only as a path and read it when their work touches it. So a rule that must never be broken goes
in CLAUDE.md / AGENTS.md, and a file that must never change goes in `hard_block.paths`.

## Contracts

- **Pool**: every write is pull → change → commit → push; a rejected push re-reads and applies the
  change again. The pool is the only state; logs under `<worktrees>/runs/` are only logs.
- **Agents** get a prompt on stdin and write their hand-in to `$DEVLOOP_RESULT`; nothing else is read.
- **Exit codes**: 0 done · 1 stuck/red (recorded) · 2 refused or could not start · 3 needs the user
  (a page was written; under `--forever` an env or unmeasured result pauses the group instead of
  exit 3 and the controller keeps running other groups until `tickets.py rerun gN`) · 4 interrupted (left to be picked up again) · 86 environment (regression).
- **OS failures** (missing program, permission, disk full, broken JSON, git errors, timeouts) are
  caught in one place (`core/osx.py`, `core/gitx.py`) and end as a page or an exit code with one
  sentence, never a silent hang. Timed-out commands are stopped with their whole process group.
- **A merged or landed ticket is frozen**: nobody revises or reopens it (`ticket_creator.py
  --revise` from any caller and `tickets.py ready` refuse with one line, D-089); overturning one takes a
  new ticket through `actl new --request "..."`, which may revert it.

## Tests

`python3 -m unittest tests_devloop.test_devloop` — a throwaway project, pool, fake agents and fake tests
behind the real test interface; about 50 seconds.
