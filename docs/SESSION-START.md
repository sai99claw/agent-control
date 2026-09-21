# 開 session 的固定動作

## 所有角色
1. `git log --oneline -1 main` 與 `git status --short`:知道自己站在哪個版本、工作樹乾不乾淨。
2. **發一筆事件**:`scripts/event.py emit session.start --role <role> --model <model>`。控制台從此看得到你。
3. 讀 `memory/model/<你的模型>.md` 與 `memory/role/<你的角色>.md`。

## 第 1 步之外,誰讀什麼(2026-09-21 對齊 `CLAUDE.md` 與 `memory/role/README.md`)
| | 主線 | 短命角色(開題者 / 實作者 / 驗證者) |
|---|---|---|
| `docs/HANDOFF.md` 最後三節 | ✓ | ✗ —— 那是主線的交接 |
| `event.py tail 20`、`ticket.py list --open` | ✓ | ✗ |
| 自己那張票 + 票的 `decision_refs` | — | ✓ |
| `docs/DISPATCH-TEMPLATE.md` | ✓ | ✓ |
| 其他文件 | **grep 定位,讀那幾行** | **grep 定位,讀那幾行** |

理由:每一份「所有角色都要讀」的檔案,成本是**乘以 agent 數**的。全域交接留給主線。

## 主線
- 看**終態收件匣** `python3 scripts/inbox.py list`:閘門、auto-fix、落地、轉 Blocked 跑完的事
  在這裡排隊,一頁答四句(哪張票、什麼狀態、要你做什麼、去哪看)。收下用 `inbox.py ack <票號>`。
  **開場讀一次,之後只在被通知時讀 —— 不准輪詢 status**(每看一次背景工作 = 整份上下文重送一輪)。
- 看裁示收件匣 `python3 scripts/ticket.py inbox`:使用者填過的裁示要落成 `docs/DECISIONS.md` 一列。
- 檢查 `scripts/heartbeat.sh`:上一個 session / land 有沒有死在半路(有的話清 worktree、把票狀態對回事實)。
- 用一句話跟使用者說現況。

## 開題者
- 只讀:使用者原話、repo 的設計文件與裁示、自己派的子工作者交回的數字。
- 產出:一張完整票面(驗收 = 測試計畫)+ 給主線的 300 字摘要與建議順序。開完就結束。

## Worker / 驗證者
- 讀派工文裡指定的副本路徑;先 `ls` 確認 `work/`、`base/` 都在。
- 讀 `docs/DISPATCH-TEMPLATE.md` §禁區與 §假綠家族。
- 開工發 `ticket.attempt.start`,交付發 `ticket.attempt.done`(附 patch 路徑)。

## 結束前(所有角色)
- 交接:主線寫 `docs/HANDOFF.md`;worker 的交接寫在回報裡。
- 發 `session.end`。
- **關票前**:`scripts/ticket.py verify <id>`(它會 `git show main:<檔> | grep` 那張票獨有的字串)。

## 角色卡(2026-09-13 起)
開場除了模型記憶,再讀 `memory/role/<role>.md`。派工 prompt 不重貼規則,只指路 + 四件票獨有的事。
主線是溝通者(不查 code、不改票面、不驗證、不逐則轉述),順序也由它決定(**沒有調度員這個角色**,D-010);
開題者可派子工作者搜集;驗證者只寫案例、證明案例是對的、登記標籤,不判 PASS/FAIL。細節見 `memory/role/README.md`。
