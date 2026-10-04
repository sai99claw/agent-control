# 票的契約

檔名 `tickets/<id>.json`。**worker 只靠這張票與可查詢的來源就能開工。**

| 群組 | 欄位 | 必填 | 說明 |
|---|---|---|---|
| 識別 | `id`, `subject`, `created` | ✓ | id 遞增整數字串 |
| 產品關聯 | `feature`, `requirement_version` | 建議 | 對到產品功能;需求改版時辨識受影響票 |
| 目標與範圍 | `objective`, `acceptance` (list), `in_scope`, `out_of_scope` | ✓ | acceptance 每條要能寫成一條會紅的斷言,斷言的對象是**使用者看到的結果**(畫面上的字、數字、能做的動作)。DOM 結構、樣式掛在哪個元素、金額帶不帶幣別一律寫「照產品既有慣例」,除非票本身就是改這些(D-H38,#61)。「其餘斷言不變」只准在查過既有 oracle(測試、期望檔、守衛)與新行為不衝突之後寫,並列出查過的檔 |
| 高階規劃 | `decision_refs` (list), `outline`, `interface_contracts`, `test_plan` | 建議 | 留關鍵決定,不抄整段規劃聊天 |
| 依賴與衝突 | `depends_on` (list of {id, condition}), **`allowed_write_paths`** (list of globs) | ✓ | `allowed_write_paths` 是**預期會動的檔,不是限制**(D-H38 ①);開題時對每個要改的函式、字串、DOM id、schema 鍵先 grep 引用端(測試、verify 案例、expected 檔、lib 腳本),會跟著動的一併列進來(#61):排順序的人拿它判平行、覆核者拿它對照;越出不擋,`apply.sh` 列進 result 的 `extra_paths`、覆核派工文多一段「白名單外改動」。**硬擋只有兩格**:票的 `out_of_scope`(glob)與 `board/config.json` 的 `protected_paths`(專案自訂的 glob 清單;沒有這一格就不擋),命中 → apply rc=5、閘門 preflight rc=4、land 拒絕,都點名檔與格;`shared_resources` 建議 |
| 執行 | `role`, `model`, `tool`, **`attempt`** (int) | ✓ | 每次派工 attempt+1;**遲到的回報對不上 attempt 就拒絕 —— 回寫時帶 `--expect-attempt N` / `--expect-state-version N`,對不上 rc=4**;`attempt_history` 建議。**model 欄 `codex:<id>` 必配 tool=codex**(反過來 tool=codex 也必配 `codex:` 前綴;起什麼命令由 `ticket.py agent-command` 決定):兩格不一致 auto-fix 拒派(rc=2、票不動,留在 Ready)。codex worker 撞到額度用完(log 有 429 / rate limit / usage limit)→ auto-fix 改派 opus(`model=opus`、`tool=claude-code`)、票回 Ready、發一頁 decision,這一次不自動重派(退出碼 7) |
| 執行 | `worker` ({timeout_seconds}) | 選 | 這張票的 worker **與驗證者**在 `auto-fix.sh` 裡的逾時秒數(#64)。取值順序:票的 `worker.timeout_seconds` > `board/config.json` 的 `worker.timeout_seconds` > 3600;不是正數(0、負數、字串)就不採用、stderr 點名這一格、退回下一層。起 worker 時 stderr 印 `auto-fix: 逾時來源=<票/config/預設> <N> 秒` |
| 版本 | **`base_sha`** | ✓ | 閘門結果只對 base_sha 有效;`branch`、`workspace` 派工時由工具填 |
| 控制 | `state`, **`state_version`**, `lease` ({holder, until}), `retry_limit` | ✓ | state 見 `docs/WORKFLOW.md`;state_version 每次變更 +1;`budget` 建議 |
| 交付 | `patch_ref`, `review_ref`, `test_evidence` (list), `handoff`, `knowledge_updates`, **`verify_strings`** (list) | Done 前 | evidence 每筆帶 base_sha、指令、rc、log 路徑;**`verify_strings` 是只有這張票才有的字串,`ticket.py verify` 對 `git show main:<檔>` grep 它們——沒給的話 verify 只能做弱檢查(看 `allowed_write_paths` 有沒有被動過),並會明說**。根目錄無副檔名的檔(README、Makefile)冒號前不像路徑、不會切,要寫 `./README:那串字`。**`patch_ref` 由 apply.sh 收件時寫**(收件與 `apply.sh rebase` 重生兩條路都寫,不動 `state_version`):相對主 repo 根的路徑,repo 外的以 `../` 開頭,不含絕對路徑 |
| 驗證 | **`needs_verifier`** | ✓ | bool;**開題者寫**,理由寫在 `outline`。`true` = 派驗證者寫案例(auto-fix 第 1 輪起;與 worker 平行與否看 `interface_fixed`,#60);`false` = 實作者自證變異紅。動到產品碼預設 `true`。**缺 = 票不完整** —— 主線的 `verify_waiver` 只是補救,不是常態(D-028) |
| 驗證 | `interface_fixed` | 建議 | bool;**開題者寫**(D-H38 ②,#60)。只在 `needs_verifier=true` 時有作用。`true` = 純後端 / 介面(函式名、參數、輸出字串、檔案格式)已在票面釘死 —— auto-fix 第 1 輪驗證者與 worker 平行起(#51 的行為);`false` 或缺 = worker 交出 `patch-round1.diff` 之後才起驗證者,它的派工文多一段「介面以分支上的實作為準,票面寫法與實作衝突時照實作、在 EVIDENCE 記一行」;worker 第 1 輪反駁 / 沒交 / 逾時 ⇒ 驗證者不起,inbox 頁寫明。拿不準就不寫(= false):平行猜錯介面的代價是一整份案例重寫,晚起的代價只是一次驗證者的時鐘 |
| 驗證 | **`verify`** ({files, tags, run, notes, **baseline**}) | 驗證者交件時 | **驗證者寫的**(D-010):案例檔在哪、宣告了哪幾個標籤、**怎麼跑**(一句可以直接貼的指令)、跑之前要知道的事。驗證者不判 PASS/FAIL、不寫 VERDICT —— 對錯由閘門跑 `verify.tags` 判。`ticket.py verify <id>` 會把這一格印出來;沒人寫時它印「還沒有人寫」而不是一片空白 |
| 覆核 | **`review`** ({verdict, by, at, note, **state_version**, **sha**}) | land 前 | **主線**讀 patch 之後記的那一格(覆核 = 讀 patch 記 review,不重跑測試)。**它綁在被覆核的那個版本上**:`state_version` 由 `ticket.py set` 自動蓋(票之後任何一次變更都讓它過期),`sha` 是最終 patch / 分支的頭。land 缺少相符的覆核即拒絕 |
| 反駁 | **`objections`** (list of {category, body, evidence, owner, disposition, blocking, follow_up, triage}) | 有異議時 | 實作者說「票寫錯 / 這條驗收做不到 / 案例本身錯(`test_defect`)」的地方。`category` 是 `ticket-wrong` / `blocking`(或 `blocking: true`)= 阻擋項,`disposition` 空著 **land 與 close 都拒絕**。處置寫 `accepted` / `rejected` / `deferred` / `fixed`。`ticket-wrong` / `blocking` 先由 auto-fix 起開題者判(D-041,#89):判 accepted / rejected 時 **auto-fix** 寫 `disposition` 與子格 `triage`(`{verdict, reason, round, by}`)並接回同一個 session 續做;開題者判需裁示、同票第二次反駁、開題者沒判出來、session 不能接回這四種才進主線收件匣,由主線處置。`test_defect` 照舊派驗證者。流程正文在 `docs/WORKFLOW.md`「實作者的反駁怎麼被收下」 |
| 修復迴圈 | `repair_round`, `owner` | 自動 | `ticket.py round <n> <r> --red` 記的;第 `retry_limit+1` 輪仍紅 → `state` 轉 Blocked、`owner` 設 main |
| 凍結 | `frozen` ({reason, criterion}) | 選 | 例:「產出會不會因視覺方向改變而重做」 |
| 自動開票欄 | `flaky_case`, `flaky_seen`, `flaky_log`, `flaky_from_ticket`;`regression_case`, `regression_source`, `regression_log`, `regression_from_ticket` | 自動 | `status.py` 開修復票時在鎖裡補的(`open_repair_ticket`)。`flaky_*`:疑似 flaky 達門檻開的 verifier 票;`regression_*`:`status.py regression-red` 開的 worker 票(`regression_source` 是 `release` / `gate`,`regression_log` 是紅在哪份 log、只給 `--case` 時為空)。**查重就讀這兩格**:開著的票 `regression_case` 或 `flaky_case` 等於那條案例 → 沿用、不寫票 |
| 成本 | `cost` (list of {role, round, model, tokens_in, tokens_out, cache_write, cache_read, wall_seconds, by, at}) | 自動 | 每次 headless 派工一筆,由 `auto-fix.sh`(worker / verifier)、`review.sh`(reviewer)、`land.sh`(land)經 `ticket.py cost` 寫(D-032)。token 四欄取信封 `usage` 的 `input_tokens` / `output_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens`;`wall_seconds` 是腳本量的秒數。**`null` 的意思是拿不到,不是 0**(land 沒有 LLM,model 與 token 欄都是 null)。**不動 `state_version`、不動 `review`** —— 落地前寫一筆成本不會讓覆核過期。不存費用:同一模型的單價會變 |

## 最小可開工範例
```json
{
  "id": "1",
  "subject": "land.sh 對 0 commit 的分支整批拒絕",
  "created": "2026-09-12T10:00:00+08:00",
  "objective": "任一支分支相對主線 0 commit 時,land 秒退並點名,不跑全套",
  "acceptance": ["0 commit → rc!=0 且輸出含分支名", "多支中一支 0 → 全部不合(另一支也不在 land worktree)", "正常路徑輸出與退出碼不變"],
  "in_scope": ["scripts/land.sh", "tests/test_land.py"],
  "out_of_scope": ["gate.sh"],
  "depends_on": [],
  "allowed_write_paths": ["scripts/land.sh", "tests/test_land.py", "docs/WORKFLOW.md"],
  "role": "worker", "model": "opus", "tool": "claude-code", "attempt": 1,
  "base_sha": "<main sha at dispatch>",
  "state": "Ready", "state_version": 1, "lease": null, "retry_limit": 2,
  "verify": {"files": [], "tags": [], "run": "", "notes": ""},
  "objections": []
}
```

## EVIDENCE 尾端的 `result` 區塊(D-017)

票是規格,這一塊是**這一輪交出來的東西**。worker 與 verifier 的 `EVIDENCE-round<輪>.md`
五段散文照舊給人看,檔尾再加一段 `## result`,底下一塊語言標記是 `result` 的 fenced
JSON 物件;`scripts/auto-fix.sh` 在收 patch 的同一手把它抽成
`reports/t<票號>/<run_id>/result-round<輪>.json`(驗證者那條路是
`result-verifier-round<輪>.json`),與那一輪的 `status.json` 同目錄。**抽取只讀
EVIDENCE,不改它**;抽不出來也不改變退出碼、不擋流程。

| 鍵 | 型別 | 說明 |
|---|---|---|
| `ticket` | 字串 | 票號 |
| `role` | 字串 | `worker` / `verifier` |
| `round` | 整數 | 第幾輪 |
| `rc` | 整數 | 交出來那一刻自己跑的閘門退出碼 —— 判綠只看它 |
| `patch_sha256` | 字串 | 交出去那份 patch 的 sha256 |
| `gate` | 物件 | `{cmd, ran, rc}` |
| `mutations` | 陣列 | `{id, count, case, red_first_line}` |
| `objection` | 物件或 `null` | `{category, body}`;`category` 是 `ticket-wrong` / `test_defect` / `blocking` |
| `excluded` | 陣列 | 已排除的假設 |
| `repro` | 物件 | `{cmd, expect}` |
| `memory` | 陣列 | `{layer, name, line, ticket}`;**鏡像而已,寫入仍由 `memory.py harvest` 做** |
| `baseline` | 物件或 `null` | **只有 verifier 寫**:`verify-case.py red` 印出的 `<out-dir>/baseline-red.json` 原封抄進來,`stage` 必是 `red`;worker 留 `null`;`apply.sh --evidence-verifier` 在鎖裡把它併進票的 `verify.baseline` |

`apply.sh` 收件那一手再補一格 `extra_paths`:這一輪改到、不在 `allowed_write_paths` 裡的檔(沒有就是 `[]`;auto-fix 抽過的那一份由 `AC_RESULT_JSON` 指路)。

抽出來的那一份還多一格 `sections`:`{patch_sha256, gate, mutations, excluded, repro}` 五個 bool,
答的是「EVIDENCE 那**五段散文**在不在」(§8 那五段;抽取只看小標與關鍵詞,**缺段印出來、不擋流程**)。
為什麼要分開記:`result` 那一塊是機器版,五段是人版;兩者都可能單獨缺,而**只驗機器版的那一天,
一份沒有「已排除的假設」的 EVIDENCE 會長得跟完整的一模一樣**,下一輪那個新的人因此把同一段 code 再查一次。

抽出來的那一份多一格 `"present": true` 與一格 `"conflict"`(`objection` 與 EVIDENCE 裡
那一行 `OBJECTION:` 對不上時為 `true`,並**以那一行為準**)。沒交的三種各有各的樣子:
`{"present": false, "reason": "no-evidence"}`、`"no-block"`、`"bad-json"`(後者把原文前
500 字放進 `raw`)—— **揉成同一個空檔的那一刻,「沒交」與「交了但都是空的」長得一樣**。

欄位的完整說明與範例另一份在 `docs/DISPATCH-TEMPLATE.md` §8.5(**只有這兩份**:第三份
一出現,三份就會各自往不同方向漂,而漂開的那一份看起來仍然像規格)。

## 為什麼是這幾個欄位(每一個都對應一次事故)
- `base_sha`:2026-09-10 用過期的 branch ref 做副本,agent 拿到看起來正常但缺了上一張票的樹。
- `allowed_write_paths`:五張票共用 index.html、三張共用同一支測試檔,只能靠人讀 code 排序。
- `attempt` / `state_version`:一張票被關成 Done 但從沒進主線,兩天後才發現。
- `test_evidence.base_sha`:一支分支 3470 條綠,跑在三小時前的基準上,主線早已走遠。
- `verify`:2026-09-20 驗證者交了案例卻沒寫怎麼跑,下一個人重跑整組閘門去找它們,一次等待燒掉幾十萬 token(D-010)。**案例在哪與怎麼跑,是交付的一部分。**
- `verify.baseline`:2026-09-21 外部審查 —— 範本只要求貼兩份 Ran/OK,而**一份貼上來的輸出沒辦法被機器比對**;票上沒有一格說得出「乾淨主線上真的紅過」,於是一條永遠綠的斷言與一條真的在驗的斷言長得一樣。同一格由兩個 `stage` 接力寫(D-020,2026-09-23):`red`(驗證者跑 `scripts/verify-case.py red <n>`,只證乾淨基底上紅、紅在案例檔自己的斷言;red 只印 `<out-dir>/baseline-red.json`、不寫票 → 驗證者抄進 result 的 `baseline` → `apply.sh --evidence-verifier` 在鎖裡併進票,`stage=red`)升成 `check`(閘門,`scripts/verify-case.py check <n>` 寫,乾淨主線該紅、candidate 該綠,`stage=check`)。`ticket.py` 的 close 只認 check——`stage=red` 只代表案例寫好了,還沒有人量過綠。
- `needs_verifier`:2026-09-26 #34–#38 五張票面都漏寫,驗證者要不要派只能由主線事後判、用 `verify_waiver` 補(D-028)。**沒寫的票與寫了 `false` 的票長得一樣**,所以它是必填,理由寫在 `outline`。
- `review.state_version` / `review.sha`:同一次審查 —— 舊的 `review` 只有 verdict/by/at/note,重套一次 patch、改一次票面之後它仍然長得有效,而 land 根本沒有讀它。
- `objections`:同一次審查 —— 實作者的反駁沒有可靠的收件與處置契約,「這張票寫錯了」講完之後東西照樣落地。
- `attempt` / `state_version` 的**比對**:同一次審查 —— schema 宣稱過「對不上就拒絕」,而 `ticket.py set` 讀出來直接覆寫。**宣稱與實作分岔的那一格,看起來與有守衛的那一格一模一樣。**
- `result` 區塊:2026-09-22 —— 機器讀得懂的只有一行 `OBJECTION:` 與一個退出碼,**停下來的理由沒有一格寫得下**;看板因此只能印「worker 沒交結構化輸出」。
