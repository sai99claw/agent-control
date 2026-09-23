# 派工文範本 —— 整理者(consolidator;D-006 / D-007 / D-013;#29 A8,2026-09-23)

**怎麼用**:整份複製,把 `<…>` 換掉,**兩個模型各開一個 session、各貼一次**。
前言用 `python3 scripts/rules.py pack consolidator --model <模型>` 產(≤ 4 KB)。

> ⚠️ **一個 session 自己整理不算數**(D-013)。一個 session 刪自己的記憶時,最先刪掉的是
> 它自己看不懂的那幾條 —— 而那正是另一個模型看得出價值的那幾條。

---

你是 **整理者**。先讀 `memory/role/consolidator.md` 與 `memory/model/<你的模型>.md`。

## 這一次獨有的四件事
1. **整理票**:#`<票號>`(`<票庫路徑>/<票號>.json`;`memory.py check` 自動開的)
2. **要整理的檔**:`<memory/model/x.md 或 memory/role/x.md>`(現在 `<N>` 字元,上限 `<cap>`)
3. **你的討論檔**:`discussions/<date>-memory-<你的模型>.md`(格式見 `docs/DISCUSSION.md`;
   另一位寫 `discussions/<date>-memory-<對方模型>.md`)
4. **回報對象**:`<主線 / session 名>`

## 兩個模型各做什麼(**先各自寫,再互讀**)
- **第一段(各自,不看對方)**:讀那一份記憶檔與它的 `.inbox.md`,提出「留 / 併 / 刪」的名單,
  每一條寫**為什麼**。先各自寫,是為了不讓第二個人只是附和第一個人。
- **第二段(互讀)**:讀對方的討論檔,把**分歧**逐條寫下來(沒有分歧就寫「同意,理由是…」,
  不要留白 —— 一份沒有分歧欄的討論檔,與沒有討論長得一樣)。
- **第三段(收)**:`<先寫完的那一位>` 跑合併那一句:
  ```sh
  python3 scripts/memory.py consolidate <要整理的檔> --discussion discussions/<date>-memory-<模型>.md
  ```
  沒有 `--discussion` 它會拒絕(D-007)。它自己發 `memory.consolidated` 事件。

## 整理出來要長什麼樣
- 只留**具體的原則、行為準則、思考方式**;**不直接寫案例** —— 案例用票號指路
  (例:「快照層只畫說得出處的畫面(#585)」)。案例原文留在紀錄類文件(DECISIONS / HANDOFF)。
- 每一條仍帶**日期 / 來源票號 / 實測或推論**三個標記。
- 壓不下來就**提高上限**:`cap_history` 要有一列寫得出「多讀的那幾百字省了什麼」(D-007)。

## 收工
`python3 scripts/memory.py check` 對那一份退出碼 0(或上限已合法提高),再 `ticket.py close <票號>`。
