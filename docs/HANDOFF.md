# 交接

## 2026-09-12 v0.1 建 repo
- 由 tabby_pool 主線(Fable)設計文件與契約;程式部分(`scripts/`、`board/`)由 Opus 從 tabby_pool 抽出並去專案名,見 `docs/TODO.md` §1 打勾狀況。
- **還沒有任何專案用過這個 repo。** 第一個使用者會是 tabby_pool 自己(TODO §0)。
- 下一個 session 該做的:照 `docs/TODO.md` §1 沒打勾的順序做,每做一項 `ticket.py` 開一張票、發事件,讓控制台從第一天就有資料。

## 2026-09-12 午前:程式部分落地,quickstart 在乾淨 clone 走通
- `scripts/{ticket,event,memory}.py`、`{land,gate,heartbeat,new-session}.sh`、`board/board.py`、`code-map/check-stale.py`、通用 `DISPATCH-TEMPLATE.md`,**145 條測試綠**(主線自己跑過),11 條變異各紅在對的地方。
- **真跑過**:乾淨 clone → `new-session.sh` → `ticket.py create` → `event.py tail` → `board.py --port 0` → `heartbeat.sh` → `ticket.py verify`。README 的 quickstart 就是那一趟的指令。
- 走的時候抓到兩個新來的人會卡的地方(子指令 `--help` 空、不認得的旗標不說認得哪些),已修;`--help` 裡的範例有測試真的拿去跑。
- 裁示:D-006(模型私有記憶 2K、老師帶學生、上限可打破但要理由)、D-007(模型討論標準格式,整理沒有討論檔不准跑)。
- `docs/REHEARSAL.md` 有七條「還只在演練裡成立」的保證——**下一個 session 第一次真的用到某條時,確認過就把那一列刪掉**。
- 還沒做:`docs/TODO.md` §0 遷移(第一個使用者是 tabby_pool 自己)、§2 第二版。控制台只綁 127.0.0.1、沒有 key 驗證。
- 主線自己踩的坑記在 `memory/model/fable.md`(`git add -A` 掃進別人未完成的檔)。

## 2026-09-21 流程定案:調度員退場、驗證者只寫案例、狀態檔進腳本
- **角色只剩七個**(`docs/ROLES.md`):產品負責人、主線、開題者、Worker/實作者、驗證者、落地器(腳本)、知識維護。
  **調度員 / 排程器整個拿掉**(D-010,不是瘦身):順序由主線決定或由腳本依 `allowed_write_paths` 提案;
  `memory/role/dispatcher.md` 已刪,`scripts/new-session.sh` 的 `scheduler` 角色換成 `opener` / `verifier`。
- **驗證者只寫案例**:證明案例是對的(乾淨主線紅、patch 綠)、登記 tag、把怎麼跑寫進票的 `verify` 欄、交
  `patch-verify.diff`、結束。**VERDICT 退場**;主線的「覆核 = 讀 patch 記 `review`」留著,那不是驗證者。
  票多兩格:`verify`({files,tags,run,notes})與 `review`,`ticket.py create` 有 `--verify-file/-tag/-run/-note`。
- **狀態檔已經是程式**:`scripts/status.py` + `gate.sh --ticket <n>` + `land.sh`,寫 `reports/t<n>-status.json`
  (state / rc / failures 逐條含 log 與 excerpt / flaky)。gate 紅了會把每條紅的案例**單獨重跑一次**判 flake,全 flaky 視為綠。
- **規格已定、腳本還沒做**(`docs/WORKFLOW.md` 有一張表逐列標著):落地器用 headless `claude -p` 自動起新 worker
  (三輪上限)、land 前檢查 `verify.files` 在不在分支上、land 那一側的 flake 重跑。**不要當成已經做了。**
- 下一個 session:`docs/TODO.md` §2 第一條(排序做成腳本)與上面那張表沒打勾的三項;測試 `python3 -m unittest discover -s tests`(現在 172 條)。

## 2026-09-21 外部審查處置(D-014)+ 記憶整理裁示(D-013)
- **上一節那句「全 flaky 視為綠」已經被推翻了。** 外部審查(Codex astra)的實測反例:
  第一條測試污染共用狀態、第二條檢查乾淨狀態 —— 整組必紅、乾淨程序單跑必綠,所以舊規則
  **每一次都把順序依賴的 bug 判成偶發**。現在單跑綠只標 `suspected_flaky`,rc 不動,
  再用原順序整組重跑一次判真紅。逐條處置:`docs/review/2026-09-21-astra-workflow-review.md`。
- **狀態檔換形狀了**:`reports/t<n>/<run_id>/status.json`,一輪一個目錄、不覆寫,
  log 複製一份進去,帶 `repair_context`(交接包)與 `phases`(gate/merge/push 分開記)。
  讀舊路徑 `reports/t<n>-status.json` 的東西要改。
- **land 多了三道拒絕**:互斥鎖(`.land.lock`)、覆核(`review` 要綁票版本與分支 sha)、
  未處置的阻擋反駁(`objections[]`)。land **不關票**,它印「已合併、尚未關票」。
- **新工具** `scripts/verify-case.py`:`check`(乾淨主線該紅 / candidate 該綠,證據寫進票的
  `verify.baseline`;import 失敗不算紅)、`extract`、`tags-merge`。新 tag 改成一票一個片段
  `verify/TAGS.d/<票號>.md`。
- **`ticket.py` 多了**:`objections[]`、`--expect-attempt` / `--expect-state-version`(過期回報 rc=4)、
  票庫寫入鎖、`round <n> <r> --red`(三輪耗盡轉 Blocked、owner=main)、
  `close` 與 `set state Done` 共用同一份必要條件(弱檢查不准自動關票)。
- **D-013 記憶整理**:任一層記憶超標由**兩個模型**討論後整理;整理後**只留原則,不寫案例**
  (案例用票號引用);記憶與紀錄分開,紀錄類用 grep 不整份讀。`memory/model/*.md` 已照這規矩重寫。
- **自動派新 worker 這一輪沒做**(主線裁示,照審查總評的順序)。`docs/WORKFLOW.md` 的能力表
  已經逐項對著真實入口重寫,**未實作的那幾列寫明現在由誰手動做** —— 不要當成已經做了。
- 下一個 session:`docs/TODO.md` §2 沒打勾的那幾項;測試 `sh scripts/gate.sh --full`(現在 215 條 + 回歸層)。

## 2026-09-21(晚):規格已定的那七件事現在都有入口(D-015)

上一節說「自動派新 worker 這一輪沒做」。**這一輪做了**,連同 D-014 留在「未處置清單」
裡的另外六項。每一支都有測試,全套 292 條綠(`sh scripts/gate.sh --full`)。

| 入口 | 一句話 | 退出碼要看的 |
|---|---|---|
| `sh scripts/apply.sh <n> <patch> [<patch-verify>]` | 套 patch → 開 `t<n>` 分支與副本 → commit(訊息帶票號 + patch sha256) | 3 檔頭 / 預檢、4 套完對不上、5 越界 |
| `sh scripts/apply.sh rebase <n> <patch>` | 三向合併(祖先 = 票的 base_sha,我方 = 當前主線,對方 = base_sha + patch),跑 `apply.regen_cmd`、出乾淨 diff | 2 祖先取不出 / 3 patch 套不回 base_sha 或衝突(指名檔與行號)/ 4 空 diff(#17;以前的『`.rej`≠0 一律失敗) |
| `sh scripts/auto-fix.sh <n>` | 讀最新狀態檔,紅就派**新** worker,三輪上限 | 0 綠(停在 InReview)、1 三輪耗盡、3 反駁、4 沒有歸因、5 沒交 patch |
| `sh scripts/gate.sh … --auto-fix` / `sh scripts/land.sh … --auto-fix` | 把上面那一支掛在閘門 / 落地後面 | 閘門自己的 rc **不會**因此變綠 |
| `python3 scripts/inbox.py list\|show\|ack` | 終態一頁四句,開場印一次 | — |
| `python3 scripts/rules.py pack <角色> --model <模型>` | ≤ 4 KB 的派工文前言,砍掉的會指名 | 1 = 壓不到上限(那是這一支壞了) |

其他:flake 累計達門檻**自動開修復票**(同一案例只開一張,`flaky_auto_ticket: false` 可關);
land 拒收 `verify.files` 沒帶進來的分支(**rc=4**);`verify.py` 在同一個 `AC_RUN_ID` + 同一個 sha
下只跑一次同一組標籤(`--no-cache` 關);遷移步驟寫成表(`docs/TODO.md` §0),
`sync-to-project.sh` 把控制腳本同步到專案的 `scripts/control/` 並印出接點。

**在乾淨 clone 裡走過兩遍**(2026-09-21):
① new-session → create → `apply.sh` → gate → inbox → review → `land.sh`(全套 289 條綠)→ `close`
(close 正確地擋下來:那張票沒有回歸證據 —— D-014 的 Done 契約在守);
② 紅的票走 `gate --auto-fix`:派工 → 第 2 輪 patch → `apply.sh` 進同一條分支 → 閘門綠 → 票停在
`InReview`、收件匣寫「等覆核」。走的過程抓到三個只在乾淨 clone 才會現形的 bug
(開場那一行的反引號、`apply`/`auto-fix` 在副本裡算錯 repo 根、同一輪兩則收件匣互蓋),
都已修並各補一條會紅的測試。

**還沒做、而且是刻意的**:一批多張票落地時的歸責(要票↔案例的對照;猜錯的歸責比不歸責更貴)。
`docs/TODO.md` §0 的遷移**步驟**做完了,遷移**本身**還沒開始 —— 那要在那個專案裡做。

下一個 session:`docs/TODO.md` §2 剩下的(控制台驗證、排序腳本、推測性佇列、token 歸因、
產品功能地圖、多 repo),或直接開始 §0 的遷移。

## 2026-10-21 交接給雲端主線

本機主線(Mac)在 2026-10-21 停工,之後由雲端 session 當主線。`reports/`、`board/events.jsonl`、`../agent-control-wt/` 都不進 git,雲端看不到;下面是它們裡面你需要知道的事。最上層判準是 `docs/VISION.md`;這一波減法的裁示是 D-053〜D-059。

### 減法(D-054)進度
已落地並關票:#134 #135 #136 #137 #138 #139 #140 #141 #142 #143 #144 #145 #148,以及試走票 #150(D-057 ② 的「新流程從開題走到落地」已通過)。#104、#106 Cancelled(分支 t104 8692063、t106 19c7c7c 已推上 origin 留存,不落地)。

### 開著的票
| 票 | 停在哪 | 分支 / 頭 | 閘門 / 覆核 | 證據 |
|---|---|---|---|---|
| #146 needs_verifier 缺 = true、小件清理(simple) | 閘門綠;**land 時合併衝突**:雲端 worker 從舊 clone 的 main(685af04)開分支,與 #139/#142/#143/#145 撞 6 檔(board/README.md、board/board.py、scripts/auto-fix.sh、tests/test_board.py、tests/test_ticket.py、tickets/SCHEMA.md)。已派雲端 rebase,**結果未收**:看 origin 有沒有 `t146-r2-cloud` 且含 `EVIDENCE-t146r2.md` | `t146` 77f591a(origin 已推;= `origin/t146-cloud~1`) | 閘門 rc 0(舊基底);simple 不覆核 | worker 證據在 `origin/t146-cloud:EVIDENCE-t146.md` |
| #149 主線驅動雲端流程固定進 agent-control(complex、needs_verifier) | Ready,票面已照實測重寫(05151e2);depends_on #146 | 無 | 未跑 | 票面本身 |
| #147 文件整併 + T 端配套清單(simple) | Ready;依賴減法其餘票(#146、#149 落地後做);T 端清單在 outline,另加:#143 的 `migrate_acked_jsonl` 在 T 同步並跑過一次後移除 | 無 | 未跑 | 票面本身 |
| #93 交件前主線對齊 | Blocked、凍結:「繼續凍結到 DAG 排程(#119)落地」 | 無 | — | — |
| #117 #118 #119 #120 #123 #124 #125 | Draft;VISION 待確認:#117、#120 等減法做完再做 | 無 | — | — |

### 雲端 worker 的做法(#149 落地前照手動做)
1. 主線用 routine API(`ac-worker`,trig_01QywoxG8TwSBMex4o6Rn9nR,opus,disabled、只手動 run)**先 update 派工文、再 run**(同時送會讀到舊的)。派工文第一步一定要 `git fetch origin`。
2. worker 推 `t<n>-cloud`,最後一個 commit 只加 `EVIDENCE-t<n>.md`;驗證者另起一個 session,從 `origin/t<n>-cloud~1` 開 `t<n>v-cloud`。
3. 等交件:每 20 分鐘 `git fetch` + `git cat-file -e origin/t<n>-cloud:EVIDENCE-t<n>.md`(使用者允許的輪詢,D-058 補)。
4. 收件:確認最後一個 commit 只有 EVIDENCE → `git branch -f t<n> origin/t<n>-cloud~1`、開 worktree、`ticket.py set <n> base_sha / branch` → `gate.sh --branch --ticket <n>` → complex 才 `review.sh` → `land.sh t<n>`(全套紅時本機用 `AC_LAND_DEPTH=9` 不自動派本機 worker)。
5. 下一張要等前一張落地再從 main 開,否則合併衝突(#143、#146 都撞過)。

### 一定要在 Mac 上做的
- tabby_pool 的瀏覽器測試(Safari / Chrome 引擎、`scripts/test-for.sh --checkin`)與發版(`scripts/build.sh` → `deploy-prod.sh`,prod 資料在 `~/tabby-data/prod/`)。
- 本機工具:`land.sh` 的落地全套(約 38 分鐘)、`gate.sh`、本機 worktree、`sync-to-project.sh` 同步到 tabby、codex / agy CLI。
- tabby 的收件匣(`../tabby_pool_ctl/`)與 launchd 服務。

### 還沒裁 / 待辦
- 使用者:VISION 的 worker 順序寫 sol→opus,現在雲端排第一格(D-058),VISION 由使用者回寫。
- 2026-11-19 前後:拿 T 的反駁往上送比例回來問使用者(D-054 Q8)。
- 減法前後對比報告(D-055 ③ / D-057 ①):增刪行數、token、落地全套耗時。減法前全套基準:unittest 1155 條 1606 s + 回歸 340 條 686 s ≈ 38 分鐘(2026-10-19)。
- 擱著的新規則(D-053 減法期間不開):worker 只跑 1 輪沒用工具沒交件時自動重起一次。
- tabby 端:A 整個改好後才改 T(使用者原話);T 的 guard-main 還在(A 已刪)。

## 2026-10-22 雲端主線第一輪(給下一個主線)

雲端主線從 2026-10-21 接手(D-059)。這一輪在雲端走通了完整流程,減法 A 端做完並宣告完成(D-063 ②)。

### 現況
- 落地:#151(root 下測試)、#146、#149(`auto-fix.sh <n> --prepare`,D-061)、#147(文件整併,減法最後一張)、#152(子 agent token 進 cost[])。
- 落地(續):**#153**(d1d3bf9,complex、覆核 pass)—— `scripts/main_guard.py` 四個比對(HEAD / origin / 主 repo 工作樹 / hard_block.dirs)取代 land_attribution 與 rc 8 快照守衛;`--prepare` 那條路收件拒收是 apply rc 7,每次收件只比收的那一輪自己的快照。auto-fix.sh 3,414 → 3,203 行(沒到 2,959:歸屬判斷約 310 行已移進 main_guard.py,再砍只剩本票沒碰的派工 / 驗證者註解,worker EVIDENCE 理由)。A 端開著的票只剩凍結的 #93 與 Draft。
- 待查(沒開票):apply.sh 反駁去重太粗 —— #153 驗證者那筆 ticket-wrong 與 worker 那筆被當成同一筆沒記進票,主線手動補記(ba8c41d)。
- 其他開著的:#93 與 7 張 Draft 照舊凍結。

### 雲端主線的做法(實測過)
- 派工:`auto-fix.sh <n> --prepare worker|verifier` 印出四件事,原樣貼給 Agent 工具的子 agent(背景跑,交件主動回報)。被擋或紅 → 用 SendMessage 叫回同一個子 agent,不重新 prepare。手派(開題者、反駁驗證者)直接給四件事。
- gate / land 一律帶 `--no-auto-fix`(不加,紅了會起 headless worker)。覆核由 gate 綠後自動叫 `review.sh`(headless `claude -p`,雲端可用)。
- 票檔 / DECISIONS 一律 `land.sh docs`;本機要在 `main` 分支上跑。落地時 docs 通道會被 `.land.lock` 擋,等落地完再推。
- 容器 2026-10-21〜22 重啟 3 次:背景工作會被殺、子 agent 結果要從 tasks/*.output 讀。每收完一步就把票檔推上 main,別讓狀態只留在容器。
- GitHub App 要有 Contents 寫入(D-060);沒有的話 push 與 API 都 403。

### 使用者待辦(在 Mac 上)
- `scripts/sync-to-project.sh` 第 466 行「(不進票 cost[])」:#152 落地後要加 --prepare 例外;hard_block 檔,使用者自己改(D-065 ③)。
- **同一支 `scripts/sync-to-project.sh` 的 SCRIPT_LIST 要加 `scripts/main_guard.py`**(#153 新模組):#153 落地後、下一次同步到 tabby 之前一定要加,否則 tabby 的 auto-fix.sh import 失敗、apply.sh 回 rc 7(#153 反駁,deferred)。
- VISION:使用者自己加「執行位置(本機/雲端/子 agent)由主線依額度決定」(D-063 ④)。
- T 端(tabby):第 6 題「何時開始、要不要搬雲端」還沒裁;主線建議報告後開 T 票,寫碼與 Chrome 測試放雲端,Safari、發版、同步留 Mac。
- 2026-11-19 前後:T 的反駁往上送比例(D-054 Q8)。減法報告第二段:T 端跑完 10 張票後補每票成本對比減法前 30 天(D-063 ②)。

### 減法簡報
Claude Docs 文件「Agent Control 減法改動簡報」:https://claude.ai/artifact/RQKo3FxGWL1oFxTetQVwKR(含一覽、流程前後、雲端主線、待決題與裁示、第一段報告)。

### #147 的 T 端配套清單(D-062;原文抄自 #147 worker EVIDENCE,該檔在 worktree 外、不進 git)
讀不到的條目標「未讀」,要在 Mac 上補(`../tabby_pool_ctl/tickets` 不在 GitHub)。

Source: `sai99claw/tabby_pool` default branch, commit 7a5c0cfb (read 2026-10-21 through the GitHub file-content tool; read-only, nothing written).
**Limits**: cloning T and processing the downloaded file in the shell were refused by this session's permission classifier, so line numbers below are hand-counted from the raw file (±1, marked) or missing (marked "未讀"). T's `tickets_dir` is `../tabby_pool_ctl/tickets` (outside the repo), so old tickets (item 11) cannot be read from GitHub.
`scripts/control/.synced-from` = `990d739a37f3b99f1dfdb79c3cf89aed24f0e623` (read). `scripts/control/` holds apply.sh, auto-fix.sh, event.py, guard-main.sh, heartbeat.sh, inbox.py, memory.py, new-session.sh, repo-map.py, review.sh, rules.py, session-hook.sh, status.py, ticket.py, verify-case.py, verify.py, wtbase.sh — no agent-cost-hook.py, transcript-cost.py, role-models.py (A's SCRIPT_LIST, scripts/sync-to-project.sh:102, has them).

| # | T 現況 (file:line) | 改成 |
|---|---|---|
| 1 | `board/config.json`: no `hard_block`; `protected_paths` :110-115 = DECISIONS.md, scripts/build.sh, scripts/deploy-prod.sh, demo/data/** ; kill/port regexes live in `verify_lint.forbid` :83-94 (os\.kill, killpg, pkill, kill -, 1890[3-5], 18889, 15349, TABBY_BROWSER_ENGINES=) (hand-counted ±1) | add `hard_block` {paths: docs/DECISIONS.md, docs/VISION.md, scripts/build.sh, scripts/deploy-prod.sh; dirs: ~/tabby-data/prod/; patterns: 4 kill + 18903/18904/18889}; drop demo/data/**; remove `protected_paths` and `verify_lint` (case lint retired #138); land-ticket.sh / test-for.sh call ticket.py's hard_block check. land-ticket.sh has 0 occurrences of `protected_paths` (Grep -o on the fetched file), so where T enforces it today is 未讀 (likely the synced ticket.py at 990d739). Open question for main: outline drops 18905 / 15349 / TABBY_BROWSER_ENGINES= that T forbids today. |
| 2 | `scripts/land-ticket.sh`: contains `review_problems` x3, no `complexity` (Grep -o, no line numbers; 未讀 line-level) | require review only for complexity=complex; drop review binding |
| 3 | `scripts/land-ticket.sh`: `verify_strings` x17, `waiver` x9 (Grep -o, no line numbers); `scripts/test-for.sh` 未讀 | delete verify_strings match, verify.files checks, waiver, second close gate |
| 4 | land-ticket.sh: 未讀 (no `patch.diff` token found by Grep -o); `docs/DISPATCH-COMMON-RULES.md` §6 :79 still says deliver `diff -ruN … > $W/patch.diff`; §10 :~118-122 "交付後清副本" assumes archive copies (hand-counted) | receive the ticket-branch commit, not a patch (#144/#145 form) |
| 5 | `.claude/settings.json` :15-26 PreToolUse matcher Bash\|Read -> `scripts/control/guard-main.sh` (:21); no SubagentStop / SessionEnd entries; agent-cost-hook.py not present in scripts/control (hand-counted) | `actl hooks install` (replaces the guard-main.sh hook, adds SubagentStop / SessionEnd into A's clone) |
| 6 | `docs/DISPATCH-COMMON-RULES.md` §1 "工作區" :36-41: "改 `$W/work` … 沒有 `.git`" (:37), "**禁止一切 git 寫入動詞**(add / commit / … / rebase / tag / clean)" (:38) (hand-counted ±1); T config `rules.sections.worker` starts with "工作區" (board/config.json:26) so this is what T workers read | rewrite §1 to the branch-commit rule (same moment as syncing #144's auto-fix.sh) |
| 7 | `board/config.json` `memory` :95-106: `cap_chars: 2000` :96, `consolidators` [fable, codex:gpt-6-astra] :101-104, `inbox_max_lines: 20` :105; no `unit` / `raise_allowed` keys (hand-counted ±1) | drop cap_chars and inbox_max_lines (#134/#148: no fixed cap; inbox trigger is ≥5 lines or 3 days); `consolidators` list is also an old key (#122: single consolidator from role table) |
| 8 | `docs/TICKET-OPENER-PROMPT.md` :21 asks for "in-scope 檔案清單、allowed_write_paths", :23 asks for `verify_strings`, :27 ticket shape with `verify_strings`; no complexity field (hand-counted); `docs/roles/` (synced) not read | opener marks every ticket simple/complex; stop asking for allowed_write_paths / verify_strings; check T's own docs against synced docs/roles |
| 9 | `scripts/board.py` exists (T-owned); index.jsonl readers 未讀 | if it reads index.jsonl, switch to the reports/inbox/ directory (#143) |
| 10 | 未讀 on T side; A side: `scripts/sync-to-project.sh:493` prints "非產出物 … 同步不管它" for files in docs/roles not on the manifest (read) | clone A beside T, `actl doctor` then `actl migrate`; no sync run |
| 11 | T tickets live in `../tabby_pool_ctl/tickets` (board/config.json:4), not in the repo: 未讀 | leave old tickets' allowed_write_paths / verify_strings / review binding as history |

## 2026-10-26 A 是安裝即用的工具;可以交給 T 遷移

### 現況
- D-071〜D-075 全部落地:#159〜#167、#172(A 當工具)、#168、#169、#170、#171、#173、#178、#119(排程)、#179;整理票 #174〜#177。開著的只剩凍結的 #93(解凍條件「DAG 排程落地」已滿足,等使用者決定)與減法前的 Draft #117、#120、#123、#124、#125(D-075 保留)。
- 單一入口 `bin/actl`(`actl --help`)。專案 clone A 放旁邊(../agent-control),從 A 的 clone 對專案執行;更新 A = git pull。sync-to-project.sh 已退役、hard_block.paths 只剩 docs/DECISIONS.md、docs/VISION.md。
- 2026-10-26 主線實測:A 自己 `actl doctor` 無發現、`actl hooks check` 無變更;一個全新的空專案 `actl init` → `doctor` → `hooks install/check` → `test preflight` → `ticket list` 全通。

### 主線怎麼跑(取代 2026-10-22 節的做法)
- 一律從 `bin/actl` 操作(直接叫 scripts/*.sh 沒有 AC_HOME,規則包會拒絕起 agent)。
- 派工與落地交給排程:主線依使用者要求寫 `board/batches.json`(不進 git;schema 見 scripts/schedule.py 檔頭),`actl schedule plan` 看計畫,`actl schedule run` 跑(派 auto-fix、同時上限 2、依檔案重疊拆落地、落地紅修好自動重落、狀態寫 reports/schedule/)。只在終態看收件匣。
- 長跑一律 `setsid nohup` 脫離背景工具(工具 2 小時上限、容器會重啟);等待用「檔案出現」判斷,不要用 pgrep(會比對到自己)。
- 派出 worker 到收件前不落 DECISIONS / VISION(main_guard 會拒收整輪,#157 撞過)。
- A 測自己的分支時要用分支自己的 `bin/actl`(主線的 actl 會載入 main 的腳本,#119 撞過)。
- 記憶整理票:`AC_HOME=… AC_ROOT=… sh scripts/consolidate-memory.sh <n>` → `actl land docs` 記憶檔 → `ticket.py close <n> --landed <sha>`(要先落再關)→ discussions/ 也走 docs 通道(#179)。

### 給 T 的遷移
- 設計文件 docs/design/project-runnable-entries.md §1、§2、§4、§5、§7(入口名已改 actl)。T 的步驟:§7 T side 六步;測試介面 §4,範本 templates/project-tests.example.sh。
- 使用者待辦(T 端):hard_block 的 18905 / 15349 / TABBY_BROWSER_ENGINES= 要不要留(建議留)。

## 2026-10-27 A 的票清空;可以交給 T

### 現況
- 開著的票:0。D-076 之後的收尾全部落地:#124、#117(b3)、#125(b4,f524206)、#180 + #181(b5,7247ecf / a4a3755)、#182(b6,5235369)。#93 / #120 / #123 依 D-076 取消。
- `actl doctor` 無發現、`actl hooks check` 無變更、收件匣清空(全部 ack)。

### 這一輪學到的(給下一個主線)
- **案例重複被覆核退回的根因**是 test_plan 沒寫「誰寫案例」:#178 的舊規則讓沒指派的契約 worker 自己判、驗證者照 acceptance 寫,兩邊都寫(#117、#125 各多一輪)。#180 已改成:沒指派的契約,有驗證者(needs_verifier 沒寫或 true)就只有驗證者寫,沒有驗證者就 worker 寫。開題時仍建議逐條寫明寫案例者。
- **派出的驗證者量紅不碰 git**(#181 反駁 0,主線裁 B):auto-fix 起驗證者前把 base_sha 的樹與 sha 標記放進副本,派工文的 red 指令用 `--ref <那個目錄>`(#182)。agent_env 的隔離沒動;給驗證者一個 git 指標(A)與每輪手動指路(C)都不採用。
- **落地的全套會抓到票外的舊案例**:#125 落地時 tests/test_testiface.py 一條釘舊行為(86 不派)的案例紅,auto-fix 第 2 輪修好、排程自動重落一次(reland 1)——排程的「落地紅 → 修 → 重落」實際走通一次。
- `ticket.py set <n> --state X` 以前會默默寫一個叫 `--state` 的欄位(#180 A5 已改成拒絕);正確寫法 `set <n> state X`。
- `ticket.py create --depends-on 181` 寫出的 condition 是空字串,`schedule.py` 只認 `landed`;lint 會抓到,手補成 `{"id": …, "condition": "landed"}`。**還沒開票修**(小;要修就讓 create 預設 landed)。
- 容器這一輪又重啟兩次:`setsid nohup` 的鏈會一起死,接手時看結果檔停在哪一步,從那一步重接(worker 的 commit 與 EVIDENCE 都在磁碟上,不必重派)。

### 接下來
- 使用者把 T 遷移的 prompt 交給 Mac 上的 tabby session(見上一節「給 T 的遷移」);T 會問 hard_block 的 18905 / 15349 / TABBY_BROWSER_ENGINES=(建議留)。
- T 遷移時回報的問題,照「該 A 改的開 A 票、T 該 porting 的交回 T」分。
