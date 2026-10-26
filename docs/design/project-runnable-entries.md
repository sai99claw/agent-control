# D-071 — A is an installed tool: a project clones A and runs it in place; no sync

Design session (Fable, 2026-10-24, second pass; replaces the D-069 ①② version of this file).
**Name:** the single entry is `bin/actl` (D-072 Q3 renamed it from the `ac` this file first used, to avoid macOS `/usr/sbin/ac`); commands below use `actl`.
A = agent-control (this repo), T = the downstream project that reported the problems. Every factual
claim is tagged **measured** (read or run in this session, in A), **inferred** (read from code, not
executed) or **T-reported** (from T's report via main; nothing in T was read here). Every trade-off
carries a "per future ticket: saves / costs" line (D-018, rough, inferred). Fixed sections follow
`docs/DESIGN.md` §設計文件的固定段; the nine topics from the dispatch are §1–§9 inside ②.

Hard rule for this file: `board/config.json` `hard_block.patterns` forbids four process-signal
spellings in added test lines, so no example below spells them; "stop a leftover process" is written in
words.

## ① Origin

User's words (D-071, 2026-10-24, verbatim): 「同步清單要一次加進 land.sh、gate.sh、board.py 三個入口  同意。你把要改好的都改好
我再去跟T 那邊講要怎麼做 T 的測試 你要定義一個好的方式 讓他遵守，我覺得A就是個管理器 一個專案要用就是clone一個 然後直接在上面跑東西
不再做額外sync, 要同步remote 就pull就好 interface 要定義完善 有彈性 T 的看板是走代理(portal」

Main's ruling (D-071, verbatim): 「A(agent-control)改成安裝即用的工具 —— 專案端 clone A,從 A 的 clone 直接對專案執行;更新 A =
在 A 的 clone `git pull`。不再把腳本同步進專案:sync-to-project.sh、SCRIPT_LIST / DEPENDENCY_ONLY、專案的 scripts/control/ 與
.synced-from 退役 … D-069 ② 由本條取代;D-069 ①(專案只留專案知識)保留並擴大。」 and 「A 自己也照同一套介面跑(自己是自己的第一個專案)」.

What is kept from the previous version of this file (all still true under the new model, re-expressed in §1–§4):
the gate's two roots (code root ≠ ticket root), the `select` contract (ids or `unmapped <file>`), rc 86 =
environment suspect with `environment_suspect` rows (D-019), `board.key_file`, `verify.py --case`, and the
compatibility period of three consecutive T tickets (D-017). D-069 ① ("the project keeps only project
knowledge: which tests, how to run them, preflight, build / deploy") is the dividing line for §3 and §4.

Symptoms and facts this design rests on:
- T-reported: after syncing A 08e6752, 8 things in T broke; 6 were glue that no longer matched A's interface
  (`review_problems` signature, verify-case lint, inbox file shape) and failed **silently** (review / objection
  checks off, no red).
- Measured: the sync machinery is `scripts/sync-to-project.sh` (496 lines), `tests/test_sync_to_project.py`
  (865 lines), a `hard_block.paths` entry, a `map()` row in `gate.sh:174`, and prose in `README.md:95`,
  `docs/TODO.md:10-27`, `docs/MEMORY.md:26`, `docs/CODE-MAP.md:12`, `docs/reference/role-procedures.md:50`,
  `new-session.sh:231,253`, `rules.py:268` (sync header handling), plus comments in `event.py`, `verify.py`,
  `heartbeat.sh`, `board.py`.
- Measured: eight scripts compute the project root by walking **up from `$0`** to `board/config.json`
  (`_ac_root()` in `apply.sh:49-57`, `review.sh:44-52`, `auto-fix.sh:68-76`, `heartbeat.sh:21-29`,
  `session-hook.sh:36-44`, `consolidate-memory.sh:35-43`, `new-session.sh:32/167`; `event.repo_root()` 73-90 does
  the same in Python). Under D-071 the scripts live in **A's clone**, so that walk always finds **A's own**
  `board/config.json` — the wrong root for every project except A, and it does not fail (the §5.5 family).
  `AC_ROOT` already overrides all of them (measured in each file).
- Measured: `gate.sh:114` uses `ROOT=$(dirname "$0")/..` as the **code root**; `verify.py:36-50` finds the code
  root from `__file__` and explicitly refuses `AC_ROOT` (#55: in a worktree the gate must test the worktree's
  `verify/`). Both become wrong when the file lives in A's clone.
- Measured: `land.sh:645-646` runs the full gate as `cd "$WT" && … sh scripts/gate.sh --full` — the gate **from
  the tree under test**. In a project that no longer has `scripts/gate.sh`; in A it is still the right thing
  (a ticket that changes the gate must be gated by its own gate).
- Measured: `status.py run-tests` (273-316) is the only runner: in-process unittest, statistical environment
  suspect (`EnvironmentResult`), `LogStream`, rc 86; `gate.sh` then parses the log (`status.py failures --log`,
  1090-1100) for ids to rerun (`flake_rerun`, 897+, reruns each id via `python3 -m unittest <id>` from `tests/`).
  `verify-case.py run_cases` (261-290) runs case files with `python3 -m unittest -v` in a `git archive` copy
  and classifies red by **traceback frames** (red_shape, D-020).
- Measured: `rules.py` has one overlay mechanism already: `roles_dir` (canon) vs `memory/role` (this repo's
  own layer); `is_canon()` = both are the same dir; when not canon the pack appends "### 本專案 …" sections
  (430-450). `board/board.py:48` inserts `dirname(HERE)/scripts` (correct inside A's clone), binds
  `127.0.0.1` only (1210), has no key code (grep `key_file|compare_digest`: 0).
- Measured: `hard_block` is read by `ticket.py:986` from `board/config.json`; `land.sh:430-445` and `apply` /
  `gate` share `hard_block_hits`. Nothing from the previous design has landed (grep `select_cmd|tests_dir|
  key_file` in `scripts/`: 0 hits), so there is no half-built shape to migrate.
- Measured: `.claude/settings.json` in A runs hooks as `sh "$CLAUDE_PROJECT_DIR/scripts/session-hook.sh"`
  (SessionStart) and `python3 "$CLAUDE_PROJECT_DIR/scripts/agent-cost-hook.py"` (SubagentStop, SessionEnd);
  `test_session_hook.py:32` pins that shape. `CLAUDE_PROJECT_DIR` is therefore the variable Claude Code gives
  hooks (inferred to be reliable from A using it since #39).
- Measured: no `ac` command exists in this container (`command -v ac` empty). macOS ships `/usr/sbin/ac`
  (accounting) — **?** not verified here, see ⑥ Q3.

## ② Conclusions (one trade-off per row)

| # | Conclusion | Why | Per future ticket: saves / costs (inferred) |
|---|---|---|---|
| C1 | **One entry `$AC_HOME/bin/actl <command>`**, a ~60-line POSIX dispatcher that resolves `AC_HOME` (its own dir/..), the project root (§1 order), exports `AC_HOME AC_ROOT AC_CONTROL_DIR`, checks `config_version` once, and `exec`s the existing script. Scripts keep their names and stay directly callable (tests, `land.sh` → gate, `auto-fix.sh` → `apply.sh`). | A single place for root + version + path; the scripts' `$AC/<sibling>` convention (measured in `apply.sh:60-61`, `review.sh:53-54`) already needs only `AC_CONTROL_DIR`. Renaming scripts would be a second shape (D-018). | saves: every "which path do I type in this project" question in dispatch prompts (one `actl` word replaces `scripts/control/…`); costs: one `exec`, 0 tokens. |
| C2 | **Project root never comes from `$0`.** Order: `AC_ROOT` → `actl -C <dir>` → `CLAUDE_PROJECT_DIR` → main root of cwd's git tree (`git rev-parse --git-common-dir`'s parent, so a worktree resolves to its main repo) → rc 2. The `_ac_root()` walk is deleted from all eight scripts; `event.repo_root()` keeps `AC_ROOT` and falls back to the cwd rule, never to `__file__`. **Code root** (tree under test) is a second variable `AC_CODE_ROOT`, default = cwd's toplevel (the worktree itself). | The `$0` walk finds A's own config from A's clone and does not fail (①). The cwd rule is what `wtbase.sh main_root()` already does (measured 15-22). | saves: one silent "wrote tickets into A's repo" round per project session (a whole ticket's events/status lost, ~1 land round); costs: a user outside any git tree must type `-C` or set `AC_ROOT`. |
| C3 | **A dogfoods**: A's clone is its own first project. `actl` run with cwd inside A resolves root = A; A's `board/config.json` carries the same `config_version` and `tests` section as any project (A's gate `map()` table becomes data in `tests.unittest.map`). One special case, pinned by a test: `land.sh` runs the gate from `$WT/scripts/gate.sh` only when the project **is** A (`realpath main_root == realpath AC_HOME`); every other project gets `$AC_HOME/scripts/gate.sh`. | D-071 原話「A 自己也照同一套介面跑」; the gate-from-worktree rule (`land.sh:645`) exists for A's own tickets that change the gate and is kept for exactly that case. | saves: A's tests are the project-interface tests (no simulator, §8); costs: one `realpath` compare in land.sh. |
| C4 | **Project config stays `board/config.json`** (no rename, no second file) and gains `"config_version": 2` plus a `tests` section (§2, §4). A owns `scripts/project_config.py`: the schema table, `CURRENT_VERSION`, explicit migrations, `actl doctor`, `actl migrate`, `actl init`. `actl` refuses (rc 2, one line naming the fix) when the project's version is **lower** (run `actl migrate`) or **higher** (A is behind, `git pull`) than A's. Unknown keys warn, known keys are type-checked, missing keys take defaults. `session-hook.sh` runs `actl doctor` so a stale project is reported at session start, not at the first land. | T's six silent breakages (①) all came from A changing an interface without the project noticing. A version number the tool checks on every call is the smallest thing that turns "silent" into "one line". Data-shape changes (inbox dir, ticket fields) are **doctor rows with a fix command**, not config versions. | saves: the T-side debugging session after each A interface change (T-reported "8 處壞", one session ≈ 100K+ tokens); costs: ~50 ms per `actl` call, one JSON read. |
| C5 | **Ownership line (§3)**: tickets, DECISIONS / VISION, `memory/{role,model,project}` (the project's own layer), `reports/` + inbox, worktrees, `board/{config,events,answers,role-models}.json`, `code-map/`, `verify/` cases + `TAGS.md`, `hard_block` — **project**. Role cards (canon), rules sections, templates, model cards, scripts, board code — **A** (read from `AC_HOME`). Project extends A by the existing overlay (`<project>/memory/role/<role>.md`, `<role>.inbox.md`, `memory/model/<m>.md`) and `rules.sections`; `rules.roles_dir` / `models_dir` retire (canon is always `$AC_HOME/memory/{role,model}`; `is_canon` = `AC_ROOT == AC_HOME`). | D-069 ① / D-071 「專案只留專案知識」; the overlay already exists (measured `rules.py:430-450`), so no new mechanism. | saves: T stops maintaining `docs/roles/` copies (T-reported drift); costs: 0. |
| C6 | **Test interface = five commands declared in `tests` (§4)**: `preflight`, `select`, `run`, `full`, optional `regression`. A's gate, land, flake rerun, verify-case and `regression-red` call **only** these. Ids are opaque strings; results come back as one JSON file with a fixed schema (`result.json`, schema 1); exit codes 0 / 1 / 86 / 2 / 3 have one meaning each. A ships the **unittest adapter** (`scripts/adapter-unittest.py`, built from today's `status.py run-tests` + `EnvironmentResult` + `LogStream`) as the default for every command; a project overrides any subset with its own command string. A uses the adapter itself. | D-071 「T 的測試 你要定義一個好的方式 讓他遵守」. The previous version rejected a project runner (③ 丙) because env-suspect statistics and rerun ids lived in A's in-process unittest; with a **result file that carries ids, status, message, exception/file/line and `environment_suspect` rows**, every one of those features works for any runner, and the unittest case keeps its in-process statistics inside the adapter. | saves: per T ticket, the "which tests, how to run them" guess and the browser-layer special cases leave A's gate (T's 2,601-line lander retires, T-reported); costs: one extra process per gate phase (<1 s), and the project must write a result file (the adapter does it for unittest projects). |
| C7 | **Red classification moves from traceback parsing to result fields.** `verify-case.py red/check` run `regression --case <file>` in the base copy and in the candidate (with `AC_CODE_ROOT` set to each tree) and classify from `exception` + `file` + `line` (D-020 table unchanged: `AssertionError` in a `verify.files` file counts; import failure, missing symbol, red elsewhere do not). A result row with `status: fail` and `exception: null` is **unmeasured** (rc 2, no ticket write), exactly today's `unmeasured` path. | D-020 rules stay; only the source of "which frame" changes. A runner that cannot say what failed must not be read as "it red-ed in the right place" (§5.5). | saves: 0 per ticket; costs: 0 (the adapter fills the fields from the same traceback it has today). |
| C8 | **Claude Code hooks via `actl hooks install|check`** (§5): idempotent, writes `.claude/settings.json` entries whose command is the **absolute path into `AC_HOME`**, keeps every entry it does not manage, removes entries that point at files that no longer exist **and** whose basename is one A used to ship (`guard-main.sh`, `session-hook.sh`, `agent-cost-hook.py`, `main_guard.py`), prints a diff, rc 1 on drift in `check`. Replaces the two "please paste" lines `sync-to-project.sh:412,464-465` print today. | A hook pointing at a deleted file is a silent no-op (§5.5); a command that fixes it is cheaper than a paragraph that asks. | saves: one session with no opening page / no cost rows per hook drift; costs: 0. |
| C9 | **Board**: `actl board` = `python3 $AC_HOME/board/board.py` with `AC_ROOT` = project; bind stays `127.0.0.1`; optional `board.key_file` (relative to project root; first line is the key; every path except `/healthz` requires it; `?key=` sets an `HttpOnly` cookie and redirects; `hmac.compare_digest`; failure 401 empty). T exposes it through its portal proxy (D-071 原話). | Unchanged from the previous version except that `board.py` no longer needs a control-dir rule (it lives in A). | saves: T's 5,711-line board fork (T-reported); costs: one `if` per request. |
| C10 | **Retire the sync machinery entirely** (§7): `sync-to-project.sh`, `test_sync_to_project.py`, the `hard_block.paths` row (user-authorised, D-071), `gate.example.sh` (replaced by `templates/project-tests.example.sh` + `templates/project-config.example.json`), `.synced-from` / `scripts/control/` handling, `rules.roles_dir`, and every prose reference listed in ①. T retires `scripts/control/`, `.synced-from`, `land-ticket.sh` and its board fork on the §7 schedule; rollback = the files are still on disk until the last step. | D-071 原話「不再做額外sync」. Two shapes (synced copies + A's clone) must not coexist in A; in T they coexist only for the rehearsal window. | saves: every sync round (hard_block authorisation + a T session, ~50K tokens each, T-reported cadence ~1 per 5 A tickets); costs: T must `git pull` A deliberately (one command). |
| C11 | **A tests "run against another project" with a fixture project** (§8): the harness no longer copies scripts into the sandbox; it runs `$AC_HOME/scripts/*` (or `AC_TEST_SCRIPTS` for mutation) with `AC_ROOT=<sandbox>` and cwd = sandbox; a committed `tests/fixtures/project/` carries a `tests` section with a **command adapter** written in shell (the T shape) and the unittest default. | D-015 §5.6: do not simulate T; the thing to test is A's own scripts from a different root. The dogfood path is tested by A's own gate. | saves: the next "works in A, breaks in T" round; costs: +~10 % test runtime (inferred; fewer file copies than today). |
| C12 | **Not now** (§9): `0.0.0.0`, auto `git pull`, a packaging/installer beyond `git clone`, a multi-project registry, a template override dir, a daemon / RPC, a new gate or rc (D-053 ④), reading or patching T. | See §9. | — |

### §1 Invocation

**Install** = `git clone <A remote> ~/agent-control` (any path; `AC_HOME` is derived, never configured).
**Update** = `git -C ~/agent-control pull`. **Pin / roll back** = `git -C ~/agent-control checkout <sha>`;
every `session.start` event and every `status.json` records `ac_sha` (the clone's HEAD) so a report says which
A produced it (new field in the event `--kv`, not a ticket field).

**`bin/actl`** — the whole dispatcher is: resolve `AC_HOME=$(cd "$(dirname "$(realpath "$0")")/.." && pwd)`;
parse `-C <dir>`; resolve `AC_ROOT` (table below); `export AC_HOME AC_ROOT AC_CONTROL_DIR=$AC_HOME/scripts`;
`python3 "$AC_HOME/scripts/project_config.py" check "$AC_ROOT"` (rc 2 stops here, §2); `exec` the mapped script.

| `actl <command>` | Runs | Notes |
|---|---|---|
| `actl land …` | `scripts/land.sh "$@"` | incl. `actl land docs "<msg>" <files>` |
| `actl gate …` | `scripts/gate.sh "$@"` | `--branch\|--base\|--full [--ticket n]` |
| `actl apply …` / `actl review …` / `actl auto-fix …` | the same-named `.sh` | |
| `actl ticket …` / `actl event …` / `actl inbox …` / `actl status …` / `actl memory …` / `actl rules …` / `actl repo-map …` | the same-named `.py` | |
| `actl verify …` / `actl verify-case …` | `verify.py` / `verify-case.py` | code root = cwd's toplevel |
| `actl test <preflight\|select\|run\|full\|regression> …` | `scripts/testiface.py` | runs the project's declared command (§4); what the gate calls internally; useful for rehearsal |
| `actl board` | `board/board.py` | §6 |
| `actl hooks install\|check` | `scripts/hooks.py` | §5 |
| `actl doctor` / `actl migrate` / `actl init` | `scripts/project_config.py` | §2 |
| `actl session …` / `actl heartbeat` | `new-session.sh` / `heartbeat.sh` | |
| `actl version` | prints `AC_HOME`, HEAD sha, `CURRENT_VERSION` | |

Unknown command → rc 2 with the table above. Calling a script directly (`sh ~/agent-control/scripts/land.sh`)
keeps working **inside a project tree** (cwd rule); the only thing lost is the version check, which is why
dispatch prompts and docs say `actl`.

**Project root (`AC_ROOT`, the ticket root)** — first hit wins, no silent fallback:

| # | Source | When it applies |
|---|---|---|
| 1 | `AC_ROOT` env | set by `actl` for every child; set by `land.sh` / `auto-fix.sh` when they run the gate inside a worktree (measured today) |
| 2 | `actl -C <dir>` | scripts, cron, a shell outside the project |
| 3 | `CLAUDE_PROJECT_DIR` | Claude Code hooks (§5) |
| 4 | main root of cwd's git tree (`git rev-parse --git-common-dir` → parent) | the normal interactive case; inside a ticket worktree it still yields the main repo |
| 5 | none | rc 2: `actl: not inside a project; use -C <dir> or set AC_ROOT` |

After resolution `AC_ROOT/board/config.json` must exist, else rc 2 naming `actl init`. **The `$0` walk is gone**;
the fallback in `event.repo_root()` becomes rule 4, and `looks_like_a_copy()` keeps working (it tests the
resolved root for `.git`).

**Code root (`AC_CODE_ROOT`, the tree under test)** — `AC_CODE_ROOT` env, else cwd's `--show-toplevel`.
Used by `gate.sh` (its `ROOT`), `verify.py` (`_find_root`), `verify-case.py` (set to each copy), and passed
to every test command (§4). In A's own clone, cwd = A → both roots are A (dogfood).

Example (T, interactive): `cd ~/T && actl ticket list --open` → root = `~/T`. In a T worktree:
`cd ~/T-wt/t12-wt && actl gate --branch --ticket 12` → `AC_ROOT=~/T`, `AC_CODE_ROOT=~/T-wt/t12-wt`.

### §2 Project configuration

**Location**: `<project>/board/config.json` (unchanged; every reader already goes through `event.config()`,
measured). **Version**: top-level `"config_version": <int>`; a file without the key is version 1 (today's
shape). A's `scripts/project_config.py` holds `CURRENT_VERSION = 2`, the schema table, and `MIGRATIONS =
{1: migrate_1_to_2}`.

**Compatibility rules** (the T lesson, applied once in one place):

| Situation | `actl` does | Who else notices |
|---|---|---|
| project version == A's | nothing | — |
| project version < A's | rc 2: `config_version 1 < 2 — run: actl migrate` (every command, including `actl inbox list`) | `session-hook.sh` prints the same line at session start |
| project version > A's | rc 2: `project needs config_version 3, this A has 2 — git -C $AC_HOME pull` | same |
| known key, wrong type | rc 2 naming key, expected type, got | `actl doctor` |
| unknown key | one stderr warning per call (never rc) | `actl doctor` lists them with "possibly retired" |
| `actl migrate` | applies migrations in order, writes the file, prints each change, refuses if the file is not valid JSON; idempotent | the user lands the change through their own docs channel (`actl land docs`) |
| data shapes (not config) | `actl doctor` rows, each with a fix command, e.g. `reports/inbox/index.jsonl exists → actl inbox migrate`; `scripts/control/ exists → retired, delete after §7 step 5`; `.claude/settings.json hook points at missing file → actl hooks install` | `actl doctor` exits 1 when any row is severity `error` |

**When A changes an interface**: a change to a config key = bump `CURRENT_VERSION` + a migration function;
a change to a data file shape (inbox, status.json, ticket field) = a doctor row + a migrate subcommand on the
owning script (`inbox.py migrate`, D-069 ③ is the pattern); a change to a dispatch-prompt / role-card contract
= nothing (A reads its own cards). The acceptance test for an A ticket that changes any config key is "the
fixture project at version N−1 is refused and migrates cleanly" (§8 V4).

**Schema (version 2)**. Type / default / reader. Rows marked *existing* are measured in A's `config.json`
today; rows marked *new* come from this design.

| Key | Type | Default | Read by | Status |
|---|---|---|---|---|
| `config_version` | int | — (required) | `actl`, `project_config.py` | new |
| `main_branch` | string | `main` | land, gate, apply | existing |
| `tickets_dir`, `events_file`, `answers_file`, `reports_dir` | path (rel. to root or abs) | `tickets`, `board/events.jsonl`, `board/answers.jsonl`, `reports` | event, ticket, status, inbox, board | existing |
| `worktree_dir` | path | `../<repo>-wt` | wtbase.sh | existing |
| `port` | int | 18905 | board | existing |
| `health_urls` | object | `{}` | board | existing |
| `routing`, `model_tiers`, `effort` | object / list / object | — | role-models, new-session, auto-fix | existing |
| `worker.command`, `opener.command`, `reviewer.command` (+ `timeout_seconds`) | string / int | — | auto-fix, review, new-session | existing |
| `lease_seconds.{opener,verifier,land,worker}` | int | — | heartbeat, auto-fix | existing |
| `flaky_threshold`, `flake_auto_single_runs`, `flake_auto_group_runs`, `environment_fail_fast_threshold`, `flaky_auto_ticket` | int / bool | 3, 5, 1, 8, true | gate, status, adapter | existing |
| `verify_case.timeout_seconds` | int | 900 | verify-case | existing (#156) |
| `batch_fix.rounds` | int | 2 | land | existing |
| `apply.regen_cmd` | string | `""` | apply | existing |
| `memory.applies_to`, `memory.inbox_suffix` | list / string | — | memory, rules | existing |
| `rules.sections` | object | A's table | rules | existing |
| `rules.roles_dir`, `rules.models_dir` | — | — | — | **retired** (migration deletes with a note) |
| `hard_block.{paths,dirs,patterns}` | lists | `[]` | ticket (apply, gate, land, auto-fix) | existing; **project-owned** |
| `board.key_file` | path | none → no auth | board | new (previous version's C6) |
| `tests` | object | `{"adapter": "unittest"}` | gate, land, verify-case, status, `testiface.py` | new (§4) |
| `tests.adapter` | `"unittest"` | `"unittest"` | testiface | new; the only built-in |
| `tests.preflight`, `tests.select`, `tests.run`, `tests.full`, `tests.regression` | string (shell) or null | null = adapter's built-in | testiface | new |
| `tests.unittest.roots` | list of dirs | `["tests"]` | adapter | new (T's `demo/` tests) |
| `tests.unittest.pattern` | string | `test_*.py` | adapter | new |
| `tests.unittest.base` | list of ids | `[]` | adapter `select --mode base` | new (A's `BASE`, gate.sh:129) |
| `tests.unittest.map` | list of `{match, ids}` | `[]` | adapter `select` | new (A's `map()`, gate.sh:132-210) |
| `tests.timeout_seconds.{preflight,select,run,full,regression}` | int | 60, 60, 1800, 3600, 2700 | testiface | new |

`migrate_1_to_2`: add `config_version: 2`; add `tests: {"adapter": "unittest"}` if absent; delete
`rules.roles_dir` / `rules.models_dir` and print "project role cards now overlay `$AC_HOME/memory/role`; move
`docs/roles/<role>.md` additions into `memory/role/<role>.md`"; delete `hard_block.paths` entries ending in
`sync-to-project.sh`; print unknown top-level keys (T has `land`, `protected_paths`, `verify_lint`, HANDOFF
item 1-3) as warnings, never delete them.

### §3 Project-owned vs A-owned

| Thing | Owner | Lives at | How the project extends / overrides |
|---|---|---|---|
| tickets, `SCHEMA.md` contract | project data / A contract | `<root>/<tickets_dir>` | — |
| `docs/DECISIONS.md`, `docs/VISION.md` | project | `<root>/docs/` | they are the project's rulings; A's own are A's project layer |
| `memory/role/<role>.md`, `<role>.inbox.md`, `memory/model/<m>.md`, `memory/project/*` | project (its own layer) | `<root>/memory/` | **this is the override**: rules pack appends them after A's canon card ("### 本專案", measured `rules.py:436-450`); `memory.py note` writes here |
| role cards (canon), model cards (canon), `docs/DISPATCH-TEMPLATE.md` sections, `templates/dispatch-*.md`, `verify/_template_ticket.py` | A | `$AC_HOME/…` | not overridable; file an A ticket. Which DISPATCH sections a role gets: `rules.sections` (existing) |
| scripts, `bin/actl`, board code, adapter | A | `$AC_HOME/scripts`, `bin`, `board/board.py` | `tests.*` command strings (§4) are the only code the project plugs in |
| `reports/`, `reports/inbox/`, `status.json`, logs | project | `<root>/<reports_dir>` | — |
| worktrees | project | `worktree_dir` | — |
| `board/config.json`, `events.jsonl`, `answers.jsonl`, `role-models.json` | project | `<root>/board/` | `role-models.json` stays project data (which model each role uses is a project choice; A ships a default copy in `templates/`) |
| `code-map/` + `repo-map.py` catalog | project (data) / A (tool) | `<root>/code-map/` | unchanged (docs/CODE-MAP.md:12 already says so) |
| `verify/` cases, `verify/TAGS.md`, `TAGS.d/` | project | `<root>/verify/` | the regression layer is project data run by A's `verify.py` (default adapter) or the project's `tests.regression` |
| `hard_block` | project | `board/config.json` | the only write-scope rule (D-054 Q2); A's own list is A's project config |
| project `CLAUDE.md` | project | `<root>/CLAUDE.md` | `templates/project-CLAUDE.md` rewritten: 「主線先讀 `$AC_HOME/CLAUDE.md`」, the 對照表 becomes one line: 「A 的入口一律 `actl <命令>`;專案特有的只有 §專案特有」 |

A's own `memory/`, `tickets/`, `reports/`, `verify/` are A-as-project; `rules.py is_canon()` becomes
`realpath(AC_ROOT) == realpath(AC_HOME)` (one comparison, no key).

### §4 Test interface (what T implements)

**Principle**: A knows *when* to test (branch / base / full / rerun / verify-red / verify-check), *what to do
with results* (status file, events, inbox, flake rerun, auto-fix, red classification) and *nothing about the
project's tests*. The project knows *which ids guard which files*, *how to run an id*, *what to check before
running*, and nothing about tickets. The seam is five commands and one result file.

**How A invokes a command** (`scripts/testiface.py`, used by gate / land / verify-case / `actl test`):
`sh -c "$cmd \"\$@\"" ac-test <args…>` with cwd = `AC_CODE_ROOT`, a fresh empty directory in `AC_TEST_OUT`,
and env:

| Variable | Value |
|---|---|
| `AC_HOME`, `AC_ROOT`, `AC_CODE_ROOT` | §1 |
| `AC_TEST_OUT` | directory for `result.json` and logs (A chooses: `<reports_dir>/t<n>/<run_id>/<phase>/` under a ticket, else `<reports_dir>/gate/<run_id>/<phase>/`) |
| `AC_TEST_MODE` | `branch` / `base` / `full` / `rerun` / `verify-red` / `verify-check` / `release` |
| `AC_TICKET`, `AC_RUN_ID` | may be empty (hand run without a ticket) |
| `AC_TEST_TIMEOUT` | the configured seconds; A also enforces it (a timeout = rc 2 "unmeasured", never red) |

Stdout and stderr of every command are captured to `$AC_TEST_OUT/<command>.log` (the raw text is evidence);
A **reads decisions only from exit codes, `select`'s stdout and `result.json`**, never by parsing the log
(`ENVIRONMENT-SUSPECT:` lines in the log stay accepted as a second source of declared rows, as today's
`parse_environment_suspects`, so T's existing case prints keep working).

**The five commands**

| Command | argv after the name | stdin | stdout | Exit codes | Must not |
|---|---|---|---|---|---|
| `preflight` | — | — | rc 86: JSON list of `environment_suspect` rows (D-019 seven keys, `source:"declared"`, `log` null); rc 0: ignored | 0 ok · 86 environment suspect · other = rc 2 for the gate ("preflight itself failed") | take longer than its timeout; run tests |
| `select --mode branch\|base` | — | `branch`: changed files, repo-relative, one per line (A computes them exactly as `gate.sh:1006-1008`); `base`: empty | one item per line: `<id>` or `unmapped <file>`; for `base` the project's fixed set | 0 ok (≥1 line) · 3 nothing selectable (stderr says why) · 0 with empty stdout = rc 2 (§5.5) · other = rc 2 | run tests; write under `reports/`; touch tickets |
| `run <id>…` | ids from `select` (deduped by A) | — | free (goes to log) | 0 every id pass/skip · 1 any fail/error · 86 environment suspect (rows in `result.json`) · 2 could not run (A also uses 2 when `result.json` is missing/invalid, when rc 0 but zero ids or any fail row, when rc 86 but no rows) | exit 0 with zero ids; claim ids it was not given |
| `full` | — | — | free | same as `run` | — |
| `regression [--tag t]… [--case path]… \| --list` | tags from the ticket's `verify.tags`; case files from `verify.files`; `--list` prints what would run, one per line | — | free (`--list`: ids) | same as `run`; plus 3 = tags declared but nothing selected (today's `verify.py` rc 3, kept) | — |

**`result.json` (schema 1)** — written by `run`, `full`, `regression` into `$AC_TEST_OUT/result.json`:

```json
{
  "schema": 1,
  "command": "run",
  "ac_test_mode": "branch",
  "started": "2026-10-24T09:12:03+00:00",
  "seconds": 41.7,
  "ids": [
    {"id": "test_event.EmitTest.test_emit_writes_one_line", "status": "pass",
     "message": null, "exception": null, "file": null, "line": null, "seconds": 0.21, "traceback": null},
    {"id": "test_land.RefuseTest.test_zero_commits", "status": "fail",
     "message": "AssertionError: 0 != 2 : land must refuse", "exception": "AssertionError",
     "file": "tests/test_land.py", "line": 88, "seconds": 1.3,
     "traceback": "Traceback (most recent call last):\n  File \"tests/test_land.py\", line 88 …"},
    {"id": "browser:safari:test_login", "status": "skip",
     "message": "engine not available on this host", "exception": null, "file": null, "line": null,
     "seconds": 0.0, "traceback": null}
  ],
  "environment_suspect": [],
  "log": "run.log"
}
```

| Field | Type | Rule |
|---|---|---|
| `schema` | int | 1; A refuses other values (rc 2) |
| `ids[].id` | string | opaque; must be rerunnable alone via `run <id>`; the set must equal the ids A passed (`full`/`regression`: whatever ran, ≥1) |
| `ids[].status` | `pass` / `fail` / `error` / `skip` | `error` = did not reach an assertion (import, setup, crash) |
| `ids[].message` | string / null | first line; **skip reasons are mandatory** (a skip without a reason is an `error`, §5.5) |
| `ids[].exception`, `file`, `line` | string / null | the innermost frame **inside the test file**; what verify-case classifies on (C7) |
| `ids[].seconds` | number / null | per id |
| `ids[].traceback` | string / null | full text when available; the adapter always fills it |
| `environment_suspect` | list (never null, `[]` when none) | D-019 rows; `source` `statistical` or `declared`; non-empty ⇔ rc 86 |
| `log` | string / null | path relative to `AC_TEST_OUT` |

**Minimal command adapter** (`templates/project-tests.example.sh`, the shape T writes; project knowledge only):

```sh
#!/bin/sh
# <project>/scripts/ac-tests.sh — every tests.* key in board/config.json points here.
set -u
cmd=$1; shift
case "$cmd" in
  preflight)
    # lock screen / leftover Safari process / free disk: project knows, A never learns what these are
    if sh scripts/preflight-mac.sh >"$AC_TEST_OUT/preflight.txt" 2>&1; then exit 0; fi
    python3 - "$AC_TEST_OUT/preflight.txt" <<'PY'
import json, sys
why = open(sys.argv[1], encoding="utf-8").read().strip() or "preflight failed"
print(json.dumps([{"source": "declared", "engine": "host", "why": why, "count": 1,
                   "threshold": None, "log": None, "line": None}]))
PY
    exit 86 ;;
  select)
    mode=$2
    if [ "$mode" = base ]; then echo test_ledger; echo test_invariants; exit 0; fi
    n=0
    while IFS= read -r f; do
      n=$((n+1))
      case "$f" in
        demo/*.py)       echo demo.tests.test_demo ;;
        browser/*.py)    echo browser:safari:test_engines; echo browser:chrome:test_engines ;;
        tests/test_*.py) echo "$(basename "$f" .py)" ;;
        *.md)            ;;                         # docs: no test, not unmapped (project's call)
        *)               echo "unmapped $f" ;;
      esac
    done
    [ "$n" -gt 0 ] || { echo "select: no changed files" >&2; exit 3; } ;;
  run)
    # unittest ids → A's adapter (reuse, do not rewrite); browser ids → the project's engine runner
    unit=""; browser=""
    for id in "$@"; do case "$id" in browser:*) browser="$browser $id" ;; *) unit="$unit $id" ;; esac; done
    rc=0
    [ -z "$unit" ] || { python3 "$AC_HOME/scripts/adapter-unittest.py" run $unit || rc=$?; }
    [ -z "$browser" ] || { python3 scripts/browser-tests.py --result "$AC_TEST_OUT/result.json" $browser || rc=$?; }
    exit $rc ;;        # browser-tests.py merges its rows into result.json; both write schema 1
  full)        exec python3 "$AC_HOME/scripts/adapter-unittest.py" full ;;
  regression)  exec python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json" "$@" ;;
  *) echo "ac-tests: unknown $cmd" >&2; exit 2 ;;
esac
```

```json
"tests": {
  "adapter": "unittest",
  "preflight":  "sh scripts/ac-tests.sh preflight",
  "select":     "sh scripts/ac-tests.sh select",
  "run":        "sh scripts/ac-tests.sh run",
  "full":       null,
  "regression": null,
  "unittest": {"roots": ["tests", "demo/tests"]},
  "timeout_seconds": {"run": 1200, "full": 3600}
}
```

`null` (or absent) = the built-in adapter's command; so T can start with **only `preflight`** declared and add
`select` / `run` when the browser layer is wired. A's own `config.json` declares none of the five (all built-in)
and carries `tests.unittest.map` (today's 32 `map()` rows as `{"match": "scripts/event.py", "ids":
["test_event","test_ticket","test_land"]}`, first match wins, `"ids": []` = "no test, not unmapped") and
`tests.unittest.base`.

**Built-in unittest adapter** (`scripts/adapter-unittest.py`; `status.py run-tests` moves into it, one runner
in A): `select` = `tests.unittest.map` + `tests/test_*.py changed → that module` + `base` list; `run <id>…` /
`full` = `loader.loadTestsFromNames` / `discover` over `tests.unittest.roots`, in-process, with today's
`EnvironmentResult` (statistical suspects, `environment_fail_fast_threshold`) and `LogStream`; it writes
`result.json` from the `TestResult` (status per id, exception type / innermost frame in the test file for
`file`/`line`, docstring-free first line as `message`, skip reason as `message`). `regression` built-in =
`$AC_HOME/scripts/verify.py` gaining `--result <path>` (and `--case`, C7 of the previous version); `preflight`
built-in = rc 0.

**How A's existing features sit on the interface** (what changes in `gate.sh`; everything after the result is
as today):

| Feature | Today (measured) | On the interface |
|---|---|---|
| mapping gap → rc 3 | `map()` + `unmapped` list (gate.sh 123-126, 1053-1066) | `select` prints `unmapped <file>`; gate keeps the semantics byte for byte: run what mapped, exit 3, `ticket_gate 3` |
| run | `status.py run-tests --mode names\|discover` (475-477) | `run <ids>` / `full`; green = rc 0 **and** result validated |
| environment suspect | rc 86 from in-process result → `SUSPECT_FILE` → `env_suspect_note`, no rerun, no auto-fix (987-988) | `preflight` rc 86 **or** `run` rc 86 → `environment_suspect` rows copied to `SUSPECT_FILE`; same path, same inbox decision page; declared rows from `ENVIRONMENT-SUSPECT:` log lines still merged by `status.py done` |
| flake rerun | `status.py failures --log` → ids → `python3 -m unittest <id>` ×N (897-935) | ids = result rows with `fail`/`error` → `run <id>` ×`flake_auto_single_runs` (each into `$AC_TEST_OUT/rerun-<k>/`), group rerun ×`flake_auto_group_runs`; flaky classification and the auto flaky ticket unchanged |
| status file | `status.py done --log` parses the log into `failures[]` (case/file/line/excerpt) | `status.py done --result <result.json>`; `failures[]` keys unchanged (`case`=id, `file`, `line`, `excerpt`=message); `--log` stays as attachment; **no new status.json field** |
| events | `gate.start/pass/fail`, `land.*` | unchanged; `--kv ac_sha=<A HEAD>` added |
| regression layer | `regression()` → `verify.py --tag …` (679-700) | `regression --tag …`; "no verify.tags declared" / "no regression layer" notes unchanged |
| verify-case red / check | `run_cases` spawns `python3 -m unittest -v` in copies, classifies by traceback | `regression --case <files>` with `AC_CODE_ROOT=<copy>`; classification from result fields (C7); timeout = `verify_case.timeout_seconds` (unchanged, #156) |
| `regression-red --case id` | exists (status.py:16) | unchanged; ids are now whatever the project's `regression` printed |
| release gate (`templates/project-CLAUDE.md` 發版 row) | `status.py regression-red --source release --log` | `actl test full` with `AC_TEST_MODE=release` then `status.py regression-red --source release --result …`; same behaviour |

**T's three needs on this interface**: `demo/` unit tests → `tests.unittest.roots` (built-in) or ids T's
`select` emits; browser engine layer → T's `run` routes `browser:*` ids to its engine runner, which writes
rows (skip with reason when an engine is absent — a skip is visible in `result.json`, not a silent pass);
lock screen / leftover Safari process / low disk → T's `preflight` rc 86 with declared rows (no worker is
dispatched into that machine, D-019 §理由 5).

### §5 Claude Code hooks

`actl hooks install [--project <dir>]` writes `<root>/.claude/settings.json`:

```json
{ "hooks": {
    "SessionStart": [{"matcher": "startup|resume|clear|compact|fork", "hooks": [
        {"type": "command", "command": "sh <AC_HOME>/scripts/session-hook.sh", "timeout": 60}]}],
    "SubagentStop": [{"hooks": [
        {"type": "command", "command": "python3 <AC_HOME>/scripts/agent-cost-hook.py", "timeout": 10}]}],
    "SessionEnd":   [{"hooks": [
        {"type": "command", "command": "python3 <AC_HOME>/scripts/agent-cost-hook.py", "timeout": 10}]}]
} }
```

Rules: `<AC_HOME>` above is written out by the installer as the clone's **absolute** path (the example keeps a
placeholder because A's own guard test forbids home-directory paths in docs). Hooks run without A's env; `$CLAUDE_PROJECT_DIR`
is still what the hook uses to find the **project**, §1 rule 3). "Managed" = an entry whose command names one
of A's hook scripts (`session-hook.sh`, `agent-cost-hook.py`, `guard-main.sh`, `main_guard.py`) at any path;
`install` rewrites managed entries to the current `AC_HOME` set, deletes managed entries whose target file does
not exist (T's `scripts/control/guard-main.sh`, HANDOFF item 5), leaves every other entry untouched, keeps
key order, prints a unified diff, rc 0. `check` prints the same diff and exits 1 when `install` would change
anything, 0 otherwise, 2 when the file is not valid JSON. `session-hook.sh` runs `actl doctor` and shows its
rows on the opening page (so a drift is seen at the next session start). A's own `.claude/settings.json`
becomes a managed instance too (dogfood): `actl hooks check` in A is green; `test_session_hook.py` A1 keeps
pinning the shape, now via the installer.

### §6 Board

`actl board [--port n]` → `python3 $AC_HOME/board/board.py` with `AC_ROOT` = project; `port` from the
project's config (`DEFAULT_PORT` 18905 measured) or the flag; bind `127.0.0.1` only (unchanged, board.py:1210).
`board.key_file` as C9. T's portal proxies to it and decides whether to set the key (D-071 原話). `/healthz`
never requires the key (the proxy's liveness probe). No `bind` key (§9).

### §7 Retirement and migration

**A side (one retirement ticket, user-authorised for the `hard_block` row, D-071)**:

| Delete | Edit |
|---|---|
| `scripts/sync-to-project.sh` | `board/config.json`: drop the `hard_block.paths` row; add `config_version`, `tests` (map + base from gate.sh) |
| `tests/test_sync_to_project.py` | `scripts/gate.sh`: remove `map()`/`BASE` (132-210, 129) and the sync row; `ROOT` → `AC_CODE_ROOT`; phases via `testiface.py` |
| `scripts/gate.example.sh` | eight `_ac_root()` blocks → `AC_ROOT` required (set by `actl`) with the cwd rule as the only fallback; `event.repo_root()` same |
| `scripts/status.py cmd_run_tests` (moves into the adapter) | `scripts/verify.py _find_root` → `AC_CODE_ROOT` / cwd; `--case`, `--result` |
| the `.sync-manifest` / `.synced-from` / sync-header code paths (`rules.py:268`, `new-session.sh:231,253`) | `scripts/rules.py`: `roles_dir`/`models_dir` → `AC_HOME`; `is_canon` = roots equal |
| `docs/TODO.md` rows 1, 5-9 (they describe `scripts/control/`) | `templates/project-CLAUDE.md` (對照表 → `actl`), `README.md:95`, `docs/MEMORY.md:26`, `docs/CODE-MAP.md:12`, `docs/reference/role-procedures.md:50`, `docs/WORKFLOW.md` §閘門 (one paragraph: the project fills `tests`, the gate is A's), `docs/HANDOFF.md` 配套清單 items 5 and 10 |
| — | comments naming `scripts/control/` in `event.py:78-83`, `verify.py:41-45`, `heartbeat.sh:19,30`, `board.py:183-186`, `new-session.sh:166` (fix when the file is touched; a `grep -c scripts/control scripts/ board/` = 0 assertion in ⑤ makes it a one-time sweep) |

New files: `bin/actl`, `scripts/project_config.py`, `scripts/testiface.py`, `scripts/adapter-unittest.py`,
`scripts/hooks.py`, `templates/project-tests.example.sh`, `templates/project-config.example.json`,
`tests/fixtures/project/` (§8).

**T side (steps; each is a T ticket or a line in one; nothing here is an A ticket)**:

| Step | Action | Rollback |
|---|---|---|
| 1 | `git clone <A> ../agent-control` (sibling path — `templates/project-CLAUDE.md` already says `../agent-control`), `alias actl=../agent-control/bin/actl` or add `bin` to PATH | delete the clone |
| 2 | `cd T && actl doctor` → rows: config version 1, unknown keys (`land`, `protected_paths`, `verify_lint`), inbox `index.jsonl`, hooks to `scripts/control/guard-main.sh`, `scripts/control/` present. `actl migrate`; `actl inbox migrate` once (D-069 ③); add `hard_block` (HANDOFF item 1); add `board.key_file` + git-ignored key file; write `scripts/ac-tests.sh` starting with `preflight` only; set `tests.unittest.roots` | `git checkout board/config.json`; `scripts/control/` untouched so far |
| 3 | `actl hooks install` (replaces the guard-main hook, adds SubagentStop / SessionEnd) | `git checkout .claude/settings.json` |
| 4 | Rehearsal, cheapest first: (a) `actl test preflight` with the screen locked → rc 86 + rows; (b) `actl test select --mode branch < <(git diff --name-only main...HEAD)` on a landed branch, compare with what `land-ticket.sh gate` picked; (c) `actl gate --branch --ticket <n>` on that branch; (d) `actl land docs "…" tickets/<n>.json`; (e) one **simple** ticket end to end with `actl land`. Both landers share the lock (`AC_LAND_LOCK` → T's lock path) during this window | run `land-ticket.sh` — still on disk; ticket / event / report formats are A's already (synced `ticket.py`, `event.py`) |
| 5 | Three consecutive tickets landed by `actl land` (D-017 threshold; main checks `land.pass` events `by=land.sh` with `ac_sha` set) → T docs ticket: delete `scripts/control/`, `.synced-from`, `land-ticket.sh`, `test-for.sh`'s A-glue, the review / objection glue, T's `docs/roles/` copies (move T-specific additions to `memory/role/`), update T's `CLAUDE.md` | `git revert` of that ticket |
| 6 | Board: run `actl board` on a second port behind the portal with `key_file`; compare the four VISION metrics with T's `board.py`; retire T's | keep T's board until the compare passes |

**Pinning A**: T records nothing; every T event / status carries `ac_sha`. If an A pull breaks T,
`git -C ../agent-control checkout <last good ac_sha>` is the rollback, and `actl doctor` says when the project's
config is newer than that A.

**Not compatible, T must know**: `actl land docs` accepts only `tickets/ docs/ memory/` prefixes (land.sh:118);
`actl` refuses to run until `actl migrate` has run (step 2), so step 2 precedes everything.

### §8 How A tests "run against another project"

- `tests/control_harness.py`: `make_repo()` stops copying `SCRIPT_FILES` into `<sandbox>/scripts/`
  (measured 274-281). Scripts run from `AC_HOME` = `$ROOT` of A (or `AC_TEST_SCRIPTS`, kept for mutation red),
  with `AC_ROOT=<sandbox>` in the env and cwd = sandbox; one `ac_env()` helper is the single place. The
  `gate_stub` goes into a per-test `<home>/scripts/gate.sh` copy of A's scripts (what today's sandbox copy did,
  moved one directory over) and `AC_HOME` points at it — so "was the gate called" stays a marker file.
- `tests/fixtures/project/` (committed): a minimal project — `board/config.json` version 2 with `tests` pointing
  at `scripts/ac-tests.sh` (the §4 shell adapter with a fake `browser:*` runner writing schema-1 rows and a
  `preflight` that reads `AC_TEST_FAKE_PREFLIGHT`), `tests/test_ok.py`, `demo/tests/test_demo.py`, `verify/` with
  one tag. The harness can also load it as the sandbox instead of the hand-built one.
- Which suites run against the fixture: `test_gate`, `test_land`, `test_verify_case`, `test_status`,
  `test_board`, `test_session_hook`, plus new `test_ac.py`, `test_project_config.py`, `test_testiface.py`,
  `test_hooks.py`.
- New cases (red today):
  - V1 `bin/actl` with cwd in a sandbox worktree → `AC_ROOT` = sandbox main repo, `AC_CODE_ROOT` = worktree;
    cwd outside any git tree and no `-C` → rc 2, nothing written; `-C` wins over cwd; `CLAUDE_PROJECT_DIR` wins
    over cwd.
  - V2 static: `grep -c '_ac_root\|dirname "\$0")/\.\.' scripts/*.sh` = 0 and `grep -c 'scripts/control' scripts/ board/` = 0.
  - V3 dogfood: `realpath AC_ROOT == realpath AC_HOME` → `land.sh` runs `$WT/scripts/gate.sh`; otherwise
    `$AC_HOME/scripts/gate.sh` (marker proves which).
  - V4 `config_version` 1 → every `actl` command rc 2 naming `actl migrate`; version 3 → rc 2 naming `git pull`;
    `actl migrate` on the T-shaped config (fixture copy with `rules.roles_dir`, `land`, `protected_paths`) produces
    version 2, deletes the two `rules` keys, keeps `land`, prints both; running it twice changes nothing.
  - V5 `actl doctor` on a sandbox with `reports/inbox/index.jsonl`, `scripts/control/`, a hook to a missing
    file → three rows each naming its fix command; rc 1.
  - V6 `select` rc 0 empty stdout → gate rc 2; `unmapped` → rc 3 after running the mapped ids; `select` rc 3 →
    gate rc 3 with the stderr shown.
  - V7 `preflight` rc 86 with rows → `status.json.state == env_suspect`, `environment_suspect` equals the rows,
    no auto-fix marker, one inbox decision page; mutation: gate treats 86 as red → V7 red.
  - V8 `run` rc 0 with zero ids / missing `result.json` / a `fail` row → gate rc 2, not 0 (§5.5); rc 86 with
    `environment_suspect: []` → rc 2.
  - V9 flake rerun calls `run <id>` exactly `flake_auto_single_runs` times for a failing `browser:*` id (the
    fixture runner counts calls) — the rerun no longer spells `python3 -m unittest`.
  - V10 `status.py done --result` yields `failures[]` with `case/file/line/excerpt` from the rows; the seven-key
    `environment_suspect` normalisation unchanged (`test_status` pins).
  - V11 verify-case `red` on the fixture: `AssertionError` row in a `verify.files` file → counts; row with
    `exception: ImportError` → "import 失敗(不算紅)"; row with `exception: null` → unmeasured rc 2, ticket
    untouched.
  - V12 unittest adapter: `select --mode branch` with `tests.unittest.map` reproduces today's `map()` for the 32
    rows (table-driven test, data copied from gate.sh before deletion); `run` writes schema 1 with
    `exception/file/line` filled; `ENVIRONMENT-SUSPECT:` print → `environment_suspect` row + rc 86.
  - V13 `actl hooks install` twice = one diff then none; a foreign hook entry survives byte for byte; a managed
    entry pointing at a missing file is removed; `check` rc 1 after editing a managed command.
  - V14 board `key_file`: `/` 401, `/?key=k` 302 + cookie, cookie → 200, `/healthz` 200 always, wrong key 401
    (port 0 as `test_board.py:29`).
- Cost: V2 is static; the fixture adds ~10 % runtime (inferred). Paid in A's gate (D-016), 0 per ticket's agent
  context.

### §9 Not doing (and why)

- **Not** `board.bind` / `0.0.0.0`: T's portal proxies (D-071 原話); a key gate on loopback is enough.
- **Not** auto-`git pull`, an "A is behind origin" network check, or a per-project pin file: updating A is a
  human act; `ac_sha` in events + `actl doctor`'s version compare are the record and the detector.
- **Not** a package / installer (pip, brew, curl|sh): `git clone` is the install (原話); `bin/actl` needs only
  `sh` and `python3`.
- **Not** a template / model-card override directory: the role-card overlay exists and no second overlay has a
  known reader; a project that needs a different dispatch template files an A ticket.
- **Not** a multi-project registry or a daemon / RPC: `actl -C` and cwd cover "which project"; D-053 ③減法.
- **Not** a new gate, rc or ticket field (D-053 ④, D-018): rc 3 / 2 / 86 keep their meanings; `status.json`
  keeps its keys; `environment_suspect` keeps D-019's seven keys.
- **Not** folding `verify_case.timeout_seconds` into `tests.timeout_seconds` now (#156 landed this week; one
  migration later if it ever matters).
- **Not** reading, patching or testing anything inside T from A; T's `browser-tests.py`, `preflight-mac.sh`,
  `test-for.sh` are T's.
- **Not** changing `land docs` prefixes, the land lock, or any land refusal rule.

## ③ Rejected alternatives

**甲 — Keep sync but make it correct (the D-069 ② design: computed closure, one list, fail on missing dependency).**
Rejected by D-071 原話「不再做額外sync」. Even done right, every A interface change still needs a human-authorised
sync into each project, and the copy in the project is a second shape that drifts the day it is copied (T's 8
breakages happened **with** a correct sync). Per future ticket: costs one sync round per A change (~50K tokens
T-reported cadence) plus the silent window between A landing and T syncing.

**乙 — Keep each script as its own entry (no `bin/actl`), fix only the root rule.**
Rejected: the root resolution, the `config_version` check and `AC_HOME` export would be repeated in ~14 entry
scripts (today eight already carry a copy of `_ac_root()`, measured), and dispatch prompts would again carry
`~/agent-control/scripts/<name>` paths that differ per machine. One dispatcher is one copy. Per future ticket:
costs a path per command in every prompt and a per-script version check that will be forgotten in one of them.

**丙 — `select` may return `run: <shell>` lines and A keeps parsing unittest logs (the previous version's C5).**
Rejected: it keeps "how to run" half in A (log parser, `python3 -m unittest <id>` reruns) and half in the project,
so T's browser layer could not be rerun or classified. The result file (C6) is the shape that lets A keep
statistics, rerun ids and red classification **without** knowing the runner; the unittest adapter keeps the
in-process statistics for projects that have none of their own. Per future ticket: 丙 would cost one worker round
per browser-layer flake that A cannot rerun.

**丁 — A per-project config file at `.agent-control/config.json` (new location, "cleaner").**
Rejected: every reader is `event.config()` on `board/config.json` (measured) and T already has that file; a
second location is a second shape (D-018) and buys nothing per ticket.

**戊 — Config version as a semver string / A refuses only on major.**
Rejected: a single integer with one migration function per step is what `actl migrate` can execute; "minor
compatible" is exactly the judgement that produced T's silent breakages. Per future ticket: 0 either way; the
integer removes a judgement call.

**己 — Project-level `AC_HOME` recorded in `board/config.json` (so `actl` is found from the project).**
Rejected: the clone location is per machine, not per project; it would land a home-directory path in git
(`test_no_project_names` family). The shell alias / PATH entry is the per-machine fact and stays per machine.

## ④ Migration

- **A files that change**: see §7 table; new files listed there. `gate.sh` without a `tests` section behaves
  as the unittest adapter with an empty map — which for A is **not** today's behaviour, so A's `config.json`
  gets the map in the same ticket that deletes `map()` (⑥ row 3b), with V12 pinning equality.
- **Two shapes coexist**: in A, none (the retirement ticket deletes sync in the same ticket that lands the
  dispatcher's docs). In T, the synced `scripts/control/` and A's clone coexist from step 2 to step 5 of §7
  (three tickets); the land lock is shared; `actl` and `land-ticket.sh` write the same ticket / event / report
  formats because T's `scripts/control/` **is** an older A.
- **Existing data**: tickets, events, `reports/`, `status.json` unchanged. `board/config.json` gains two keys
  via `actl migrate` (reviewable diff, landed by the project's docs channel). `reports/inbox/index.jsonl` → `actl
  inbox migrate` (D-069 ③, already decided).
- **Order**: ⑥ rows in order; T steps only after rows 1–6 have landed in A and T has pulled.

## ⑤ Acceptance (one assertion per conclusion)

| Conclusion | Red-today assertion / greppable change |
|---|---|
| C1 | `bin/actl` exists; `sh bin/actl version` prints `AC_HOME`, sha, `2`; `sh bin/actl nonsense` rc 2 lists commands; V1. |
| C2 | V1, V2; `grep -c 'def _find_root\|__file__' scripts/verify.py` shows no `__file__`-based root. |
| C3 | V3; A's `board/config.json` has `config_version` and `tests.unittest.map`; `actl gate --base` in A runs `test_ticket test_event test_memory test_no_project_names` (today's `BASE`). |
| C4 | V4, V5; `scripts/project_config.py` has `CURRENT_VERSION = 2` and `MIGRATIONS`; `session-hook.sh` output contains the doctor rows when the sandbox is at version 1. |
| C5 | `grep -c roles_dir scripts/rules.py` = 0; rules pack in the fixture project appends "### 本專案 memory/role/worker.md" after A's card (existing test re-pointed). |
| C6 | V6–V10, V12; `grep -c 'python3 -m unittest' scripts/gate.sh` = 0; `grep -c 'def cmd_run_tests' scripts/status.py` = 0. |
| C7 | V11; `grep -c 'subprocess.run(\[sys.executable, "-m", "unittest"' scripts/verify-case.py` = 0. |
| C8 | V13; `grep -c '請貼\|print_hooks' scripts/` = 0 (the paste-two-lines text is gone with sync). |
| C9 | V14; `grep -n compare_digest board/board.py` ≥ 1. |
| C10 | `test -e scripts/sync-to-project.sh` fails; `grep -rc 'sync-to-project\|scripts/control\|\.synced-from' scripts board templates README.md docs/WORKFLOW.md docs/MEMORY.md docs/TODO.md` = 0; `board/config.json` `hard_block.paths` has two entries. |
| C11 | `tests/fixtures/project/board/config.json` exists with a `tests` section naming a shell adapter; `test_gate test_land test_verify_case` green against it; mutation: point `AC_TEST_SCRIPTS` at a copy of `scripts/` with `testiface.py` ignoring rc 86 → V7 red. |

## ⑥ Follow-up work (model table)

| # | What | Ticket | Model | Depends on | Size (inferred) | One-line reason |
|---|---|---|---|---|---|---|
| 1 | `bin/actl` + root rule: delete eight `_ac_root()` blocks, `event.repo_root()` cwd rule, `AC_CODE_ROOT` in gate/verify/verify-case, `land.sh` dogfood gate rule (C3), `ac_sha` kv; harness `ac_env()` + no script copies; V1–V3 | 未開 | opus | — | L (touches 10 scripts + harness) | Foundation every other row runs on; the root bug is silent, so the harness switch must come with it. |
| 2 | `scripts/project_config.py`: schema table, `CURRENT_VERSION`, `migrate_1_to_2`, `actl doctor` / `migrate` / `init`, `actl` version check, `session-hook.sh` doctor rows; V4–V5 | 未開 | opus | 1 | M | Policy-shaped (what is an error vs a warning); needs the T config facts from HANDOFF items 1-7. |
| 3a | `scripts/adapter-unittest.py` (from `status.py run-tests`) + `result.json` schema 1 + `status.py done --result` + `verify.py --result/--case`; A's `tests.unittest.map/base` data; V10, V12 | 未開 | opus | 1 | L | The runner move must keep `EnvironmentResult` statistics and `LogStream` behaviour exactly (D-019). |
| 3b | `scripts/testiface.py` + `gate.sh` phases on the interface (preflight / select / run / full / regression, flake rerun by id, delete `map()`); `templates/project-tests.example.sh` + example config; fixture project; V6–V9 | 未開 | opus | 3a | L | Rewires the 1,079-line gate around one seam; the fixture with a fake browser runner is the T rehearsal. |
| 3c | `verify-case.py red/check` through `regression --case` with `AC_CODE_ROOT` per copy; classification from result fields; V11 | 未開 | opus | 3a, 3b | M | D-020 red shapes must survive the switch; the unmeasured path is the safety net. |
| 4 | `scripts/hooks.py` (`actl hooks install\|check`), A's own settings as a managed instance, `test_session_hook` re-pointed; V13 | 未開 | sonnet | 1 | S | Self-contained JSON rewrite with a diff; the shape test exists. |
| 5 | `board.py` `key_file` + `actl board`; V14 | 未開 | sonnet | 1 | S | Self-contained HTTP change; port-0 harness exists. |
| 6 | Retirement: delete sync script + test + `gate.example.sh`, `hard_block.paths` row (**user-authorised, main lands**), the §7 edit list, `templates/project-CLAUDE.md`, `README.md`; C10 assertions | 未開 | sonnet | 3b | M (mostly deletes) | Text + deletes; depends on 3b so no gate row still names `test_sync_to_project`. |
| 7 | Docs: `docs/WORKFLOW.md` §閘門, `docs/MEMORY.md`, `docs/CODE-MAP.md`, `docs/TODO.md`, `docs/DECISIONS.md` pointer, `docs/HANDOFF.md` T list items 5/10 | 未開 | sonnet | 6 | S | Docs channel. |
| 8 | T side: §7 steps 1–6 | T 未開 | — | 1–7 pulled | — | Main dispatches after A rows land; T writes `scripts/ac-tests.sh`. |

Order: 1 → 2 → 3a → 3b → 3c; 4 and 5 run in parallel with 2–3; 6 after 3b; 7 last; 8 after T pulls.
Rows 1, 3a, 3b are `complex` (reviewer pass required); the rest `simple`.

### Questions for the user (with recommendation)

- **Q1** Does T accept writing `scripts/ac-tests.sh` (preflight + select + run for the browser layer) in the
  §4 shape, with the unittest adapter for `demo/` and `tests/`? Recommendation: yes; T starts with `preflight`
  only (step 2) and adds the rest when the browser runner writes schema-1 rows.
- **Q2** Where does T clone A: sibling `../agent-control` (what `templates/project-CLAUDE.md` already says) or
  `~/agent-control`? Recommendation: sibling; nothing in A depends on it (`AC_HOME` is derived), only T's alias.
- **Q3** The entry name `actl` may collide with macOS `/usr/sbin/ac` (login accounting) — **?** not verified
  here. Recommendation: keep the file `bin/actl` and have T alias it (`alias actl=../agent-control/bin/actl`), or
  prepend `bin` to PATH; rename only if the Mac check shows a real collision in T's shell.
- **Q4** Should `actl` refuse to run when the project's `config_version` is **newer** than A's (A behind)?
  Recommendation: yes (C4); the alternative is the silent class T just reported.
- **Q5** Row 6 deletes `scripts/sync-to-project.sh` and edits `hard_block.paths` — D-071 原話「不再做額外sync」is
  read as that authorisation. Recommendation: main lands row 6's `hard_block` edit itself as the one-time
  user-authorised change; no further authorisation needed.
