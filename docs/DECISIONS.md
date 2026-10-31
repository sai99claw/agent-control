# 裁示

一列一條;來源要有原話;推翻舊的要指名。

| 編號 | 裁示 | 來源 | 日期 | 狀態 |
|---|---|---|---|---|
| D-001 | 規則住在程式裡,不住在提示裡:票欄位、落地拒絕條件、寫入範圍、租約由 `scripts/` 與 `board/` 執行 | 產品負責人提案 v1.1 §4「不能只靠 agent 在對話中記得規則」;tabby_pool 2026-09-10 的事故清單 | 2026-09-12 | ✅ |
| D-002 | 排程與落地分開:排程器只提案,落地是純機械流程 | 主線對調度員實驗的評估(一天一百萬 token、四十則回報、撞額度靜停兩天) | 2026-09-12 | ⛔ 2026-09-21 由 **D-010** 取代(整個排程器 / 調度員角色拿掉,順序回主線) |
| D-003 | agent 的每個動作是控制台上的一筆事件,控制台只讀事件不猜 | 產品負責人 2026-09-12「agent 的行為要具體反應到控制台上」 | 2026-09-12 | ✅ |
| D-004 | code wiki 第一版只做「查找失敗過的模組卡片 + 過期偵測」,不預先鋪滿 | `docs/CODE-MAP.md`;成本未證明前不擴 | 2026-09-12 | ✅ |
| D-005 | 模型私有記憶:一模型一檔、同模型跨 session 共享、超過可調門檻由高階模型整理 | 產品負責人提案 §13(原始要求) | 2026-09-12 | ✅ |
| D-006 | **模型私有記憶**(`memory/model/<model>.md`,每個同模型 session 開頭必讀)預設上限 2K 字元;專案共用層不受此限;超過就觸發與高階模型的討論(老師帶學生),討論結果可以壓縮也可以**打破上限**,提高的理由寫進檔案 front matter | 產品負責人 2026-09-12(原話說「專案私有記憶」,隨即更正為**模型私有記憶**):「原始規範 2K…超過會觸發機制跟高級 session 討論要如何壓縮記憶,2K 的限制可以被討論結果打破,這個過程就像是老師帶學生成長」 | 2026-09-12 | ⛔ 2026-10-18 被 D-053 ⑥推翻(不設固定字數上限) |
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
   ⛔ 「覆核」這半句由 **D-022**(覆核改派短命 opus 覆核者)/ **D-025 ②**(pass 由腳本直接 `set review`)取代;自動派新 worker 那半不變。
   **規格已定、腳本未實作**(來源專案 #616 實作中)。
4. **驗證者只寫案例**:寫 → 乾淨主線上紅、patch 上綠 → 登記 tag → 把怎麼跑寫進票的 `verify` 欄(`files`/`tags`/`run`/`notes`)→ 交 `patch-verify.diff` → 結束。
   **不判 PASS/FAIL、無 VERDICT、不讀 EVIDENCE、不重跑閘門、不輪詢**;對錯由 gate 跑票的 `tags` 判。D-009 裡的 `VERDICT.md` 退場。
   主線的「覆核 = 讀 patch 記 `review`」**不是**驗證者的工作,留著。
   ⛔ 「主線覆核」這一句由 **D-022** / **D-025 ②** 取代(覆核不是驗證者的工作這點不變);驗證者只寫案例那半不變。
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

## D-030(2026-09-26)流程控制的「做完」定義與尾巴凍結

產品負責人問「流程控制的東西為什麼要做那麼久」後裁示(原話「Ok」對主線提案):**做完 = A 與 T 兩邊,主線每票只剩「派開題者 → 收一行終態 → 排落地」**。達到這句要的只有 #42(review.sh)、#43(land 試 close + inbox ack)、T#652(T 契約 + hook)、T#653(T 端接線)四張。落完即回產品票(D-024 的「完全做完」以此為準)。
**尾巴凍結**:#44(文件契約對齊)、#45(守衛說錯話)、#46(副本管理三題)進待辦,哪一條真的咬到人再開;覆核者之後列的「不阻擋、另開票」一律進待辦不自動開票。理由:每張票落地會生 3–5 條新票(#44–#47 就是 9/26 一天長出來的),不砍會無限長;文字規則幾分鐘,腳本規則每張 1.5–2.5 小時、8–33 萬 token。

## D-031(2026-09-26)開題者交票前必做票面格式自檢
使用者裁示:「票面格式退回好幾次很沒效率,票面格式檢查加到開票者記憶中」。#651/#652 同一個開題者把 `verify_strings` 寫成 {path,contains}、漏 `needs_verifier`、漏 `tags`,兩張票各退三輪閘門/落地。規則寫進 `memory/role/opener.md`:四格(needs_verifier 布林、verify_strings list[str] 內容字串、tags 登記過、指令先 --help)交票前跑一行 python 自檢,沒過不准 `set Ready`。後續若要做成 `ticket.py lint`,另開票。

## D-032(2026-09-26)接收者退場:票 JSON 就是儀表板,主線只收兩種頁
使用者裁示:「Worker 正常在跑的話訊息不該送到主線;接收者不該是 session,應該是儀表板上的一個欄位或格式;只有需要 user 決定方向、或整票完成時回報主線(這票在幹嘛 + 完成簡報)。」實測依據:2026-09-26 三張 T 產品票,接收者一個花 25 萬 token 只在轉述票面。
形狀:(1) 票 JSON 是儀表板一列,派工/閘門/覆核/落地腳本直接寫進去,加 `cost[]`(每輪 token 與時鐘,腳本寫);(2) inbox 只剩兩種頁:`decision`(ticket-wrong / blocking 反駁、三輪紅、覆核 fail、閘門不可歸因)與 `done`(落地 sha、做了什麼、覆核結論、成本);其餘事件只進 events.jsonl 與 EVIDENCE;(3) A 端先做(C6 驗證者自動派、done 頁格式、cost 欄),T 端接線跟著(AUTOFIX、review.sh 收 AwaitingReview、dispatch-reviewer 範本進 sync)。tabbypool_report 卡在 T 接線完成後退場。

## D-033(2026-09-26)開題者開票前在既有測試與資料上實測票面假設
三張產品票第 1 輪全 ticket-wrong:#605 既有快照測試把舊行為釘死且 docstring 明寫;#614 票面假設的「零帳本帳號」註冊流程根本造不出來;#520 投影多兩鍵讓 545 份黃金檔跟著動,寫入範圍沒列。共同點都不是實作問題,是票面假設沒驗。規則進 memory/role/opener.md:開票前 grep 既有測試對這個行為的斷言、跑一次票面要的前置狀態能不能造出來、改動會牽動哪些黃金檔;驗過的寫進 outline,驗不了的標「未實測」。

## D-H38(2026-09-28)寫入範圍白名單只是預期、黑名單才擋;驗證者依 `interface_fixed` 晚起
來源專案 tabby_pool 的 D-H38(流程浪費稽核 R1–R9 的裁示),編號沿用 —— A 的角色卡、`tickets/SCHEMA.md`、`docs/DISPATCH-TEMPLATE.md` 與程式註解都引用這個號。使用者原話:「我覺得現在有很多不必要的來回」「之後開票不要去限制他一定只能改什麼檔案」。起因:T 六張票(#658–#663)16 條反駁裡 5 條是「舊測試釘舊行為但不在寫入範圍」、5 條是驗證者案例照票面逐字寫撞產品慣例。
- ① **寫入範圍**:`allowed_write_paths` 是預期會動的檔(排順序的人判平行、覆核者對照),越出不擋,`apply.sh` 列進 result 的 `extra_paths`、覆核派工文多一段「白名單外改動」;硬擋只剩票的 `out_of_scope` 與 `board/config.json` 的 `protected_paths`。取代 D-015 1 的「再檢查 `allowed_write_paths`(rc=5)」—— rc=5 現在只給這兩格。A #59。
- ② **驗證者預設不與實作者平行**:票 `interface_fixed=true`(純後端、介面已在票面釘死)才第 1 輪並派;否則等 worker 交出第 1 輪 patch 才起,介面以分支上的實作為準,票面與實作衝突時照實作並在 EVIDENCE 記一行。改掉 #51(D-032 (3) 的 C6)的一律平行;理由是時鐘換 token。A #60。
- ⑥ **開題規則**:寫入範圍先 grep 引用;驗收寫使用者看到的結果,DOM / 樣式 / 幣別「照產品既有慣例」;「其餘斷言不變」查過 oracle 才准寫。A #61。
- 同一裁示的其餘幾條在 A 落成 #56(閘門一個真相)、#57(腳本鏈狀態寫回)、#58(cost 信封);#56–#61 皆已 Done。T 的鎖屏紅(⑦)與 T 同步票不屬 A。

## D-034(2026-09-30)tests/ 稽核四批:批 1–3 照建議做,批 4 hold
使用者原話:「agent-control 測試稽核那四批,都按照你的建議做。」
- Fable 稽核 33 檔/877 條:約八成有意義。批 1+2 退役/替換 + gate.sh map() -f 守衛(#71)、批 3-A(#72,dep 71)、批 3-B(#73,dep 70);批 4 不動。#70 先修測試衛生(test_land 真叫 claude、test_verify_runner 寫真 verify/)。
- 同日一次性授權:移開 main 未追蹤檔讓 #68 落地;#69 落地被記憶體不足砍掉後授權重新覆核(A 與 T 全套不要同時跑)。
- 全套統計:落地 139 次、全套紅 9 次、其中 4 次抓到真 bug;每次 11–14 分鐘。落地是否改成只跑相關模組+煙霧、全套定期跑,未裁。

## D-035(2026-09-30)記憶規則必須由腳本與測試落實
使用者原話:「這個問題很嚴重,讓astra 去開票,把所有該修復的地方寫清楚,我希望這次改完之後這些規矩可以真正被落實。」
- 必要規則未送達即停止派工;超標與過期條目進入有指派、有證據的整理流程。方案與驗收見 #74–#79(規則包不截空、note 檔名、整理閉環、repo-map、A 記憶整理、分層 lint);本條不表示已實作。

## D-036(2026-09-30)角色與模型記憶只留通用原則
使用者原話:「直接跟實作相關的內容只能寫在專案記憶,不該出現在模型記憶或角色記憶,他們只會有原則性的通用描述,是學習後檢討的地方。」
- 角色與模型層不得保存專案實作配方;來源票號與通用反思可保留。專案層粒度依 D-037。lint 見 #79。

## D-037(2026-09-30)專案記憶保留設計理由,不保存易過期實作指示
使用者原話:「專案記憶也不該寫太實作的東西,裡面該寫的是那種東西的設計邏輯跟原則,目的是讓整個框架可以長期有一致性的被維護,還有避免出現錯誤的過期指示。」
- 精確命令、符號定位、行號與版本事實移至可查證的 reference/實作索引;不調高既有記憶或規則包上限。

## D-038(2026-09-30)建立實作速查入口
使用者原話:「實作相關的東西應該有個架構可以讓 agent 速查到需要的東西而不是每次都 grep。」
- 實作提案(Astra):延伸既有 code-map,可重生的 repo map(aider repo map 形式)連結架構、裁示與 reference,閘門驗索引新鮮度(#77)。

## D-039(2026-10-03)主線與開題者的建議模型改為 opus 或以上
使用者原話:「agent-control 的開題者 主線 建議模型 改成opus 或以上」
- 起因:board/config.json 的 routing.main / routing.open 是 fable,但 10/03 主線 session 跑在 opus、派的開題者也全是 opus,結果可用;使用者裁示下限放寬為 opus。
- 落實:routing.main、routing.open 改 opus(fable 仍可用,屬「以上」);board/config.json 受保護,走票(排在 #85 之後,兩張同改 config)。design/bug 未裁,維持 fable。

## D-040(2026-10-03)A 待改項併成一張 #86;#76 先單獨落地;#78 凍結
使用者原話:「Agent control 所有相關的東西都停掉,先把所有要改的 用fable 重新開票整理在一起」「我希望新的 A票 只有一張, worker 改完 , 一次測」;裁示題答「先單獨落地(建議)」「凍結,#86 完再做(建議)」
- 落實:#77 #79 #80 #82 #83 #84 作廢併入 #86(41 條驗收,對照表 docs/review/20261003-a-reticket.md);#76 已實作且覆核 pass,重跑 review.sh 後單獨 land;#78(記憶正文,兩模型)凍結到 #86 Done。
- 未裁:#86 worker.timeout_seconds 10800(票面建議值)。

## D-041(2026-10-04)worker 反駁不再停下等主線:新開題者判、接回驗證者與實作者續做
使用者原話:「這邊之後改成 發動新開題者 session 判斷是否合理 合理的話 接回驗證者 改測試 接回實作者 繼續完成。實作者正常的完成後, 實作者 驗證者 一起關掉。用這樣避免接入主線」
- 起因:T #695 第 1 輪 worker 提 ticket-wrong 反駁(驗收量法錯),auto-fix 轉 Blocked、收件匣要主線裁;主線派開題者改票後 auto-fix 從第 1 輪重來,丟掉其餘可用的 patch。
- 落實:auto-fix 收到反駁 → 自動起一個新的開題者 session 判斷;合理 → 開題者改票面、處置反駁,接回同一個驗證者 session 改案例,再接回同一個實作者 session 從原副本續做;實作者正常交件後,實作者與驗證者 session 一起收掉。全程不進主線收件匣。
- 未裁(主線預設,待使用者改):開題者判不合理 → 以 rejected 處置並附理由接回實作者照原票做;同一張票第二次反駁、或開題者判斷需要使用者取捨 → 才進主線收件匣。
- 走票:併入 A #87(auto-fix 流程)與 #88(開題者/實作者/驗證者角色卡)。

## D-042(2026-10-04)#88 卡在舊角色卡:先整理卡片再做 #88,順序 #90 → #78 → #88
使用者裁示(#88 worker 反駁經 #89 自動判反駁升級):答「先整理卡片再做 #88(建議)」。
- 起因:gate 對改到的角色卡整檔 lint,opener/implementer/verifier 三張卡舊正文已有 60 條命中(#88 新增 0 條);implementer.md 2700/2700 到頂,#88 P14 一句即超標。
- 落實:#90 加「閘門對記憶卡只 lint 分支新增行」;#78 解凍(兩模型整理三張卡正文、騰出字數,D-007/D-013),depends_on #90;#88 depends_on #78,其反駁 disposition=accepted。

## D-043(2026-10-04)#78 角色卡整理由 fable 單獨進行
使用者原話:「照建議 這題用fable 處理」(主線建議:D-013 原話允許「或一個高階模型」,fable 為最高階;避開 codex:gpt-6-astra 額度上限風險與兩模型手動協調)。
- 落實:#78 改為 fable 單一模型整理三張角色卡;走一般 worker 管線(auto-fix / apply / gate / review / land)為優先,不行再由主線手派。

## D-044(2026-10-06)worker 交件前唯讀比對主線
使用者原話(在 tabby_pool 說):「每個worker 再做的時候都是 獨立資料夾的對吧？  Worker 要收工提交之前 加prompt 去檢查一下主線」
- 起因:T #711 在獨立副本做完、閘門綠、覆核 pass 之後,#700 先落地(兩票改 docs/RUNBOOK.md 相鄰行),#711 land 時累積 patch 套不上,另派人手對齊。
- 落實:#93 —— auto-fix 產的 worker 派工文加「交件前看主線」(唯讀 rev-parse / diff --stat base..main);碰到的檔被主線動過就在副本內三向對齊、重跑票測試,result 記 `aligned_main`,收件時換 base_sha。
- 限制(寫進 DISPATCH-TEMPLATE §1 理由):只擋「worker 工作期間主線動了」;交件後主線才動(#711 正是這種)要靠 T #712(gate --redo 用 git apply --3way)。

## D-045(2026-10-06)每次執行記一列成本;關票加總
使用者原話(在 tabby_pool 說):「如果以前的記錄查不出來就算了，我希望接下來有相關的紀錄， 角色：票：model:time:token， 我能想到的要涵蓋這些資訊你看這樣子夠不夠，這件事情應該會是一張a票吧」
- 主線補充(使用者五項之外):第幾輪、token 拆四種(input / output / cache_write / cache_read —— cache_read 是大宗)、outcome(算白花的)、關票加總、主線與 Agent 工具派的子 agent 也要有紀錄。不補歷史。
- 欄位形狀定案(#94):cost[] 每列 14 鍵 role, round, model, tokens_in, tokens_out, cache_write, cache_read, wall_seconds, outcome, run_id, session_id, by, note, at;拿不到填 null 不填 0;outcome 只收登記表的字。關票寫 `cost_total` 並帶進 `ticket.closed`。
- #95(依賴 #94):SubagentStop / SessionEnd hook 讀 transcript 寫 `agent.cost` 事件(同鍵 + ticket、agent_id),歸票靠猜所以不進 cost_total;互動 transcript 的 output 多半只有串流開頭值,記 null。

## D-046(2026-10-06)成本審查後的排票:開浪費票、凍結省不到 token 的、#93 延後
使用者在 tabby_pool 的裁示(AskUserQuestion 原選項):「全開，依浪費量排(建議)」「延後，#712 落地後觀察一週(建議)」「凍結(建議)」「記成裁示(建議)」。全文與排票三原則見 tabby_pool D-G144。
- A 側:#96(dry-run 不清 round 副本)、#97(needs_verifier 票的寫入範圍不得含驗證者案例檔)、#98(覆核綁 patch 內容而非分支 sha)。
- #93 凍結至 2026-10-13 後,看 T #712 落地以來的 redo 次數再決定。
- 開題者交票前自查 objective / acceptance / test_plan 三處一致(近期 10 條反駁 4 條是票面自相矛盾)→ memory/role/opener.inbox.md。
- (補 2026-10-06)使用者問:「重新產生程式碼索引 可以改到gate 裡面去coding做嗎？」主線答放在套用 patch 那一步、不放 gate(閘門只判交件、不改交件),提兩條路,使用者回「2 ok」→ 併進 #96:apply.sh 套上分支後自動 repo-map build,有變併進同一個 commit(trailer `regen: repo-map build`)。

## D-047(2026-10-14)票可指定推理強度 effort(#114)
- 起因:使用者原話「接下來改code 的session 優先交給codex sol high」「要讓推理強度可以指定，…claude 跟agy現在都行」。主線查證:三個工具本身都支援(claude --effort、codex -c model_reasoning_effort、agy 經 ai-hub 的 effort),派工腳本都沒帶。
- #114 落地(9bf6020):票加獨立欄位 `effort`(不用 model 後綴,D-018:model 格被多處當模型 id 讀);claude-code → `--effort`,codex → `-c model_reasoning_effort="…"`;取值順序 票 > board/config.json `effort.implement` > 不帶(與今天逐字相同);不合法值 rc 2 不派。agy 本票只記不做。
- **推翻 D-045 / #94「cost[] 每列 14 鍵」**:改為 15 鍵,多 `effort`(未指定為 null);worker 的 agent.start/done/failed 事件也帶 effort。

## D-048(2026-10-14)claude worker 與 codex worker 同權限
- 原話:「claude worker 跟codex worker 有一樣的權限 用這個原則去做 agemt control 可能要寫進去」。
- 起因:使用者要改 code 的工作優先交 codex sol high(D-047);codex 自己的沙盒(-s workspace-write)擋 127.0.0.1 bind 與 Chrome 啟動(Seatbelt),跑不了伺服器 / 瀏覽器案例(T #461 起的老問題;2026-10-13 astra 調查確認,且在 codex 沙盒內無法驗證任何放寬設定)。
- 現況對照:claude worker 在副本資料夾(git archive 展開、無 .git)裡跑,沒有 OS 層沙盒,靠規則包禁區、禁 git 寫入、閘門與覆核把關。
- 原則:codex worker 比照 —— 同樣的副本資料夾、同樣的禁區規矩、不另加也不少一層;不再套 codex 自己的 workspace-write 沙盒。開票實作(A),T 端同步後生效。

## D-049(2026-10-17)角色模型表、閘門紅自動修補、DAG 排程;codex 改寫票的處置與整併
- 起因:主線停機三天期間,使用者用 codex 在 A 改了 auto-fix.sh 並把 #115 改寫成 provider-neutral runtime、另開 #116–#120(英文票面,無裁示依據;改寫版另存 reports/t115/proposal-20261016/)。主線派 opus 唯讀審查後向使用者提題。
- ① #115:原話(選項)「回到 D-048 原票(建議)」→ tickets/115.json 已還原為 v5(D-048:codex danger-full-access + 兩種 worker 共用事後越界檢查);t115 分支不落地。
- ② 角色模型表(每個角色一串有順序的模型,前一個額度用完 / 不可用才用下一個)。使用者原話:「新做法裡面有個jason 是紀錄每個角色的預設模型的，這我覺得不錯 每個角色的模型我們加個預設順序。主線:opus, worker: sol,opus, 複雜任務開題者：astra, fable, 一般認為開題者：opus, 驗證者：sol, opus.」補充原話:「記憶整理者 改成fable」「設計 / 想一下 astra, fable」「簡單工作worker, sonnet」。主線整理成表:
  - 主線 opus;worker sol → opus;簡單工作的 worker sonnet;複雜開題者 astra → fable;一般開題者 opus;驗證者 sol → opus;記憶整理者 fable(單一高階模型路,D-043);設計 / 想一下 astra → fable。
  - 主線提、待使用者確認:覆核者 opus(現行);驗證者優先選與該票 worker 不同的模型(worker 是 sol 時驗證者先 opus),避免同模型自驗;sol 預設 effort high;整串都用完 → 停下發 decision 頁,不硬跑;表只管被派出去的 agent,主線 session 的模型由開 session 時決定。
  - codex 額度用完改派(#87 A23 / #114 A10)由這張表的順序取代;#116 的「絕不 fallback 到 Opus」不採。
- ③ 閘門紅 / 落地全套紅的診斷與修補由 auto-fix 自動處理,不回主線。使用者原話:「閘門紅時的診斷、修補, 這理論上不需要送到主線判斷？ 應該是autofix 要解決的事情吧？」主線轉述:預設開自動修補;只有 worker 反駁、產品取捨、修 N 輪仍紅才發 decision 頁給主線。
- ④ DAG 排程:原話「DAG 排程 是我想要的， 我希望開題者 開完之後直接把票放到合適的執行順序上」。開題者一次交一批票附依賴,排程器依依賴與寫入範圍重疊決定先後與並行(在 queue.sh 的 depends_on / --batch 上長)。
- ⑤ #117(session 生命週期與交接)、#120(不用 LLM 的常駐 Controller,自動恢復):原話「117 120 的內容也要做」。
- ⑥ 票整併:原話「現在好像有很多票 請你把重疊 或相關的票做整合，這邊討論的東西都要寫到agent control 裡面去，不要只改claude本身的設定」。A 的開著的票(#93–#106 與 #116–#120 提案)整併成一套,票面一律中文(A 慣例);整併交複雜開題者(依 ② 為 astra → fable)。
- 另:codex 在 main 工作樹留下的 auto-fix.sh 兩處修正(權威票面快照、codex --add-dir)經審查正確、測試綠,併入 D-048 實作或整併後的對應票,不以未 commit 形式留在 main。
- D-049 補(2026-10-17,使用者確認待確認格):原話「覆核者維持opus， 驗證者照我的排序開 跟 worker 一樣模型沒關係 worker 的推理強度給你建議」。→ 覆核者 opus;驗證者 sol → opus,不強制與 worker 異模型。worker 推理強度(主線建議,使用者授權):sol 預設 high、票標「複雜」或修補第 2 輪仍紅時 xhigh;opus 接手時 high;sonnet(簡單工作)medium;閘門紅修補輪沿用原 worker 的模型與強度。整串用完 → 停、發 decision 頁(主線提,使用者未反對)。
- D-049 補:原話「astra 已寫完20張草稿 不能一次給他這麼多工作，他會一下就用光光token」。→ 派給 gpt-6-astra 的工作要切小批(一次一小塊、可在一輪內交件);角色模型表要能表達每次派工的份量上限。

## D-050(2026-10-17)A 票整併的四題(Fable 接手 astra 的整併計畫 N1–N20)
- 計畫:reports/t115/proposal-20261016/ 之外,整併草稿在主線 scratchpad astra-consolidate/(PLAN.md、drafts/N1–N20.json);20 張、合計約 1.2M token、全部中文;D-049 ①–⑥ 全有票承接。
- ① 寫入範圍:原話「這是在說 票上面要定義有哪些檔案可以被改動嗎？ 我覺得在開票的階段是沒辦法完整定義的，如果要寫的話就是寫一個建議的改動範圍，他應該不會是一個對工作者的硬限制」→ allowed_write_paths / out_of_scope 都是建議範圍,不硬擋 worker;改到範圍外要逐檔寫理由、交覆核看。主線補:protected_paths(裁示文件、發版腳本、prod 資料)是安全邊界,仍硬擋,不在本條。
- ② 閘門紅 / 落地全套紅自動修補上限,原話(選項)「3 輪(建議)」:首次紅後最多 3 次修補,跨重啟、換模型不歸零;第 3 次仍紅才發 decision 頁。
- ③ 開題實跑證據有效性,原話(選項)「相關檔沒變就算(建議)」:sha 是 main 祖先、且本票寫入範圍與 test_plan 指到的測試自該 sha 無變動即有效;證明不了才補跑。
- ④ #93,原話(選項)「繼續凍結到 DAG 排程落地(建議)」。
- codex 在 main 工作樹留下的 auto-fix.sh / test_auto_fix_codex.py 兩處修正存為 reports/t115/main-wt-fix-20261016.diff,附給 #115(N1);main 工作樹已還原。

## D-051(2026-10-17)code-map 重產歸腳本,不歸 worker 也不歸主線
- 起因:#116、#95 閘門都因 code-map 索引過期(rc 4,機械格不合)停下,主線在副本跑 `repo-map.py build`、經 apply.sh 收進分支(reports/t116、t95 的 regen-20261017)。
- 原話:「應該要改到 完工後要執行的腳本內，或是改到 測試全綠後 上主線的腳本內」
- 落實:#96(已落地)apply 單票路同 commit 重產;#129 補 apply --batch 與 land.sh 合併後過期自動重產(只動 code-map/,其他差異照擋)。順序 #101 → #129。

## D-052(2026-10-18)A 剩下的票串行疊加開發,做完一串再跑一次大驗證
- 原話:「我不是要你同時寫 一人一個一個票開發 按照排好隊順序，我是要你做好幾張之後 再跑大驗證」;「現在opus worker 品質應該可以，也要讓token 使用有效率」
- 做法:一次一個 opus worker,票 N 的分支從票 N-1 的分支頭開(主線先 `git branch t<N> t<N-1>`、base_sha 設成前票分支頭);每張只跑 `gate.sh --branch --ticket` 與覆核(覆核只審 `<base_sha>..t<n>`);整串綠了把 base_sha 改回鏈根、一次 `land.sh` 多票落地跑全套,紅了 auto-fix --batch。第一串 #131 → #132 → #122 → #104 → #106(開題者排序,depends_on 寫 stacked)。
- 主線裁(開題者 Q1,未經使用者):#131 A10「票上 model 當第一候選」只套在與票 role 相同的角色鏈;verifier / reviewer / opener / consolidator 照各自角色鏈(否則整理票寫 opus 會違反 #122「只派 Fable」)。
- 現有工具的缺口(開題者讀碼,未實測):apply 在分支不存在時從 main 開、land 拒絕 base_sha 不是 main 祖先的票 —— 目前靠主線手動建分支與改 base_sha 繞過;長期解另開票讓 apply / land 認得 stacked 依賴。

## D-053(2026-10-18)VISION 成為最上層判準;減法先行、停開關卡類票;記憶不設字數上限(推翻 D-006)
- 原話:「把它原文落成 agent-control/docs/VISION.md，標明「使用者原話，只有使用者能改」，同步到 tabby。DESIGN.md 是 AI 擴寫的提案，兩者衝突時以 VISION 為準。」
- ① VISION:`docs/VISION.md` 逐字落成(使用者 Claude Docs 文件 rev 17 的 Markdown 匯出),頁首標「使用者原話,只有使用者能改」;`docs/DESIGN.md` 頁首加註它是 AI 擴寫的提案、衝突以 VISION 為準。tabby 那份由主線手動複製(`sync-to-project.sh` 目前不帶 VISION,未改)。
- ② 先停,原話:「在減法做完之前，不開新的關卡類、規則類 A 票。現在這一串（#131 → #132 → #122 → #104 → #106）裡，#104 和 #106 屬於 VISION「明確不是我要的」，先撤出來。其餘的照跑，跑完就停。」—— #104、#106 凍結並轉 Blocked(分支 t104 / t106 留著),解凍條件是使用者在減法清單裁定那一項。#131 / #132 / #122 已落地。主線裁(未經使用者):#133(classify 認得 codex 純文字額度行)不在原串、是同批疊上去的路由修補,不屬三類,單獨落地後停。
- ③ 減法稽核,原話:「派一個 opus 唯讀稽核。現有每個機制、欄位、腳本一列，各自對到 VISION 的哪一條。對不到的要寫：每張票多花多少（從 cost 算，算不到寫未知）過去 30 天實際擋下幾次、哪幾張票 拿掉之後最可能出什麼事 最後交一份「刪除／降級／保留」清單，每條附建議，用選擇題問我。VISION「明確不是我要的」那三類，預設都是刪除，除非稽核拿得出數字證明值得留。」
- ④ 之後的規矩,原話:「每張 A 票要寫它改善 VISION 的哪一條、哪個指標；加一道關卡就要拿掉一道。」—— 開題者開 A 票時在票面寫明;目前只是開題規矩,不加新的機械檢查(加檢查本身就是一道關卡)。
- ⑤ 收件匣,原話:「287 則先清掉，只留真的要我裁的。」
- ⑥ 記憶,原話:「記憶不設固定字數上限，推翻 D-006。只要暫存區有新條目、還沒整理，就觸發整理；寫什麼、留多長由整理的 LLM 決定。整理時的判準是「每個 session 開場都會讀，只留值得這個成本的」。」—— 推翻 D-006 的 2000 字元上限與「超過才整理」;實作另開一張票(拿掉一道上限,屬減法,不受 ② 限制)。

## D-054(2026-10-19)減法稽核的 12 題(稽核報告:120 列機制、42 列對不到 VISION)
- 來源:opus 唯讀稽核(D-053 ③)的選擇題,使用者逐題選。數字是稽核從 cost[] 與兩庫 events 算的,其中「覆核常態真抓 3.3%」與「guard-main 27 次誤擋」稽核自標判斷、未逐筆重現。
- Q1 覆核:原話(選項)「只派複雜票(建議)」—— 開題者每張票都標 simple/complex,只有 complex 派 reviewer;land / close 的覆核要求同步只對複雜票。
- Q2 寫入範圍:原話(選項)「全刪,只留硬擋清單(建議)」—— allowed_write_paths、in_scope、out_of_scope、protected_paths、hint_scope、info_homes 與下游全刪。
- Q3 硬擋清單:原話(選項)「文件+發版+prod 資料(建議)」—— A、T 各一張:DECISIONS、VISION、發版腳本、prod 資料目錄(repo 外,由事後越界守衛查),加案例 lint F4 的殺行程 / 打 prod 埠字樣;要先立清單再刪寫入範圍。
- Q4 驗證疊加:原話(選項)「全刪(建議)」—— 案例 lint F1–F12、四問、#106、verify_strings、閘門機械格、close 第二道判準、land 的 verify.files、verify_waiver;只留實作者單元測試 + 驗證者案例 + 閘門跑案例 +「沒案例 = 缺口」。#104、#106 轉 Cancelled(分支留著不落地)。
- Q5 單一狀態,原話:「選1。條件:刪 metrics.py 和 index.jsonl 跟看板 改讀票 JSON 放在同一張票,看板要能從票 JSON 顯示 VISION 的四個指標。刪 ticket.state 事件 前,先確認沒有東西在讀狀態轉換的歷史;有的話 一起改。」—— 票 JSON 為準;刪 ticket.state / ticket.cost / inbox.posted 三種鏡像事件與「主線每個動作要發事件」;land.* / agent.* 事件留著(#115 越界守衛靠它)。
- Q6 上限:原話(選項)「兩個都刪硬擋(建議)」—— 記憶照 D-053 ⑥(#134);rules.py 刪位元組上限硬擋,留「缺節就不起 agent」。
- Q7 主線守衛:原話(選項)「整支刪掉(建議)」—— guard-main.sh 整支刪。
- Q8 反駁分流,原話:「worker 說票寫錯，要有腳本直接派開題者（A、T 都一樣）。開題者一輪內只有兩種結果：改好票面接回 worker，或交一題附建議的裁示題給主線。一個月後看 T 的往上送比例再決定要不要改」—— 2026-11-19 前後主線拿 T 的 escalate 比例回來問。
- Q9 交件:原話(選項)「改成分支 commit(建議)」—— worker 在自己的 worktree / 分支 commit,腳本只檢查分支;刪 apply.sh 檔頭驗證、rebase 重套、#107 landed-check。排在寫入範圍與覆核之後。
- Q10 票面欄位:原話(選項)「缺=true,其餘選填(建議)」—— needs_verifier 沒寫當 true、不擋 Ready;interface_fixed 與實作者變異表改選填。
- Q11 小件:原話(選項)「全刪,回答鈕留著(建議)」—— REHEARSAL 看板段、check-stale、budget、info_map、scope_conflict 死碼一張清理票刪;看板回答鈕留著。
- Q12 文件,原話:「每條規則只留一處正文、刪掉 test_dispatch_template,方向對。用測試釘住多份文件一致,本身就是「存好幾份」的症狀。例外,也是上面說的衝突:會改變 agent 行為的那幾刀,要在同一張票裡就改掉 agent 讀的規則原文,不能等最後才整理。最明顯的就是Q9:現在每個 worker 開場都會讀到「禁一切git 寫入」。改成分支 commit 之後,如果規則包還帶著這句,worker 會被兩邊互相矛盾的指令卡住,或者乾脆不commit。」
- 順序:先 A 後 T(使用者 2026-10-18:「我們可以先把agent control 整個改好再去改tabby」)。

## D-055(2026-10-19)記憶整理改批次觸發;減法票的驗收上限;減法做完的回報
- ① 原話:「#134讓它跑完,之後補一個小改動:整理改成 批次觸發(每份記憶一天最多一次,或累積N條, N 你定),不要有1條就派 Fable。」—— 主線定 N=5:一份暫存區累積 ≥5 條,或最舊一條已放滿 3 天,才開整理票;同一份記憶 24 小時內最多觸發一次。另開一張票,depends_on #134。(主線裁「3 天」那條,免得條目少的暫存區永遠不整理。)
- ② 原話:「給減法開題者加一條要求:每張驗收最多5條 (grep 已刪名字為0、既有全套綠、一條行為改變 的斷言),不新增事件類型、不寫死stdout 字串、 不加併發斷言,純刪除的票 needs_verifier=false」
- ③ 原話:「整批減法做完後,回報 合計增刪行數、token、每張落地票的成本前後對比。」—— 對照組:減法前 30 天每張落地票的 cost(稽核報告已算的基準)。
- 開題結果(複雜開題者,2026-10-19):減法拆成 #135–#148 共 14 張,全 Ready、驗收 ≤5 條、純刪除票 needs_verifier=false;DAG:#141 →(#134 落地後)#135 / #140 / #148 → #136 → #137 → #138 → #139 / #142 → #143 → #144 → #145 / #146 → #147(T 端 11 條配套清單在 #147 outline,A 做完再開 T 票)。
- 主線裁(開題者待裁 1 題,未經使用者):#143 加 `interventions` 計數 —— 票轉 Blocked / NeedsDecision 且 owner=main 時 +1,舊票顯示「未知」;它是 VISION「每張票需要我介入幾次」的近似(數的是要主線處置的次數,不分主線自己處理或轉問使用者),看板標名照實寫。這是統計欄,不是關卡。

## D-056(2026-10-19)減法票的驗證者不開新案例檔;每張落地回報行數與 token
- ① 原話:「驗證者別再新開以票號命名的大案例檔。#134 和 #135 各多了 300 到 400 行，抵掉了大半刪除。行為的斷言寫進既有的測試模組；純刪除的票，驗收就是 grep 為 0 加上既有全套綠。#143、#144 派驗證者時照這個做。」—— #136 已在跑,照原票面;其餘還沒起的票由開題者改票面。
- ② 原話:「每張落地時順手回報一行：git 增刪行數和這張的 token。不用等整批，我要早點看到趨勢。」—— 行數取 merge commit 對主線的 numstat;token 取票 `cost[]`(非 cache = in + out + cache_write,另列 cache 讀),信封讀不到的筆數照實寫「未知」。

## D-057(2026-10-19)減法收尾的三條
- ① 原話:「落地全套跑一次要 30 多分鐘，這也是成本。整批做完回報時，加上落地全套的耗時前後對比；測的是已刪機制的測試要跟著刪。」—— 剩下的票驗收「已刪名字 grep 為 0」範圍含 tests/ 與 verify/;對照組取減法前的落地全套耗時。
- ② 原話:「#144（改分支 commit）是最大、也最可能出事的一刀。落地後，先拿一張真的小票從開題走到落地，完整走一遍，確認新流程通了，再宣告減法完成。」
- ③ 原話:「減法做完前不用逐張回報給我，照原本的節奏：做完或卡住要裁的時候再說。」—— 取代 D-056 ② 的逐張回報;數字照記,整批做完一次交。

## D-058(2026-10-20)本機額度快用完:#144 先做,worker 改用雲端
- 原話:「claude --cloud 還有很多額度 你看接下來worker 能不能先派到這邊」;「144 要先 這邊token 快用完了 我需要用cloud 那邊的 我們進度才能繼續」
- 做法:本機串接停在 #138(worker 已交、只剩本機閘門);#144 跳到 #139 / #143 之前,由使用者開雲端 session 貼主線準備的派工文,雲端推 `t144-cloud`,本機 fetch 後走 apply / 閘門 / 覆核 / 落地。#139 / #142 / #143 之後再排。
- #149(雲端 worker 候選自動化)開題,depends_on #144。主線實測補充:`claude --cloud` 從非 TTY 呼叫直接拒絕(「--cloud requires an interactive terminal」);Agent 工具的 remote 隔離在本機跑、不算雲端。
- 與 VISION「worker sol→opus」(D-049 ②)不同:雲端放 worker 鏈第一格是本條的原話所致;VISION 由使用者回寫。開題者待裁 1(完成訊號)交 spike 實測後再裁;待裁 3(雲端 token 記「未知」)主線裁接受。
- 補(2026-10-20)完成訊號,原話:「怎麼知道雲端做完: 用 loop 20 分鐘看一次」—— #149 的完成訊號用 20 分鐘一次的低頻檢查(`git ls-remote` 看分支出現),這是使用者明示允許的輪詢,只限雲端 worker 的完成判斷;主線那一側用背景迴圈等,不用排程喚醒(喚醒一次就重送一輪主線上下文)。

## D-059(2026-10-21)減法執行中的主線裁示彙整;主線交接給雲端 session
- 反駁處置(主線裁,未經使用者;逐條也在各票 objections[]):#141 舊案例引用 guard_main → 寫入範圍只是建議,worker 照改;#135 驗收 grep `verify_lint` 撞 #138 範圍 → 豁免,test_defect 由獨立驗證者修 a3;#136 verify_strings 與驗收互斥 → 不擋落地,#138 落地後關票;#143 收件匣 acked.jsonl 一次性搬遷(搬到 reports/inbox/acked/)、test_board.py:444 先斷言非空、驗收 1 豁免 migrate_acked_jsonl(T 同步並跑過一次後移除,列進 #147 的 T 端清單);#144 驗收 grep 改字邊界;#144 保留 apply.sh headers()/verify_targets 給 #145 刪。
- 雲端 worker 實測:routine API(只在主線 session 工具裡)換派工文 → run(先 update 再 run,同時送會讀到舊的)→ 背景每 20 分鐘查 `origin/t<n>-cloud:EVIDENCE-t<n>.md` → 收件(`t<n>` = `origin/t<n>-cloud~1`)→ gate / review / land。雲端容器會沿用舊 clone,派工文第一步一定要 `git fetch origin`(#146 因此從舊 main 開、合併衝突 6 檔)。雲端 auto mode 會擋「刪檢查」類編輯,使用者已對 routine 放行。
- 原話:「主線要交接給雲端 session。交接完你就停下來，不再派工、不再落地，也不再動票。」—— 本機主線 2026-10-21 停工,票況與未完事項寫在 docs/HANDOFF.md「交接給雲端主線」。

## D-060(2026-10-21)雲端主線可以從雲端 session 推 main
- 原話:「意讓 land.sh 從這個雲端 session 推 main」—— 雲端主線照本機流程用 `scripts/land.sh`(票與 docs 通道)落地並 push `origin main`;不改成開 PR。前提:GitHub App 對 agent-control 要有 Contents 寫入(2026-10-21 補裝後 push 才通,之前 git push 與 API 都 403 `Resource not accessible by integration`,雲端 worker 的 `t146-r2-cloud` 也因此第一次沒推上)。
- 雲端容器以 root(uid 0)跑:靠 `chmod` 造寫入失敗的案例在 root 下不紅(#146 分支閘門唯一一條紅:test_apply `test_an_objection_that_cannot_be_recorded_is_not_called_already_there`,乾淨 main 一樣紅)。主線派開題者開票改成 root 也擋得住的造錯方式,不准 skip;該票落地是雲端第一次 land 的前置。(讀 log 得知,修法未實測)

## D-061(2026-10-21)派工以主線 session 內的子 agent 為主,雲端與地端同一套;routine 降為備用
- 原話:「照你建議 worker 以後可能是雲端可能是地端 現在的方式較好的話 地端也可以這樣做」—— 回應主線建議:#149 改寫成「主線在同一個環境用子 agent 派工」(主線開 worktree 與輪次目錄、派工文只給四件事、子 agent 做完主動回報、主線 apply → gate → land),不用推中間分支、不用 20 分鐘輪詢;routine(`ac-worker`)只留作備用。雲端與 Mac 都照這一套。
- 實測依據(2026-10-21 雲端,uid 0):#151 開題者 + worker 都是同容器子 agent,apply / gate / land 一次走通(land 全套 1756 s:unittest 1051 條 1217 s + 回歸 312 條 535 s);#146 由 routine worker 做,撞過舊 clone 開分支(合併衝突)與 push 403 兩次。子 agent 方式在 Mac 上還沒實測。
- 補(2026-10-21,主線裁,未經使用者)#149 開題者交回的三題,照建議:① 同容器子 agent 的 token 只進 `agent.cost` 事件、不進票的 cost[] → 另開票,不在 #149 做;② 同容器這條路沒有 rc 8 越界守衛 → 暫時接受;③ 覆核仍由 `review.sh` 起 headless 程序、雲端未實測 → 先照跑,失敗再開票。

## D-062(2026-10-21)#147 的 T 端配套清單先交讀得到的部分
- 原話:「A」—— 回應主線的選擇題(A:先落地,缺的標「Mac 上補」;B:放行唯讀 clone tabby_pool 再補)。#147 驗收 4 以 worker EVIDENCE(`fix-t147/round1/EVIDENCE-round1.md` 的 T 端 11 條)為準:讀到並附行號的約 6 條照交;讀不到的(test-for.sh、看板讀 index.jsonl 處、第 10 條、第 11 條 `../tabby_pool_ctl/tickets` 舊票,以及 land-ticket.sh 的行號)在 Mac 上開 T 端票時補。雲端讀不到的原因:clone / `gh api` / jq 讀 tabby_pool 被 session 權限分類器擋,`tabby_pool_ctl` 不在 GitHub。

## D-063(2026-10-21)簡報待決題的裁示;auto-fix.sh 要比減法前小
- ① 原話:「1 照建議 A。」—— 即 D-062。
- ② 原話:「#147 落地即宣告完成，報告分兩段：現在交行數、token、全套耗時；T 端跑完 10 張票後，補每票成本對比減法前 30 天。另外報告要列 auto-fix.sh 的行數，它現在比減法前大，收尾要讓它變小，至少說明為什麼不行。」
- ③ 原話:「3 開票補。」—— 同 session 子 agent 的 token 進票的 cost[](D-061 ①)。
- ④ 原話:「4 VISION 我自己改：worker 的模型順序不變，加一句「執行位置（本機/雲端/子 agent）由主線依額度決定」。」—— 使用者自己改 VISION(hard_block,主線不動)。
- ⑤ 原話:「5 追認。子 agent 交件後加一個事後比對：main 的 HEAD 前後不變、沒有被 push，有變就拒收。只做這一個比對，不恢復守衛。」—— 追認 D-059 反駁處置、D-061 三題、D-055 interventions 近似與「滿 3 天」;另開票做這一個比對。
- ⑥ 原話:「auto-fix.sh 還是 3,223 行，比減法前（2,959）還大。 我上次請它「收尾時要比減法前小」，簡報裡沒有提。#149 的 --prepare 很可能又加在這支裡。」—— 實測(主線,git show | wc -l):cf6b991(2026-10-18,#121 前)2,959;a36eb64(減法開票)3,302;ac3fb4d(#145)3,223;9ef2d6e(#149,+--prepare)3,415。目標以使用者說的 2,959 為準:收尾票把 auto-fix.sh 壓到 2,959 以下,做不到要寫明為什麼。之前那句要求沒有落進 DECISIONS,這一條補上。

## D-064(2026-10-21)#153 / #154 的裁示:保住安全、拿掉歸屬判斷
- ① #153 原話(選項):「只准主線落地前進」—— 子 agent 跑的期間 main 只能因主線自己的落地前進。
- ② #154 原話:「拿掉歸屬判斷那套複雜邏輯，但留兩個簡單比對，跟 #153 的 HEAD/push 比對放在一起：① 主 repo 工作樹 git status --porcelain 加髒檔雜湊，跑前跑後要一致；② hard_block.dirs 底下的檔跑前跑後要一致（tabby 的 prod 資料目錄靠這條）。主線在 worker 跑的期間自己落地造成的差異，用最簡單的方式處理就好，例如 worker 跑的期間主線不落地，或者有落地就只記錄不擋，不要再做逐 commit 歸屬。行數到不到 2,959 不重要。沒到的話，報告寫一句理由就好。這樣應該也能省掉大半行數，因為大頭是歸屬判斷。我沒有實測，把握中等，但方向是保住安全、拿掉複雜。」
- 主線解讀(① 與 ② 並讀,未經使用者確認):不做逐 commit 歸屬(不走 land.pass 鏈);main HEAD 在子 agent / worker 跑的期間前進時,若同一段期間有主線的 land 事件 → 只記錄不擋;沒有 land 事件卻前進、或 origin 被 push → 拒收。四個比對(HEAD、push、主 repo 工作樹 porcelain + 髒檔雜湊、hard_block.dirs)放同一處,headless 與子 agent 兩條路共用;#127 的歸屬判斷(land_attribution 鏈走查)與 rc 8 的檔案快照守衛由這四個比對取代。

## D-065(2026-10-21)#153 的三個細節;sync-to-project.sh 那一行留給使用者
- ① 原話:「1 照建議。「只記錄不擋」的那一段，紀錄裡要列出不屬於落地的 commit，不擋，只要查得到。」—— D-064 主線解讀成立:main 前進而同期有主線落地 → 只記錄不擋;紀錄要列出這段期間不屬於任何 land.pass sha 的 commit(集合差,不走鏈),查得到即可。
- ② 原話:「2 豁免 tickets/、docs/、memory/，但 hard_block 清單上的路徑（DECISIONS.md、VISION.md 等）不豁免，有變就拒收。」—— 主 repo 工作樹比對:tickets/、docs/、memory/ 底下的變動只記錄不擋;hard_block.paths 上的檔(含 docs/DECISIONS.md、docs/VISION.md)不豁免,有變就拒收。主線因此在子 agent / worker 跑的期間不能改 DECISIONS.md(主線推論,#153 落地後生效)。
- ③ 原話:「3 照建議，記進 HANDOFF，我在 Mac 上改。」—— scripts/sync-to-project.sh 第 466 行「(不進票 cost[])」在 #152 落地後要加 --prepare 例外;hard_block 檔,使用者在 Mac 上改。
- 主線裁(未經使用者,#153 開題者 Q3–Q5 照建議):#144 的「其他本機分支被動到」比對拿掉(不在四個比對裡);`git ls-remote` 失敗或逾時只印「push not compared」不擋;沒有 --prepare 快照的手派子 agent 不比對、印一行。
- 補(主線裁,未經使用者)#153 開題者兩題:Q1 窗內 commit 動到 hard_block.paths 也算「有變」→ 拒收(否則主線在窗內改了再落地 DECISIONS.md,工作樹前後都乾淨,看不到;D-065 ② 的延伸);Q2 不加新事件型別,紀錄寫在 main-compare.json 與 guard.json。

## D-066(2026-10-24)使用者授權改 hard_block 檔 sync-to-project.sh 兩處,直接 commit 進 main
- 原話:「我授權修改 hard_block 檔 scripts/sync-to-project.sh，只限這兩處：(a) SCRIPT_LIST 加 scripts/main_guard.py；(b) 第 466 行附近「(不進票 cost[])」補上 --prepare 的例外。這個檔 land.sh 會擋，所以這一次直接 commit 到 agent-control main 並 push；commit 訊息寫明「user-authorised hard_block edit (D-066)」。同時在 agent-control docs/DECISIONS.md 加 D-066，附我這段原話。除了這兩處，不准動任何 hard_block 檔。」
- 做法:SCRIPT_LIST 末尾加 `main_guard.py`(#153 新模組,不加 tabby 的 auto-fix.sh 會 import 失敗);agent-cost-hook 提示那行改成「不進票 cost[];`auto-fix.sh <n> --prepare` 派的子 agent 例外,記進那張票的 cost[],#152」。`tests.test_sync_to_project` 46 條實跑綠。沒有加進 DEPENDENCY_ONLY(授權範圍外):同步時若對 main_guard.py 印「沒有呼叫點」的提示,是這個原因。

## D-067(2026-10-24)派工回到方式一(headless);方式二(--prepare 子 agent)退役,推翻 D-061
- 原話:「選方式一， 我感受不到方式二的好處，如果之前往錯誤方向做了 就當教訓吧」—— 回應主線的修正建議:headless(`auto-fix.sh` 起 `claude -p` 等程序,跑完寫收件匣)恢復為唯一主要做法,雲端也用;每一輪預設開新 agent,靠 EVIDENCE「已排除的假設」與狀態檔 repair_context 交接;續做只用在交件後短時間內的退回(headless `--resume`),或不做。`--prepare` 這條路(#149)與 #153 在 apply.sh 收件那一側的比對一起退役;#152 的 cost[] 併入(從派工文路徑認票)隨之檢討。
- 為什麼錯(主線檢討,寫給下一個主線):D-061 拿子 agent 跟 routine 比(舊 clone、push 403、20 分鐘輪詢都是 routine 的問題),沒有跟 headless 比;headless 在雲端可用(#149 的 review.sh 用 `claude -p` 實跑成功)。子 agent 的代價正是 headless 要避免的:每則回報進主線上下文;等待期間主線與子 agent 都被「還在跑」通知叫醒(#151 約 30 次,worker 每次重讀約 95k);主線第一天 8 小時 cache_read 約 2,786 萬。續做也不省:#147 驗證者第 2 輪(修一條案例)約 28 萬換算 token,與全新第 1 輪的約 30 萬相當(快取寫 ×1.25、讀 ×0.1 換算;疑為等閘門期間快取過期整份重寫,未完全確認)。
- 連帶:scripts/sync-to-project.sh 在 D-066 加的「--prepare 例外」註記將過時;hard_block 檔,使用者改(主線待辦)。

## D-068(2026-10-24)#156 / #157 開題者兩題(主線裁,未經使用者)
- Q1:`verify_case.timeout_seconds` 設 2700(check 跑 base 與 candidate 兩趟,各算各的時限);#156 落地後在雲端實跑 `verify-case.py red 155`,2700 還逾時就另開票查是卡住還是慢,不再加大數字。
- Q2:驗證者 EVIDENCE 有反駁時 apply.sh 回 rc 6、auto-fix 報「收件沒過」,不像 worker 反駁那樣交開題者判 —— #157 先接受這個行為,路由另開票。
- 順序:#156 先落 → #155 閘門、覆核、落地 → #157。#157 開題者實測:反駁沒記進票的真因不是 ticket.py 去重(它比 category + body),而是 apply.sh 只讀 worker EVIDENCE 的第一行反駁、驗證者 EVIDENCE 的反駁沒送進票(讀 code 推,原始現場未重演)。

## D-069(2026-10-24)tabby 回報的三題:主線受權裁定
- 來源:T 端回報「tabby 改走 agent-control:遇到的問題」(2026-10-24)。使用者原話:「分別啥影響? 你能分析優劣然後 決定嗎? 這些問題是有啥設計取捨嗎?」—— 使用者授權主線分析後裁定;以下為主線裁定。
- ① 方向 1「A 的入口都能從專案跑」:採用。取捨是共用平台 vs 專案分叉;證據:T 自寫落地器 land-ticket.sh 2,601 行,同步 A 08e6752 後 8 處壞、其中 6 處是黏合層對不上 A 介面(覆核 / 反駁檢查默默失效)。A 補:land.sh / gate.sh / 看板的控制腳本路徑走 $AC 並進同步清單;gate 選填 `gate.select_cmd`(專案只提供挑測試與怎麼跑,preflight 回 86 走既有 env_suspect);verify.py `--case`;看板認 AC_CONTROL_DIR 與選填 `board.key_file`。專案只留專案知識(挑測試、瀏覽器引擎層、preflight、build / deploy)。先派設計 session 落文件(D-019),再開票。
- ② 同步清單:留在 hard_block(發程式碼進別的專案是政策,要人把關);SCRIPT_LIST 與 DEPENDENCY_ONLY 合成一份,依賴由 import 關係自動算,同步時發出去的腳本缺依賴就失敗(不只警告)。只有新增入口才需授權;方向 1 的新增一次授權。設計併入 ① 的文件。
- ③ 收件匣搬遷:`inbox.py` 讀取時不再自動執行 migrate_acked_jsonl,改成明確 `inbox.py migrate`。開票先做。T 在 A 修好並重新同步前:不要重開 session,或先把工作樹的 scripts/control/inbox.py 換回同步前版本(主線建議後者);T 改用 A 看板時手動跑一次 migrate。

## D-070(2026-10-24)#157 / #158 開題題目與設計文件(主線裁,未經使用者)
- #158 不派驗證者(needs_verifier=false,A1–A6 已寫死 fixture 與期望值)。
- #157 Q2:驗證者反駁暫走 rc 6,分流到開題者另開票(同 D-068 Q2)。Q3:同處插入只對 .py 自動接,並要求接完可解析、沒有重複的頂層名稱;其他衝突照舊 rc 3。
- 設計文件 docs/design/project-runnable-entries.md(D-069 ①②)收下;建議 6 張 A 票。待裁:Q1(T 的閘門測試是否全能由 unittest 載入)與 Q2(T 看板對外是直接綁介面還是走代理)轉 T 端回答;Q3(同步清單一次加入 land.sh / gate.sh / board.py 三個入口,hard_block 檔)等使用者授權。

## D-071(2026-10-24)A 是管理工具:專案 clone A 直接跑,不再同步;介面要完善有彈性
- 原話:「同步清單要一次加進 land.sh、gate.sh、board.py 三個入口  同意。你把要改好的都改好 我再去跟T 那邊講要怎麼做 T 的測試 你要定義一個好的方式 讓他遵守，我覺得A就是個管理器 一個專案要用就是clone一個 然後直接在上面跑東西 不再做額外sync, 要同步remote 就pull就好 interface 要定義完善 有彈性 T 的看板是走代理(portal」
- 裁示:A(agent-control)改成安裝即用的工具 —— 專案端 clone A,從 A 的 clone 直接對專案執行;更新 A = 在 A 的 clone `git pull`。不再把腳本同步進專案:sync-to-project.sh、SCRIPT_LIST / DEPENDENCY_ONLY、專案的 scripts/control/ 與 .synced-from 退役(使用者原話「不再做額外sync」視為授權退役這支 hard_block 檔;「三個入口同意」的授權因此不再需要,保留紀錄)。D-069 ② 由本條取代;D-069 ①(專案只留專案知識)保留並擴大。
- 介面要寫完整、有彈性:專案設定檔(schema 與版本)、專案提供的測試介面(主線負責定義,T 照著實作)、Claude Code hooks 的接法、專案自有狀態(票、裁示、記憶的專案層)與 A 自有內容(角色卡、規則、範本)的界線。A 自己也照同一套介面跑(自己是自己的第一個專案)。
- T 看板走代理(portal):A 的看板維持只綁 127.0.0.1;key 門做成選填設定,由代理那一側決定要不要用。
- 先派設計 session 重寫 docs/design/project-runnable-entries.md(D-019),再開票。

## D-072(2026-10-24)設計文件 v2(A 是安裝即用的工具)的五題(主線裁,未經使用者)
- 依據 D-071 與使用者「interface 要定義完善 有彈性」「T 的測試 你要定義一個好的方式 讓他遵守」。
- Q1:T 照設計 §4 的測試介面寫自己的轉接器(可先只宣告 preflight)。Q2:A 的 clone 預設放專案旁(../agent-control),A 不依賴位置。Q3:單一入口改名 `actl`(bin/actl),避開 macOS 內建 /usr/sbin/ac。Q4:專案設定比 A 新 → actl 拒跑 rc 2,訊息叫人 git pull A。Q5:刪 scripts/sync-to-project.sh 與測試視為 D-071 已授權;board/config.json hard_block.paths 那一列由主線落,不在 worker 票裡。
- 票:#159〜#167(依賴見各票;#159 先做)。
- 主線事故(寫給下一個主線):#157 第 1 輪 worker 被 main_guard 拒收(rc 8),原因是主線在 worker 跑的期間落了 D-071(DECISIONS.md 是 hard_block 路徑,D-065 ②)。守衛照設計運作;主線查清後認可 worker 的 commit、手動收件接回。規矩:派出 worker 到它收件前,主線不寫 DECISIONS / VISION。

## D-073(2026-10-25)worker 只跑驗證者的案例加基礎組;改用批次落地
- 原話:「worker 本來就該只跑驗證者寫給他的測試+1分鐘基礎case 。要 改用批次落地」
- ① worker 交件前只跑:這張票驗證者寫的案例(驗證者後起、案例還沒有時,改跑自己改到的單元測試)+ 基礎組(約 1 分鐘;`gate.sh --base` 的實際耗時由開題者量)。全套與回歸交給閘門與落地。改派工文與實作者角色卡的規則原文,開票做(D-054 Q12)。
- ② 落地改批次:彼此不依賴的票各自過分支閘門(complex 加覆核)後,一次 `land.sh t<a> t<b> …` 串接、只跑一次全套。剩下的 A 票分批:{#162、#164、#165} → {#163、#166} → {#167}。
- 背景(主線實測,寫給使用者的回答):一張票約 1.5–2.5 小時;落地全套雲端 27–47 分鐘、單程序、一次一張排隊;worker 自己跑大段測試是 #155 / #159 逾時主因;分支閘門不跑回歸層,#159 到落地才紅。另記待開:全套平行跑(預估 27 → 約 10 分鐘,未實測)、分支閘門跑回歸層。

## D-074(2026-10-25)DAG 排程由腳本做;全套平行跑依核心數與記憶體決定程序數
- 原話:「票的Dag 上 應該可以 跟 land.sh 1 2 3 連動對吧? land.sh t162 t164 t165 應該是系統自動知道的 不是 llm 去控制的對吧? Llm 應該是依我的要求 去改 dag 的batch 檔案,後面的 land 該怎麼跑 應該是自動的?」「票之間的Dag 之前有開過票要實作了才對? 開題者要在開題時 把自己的票插入dag 你看怎麼把他們都做完最有效率 就做」—— 改寫 #119 為排程腳本(讀 depends_on + 主線依使用者要求編輯的批次檔,自動派工、自動批次落地、只在終態叫醒主線);剩下的 A 票由主線最後一次手動分批:{#160、#165} → {#162、#164} → {#163、#166} → {#167}。
- 原話:「全套平行跑 要先去抓系統有多少core, 還有當前記憶體用量 去判斷該用幾顆core, 避免系統卡死」—— 平行跑那張票的驗收:執行當下讀核心數(以容器 cgroup 配額為準)與可用記憶體(cgroup memory.max 減目前用量,或 MemAvailable),除以實測的每程序高峰記憶體,取兩者較小並留餘裕(至少留一顆核心給系統;記憶體不足退回單程序);上限可由設定覆寫;閘門輸出印出用了幾個程序與原因。

## D-075(2026-10-25)不做 profile 模式;驗證者與覆核照票上既有欄位;#118 取消
- 原話:「Agent control 裡是不是應該有個mode , 只留下基礎票面本身 跟主線 可能還有儀表板， 然後其他都交給claude 自己控制」→ 主線建議做成設定預設值而非另一條程式路徑 → 原話:「票裡要有欄位給開題者 看要不要啟用 覆核 跟驗證者 這樣就好了」—— 不做 profile / lite 模式。驗證者看 `needs_verifier`(缺 = true,#146),覆核看 `complexity`(complex 才覆核,#136),都由開題者在票上判。看板補兩格顯示「驗證者:有 / 無、覆核:有 / 無」(之後開小票;欄位本身不改,complexity 另管開題模型選路)。
- 主線裁(#119 開題者兩題,使用者授權「你看怎麼把他們都做完最有效率 就做」):#118(PlanningBatch manifest)取消 —— DAG 在票的 depends_on、順序在 board/batches.json;#117 叢集(#120、#123、#124、#125)保留 Draft、之後另判。#119 改寫為 `actl schedule plan|run`(depends_on #162);新票 #168(worker 只跑驗證者案例 + 基礎組,`gate.sh --base` 實測 35.5 s)、#169(全套平行,程序數依 cgroup 核心配額與可用記憶體,D-074)、#170(分支閘門跑整個回歸層)。

## D-076(2026-10-26)舊 Draft 的處置:#93、#120、#123 取消;#117、#125 要做;#124 重寫
- 原話:「93 不用 117 125 要 其他按照你建議」—— 回應主線的逐張建議。
- #93(交件前主線對齊)取消:worker 已在分支 commit(#144)、land 在最新 main 上合併跑全套、收件三向合併(#157)、#119 依檔案重疊拆落地。
- #120(無 LLM 常駐 Controller)取消:#119 `actl schedule` 已是無 LLM、有狀態檔、可接續、會自動派工與重新落地的排程器。#123(保留副本續作)取消:D-067 每輪開新 agent、靠 EVIDENCE 交接;實測續做不省 token。
- #117(session 生命週期:分得出中途死亡 / 待接手 / 已接手)、#125(閘門與落地紅預設自動修補,含無歸因紅與環境可疑的有界診斷)要做,照 D-067 / D-071 / #119 之後的架構重寫票面(舊依賴 #95、#96、#98、#121、#126 是 Done 卻沒有 landed_sha,改寫時拿掉或改指現行票)。背景:容器 2026-10-21〜26 重啟 4 次以上,每次由主線手動查與接回。
- #124(逾時 / 中斷的 headless 成本從 transcript 補讀)保留,重寫成不依賴 #123 的獨立小票。背景:#155、#159、#169 逾時輪的 token 記 null,成本帳約 4 成「未知」。

## D-077(2026-10-26)#117 / #124 / #125 重寫後的三題(主線裁,未經使用者)
- Q1:D-019 理由 5「環境可疑不自動派」與 D-076 衝突 —— D-076 勝。依據:使用者批准做 #125 時,主線列的範圍已寫明「環境可疑先自動診斷」。改為:一次免費機械重跑 → 一輪 LLM 診斷 → 仍紅才發頁。
- Q2:診斷輪固定連續最多一次,不做設定鍵。Q3:`batch_fix.rounds` 拿掉,批次修補也由單一 `repair.max_rounds`(預設 3,D-050 ②)管。
- 排程:batches b3 = #124、#117;b4 = #125(depends_on #117)。#117 與 #125 不併(一個是程序死亡後的事實對帳,一個是測試紅的修補預算)。

## D-078(2026-10-30)開發迴圈重寫:兩種謹慎度、刪五塊、流程乾淨優先於例外
- 原話:「要的是幾張做完一起跑一次大驗證 我已經講好幾次了 為啥遲遲不改進去?? 根本不該存在的五塊 全刪了 agent-control 主要支援2種謹慎度 dag 每票都全驗 跟 dag 一堆一起驗,哪個檔案幾行不重要 開發迴圈的邏輯切乾淨 哪個時候call 啥腳本要明確 ,用這原則 去重新定義該怎麼寫腳本,我不介意整個翻掉重來。 邏輯乾淨 互動介面單純 是最重要的指標,不要生出一堆例外。遇到例外 優先想怎麼改流程」
- 主線承認:D-052「做好幾張之後再跑大驗證」與 D-073「改用批次落地」兩次都被做成加法(批次落地加在每票分支閘門之上,#170 又往分支閘門加回歸層),結果是「每票驗一次 + 每批再驗一次」。
- ① 刪五塊(審查報告 2026-10-30「該化簡的、不該存在的」表):flaky 帳本 / 自動開票 / 順序相依偵測;batch_fix;驗證者的 git archive 副本 + patch-verify + 三方合併 + ref 樹(→ 驗證者也在自己的 worktree / 分支 commit,取代「驗證者不 git 寫入」);第二棵測試樹 verify/ + verify.py + 分支閘門回歸層(#170);同一件事存好幾份(票 JSON 是唯一狀態,其餘只當 log)。
- ② 兩種謹慎度,都走 DAG:`ticket`(每票都全驗,綠一張落一張)與 `batch`(一堆做完一起驗一次,綠就一起落)。
- ③ 判準:邏輯乾淨、互動介面單純是最重要的指標;行數不重要;何時呼叫哪支腳本要明確;不生例外,遇到例外先改流程;可以整個重寫。
- 主線解讀(未經使用者,寫在設計文件待確認):每票分支閘門拿掉,「驗證」只剩落地前那一次全套;D-077 的診斷輪與環境可疑重跑改由流程處理(跑之前 preflight 擋、批次紅逐票重跑歸因、歸因不到就把這批退回 `ticket` 模式)。
- 先寫 `docs/design/dev-loop-v2.md`,使用者看過再開重寫票;#188、#189 主線建議暫緩到設計定案。

## D-079(2026-10-31)開發迴圈 v2 改照使用者的五支腳本規劃;整合分支即 PR、綠了合進 main 並發版
- 使用者規劃原話(節錄):「我說要做什麼 主線轉給開題者用: ticket_creator.sh 裡面會定義: 一串prompt 還有一個參考格式的票 跟json 還有把開題者叫起來的指令 開題者完成會回報主線。DAG 是開題 會要求去填寫的東西,DAG是一個獨立檔案,每票會標記 能不能跟前票跑完再一起驗,除非是系統性的重大變動 不然應該是都可以多個跑完再驗。票庫應該有自己的git 。要讀寫DAG 都需用git先更新檔案,local push 成功才算真的寫入完成 / ticket_controller.sh //no llm session 會去讀dag 產生 ticket_issuer.sh xxxx 並派下去,平行度在這邊決定 ticket 要提交到哪個branch 也是這邊事先決定好的 branch 或是說PR 走到要驗證的時候 由這邊觸發regression 腳本 regression 有紅的話 call auto_fixer … regression 綠了就發版 腳本重新去DAG 找事做 / ticket_issuer.sh --id 123 --taregt branch 腳本根據票派 worker · 驗證者,worker +驗證者 都done 跑 (票上需要覆核者 的話 這個階段發出去), 然後 跑驗證者寫的CASE ,有過的話提交一個PR , 沒過就自己繼續修。worker 要反駁票開錯 的話 讓他自己用prompt 啟動開題者討論 並修改票面」
- 確認題原話:「一條整合分支 就是一個PR ticket_controller 應該不需要合PR 他是腳本 沒能力處理衝突 / 發版」是指合進 main +發布 / PR本地標記 / regression 紅了,在整合分支上修 / 兩個修補迴圈的上限先設 3 輪 / 票庫放哪裡? 這邊是控制系統 票庫預設是A的sub repo」
- 取代 D-078 之後主線提的 `docs/design/dev-loop-v2.md`(一個 actl run + 三階段);設計文件改寫成這一版後才開重寫票。
- 推翻:CLAUDE.md「發版永遠是人授權、主線執行」—— 改為 regression 綠 → controller 合進 main 並發版(各專案的發版指令與範圍待定,見設計文件的例外題)。
- 主線解讀(未經使用者):ticket 分支由 issuer 的 worker 自己合進整合分支(衝突由 LLM 解;push 被拒就 pull 重來,與票庫同一條規則);controller 只做不需要判斷的事。

## D-080(2026-10-31)v2 的三個答案、開題者回報範圍、這次改動直接當一般專案改
- 原話:「開題者發現 問題跟大方向,商業邏輯 ,使用者體驗 等等相關的時候 要回報主線 / 1 一組裡有一張票卡住了 他為什麼會卡住? 流程哪裡有缺陷? 可能issuer 要有3小時timeout機制? / 2 專案中一定要有發版腳本阿,controller 只會去呼叫他 / 3 ok / 我希望這次的改動不要用A自己的規矩,讓claude code 把他當成一般專案來直接改,腳本不一樣要sh ,py 或是其他也行 高效 高相容 要有os exception 處理機制」
- ① 開題者遇到大方向、商業邏輯、使用者體驗未定的地方,交 question 給主線,不自己猜(`loop/prompts/creator/01-role.md`)。
- ② 卡住的來源與流程修正:案例寫錯 → worker 可經 `--revise` 讓開題者判「案例錯」並重寫;票太大 → 開題規矩「一張票 = 一次 session」;卡死 → issuer 3 小時、每次叫 agent 1 小時;要你決定 → 回報主線。一組裡卡住的票不拖住其他票(主線解讀,使用者先前未反對)。
- ③ 每個專案必須有發版腳本(`board/config.json` loop.release,沒有 controller 不啟動);controller 只呼叫它。
- ④ 票庫 = A 的 sub repo:`pools/<專案>`。
- ⑤ 這次改動不走 A 的票 / land 流程:主線在分支 `claude/repo-access-check-s7faow` 直接寫 `loop/`(Python 標準函式庫,Linux / macOS,OS 例外集中處理)與 `tests_loop/`,合進 main 等使用者看過。

## D-081(2026-10-31)開題規矩:一票 = 一句話講得完的目的,並估 token;不暗示開發時長
- 原話:「開題者的規矩不該(一張票要能在一次 session 內做完,做不完就拆票)我感覺意義不大: 應該是限制一票的功能要是能被一句話描述的目的,不要暗示他開發的時長,只要叫他一併評估需要消耗的token 就可以」
- 取代 D-080 ② 的「一張票 = 一次 session」。`loop/prompts/creator/02-rules.md`:一張票一個目的,`objective` 就是那一句(要用「和」才講得完就是兩張票);每張票必填 `token_estimate`(正整數,worker + 驗證者合計)與 `token_basis`(怎麼估的),只供規劃、不是上限。`ticket_creator.py` 驗證 `token_estimate`。
