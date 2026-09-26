# 設計:主線每張票的接觸點收到三下(2026-09-26,設計 session,base A `f3482b3`)

固定段照 `docs/DESIGN.md` §設計文件的固定段。每句標 **實測** / **讀 code 推的**。
量測由 Opus 子工作者做(事件帳 `board/events.jsonl` 596 行、`tickets/*.json`、`reports/inbox/`),本檔只讀它的結論。

## ① 起因

產品負責人 2026-09-26 原話:「我應該早就要讓主線只跟我互動,大多票相關的事情都交出去了啊?」

### 症狀

- **主線每張票親手打 5–13 下指令,中位數 7**(實測,#23–#33 十一張 Done 票,事件帳的 `ticket.state` / `land.start` / `ticket.closed` 的 pid 數;`apply.sh` 不發事件、手跑 `gate.sh` 的事件沒有 ticket 欄、派 worker 沒有紀錄,所以 7 是**下限**,真實數字更高)。
- **收件匣 40 則、24 則沒 ack,其中八張(#11/#23/#25–#28/#30/#31)早就 Done**(實測 4a)。ack 這一下沒有人記得打,而沒打也沒有任何後果 —— 一個沒有人執行的步驟與沒有規矩長得一樣。
- **33 張票的 `review.by`:31 張 `main`、1 張 `reviewer@opus`、1 張沒有**(實測 4b)。D-022(9/23)裁「覆核由短命 opus 做」,三天後只有 #29 走過;因為沒有派工範本、沒有觸發點,主線每次都得自己組派工文,於是回到自己讀 patch。
- **auto-fix 三天只跑過 2 次 attempt**(#7、#32,都是第 2 輪;實測)。「紅了自動派」這條路存在,但**綠的那條路全程手動**:11 張票裡 10 張一次綠,所以自動化幾乎沒有機會啟動。

### 主線今天實際要親手碰的步驟(逐一對照真實入口;讀 code 推的,除非另標)

| # | 步驟 | 真實入口 | 對照結果 |
|---|---|---|---|
| 1 | 派開題者 | Agent tool;票檔 commit `land.sh docs` | 確認。開題者 `ticket.py create` 寫檔,**commit 是主線打**(`templates/dispatch-opener.md` 沒有那一行) |
| 2 | 第 1 輪派實作者 | `auto-fix.sh <n> --dry-run --round 1` 只印派工文(auto-fix.sh:288–346);`ticket.attempt.start` 由主線發(SESSION-START §Worker) | 確認。**round≥2 的整條自動路已經在 `round_once()`(auto-fix.sh:518–786):起 headless worker、收 patch、`apply.sh`、`gate.sh`、轉 InReview、寫 inbox。第 1 輪缺的只是「沒有狀態檔就不跑」這個門檻(auto-fix.sh:350–355 exit 2)** |
| 3 | 收 patch 後 `apply.sh` | auto-fix.sh:323 印給主線的那一行 | 確認(手打;`apply.sh` 不發事件,量不到次數) |
| 4 | `gate.sh --branch --ticket <n>` | auto-fix.sh:353 | 確認。紅了 `gate.sh` 自己叫 auto-fix(gate.sh:437–462,預設開) |
| 5 | 綠 → InReview → 派 reviewer | `auto-fix.sh:766` 只在 auto-fix 路徑上轉 InReview;手跑 gate 綠時 **沒有人轉 InReview**;`memory/role/reviewer.md` 與 `rules.py pack reviewer` 已有(#29),**`templates/dispatch-reviewer.md` 沒有、觸發點沒有** | 確認 |
| 6 | `land.sh t<n>` | land.sh | 確認,**應留在主線**(D-010 順序由主線決定) |
| 7 | `ticket.py close <n> --landed <sha>` | land.sh:608–615 刻意不關,印一行 | 確認 |
| 8 | `inbox.py ack` | inbox.py:224 | 確認;實測 24 則沒 ack |
| (9) | 派驗證者(D-020,票 Ready 時) | `templates/dispatch-verifier.md`,Agent tool | 使用者清單沒列,但也是每票一下;auto-fix 已有 headless 驗證者路(auto-fix.sh:393、`VERIFIER_CMD`,只在 test_defect 時走) |

## ② 結論表

目標形狀:**派出去 → 收件匣看一行終態 → 決定落地順序(與裁示)**。每票主線接觸點從 ≥7 下收到 **3 下**:① 派開題者 ② `sh scripts/auto-fix.sh <n>`(一行,票 Ready 到 InReview+覆核) ③ `land.sh t<n>…`(順序是判斷)。裁示是第四種,只在 Blocked 時出現。

判準(使用者原話):**不拿掉守衛,把「人要記得打指令」拿掉**。每條結論列「會失去哪一道守衛」;答案全部是「不失去」,因為守衛都在腳本裡,被拿掉的只是打指令的人。

| 結論 | 理由 | 未來每票省 / 多花(推的) | 失去哪道守衛 |
|---|---|---|---|
| **C1 第 1 輪由 `auto-fix.sh <n>` 起**:沒有狀態檔 + 票 state=Ready ⇒ 走 `round_once 1`,派工文用既有 `first_round_dispatch`,副本 base 用票的 `base_sha`(`round_once` 已有「分支不存在退回 base」那一格,auto-fix.sh:527–528),`ticket.attempt.start` 由它發、票轉 Running。**不加 `run.sh`**(D-018:同一件事一個入口;`--dry-run --round 1` 留著給人看派工文) | 整條 apply→gate→InReview→inbox 的程式已經有(auto-fix.sh:625–786),第 1 輪只是被 exit 2 擋在門外。round≥2 都是 headless `claude -p`,第 1 輪沒有理由不是 | 省:主線 3 下(派 worker、apply、gate)≈ 每票 6–10K 主線 token(派工文 + patch 路徑 + gate 輸出都不再進主線上下文)。多花:0(worker 本來就是 opus) | 無。`apply.sh` 檔頭 / `allowed_write_paths` / gate / OBJECTION / 三輪上限全在 `round_once` 裡照走;SESSION-START「第 1 輪由主線發 attempt.start」那一句改成「auto-fix 發」 |
| **C2 reviewer 在 gate 綠、票轉 InReview 那一手自動派**:新 `scripts/review.sh <n>`(headless,`reviewer.command` 設定,預設 `claude -p --model opus --allowedTools Read Glob Grep Bash`),派工文 = `rules.py pack reviewer` + 新 `templates/dispatch-reviewer.md`;產出檔尾 `## result` JSON(與 worker 同一個抽取 `ticket.py result`,#29 A4),`verdict=pass` ⇒ `ticket.py set <n> review {verdict,by:"reviewer@<model>",sha:<分支頭>,note}`;`fail` ⇒ 逐條寫進 `objections[]`(blocking)、票轉 Blocked、inbox 一頁「覆核退回,裁示」。觸發點:`auto-fix.sh:766` 轉 InReview 之後(`--no-review` 關);手跑 `gate.sh --branch --ticket` 綠時也轉 InReview 並叫它(今天手跑綠是沒有人轉 InReview 的,那是一個洞) | D-022 裁了三天只走過一次(4b),因為缺範本與觸發。reviewer 讀的是分支上的 code,不是主線該讀的東西(D-016) | 省:主線 2 下(組派工文、`set review`)≈ 每票 4–8K,加上主線不再讀 patch(#29 那次 reviewer 118K 若進主線就是 118K)。多花:reviewer 一次 opus(D-022 已裁,不是新成本) | 無。`review` 仍綁 state_version + 分支 sha(`ticket.py set` 自動蓋,land 第 6 步照查);reviewer 唯讀(工具白名單沒有 Edit/Write);verdict 由腳本寫,人不再手抄 sha —— #29 那次落地前抓到的 verify_strings 機械錯就是 reviewer 抓的,這條路只會更常走 |
| **C3 land 綠後 `close` 由 land.sh 試著關**:push 成功後對批次裡每張票跑 `ticket.py close <n> --landed <merge sha>`;**關不掉就照今天印「已合併、尚未關票」+ inbox 那一頁,理由逐字帶出**(弱檢查、缺 verify_strings、baseline 缺 check、review 過期、objection 未處置)。不加任何 `--force`。 | `close` 的四條件與弱檢查不算過(`done_blockers`、`cmd_close`:1214–1230)一條都不動;差別只是打指令的人從主線變成 land.sh。T 端 `land-ticket.sh land` 已經是落地即關(land-ticket.sh:1804–1814),但 T 那一份**沒有** done_blockers —— A 這條反而比 T 嚴 | 省:主線 1 下 ≈ 每票 1–2K;省掉的還有「忘了關」這種狀態漂移(實測八張 Done 票的 inbox 還躺著「尚未關票」)。多花:0 | 無。守衛(verify 強檢查、baseline stage=check、review 綁定、objections)全在 `close` 裡;land 只是呼叫它 |
| **C4 inbox 只在下一步是人的時候留頁;腳本做掉的頁由腳本 ack**:規則寫進 `inbox.py` 檔頭 —— `post` 的 `what` 若是某支腳本接著就會做的事,那支腳本開跑時 `inbox.py ack <name> --by <script>`。具體:review.sh 開跑 ack「綠了等覆核」;land.sh 收票時 ack「覆核 pass 等落地順序」;close 成功時 ack「已合併尚未關票」並 post 一頁 Done(`what: 無`)且立刻 ack。主線只剩 Blocked / 裁示 / 落地順序三類頁 | 24 則未 ack 且多數已無事可做(4a):ack 今天是「記得就打」,而收件匣本來的定義是「要主線做什麼」—— 沒有事的頁不該留在清單上 | 省:主線每票 1–3 下 ack ≈ 1–3K。多花:inbox.py 多一個 `--by` 欄(≈20 行) | 無。`acked.jsonl` 仍是只加不改的帳,`--by` 記下是誰收的;票的狀態仍由 `ticket.py` 說了算(inbox.py 檔頭那條不動) |
| **C5 票檔 commit 由開題者自己走 docs 通道**:`templates/dispatch-opener.md` 末段加「票寫完:`sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json`」;`memory/role/opener.md` 的「不 git 寫入」加一句例外:**只准 `land.sh docs`,只准自己那張票檔**。不寫新腳本 | docs 通道(#29 A10)本來就是為「每天都在走、卻沒有守衛的路」做的:持同一把 `.land.lock`、只收三個前綴、不在 main 上拒收。開題者呼叫它與主線呼叫它守衛一樣 | 省:主線每票 1 下 ≈ 1K。多花:0 | 無。鎖與前綴白名單在 land.sh 裡;開題者仍不能裸 commit(裸 commit 的禁令照舊) |
| **C6 驗證者派工也進 `auto-fix.sh` 第 1 輪之前**(票有 `verify.files` 或 acceptance 標了要案例、且 `verify.baseline` 空 ⇒ 先起 headless 驗證者,派工文 = `templates/dispatch-verifier.md` 版本;它交件後才起 worker) | `VERIFIER_CMD` 路已有(auto-fix.sh:393–420,test_defect 用);驗證者 D-020 起不等 patch,Ready 就能做,順序天然在 worker 前。**裁「開票之後再做」**:先落 C1–C5 看一週,C6 依賴 C1 的 round_once 形狀定下來 | 省:主線每票 1 下 ≈ 2–4K。多花:0 | 無。驗證者仍只證紅(`verify-case.py red`),不判 PASS |
| **C7 留在主線的三下不動**:派開題者(需求→一句可派的話,只有主線聽得到使用者)、`land.sh` 順序(D-010)、裁示(Blocked / OBJECTION / reviewer fail / 三輪耗盡);發版只有主線(D-010) | 這三件的產出是判斷,不是執行 | — | — |

## ③ 否決案

**甲、一支 `scripts/run.sh <n>` 把 auto-fix → review → land → close 一路跑到 Done。**
不選:land 的順序是主線的判斷(D-010),一路跑到 Done 等於把「哪張先落」交給腳本;而且多一個入口就是第二個形狀(D-018)。auto-fix.sh 已經是「票 Ready 到 InReview」的那一支,把第 1 輪放進去就夠。

**乙、reviewer 由主線用 Agent tool 派,只補範本不做 `review.sh`。**
不選:D-022 三天只走過一次,證明「有範本、人記得派」不夠;而且主線用 Agent tool 派,verdict 是回到主線上下文再由主線 `set review` —— reviewer 讀了 118K 的東西,主線還是要讀它的回報。headless + 腳本寫 `review` 才是「主線只看 inbox 一行」。

**丙、land 綠後 `--force` 關票,或把弱檢查放行。**
不選:弱檢查不准自動關票是 2026-09-21 外部審查 + 2026-09-10 事故(改動從沒進主線卻關成 Done)換來的;C3 只是換打指令的人,關不掉照樣停。

**丁、inbox 全部自動 ack(或拿掉 ack)。**
不選:Blocked / 裁示 / 等落地順序這三類頁是主線唯一的待辦清單;自動 ack 會讓它們消失。C4 的規則是「腳本做掉的事腳本收」,不是「全收」。

**戊、把 close 併進 land.sh 的 review 檢查前(落地即 Done)。**
不選:T 端就是這樣(status=completed 不看 done_blockers),它比 A 弱;而且 Done 的契約只能有一份(D-014 第 1 點)。

## ④ 遷移

- 既有 24 則未 ack 的頁:C4 落地那張票的驗收裡跑一次 `inbox.py ack --all --by migration`(標明是遷移,不是人收的);之後清單只長「需要主線的頁」。
- 既有 `review.by=main` 的 31 張票不動;新票 `review.by` 一律是 `reviewer@<model>`。`ticket.py set review` 對 `by` 不做白名單(主線緊急時仍可手記,land 不分)。
- 過渡期:C1 落地前,`auto-fix.sh <n>` 對 Ready 票仍 exit 2 印下一步;落地後同一句話變成「起第 1 輪」。SESSION-START §Worker「第 1 輪由主線發 attempt.start」與 `memory/role/main.md`「覆核 = 讀 patch 記 review」兩句與 C1/C2 同一張票改掉,不留兩個說法。
- `docs/WORKFLOW.md` §140 能力表加三列(第 1 輪自動、reviewer 自動、land 試關);`docs/FLOW.html` 的 InReview 節點改成「reviewer(腳本派)」—— 交給 C2 那張票,不另開稽核票。
- T 端(`tabby_pool`):`scripts/control/` 由 `sync-to-project.sh` 帶過去(C1/C2/C4 的腳本);T 自己要改的行見 ⑥。

## ⑤ 驗收表

| 結論 | 會紅的斷言 / 可 grep 的修改 |
|---|---|
| C1 | `tests/test_auto_fix.py`:對 Ready 且無狀態檔的票跑 `auto-fix.sh <n>`(worker.command 換成 fixture 腳本)⇒ 出現 `reports/t<n>/<run>/dispatch-round1.md`、事件帳有 `ticket.attempt.start attempt=1` 且發出者是 auto-fix、票 state 經 Running 到 InReview;`grep -c "這一支\*\*不起第 1 輪的 worker" scripts/auto-fix.sh` = 0;`grep -c "第 1 輪由主線發" docs/SESSION-START.md` = 0 |
| C2 | `templates/dispatch-reviewer.md` 存在;`tests/test_review.py`:fixture reviewer 印 `verdict:pass` ⇒ 票 `review.by` 以 `reviewer@` 開頭、`review.sha` = 分支頭、`state_version` 綁上;印 `fail` ⇒ `objections[]` 有 blocking 一筆、state=Blocked、inbox 一頁;`grep -n "review.sh" scripts/auto-fix.sh scripts/gate.sh` 各 ≥1;`grep -c "覆核 = 讀 patch" memory/role/main.md` = 0 |
| C3 | `tests/test_land.py`:單票 land 綠且票有 `verify_strings` + review + baseline check ⇒ state=Done、事件 `ticket.closed`;缺 `verify_strings` ⇒ 仍印「已合併、尚未關票」rc 不變、inbox 頁帶「弱檢查」原文;`grep -c "\-\-force" scripts/land.sh` = 0 |
| C4 | `tests/test_inbox.py`:`ack <name> --by review.sh` 後 `acked.jsonl` 那一筆有 `by`;C2 fixture 跑完 ⇒ 「綠了等覆核」那頁已 ack;C3 fixture 跑完 ⇒ 該票 `inbox.py list` 為 0 則 |
| C5 | `grep -c "land.sh docs" templates/dispatch-opener.md` ≥1;`grep -c "land.sh docs" memory/role/opener.md` ≥1;`sync-to-project.sh` 後 T 的 `docs/roles/opener.md` 同句 |
| C6 | 未開(見⑥);驗收待 C1 形狀定下來後寫 |

## ⑥ 後續工作的模型表(建議開票順序;每張一行目的)

| 序 | 做什麼 | 票號 | 模型 | 一行理由 |
|---|---|---|---|---|
| 1 | **C1** `auto-fix.sh` 第 1 輪:Ready+無狀態檔 ⇒ `round_once 1`,發 attempt.start、轉 Running;改 SESSION-START/WORKFLOW 兩句;測試 | 未開 | opus | 動的是 815 行 shell 裡的門檻與 round_once 的 base 來源,要讀整支;機械但有副本路徑陷阱(G15) |
| 2 | **C5** opener 範本 + 角色卡加 `land.sh docs` 那一句;sync 到 T | 未開 | sonnet | 純文字兩檔,驗收是 grep |
| 3 | **C2** `review.sh` + `templates/dispatch-reviewer.md` + `reviewer.command` 設定 + auto-fix/gate 綠時觸發 + main.md/ROLES/FLOW 改句;測試 | 未開 | opus | 新腳本 + 結果解析共用 `ticket.py result`;fail 路要寫 objections 與 Blocked,錯了會讓 land 收到假 review |
| 4 | **C3+C4** land.sh 試關 + inbox `--by` ack 規則 + 遷移一次 `ack --all --by migration`;測試 | 未開 | opus | 兩支都是每天在走的落地路,改錯直接影響明天每張票;合一張因為「close 成功才 ack」是同一手 |
| 5 | **C6** 驗證者進第 1 輪之前 | 未開 | opus | 依賴 1 的 round_once 形狀;一週後看 C1 跑幾張再開 |
| 6 | T 端接線:`land-ticket.sh` 的 `AUTOFIX=0`(289 行)改預設 1 對齊 A;`gate` 綠轉 `AwaitingReview`(1430)後叫 `scripts/control/review.sh`;`docs/roles/` 加 reviewer 卡(sync 帶);T 的 `land` 已自動關票(1804–1814)**不動** | 未開(T 票) | sonnet | 三處各一行接點,規則都在 control/ 裡;T-only 接觸點是 `build.sh`/`deploy-prod.sh` 發版,D-010 留主線 |

**不重做**:9/23 票單(gate 誤判、OBJECTION 回 0、G13/G15/G16、規則包)當作會落地的前提;1 號票依賴 G15(副本相對路徑巢狀)先落。

## 裁示題(每題附建議)

1. **單票 land 綠後由 land.sh 自動 `close`,可不可以?**(C3)建議:**准**。四條件與弱檢查一條不放,只是換人打指令;關不掉照今天印一行。T 端已經是落地即關而且更弱。
2. **reviewer 的 `pass` 直接寫進 `review` 格、主線不再過目,可不可以?**(C2)建議:**准**。D-022 已裁主線不覆核;land 仍查 review 綁定;`fail` 一律回主線裁。附帶:reviewer 也讀不到主線對票的口頭補充 —— 那種補充本來就該在票面上(D-023 開題品質)。
3. **開題者可以呼叫 `land.sh docs` 提交自己那張票檔嗎?**(C5)建議:**准**,例外只寫這一句,裸 commit 禁令不動。
4. **C6 驗證者自動派要不要現在一起做?** 建議:**先不要**,等 C1 跑過幾張再開(⑥ 第 5 列)。

## 主線接觸點:前後對照(讀 code 推的;「前」的下限由實測 7 下佐證)

| 前(≥7 下) | 後(3 下 + 裁示) |
|---|---|
| 派開題者 → **commit 票檔** → **派 worker + 發 attempt.start** → **apply.sh** → **gate.sh** → **派 reviewer(或自己讀 patch)+ set review** → land.sh → **close** → **ack** | 派開題者 → `auto-fix.sh <n>` → (inbox 一行:覆核 pass 等落地 / Blocked 裁示)→ `land.sh t<n>…` |

## result
```json
{"design":"DESIGN-MAIN-TOUCHPOINTS","base_sha":"f3482b3","measured":{"main_cmds_per_ticket_median":7,"main_cmds_per_ticket_range":[5,13],"inbox_unacked":24,"inbox_total":40,"review_by_main":31,"review_by_reviewer":1,"autofix_attempts_total":2},"conclusions":["C1","C2","C3","C4","C5","C6-deferred","C7"],"tickets_proposed":6,"decisions_asked":4,"rejected":["甲 run.sh 到 Done","乙 reviewer 走 Agent tool","丙 force close","丁 全自動 ack","戊 落地即 Done"],"memory_notes":[]}
```
