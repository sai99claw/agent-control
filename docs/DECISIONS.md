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
落地前把 patch 三向合併到**當前主線的副本**(祖先 = 票的 base_sha,對方 = base_sha + patch,`git apply` 不吃 fuzz;#17 改,原本是 GNU `patch` 吃 fuzz)、重生清單、出一份乾淨 diff;
**衝突 rc 3 並指名檔與行號、空 diff rc 4 一律當失敗**(#17;原本是 `.rej`≠0);`diff -ruN` 刪檔的 `+++` 側要手改成 `/dev/null`(不然 `git apply` 只清空不刪);
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
   `apply.sh rebase` 三向合併到**當前主線**的副本(#17 起;原本 GNU `patch` 吃 fuzz)、跑可設定的清單重生 hook
   (`apply.regen_cmd`)、出一份乾淨 diff,**衝突 rc 3、空 diff rc 4 一律失敗**(#17)。
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

## D-017(2026-09-22)「合理、高效、好用」的門檻、看板是必要品、Codex 停用改 opus

使用者原話:「我們邊做邊往這樣前進(三張票連續零機械格返工、環境紅由守衛在起跑前擋掉、每張票有 token 與返工輪數的數字、另一個小專案用同一套跑通)。儀表板監控在 agent control 也是必要的喔,而且不是用抄送的,模型輸出的東西就該是儀表網頁可以讀的格式。Codex 先不買,改用 opus 做。」
① 四個門檻是這個 repo 的驗收目標,主線每次回報都對著它們說進度:機械格零返工(連續三票)、環境紅起跑前擋、每票 token 與返工輪數有數字、第二個專案跑通。
② 看板是 agent-control 的一部分,不是專案端的加值:worker / verifier / opener 的輸出(EVIDENCE、RESULT、反駁、輪數、token)一產生就是看板讀得懂的結構化檔,看板直接讀,不抄、不轉寫。
③ 機械票的工人從 codex-sol 換成 opus(週額用完,不買);ac-batch.sh 那條 codex 路保留但不是預設。

## D-018(2026-09-23)設計為未來的 token 打算,不為眼前的複雜度繞路

設計取捨的判準是「這個決定之後每一票多讀多少、多重來幾輪」,不是「現在改起來多難」。眼前複雜度太高,可以裁「先開票、之後再做」,但票要把問題完整寫下(症狀、為什麼現在不做、做的時候會對到哪些檔);**不准把問題繞過去**(改個名、加第二個欄位、兩邊各做各的形狀)。設計文件裡每個取捨附一行「未來每票省/多花多少」(粗估即可,標推的)。

來源:產品負責人 2026-09-23 原話,起因是 `environment_suspect` 在兩個 repo 長成同名不同形(A #7 是 dict/None、tabby #644 是 list),而多階層記憶裡沒有一條擋這件事 —— D-016 管的是執行的動作(不圖快),這一條管的是設計的形狀。

## D-019(2026-09-23)`environment_suspect` 只有一種形狀:list,每筆標來源

設計文件:`docs/DESIGN-ENV-SUSPECT.md`(Fable 設計 session 裁,依 D-018 每個取捨附未來每票省/多花)。結論:`status.json` 的 `environment_suspect` 永遠是 list,空值只有 `[]`;每筆七鍵 `source(statistical|declared) / engine / why / count / threshold / log / line`,缺料 null;`state`/`rc` 不因這格而變;**這格非空 → gate 不自動派 auto-fix**(每次環境紅省一輪 worker)。實作票 #23,tabby 端由同步票帶回;讀端正規化舊檔,不改寫 reports/。

## D-020(2026-09-23)驗證者只證紅,綠由閘門量

設計文件:`docs/DESIGN-VERIFY-CASES.md`(Fable 設計 session,依 D-018 每個取捨附未來每票省/多花;全篇結論皆為讀 code 推的,沒有執行任何腳本、沒有動任何 repo)。起因:驗證者每張票自己搭一套拋棄式參考實作來證明「案例做得到綠」,每張多燒 5 萬到十幾萬 token(#23 sonnet 340K 自報、#647 約 260K、#642 約 294K)。結論(C1–C7,細節見該文件):
- **C1** 根因排序:主線派工文(三份都明著或等於明著要求「有實作時綠」)> 沒把閘門接上 `verify-case.py check` > 沒範本 > 格式不明確;使用者原先假設的「沒範本、格式不明」只占三分之一的帳。
- **C2** 驗證者只證「乾淨基底紅、而且紅在自己的斷言」(`verify-case.py red`,新子指令),寫進票的 `verify.baseline{stage:"red"}`;**不搭參考實作、不做產品變異、不證綠**。綠由閘門在實作者 patch 進來時用既有的 `verify-case.py check` 量(#22 做好的,現在沒人自動叫它)。
- **C3** 驗證者的變異驗紅整條拿掉;oracle 不被騙的證據改成三件機器量的事(基底紅在案例檔自己的 `AssertionError`、candidate 上綠、兩邊案例數相同),`red_lines` 給主線覆核時看。實作者對自己單元測試的變異照舊(D-009 那一半不動)。
- **C4** 一份範本 `verify/_template_ticket.py`(A 正本、同步到 T)+ 機器 lint(F1–F6,`verify-case.py lint`,閘門叫)。
- **C5** 閘門接線:`gate.sh --ticket`(A)/ `land-ticket.sh gate`(T)在回歸層之後多兩步 `lint` → `check`;`check` 把 `stage` 從 `red` 升成 `check`;`ticket.py close` 只認 `stage=="check"`(或 `verify_waiver`)。
- **C6** A 放:範本、lint、`red` 子指令、gate 接線、角色卡、`templates/dispatch-verifier.md`、VERIFICATION/SCHEMA 措辭。T 自備:`land-ticket.sh` 的兩行呼叫、`board/config.json` 禁字清單、fixture、`docs/DISPATCH-COMMON-RULES.md` §133(過期,還在說 VERDICT)。
- **C7** 四張票,順序:#25 文字(sonnet)→ #26 工具(opus)→ #27 閘門(opus)→ T#650 同步+接線(sonnet);#26 依賴 #25,#27 依賴 #26,T#650 依賴 #25–#27 全落地。

本票(#25)只動文字與一份 ≤60 行範本檔,`verify-case.py` 的 `red`/`lint`/`stage` 實作在 #26,`gate.sh` 接線在 #27。

## D-021(2026-09-23)專案端記憶備忘的唯一去處:專案自己的 `memory/`,規則包疊兩層

設計文件 `docs/DESIGN-MEMORY-INBOX.md`(Fable 設計 session)。結論:`memory.py note` 寫的 `<repo>/memory/{role,model,project}/` 就是唯一去處,寫入端不改;`roles_dir == memory/role` 的 repo 是正本,否則是專案,`rules.py pack` 疊兩層(正本角色卡 + 專案 memory 主檔與 inbox 尾巴,上限仍 4 KB);`sync-to-project.sh` 不再複製 `*.inbox.md`、永不碰專案 `memory/`(測試釘死);專案 config 的 `memory.applies_to` 指 `memory/`。實測發現:`rules.py pack` 整支沒有 inbox 這個字,inbox 備忘在 A 也要等整理才進規則包。否決甲(專案事實污染所有專案,A inbox 已混入 1 行 T 專屬)與乙(與同步產出物同名同目錄)。A 正本改動另開 opus 票;tabby #648 只做 config 與 `git add memory/`。

## D-022(2026-09-23)覆核由短命 opus 做,看的是「做出來的是不是票要的」

閘門綠、票停 InReview 之後,**主線不自己讀 patch 覆核**;派一個短命的 opus 覆核者(reviewer):讀票面 objective/acceptance、讀分支上的 code(不只 diff)、讀 EVIDENCE,逐條把驗收對到實作的行與守它的案例,查範圍有沒有擴、反駁有沒有處置;不重跑測試、不改檔。交付固定格式:`verdict(pass|fail)`、逐條驗收 → code 位置、疑慮清單;fail 寫成 objection。主線只把 verdict 寫進票(`ticket.py set <n> review …`)並決定落地順序。

來源:產品負責人 2026-09-23 原話(「不要用主線去覆核,新的 opus 做就可以;要看 code 理解實際做出來的功能是不是票上定義所需要的」)。理由:主線上下文最貴(D-016),而覆核是讀 patch 對票面的工作;今天七張票主線自己覆核,每張都把 patch 讀進主線。待補:reviewer 角色卡與 `rules.py pack reviewer`(併 #29 的 G1 一類)、派工範本。

## D-023(2026-09-26)開題者預設 Fable,簡單題允許 Opus,不准 Sonnet 開題

`routing.open` 維持 `fable`。主線判「簡單題」(症狀清楚、單一檔、不需要設計裁示、驗收一兩條)時可改派 Opus 開題;**Sonnet 不開題**,任何角色都不得把開題派給 Sonnet。理由:票面的品質決定下游三個角色要不要重做(docs/ROLES.md),省在開題的 token 會在實作、驗證、閘門各賠一次。來源:產品負責人 2026-09-26 原話。

## D-024(2026-09-26)先把開發與制度的事完全做完,再回產品票

順序:agent-control 工具與制度票(9/23 20:0x 票單、tabby #618/#621/#622)優先於 tabby 產品票;產品端的「待開題」批(#601–#614、#638)與零星小修等制度收尾後再排。來源:產品負責人 2026-09-26 原話。

## D-025(2026-09-26)主線接觸點收斂四題:C3 准、C2 准、C5 准、C6 緩

依 `docs/DESIGN-MAIN-TOUCHPOINTS.md` 裁示題 1–4,產品負責人原話「要產品負責人裁的題 都照你的建議」:
1. **准**:單票 `land.sh` push 成功後對每張票試 `ticket.py close --landed`;`done_blockers()` 四條件與弱檢查一條不放,關不掉照舊印「已合併、尚未關票」+ inbox 頁,不加 `--force`。
2. **准**:reviewer(短命 opus,`review.sh` 派)的 `pass` 由腳本直接 `set review`(綁 sha / state_version,land 照查);`fail` 寫 objection、票轉 Blocked 回主線。主線對票的補充一律寫在票面(D-023)。
3. **准**:開題者可呼叫 `sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json` 提交**自己那張**票檔;角色卡「不 git 寫入」只加這一句例外,裸 commit 禁令不動。
4. **緩**:C6 驗證者自動進第 1 輪之前,等 C1 跑過幾張再開。

## D-026(2026-09-26)session 契約三題:hook 進 repo、compact 重印不發事件、T 主線讀 A 契約

依 A #39 / T #652 裁示題甲乙丙,同一句原話:
- **甲 准**:SessionStart hook 放 repo 內 `.claude/settings.json`(換人也生效;代價是第一次互動 trust 一次)。
- **乙 准**:`source` 為 compact / resume 時重印開場那一頁但不發 `session.start`(事件是「一個 session 開始了」;重印是為了壓縮後的新上下文還看得到收件匣)。
- **丙 接受**:T 主線每次開場多讀 A 的 `CLAUDE.md`(~2.5 KB);不接受就得把「不可違反的」抄進 T,兩份會分岔。

## D-027(2026-09-26)模型卡不按版本拆;一張卡對一個路由標籤

`memory/model/opus.md` 不拆成 opus-5.4 / opus-5.5,`fable.md` 亦同。理由:卡裡記的是流程的坑不是版本的坑;`worker.command` 的 `--model opus` 是浮動別名,卡早已跨版本。只在兩個版本**同時**進 `routing` 時才拆(卡名跟路由標籤走)。被 5.5 證明不成立的那一條就刪,不預先分家。來源:產品負責人 2026-09-26(先口頭「算了先不改」,後「都照你的建議」)。

## D-028(2026-09-26)驗證者不一律必備,依票的複雜度;開題者在票面明寫 `needs_verifier` 與理由

產品負責人原話「不需要一律要驗證者,依據票的複雜度而定,這件事票裡要寫」。規則:開題者每張票明寫 `needs_verifier`(true / false)加一句理由(複雜度、有沒有動到產品碼、驗收是否機械);漏寫 = 票不完整。動到產品碼(`demo/**` 等)的票預設 true,land 的 #8 規則(`verify.files` 空 → rc=4)不變;A 端工具票由開題者判。主線的 `verify_waiver` 只用於票面漏寫時的補救(9/26 #34–#38 即此類),不是常態。

## D-029(2026-09-26)新角色「回報接收者」,暫名 `tabbypool_report`:主線不收中途回報

產品負責人原話「多加一個角色卡專門接收狀態回覆,目前請用:tabbypool_report,主要是要避免主線 token 浪費」。這個角色替主線接 worker / 驗證者 / 覆核者 / 閘門的所有中途回報,把機械下一步(apply → gate → InReview → 派覆核 → set review)跑完,只把**終態**(等落地 / Blocked / 需裁示)一行交給主線;不 land、不 close、不裁示、不對使用者說話(順序仍是主線的,D-010)。暫行卡先放 tabby 專案層 `memory/role/tabbypool_report.md`(D-021 疊層);正式形狀(`rules.py WANTED`、派工範本、與 DESIGN-MAIN-TOUCHPOINTS C1/C2 腳本化的分工)開設計票決定。與 D-G122 退場的「調度員」的差別:不決定順序、不裁示,只是主線的回報緩衝。
