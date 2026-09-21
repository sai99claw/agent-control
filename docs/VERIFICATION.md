# 驗證系統(2026-09-13,使用者裁示)

兩層、兩個框架、一套標籤。

## 單元層(實作者的)
- 位置:專案原本的測試樹(例如 `demo/test_*.py`);實作者每張票附自己的單元測試,**綠了才交**。
- 誰跑:實作者自己;閘門(`gate`)用專案的對照表挑一組跑。
- 收集:同一支執行器 `scripts/verify.py --unit` 跑專案在 `board/config.json` 的 `unit_cmd`(例如 `cd demo && python3 -m unittest discover -s . -p "test_*.py"`);`--all` = 單元 + 回歸。專案沒設 `unit_cmd` 就印「未設定」rc=3,不假綠。

## 測試計畫(開題者的,票面必填)
每條驗收一列:`行為 | 層(unit/api/browser)| 怎麼驗(輸入、步驟、可觀察輸出、期望值來源)| 標籤`。
期望值來源必須獨立於被測程式(設計文件、手算、既有 golden);「跑一次記下來當期望」不算。
驗證者照計畫實作,一列一個案例;計畫寫不出可執行的驗法 → 退回開題者,不猜。

派工文範本:`templates/dispatch-verifier.md`。

## 回歸層(驗證者的)
- 位置:`verify/<feature>/test_*.py`,每個檔頂宣告 `TAGS = ["ledger-colour", "report"]`(功能標籤,小寫 kebab)。
- 來源:**驗證者**照票面的驗收(行為 + 標籤)自己實作;隨票落地,從此是回歸的一部分。**不由實作者寫**——驗收與程式不同人寫,oracle 才獨立。
- 誰跑:`scripts/verify.py --tag <t> [--tag …]`(閘門:這張票的標籤;落地:不帶 `--tag` = 全部);`--list` 印標籤與案例數。
- 守衛:每個案例檔必須有 `TAGS`,標籤必須登記過;沒登記就紅。
- **登記走一票一個片段** `verify/TAGS.d/<票號>.md`(格式與 `TAGS.md` 一樣)。`verify.py` 兩邊都認,所以片段還沒合併也不會紅;`scripts/verify-case.py tags-merge` 折進 `verify/TAGS.md`,撞名而說明不同會出聲。
  理由:所有票都往同一份登記檔尾巴附加 = 每張票都得等前一張落地(D-012 認過它是最常見的衝突點)。**開題者只宣告 tags,登記由工具合併** —— 以前文件說開票時登記、範本又要驗證者登記,兩邊都對不上。

## 流程(2026-09-21,D-010;兩段式與 baseline 由 D-014 改寫)
驗證者的工作**拆成兩段**,中間由程式保管產物,不留 agent 在那裡等 patch:

**第一段(票 Ready 就能開始,不必等 patch)**:照票面驗收寫案例、宣告 `TAGS`、寫 `verify/TAGS.d/<票號>.md`、
把 `files` / `tags` / `run` / `notes` 寫進票的 `verify` 欄。
> `files` 那一格是一把尺:**land 會檢查它們真的在分支上**,缺了 rc=4(D-015)。填了卻沒交,
> 票的 tags 會被閘門呼叫、卻一個案例都選不到 —— 那是缺口,不是綠。
**第二段(拿到指定的 patch 之後)**:`scripts/verify-case.py check <票號>` —— 同一份案例在**乾淨主線副本**上跑
(該紅)、在 **candidate 副本**上跑(該綠),工具把證據寫進票的 `verify.baseline`:案例數、紅的是哪幾條、skip 幾條、
兩邊的 sha。交付物用 `scripts/verify-case.py extract <票號>` 出(**只含自己的 verify 檔**,差分基準是乾淨主線)。
然後**結束**。

> 🩸 **`import` 失敗不算紅。** 乾淨主線上沒有那個新符號,案例 `import` 就會炸 —— 那是「還沒接上」,不是「驗到了」,
> 而兩者都讓 unittest 回非零。工具把它們分開數並**明列**;不適用「baseline 該紅」的驗收要在票裡寫明理由。

接著:gate(單元對照組 + **票的 `tags` 真的被呼叫**)→ 主線讀 patch 記 `review`(綁票版本與分支 sha)→ land(單元全套 + 回歸全部)。

**誰判對錯:閘門。** 驗證者**不判 PASS/FAIL、不寫 VERDICT、不讀實作者的 `EVIDENCE.md`、不輪詢**,
而且**不跑 tag 回歸那一整組** —— 它只跑自己的案例與 `verify-case.py check`。同一組 tag 被 worker、驗證者、gate 各跑一次
是三份同樣的綠(2026-09-21 外部審查的 token 帳)。
閘門紅了走 `docs/WORKFLOW.md` §回歸紅了之後(`scripts/auto-fix.sh` 起新 worker),**不回到驗證者**。
同一組標籤在同一輪裡只跑一次(`scripts/verify.py` 的 `reports/t<n>/<run_id>/verify-<雜湊>.log`,
雜湊含標籤與 HEAD sha;`--no-cache` 關)—— 三份一模一樣的綠只是三份帳單。
案例寫不出來(票面驗收不可執行)→ 退回開題者,不猜。

## 案例本身錯了:`test_defect`(2026-09-21,D-014)
票是對的、程式也是對的,錯的是 oracle 或 fixture。**worker 不准放寬斷言、不准改 oracle**
(它手上唯一能動的東西就是斷言,所以這條界線要由流程守,不是由自律守)。
worker 交一筆 `objections[{"category": "test_defect", "body": …, "evidence": <反例>}]`,
主線派**獨立的驗證者**修案例。需求本身有爭議才退回開題者。

## 與變異測試的關係
變異是實作者證明「自己的單元測試有牙齒」的手段,寫進 `EVIDENCE.md`;驗證者不重做變異,它的獨立性來自「案例是它自己從票面寫的」。

## 與既有專案的關係(tabby_pool)
既有 7,000+ 條測試視為單元層,不搬。回歸層從第一張帶標籤的票開始累積(UI 批 #550–#557 是第一批)。
