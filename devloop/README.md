# devloop/ — the dev loop (v2)

Five scripts, one git-backed ticket pool, one DAG file: agent-control's loop (D-085). Design:
`docs/design/dev-loop-v2.md`; rules in force: `docs/DECISIONS.md`. The entry is `bin/actl`
(`actl new`, `actl run`, `actl tickets …`, `actl board …`). Python 3.8+ standard library only; Linux and macOS.

```
you -> main session -> ticket_creator.py -> opener (LLM) -> pool: tickets + dag.json + their group
ticket_controller.py (no LLM, the only driver)
  reads the DAG, follows each ticket's group, makes branch int/<group> from main at its first start,
  starts ticket_issuer.py --id N --target int/<group> (parallelism decided here)
ticket_issuer.py
  worker + verifier -> (reviewer) -> verifier's cases -> worker's branch merged into int/<group>
  each round starts on int/<group>'s current head with only the ticket's own commits (D-098)
  failing step = feedback for the next round (max 3 rounds, 3 hours; a round that ended on a
  merge conflict with the group (also a conflict replaying the ticket's own commits at round
  start), on a revision that threw out the cases, or on a blocked worker
  whose failing ids passed when devloop ran them (agent environment) does not count, at most
  2 x max_rounds rounds in all)
  worker thinks the ticket is wrong -> it runs ticket_creator.py --revise itself
group complete (= one PR, a local mark) -> regression.py (preflight + quick, if declared + full suite)
  groups form one chain (core/chain.py, ordered by the DAG): the regression (or a fix round)
    starts only when every earlier group is landed or cancelled; a stuck group with no merged
    ticket holds no one
  quick or full red with 1-5 failing cases -> those ids run once more (`run <ids>`); the ones that
    pass are flaky: not red, named in the report's `flaky` and on the group's pages
  red -> auto_fixer.py on the integration branch (max 3 rounds) -> regression again
  green -> main fast-forwards to the branch, the project's release script runs
```

## What the main session runs

| Command | When |
| --- | --- |
| `python3 devloop/tickets.py init` | once per project: create the pool |
| `python3 devloop/tickets.py lessons --layer L --name N --file F` | queue every bullet line of F as a lesson for that memory file (a project's own older memory) |
| `python3 devloop/ticket_creator.py --request "..."` | the user wants something; `--revise N --from user --decision D [--page P] [--by NAME]` is the user's answer (`--from main` is the main session answering for the user, D-094: same `--decision` effects, `by` defaults to `main-session`; `--decision` anywhere else is refused with exit 2), recorded first as a 'user answer' event (decision, text, by, page) and refused for a running ticket |
| `python3 devloop/ticket_controller.py` | run until nothing is left (`--forever` keeps watching); a run that ends idle with tickets or groups left undone writes one idle page (ref `controller`, the same lines on stderr) saying what each waits for; an open group held by stuck members gets `<gid> waits for the decision on #<N>[, #<M>]` (D-097); after a group's own line, each group that waits on an earlier one gets `<gid> waits for <gM> to land (place <k> in the chain)` |
| `python3 devloop/tickets.py inbox` / `ack P [--by NAME]` | pages: created, landed, decision, env, cancelled, idle; `--by` sets acked_by and, for a page whose ref is a ticket or group, writes a 'page acknowledged' event on it (`ack --all --by NAME` only sets acked_by); a page with options also prints its question, each option with the command it runs, and the recommendation |
| `python3 devloop/tickets.py wait [--timeout S] [--kind K,...]` | block until a page arrives that was not in the inbox at its start (new = not there then, not acked, kind listed in `--kind`), print it as inbox does, exit 0; exit 1 after `--timeout S` with no new page; exit 2 for a bad kind, a timeout <= 0 or no pool |
| `python3 devloop/tickets.py list` / `show N` | state of tickets and groups; a merged ticket shows as 功能完成 and a landed one as 驗證完成, while the pool keeps merged / landed |
| `python3 devloop/tickets.py ready N [--by NAME] [--page P]` | after deciding on a stuck ticket (one without an open group must then be placed with `actl dag move`) |
| `python3 devloop/tickets.py ready gN` `[--note "..."] [--by NAME] [--page P] [--via main|web]` | a stuck group whose branch and merged tickets are still there: verify it again (fix rounds back to 0); the `--note` is stored on the group and every later fixer of it gets it as the user's ruling (`--via web` marks a note sent from the board: the fixer gets it cleaned and wrapped behind the injection check, like web text for the opener); `--by` names who gave the answer on the events and `--page` acks that page in the same write |
| `python3 devloop/tickets.py cancel N --why ... [--by NAME] [--page P]` | drop a ticket |
| `python3 devloop/tickets.py release gN [--again] [--by NAME] [--page P]` | re-run a landed group's failed release once (`--again` also when it was ok); the outcome is a page; `--by` is stored on the 'release re-run' event and `--page` acks that page in the same write (a refused re-run writes nothing) |
| `python3 devloop/tickets.py cancel gN --why ... [--by NAME] [--page P]` | close a stuck or open group with no running member: its ready and merged tickets are cancelled, the branch is kept; one `cancelled` page lists them (it names `--by`); `--page` acks that page in the same write |
| `python3 devloop/tickets.py rerun gN [--why …] [--by NAME] [--page P]` | the environment of a paused group (regression env or unmeasured, or its fixer's worktree setup failed) is fixed: the paused mark is removed, an 'environment fixed: verify again' event is written and `--page` is acked, in one write; a `--forever` controller picks the group up at its next tick; without `--forever`, run the controller again. Refused (exit 2, nothing written) unless the group is pr or fixing without a job |
| `actl dag dep N --add M` / `--drop M` / `actl dag move N --to gK|new` (`--reason R` required, `[--by NAME]`, default main-session) | the main session reorders a ready ticket (`devloop/dag_edit.py`, checks in `devloop/core/dagedit.py`): `dep` adds or drops one entry of N's `depends_on`, `move` puts N in another open group or a new one (an old group left empty is cancelled); one pool write with `dag:` events on the ticket and the groups involved; `actl dag after gA --add gB` / `--drop gB` puts group gA after group gB (or drops that edge) in dag.json's `group_after`, also when gA's tickets are merged, with a `dag:` event on both groups; the last stdout line is `chain: ` and the group order after the change. Refused (exit 2, one stderr line, nothing written) as the paragraph after 'Groups form one chain' lists |
| `python3 devloop/board.py serve [--port 8765] [--key-file F]` | the dashboard (or `board.py html --out F [--query Q]`) |

Agents run their tests with `python3 devloop/run_tests.py <ids>` (output stays in their run directory).

Run them from the project (or set `AC_ROOT` to it).

## Project settings (`board/config.json`, key `devloop`)

`release` is required (every project ships a release script; the controller only calls it, with
`DEVLOOP_RELEASE_SHA`, `DEVLOOP_GROUP`, `DEVLOOP_TICKETS`, `DEVLOOP_RELEASE_DIR` set; it runs in a throwaway checkout that is removed afterwards, so anything it produces goes to `$DEVLOOP_RELEASE_DIR` = `<worktrees>/runs/release/<group>/`, which devloop never empties; its whole output is kept in `<worktrees>/runs/release/<group>.log`, named on the group as `release_log` and in the page) and is stopped after `release_timeout_seconds` (default 3600). A release that exits 1 at landing (the product failed a check) has the opener open a fix ticket for it (`ticket_creator.py --by controller --origin '{"kind": "release", "group": ..., "sha": ...}'`; none while a ticket opened this way is still open, its number goes on the page instead) and then runs once more on the same sha; the controller waits for that opener run (minutes) and the rerun decides the group's `release` and page; it changes nothing on main or in the release. Every other outcome runs once (see **Release exit codes**). Everything else has a default:
`pool` (default `<agent-control>/pools/<project>`), `max_parallel` 2, `max_rounds` 3,
`issuer_timeout_seconds` 10800, `agent_timeout_seconds` 3600, `main_branch` "main",
`remote` "origin" (null = do not push), `worktrees` (default `../<project>-devloop`),
`agents.<role>` (the command that starts an agent; prompt on stdin),
`memory` (D-088: the folder of the memory files; default `<pool>/memory`, a relative path is against the project root; see Memory).
Tests run through the project's existing test interface (`tests.preflight`, optional `tests.quick`, `tests.run`, `tests.full`; `scripts/testiface.py`).
`hard_block.paths` / `patterns`: a ticket whose own change touches them stops before merging (what it merged in from main does not count).

`devloop.no_reverify_paths` (D-090; default `["memory/", "docs/DECISIONS.md", "docs/HANDOFF.md"]`,
a project's list replaces it, `[]` turns it off): when main moved after a group was verified and
every file main changed since (`git diff --name-only --no-renames <merge-base> <main>`) is one of
these paths or under one of these folders, the controller merges main into the integration branch
itself (a `--no-ff` merge commit in a throwaway worktree) and lands that merge without a second
regression and without a fix round. A move that touches anything else is merged the same way
(#72, below) and the merged branch is verified again without a fix round; only a conflict or any
other merge failure costs a fix round: auto_fixer merges main, then the regression runs again.

`devloop.notify` (default `null`): a shell command started once, detached, for every new `decision`,
`env` or `idle` page (not `created`, `landed` or `cancelled`), right after the page file is written.
It runs in the project root with the variables `DEVLOOP_PAGE_KIND`, `DEVLOOP_PAGE_SUBJECT` (the
ticket or other ref), `DEVLOOP_PAGE_TEXT` (the page's first line) and `DEVLOOP_PAGE` (absolute path
of the page file) added to the environment. Nothing waits for it: its output is appended to
`<worktrees>/runs/notify.log` and a failure is ignored. macOS notification:
`"notify": "osascript -e 'display notification (system attribute \"DEVLOOP_PAGE_TEXT\") with title (system attribute \"DEVLOOP_PAGE_KIND\")'"`;
phone push with ntfy.sh: `"notify": "curl -s -H \"Title: $DEVLOOP_PAGE_KIND $DEVLOOP_PAGE_SUBJECT\" -d \"$DEVLOOP_PAGE_TEXT\" ntfy.sh/my-topic"`.
In the shell command itself (before JSON escaping) read the variables quoted, as in `curl -s -d "$DEVLOOP_PAGE_TEXT" ntfy.sh/my-topic`.

The controller follows main itself (#72): before a group's next regression, every integration
branch whose group is not landed, cancelled or verified, has no job running and exists gets main
merged in - moved straight to main when it has no commits of its own, otherwise a `--no-ff` merge
commit made in the throwaway tree `main-<group>` and compare-and-swapped onto the branch. One group
event names main's sha; a clean merge changes neither state nor fix round. A conflict leaves the
branch as it was, is not retried for the same main sha (`main_tried` on the group), and starts
nothing until the group is due for its regression (pr or fixing, no job, not paused under
`--forever`): then auto_fixer runs with a main-moved report instead of the regression, counted as a
fix round (at `devloop.max_rounds`: stuck "main kept moving" and a decision page).

A ticket's group is written when it is opened, and the controller only follows it (issue #6
part 2). The opener chooses it (issue #6 part 3): every DAG entry of its hand-in is
`{"depends_on": [...], "group": G}`, G being an open group's id (the ticket is appended to its
members, event `member #N added by the opener`) or a new-group placeholder `new-g<n>` (tickets naming
the same placeholder share one new group; new groups are made at the end of the chain in the order
their placeholder first appears in `tickets`, first event `opened by the opener for #N, #M`), all in
the same pool write as the tickets; the opening makes no git branch, and `dag.json` keeps only
`depends_on` per ticket and keeps the file's other top-level keys (`first_ticket_id`, `group_after`). The opener's prompt has a `## Groups` section after `## Current DAG`: the chain, one
line per group not landed or cancelled with its state, whether it takes new tickets and its members.
A hand-in is refused, with nothing written, when an entry has no group, names a group that does not
exist or is not open, or places a ticket so that the group order becomes a cycle (the pool's tickets plus the
new tickets in their groups, new groups made last; the sentence is `<key>: group <g> makes the group
order a cycle: g1 -> g2 -> g1`; a landed or cancelled dependency or one without a group imposes
nothing); the check runs on the snapshot and again inside the pool write (a group that went
to pr meanwhile). The sentences go to the opener's one retry; a second refusal is the decision page
`the opener could not produce valid tickets:`. The reviser may change a ticket's depends_on and
group through the hand-in's `dag` (`{"depends_on": [ids], "group": "gK"|"new"}`, each key optional),
in the same pool write and under the refusals of `actl dag` except 'mentions #M'. A ready ticket starts only in its own group
while that group is open, once each dependency is landed or merged in the same group and, with a
landed one, the group's branch contains main or does not exist yet; the controller makes the branch
from main's current sha right before the first start and records that sha as the group's `base`.
A stuck member holds its open group (D-097): while a ticket not cancelled depends on it directly,
or another member is neither merged nor cancelled, the group waits for the decision on it - it is
not closed, no member is moved out, and a ready ticket that depends on it can still start once it
is decided (it counts like a ready dependency in its own or an earlier group in the chain). An open
group with no running member, no ready member that can still start and no stuck member that holds
it goes on: its stuck members leave it with no group (event `member #N left: stuck, nothing depends
on it and no other member of gK is unfinished (D-097)`), its ready members leave it, then it
becomes pr (a merged member remains), stuck (other members remain) or cancelled (none remains); a
group with no members left is cancelled. Existing pools: a ready
ticket written before this change has no group and gets a group of its own at the end of the chain,
one group each, in ticket order, once its dependencies allow (each is landed, running, merged, or
ready in an open group; first event `opened for #N, which had no open group`), so nothing is lost;
only those are adopted: a ticket that left its group, or whose group is not open, waits for
`actl dag move N --to gK|new` (D-097 3; the idle page says so). The group-size setting and the DAG's `verify` are
gone; a config or `dag.json` that still has them is accepted.

Groups form one chain (issue #6), ordered by the DAG (issue #13), so a group id is only a name.
Group B waits for group A when a ticket of B depends directly on a ticket of A, neither ticket nor
group landed or cancelled. Group B also waits for group A when dag.json's `group_after` lists A
under B (D-099: `{"B": ["A"]}`), while neither group is landed or cancelled; such an edge to a
landed or cancelled group stays in dag.json but orders nothing. Taking the groups by creation key (`created`, then the id in natural
order: g2 < g10, g62 < g62a < g63), each group comes after every group it waits for that is not
placed yet (pulled ahead, recursively), so a dependency moves its group ahead (g75 before g63); a
cycle (only hand edits make one) is broken without an error. A group in pr or fixing
starts its regression, a main-moved fix round or a retried fix round only when
every earlier group is landed or cancelled, so a group that waited merges main once and runs its regression once;
following main goes on while it waits, and landing a verified group is not gated. A stuck group
with no merged ticket holds no one (it has nothing to verify or land); a stuck group with a merged
ticket, or a paused one, holds every later group until it lands or is cancelled. A group that
only waits does not keep a controller without `--forever` running: it ends with the idle page.

Only the main session changes the DAG by hand, from the CLI, never on the board (D-096: the DAG is
scheduling, not ticket content): `actl dag dep N --add M|--drop M --reason R` and `actl dag move N
--to gK|new --reason R` (`devloop/dag_edit.py`, its checks in `devloop/core/dagedit.py`, run inside
the one pool write on the data it reads). Each refuses with one line, exit 2 and nothing written:
a blank `--reason`; N unknown or not ready (running, stuck, merged or landed tickets are frozen; a
stuck one is readied first); M unknown, M == N, a dependency N already has, a cancelled M (N would
wait forever), dropping one N does not have or one N's text mentions (`#M`: that is ticket content,
so the route for a mentioned dependency is `actl new --revise N`, whose opener judges it and may change
depends_on and group through its hand-in's `dag`); a group that does not exist, is not open or already holds N; and a change
that makes the ticket DAG or the group order a cycle. `actl dag after gA --add|--drop gB --reason R`
(D-099) refuses the same way: a blank `--reason`; gA or gB not a group; gA == gB; gA with a regression
running, verified, landed or cancelled (a group in pr, fixing or stuck may still be reordered); for
`--add`, an edge already set, a landed or cancelled gB, or a group order that would be a cycle; for
`--drop`, an edge that is not set.

Every round starts from the latest verified point (D-098; the target takes in main whenever main moves). Before each round's agents, ticket_issuer.py (`rebase_round`) puts both t/<N>-cases (when it exists) and t/<N> on the target's current head T and replays only the ticket's own commits on top: the commits on the branch's first-parent line that are not merge commits (so nothing a merge of the target or of the cases branch brought in) and are not reachable from T, from the target sha the branch was last put on, or from any existing branch the ticket's history names as an issuer's target (for t/<N> also not from the old t/<N>-cases head). t/<N>-cases goes first, onto T; t/<N> goes onto the replayed cases head when it held the old one, else onto T. Authors are kept, the committer is devloop; a commit whose change is already in T is dropped; a branch that already is its start plus its own commits stays as it is. Tracked changes left uncommitted in either worktree are first committed as devloop (`devloop: uncommitted changes in <branch> at the start of round <r>`), so nothing is lost. The ticket records `branch_base` ({branch: T's sha}, written by the issuer only) and one `rebased` event (branch, onto, base, replayed) per branch whose head changed. So a ticket moved to another group, or readied after stuck, carries no commit of an earlier group: commits of its own that already reached an earlier target are that group's unverified work and are not replayed. A replay that stops on a conflict (unmerged paths, or untracked files it would overwrite) ends the round before any agent starts, as a free `merge` round like a step-6 conflict: the conflicting branch's head before the replay is kept under `refs/devloop/before-rebase/t<N>-r<r>` (or `t<N>-cases-r<r>`; `-2`, `-3`, ... when the name is taken; never pushed) and the branch restarts at its start point (T, or for t/<N> the replayed cases head when it held the cases), with `branch_base` set to T and one `rebase conflict` event (branch, onto, kept) per such branch. A conflict on t/<N>-cases throws the cases out (cases null): only the verifier writes them, so it writes them again from the target. The feedback for t/<N> names the start point, the kept ref and `git cherry-pick <own commits>`: the next round's worker replays its kept commits and resolves the conflicts. Any other replay failure (a signer that fails, an environment failure) puts the branches back on their old heads and the ticket goes stuck with reason `env` and git's last line. A fix round already starts from the latest main: auto_fixer merges main into the group branch before the fixer agent starts.

A fix round that leaves the branch redder is dropped (#74). auto_fixer records `fix_start_sha` (the
branch head the fixer agent starts from, after its own clean merge of main) in the same pool write
as `fix_agent_round`; the controller records `fix_end_sha` (the branch head) when it reads the
fixer's outcome. When the next regression is red, tested exactly `fix_end_sha` and fails more cases
than the red report the round started from, the round's head is kept under
`refs/devloop/dropped/<gid>-r<round>`, the branch is compare-and-swapped back to `fix_start_sha`
(a lost swap resets nothing) and one group event names the round, both failure counts, the ref and
the dropped commits. The round still counts: the next round starts right away from the old report,
without another regression; at `devloop.max_rounds` the group is stuck and the decision page names
the ref. A main-moved report, the same or fewer failing cases, or a group sent back by
`tickets.py ready gN` (which drops `fix_end_sha`) never resets. Neither `int/<gid>` nor
`refs/devloop/*` is pushed.

A controller stopped with SIGTERM or SIGINT leaves its issuers, regressions and fixers running, and
the next controller adopts them (#124). An issuer holds a BSD flock on
`<worktrees>/locks/issuer-<N>.lock` (never deleted or replaced) from before its claim until it
exits; a second `ticket_issuer.py --id N` while it is held exits 2 with one line naming the lock
and changes nothing. The controller treats a held lock as a live issuer whatever the stamp says:
it neither releases a running ticket nor dispatches a ready one while another process holds its
lock (#130). Stopping every process the controller started also stops
its regressions: regression.py (on SIGTERM, SIGINT or SIGHUP) removes its worktree, releases its
slot or machine and records `stopped by <signal> during <stage>` as an unmeasured result; a
regression that could not record it is named on the unmeasured page by pid, exit code or signal,
with the last line of its log (`runs/regression/<gid>/regression.log`).

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

Right before each command of an issuer agent's chain starts (the first, each fall-through, each
restart after a quota wait), a missing worktree `t<id>` (branch `t/<id>`) or `t<id>-cases` (branch
`t/<id>-cases`) is made again from its branch head and set up like any new worktree, with the
ticket event "worktree made again" (path, branch; uncommitted work in it is lost); when the branch
is gone or making it fails, the ticket is stuck with reason `env` and a text that starts
`worktree missing: <path>`.

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
except the opener (role creator, also when revising) and memory consolidation (consolidator,
consolidator_peer), which take no slot and start at once even while a regression holds the machine
(every other role, every command of its chain, from just before it starts until its process exits), the
issuer's red check and each case run, and a regression (from before preflight until `full` ends).
A slot is a `flock` on `<worktrees>/slots/slot-<i>` (i = 0..N-1); the files are created when
missing and never deleted. The devloop process that started the run holds the lock, so a
background process an agent leaves behind keeps no slot, and a process that dies frees its slot.
An agent and an issuer wait for a slot without a limit: the wait does not count against their
own limit (the agent's timeout, `issuer_timeout_seconds`), whose time counts from when the slot
is taken. A regression waits for its slot at most `regression_timeout_seconds`
("unmeasured" when no slot comes free in time) and its run gets `regression_timeout_seconds` from when the slot is taken;
the wait reuses that setting because another group's regression runs at most `regression_timeout_seconds` once it has the machine, so the wait is long enough for it to finish.
Nested runs use their parent's slot: agents get `DEVLOOP_SLOT` (their slot file) in the
environment, and a process with it set takes no slot (an agent's `run_tests.py`, a worker's
`ticket_creator.py --revise`). `run_tests.py` outside a slot (the opener's way to run tests) takes
one without waiting, or runs nothing and exits 2 with machine busy on stderr.

`regression_exclusive` (default false) makes a regression take the whole machine: it runs only
while no other slot is held, and no new slot is taken while it waits or runs. It
needs `machine_slots` (exclusivity is counted in slots; true without a slot count stops every devloop
script). The mark is a `flock` LOCK_EX on `<worktrees>/slots/exclusive`, held by the regression
from the moment it starts waiting until it has released every slot; with it held, the regression
takes every `slot-<i>`, waiting for each, then runs preflight and `full`. Every other taker needs a
LOCK_SH on that file, only while it takes its slot; its wait for it is a slot wait (no limit for
an agent or an issuer, its time limits count from when the slot is taken). A regression waits for the whole machine at most `regression_timeout_seconds` ("unmeasured" when the
machine is not free in time) and its run gets `regression_timeout_seconds` from when the machine is taken, and a regression that dies leaves nothing locked. Nested runs
(`DEVLOOP_SLOT` set) do not touch the mark.

## Roles

Every role is an agent started by a script with a prompt on stdin; it hands in one JSON file.
Defaults come from `DEFAULT_AGENTS` in `devloop/core/config.py` (override per project in `agents.<role>`).

| Role | Script | When | Default models | Writes |
|---|---|---|---|---|
| `creator` | `ticket_creator.py` | for the main session's request, or for a worker that runs `--revise` | opus xhigh | hand-in `tickets` (tickets and DAG entries, each entry naming the ticket's `group`: an open group from the prompt's `## Groups` or a new `new-g<n>`; a group that cannot work is refused before anything is written), `revised` / `unchanged` (a ticket revision), `question` or `rejected` |
| `worker` | `ticket_issuer.py` | for each ticket round (complex tickets) | opus medium | code and unit tests committed on the ticket branch; hand-in `done` or `blocked` (with optional `failing` test ids: devloop runs them on the ticket branch, and when they pass the worker runs again on the next command of its chain in a round that does not count; otherwise a `blocked` hand-in gets one decision page, unless the worker's revise escalated in that round: its page is the page) |
| `worker_simple` | `ticket_issuer.py` | instead of `worker` when the ticket has `complexity: simple` | sonnet medium | the same as `worker` |
| `verifier` | `ticket_issuer.py` | at the same time as the worker, for tickets that need one | opus medium | the acceptance cases (marked as described below) on the branch `t/<id>-cases`; hand-in `cases` |
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
- devloop calls only `tests.preflight`, the optional `tests.quick`, `tests.run` and `tests.full` (it
  does not use `select` or `regression`). `full` is the regression and must run every layer the
  project has (unit tests, a separate verification layer, every browser engine). `tests.quick`, when
  declared, is a fast subset the regression runs between preflight and full: a red (or env /
  unmeasured) quick ends the regression with a summary starting `quick: ` and full does not run;
  undeclared it is a built-in no-op. `run <id>...` must accept every id that `full` or `quick`
  reports in result.json, because the issuer re-runs the verifier's cases by id (red check, case
  runs) and the regression reruns a red stage's failing ids.
- Flaky cases (#109): when quick or full ends red with 1 to 5 failing rows, the regression runs exactly
  those ids once more (one `run <ids>`, same worktree and slot, within the time left; logs in
  `runs/regression/<branch>/<stage>-rerun`). An id is flaky when that rerun answered (rc 0 or 1) and
  every row of it or nested under it passed. All flaky: the stage is green (a flaky quick goes on to
  full); some still red: red with only those in failures and summary, so the fixer sees only them.
  6 or more failing rows, or a rerun that cannot answer (rc 86 / 2): no rerun effect. Every report
  has `flaky` ([{"id", "message"}], [] when none); the controller writes a `flaky:` group event for
  a non-empty one, and the landed page, the release-failed page and both "still red after N fix
  rounds" pages end with `flaky (passed when run again): <id> (<message>); ...`. Before such a page
  (once per landing or stuck ending) the controller has the opener open one fix ticket (origin
  `{"kind": "flaky", "group", "ids"}`) for the flaky ids no open fix ticket carries (open: any state
  but landed or cancelled), writes one `flaky fix` group event, and the flaky line names the ticket:
  `... - fix ticket: #12, #9 (already open)` (or `none opened (<problem>)`). The rerun is the
  gate's one second look, not a fix: agents still must not hide a flaky test with sleeps or
  retries in the test. The verifier writes its cases into existing test modules of the layer that covers the
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
  regression waiting for the whole machine, `preflight`, `quick`, `full`; without `machine_slots` it starts at
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
first-pass share, user interventions per ticket, what waits for the user: the unread pages other than
`created` ones, plus stuck tickets), every ticket, every
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

DAG draws one lane per group; the lane's title is the group id, its state, what runs for it, the
last regression and the member count (`<n> 張`, `data-members`), and for a group in the chain
(`core/chain.order()`: not landed or cancelled) its place in it, `鏈上第 <k> 個` (`data-place`), then,
when an earlier group still holds the chain (`core/chain.waits_for()`), `等 <gM> 落地` (`data-waits`).

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
| a page option on landed group gN | 重跑發版 | `tickets.py release gN` |

A decision or env page that carries a question (`question`, `options`, `recommendation`, valid by
`core/pages.problems()`) shows them on its card after the page text: the question, then one button
per option in the page's order, the recommended one first and marked 建議, each with its prepared
note in an editable box (kept as a draft like the free-text box; no box for 重跑發版 and 知道了) and its
consequence under it, then the recommendation. An option posts `act` with its action and
`option=<1-4>` and is redirected to `…&sent=act-<subject>-<page>-o<option>`; a box still holding its
prepared note does not hold the 30 s reload. Read-only viewers see the same text without buttons.
The free-text box and its buttons stay below.

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
- **Release exit codes**: 0 released · 1 the product failed a check (at landing: one fix ticket from
  the opener, then one rerun on the same sha, issue #1) · 86 the environment (one decision page). Any
  other code and a timeout count as 86: one decision page, no ticket, no rerun. `tickets.py release gN`
  always runs once.
- **A merged or landed ticket is frozen**: nobody revises or reopens it (`ticket_creator.py
  --revise` from any caller and `tickets.py ready` refuse with one line, D-089); overturning one takes a
  new ticket through `actl new --request "..."`, which may revert it.
- **Pages**: a decision or env page may carry `question` (one line, at most 200 characters),
  `options` and `recommendation` (one line, the reason for the recommendation), all three or none
  (`core/pages.py`). Each option is `{action, note, consequence}`; there are 2-4 of them, or exactly
  one when it is `ack`, and the recommended one is first. Actions: revise, retry, cancel (a ticket),
  group-ready, group-cancel, rerun, release (a group), ack (any page); each maps to one answer
  command that inbox prints. `Pool.report` checks the set when the page is written: a bad set is
  dropped and the text gets a last line `(options dropped: <the first problem>)`.
- **Pages close with their subject**: an unread decision or env page whose `ref` is a ticket or group
  is acked (`acked_at`, `acked_by`, no event) in the same pool write that handles that subject; the
  `--page` page keeps `--by` (else `answer`). `tickets.py ready N|gN` acks as `tickets ready`,
  `cancel N|gN` as `tickets cancel` (with each member ticket it cancels), `rerun gN` as `tickets rerun`,
  `release gN` as `tickets release` (pages that existed when it started); `ticket_creator.py --revise N
  --from user|main --decision retry|cancel`, and `revise` ending ready or escalated, as `new --revise`
  (pages that existed when it started; its own question stays unread). The controller acks as
  `superseded` when it dispatches a ready ticket (the ticket's pages), starts a regression or fixer
  job for a group (the group's pages), lands a group (the group's and each landed ticket's pages that
  existed when the landing began; its push-failed and release pages stay unread), and sets a group
  cancelled (the group's pages). Created, landed, cancelled and idle pages are never acked this way.

## Tests

All loop tests run with `python3 -m unittest discover -s tests_devloop -t tests_devloop`; one area
runs with `python3 -m unittest tests_devloop.test_<area>` from the repo root — a throwaway project,
pool, fake agents and fake tests behind the real test interface; about 730 seconds in one process
(measured 2026-11-21); `full` runs one process per file. Shared fixtures live in
`tests_devloop/loop_harness.py`. A ticket's cases go in its area's file or a new
`tests_devloop/test_<area>.py`; a test file never imports another test file and never subclasses a test
class from another file (inheriting a test class re-runs its cases).
