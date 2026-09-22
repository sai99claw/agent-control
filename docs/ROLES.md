# 角色

角色是責任分類,不是一排常駐模型。**作者與審查者要用分離的上下文。**
除了主線與產品負責人,每一個角色都是**短命**的:開一次、交一次、結束。

| 角色 | 誰 | 責任 | 必須交付 | 權限牆 |
|---|---|---|---|---|
| **產品負責人** | 人 | 方向、取捨、必要決策 | 目標、優先序、裁示 | 可暫停、取消、重排;不逐步批准例行工作 |
| **主線** | 跟人互動的 session(Fable) | 接需求、派開題者、裁示、**覆核(讀 patch 記 `review`)**、決定落地順序、發版、維持脈絡 | 裁示、票的 `review` 格、交接、發版紀錄 | 不放寬驗收;不跳過閘門;不自己查 code、不自己改票面;不替人做產品決策 |
| **開題者** | 短命 session(Fable) | 把使用者一句話寫成**完整票面**(含測試計畫) | 票 JSON + 給主線的 300 字摘要與建議順序 | 不改檔、不 git 寫入、不執行整支腳本;不替實作者先做一遍 |
| **Worker / 實作者** | 短命(預設 Opus;機械、規格逐字的票可派 Codex sol) | 在副本裡實作與局部驗證 | `patch.diff`(含自己的單元測試)、`EVIDENCE.md`、變異驗紅 | 只寫自己的副本;禁 git 寫入;範圍擴大要回報不准自己做 |
| **驗證者** | 短命、獨立上下文(Sonnet / Codex sol) | 把票面驗收寫成回歸案例;`verify-case.py check` **證明案例是對的**(乾淨主線紅、candidate 綠),登記片段 `verify/TAGS.d/<n>.md`,把怎麼跑寫進票的 `verify` 欄 | `verify/<feature>/test_ticket_<n>.py` + 票的 `verify`(含 `baseline`)+ `patch-verify.diff`(`verify-case.py extract` 出的) | **不判 PASS/FAIL、不寫 VERDICT**;不讀實作者的 `EVIDENCE.md`;**不跑 tag 回歸那一整組**(只跑自己的案例與 `verify-case.py check`);不輪詢;不改產品碼 |
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

## 模型路由(2026-09-20,D-011;可調的那一份在 `board/config.json` 的 `routing`)
| 工作 | 預設 | 為什麼 |
|---|---|---|
| 主線、設計、查 bug 根因、驗收計畫、審稿 | **Fable** | 判斷題,不是打字題 |
| 開題 | **Fable**(短命) | 票面的品質決定下游要不要重做 |
| 實作 | **Opus**(`worker.command` 實際起的那個);機械、規格逐字的票可選 Codex `gpt-5.6-sol` | `board/config.json` 的 `routing.implement` 只是路由標籤,事件記的是 `worker.command` 真的起的模型 |
| 實作(要起 server / 瀏覽器實跑) | **Opus** | Codex 在 `workspace-write` 綁不了埠、起不了瀏覽器(`-s danger-full-access` **不設**) |
| 驗證(寫回歸案例) | **Sonnet** 或 Codex sol | 照票面寫案例是機械的 |
| 帳務 / 資料遷移類的票 | 加派**第二家模型**(agy gemini 或 Codex)做**機器可驗**的獨立驗證 | 同一家模型的兩個 session 會犯同一種錯 |
| 討論、找靈感 | 另一家模型(agy / Codex) | 要第二個腦袋,不是第二雙手;**它的回答提到產品功能一律 grep 一次再用** |

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
**覆核不自動**:綠了票停在 `InReview`,主線讀 patch 記 `review`(綁票版本與分支 sha)後 land 才收。

**歸責不看「這個檔有沒有被這張票改過」**(2026-09-21 取消這一條):`failures.file` 取的是 traceback 最後一個檔案,
經常是既有測試或共用 helper,而產品改壞行為本來就會紅在沒修改過的測試檔。改用同條件的
**baseline / candidate 對照**(`scripts/verify-case.py check`)。**未完成歸因前,票由原 owner 持有。**
流程與狀態檔格式:`docs/WORKFLOW.md`。

## 並行上限與不准輪詢
- 任何 agent 都不准用 Monitor / sleep 迴圈等背景工作。要跑的測試**前景跑、給 timeout、一輪拿結果**,
  只擷取 `^Ran |^OK|^FAILED|^(FAIL|ERROR):` 那幾行。每看一次背景結果 = 整份上下文重送一次。
- **同時最多兩個會起瀏覽器的 agent**;全套並跑時不再起瀏覽器型 agent。
- **優先序改變時把低優先的 agent 停掉**,不要讓它自己滾完。
