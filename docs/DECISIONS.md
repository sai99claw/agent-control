# 裁示

一列一條;來源要有原話;推翻舊的要指名。

| 編號 | 裁示 | 來源 | 日期 | 狀態 |
|---|---|---|---|---|
| D-001 | 規則住在程式裡,不住在提示裡:票欄位、落地拒絕條件、寫入範圍、租約由 `scripts/` 與 `board/` 執行 | 產品負責人提案 v1.1 §4「不能只靠 agent 在對話中記得規則」;tabby_pool 2026-09-10 的事故清單 | 2026-09-12 | ✅ |
| D-002 | 排程與落地分開:排程器只提案,落地是純機械流程 | 主線對調度員實驗的評估(一天一百萬 token、四十則回報、撞額度靜停兩天) | 2026-09-12 | ⛔ 2026-09-21 由 **D-010** 取代(整個排程器 / 調度員角色拿掉,順序回主線) |
| D-003 | agent 的每個動作是控制台上的一筆事件,控制台只讀事件不猜 | 產品負責人 2026-09-12「agent 的行為要具體反應到控制台上」 | 2026-09-12 | ✅ |
| D-004 | code wiki 第一版只做「查找失敗過的模組卡片 + 過期偵測」,不預先鋪滿 | `docs/CODE-MAP.md`;成本未證明前不擴 | 2026-09-12 | ✅ |
| D-005 | 模型私有記憶:一模型一檔、同模型跨 session 共享、超過可調門檻由高階模型整理 | 產品負責人提案 §13(原始要求) | 2026-09-12 | ✅ |
| D-006 | **模型私有記憶**(`memory/model/<model>.md`,每個同模型 session 開頭必讀)預設上限 2K 字元;專案共用層不受此限;超過就觸發與高階模型的討論(老師帶學生),討論結果可以壓縮也可以**打破上限**,提高的理由寫進檔案 front matter | 產品負責人 2026-09-12(原話說「專案私有記憶」,隨即更正為**模型私有記憶**):「原始規範 2K…超過會觸發機制跟高級 session 討論要如何壓縮記憶,2K 的限制可以被討論結果打破,這個過程就像是老師帶學生成長」 | 2026-09-12 | ✅ |
| D-007 | 模型之間的討論(記憶整理、雙方案規劃、外部審稿、票面爭議)一律用 `docs/DISCUSSION.md` 的標準格式留檔;記憶整理沒有討論檔不准執行;停止條件先寫、分歧保留不硬合 | 產品負責人 2026-09-12:「模型私有記憶那我希望讓高低模型討論出結果,agent control 那邊也要有標準的模型討論格式」 | 2026-09-12 | ✅ |

## D-008(2026-09-13)角色記憶:每個 session 開場帶模型記憶 + 角色卡
tabby_pool 三天實測:角色定義散在派工 prompt 裡會漂,同一主張被證明三到四次。角色卡進 `memory/role/`,
證據帳 `EVIDENCE.md` 承接證明,上游不重做。對應 tabby D-G114。

## D-009(2026-09-13)覆核者改名驗證者;兩層驗證(單元/回歸)與功能標籤
> ⚠️ **這一條的 `VERDICT.md` 那一半 2026-09-21 被 D-010 取代**:驗證者不判 PASS/FAIL、不寫 VERDICT,
> 對錯由閘門跑票的 `tags` 判。兩層驗證與功能標籤那一半不變。
使用者:驗證者「去看實作者有沒有真的把票上要的功能做出來,票上寫的驗證由他實作、由他跑,有問題跟實作者講」;
實作者「做完要有自己的 unit test,過了才去找驗證者」;所有 worker 的單元測試有簡易框架收;驗證者的案例進另一個
回歸框架、帶功能標籤可篩。規範 `docs/VERIFICATION.md`,執行器 `scripts/verify.py`,角色卡 `memory/role/verifier.md`。
變異測試留給實作者證明自己單元測試有牙齒;驗證者的獨立性來自案例是它自己從票面寫的。

## D-010(2026-09-21)調度員 / 排程器整個角色拿掉;回歸紅了自動派新 worker;驗證者只寫案例
使用者原話(來源專案 D-G122):「如果 regression 打出 bug 就找新 worker 輸入票跟 diff 跟 bug,讓他直接去解,
這樣這過程也不需要你來叫他」「用 A 方案,新 worker 覺得 issue 真的有問題再直接問你就好」
「驗證者做的事情只負責寫 case 然後把東西加進測試系統,但他要確保他的測試 case 是正確的,
把自己寫的 case 要怎麼使用寫到票裡面,然後關掉自己」。

1. **角色只剩七個**:產品負責人(人)、主線、開題者、Worker / 實作者、驗證者、落地器(腳本)、知識維護。
   **調度員 / 排程器不再存在**(不是瘦身,是收掉):順序由主線決定,或由腳本依 `allowed_write_paths` 提案;
   紅了由落地器自動派新 worker。理由:它的判斷九成是機械的,而長命 agent **活著就燒錢**
   —— 每看一次狀態就是整份上下文重送一輪。取代 D-002。`memory/role/dispatcher.md` 已刪。
2. **狀態檔**:每次 gate / land 寫 `reports/t<n>-status.json`(`state`、`rc`、`failures[{case,file,engine,log,line,excerpt}]`、`flaky[]`)。
   紅的案例**先單獨重跑一次**判 flake,全 flaky 視為綠。**已實作**:`scripts/status.py` + `scripts/gate.sh --ticket`。
3. **自動派新 worker**:真紅由落地器用 headless `claude -p` 起**新** worker(票 + 目前 patch + 紅榜 + 上一輪 EVIDENCE),
   三輪上限;三種情況停下來報主線(票寫錯 / 需裁示、三輪仍紅、紅在票沒動到的檔)。**覆核不自動**:主線讀 patch 記 `review` 後才 land。
   **規格已定、腳本未實作**(來源專案 #616 實作中)。
4. **驗證者只寫案例**:寫 → 乾淨主線上紅、patch 上綠 → 登記 tag → 把怎麼跑寫進票的 `verify` 欄(`files`/`tags`/`run`/`notes`)→ 交 `patch-verify.diff` → 結束。
   **不判 PASS/FAIL、無 VERDICT、不讀 EVIDENCE、不重跑閘門、不輪詢**;對錯由 gate 跑票的 `tags` 判。D-009 裡的 `VERDICT.md` 退場。
   主線的「覆核 = 讀 patch 記 `review`」**不是**驗證者的工作,留著。
5. **不准輪詢**;**同時最多兩個會起瀏覽器的 agent**;**優先序改變時停掉低優先的 agent**;
   **實作者交付前必跑** tag 回歸 + 整支瀏覽器模組 + 時序類的票在負載下跑 3 次。

## D-011(2026-09-20)模型路由:實作預設 Codex sol,起 server / 瀏覽器的票派 Opus
使用者原話(來源專案 D-G121):「開工……實作優先使用 Codex sol」。
1. 實作者、驗證者預設 Codex `gpt-5.6-sol`;驗證者也可以是 Sonnet;開題者 Fable;主線 Fable。
2. **`-s danger-full-access` 不設**,所以 Codex 在 `workspace-write` 底下**綁不了埠、起不了瀏覽器**
   —— 要起 server / 瀏覽器實跑的票派 **Opus**。
3. **帳務 / 資料遷移類的票加派第二家模型**(agy gemini 或 Codex)做**機器可驗**的獨立驗證。
4. 額度:Codex 的五小時桶**一次三張就會用盡**;用完**退 Opus 一張並報主線**,不默默改走付費通道。
   表在 `docs/ROLES.md` §模型路由,可調的那一份在 `board/config.json` 的 `routing`。

## D-012(2026-09-21)patch 管線:落地前重套、重生清單、出乾淨 diff
多張票接連落地幾乎一定撞到「所有票都往尾端附加的登記檔」與「自動產生的清單」。
落地前把 patch 用 GNU `patch`(吃 fuzz)套到**當前主線的副本**、重生清單、出一份乾淨 diff;
**`.rej` 數量 ≠ 0 一律當失敗**;`diff -ruN` 刪檔的 `+++` 側要手改成 `/dev/null`(不然 `git apply` 只清空不刪);
票的 `verify_strings` 落地前先對 patch `grep` 一次;**patch 檔頭只准 `base/…` / `work/…` 的相對形式,絕對路徑閘門要拒**
—— 🩸 真的發生過:絕對路徑的檔頭讓檔案被寫進暫存目錄,套用成功、閘門也綠,而被改的不是 repo 裡那一份。
細節與理由:`docs/WORKFLOW.md` §patch 管線。

## D-013(2026-09-21)記憶整理:兩個模型、只留原則、記憶與紀錄分開
使用者原話:「任一層記憶超過上限時,由**兩個不同模型**或一個高階模型討論後整理,不是單一 session 自己刪」;
「整理後只留**具體的原則、行為準則、思考方式**,**不直接寫案例**,允許引用票號當來源」。

1. **上限適用任一層記憶**:`memory/model/`、`memory/role/`、`memory/project/`,以及專案給 agent 讀的那一份共識。
   名單在 `board/config.json` 的 `memory.applies_to`。
2. **整理由兩個不同的模型**(`memory.consolidators`,例如 Fable + Codex astra)或一個明確更高階的模型帶;
   **不准單一 session 自己刪自己的記憶** —— 它最先刪掉的是它自己看不懂的那幾條,而那正是別的模型看得出價值的那幾條。
3. **產出是原則,不是案例**:一條保留下來的記憶要能直接當行為準則用;案例用票號引用
   (例:「快照層只畫說得出處的畫面(#585)」),原文留在紀錄類文件。
4. **記憶與紀錄分開**:agent 每次只載入「角色卡 + 自己模型的記憶 + 專案共識 + 這張票」;
   紀錄類(`DECISIONS` 歸檔、`HANDOFF` 歷史、`docs/review/`、`discussions/`)**用 grep 定位,不整份讀**。
   理由:原則每次讀都在用,案例只有寫的那一天在用;一年問一次的東西不該每個 session 付一次。
5. **`scripts/memory.py check` 超標時自動開一張「記憶整理」票並指定那兩個模型**;票面的驗收就是上面 3、4 兩條。
6. `memory/model/fable.md`、`memory/model/opus.md` 已照這個規矩重寫一次,當範例。
   文件:`docs/MEMORY.md` §記憶不是紀錄;同步的一句話在 `CLAUDE.md`、`memory/role/README.md`、`docs/SESSION-START.md`。

**補註(2026-09-21,使用者裁示)**:`memory/project/`(專案共識 = 前人踩坑的經驗)**不設大小上限**,它本來就會長得比較快,之後的人進去 grep 或整份讀都可以;它仍只准寫原則 / 行為準則 / 思考方式並引用票號,不貼案例原文。有上限、超標要兩個模型整理的是 `memory/model/` 與 `memory/role/`(每個 session 都要載入的那兩層)。

## D-014(2026-09-21)外部審查的處置:硬閘門、取消 flake 自動判綠、交接閉環
來源:Codex astra 的獨立審查(全文與逐條處置 `docs/review/2026-09-21-astra-workflow-review.md`)。
總評原話:「最大三個風險是回歸與覆核契約沒有真正擋住落地、單跑綠掩蓋順序/負載問題、失敗回報缺少版本與負責人而失聯…
**這三件先完成,再上自動派 worker**,才不會把目前的漏接自動放大。」主線照它的順序做,**自動派 worker 這一輪不做**。

1. **硬閘門**:`gate.sh --ticket` 真的呼叫票的 `verify.tags`(原始輸出存檔);`--full` 跑全部回歸;
   land 檢查 `review`(綁票版本 + 分支 sha)與 `objections[]`(未處置的阻擋項);land 持一把 mkdir 互斥鎖;
   進 Done 的必要條件 `close` 與 `set state Done` 共用,弱檢查不准自動關票。
2. **取消 flake 自動判綠**(取代 D-010 第 2 點的後半):單跑綠只標 `suspected_flaky`,
   **原始失敗與非零 rc 保留**,再用原順序整組重跑一次判真紅。實測反例:第一條測試污染共用狀態、
   第二條檢查乾淨狀態 —— 整組必紅、單跑必綠。疑似 flaky 進持久事件帳 `reports/flaky.jsonl`,達門檻發 NeedsDecision。
3. **交接閉環**:狀態檔一輪一個目錄、不覆寫,帶 `repair_context`(base_sha、票面快照、worktree、patch 路徑與雜湊、
   輪數、上一輪 EVIDENCE、重現指令與 cwd/env);gate / merge / push 分開記;每條退出路徑寫終態(含「沒有測試可跑」);
   `ticket.py set` 帶 `--expect-attempt` / `--expect-state-version` 拒收過期回報,寫入走同一把鎖;
   三輪耗盡由 `ticket.py round` 轉 Blocked 並指派主線。
4. **`verify-case.py`**:同一份案例在乾淨主線該紅、candidate 該綠,證據寫進票的 `verify.baseline`;
   **import 失敗不算紅**要明列;`extract` 只抽驗證檔;新 tag 一票一個片段 `verify/TAGS.d/<n>.md` 由工具合併。
5. **歸責規則改寫**:拿掉「紅在票沒動到的檔 → 疑似他票」(`failures.file` 取 traceback 最後一個檔,
   經常是既有測試或共用 helper),改用同條件的 baseline / candidate 對照;**未歸因前由原票 owner 持有**。
6. **`test_defect` 交接類型**:案例的 oracle 或 fixture 錯了 → 派獨立驗證者修案例,**產品 worker 不改 oracle**。
7. 能力表以真實入口逐項重寫(以前漏列票 tags 串接、review 檢查、baseline 驗紅、套 patch 與關票、land 互斥鎖)。

## D-015(2026-09-21)規格已定的那七件事全部做成程式:套 patch、自動派 worker、終態喚醒
D-014 收掉了外部審查的三個風險(硬閘門、取消 flake 自動判綠、交接閉環),而它自己列的
「未處置清單」有七項是**規格已定、未實作**。一份寫著規格卻沒有程式的流程,與沒有規格
的流程在明天早上長得一樣 —— 差別只在誰記得。所以這一輪把那七項做成入口與測試:

1. **`scripts/apply.sh <票號> <patch> [<patch-verify>]`** —— 套 patch → 開 `t<票號>` 分支與
   worktree → commit(訊息帶票號與 patch 的 **sha256**)。`git apply` 之前擋檔頭
   (只准 `base/…` / `work/…` / `/dev/null`;絕對路徑拒;`diff -ruN` 的刪檔沒把 `+++` 改成
   `/dev/null` 也拒 —— 那會讓 `git apply` **清空**而不是刪掉),之後逐一比對 `+++` 目標並
   `--reverse --check`(rc=4),再檢查 `allowed_write_paths`(rc=5)。
   `apply.sh rebase` 用 GNU `patch` 吃 fuzz 套到**當前主線**的副本、跑可設定的清單重生 hook
   (`apply.regen_cmd`)、出一份乾淨 diff,**`.rej`≠0 一律失敗**。
2. **`scripts/auto-fix.sh <票號>`** —— 讀最新狀態檔,紅就用 `worker.command`(預設
   `claude -p --model opus`)派**新的** worker,收 `patch-round<r>.diff` + `EVIDENCE-round<r>.md`,
   走 `apply.sh` → `gate.sh --branch --ticket`,三輪上限。`gate.sh --auto-fix` /
   `land.sh --auto-fix` 掛在後面。**三種停下來**:worker 在 EVIDENCE 寫 `OBJECTION:`(記成票的
   `objections[]`、轉 Blocked)、三輪耗盡、**failures 沒有歸因**(rc 非零卻解析不出紅榜 ——
   那一種最像「沒紅」,而派下去的 worker 會拿著空紅榜去猜)。**覆核不自動**:綠了停在 `InReview`。
3. **終態叫醒主線** —— `scripts/inbox.py post|list|show|ack` + `reports/inbox/<票號>-<run_id>.md`
   一頁四句(哪張票、什麼狀態、要主線做什麼、去哪看)+ `inbox.posted` 事件;
   `new-session.sh` 開場印。**主線不輪詢** status:每看一次背景工作就是整份上下文重送一輪。
4. **flake 達門檻自動開修復票** —— 改掉 D-014 的「只發 `decision.asked`」。理由反過來了:
   事件沒有 owner、沒有驗收,而**一則沒人認領的事件比一張沒人認領的票更容易被滑過去**。
   誤判那一半用「同一條案例只開一張」(票上的 `flaky_case`)擋;`flaky_auto_ticket: false` 可關。
5. **land 前檢查票的 `verify.files` 都在分支上**(#587 那把尺),缺了 **rc=4** —— 與其他拒收的 2
   分開:呼叫者要分得出「票面沒填好」與「分支沒準備好」。
6. **`scripts/rules.py pack <角色>`** —— 從共用規矩抽該角色要的幾節 + 角色卡 + 那個模型的記憶,
   壓進 4 KB,砍掉的部分**指名砍了哪一份**。派工範本改成引用它,不整份貼。
7. **同一輪的回歸只跑一次** —— `scripts/verify.py` 在 `AC_TICKET` + `AC_RUN_ID` 下把輸出與 rc
   存成 `reports/t<n>/<run_id>/verify-<雜湊>.log`,雜湊含標籤與 **HEAD sha**(sha 變了一定失效);
   `--no-cache` 關,沒有 `AC_RUN_ID` 就完全不快取。

另外:遷移計畫寫成可執行步驟(`docs/TODO.md` §0),`scripts/sync-to-project.sh` 把控制腳本同步到
專案的 `scripts/control/`、刪已退場的檔、並印出**專案端要改的接點**;`event.repo_root()` 改成往上找
`board/config.json`(不然放在 `scripts/control/` 的那幾支會把票與 reports 寫進 `<專案>/scripts/`,
**而且不會報錯**)。

**留著沒做的一項,連同理由**:`land.sh` 那一側在**一批多張票**時的歸責。一批裡哪一條紅對到哪一張票,
要有票↔案例的對照才判得出來,而**猜錯的歸責比不歸責更貴** —— 它會讓一個新 worker 去修一張沒有壞的票。
所以 `land --auto-fix` 只在剛好一張票時派下一輪,多張就印出來留給主線。

## D-016(2026-09-22)不追求快,只看效率

不追求快,只看效率:任何角色在因為「這樣比較快」而做事之前,先算 token 是多還是少(誰的上下文在讀、重送幾次、失敗重來幾輪);快不快不是判準。規則包每一份都帶這一行(#16)

來源:產品負責人 2026-09-22 原話;主線同日越界清單全是圖快。
