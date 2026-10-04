# 派工文範本 —— 整理者(consolidator;D-006 / D-007 / D-013;#29 A8,2026-09-23)

**怎麼用**:
- **自動(#87 A39–A41)**:主線開場由 `scripts/new-session.sh` 對每張 role=consolidator、Ready、沒有有效租約的
  整理票背景叫 `scripts/consolidate-memory.sh`;它依 `board/config.json` 的 `memory.consolidators` 每個模型起一個
  session,這一份由它逐格填好、接在規則包之後從 stdin 餵。同一張票**租約排他**:先拿到租約的那一組才起,另一個
  主線開場不再起。命令缺、規則包產不出來、範本找不到、session 退出非零、逾時 → 票轉 NeedsDecision、租約清空,
  收件匣一頁 decision。下面的 `<…>` 是它填的佔位,名字一個都不要改(改了它就填不到)。
- **人工**:整份複製,把 `<…>` 換掉,**兩個模型各開一個 session、各貼一次**。
前言用 `python3 scripts/rules.py pack consolidator --model <模型>` 產(≤ 4 KB);它非零就**不派**,照 stderr 補缺項,不要手寫前言代替(#74)。

> ⚠️ **一個 session 自己整理不算數**(D-013)。一個 session 刪自己的記憶時,最先刪掉的是
> 它自己看不懂的那幾條 —— 而那正是另一個模型看得出價值的那幾條。

---

你是 **整理者**。先讀上面規則包標題列的角色卡與模型記憶(路徑是 pack 解析過的那一份)。

## 這一次獨有的四件事
1. **整理票**:#`<票號>`(`<票庫路徑>/<票號>.json`;`memory.py check` 自動開的)
2. **要整理的檔**:`<memory/model/x.md 或 memory/role/x.md>`(現在 `<N>` 字元,上限 `<cap>`)
3. **你的討論檔**:`discussions/<date>-memory-<你的模型>.md`(格式見 `docs/DISCUSSION.md`;
   另一位寫 `discussions/<date>-memory-<對方模型>.md`)
4. **回報對象**:`<主線 / session 名>`

## 兩個模型各做什麼(**先各自寫,再互讀**)
- **第零段(開場,兩位同一份)**:`python3 scripts/memory.py snapshot <要整理的檔>`,把印出的
  `source_lines` / `source_sha256` 兩行連同 `model: <你的模型>` 抄進自己討論檔的檔頭;
  逐條來源分類看 `python3 scripts/memory.py check-stale --read-only --file <要整理的檔>`。
- **第一段(各自,不看對方)**:讀那一份記憶檔與它的 `.inbox.md`(前 `source_lines` 行),
  `## 結論` 區對每一行寫 `- L<n>: <保留|升格|移至 reference|歸檔|重複>`(其後可接目的地),
  每一條寫**為什麼**。先各自寫,是為了不讓第二個人只是附和第一個人。
- **第二段(互讀)**:讀對方的討論檔,把**分歧**逐條寫下來(沒有分歧就寫「同意,理由是…」,
  不要留白 —— 一份沒有分歧欄的討論檔,與沒有討論長得一樣)。
- **第三段(收)**:`<先寫完的那一位>` 把整理後的新版寫成候選檔,跑合併那一句:
  ```sh
  python3 scripts/memory.py consolidate <要整理的檔> --candidate <候選檔> \
    --discussion discussions/<date>-memory-<模型 A>.md --discussion discussions/<date>-memory-<模型 B>.md
  ```
  少一份討論、兩份 model 相同、快照對不上、缺某一行的處置、候選超過上限,它都拒絕且不動任何檔
  (D-007 / D-013)。通過才換主檔、只消耗那 K 行,並自己發 `memory.consolidated` 事件。

## 整理出來要長什麼樣
- 只留**具體的原則、行為準則、思考方式**;**不直接寫案例** —— 案例用票號指路
  (例:「快照層只畫說得出處的畫面(#585)」)。案例原文留在紀錄類文件(DECISIONS / HANDOFF)。
- 每一條仍帶**日期 / 來源票號 / 實測或推論**三個標記。
- 壓不下來就**提高上限**:`cap_history` 要有一列寫得出「多讀的那幾百字省了什麼」(D-007)。

## 收工
`python3 scripts/memory.py check` 對那一份退出碼 0(或上限已合法提高),再 `ticket.py close <票號>`。
