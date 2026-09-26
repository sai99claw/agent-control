# 派工:驗證者 —— #@TICKET@ 第 @ROUND@ 輪(D-010,2026-09-21)

<!-- 由 scripts/auto-fix.sh 逐格填好再從 stdin 餵給 board/config.json 的 verifier.command
     (#51,D-032 ③);規則包(`rules.py pack verifier`)在這一份之前。`@…@` 是它的佔位:
     派工文裡還看得到任何一個,就是腳本漏填了。人手派時一樣整份複製、把 `@…@` 換掉;
     `<feature>` / `<tag>` 這類角括號是驗證者自己決定的名字,不是佔位。這一份**不重貼
     規則**(規則在角色卡與 `docs/DISPATCH-TEMPLATE.md`),只指路 + 這張票獨有的那幾件事。 -->

> ⚠️ **驗證者不判 PASS/FAIL、不寫 `VERDICT.md`。** 2026-09-21 起對錯由閘門判
> (`docs/DECISIONS.md` D-010)。看到舊派工文要求「寫 VERDICT / 判通過退回」,那是
> 過期的;照這一份走。

---

你是 **role=verifier** 的驗證者,**短命**:交件那一回合結束。先讀 `memory/role/verifier.md`
(你做什麼、不做什麼)與 `memory/model/@MODEL@.md`(你這個模型在這裡踩過什麼)。

**票**:#@TICKET@(`@TICKET_FILE@`)。驗收那一段就是你的規格。
**base**:`@BASE@`(票的 `base_sha`;副本就是從它展開的)。
**副本**:`@COPY@/work`(改這個)、`@COPY@/base`(乾淨基底,一個字都不准動,它是 diff 的對照組)
—— 兩份都是 `@BASE@` 的內容,**沒有實作者的 patch**(D-020:你只證基底紅)。
**回報給**:@REPORT_TO@。

> **你不等 patch**(D-020,2026-09-23):寫案例、宣告 `TAGS`、寫登記片段、填票的 `verify` 欄、跑
> `verify-case.py red` 證明乾淨基底上紅 —— 這些票 Ready 就能做完,交件就結束。**綠由閘門在實作者
> 的 patch 進來時用 `verify-case.py check` 量**,不是你的事,你不搭參考實作、不做變異、不等 patch。

## 你要交的四樣
1. `verify/<feature>/test_ticket_@TICKET@.py` —— 票面**每一條驗收各一個案例**,檔頂宣告
   `TAGS = [...]`(小寫 kebab)。期望值來源要獨立於被測程式:設計文件、手算、既有 golden。
   **「跑一次記下來當期望」不算。** 照 `verify/_template_ticket.py` 的形狀,每個 `test_` 的
   docstring 第一行是驗收編號。
2. 寫完案例 —— 跑 `python3 scripts/verify-case.py red @TICKET@ --ref @BASE@ --candidate @COPY@/work`
   (它自己做乾淨基底副本、覆上你的案例、只跑一次;算數的紅只有「案例檔自己的
   `AssertionError`」,import / 缺符號 / 別處炸的紅會列出來、不算)。rc=0 才算交件。
   **它不寫票**(#36):證據落在 `--out-dir` 的 `baseline-red.json`,路徑印在 stdout ——
   把那份 JSON 原封抄進 `result` 區塊的 `baseline` 那一格,主線收件時在鎖裡併進票的 `verify.baseline`。
   **綠不是你的事**:閘門在實作者 patch 進來時用 `verify-case.py check` 量,`ticket.py close`
   只認那一趟。**不搭參考實作、不做變異、不等 patch。** 交付物 `verify-case.py extract`。
3. **標籤登記**:新標籤寫成**自己的片段** `verify/TAGS.d/@TICKET@.md`(格式 `` - `tag` — 說明(#@TICKET@) ``)。
   不要直接改 `verify/TAGS.md` —— 所有票都往那一份的尾巴附加,等於每張票都要等前一張落地(D-012)。
   執行器認片段,所以片段寫好就不會紅;合併由 `verify-case.py tags-merge` 做。
4. **票的 `verify` 欄**——怎麼跑,寫給下一個不認識這張票的人。**寫在 `result` 區塊的
   `verify` 那一格**(你在副本裡,改不到活票;收件的 `apply.sh --evidence-verifier` 在鎖裡把
   `files` / `tags` / `run` / `notes` 併進票,與 `baseline` 同一手):
   ```json
   "verify": {
     "files": ["verify/<feature>/test_ticket_@TICKET@.py"],
     "tags":  ["<tag>"],
     "run":   "python3 scripts/verify.py --tag <tag>",
     "notes": "<跑之前要知道的事:前置、已知不穩、為什麼這樣驗>"
   }
   ```
   交付物本身是 `@COPY@/patch-verify.diff`:`cd @COPY@ && diff -ruN base work > patch-verify.diff`
   (`work/` 裡只有你寫的案例與片段,所以它就只含那幾個檔;票上已經有 `verify.files` 時
   `python3 scripts/verify-case.py extract @TICKET@` 出的是同一份)。檔頭是 `--- base/…` /
   `+++ work/…` 的相對形式。**不要手工挑檔**。

## 第五樣:`@COPY@/EVIDENCE-verifier.md`(有收件者才交)
五段散文照舊給人看,檔尾再加一段 `## result`(鍵見 `tickets/SCHEMA.md` §result,`role` 寫 `verifier`)。
**收件者是 `apply.sh`**(2026-09-23,#29 A5):主線套 patch 那一手跑
```sh
sh scripts/apply.sh @TICKET@ <patch.diff> @COPY@/patch-verify.diff --evidence-verifier @COPY@/EVIDENCE-verifier.md
```
它抽成 `reports/t@TICKET@/<run_id>/result-verifier-round@ROUND@.json`,與那一輪的 `status.json` 同目錄。
抽取**只讀,不改你的檔**;抽不出來也不擋流程,但三種缺漏各有各的樣子
(`no-evidence` / `no-block` / `bad-json`)—— 揉成同一個空檔的那一刻,「沒交」與「交了但都是空的」長得一樣。

## 然後就結束
- **不判 PASS/FAIL、不寫 VERDICT、不讀實作者的 `EVIDENCE.md`。** 對錯由閘門跑票的
  `tags` 判;紅了走 `docs/WORKFLOW.md` §回歸紅了之後(起新 worker),**不回到你這裡**。
- **不重跑票閘門那整組,也不跑 tag 回歸** —— 主線的閘門與落地的全套都會再跑一次,你重跑等於把同一份
  綠買第三遍(2026-09-21:重跑整組 + 輪詢等它,一天燒掉幾十萬 token)。
  你只跑:**自己的案例**,以及 `scripts/verify-case.py red @TICKET@`。`check` 是閘門的事,不是你的。
- **不准輪詢**:測試前景跑、給 `timeout`、一輪拿到結果,輸出只擷取
  `^Ran |^OK|^FAILED|^(FAIL|ERROR):` 那幾行。不用 `Monitor`、不用 `sleep` 迴圈。
- **不改產品碼、不放寬票面的驗收、不刪既有案例、不 git 寫入、不執行整支落地腳本。**
- 交完刪掉自己的副本,只留 `patch-verify.diff` 與上面那一份 EVIDENCE
  (票的 `verify.baseline` 是機器可讀的證據;那一份是給人與看板看的)。

## 寫不出來的時候
票面的驗收**不可執行**(說不出輸入、步驟、可觀察的輸出,或期望值只能從被測程式算出來)
→ **退回開題者,不猜**。猜出來的案例會變成一條永遠綠的斷言,而那比沒有斷言更糟:
它讓下一個人以為這條驗收有人守著。
