# Dev loop v2 — one driver, four stages, one state

Status: **draft for the user's review (D-078)**. Nothing here is built yet. Supersedes the
per-ticket branch gate, auto-fix / apply / gate / review / schedule as separate drivers, and every
mechanism D-078 ① deletes. The tool interface of `docs/design/project-runnable-entries.md`
(`actl`, `board/config.json` versioning, the five-command test interface, hooks) stays as is, so a
project that already migrated (T) keeps its config and `scripts/ac-tests.sh`.

User's words this design answers (D-078): 「要的是幾張做完一起跑一次大驗證」「agent-control 主要支援2種謹慎度
dag 每票都全驗 跟 dag 一堆一起驗」「開發迴圈的邏輯切乾淨 哪個時候call 啥腳本要明確」「邏輯乾淨 互動介面單純
是最重要的指標,不要生出一堆例外。遇到例外 優先想怎麼改流程」. The loop itself is VISION's 「一張需求從頭到尾」
ten steps; every step below names the one it implements.

## 0. Rules every script follows

1. **One driver.** `actl run` is the only thing that moves tickets on its own. It calls the four
   stages (`dispatch`, `done`, `land`, and the agents they start). No stage calls another stage,
   no agent calls a stage, no script polls another script.
2. **A stage is one command on ticket ids.** It reads the ticket JSON, does one thing, writes the
   ticket JSON, appends logs under `reports/t<n>/`, and exits. Running it twice on the same input
   does nothing the second time. Exit codes are the same for every stage:
   `0` advanced · `1` sent back for another round · `3` blocked, a decision page was written ·
   `2` refused (usage, lock held, wrong state; nothing changed).
3. **One state.** The ticket JSON is the only state (VISION 「同一件事存好幾份」不要). Logs, test
   output and agent transcripts are never read to decide anything. The board reads tickets.
4. **One failure rule.** Anything that fails on a ticket — no hand-in, red tests, review fail, merge
   conflict — is *feedback*: it is appended to the ticket's current round and the ticket goes back
   for round + 1. Round > `max_rounds` (default 3, D-050 ②) → Blocked. There is no second kind of
   failure handling. Something that is not the ticket's fault (the environment) stops the run, not
   the ticket (§3.3 step 1).
5. **Agents only do work.** Worker, verifier, reviewer and opener are started by `dispatch`, work in
   their own worktree, commit on their own branch (worker, verifier), write `result.json` +
   `EVIDENCE.md` in the run dir, and exit. They never write tickets, never touch main, never call
   `actl` stages.
6. **An exception is a flow bug.** When a case does not fit these rules, change the flow so it fits;
   do not add a branch, a flag or a special page for it.

## 1. Ticket states

```
Draft ──ready──▶ Ready ──dispatch──▶ Working ──done──▶ Built ──land──▶ Landed
                   ▲                    │                 │
                   └──── feedback ◀─────┴───── feedback ◀─┘
                   Blocked (decision) ◀── round > max_rounds, opener asks, hard_block touched
                   Cancelled
```

| State | Means | Who moves it out |
| --- | --- | --- |
| Draft | being written | opener / main: `actl ticket ready <n>` (lint must pass) |
| Ready | dispatchable; `next` says which agent: `work`, `review` or `opener` | driver → `dispatch` |
| Working | agents of the current round are running | driver → `done` once they all exited |
| Built | hand-in accepted (and reviewed if complex) at branch head `built_sha` | driver → `land` |
| Landed | on main, `landed_sha` set | — |
| Blocked | needs the user; one decision page | main after the user decides: `actl ticket ready <n>` |
| Cancelled | dropped | — |

Fields the loop uses (everything else on the ticket is the opener's text): `state`, `next`,
`round`, `rounds[]` (per round: agents with model / pid / boot id / run dir, `feedback[]`,
`result`), `depends_on[{id, condition: "landed"}]`, `complexity` (`complex` → review),
`needs_verifier` (missing = true), `branch` (`t<n>`), `verifier_branch` (`t<n>-v`), `base_sha`,
`built_sha`, `landed_sha`, `cost[]`, `blocked{reason, page}`.

## 2. The driver: `actl run`

Reads every ticket and `board/batches.json`, then loops `tick()` until nothing is running and
nothing can move; then writes **one** inbox page ("run finished: landed …, blocked …") and exits.
Running `actl run` again after a crash or a restart resumes from the tickets: there is no other
state file.

`tick()` does three things, in this order:

1. **Finish rounds.** Each Working ticket whose agents have all exited (result written, or pid +
   boot id gone) → `actl done <n>`.
2. **Land.** For each batch in file order:
   - mode `ticket`: every Built ticket whose `depends_on` are all Landed → `actl land <n>`, one
     ticket at a time.
   - mode `batch`: once every ticket of the batch is Built, Blocked or Cancelled and at least one
     is Built → `actl land <all Built tickets of the batch>`.
3. **Dispatch.** Ready tickets that are in a batch and whose `depends_on` are all Landed, up to
   `schedule.max_workers` running agents → `actl dispatch <n>`.

Then it waits for a child to exit (or 60 s) and ticks again.

`board/batches.json` (written by main from the user's words, D-074):

```json
{"schema": 2, "mode": "batch",
 "batches": [{"id": "b1", "tickets": ["201", "202"], "mode": "ticket"},
             {"id": "b2", "tickets": ["203", "204", "205"]}]}
```

`mode` = the two caution levels of D-078 ②: `ticket` (每票都全驗: verify and land each ticket on its
own) or `batch` (一堆一起驗: verify the whole batch once, land it together). File-level `mode` is
the default, a batch may override it. A ticket that is in no batch is never dispatched.

## 3. The stages

### 3.1 `actl dispatch <n>` — VISION 4, 5

Precondition: Ready. Starts what `next` says, records pids in `rounds[round]`, sets Working, exits 0.

- `next = work`, round 1: create worktree `t<n>` from main HEAD (= `base_sha`); if
  `needs_verifier` is not false, also worktree `t<n>-v` from the same sha. Start worker and verifier
  together.
- `next = work`, round ≥ 2: start a worker in the existing `t<n>` worktree (and a verifier in
  `t<n>-v` only when the feedback is addressed to the verifier).
- `next = review`: start the reviewer read-only on `t<n>` at its head.
- `next = opener`: start the opener on the ticket.

The packet is the same shape for every agent: role card path + ticket path + four facts (ticket id,
base sha, worktree, run dir) + for round ≥ 2 the previous round's feedback and the branch diff
summary (VISION 8: 「輸入票跟 diff 跟 bug」). No rules pack. Model = the first entry of
`models.<role>` in `board/config.json` that is available (D-049 ②: an ordered list); nothing else
chooses models.

Agent contracts (each one line in its role card):

| Agent | Does | Hands in |
| --- | --- | --- |
| worker | implements; merges `t<n>-v` if it exists; runs only the verifier's cases + the base set (D-073) | commits on `t<n>`; `result.json {kind: "work" \| "objection", summary, objection?}` |
| verifier | writes the ticket's cases into existing test modules (D-056); runs them on its own branch base and sees them red | commits on `t<n>-v`; `result.json {kind: "cases", files, run}` |
| reviewer | reads the branch against the ticket (VISION 9) | `result.json {kind: "review", verdict: "pass" \| "fail", feedback[]}` |
| opener | judges a worker objection (D-054 Q8) | edits the ticket text; `result.json {kind: "ticket", outcome: "fixed" \| "decision", question?}` |

### 3.2 `actl done <n>` — VISION 6

Precondition: Working and all agents of the round exited. Mechanical checks in this order; the first
one that does not pass decides the outcome:

1. A `result.json` for every agent started → else feedback "no hand-in" (round + 1).
2. Reviewer result: `pass` → Built (`built_sha` = head); `fail` → its feedback, round + 1.
3. Opener result: `fixed` → Ready, `next = work`, same round count; `decision` → Blocked with the
   opener's question.
4. Worker `objection` → Ready, `next = opener`.
5. The branch touches a `hard_block` path → Blocked ("needs your decision: <path>").
6. Verifier cases (needs_verifier): run the verifier's case files on `base_sha`; every case must
   fail by assertion (the D-020 rule) → else feedback to the verifier.
7. Merge `t<n>-v` into `t<n>` (script, `--no-ff`); conflict → feedback to the worker ("merge the
   verifier branch").
8. Main moved during the round by a commit that is not a land or a docs-channel commit → recorded in
   the round (D-064: record, don't block).
9. `complexity = complex` and no passing review at this head → Ready, `next = review`.
10. Otherwise → Built.

No tests run here except step 6: verifying is `land`'s job (§3.3).

### 3.3 `actl land <n…>` — VISION 7, 8, 10

Precondition: every ticket given is Built. Holds the main lock for the whole command (`actl docs`
waits on the same lock: one writer of main at a time).

One rule for the outcome: **`land` lands every ticket it was given, or none of them.** Tickets that
caused the failure get feedback; the others stay Built, and the driver lands them again on a later
tick — in `batch` mode when the fed-back tickets are Built again (or Blocked), in `ticket` mode there
is only one ticket per call anyway.

1. **Preflight** (the project's test interface `preflight`). rc 86 → nothing changes on any ticket;
   exit 3 with one decision page "environment: <reason>". The run stops until `actl run` is called
   again. This replaces the environment-suspect re-run and its diagnosis round.
2. **Candidate**: a worktree at main HEAD; merge the tickets' `built_sha` in DAG order. Any ticket
   that conflicts → feedback ("conflicts with main / with #m"); exit 1, nothing lands.
3. **One full suite** on the candidate (the test interface `full`, parallel by cores and free
   memory, D-074). This is the only verification run in the loop (D-052, D-078).
4. **Green** → fast-forward main to the candidate, push, each ticket Landed (`landed_sha`), exit 0.
5. **Red, one ticket** → the red list is that ticket's feedback, round + 1, exit 1.
6. **Red, several tickets** → attribute by running, not by guessing: run only the failing cases on
   main + each ticket alone. Tickets that reproduce get the red list as feedback; exit 1. If none
   reproduced (the red only happens together), the batch's mode becomes `ticket` in
   `batches.json` and the driver lands its tickets one by one. This replaces the unattributed-red
   diagnosis round and batch_fix.

## 4. What main and the user type

| Command | When |
| --- | --- |
| `actl ticket new / edit / ready / cancel / show / list / lint` | opening tickets; `new --after <n>` writes `depends_on` with `landed` (D-074 「開題者…插入dag」) |
| edit `board/batches.json` | the user says what to run and how carefully |
| `actl run` | start or resume the loop; it returns when everything landed or waits on the user |
| `actl inbox` | read the pages: only "decision" and "run finished" exist (D-032) |
| `actl docs "<msg>" <files>` | main's docs channel (tickets/, docs/, memory/, discussions/, setup files) |
| `actl board` | the dashboard, reading tickets |
| `actl memory`, `actl doctor / migrate / init / hooks / test`, `actl version` | unchanged |

## 5. What disappears

| Today | v2 |
| --- | --- |
| `auto-fix.sh`, `apply.sh`, `review.sh`, `schedule.py` (four drivers) | `run` + `dispatch` + `done` |
| `gate.sh --branch` per ticket, `verify.py`, `verify/`, the #170 regression layer | `land`'s one full suite; verifier cases live in `tests/` |
| `verify-case.py` red/check, patch-verify, three-way merge, the #182 ref tree | `done` step 6 runs the verifier's cases on `base_sha`; the verifier commits on `t<n>-v` |
| `status.py` status files, flaky ledger / auto-tickets / order-dependence, `reports/schedule/state.json`, ticket state in `events.jsonl` | the ticket JSON; logs are logs |
| batch_fix, diagnosis round, environment re-run, re-land counting | `land` steps 1, 2, 6 |
| `rules.py` pack | role card + ticket + four facts + feedback |
| role-model chain, effort escalation, codex / agy fallback | `models.<role>`: first available |
| `heartbeat` reconcile, `liveness` | `run` tick step 1 (a dead agent is a round without a hand-in) |
| `main_guard.py` | `done` step 5 (hard_block) and step 8 (record) |
| inbox page kinds beyond two | decision, run finished |

## 6. Open questions for the user

1. **Flaky tests.** With one failure rule, a flaky red is feedback to the worker like any red; the
   worker can object (→ opener). No flaky bookkeeping. Accept?
2. **D-076 「125 要」.** The environment re-run and the diagnosis round are replaced by preflight
   (§3.3 step 1) and attribution by running (§3.3 step 6). Accept?
3. **Default mode** in `batches.json`: `batch` (proposed, it is what D-052 / D-078 ask for) or
   `ticket`?
4. **main_guard.** Kept only as `done` steps 5 and 8 (hard_block blocks, other main moves are
   recorded). The main-repo `git status` comparison of D-065 goes away. Accept?
5. **#188, #189.** #188 adds an objection category (an exception by D-078 ③); #189 adds rules to the
   role cards. Proposed: cancel #188, re-open #189 after v2 against the new role cards.

## 7. How to get there

The current loop builds v2, in `ticket` mode (each ticket changes the loop itself):

1. Ticket JSON v2 (`next`, `rounds[]`, `feedback`, `built_sha`, states) + `actl ticket` + a one-off
   migration of existing tickets.
2. `dispatch` (worktrees incl. `t<n>-v`, packet, `models.<role>`).
3. `done` (§3.2).
4. `land` v2 (§3.3).
5. `run` (§2, `batches.json` schema 2).
6. Cut over: delete everything in §5's left column; rewrite CLAUDE.md, the role cards,
   DISPATCH-TEMPLATE and WORKFLOW to describe only this loop (one text each); board reads v2 tickets.

T's migration is not wasted: T's config, `scripts/ac-tests.sh` and hooks are the interface v2 uses.
T should stop at its current step (no `actl land` rehearsal on the old loop) until step 6 lands.
