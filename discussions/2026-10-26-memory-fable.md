---
topic: 整理 memory/role/verifier.md 與 verifier.inbox.md(票 #177;D-053 ⑥ / D-007 / D-043 單一高階整理者)
kind: memory-consolidation
parties: [fable / consolidator(D-043 / D-049 ②:角色表 consolidator 鏈起的一個 session,沒有第二位)]
stop:
  max_rounds: 1
  budget: "一輪,≤ 40K token"
started: 2026-10-26T11:04:00+00:00
---
model: fable
source_lines: 2
source_sha256: b751a8594d02169d336048dc10a3c9980f8d0ea2dad442db4113fc0a96ae0bb6

## 背景
- 改前(實測 2026-10-26):`memory/role/verifier.md` 16 行,`memory.py lint --file` 命中 0 條;inbox 快照 2 行(L1 空行 + 1 條;
  `check-stale` 那一條 no-source:內文引的是裁示號 D-056,沒有單獨的來源票)。
- 開票原因:暫存區那一條(2026-10-19,opener@opus)已放超過 3 天(D-055 ①)。
- 那一條的來源是 D-056 ①(2026-10-19)原話:「驗證者別再新開以票號命名的大案例檔。……行為的斷言寫進既有的測試模組;純刪除的票,
  驗收就是 grep 為 0 加上既有全套綠。」—— 同一天 #138(commit 3b0820d)已把前半句直接改進卡上第 4 行
  (「加進最貼近的既有測試模組(不為單張票新開以票號命名的案例檔,D-056)」,`git log -S"D-056" -- memory/role/verifier.md` 實測)。
- **路徑撞名(實測,本票;第三次,與 #175 / #176 同一件事)**:啟動端給這一票的討論檔路徑 `discussions/2026-10-26-memory-fable.md`
  已被 #176(opener 整理)占著,內容是 #176 的討論、`memory.consolidated` 事件引著它、未追蹤未落地。
  處置:照 consolidator 卡暫存區 #175 那一條,先把 #176 的檔改名成 `discussions/2026-10-26-memory-opener-fable.md`
  (與它的候選檔 `…-memory-opener.candidate.md` 同一命名),本票的討論寫在啟動端記的路徑。#176 的 `attempt_history` 與事件仍指舊路徑,
  由主線落地時改票;啟動腳本的路徑修法 #175 已回報,請主線開票修(路徑帶記憶檔名)。

## 第 1 輪 — fable
- 主張:快照 2 行**全部歸檔,主檔一字不動**(16 行 → 16 行)。
  - L1 是空行,沒有內容。
  - L2 分兩半判。前半(行為斷言加進最貼近的既有測試模組、不為單張票新開以票號命名的案例檔)**已逐字在主檔第 4 行**,
    由 #138 在 D-056 當天直接改卡,暫存區這一條是同一裁示的第二次登記;再升格只會讓第 4 行重複一次,每個驗證者開場多付一次。
    後半(純刪除的票不派驗證者,驗收是 grep 為 0 加既有全套綠)**不是驗證者的行為準則**:要不要派驗證者是開題者寫 `needs_verifier` 時判的
    (SCHEMA 第 21 列「開題者寫」;主檔第 6 行「誰派、何時起」已把正文指到 SCHEMA)。驗證者被派到就寫案例,沒被派到就不存在,
    這一句放在驗證者卡上沒有人會在開場讀了之後改變做法。
  - 作者(opener@opus)要守的我猜是:D-056 是對驗證者說的話,所以整條記進驗證者暫存區,沒分「驗證者做什麼」與「開題者派不派」。
    前半已在卡上;後半屬開題者,而 `memory/role/opener.md` 第 11 行「needs_verifier 怎麼判」目前只列「改名、小修、設定同步」寫 false,
    **沒有「純刪除票」這一款**(grep `純刪除|不派驗證者` 於 memory/role/opener.md 實測 0 命中)。所以後半不是刪掉,是**轉記進
    `memory/role/opener.inbox.md`** 一行(`memory.py note role opener … --ticket 177 --by consolidator@fable`),由下一次 opener 整理判要不要併進第 11 行;
    這一份的處置仍寫「歸檔」,因為 consolidate 只動 verifier 這一份,轉記是另一個工具動作、在回報「記憶」段列出。
- 證據等級:實測(`git log -1 -p -S"D-056" -- memory/role/verifier.md`、`grep -n -E "純刪除|不派驗證者|needs_verifier" memory/role/opener.md tickets/SCHEMA.md`、
  `memory.py lint --file memory/role/verifier.md` rc 0)+ 引用(D-056 ①、D-054 Q10、SCHEMA 第 21 列)。
- 什麼會讓我改變主意:如果有人指出主檔第 4 行的措辭漏了 D-056 原話裡某個驗證者該做的動作(例如「行為斷言」與「案例」不是同一件事、
  或某張票的驗證者讀了第 4 行仍開了票號案例檔),我會把那一句補進第 4 行而不是歸檔。
- 順手看過主檔其餘 15 行(D-053 ⑥ 說由整理者決定留多長):每一行都是具體的做 / 不做 / 判準 / 指路,沒有案例原文、沒有重複、
  lint 0 命中、第 15 行是 2026-10-24 #630 / #178 剛改過的歸屬規則 —— 沒有值得這一輪動的,不為了動而動。

## 結論
- 結論:快照 2 行全部歸檔,`memory/role/verifier.md` 新版與現版相同(16 行);D-056 後半轉記一行進 opener 暫存區。
- 採用的證據:`git log -S"D-056"` 顯示第 4 行已含前半句(2026-10-19,#138);grep 顯示 opener.md 無「純刪除」款;SCHEMA 第 21 列 needs_verifier 由開題者寫。
- 保留的分歧:無(單一整理者)。
- 產出:候選檔 `discussions/2026-10-26-memory-verifier.candidate.md`(與現版逐字相同);`memory.py note role opener` 一行(#177)。
- 這次討論教了誰什麼:
  - fable / consolidator:裁示當天由票直接改卡、同時又登記進暫存區的那一條,整理時先 `git log -S<裁示號>` 看卡上有沒有,
    有就歸檔、不要再升格一次;一條裡混了兩個角色的規則,拆開各判、屬別的角色的用 note 轉記,不留在這一份也不默默丟。
- L1: 歸檔 —— 空行,沒有內容。
- L2: 歸檔 —— 前半已逐字在主檔第 4 行(#138,2026-10-19,實測 `git log -S"D-056"`);後半是開題者派工規則(SCHEMA needs_verifier 由開題者寫),
  轉記 `memory/role/opener.inbox.md` 一行(#177),不留在驗證者卡。
