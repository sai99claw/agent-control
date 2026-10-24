# D-069 ①② — A's entry points run from a project; one sync list with computed dependencies

Design session (Fable, 2026-10-24). A = agent-control (this repo, the master), T = the downstream
project that reported the problems (its own `land-ticket.sh`, `board.py`, `test-for.sh`,
`verify-refs.py`). Every factual claim is tagged **measured** (read or run in this session, in A)
or **inferred** (read from code, not executed) or **T-reported** (from T's report; nothing in T
was read here). Every trade-off carries a "per future ticket: saves / costs" line (D-018, rough,
inferred). Fixed sections follow `docs/DESIGN.md` §設計文件的固定段; the five questions from the
dispatch are answered in §1–§5 inside section ② / ④.

## ① Origin

User's words (D-069, 2026-10-24, verbatim): 「分別啥影響? 你能分析優劣然後 決定嗎? 這些問題是有啥設計取捨嗎?」
— the user authorised main to analyse and rule. Main's ruling ① (verbatim): 「方向 1「A 的入口都能從專案跑」:採用。
… A 補:land.sh / gate.sh / 看板的控制腳本路徑走 $AC 並進同步清單;gate 選填 `gate.select_cmd`(專案只提供挑測試與怎麼跑,
preflight 回 86 走既有 env_suspect);verify.py `--case`;看板認 AC_CONTROL_DIR 與選填 `board.key_file`。專案只留專案知識
(挑測試、瀏覽器引擎層、preflight、build / deploy)。」 Ruling ② (verbatim): 「同步清單:留在 hard_block … SCRIPT_LIST 與
DEPENDENCY_ONLY 合成一份,依賴由 import 關係自動算,同步時發出去的腳本缺依賴就失敗(不只警告)。只有新增入口才需授權」.

Symptoms:
- T-reported: T wrote its own lander `scripts/land-ticket.sh` (2,601 lines) and `board.py` (5,711 lines); after syncing
  A 08e6752, 8 things broke, 6 of them glue that no longer matched A's interface (review / objection checks silently off).
- Measured: `scripts/land.sh` has 18 `$ROOT/scripts/*` call sites (lines 113–888) plus `. "$ROOT/scripts/wtbase.sh"` (540);
  `scripts/gate.sh` has 17 `$ROOT/scripts/*` sites (467–971) and computes `ROOT=$(cd "$(dirname "$0")/.." && pwd)` (114),
  which from `<project>/scripts/control/` resolves to `<project>/scripts`, not the project root.
- Measured: `review.sh:53-54`, `apply.sh:60-61`, `auto-fix.sh:79-80`, `heartbeat.sh:32-33`, `consolidate-memory.sh:44-45`
  already use `AC=${AC_CONTROL_DIR:-$(dirname "$0")}` and `export AC_CONTROL_DIR`; `apply.sh:57` resolves `ROOT` by
  walking up to `board/config.json` (`_ac_root`, `AC_ROOT` overrides). `gate.example.sh:88` has the `$AC` form too.
- Measured: `board/board.py:48` inserts `dirname(HERE)/scripts` into `sys.path` — from `scripts/control/` that is
  `<project>/scripts/scripts`; `board.py` is not in `SCRIPT_LIST` at all; bind is `127.0.0.1` only (1210); `/healthz` exists (1109);
  there is no key / cookie code anywhere in A (grep `key_file|cookie|?key=`: 0 hits).
- Measured: `sync-to-project.sh:102` `SCRIPT_LIST` (20 names) and `:113` `DEPENDENCY_ONLY` (6 names) are two hand-written
  lists; a missing dependency is only detectable by reading the comments (18–20, 103–113). The wiring check (427–448) and the
  fork check (474–482) both iterate `SCRIPT_LIST`.
- Measured (ran an AST / regex scan over `scripts/`, see §2 for the rule): the import closure of today's entry points is
  computable; `verify.py` imports `status.py`, which imports `event.py` + `ticket.py`; `ticket.py` imports `verify.py` (a cycle,
  harmless for closure); `status.py:898` launches `auto-fix.sh` by path next to `__file__`; shell scripts reference siblings
  as `$AC/<name>`, `$ROOT/scripts/<name>`, `. "$AC/wtbase.sh"` and `import <mod>` inside python heredocs.
- Measured: `status.py run-tests` (273–316) runs **in-process unittest** from `<root>/tests` (`--mode discover|names`), so the
  statistical environment-suspect detection and rc 86 only exist for unittest modules it loads itself. Declared suspects are
  also parsed from any log by `done --log` (`parse_environment_suspects`, 534+).
- Measured: `verify.py` selects cases only by `--tag` (119, 154); T's `test-for.sh` wants to name case files found via its
  `verify-refs.py` reverse lookup (T-reported).
- Measured: `land.sh:645-646` runs the full gate as `cd "$WT" && … sh scripts/gate.sh --full` — the gate **from the tree under
  test**, not from `$ROOT`. This must survive (the merged tree may change the gate).

## ② Conclusions (one trade-off per row)

| # | Conclusion | Why | Per future ticket: saves / costs (inferred) |
|---|---|---|---|
| C1 | **One control dir, one rule for roots.** Every synced shell script starts with the same 4 lines: `ROOT=${AC_ROOT:-$(_ac_root)}` (walk up from `$0` to `board/config.json`), `AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}`, `export AC_CONTROL_DIR=$AC`; siblings are called only as `"$AC/<name>"`. `gate.sh` and `land.sh` adopt it; `$ROOT/scripts/` is forbidden in synced scripts (pinned by a test, ⑤ V1). | `apply.sh:57-61` is the already-working shape; two shapes for the same thing is what D-018 bans. | saves: T's glue layer (T-reported 6 of 8 breakages) disappears per sync; costs: 0 per ticket. |
| C2 | **gate.sh keeps two roots**: code root `ROOT` = the tree the script file lives in (walk up from `$0`); ticket root = `event.repo_root()` (`AC_ROOT`). `land.sh` keeps calling the gate **inside the worktree** via `$WT/$AC_REL/gate.sh` where `AC_REL` = `$AC` relative to `main_root` (`scripts` in A, `scripts/control` in T). | Same split `verify.py` already has (#55, docstring 26–31); land must test the merged tree's own gate. | saves: a wrong-tree gate run is a whole land round (~30–80K tokens); costs: 0. |
| C3 | **`gate.select_cmd` (optional config key) replaces A's hard-coded `map()`/`BASE` when set.** Contract in §1 below. Without it, `gate.sh` behaves exactly as today (A's own table). It is not a new gate: it is the existing gate's selection step made pluggable (D-053 ④ respected). | The 32-row `map()` (measured, gate.sh 136–200) is A knowledge; the project's only contribution is "which tests guard which files, how to pick them, and what to check before running" — which is what D-069 ① says the project keeps. | saves: T stops maintaining a 2,601-line lander to own one `case` table (T-reported); costs: one extra process per gate run (~0 tokens, <1 s). |
| C4 | **Preflight lives inside `select_cmd`**: exit 86 + a JSON list on stdout in the `environment_suspect` shape (`source:"declared"`, D-019) → gate writes it to `SUSPECT_FILE`, takes the existing `env_suspect_note` path (no flake rerun, no auto-fix, one decision page). No new config key, no new gate. | gate.sh already has that whole path (987–988, 1037–1038, 561–569); the project just needs a way to say "the machine is bad before you start". | saves: a worker round dispatched into a locked screen / orphan Safari / full disk (D-019 §理由 5); costs: 0. |
| C5 | **"How to run" stays in A**: `status.py run-tests --mode names` loads what `select_cmd` printed; the only project knob is `gate.tests_dir` (default `tests`) for where unittest names resolve. A `run: <command>` line form is **rejected** (③ 丙). | Keeps statistical env-suspect + per-case red lists + flake rerun working for projects; the browser layer is "which modules", not "which runner" (T's cases are unittest: `verify.py` and `land-ticket.sh gate` both call `python -m unittest`, T-reported via D-020 C5). | saves: a project never re-implements the red-list parser; costs: a project whose tests are not unittest-loadable cannot use `select_cmd` (none known; **?** for T, see ⑥ Q1). |
| C6 | **Board**: `board.py` enters the sync list (lands in `scripts/control/`), resolves its control dir as `AC_CONTROL_DIR` → `HERE` if `event.py` sits beside it → `HERE/../scripts`; optional `board.key_file`: when set, every path except `/healthz` requires the key, `?key=<k>` on any path sets an `HttpOnly` cookie and redirects to the same path without the query, cookie or `?key=` compared with `hmac.compare_digest`, failure = 401 with an empty body. Bind stays `127.0.0.1`; exposure is the project's reverse proxy / tunnel (**?** ⑥ Q2). | T-reported need; A's board has no auth at all (measured), so the project forked 5,711 lines to add one. Key in a file (not in `config.json`) because `config.json` is synced-adjacent and lands in git. | saves: the fork; costs: 0 per ticket (one more `if` per request). |
| C7 | **`verify.py --case <path|dotted>`** (repeatable, mixes with `--tag`): selects exactly those case files; unregistered tags still fail (rc 2); nothing selected is still rc 3; cache key includes the sorted cases. | T's `test-for.sh` already knows the files (via `verify-refs.py`); forcing it through tags means a tag per file. | saves: T's per-ticket "which tags cover this file" guess (T-reported); costs: 0. |
| C8 | **One sync list `ENTRIES`** (entry points only) + dependencies **computed** (rule in §2); a computed dependency that is not in `scripts/` fails the sync with rc 2 naming `<caller> → <missing>`; the "uncalled" and "fork" checks iterate `ENTRIES` only; `.sync-manifest` records the closure. `sync-to-project.sh` stays in `hard_block.paths` (D-069 ②): adding an entry is a human-authorised change; the closure is not. | Two hand lists drift (the file already needs 11 comment lines to explain why 3 names are where they are, measured 103–113). | saves: one silent "import explodes in the project" per missed dependency (today found at T's first run, T-reported "8 處壞"); costs: ~0.5 s per sync. |
| C9 | **Compatibility period is T's choice per ticket, not a flag in A** (§3): T keeps `land-ticket.sh` on disk, lands with `scripts/control/land.sh` for 3 consecutive tickets (D-017 threshold), then retires its own lander in a docs ticket. Both landers must share the lock (`AC_LAND_LOCK` → T's existing lock path) during the period. Rollback = call the old script; no data migrates. | Tickets, events and reports are already A's formats in T (synced `ticket.py` / `event.py`); only the lander changes. | saves: nothing to migrate; costs: during the period a human types a different command. |
| C10 | **A tests "run from a project" with the real sync output** (§4): a harness layout where the sandbox's control scripts live in `scripts/control/` (copied by `sync-to-project.sh` itself), a `select_cmd` fixture, and the existing land / gate / board tests re-run in that layout through one env var. Plus a static test that greps synced entries for `$ROOT/scripts/`. | D-015 §5.6: do not build a simulator of T; the thing to test is A's own scripts from a different directory. | saves: the next "works in A, breaks in T" round (one sync + one T debugging session); costs: test suite +~20 % runtime (inferred), 0 tokens per ticket. |
| C11 | **Not now** (⑤ has no row for these; ⑥ lists none): binding the board to `0.0.0.0`; a `run:` command mode in `select_cmd`; moving `templates/` / `verify/_template_ticket.py` into the computed closure; syncing `VISION.md`; auto-running sync; touching T's scripts from A. | See §5. | — |

### §1 Interface between project and A

**Config keys (`board/config.json`, all optional):**

| Key | Type | Meaning | Read by |
|---|---|---|---|
| `gate.select_cmd` | string (shell) | Project's test selector + env preflight. Absent → A's built-in `map()`. | `gate.sh` |
| `gate.tests_dir` | string, default `tests` | Directory unittest names resolve from (`status.py run-tests` cwd + `sys.path`). | `status.py run-tests` (new `--tests-dir`), `gate.sh` |
| `board.key_file` | path (relative to repo root) | File whose first line is the access key. Absent → no auth (today's behaviour). | `board.py` |
| `main_branch`, `tickets_dir`, `reports_dir`, `worktree_dir`, `hard_block`, … | existing | unchanged | |

**Environment variables (already exist; now honoured by every synced script):** `AC_ROOT` (ticket root override),
`AC_CONTROL_DIR` (where the sibling scripts are; set and exported by whichever script runs first), `AC_LAND_LOCK`, `AC_GATE_*`.

**`gate.select_cmd` contract.** gate.sh runs `sh -c "$select_cmd" select <mode>` with:
- `<mode>` ∈ `branch | base | full | files`; for `files` and `branch` the candidate file list is on **stdin**, one path per line,
  repo-relative (for `branch` gate.sh computes it exactly as today, lines 1006–1008); for `base` and `full` stdin is empty.
- env: `AC_ROOT`, `AC_CONTROL_DIR`, `AC_GATE_MODE=<mode>`, `AC_TICKET` (may be empty), `AC_GATE_RUN_ID`; cwd = code root.
- **stdout, rc 0**: one item per line; `<unittest-name>` (module or dotted `module.Class.test`, resolvable from `gate.tests_dir`)
  or `unmapped <file>` (no test guards this file). gate.sh dedups names, runs them via `run-tests names`, and if any
  `unmapped` line exists keeps today's semantics (run what mapped, exit 3, `ticket_gate 3`). For `full` the selector may print
  the single word `discover` meaning "everything under `tests_dir`" (the default when `select_cmd` is absent).
- **rc 86**: environment preflight failed; stdout is a JSON list of `environment_suspect` rows (D-019 seven keys, `source:"declared"`,
  `log` null). gate.sh writes it to `SUSPECT_FILE`, calls `env_suspect_note`, `status_done 86`, no `flake_rerun`, no regression,
  no `verify_case`, no auto-fix, inbox decision page as today. (The selector is where the project checks lock screen / orphan
  Safari / free disk; A never learns what those are.)
- **rc 3**: nothing selectable (selector's own "no tests for these files") → gate.sh prints the selector's stderr and exits 3.
- any other rc: gate.sh exits 2 ("selector failed", stderr passed through). Empty stdout with rc 0 = exit 2 as well
  (§5.5: "nothing to run" must not look like green).
- The selector never runs tests, never writes `reports/`, never touches tickets.

**Which A scripts become entries (go into `ENTRIES`):** today's 20 minus the 6 that were `DEPENDENCY_ONLY`, plus
`land.sh`, `gate.sh`, `board.py` (new; one-time authorisation per D-069 ②). `verify.py` stays an entry (#35).
`gate.example.sh` is **not** synced (it is a template for projects that still want their own gate; with `select_cmd` they
no longer need one, and the example is rewritten to show a `select_cmd` instead).

**What a project shim looks like** (lives in the project's own `scripts/`, not in `control/`; this is the entire glue):

```sh
# <project>/scripts/select-tests.sh — gate.select_cmd (project knowledge only)
#!/bin/sh
set -u
mode=$2
# 1. env preflight (lock screen / orphan Safari / disk) — rc 86 + JSON rows
if ! ok=$(sh scripts/preflight.sh 2>&1); then
    printf '[{"source":"declared","engine":"safari","why":%s,"count":1,"threshold":null,"log":null,"line":null}]\n' \
        "$(printf '%s' "$ok" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))')"
    exit 86
fi
# 2. selection — the project's own table (this is what land-ticket.sh / test-for.sh know today)
case "$mode" in
    full) echo discover ;;
    base) echo test_ledger; echo test_invariants ;;
    branch|files)
        while IFS= read -r f; do
            case "$f" in
                demo/*.py)        echo test_demo ;;
                browser/*.py)     echo test_browser_engines ;;
                tests/test_*.py)  echo "$(basename "$f" .py)" ;;
                *.md)             ;;                     # docs: no test, not unmapped (project's call)
                *)                echo "unmapped $f" ;;
            esac
        done ;;
esac
```

```json
{ "gate": { "select_cmd": "sh scripts/select-tests.sh", "tests_dir": "tests" },
  "board": { "key_file": ".board.key" } }
```

Everything else the project used to wrap (`land-ticket.sh gate`, `docs`, review check, objection check, hard_block, close)
is called directly: `sh scripts/control/land.sh t<n>-…`, `sh scripts/control/land.sh docs "…" <files>`,
`sh scripts/control/gate.sh --branch --ticket <n>`, `python3 scripts/control/board.py`.

### §2 One sync list, computed dependencies, missing dependency = failure

- `ENTRIES="apply.sh auto-fix.sh review.sh land.sh gate.sh inbox.py rules.py memory.py status.py verify.py verify-case.py
  session-hook.sh wtbase.sh repo-map.py agent-cost-hook.py main_guard.py board.py"` (one list; `board.py` is copied from
  `board/board.py` into `scripts/control/` — the only entry whose source is not `scripts/`).
- Dependency rule (one python block inside `sync-to-project.sh`, no new file; measured against today's tree, closure below):
  - `*.py`: AST `import X` / `from X import …` where `scripts/X.py` exists (module `_` ↔ file `-`), plus any string literal
    equal to a sibling file name (`status.py:898` `"auto-fix.sh"` is a real launch; the only false positives are
    self-references, which the closure ignores).
  - `*.sh`: on each line **with the `#…` comment stripped** (same stance as the wiring grep, line 443), the pattern
    `(\$AC|\$\{AC\}|\$ROOT/scripts|\$HERE/scripts)/<name>.(py|sh)` and a heredoc line `^\s*(import|from) <module>`, where the
    name is a sibling file. **Bare `scripts/<name>` does not count** — measured: with it, `gate.sh`'s `map()` table
    (`scripts/sync-to-project.sh) add test_sync_to_project`) pulls `sync-to-project.sh` into the closure; without it the
    closure is exactly the one below. After C1 every real call is a `$AC/` call, so the bare form has nothing left to catch.
  - Closure = fixed point over `ENTRIES`; `DEPS = closure − ENTRIES`.
  - Measured closure today (from `ENTRIES` above; the scan was run in this session): adds `event.py ticket.py heartbeat.sh
    new-session.sh transcript-cost.py role-models.py consolidate-memory.sh` — today's `DEPENDENCY_ONLY` plus
    `consolidate-memory.sh`, which `new-session.sh:436/462` calls behind an `[ -f "$AC/consolidate-memory.sh" ]` guard: not a
    crash in T today, but the main session's memory consolidation silently never starts there (inferred).
- **Failure**: any name in the closure with no file in `scripts/` (or `board/board.py`) → `sync: <caller> 需要 scripts/<name>,
  主 repo 沒有 —— 不同步` and `exit 2` **before any file is written** (same stance as the invalid role table, lines 133–138).
  Today's "名單與事實分岔了(先修名單)" `continue` (221) becomes this failure.
- The manifest (`scripts/control/.sync-manifest`) lists the closure; retirement logic (310–322) unchanged.
- The wiring check (427–448) iterates `ENTRIES`; `heartbeat.sh`/`new-session.sh`/`role-models.py` never show as "uncalled" because
  they are not entries — same outcome as today's `DEPENDENCY_ONLY` skip, without the list.
- `templates/` and `verify/_template_ticket.py` keep their own small lists (different destination, different header rules);
  folding them in buys nothing per ticket.

### §3 Compatibility period: T from `land-ticket.sh` to A `land.sh`

Preconditions (A side, all in ⑥): C1/C2/C3/C8 landed and synced; `inbox.py migrate` (D-069 ③) done once in T.

Steps in T (each is a T docs/config ticket or a line in one):
1. Sync (`sh ../agent-control/scripts/sync-to-project.sh . --dry-run` → real). Expect `land.sh gate.sh board.py` new in
   `scripts/control/`; expect the "uncalled" line to name them until step 3.
2. `board/config.json`: add `gate.select_cmd` + `gate.tests_dir`; add `board.key_file` and create the key file
   (git-ignored); confirm `hard_block` is there (HANDOFF item 1); set `worktree_dir` if T's differs from `../<repo>-wt`.
   Point `AC_LAND_LOCK` at the lock `land-ticket.sh` uses today (or make `land-ticket.sh` use `.land.lock`) so the two landers
   cannot run at once.
3. Rehearsal, cheapest first: (a) `sh scripts/control/gate.sh --branch --ticket <n>` on an already-landed branch → compare
   the module list with what `land-ticket.sh gate` picked (the selector table is right when they match); (b) `select_cmd`
   with the screen locked → rc 86 decision page, no worker dispatched; (c) `land.sh docs "…" tickets/<n>.json` on a real
   ticket file; (d) one **simple** ticket end to end with `land.sh`.
4. Three consecutive tickets landed by `land.sh` (D-017 "連續三票" threshold) → docs ticket: delete `land-ticket.sh`, the
   T-side review/objection/hard_block glue, and update T's `CLAUDE.md` 對照表 (`templates/project-CLAUDE.md` rows now say
   `scripts/control/<same name>`).
5. `board.py` (T's) is switched only after (4): run A's board on a second port with `board.key_file`, compare the four VISION
   metrics, then retire T's.

Rollback at any step before 4: run `land-ticket.sh` again — it is still on disk, tickets/events/reports formats did not
change, `base_sha`/`gate`/`review` fields are written by the same synced `ticket.py`. The one thing to undo is the shared lock
path if it was moved. After step 4 rollback = `git revert` of that docs ticket.

What is **not** compatible and T must know: `land.sh docs` accepts only `tickets/ docs/ memory/` prefixes (118); if T's
`land-ticket.sh docs` accepted more, those files go through a normal ticket branch.

### §4 Test strategy: how A simulates "run from a project"

- **Layout switch in the harness** (`tests/control_harness.py`): `AC_TEST_LAYOUT=control` makes `make_repo()` place the
  scripts at `<sandbox>/scripts/control/` **by running `scripts/sync-to-project.sh <sandbox>`** (the real artefact, not a
  copy loop), write a `select_cmd` fixture into `<sandbox>/scripts/select-tests.sh`, and set `gate.select_cmd` in the
  sandbox config. Every `subprocess` call in the harness that builds a path with `"scripts", name` goes through one
  `script_path(name)` helper so the layout is chosen in one place.
- **Which suites run twice**: `test_land`, `test_gate`, `test_board`, `test_apply`, `test_review` — the entry points. A one-line
  loader (`tests/test_project_layout.py`) re-imports those modules with the env set, so no test body is duplicated.
- **New cases (red today, green after C1–C8)**:
  - V1 static: no synced entry/dependency contains `$ROOT/scripts/` or `dirname(HERE)/scripts` (grep over the closure).
  - V2 `land.sh` from `scripts/control/` lands a branch; the gate marker file proves the gate was called **from the
    worktree's `scripts/control/gate.sh`** (harness replaces the expensive gate with a marker, header lines 10–13).
  - V3 `gate.sh --branch` with `select_cmd`: prints the fixture's modules, never A's `map()` names; `unmapped` → rc 3.
  - V4 `select_cmd` exit 86 with rows → `status.json.state == env_suspect`, `environment_suspect` is that list, no
    `auto-fix` marker, one inbox decision page.
  - V5 `select_cmd` rc 0 empty stdout → rc 2, not 0.
  - V6 `board.py` with `key_file`: `/` → 401 without key; `/?key=k` → 302 + cookie; `/` with cookie → 200; `/healthz` → 200
    always; wrong key → 401 (port 0 as today, `test_board.py:29`).
  - V7 `verify.py --case verify/x/test_a.py` runs only that file; `--case` + `--tag` is the union; unknown case → rc 3.
  - V8 sync: delete a dependency from the sandbox's `scripts/` → `exit 2`, no file written, message names caller → missing.
  - V9 sync: `.sync-manifest` equals the computed closure; adding an entry with a new import pulls the import in without
    touching any list (mutation: hard-code the old `DEPENDENCY_ONLY` → V9 red).
- Cost model: static V1 is ~0 s; the duplicated suites roughly double land/gate test time (inferred +20 % of the full gate).
  Per D-016 this is paid in A's gate, not per ticket's agent context.

### §5 Not doing (and why)

- **Not** a `run: <cmd>` line in `select_cmd` (③ 丙): it would move "how to run" to the project and lose rc 86 statistics,
  per-case red lists and flake rerun for that project.
- **Not** `board.bind` / `0.0.0.0`: exposure is the project's proxy; a key gate on a loopback server is enough for T's
  tunnel (T-reported "對外網"; **?** ⑥ Q2 if T binds directly).
- **Not** reading, patching or syncing anything **into** T from this design; T's `verify-refs.py` and `test-for.sh` stay T's.
- **Not** auto-sync on A land, not a T-side "am I stale" check: sync is a human act (hard_block, D-069 ②).
- **Not** a new gate, a new rc, a new ticket field (D-053 ④, D-018).
- **Not** folding `templates/` / `verify/_template_ticket.py` into the closure (§2 last bullet).
- **Not** changing `land.sh docs` prefixes, the lock, or any land refusal rule.

## ③ Rejected alternatives

**甲 — Keep the project fork, fix the glue (T patches `land-ticket.sh` to A's new interface each sync).**
Rejected: this is exactly what produced "8 處壞、6 處黏合層" (T-reported); every A interface change costs a T ticket, and the
breakage is silent (review/objection checks turned off without a red). Per future ticket: costs a T glue ticket per A
interface change (~1 per 5 A tickets, inferred) plus undetected policy gaps.

**乙 — Make `gate.sh` itself a template the project copies and edits (today's `gate.example.sh` route).**
Rejected: a copied gate stops following A the day it is copied (`sync-to-project.sh` header lines 5–6 say why for role cards;
the same holds for a 1,050-line gate). The project's real contribution is one `case` table and a preflight; everything else in
`gate.sh` (status files, env suspect, flake rerun, regression, verify-case, review handoff) is A's and changes often (measured:
D-010, D-014, D-019, D-020, #126, #136 all touched it). Per future ticket: costs re-copying or diverging on each gate change.

**丙 — `select_cmd` may also emit `run: <shell command>` lines (project supplies the runner).**
Rejected (C5): `status.py run-tests` detects environment failures **in-process** (lines 302–315); an external command gives
only an exit code and a log, so rc 86 statistics, `failures[]` ids that feed back into `python -m unittest`, and the
flake rerun all stop working for that project — the three things that make auto-fix dispatch safe. Declared suspects would
still parse from the log, but that is a smaller guarantee than today. If a project ever has non-unittest tests, that is a
new design question, not a line form. Per future ticket: would cost a worker round dispatched into a broken environment
whenever the project's runner hides it.

**丁 — `board.key` inside `config.json` instead of `key_file`.**
Rejected: `config.json` is committed and read by every script and every short-lived agent (`rules.py pack` reads it); a
secret in it is a secret in every dispatch and in git history. Per future ticket: costs nothing either way; the file form
costs one `open()`.

**戊 — Keep `SCRIPT_LIST` + `DEPENDENCY_ONLY` but add a test that they match the import graph.**
Rejected: D-054 Q12 — pinning two copies with a test is the symptom of keeping two copies. The computed closure *is* the
list; the test (V9) checks the computation, not a second hand copy.

## ④ Migration

- **A files that change** (all A tickets, ⑥): `scripts/gate.sh` (root rule, `$AC`, `select_cmd` branch, rc 86 from selector,
  `--tests-dir` pass-through), `scripts/land.sh` (`$AC`, gate path via `AC_REL`), `scripts/status.py` (`run-tests --tests-dir`),
  `scripts/verify.py` (`--case`), `board/board.py` (control dir rule, `key_file`), `scripts/sync-to-project.sh` (`ENTRIES`, closure,
  failure; **hard_block** → human lands it), `scripts/gate.example.sh` (rewritten as a `select_cmd` example),
  `templates/project-CLAUDE.md` (對照表 rows become `scripts/control/<same>`), `docs/WORKFLOW.md` §閘門 (one paragraph:
  "the project fills `gate.select_cmd`; the gate is A's"), `tests/control_harness.py` + new `tests/test_project_layout.py`.
- **Two shapes coexist**: none in A. In T, two landers coexist for the §3 period (three tickets); the lock is shared so they
  cannot interleave. `gate.sh` without `select_cmd` is byte-for-byte today's behaviour, so A's own gate is unaffected.
- **Existing data**: tickets, events, `reports/`, `status.json` unchanged. `.sync-manifest` in T gains the three new names on
  the next sync (and `consolidate-memory.sh`, which was missing); nothing is retired.
- **Order**: ⑥ rows in order; T steps in §3 only after A rows 1–6 have landed and been synced once.

## ⑤ Acceptance (one assertion per conclusion)

| Conclusion | Red-today assertion / greppable change |
|---|---|
| C1 | V1: `grep -l '\$ROOT/scripts/' scripts/land.sh scripts/gate.sh` returns nothing; `gate.sh` run as `<sandbox>/scripts/control/gate.sh --full` writes `status.json` under `<sandbox>/reports/`, not `<sandbox>/scripts/reports/`. |
| C2 | V2: marker proves the gate ran from `$WT/scripts/control/gate.sh`; `AC_ROOT` still points at the main repo (ticket file updated there). |
| C3 | V3; and `gate.sh` with no `select_cmd` still picks `test_ticket test_event test_memory test_no_project_names` for `--base` (today's `BASE`, line 129). |
| C4 | V4; mutation: make gate.sh treat selector rc 86 as "red" → V4 red (auto-fix marker appears). |
| C5 | `status.py run-tests --tests-dir demo/tests --mode names test_x` loads from that dir; V5. |
| C6 | V6; `grep -n 'compare_digest' board/board.py` ≥ 1; `/healthz` has no key check (assert in V6). |
| C7 | V7; `python3 scripts/verify.py --list --case verify/x/test_a.py` prints one line. |
| C8 | V8, V9; `grep -c 'DEPENDENCY_ONLY' scripts/sync-to-project.sh` = 0; `test_sync_to_project.py:96-128` (the two `verify.py` mutations) still green. |
| C9 | `docs/WORKFLOW.md` §閘門 and `templates/project-CLAUDE.md` 對照表 mention `scripts/control/land.sh`; T-side: three `land.pass` events with `by=land.sh` before the retirement ticket (checked by main, not by a test in A). |
| C10 | `AC_TEST_LAYOUT=control python3 -m unittest test_land test_gate test_board` green; `.sync-manifest` in the sandbox equals the closure. |

## ⑥ Follow-up work (model table)

| # | What | Ticket | Model | One-line reason |
|---|---|---|---|---|
| 1 | `gate.sh` + `land.sh`: root rule, `$AC`, gate via `AC_REL`; harness `AC_TEST_LAYOUT=control` + V1, V2 | 未開 | opus | Mechanical but spans two 1,000-line scripts and the harness; the layout switch is the foundation every other row tests against. |
| 2 | `gate.select_cmd` + rc 86 path + `gate.tests_dir` (`status.py --tests-dir`); V3–V5; `gate.example.sh` rewritten as a selector example | 未開 | opus | Touches the env-suspect path (D-019); needs someone who reads `status.py` and `gate.sh` together. |
| 3 | `verify.py --case` + V7 | 未開 | sonnet | 185-line file, one flag, cache key change; verification is a `--list` call. |
| 4 | `board.py`: control-dir rule, `key_file`, V6 | 未開 | sonnet | Self-contained HTTP change; the test harness for the board already exists (port 0). |
| 5 | `sync-to-project.sh`: `ENTRIES` + computed closure + failure + `board.py` source; V8, V9; adjust `test_sync_to_project.py` | 未開 | opus | File is in `hard_block` — main lands it after human authorisation (D-069 ②: this is the one-time "new entries" authorisation for `land.sh gate.sh board.py`). |
| 6 | Docs: `docs/WORKFLOW.md` §閘門 paragraph, `templates/project-CLAUDE.md` 對照表, `docs/DECISIONS.md` pointer to this file | 未開 | sonnet | Text only; lands via the docs channel. |
| 7 | T side (not A tickets): §3 steps 1–5 | T 未開 | — | Main dispatches after rows 1–6 are synced; order 1 → 2 → 3 → 4,5 (parallel) → 6; row 7 last. |

Rows 3 and 4 do not depend on 1–2 and can run in parallel with them; row 5 depends on 4 (it copies `board.py`); row 6 last.

### Questions for the user (with recommendation)

- **Q1** Are all of T's gate tests unittest-loadable from one directory (`gate.tests_dir`)? If T's browser-engine layer is run
  by something else, C5 needs a second look. Recommendation: assume yes (T's `verify.py` and `land-ticket.sh gate` call
  `python -m unittest`, D-020 C5), confirm with one `python3 -m unittest <browser module>` in T before row 2 lands.
- **Q2** Does T expose the board by binding a public interface, or through a proxy / tunnel? Recommendation: keep A's board
  on `127.0.0.1` and let the proxy forward (no `board.bind` key); only add `bind` if T answers "directly".
- **Q3** Authorise the three new sync entries (`land.sh`, `gate.sh`, `board.py`) now, in one go (D-069 ②: only new entries
  need authorisation; this design is that list). Recommendation: yes, as one authorisation attached to row 5.
