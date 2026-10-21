# EVIDENCE — #146 r2 (cloud): bring t146 onto current main

- Source: `origin/t146-cloud~1` (77f591a; its last commit `t146: EVIDENCE` dropped), originally based on 685af04.
- Method: **rebase** onto `origin/main` = ac3fb4d (after #139, #142, #143, #145 landed).
- Result: `t146-r2-cloud` = ac3fb4d + 1eb0a28 (product change) + 748151c (verify cases) + this EVIDENCE commit.
- Second commit (verify cases) rebased without conflict; all conflicts were in the first commit.

## Conflict resolution (per file)

Rule: keep everything main already landed; re-apply #146's intent on top; do not re-add anything main deleted.

| File | Main side (kept) | #146 intent applied |
|---|---|---|
| `board/README.md` | #143 wording: reads `tickets/*.json`, `board/events.jsonl`, `reports/inbox/` dir; VISION four metrics item | Removed only the `「哪些保證還只在演練裡成立」` (REHEARSAL) item. Did **not** take #146's old "票與依賴" line (it predates the VISION metrics). |
| `board/board.py` | Docstring ④ line that reads `<reports_dir>/inbox/` dir (`scripts/inbox.py`) — #143 | Dropped ⑤ REHEARSAL docstring line. Did **not** take #146's `inbox/index.jsonl` wording (main deleted index). REHEARSAL code (render_rehearsal / rehearsal_rows / REHEARSAL_REL / state cell / NAV) auto-merged out; `grep -i rehearsal board/board.py` → 0. |
| `scripts/auto-fix.sh` | #145 batch-path prompt (worktree, branch commits, EVIDENCE outside repo) | Kept main's block; dropped #146's `base/…`/`work/…` header line (main removed patch diffs). Changed "必備五段(見角色卡)" → "必備四段(見角色卡;變異表選填)" so the batch prompt matches the other three prompts. |
| `tests/test_board.py` | — | Dropped main's two REHEARSAL tests (ticket test_plan: delete). Dropped #146-side metrics tests (`..._draws_the_metrics_...`, `..._carries_the_metrics_...`) — main deleted them with metrics in #143; not re-added. |
| `tests/test_ticket.py` | rest of file | Took #146's `ReadyWithoutNeedsVerifier` (missing/odd needs_verifier all go to Ready, ticket bytes not filled in, Blocked→Ready passes); main's `ReadyNeedsABooleanNeedsVerifier` and its "checked / not checked" cases removed. |
| `tickets/SCHEMA.md` | `verify.baseline` note "閘門量 check;close 不讀(D-054 Q4,#139)"; #142 objections row; #143 interventions/cost rows | `needs_verifier` row → 選, missing = true, no Ready block, no lint; `interface_fixed` → 選; `needs_verifier` history line → D-054 Q10 wording. |

Auto-merged without conflict (checked by reading the diff vs main, no main-deleted strings re-added — grep of added lines for `index.jsonl|metrics|patch-round|acked|ticket.cost` → 0): `scripts/ticket.py`, `scripts/gate.sh`, `tests/test_apply.py`, `tests/test_auto_fix.py`, `tests/test_gate.py`, `tests/control_harness.py`, docs, memory cards, code-map, verify/*.

## Acceptance grep

```
$ git grep -nE 'needs_verifier_problem|ReadyNeedsABooleanNeedsVerifier|render_rehearsal|rehearsal_rows|REHEARSAL_REL|check-stale\.py|test_check_stale|\bbudget\b' -- scripts board tests verify templates/dispatch-*.md docs/WORKFLOW.md docs/ROLES.md docs/DISPATCH-TEMPLATE.md docs/CODE-MAP.md docs/DESIGN.md docs/reference tickets/SCHEMA.md README.md CLAUDE.md memory/role/*.md code-map/catalog.json code-map/index.json
(no output) grep_rc=1
```
`code-map/check-stale.py` and `tests/test_check_stale.py` deleted.

## Test runs (on 748151c, run as uid 0 in the cloud container)

```
== test_board
Ran 28 tests in 6.141s
OK
== test_ticket
Ran 109 tests in 24.524s
OK
== test_auto_fix
Ran 153 tests in 366.071s
OK
== test_apply
Ran 54 tests in 31.842s
FAILED (failures=1)
== test_gate
Ran 49 tests in 23.381s
OK
```

The one `test_apply` failure is **environmental, not this branch**:

```
FAIL: test_an_objection_that_cannot_be_recorded_is_not_called_already_there (tests.test_apply.TheStructuredDeliveryOfTheFirstRound...)
AssertionError: '記不進票' not found in 'apply: ... #1 已記進 objections[] ...'
```

The case makes `tickets/` read-only with `chmod 0o555`; this container runs as root (`id -u` = 0), so the write still succeeds. Same case on a clean export of `origin/main` (ac3fb4d, `git archive` into scratch) → same failure (`Ran 1 test ... FAILED (failures=1)`). Expected to pass on a non-root runner. Not touched.

## Token

未知(雲端 session,拿不到用量)。

No objection: the ticket's intent applied cleanly on current main.
