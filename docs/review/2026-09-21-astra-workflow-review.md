# 外部審查(Codex astra,2026-09-21)→ 逐條處置

審查者:Codex `gpt-6-astra`,獨立上下文,全程未改檔。處置裁示:**D-014**。
主線的順序裁示:**先硬閘門、再取消 flake 自動判綠、再交接閉環;自動派 worker 這一輪不做。**

> **總評原話**:「最大三個風險是回歸與覆核契約沒有真正擋住落地、單跑綠掩蓋順序/負載問題、
> 失敗回報缺少版本與負責人而失聯。我會先接通票 tags、baseline 證據及版本化 review 的硬閘門;
> 取消自動把疑似 flake 判綠;建立有輪次、完整修復交接、持久證據及 owner 的閉環。
> **這三件先完成,再上自動派 worker,才不會把目前的漏接自動放大。**」

> **開場原話**:「這套流程明天可以由主線人工帶跑,但還不能依文件宣稱的自動交接與閘門保證來運作。」

路徑一律相對本 repo 根;審查原文的行號是它當時讀到的那一版。

## 1. 角色與互動

| # | 發現(審查原話節錄) | 依據 | 處置 |
|---|---|---|---|
| 1.1 | 「實作者與驗證者的『平行』缺少明確接點…不是驗證者等 patch,就是整個驗證工作延後才開始」 | `docs/VERIFICATION.md:24`、`templates/dispatch-verifier.md:18` | **已處置**:驗證拆成兩段 —— 第一段(票 Ready 就能做:寫案例、宣告 TAGS、寫登記片段、填票的 `verify` 欄)與第二段(拿到 patch 後跑 `verify-case.py check`)。中間的產物由程式保管(票的 `verify.baseline`),agent 不等待。`docs/VERIFICATION.md` §流程、`memory/role/verifier.md`、`templates/dispatch-verifier.md` |
| 1.2 | 「patch 轉成可落地分支仍需要主線接手,角色表把這一手藏掉了」 | `docs/ROLES.md:13`、`scripts/land.sh:86` | **部分處置**:角色表改成明說落地器**不**套 patch、**不**關票、**不**自動派 worker(`docs/ROLES.md` 的引言框),能力表也列了「套 patch / 建分支 / commit = 主線手動」。**「做成一個程式入口」未實作**,已進 `docs/TODO.md` §2 —— 這一輪的重點是先讓宣稱與事實對齊,不是先加自動化。 |
| 1.3 | 「最容易空轉的是『gate 紅了以後由主線人工接續』…規格同時寫『修完自動 redo/land』與『覆核不自動』」 | `docs/WORKFLOW.md:47,56` | **部分處置**:覆核成為 land 的硬閘門(缺或過期都拒絕),所以「待覆核」現在是一個**機器看得見的停止點**。**以完成事件喚醒主線**那一半未實作(需要自動派 worker 那條線,這一輪不做)。 |

## 2. status.json 與 flake 判定

| # | 發現 | 依據 | 處置 |
|---|---|---|---|
| 2.1 | 「目前 status 是結果摘要,不是新 worker 可直接開工的交接包…缺 `base_sha`、票版本快照、worktree、產品/驗證 patch 路徑及雜湊、輪數、上一輪 EVIDENCE、重現指令與 cwd/環境」 | `scripts/status.py:162,181`、`scripts/gate.sh:109` | **已處置**:`repair_context`(version 1)帶齊上述每一格,patch 那一格帶 sha256。`scripts/status.py`、`tests/test_status.py::test_the_repair_context_carries_what_a_new_worker_needs` |
| 2.2 | **實測**「原生 subTest 被解析成錯誤的重跑 ID」;解析出來的 case 是 `__main__.NativeSubtest.test_engine) (engine='firefox'.test_engine`,`subtest` 與 `engine` 都是空字串 | `scripts/status.py:46,67` | **已處置**:改成先取 `FAIL: <方法>`,再把後面的括號群**依深度**拆開,第一個點號形狀的當 qualifier、其餘當 subtest 參數;`engine` 的正規表示式吃引號。審查那段原文逐字進了測試。`tests/test_status.py::test_a_native_subtest_parses_into_a_rerunnable_id` |
| 2.3 | **實測+推論**「『單跑綠 = flake』確實會放過順序依賴…目前 gate 正是以所有失敗案例單跑成功為由回傳 0,而且重跑輸出直接丟掉」 | `scripts/gate.sh:128` | **已處置**:單跑綠只標 `suspected_flaky`,**原始失敗與非零 rc 保留**;再用原順序整組重跑一次(`AC_FLAKE_RERUN_GROUP=0` 關掉),仍紅是真紅、綠了也只是疑似;重跑輸出存成 `extra_logs` 並複製進 run 目錄。審查的順序依賴反例寫成 `tests/test_status.py::test_an_order_dependent_failure_is_not_written_off_as_flaky` |
| 2.4 | 「status 的生命週期會誤導接手者…沒有 `run_id`;land 在 merge/push 前就寫 `done, rc=0`;成功後又移除存放 log 的 worktree;無測試可跑的路徑直接退出」 | `scripts/status.py:59`、`scripts/land.sh:231,250`、`scripts/gate.sh:200` | **已處置**:`reports/t<n>/<run_id>/status.json` 一輪一個目錄;log 複製進 `logs/`;`phases` 把 gate/merge/push 分開記;gate 與 land 的**每一條退出路徑**都寫終態(含「沒有測試可跑」rc=2 與「對不到任何模組」rc=3)。 |

## 3. 測試案例介面

| # | 發現 | 依據 | 處置 |
|---|---|---|---|
| 3.1 | 「登記介面有了,但『gate 跑票 tags』沒有接好…全套會透過執行器自測**間接**跑回歸,但失敗輸出被 `capture_output=True` 收走」 | `scripts/verify.py:54`、`scripts/gate.sh:98,179`、`tests/test_verify_runner.py:6` | **已處置**:`gate.sh --ticket` 讀票的 `verify.tags`(併 `tags`)正式呼叫 `verify.py --tag …`,原始輸出存 `verify.log` 並進狀態檔的 `logs`;`--full` 正式呼叫全部回歸。宣告了 tags 卻選不到案例 = 非零。 |
| 3.2 | 「沒有『乾淨主線該紅』的機器檢查…`ticket.py verify` 只是印出這些欄位」 | `templates/dispatch-verifier.md:25`、`tickets/SCHEMA.md:16`、`scripts/ticket.py:746` | **已處置**:`scripts/verify-case.py check <票號>` 在乾淨主線副本與 candidate 各跑同一份案例,把案例數、紅的是哪幾條、skip、兩邊 sha 寫進票的 `verify.baseline`;**import 失敗分開數並明列**,而且讓 `ok` 為 False。`tests/test_verify_case.py` |
| 3.3 | 「案例本身錯了,沒有完整的處理路徑…worker 又不得放寬斷言,流程只剩模糊的『需要裁示』」 | `docs/VERIFICATION.md:28`、`memory/role/verifier.md:4` | **已處置**(文件層):`test_defect` 交接類型 —— worker 交一筆 `objections[{category: test_defect}]` 附反例,主線派**獨立驗證者**修案例,產品 worker 不改 oracle。`docs/VERIFICATION.md` §案例本身錯了、`docs/WORKFLOW.md` §交接類型、`memory/role/implementer.md` |
| 3.4 | 「案例交付與登記仍有衝突…驗證者的 `work/` 已含產品 patch,卻要求交『只含自己 verify 檔』的 diff,沒有給差分基準」 | `docs/VERIFICATION.md:21`、`templates/dispatch-verifier.md:18,28` | **已處置**:`verify-case.py extract` 的差分基準寫死成乾淨主線,只抽 `verify.files`;登記改成一票一個片段 `verify/TAGS.d/<票號>.md`,`verify.py` 直接認片段,`tags-merge` 負責折進 `TAGS.md`(撞名而說明不同會出聲)。開題者只宣告 tags。 |

## 4. token 浪費

| # | 發現 | 處置 |
|---|---|---|
| 4.1 | 「共用規矩重複載入。CLAUDE 要整份派工規範一起給,角色卡卻說 prompt 只指路」 | **已處置**:`CLAUDE.md`、`memory/role/README.md`、`docs/SESSION-START.md` 三份現在說同一句話 —— 短命角色載入「角色卡 + 自己模型的記憶 + `DISPATCH-TEMPLATE` + 這張票 + 票的 `decision_refs`」;全域交接、事件流、開票清單**只有主線讀**;其他文件 grep 定位。`SESSION-START` 多了一張「誰讀什麼」的表。**「產生按角色裁切、帶版本的規則包」未實作** —— 指路已經解掉重複載入,自動裁切要先有人證明省下來的量。 |
| 4.2 | 「每輪換新 worker,但只傳上一輪 EVIDENCE…三輪可能重查相同 code」 | **已處置**:`EVIDENCE.md` 必備五段,其中兩段是新的:**已排除的假設**(查過什麼、為什麼排除、證據在哪一行)與**最小重現**(一句可貼的指令 + 預期輸出);`repair_context` 帶輪數與上一輪 EVIDENCE 路徑。`memory/role/implementer.md` |
| 4.3 | 「同一組 tag 回歸可能被 worker、驗證者、gate 重跑」 | **已處置**:驗證者**不跑 tag 回歸**,只跑自己的案例與 `verify-case.py check`。`memory/role/verifier.md`、`templates/dispatch-verifier.md`、`docs/VERIFICATION.md`。**runner 層級的去重(綁產品+測試+環境雜湊)未實作**。 |
| 4.4 | 「共享登記檔造成整票等待」 | **已處置**:見 3.4 的 `verify/TAGS.d/`。 |

## 5. 資訊漏接與無人處理的 bug

| # | 發現 | 依據 | 處置 |
|---|---|---|---|
| 5.1 | 「實作者的反駁沒有可靠的接收與處置契約」 | `memory/role/implementer.md:5`、`docs/WORKFLOW.md:48`、`scripts/ticket.py:632` | **已處置**:票的 `objections[]`(category / body / evidence / owner / disposition / blocking / follow_up);未處置的阻擋項 **land 拒絕、`close` 拒絕**。`scripts/land.sh`、`scripts/ticket.py::objection_problems` |
| 5.2 | 「『紅在票沒動的檔』不是可靠的歸責規則…產品改壞行為,本來就可能紅在完全沒修改的測試檔」 | `scripts/status.py:115`、`docs/WORKFLOW.md:48` | **已處置**:那一條規則**拿掉**;改用同條件的 baseline / candidate 對照;**未歸因前由原票 owner 持有**。`docs/WORKFLOW.md`、`docs/ROLES.md`、`memory/role/implementer.md` |
| 5.3 | 「三輪失敗與 flake 都可能只『被看見』,沒有追到底」 | `docs/WORKFLOW.md:48`、`scripts/status.py:162`、`scripts/gate.sh:140` | **已處置**:`ticket.py round <n> <r> --red` 在第 `retry_limit+1` 輪把票轉 **Blocked、owner=main** 並發 `ticket.attempt.failed`;疑似 flaky 進 `reports/flaky.jsonl`,達門檻(`flaky_threshold`,預設 3)發 `decision.asked`。**「自動開修復票」未實作** —— 只到 NeedsDecision,開不開由主線決定(審查的 改法 本來就給了這個二選一)。 |
| 5.4 | 「覆核沒有綁定被覆核版本,也沒有被 land 消費」 | `tickets/SCHEMA.md:17`、`scripts/land.sh:125` | **已處置**:`review` 多兩格 —— `state_version`(由 `ticket.py set` 自動蓋,票之後任何一動都讓它過期)與 `sha`(最終 patch / 分支的頭);land 與 `close` 都比對,不符或缺就拒絕。 |
| 5.5 | 「state_version 有遞增,卻沒有防遲到回報的比較…平行回寫 verify、review 時可能互蓋」 | `tickets/SCHEMA.md:12`、`scripts/ticket.py:519` | **已處置**:`ticket.py set --expect-attempt N` / `--expect-state-version N`,對不上 rc=4;讀-比對-寫回包在同一把 `mkdir` 鎖(`tickets/.ticket.lock`)裡。 |

## 6. 與能力表的落差

| # | 發現 | 處置 |
|---|---|---|
| 6.1 | 「能力表漏列重大未實作項目…沒有列票 tags 串接、review 檢查、baseline 驗紅,以及套 patch/關票」 | **已處置**:`docs/WORKFLOW.md` 的能力表逐項對著**真實入口**重寫,新增 12 列,未實作的那幾列寫明「目前由誰手動執行」。 |
| 6.2 | 「能力表還誤稱 land 檢查 verify_strings…land 合併、push 後直接結束,沒有關票」 | **已處置**:land 成功後印「#n 已合併、尚未關票 —— `ticket.py close n`」,能力表分開呈現。`tests/test_land.py::test_a_green_landing_says_the_ticket_is_still_open` |
| 6.3 | 「Done 的契約可以繞過」 | **已處置**:`done_blockers()` 一份必要條件,`close` 與 `set state Done` 共用;**弱檢查不准自動關票**。 |
| 6.4 | 「『只能一個 land』沒有互斥鎖」 | **已處置**:`land.sh` 用 `mkdir .land.lock`(原子),holder 檔寫 pid / 時間 / 分支,拿不到就指名;trap 在每一條退出路徑放鎖。票的寫回用**另一把**鎖,所以不會與程式落地互相阻塞。 |
| 6.5 | 「專案端入口與同步不完整…同步腳本最後要求呼叫本 repo 沒提供的 `land-ticket.sh docs`,而同步只覆寫現有卡、不刪已退役卡」 | **已處置**:`sync-to-project.sh` 留 manifest,退場的角色卡**唸出來再刪**;結尾先看專案有什麼(`land-ticket.sh` / `land.sh` / 都沒有),再說得出對它成立的那一句。多了 `--dry-run`。 |
| 6.6 | 「遷移仍未完成,範本也沒跟上 D-010;gate 範本不接受 `--ticket`」 | **部分處置**:`scripts/gate.example.sh` 接 `--ticket` 並示範狀態檔與票的回歸;`docs/TODO.md` 新增「明天用哪一份」(票庫、事件流、閘門版本)並把已完成項打勾。**遷移本身(§0 那六條)仍未做** —— 那是另一批工作,不在這次處置範圍。 |

## 未處置的清單(連同理由)

| 項目 | 理由 |
|---|---|
| 自動派新 worker(headless `claude -p`,三輪上限) | **主線裁示這一輪不做**。審查自己的總評就是「這三件先完成,再上自動派 worker,才不會把目前的漏接自動放大」。 |
| 套 patch → 建分支 → commit 的程式入口(1.2) | 這一輪先讓宣稱與事實對齊(角色表 + 能力表已改)。做成入口是新功能,排進 TODO §2。 |
| 以完成事件喚醒主線,禁止輪詢 status(1.3) | 需要自動派 worker 那條線;覆核已經是機器看得見的停止點,先用它。 |
| flake 達門檻**自動開修復票**(5.3) | 只做到 `decision.asked`。自動開票會在門檻誤判時生出一堆沒人認領的票;先讓主線看見累計次數。 |
| land 前檢查 `verify.files` 都在分支上 | 規格已定、未實作,留在能力表與 TODO 裡。 |
| 按角色裁切、帶版本的規則包(4.1) | 指路已經解掉重複載入。自動裁切要先有人量出它多省了什麼。 |
| runner 層級的回歸去重(4.3) | 驗證者不再重跑 tag 已經拿掉最大那一份;綁三段雜湊的去重需要先有穩定的環境雜湊。 |
| 遷移計畫 §0 的六條 | 另一批工作。 |
