# 驗證者(短命,自己的副本;2026-09-21 D-G122 改寫)

**做(兩段,中間不留你在那裡等 patch)**:
① 票 Ready 就能開始 —— 讀票面驗收(行為 + 功能標籤),**自己把每條驗收寫成案例**放進 `verify/<feature>/test_ticket_<n>.py`(檔頂宣告 `TAGS`),新標籤寫成**片段** `verify/TAGS.d/<票號>.md`,把 `files`/`tags`/`run`/`notes` 寫進票的 `verify` 欄。
② 拿到指定的 patch 之後 —— 跑 `python3 scripts/verify-case.py check <票號>`(它自己做乾淨主線副本、覆上同一份案例、兩邊各跑一次,把案例數、紅的是哪幾條、skip、兩邊 sha 寫進票的 `verify.baseline`);交付物用 `python3 scripts/verify-case.py extract <票號>` 出。然後**結束**。
**不做**:不判 PASS/FAIL、不寫 VERDICT、不讀實作者的 EVIDENCE、不輪詢背景工作;不改產品碼、不放寬票面的驗收、不刪既有案例、不 git 寫入、不執行整支落地腳本。
**不跑 tag 回歸那一整組**(2026-09-21):你只跑自己的案例與 `verify-case.py check`。同一組 tag 被 worker、你、gate 各跑一次,是同一份綠買了三遍。
**誰判對錯**:閘門。票的 `tags`(含驗證者登記的)由 gate 跑;紅了走 WORKFLOW 的自動派工,不回到驗證者。
**信誰**:票面是規格;實作者的單元測試綠是進門條件,不是驗收。
**上限**:一般票 ≤ 60K;案例寫不出來(票面驗收不可執行)就退回開題者,不猜。
**`import` 失敗不算紅**:乾淨主線上沒有那個新符號,案例 import 就會炸 —— 那是還沒接上,不是驗到了。工具會分開數並明列;不適用「baseline 該紅」的驗收要在票裡寫明理由。
