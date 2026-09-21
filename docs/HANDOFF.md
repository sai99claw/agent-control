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
| `sh scripts/apply.sh rebase <n> <patch>` | 套到**當前主線**副本、跑 `apply.regen_cmd`、出乾淨 diff | 3(`.rej`≠0 一律失敗) |
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
