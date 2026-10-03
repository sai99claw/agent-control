# A 重新開票整理表(2026-10-03,開題者 Fable)

使用者裁示(原話):「Agent control 所有相關的東西都停掉,先把所有要改的用 fable 重新開票整理在一起。」
→ 追加:「我希望新的 A 票只有一張,worker 改完,一次測。」

結果:**一張新票 #86**(Ready,depends_on #76 落地),6 張舊票作廢、1 張凍結、#76 建議先單獨落地。
票面全文在 `tickets/86.json`;本表只記「原項 → 處置 → 去向 → 理由」。

## 第 0 步:tickets/76.json 的絕對路徑

| 項 | 結果 |
|---|---|
| 位置 | **不在 cost.note**(主線交接寫 cost),在 `verify.baseline.candidate`、`verify.baseline.baseline.log`、`verify.baseline.candidate_run.log` 三格 —— `verify-case.py check` 寫的 |
| 處置 | `ticket.py set 76 verify '<整份物件>' --expect-state-version 43`,三格改成 `../agent-control-wt/t76…`(state_version 43 → 44) |
| 驗證 | `python3 -m unittest tests.test_no_project_names` → Ran 6 tests, OK;家目錄絕對路徑前綴掃 tickets/*.json 無命中 |
| 副作用 | #76 的 `review.state_version` 41 本來就已過期(停工轉 Blocked 時 sv 走到 43),land 前照舊要 `review.sh` 重跑一次(#76 的 reviewer 上一次 121 秒) |
| 新發現 | 這是 #83 沒涵蓋的第二個寫入點 → 併成 #86 **A2**(verify-case.py 也存相對路徑) |

## 逐項處置

| 原項 | 處置 | 去向 | 理由 |
|---|---|---|---|
| #74 規則包不截空、產包失敗停派 | 已 Done | — | 不動 |
| #75 memory note 名稱正規化 | 已 Done | — | 不動 |
| #85 routing.verify → opus | 已 Done | — | 不動 |
| #76 超標/過期記憶:唯讀檢查、不重複開票、成功前不消耗原文 | **保留,建議先單獨落地**(Blocked,t76 分支在) | #86 depends_on 76 | 已實作、驗證者 15 條案例乾淨基底紅/候選綠、閘門綠、覆核 pass;併入 = 叫 worker 重寫一份已寫好的 patch。要主線裁:若使用者仍要併,把 t76 diff 當 #86 A38 前置、#76 A1–A8 加回 #86 |
| #77 code-map 實作速查索引 | 作廢 → 併入 | #86 **A28–A32**(原文逐條) | code 改動;model 由 codex:gpt-6-astra 改 opus(codex 派工能力本身是 A19–A25,雞生蛋) |
| #78 整理 opener 超標與 A 記憶錯層 | **凍結**(不作廢、不併) | 等 #86 Done 解凍,人工派兩模型 | 兩個模型討論記憶正文(D-007/D-013),不是 worker 的 code;它要 #86 的 code-map(去向)與 lint(判準)。model 改 opus;(h2) opener 卡「verify_strings 是 list[str]」那句一併整理 |
| #79 記憶分層契約 lint | 作廢 → 併入 | #86 **A33–A37** | A35(原 A3)改寫:lint 對全 repo 為 0 需要 #78 先整理正文,改成「只對分支改到的記憶檔為 0、全 repo 結果印出不當驗收」;既有卡正文列 out_of_scope 硬擋 |
| #80 auto-fix 依票 model/tool 派(codex exec) | 作廢 → 併入 | #86 **A19–A25**(原文逐條) | 與隔離(A9–A17)、派工文段(A18)、整理啟動(A39–A41)四組同改 auto-fix.sh 起命令那段,一張票一次改完正好 |
| #81 整理 opener.md | 已 Cancelled | — | 不動(#74 副本外洩誤開的) |
| #82 worker 環境隔離 + 逾時收整棵子行程樹 | 作廢 → 併入 | #86 **A9–A17**(原文逐條;A17 變異改實作者自證) | 原 needs_verifier=true;#86 整張 false(理由見票面 outline),worker-isolation 案例改進 tests/test_auto_fix.py |
| #83 cost note 不存絕對路徑 | 作廢 → 併入 | #86 **A1**;同型新項 **A2**(verify-case.py) | 第 0 步實測到第二個寫入點 |
| #84 整理票自動啟動(自 #76 拆出) | 作廢 → 併入 | #86 **A39–A41** | 原票只有 Draft 三句,這次補成可驗的三條(租約排他、三種失敗態、模型身份由啟動端記) |
| (a) D-039 routing.main/open → opus | 新 → 併入 | #86 **A5** | config 改動要票;與 #85 同檔,#85 已落地無衝突 |
| (b) gate --branch 在主線 HEAD 不擋、空 diff 比 verify_strings | 新 → 併入 | #86 **A3** | 讀碼:gate.sh 無任何 HEAD==main 檢查;提示路徑用 apply.sh 的 worktree_dir 規則 |
| (c) 派工不照 routing 帶模型無人擋 | 新 → 併入 | #86 **A8** | 可強制的點是 agent 開場自己跑的 `rules.py pack`:config 加 model_tiers,低於角色下限 rc=2;codex:*/agy:* 不在 tiers 不比 |
| (d) verify_strings 把主工作樹票檔未提交 diff 算進去 | 新 → 併入 | #86 **A4** | 讀碼:gate.sh 第 289–305 行 changed 名單濾了票檔、content blob(`diff HEAD`)沒濾 |
| (e) worker 3600 秒大半花在跑測試 | 新 → 併入 | #86 **A18** + 票面 test_plan 形式 | 派工文由 auto-fix 抽票的 test_plan 成「只准跑的測試」段 + 固定一行禁全套;範本同步寫 |
| (f) 主線 docs commit 讓別票 gate 基底失效 | **不開票** | 流程 | A 的 land.sh:374 只檢 base_sha 是不是 main 祖先,docs commit 不破祖先、A 不擋;T 的 land-ticket.sh 檢「gate base = main HEAD」才擋 —— T 的題。建議:主線的 docs 提交排在一條鏈 land 之後、或 T 的 gate 基底判準放寬成祖先(T 票) |
| (g) status.py:787 "sonnet";model README 卡名不符;agy:gemini 無卡 | 新 → 併入 | #86 **A6–A7** | 補 memory/model/gemini-3.8-flash-high.md + 新測試 test_routing_cards.py 守「每個 routing 值有卡」 |
| (h1) apply.sh / sync 收尾提示猜落地入口 | 新 → 併入 | #86 **A27** | sync-to-project.sh:305 已依 land-ticket.sh 存在分支;apply.sh:797 固定印 land.sh,改用同一判準 |
| (h2) `create --verify-string` 存物件、開題者卡要純字串 | **不開票** | #78 解凍時改卡 | 讀碼:`normalise_verify` 把 `路徑:字` 存成 {path, contains} 是 SCHEMA 明寫的三種認法之一,gate/verify 都認;錯的是記憶卡那句 |
| (h3) auto-fix 不在票上記 patch 路徑 → T `gate --redo` 拒 | 新 → 併入 | #86 **A26** | A 沒有 `gate --redo`,但 apply.sh 是收件那一手、同步到 T;patch_ref 是 SCHEMA 既有欄位 |
| (i) memory.py 同步到專案後非 consolidator 票蓋不到 → 重複開整理票 | 新 → 併入 | #86 **A38** | 判定擴成「開著且寫入範圍覆蓋該檔的任何票」;consolidator 字樣不變(不破 #76 A3) |

統計:保留 1(#76)、凍結 1(#78)、作廢 6(#77 #79 #80 #82 #83 #84)、新開 1(#86)、不開票 2((f)(h2))。

## #86 的形狀

- role worker / model opus / tool claude-code / needs_verifier **false** / interface_fixed false
- 41 條驗收,每條帶出處標籤 `[原 #n-k]` / `[新 (x)]`
- allowed_write_paths 51 條(含 4 支新腳本 / 新測試檔);out_of_scope 硬擋既有記憶卡正文、land.sh、event.py、control_harness、DECISIONS/HANDOFF
- verify_strings 9 條純字串,每條都是交件 diff 一定會出現的字
- **worker.timeout_seconds = 10800**:估 3,000–3,500 行、45 檔;#74 的數據是 1,294 行實作 30 分鐘、其餘 3,000 秒全在跑全套 → 本票實作 75–100 分鐘 + 分組指定測試與 17 條變異自證 40–60 分鐘 ≈ 2–2.5 小時,3600 做不完;×1.3 餘裕取 3 小時。retry_limit 2(最壞 3 輪 9 小時)。reviewer.timeout 1800 預估夠(#76 reviewer 121 秒),逾時再調是 config 改動、要人授權。

## 建議執行順序

1. **#76 先落地**(要主線裁):`review.sh` 重跑覆核(sv 已過期)→ `land.sh t76`。約 10 分鐘。
2. `ticket.py set 86 base_sha <#76 落地後 main sha>` → `auto-fix.sh 86`(或 apply/gate 手派),worker 一輪 ≈ 2–2.5 小時;票面 outline 給了 G1→G11 的施工順序(先修守衛,再改 auto-fix.sh 四組,最後 repo-map / lint / 整理啟動)。
3. 閘門綠 → review.sh 覆核 → land(全套只跑這一次)。
4. #86 Done 後解凍 #78,人工派兩模型整理記憶正文(含 opener 卡的 verify_strings 那句)。
5. 全部落地後 `sync-to-project.sh` 到 tabby_pool,T 側照 (f) 的建議另開 T 票。

無可平行項:一張票、一支分支。

## 改動過的檔(主線用 docs 通道提交;已確認 家目錄絕對路徑前綴掃 tickets/*.json 無命中)

tickets/76.json(verify 三格相對路徑)、tickets/77.json、79.json、80.json、82.json、83.json、84.json(handoff + Cancelled)、tickets/78.json(handoff + frozen + model opus)、tickets/86.json(新)、docs/review/20261003-a-reticket.md(本檔)。`board/events.jsonl` 由 ticket.py 自動追加事件。
