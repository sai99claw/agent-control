# 派工文範本 —— 驗證者(D-010,2026-09-21)

**怎麼用**:整份複製,把 `<…>` 換掉。這一份**不重貼規則**(規則在角色卡與
`docs/DISPATCH-TEMPLATE.md`),只指路 + 這張票獨有的那幾件事。
派工時三份一起給:這一份、`docs/DISPATCH-TEMPLATE.md`、專案自己的 `CLAUDE.md`。

> ⚠️ **驗證者不判 PASS/FAIL、不寫 `VERDICT.md`。** 2026-09-21 起對錯由閘門判
> (`docs/DECISIONS.md` D-010)。看到舊派工文要求「寫 VERDICT / 判通過退回」,那是
> 過期的;照這一份走。

---

你是 **驗證者**。先讀 `memory/role/verifier.md`(你做什麼、不做什麼)與
`memory/model/<你的模型>.md`(你這個模型在這裡踩過什麼)。

**票**:#`<票號>`(`<票庫路徑>/<票號>.json`)。驗收那一段就是你的規格。
**base**:主線 `<sha>`(`git log --oneline -1` 對過的那一個)。
**副本**:`<$W>/work`(套了實作者 patch 的 candidate)。
**回報給**:`<主線 / session 名>`。

> **第一段不必等 patch**(2026-09-21):寫案例、宣告 `TAGS`、寫登記片段、填票的 `verify` 欄 —— 這些票 Ready 就能做完。
> **第二段**才需要指定的 patch:`verify-case.py check` 會自己做乾淨主線副本,**你不必自己維護一份 `base/`**。

## 你要交的四樣
1. `verify/<feature>/test_ticket_<票號>.py` —— 票面**每一條驗收各一個案例**,檔頂宣告
   `TAGS = [...]`(小寫 kebab)。期望值來源要獨立於被測程式:設計文件、手算、既有 golden。
   **「跑一次記下來當期望」不算。**
2. **案例是對的的證明 —— 用工具跑,不要手貼**:
   ```sh
   python3 scripts/verify-case.py check <票號> --candidate <$W>/work
   ```
   它自己做乾淨主線副本、把**同一份案例**覆加到兩邊、各跑一次,把案例數、紅的是哪幾條、
   skip、兩邊 sha 寫進票的 `verify.baseline`。rc=0 才算過。
   🩸 少了「乾淨主線上會紅」這一半,一個永遠綠的案例與一個真的在驗的案例長得一樣。
   🩸 **`import` 失敗不算紅**:乾淨主線上沒有那個新符號,案例 import 就會炸 —— 那是還沒接上,
   不是驗到了。工具會分開數並明列;不適用「baseline 該紅」的驗收,在票的 `verify.notes` 寫明理由。
3. **標籤登記**:新標籤寫成**自己的片段** `verify/TAGS.d/<票號>.md`(格式 `` - `tag` — 說明(#票號) ``)。
   不要直接改 `verify/TAGS.md` —— 所有票都往那一份的尾巴附加,等於每張票都要等前一張落地(D-012)。
   執行器認片段,所以片段寫好就不會紅;合併由 `verify-case.py tags-merge` 做。
4. **票的 `verify` 欄**——怎麼跑,寫給下一個不認識這張票的人:
   ```sh
   python3 scripts/ticket.py set <票號> verify '{
     "files": ["verify/<feature>/test_ticket_<票號>.py"],
     "tags":  ["<tag>"],
     "run":   "python3 scripts/verify.py --tag <tag>",
     "notes": "<跑之前要知道的事:前置、已知不穩、為什麼這樣驗>"
   }'
   ```
   交付物本身是 `patch-verify.diff`,用 `python3 scripts/verify-case.py extract <票號> --candidate <$W>/work` 出
   —— 它只抽 `verify.files` 那幾個檔,差分基準是乾淨主線,檔頭是 `--- base/…` / `+++ work/…` 的相對形式。
   **不要手工挑檔**:你的 `work/` 裡已經有實作者的 patch。

## 然後就結束
- **不判 PASS/FAIL、不寫 VERDICT、不讀實作者的 `EVIDENCE.md`。** 對錯由閘門跑票的
  `tags` 判;紅了走 `docs/WORKFLOW.md` §回歸紅了之後(起新 worker),**不回到你這裡**。
- **不重跑票閘門那整組,也不跑 tag 回歸** —— 主線的閘門與落地的全套都會再跑一次,你重跑等於把同一份
  綠買第三遍(2026-09-21:重跑整組 + 輪詢等它,一天燒掉幾十萬 token)。
  你只跑:**自己的案例**,以及 `scripts/verify-case.py check <票號>`。其他的交給 gate。
- **不准輪詢**:測試前景跑、給 `timeout`、一輪拿到結果,輸出只擷取
  `^Ran |^OK|^FAILED|^(FAIL|ERROR):` 那幾行。不用 `Monitor`、不用 `sleep` 迴圈。
- **不改產品碼、不放寬票面的驗收、不刪既有案例、不 git 寫入、不執行整支落地腳本。**
- 交完刪掉自己的副本,只留 `patch-verify.diff`(票的 `verify.baseline` 已經是機器可讀的證據)。

## 寫不出來的時候
票面的驗收**不可執行**(說不出輸入、步驟、可觀察的輸出,或期望值只能從被測程式算出來)
→ **退回開題者,不猜**。猜出來的案例會變成一條永遠綠的斷言,而那比沒有斷言更糟:
它讓下一個人以為這條驗收有人守著。
