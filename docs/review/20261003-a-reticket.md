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

## #86 拆票(2026-10-03,開題者 Fable)

使用者裁示(原話):「把86切成2張 一張是改code 一張是改prompt 分別跑的測試照前面說的 一邊跑regression 一邊跑llm review (opus)」
追加(原話):「worker 做完之後才跑regression　regression 有錯在自動autofix 發新worker處理． 所以worker時間跟regression 時間應該要切開」

結果:#86 → Cancelled;**#87 code**(40 條,Ready,depends_on #76 落地)、**#88 prompt**(P1–P12,Ready,depends_on #87 落地)。
兩張 `allowed_write_paths` 不重疊,且互相列進對方的 `out_of_scope` 硬擋(#87 37 檔、#88 12 檔)。

| | #87 code | #88 prompt |
|---|---|---|
| 改什麼 | scripts/、tests/、board/config.json、code-map/、gemini 卡 | memory/*/README.md、CLAUDE.md、docs/(DISPATCH-TEMPLATE、WORKFLOW、SESSION-START、MEMORY、CODE-MAP)、tickets/SCHEMA.md、templates/dispatch-verifier.md、dispatch-consolidator.md |
| 驗收 | worker 跑 test_plan 列的測試 + 18 條變異自證;閘門跑對照表模組 + verify.py 回歸 | 不寫測試;review.sh(opus)照 P11 審查清單逐檔判;既有守衛不改仍綠(P12) |
| needs_verifier | false | false |
| worker.timeout_seconds | 10800 | 3600 |

### 逐條去向

| 原驗收 | 去向 | 備註 |
|---|---|---|
| A1–A5 | #87 A1–A5 | 原文 |
| A6 | 拆:#87 A6(test_routing_cards.py + gemini 卡,內容釘死)/ #88 P1(model/README 卡名清單與命名規則句) | gemini 卡放 code 票:守衛與它守的檔要同一份 patch 才綠,放 prompt 票會成環;README 卡名的檢查改由覆核者對目錄 |
| A7 | #87 A7 | 原文 |
| A8 | 拆:#87 A8(model_tiers、pack rc=2)/ #88 P2(DISPATCH-TEMPLATE「模型低於這個角色的 routing 下限」那句) | |
| A9–A15 | #87 A9–A15 | 原文 |
| A16 | #88 P3(整條) | WORKFLOW「拋棄式控制根」、SESSION-START 對齊 |
| A17 | #87 A17 | 原文 |
| A18 | 拆:#87 A18(auto-fix 產「## 只准跑的測試」段,固定行加「不跑 verify.py 全部回歸,回歸是閘門的事」)/ #88 P4(dispatch-verifier.md 與 DISPATCH-TEMPLATE 同一條) | templates/dispatch-worker.md 不存在(實測),不新建 |
| A19–A24 | #87 A19–A24 | 原文 |
| A25 | 拆:#87 A25(test_auto_fix_codex.py 與變異表)/ #88 P5(SCHEMA 執行列「必配 tool=codex」) | |
| A26 | 拆:#87 A26(apply.sh 記 patch_ref)/ #88 P6(SCHEMA 交付列「apply.sh 收件時寫」) | |
| A27–A37 | #87 A27–A37 | 原文;#77 規劃 7 與 #79 規劃 1–2 的文件交付(原在 #86 寫入範圍、驗收沒寫出來)→ #88 P7(CODE-MAP、先 query 再 grep)、P8(分層契約一份正文 + 三處指路) |
| A38 | 拆:#87 A38(memory.py 判定)/ #88 P9(MEMORY.md 判準句) | |
| A39 | 拆:#87 A39(consolidate-memory.sh,照現有佔位填、不改範本)/ #88 P10(dispatch-consolidator.md「怎麼用」段) | |
| A40–A41 | #87 A40–A41;文字半併在 #88 P10 | |
| (新)覆核審查清單 | #88 P11 | 對 DECISIONS / SCHEMA / role README 無矛盾;點名的入口旗標在程式裡存在;只寫在文字上卻可由程式強制的規則逐條列 |
| (新)既有守衛 | #88 P12 | test_no_project_names、test_memory、test_dispatch_template、test_rules_delivery、test_ticket、test_sync_to_project 不改仍綠 |

### 依賴與平行

#88 的每條文字都指向 #87 的新入口,覆核 (b) 要在分支上 grep 得到 → depends_on #87 落地。
可先做:#88 的 worker 可在 #87 Running 時以 `auto-fix.sh 88 --no-review` 平行派(入口名照 #87 票面寫;P8 分層原則句不依賴入口);
覆核前 `apply.sh rebase` 到含 #87 的主線,再 `review.sh 88`。

### 逾時(worker 時限與回歸切開計;都未實測)

worker 時限只算「寫程式 + 跑 test_plan 列的測試 + 變異自證」;閘門回歸(對照表模組 + verify.py)由 auto-fix 在 worker 返回後跑,紅了下一輪派新 worker,不吃 worker 時限。
#74 那 3,000 秒是 worker 自己在副本裡跑全套,不是閘門 —— 兩張的派工文與 test_plan 都已禁。

| 段 | #87 | #88 |
|---|---|---|
| ① 寫程式 / 文字 | 2,700–3,200 行 / 33 檔,43 行/分(#74)+ 讀碼 → 75–95 分 | 150–300 行 / 12 檔,每句先 grep 入口 → 30–40 分 |
| ② 跑指定測試 | 相關模組整輪 400–450 秒 × 2 + 單條紅綠 → 25–30 分 | 六支守衛 × 2 → 5 分 |
| ③ 變異自證 | 18 條 × 每條只跑一條案例 → 20–25 分 | 0 |
| 合計 → 時限 | 120–150 分,上限 ×1.2 → **10800** | 35–45 分,×1.3 → **3600** |

#86 原估 3 小時的大頭是 ① 寫程式(75–100 分,約佔 2–2.5 小時估值六成),不是跑測試。

### #78 建議:不併進 #88

1. D-007 / D-013 要求記憶整理由兩個不同模型討論、留討論檔;#88 是一個 worker 寫、一個 opus 覆核,併進來不是違反裁示,就是要把 #88 改成整理票流程。
2. #78 的判準是 #88 寫定的分層契約(P8)與 #87 的 lint / repo-map —— 先定規則再整理正文,順序不能並。
3. 寫入範圍:#78 改既有角色卡 / 模型卡 / project 卡正文,#88 正好把這些列 out_of_scope;併了就沒有這道分界。
4. 規模:#78 約 15–20 個記憶檔 + 兩份討論,會讓 #88 的時限與覆核量翻倍。

建議主線:#78 的 depends_on(現在指已作廢的 #77)改成 #87 + #88 落地,解凍條件同步;可用 #87 A39 的 consolidate-memory.sh 起兩模型。(開題者未動 #78。)

### 改動過的檔

tickets/86.json(handoff + Cancelled)、tickets/87.json(新)、tickets/88.json(新)、docs/review/20261003-a-reticket.md(本節)。`board/events.jsonl` 由 ticket.py 自動追加事件。
