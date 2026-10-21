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
