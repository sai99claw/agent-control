# 角色

角色是責任分類,不是一排常駐模型。**作者與審查者要用分離的上下文。**
除了主線與產品負責人,每一個角色都是**短命**的:開一次、交一次、結束。

| 角色 | 誰 | 責任 | 必須交付 | 權限牆 |
|---|---|---|---|---|
| **產品負責人** | 人 | 方向、取捨、必要決策 | 目標、優先序、裁示 | 可暫停、取消、重排;不逐步批准例行工作 |
| **主線** | 跟人互動的 session(啟動時指定的模型;角色表 `main` 只是建議,`new-session.sh` 印出、不熱切) | 接需求、派開題者、裁示、**覆核退回時裁示**(覆核由 `review.sh` 派 reviewer,主線不讀 patch)、決定落地順序、發版、維持脈絡 | 裁示(含覆核退回的反駁處置)、交接、發版紀錄 | 不放寬驗收;不跳過閘門;不自己查 code、不自己改票面;不替人做產品決策 |
| **開題者** | 短命 session(角色表 `opener` = opus;明確複雜的開題 `opener_complex` = astra → fable;`rules.py pack` 不判模型,#121 A8) | 把使用者一句話寫成**完整票面**(含測試計畫) | 票 JSON;**摘要與建議順序寫進票的 `outline` 欄**(`ticket.py create --outline`),不只回在對話裡;交票前 `ticket.py lint` | 不改產品碼與 repo 檔(票檔、triage 檔、暫存目錄可寫);不 git 寫入(docs 通道落地自己那張票檔例外,別的檔 land 會擋);不替實作者先做一遍 |
| **設計 session** | 短命 session(角色表 `design` = astra → fable;「想一下」同一條) | 設計題先設計再開題(D-019):決定形狀、比較方案、每個取捨附「未來每票省/多花」(D-018) | `docs/DESIGN-<題>.md`(固定段見 `docs/DESIGN.md` §設計文件的固定段) + 回主線 ≤ 300 字摘要 | 不開票、不改產品碼、不 git 寫入、不跑閘門 / 落地;量測派子工作者,自己只讀結論 |
| **Worker / 實作者** | 短命(預設 Opus;機械、規格逐字的票可派 Codex sol) | 在副本裡實作與局部驗證 | `patch.diff`(含自己的單元測試)、`EVIDENCE.md`、變異驗紅 | 只寫自己的副本;禁 git 寫入;範圍擴大要回報不准自己做 |
| **驗證者** | 短命、獨立上下文(Opus / Codex sol) | 把票面驗收寫成回歸案例;`verify-case.py red` **證明案例在乾淨基底上紅**(綠由閘門用 `check` 量,D-020),登記片段 `verify/TAGS.d/<n>.md`,把怎麼跑寫進 result 的 `verify` 格(收件併進票) | `verify/<feature>/test_ticket_<n>.py` + 票的 `verify`(含 `baseline`)+ `patch-verify.diff`(`verify-case.py extract` 出的) | **不判 PASS/FAIL、不寫 VERDICT**;不讀實作者的 `EVIDENCE.md`;**不跑 tag 回歸那一整組**(只跑自己的案例與 `verify-case.py red`);不改產品碼 |
| **覆核者** | 短命(Opus;D-022;`scripts/review.sh` 派,D-025 ②) | 閘門綠、票轉 `InReview` 後讀**分支上的 code** 對票面驗收 | `verdict(pass\|fail)` + 逐條驗收 → code 位置 + 疑慮清單;`fail` 寫成 objection | 不重跑測試、不改檔、不 git 寫入、不判落地順序(`review.sh` 依檔尾 `## result` 把 pass 寫進票的 `review` 格;fail 轉 Blocked 回主線) |
| **整理者** | 短命 × 1(角色表 `consolidator` = fable 單一高階模型,D-043 / D-049 ②;沒有雙模型互讀) | 記憶超上限時壓縮(D-006 / D-007 / D-013);範本 `templates/dispatch-consolidator.md` | 壓縮後的記憶檔或提高上限 + `cap_history` 一列、一份討論檔(啟動端記在 `attempt_history`)、`memory.consolidated` 事件 | 沒有討論檔不准 consolidate;不直接寫案例(用票號指路);不改 `docs/DECISIONS.md` |
| **落地器** | `scripts/land.sh`(程式) | 閘門 → 合併 → push;紅了寫紅榜 | 事件、退出碼、`reports/t<n>/<run_id>/status.json` | 0 commit / 基準過期 / 越界 / **覆核缺或過期** / **未處置的阻擋反駁** / 閘門紅,一律拒絕;land 期間持一把互斥鎖。它**沒有判斷** |

> **落地器不做三件事,而角色表以前把它們藏掉了**(2026-09-21 外部審查;第 ①③ 兩條 D-015 起有自己的入口):
> **① 套 patch、建分支、commit** —— 走 `scripts/apply.sh <票號> <patch> [<patch-verify>]`;
> `land.sh` 收的還是**已經有 commit 的分支**,它自己不套 patch。
> **② 關票** —— land 成功後印「已合併、尚未關票」,關票走 `scripts/ticket.py close <n>`。
> **③ 自動起新 worker** —— 走 `scripts/auto-fix.sh <票號>`(`land.sh --auto-fix` 只在一批剛好
> 一張票時掛得上去;多張票的歸責要票↔案例的對照,還沒有)。
| **知識維護** | 主線 | 更新 code map 與記憶 | 帶來源與版本的變更 | 未驗證推測不進共用知識 |

## 排序誰來做:沒有調度員這個角色(2026-09-21,D-010)
**順序由主線決定**,或由腳本依票的 `allowed_write_paths` 算衝突圖提案(`docs/TODO.md` §2)。

2026-09-10 的實驗:一個 Opus **調度員**長命 session 同時排程、派工、審 patch、落地、寫文件,
**一個下午一百萬 token、四十則回報、撞額度後靜停兩天無人發現**。2026-09-21 收掉整個角色
(不只瘦身):它的判斷九成是機械的,而**活著就燒錢** —— 每看一次狀態就是整份上下文重送一輪;
需要判斷的那一成本來就該回主線。排序便宜,判斷貴;把判斷留給主線,把機械留給程式。
`memory/role/dispatcher.md` 已刪,`schedule.proposed` 事件留著給腳本或主線用。

## 模型路由(2026-09-20,D-011;派工的模型只由 `board/role-models.json` 選,見下方 #116 / #122 段)
| 工作 | 預設 | 為什麼 |
|---|---|---|
| 主線、開題 | **Opus**(D-039 / D-049 ②;角色表 `main` / `opener`;明確複雜的開題 astra → fable) | 判斷題;10/03 實測 opus 可用,下限放寬 |
| 設計 / 想一下、查 bug 根因 | **astra → fable**(D-049 ②,角色表 `design`;查 bug 根因沒有表內角色,主線指定) | 會影響之後每一張票的形狀 |
| 實作 | **sol → opus**(角色表 `worker`;簡單工作 `worker_simple` = sonnet) | 事件記的是角色表選中、真的起的那個模型 |
| 實作(要起 server / 瀏覽器實跑) | **Opus**;Codex 待專案同步後冒煙實證 | D-048 現況(#115):codex worker 與 claude 同權限,`ticket.py agent-command` 起 `-s danger-full-access -c 'approval_policy="never"' -C .`,不再用 `workspace-write`;越界由 auto-fix 的事後守衛抓(rc=8,見 `docs/WORKFLOW.md`)。server / 瀏覽器在真 codex 上能不能跑**還沒實證**(待專案同步後冒煙) |
| 驗證(寫回歸案例) | **Opus**(預設)或 Codex sol | 驗證者是判斷工作(使用者 2026-10-03 裁示) |
| 帳務 / 資料遷移類的票 | 加派**第二家模型**(agy gemini 或 Codex)做**機器可驗**的獨立驗證 | 同一家模型的兩個 session 會犯同一種錯 |
| 討論、找靈感 | 另一家模型(agy / Codex) | 要第二個腦袋,不是第二雙手;**它的回答提到產品功能一律 grep 一次再用** |

**角色 → 有序模型候選(#116,D-049 ②)**:唯一的一張表在 `board/role-models.json`,每個角色一串
`{model, tool, effort, effort_escalated, dispatch_cap}`;所有派工呼叫同一個選路器
`python3 scripts/role-models.py resolve --role <角色> [--complexity simple|complex] [--repair-round N]
[--last-red] [--worker-model M] [--ticket-effort E] [--items N] [--exhausted-index I [--evidence LOG --evidence-rc RC]]`,
印一行 JSON(rc 0 選到、3 整串耗盡 → 停、發一次 decision,2 不派)。只有 provider 錯誤信封說額度用完 /
模型不可用、或 CLI 不在(rc=127)才換下一個候選(`--exhausted-index` 不帶 `--evidence` 時,呼叫端要先用
`role-models.py classify` 判);一般失敗、測試紅、逾時、字面 `429` 不換,認證錯停下。
verifier 鏈固定 sol → opus,不看 worker 模型;每次派工上限 `dispatch_cap` 由 `--items` 擋。
舊入口(`board/config.json` 的 `routing.main/open/implement/implement_browser/verify/design/bug`、`memory.consolidator(s)`)
#121/#122 起都不是選路來源;`python3 scripts/role-models.py legacy` 列出還在的那幾格(與表首選不同的標「衝突」,主線開場也印),
`legacy --write` 是一次明確遷移(`routing.main` 留給 `session-hook.sh` 當模型標籤)。`routing.second_opinion / discuss` 沒有 D-049 角色,照舊。
#122 起判反駁的開題者(auto-fix)、獨立開題 / 設計派工(`sh scripts/new-session.sh dispatch <opener|design> --items N --prompt <派工文>
[--complexity complex]`)與整理者(`consolidate-memory.sh`)也只讀這張表;這幾個角色不帶票的 `model`(#131 A10 只套 worker 鏈)。
**每次派工上限**(D-049 補):開題 / 設計派工要帶這次要交的票數 `--items`;超過候選的 `dispatch_cap`(astra 初值 5)rc=2、
印切批建議、不起程序,切批由主線做;fable / opus 的 cap=null 不擋。`effort` 照表(這幾個角色的候選目前都是 null = 不帶)。
Fable 整理用不了就是 consolidator 鏈耗盡:停、送 decision,不改派別的模型。
#121 起 `auto-fix.sh` 的 worker / 驗證者與 `review.sh` 的覆核者只讀這張表(`routing.implement` / `routing.verify`
不再參與;worker 的 `effort.implement` 只在票與被選候選都沒給 effort 時才補上);`rules.py pack` 不判模型。
表中 `main` 只是開 session 時的建議,不熱切已在跑的互動 session。

額度政策:Codex 的五小時桶**一次派三張就會用盡**,別再多;用完**退 Opus 一張並報主線**。
**等、換、或送進收件匣;不准默默改走付費通道。**

## 回歸紅了誰去修(2026-09-21,D-010;flake 與歸責 D-014 改寫)
不叫醒舊 worker(它醒來一次 = 累積的整份上下文),也不設常駐調度員。紅榜寫成
`reports/t<n>/<run_id>/status.json`,紅的案例**單獨重跑一次**:**單跑綠只標 `suspected_flaky`,rc 不動**,
再用原順序整組重跑一次判真紅。真紅就由 **`scripts/auto-fix.sh <票號>`** 起一個**新** worker
(規則包 + 票面快照 + `repair_context` + 紅榜),**三輪上限**;
第 `retry_limit+1` 輪仍紅由 `ticket.py round` 把票轉 **Blocked、owner=main**。
停下來報主線的三種情況:worker 判斷票寫錯 / 需要裁示(EVIDENCE 寫一行 `OBJECTION:`,
工具記成 `objections[]`)、三輪耗盡、**failures 沒有歸因**。每一種都寫一則
`reports/inbox/<票號>-<run_id>.md` 去叫醒主線 —— **主線不輪詢**。
**覆核由 `review.sh` 派**(#42,D-025 ②):綠了票轉 `InReview`,`review.sh` 派 reviewer;pass 由腳本記 `review`(綁票版本與分支 sha)後 land 才收,fail 票轉 Blocked 等主線裁示,沒交件不算 pass。

**歸責不看「這個檔有沒有被這張票改過」**(2026-09-21 取消這一條):`failures.file` 取的是 traceback 最後一個檔案,
經常是既有測試或共用 helper,而產品改壞行為本來就會紅在沒修改過的測試檔。改用同條件的
**baseline / candidate 對照**(`scripts/verify-case.py check`)。**未完成歸因前,票由原 owner 持有。**
流程與狀態檔格式:`docs/WORKFLOW.md`。

## 並行上限與不准輪詢
- 不准輪詢的正文:主線在 `CLAUDE.md`「不可違反的」,短命角色在 `docs/DISPATCH-TEMPLATE.md` §3;這裡不重抄。
- **同時最多兩個會起瀏覽器的 agent**;全套並跑時不再起瀏覽器型 agent。
- **優先序改變時把低優先的 agent 停掉**,不要讓它自己滾完。
