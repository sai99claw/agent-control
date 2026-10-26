# 開 session 的固定動作

## 所有角色
1. `git log --oneline -1 main` 與 `git status --short`:知道自己站在哪個版本、工作樹乾不乾淨(worker 在自己的票分支 worktree 裡;驗證者的 `git archive` 副本不是 git 樹,看派工文給的 base sha)。
2. 主線的 `session.start` 由開場 hook(`scripts/new-session.sh`)發,結束前發 `session.end`(心跳拿這一對找死在半路的 session)。短命角色不發。
3. 主線讀 `memory/model/<你的模型>.md` 與 `memory/role/main.md`(hook 那一頁已帶);**短命角色開場只讀「角色卡 + 票」**,正文在 `memory/role/README.md`,規則包已帶模型卡。

## 第 1 步之外,誰讀什麼(對齊 `CLAUDE.md` 與 `memory/role/README.md`;正文在 README)
| | 主線 | 短命角色(開題者 / 實作者 / 驗證者 / 覆核者 / 整理者 / 設計) |
|---|---|---|
| `docs/HANDOFF.md` 現況(舊的在 `docs/handoff/`) | ✓ | ✗ —— 那是主線的交接 |
| `event.py tail 20`、`ticket.py list --open` | ✓ | ✗ |
| 自己那張票 + 票的 `decision_refs` | — | ✓ |
| `docs/DISPATCH-TEMPLATE.md` | 派工前查 §0、§0.5 | **按需查 §**:規則包標題列指名的節、角色卡「遇到就查」指的節;不整份讀 |
| 其他文件 | **grep 定位,讀那幾行** | **grep 定位,讀那幾行** |

理由:每一份「所有角色都要讀」的檔案,成本是**乘以 agent 數**的。全域交接留給主線。
`new-session.sh <短命角色> <模型>` 照這張表印:不印事件流、開票清單,讀單不列 `docs/HANDOFF.md`(#74)。

## 主線
- **這一節現在由 hook 自動跑**(#39):`.claude/settings.json` 的 SessionStart hook 叫 `scripts/session-hook.sh` → `new-session.sh main <routing.main>`,那一頁在第一個 prompt 之前就在上下文裡;startup / clear 發 `session.start`,resume / compact 只重印;副本裡(`worktree_dir` 底下、`AC_ROLE` 非 main、`AC_SESSION_HOOK=0`)不觸發。
- 那一頁的**第一段**是「不可違反的」那一節 + 主線角色卡全文 + 專案自己整理過的 `memory/role/main.md`
  (專案才有、存在才印,#74)+ `memory/role/main.inbox.md` 最後 5 行(#62):
  compact 後規則已經在上下文最前面,不靠自覺去讀。正本讀 `CLAUDE.md` 那一節;專案讀 sync 產的
  `<rules.roles_dir>/contract.md`,缺了那一頁會說。票(Draft 只計數)與收件匣各列最多 20 行,
  沒列的說數字與全文指令;整頁要在 hook 輸出上限 10,000 字元內(`session-hook.sh` 超過就截中間)。
- 看**終態收件匣** `python3 scripts/inbox.py list`:只有兩種頁(D-032)—— **decision**(要你裁:
  反駁升級 —— 只有開題者判需要裁示、同票第二次反駁、開題者沒判出來、session 不能接回四種,其餘反駁由
  auto-fix 起開題者判、接回原 session 續做,不發頁(`docs/WORKFLOW.md`「實作者的反駁怎麼被收下」);三輪紅、覆核退回、環境可疑、worker 沒交件 / patch 套不上、land 拒收或紅、關不掉的票)
  與 **done**(整票完成簡報:subject、落地 sha、做了什麼、覆核結論、cost 表)。一頁答四句
  (哪張票、什麼狀態、要你做什麼、去哪看);閘門綠、覆核通過這類腳本接著會做的事只寫事件。
  收下用 `inbox.py ack <票號>`。
  **開場讀一次,之後只在被通知時讀 —— 不准輪詢 status**(每看一次背景工作 = 整份上下文重送一輪)。
- 看裁示收件匣 `python3 scripts/ticket.py inbox`:使用者填過的裁示要落成 `docs/DECISIONS.md` 一列。
- 跑 `actl heartbeat`:上一個 session / land / worker 有沒有死在半路。它報出 `dead awaiting-takeover` 的列就跑
  `actl heartbeat --reconcile`(存 wip diff、清 worktree、補結尾事件、票 Running → Ready、收 land 的鎖與殘骸);
  `unknown` 的列(#117 之前的舊列)照它印的那一句自己查。
- 用一句話跟使用者說現況。
- **派工(D-067;headless 是唯一的派工路,雲端與 Mac 同一條)**:`sh scripts/auto-fix.sh <n>`(票 Ready)自己起
  worker 與驗證者的程序、收件(apply)、跑閘門、綠了叫 `review.sh`,每一個結局寫進 `reports/inbox/`;
  主線被通知時讀收件匣,**不輪詢**。只看派工文:`sh scripts/auto-fix.sh <n> --dry-run --round 1`。
  每一輪都是**新的** agent,交接只靠上一輪 EVIDENCE 的「已排除的假設」與狀態檔的 `repair_context`;
  `--resume` 只用在交件後短時間內的退回,其餘不續做(D-067)。主 repo 比對包在 auto-fix 起的每一個程序前後,
  拒收 = auto-fix rc 8(#153,D-064)。
  **一輪跑著的期間,主線不改、也不落地 `hard_block.paths` 上的檔**(`docs/DECISIONS.md`、`docs/VISION.md`):
  改了那一輪就被拒收(auto-fix rc 8,D-065 ②);裁示等收件之後再寫。

## 開題者
- 只讀:使用者原話、repo 的設計文件與裁示、自己派的子工作者交回的數字。
- 產出:一張完整票面(驗收 = 測試計畫)+ 給主線的 300 字摘要與建議順序。開完就結束。

## Worker / 驗證者
- worker:cwd 是派工文指定的票分支 worktree(`git rev-parse --abbrev-ref HEAD` 對一眼分支名),
  在裡面自己 commit;EVIDENCE 寫到派工文給的輪次目錄。驗證者:讀派工文裡指定的副本路徑,先 `ls` 確認 `work/`、`base/` 都在。
- 派工文的前言是 `rules.py pack` 產的規則包:角色卡、模型卡、每一節都至少帶一段正文,標題列就是
  全文路徑。產不出來(缺節、空正文)時 pack 非零、送 decision 頁,派工方**不起 agent**(#74)。
- 驗證者什麼時候起:正文只在 `tickets/SCHEMA.md` 的 `needs_verifier` 與 `interface_fixed` 兩列;交件路徑在派工文
  (`<副本根>/patch-verify.diff` 與 `EVIDENCE-verifier.md`)。
- 共用規矩按需查:角色卡「遇到就查」那一行說遇到什麼查 `docs/DISPATCH-TEMPLATE.md` 哪一節,grep 那一節讀,不整份讀。
- **短命角色不發事件**(#29 A9)。`ticket.attempt.*` / `agent.*` 由派工的 `auto-fix.sh` 發
  (#40:Ready 票打 `sh scripts/auto-fix.sh <n>` 就起第 1 輪、發 `--attempt 1`、票轉 Running)。
  auto-fix 派的 `AC_ROOT` 指輪次目錄 / 副本根的 `control/` **拋棄式控制根**,你跑腳本留下的票與收件匣
  落在那裡,不是控制台在讀的(`docs/WORKFLOW.md`「一票一分支一 worktree」)。

## 結束前(所有角色)
- 交接:主線寫 `docs/HANDOFF.md`;worker 的交接寫在回報裡。
- 發 `session.end`。
- **關票前**:`scripts/ticket.py verify <id>`(它會 `git show main:<檔> | grep` 那張票獨有的字串)。

## 角色卡(2026-09-13 起)
短命角色開場只讀「角色卡 + 票」(正文 `memory/role/README.md`;模型記憶由規則包帶)。派工 prompt 不重貼規則,只指路 + 四件票獨有的事。
主線是溝通者(不查 code、不改票面、不驗證、不逐則轉述),順序也由它決定(**沒有調度員這個角色**,D-010);
開題者可派子工作者搜集;驗證者只寫案例、證明案例是對的、登記標籤,不判 PASS/FAIL。細節見 `memory/role/README.md`。
