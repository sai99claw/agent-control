# 派工文範本 —— 整理者(consolidator;D-053 ⑥ / D-007 / D-043 / D-049 ②;#122)

**怎麼用**:
- **自動(#122,原 #87 A39–A41)**:主線開場由 `scripts/new-session.sh` 對每張 role=consolidator、Ready、沒有有效租約的
  整理票背景叫 `scripts/consolidate-memory.sh`;它照 `board/role-models.json` 的 `consolidator` 鏈(`role-models.py resolve
  --role consolidator`,現在只有 fable)起**一個** session,這一份由它逐格填好、接在規則包之後從 stdin 餵。票上的 `model`
  不參與(#131 A10 只套 worker 鏈);`board/config.json` 的 `memory.consolidator(s)` 已不讀。同一張票**租約排他**:先拿到
  租約的那一個才起,另一個主線開場不再起。候選額度用完 / 不可用才換鏈上下一個;fable 用不了就是整串耗盡 —— 票轉
  NeedsDecision、租約清空、收件匣一頁 decision,不改派別的模型。規則包產不出來、範本找不到、session 退出非零、逾時同樣送
  decision。下面的 `<…>` 是它填的佔位,名字一個都不要改(改了它就填不到)。
- **人工**:照 `python3 scripts/role-models.py resolve --role consolidator` 印的模型開一個 session,整份複製、把 `<…>` 換掉貼一次。
  人工派的討論檔沒有啟動端紀錄,`consolidate` 照舊要兩份不同模型的討論檔 —— 單一整理者的路只認 `consolidate-memory.sh` 記下的那一份。
前言用 `python3 scripts/rules.py pack consolidator --model <模型>` 產(≤ 4 KB);它非零就**不派**,照 stderr 補缺項,不要手寫前言代替(#74)。

> ⚠️ **同一個模型的 session 自己整理自己的記憶不算數**(D-013)。算數的是一個明確更高階的模型(D-013 ②「或一個高階模型」,
> D-043:fable 單獨整理;D-049 ② 記憶整理者 fable)—— 它不是被整理那一層的作者,所以由角色表選、不由票選。

---

你是 **整理者**。開場只讀角色卡(上面規則包標題列的路徑,pack 解析過的)+ 整理票;模型記憶規則包已帶。

## 這一次獨有的四件事
1. **整理票**:#`<票號>`(`<票庫路徑>/<票號>.json`;`memory.py check` 自動開的)
2. **要整理的檔**:`<要整理的檔>`(它的暫存區有未整理的條目,D-053 ⑥)
3. **你的討論檔**:`discussions/<date>-memory-<你的模型>.md`(格式見 `docs/DISCUSSION.md`;啟動端已把這個路徑記進票的 `attempt_history`)
4. **回報對象**:`<主線 / session 名>`

## 你一個人做完三段(沒有第二位、沒有互讀)
- **第零段(開場)**:`python3 scripts/memory.py snapshot <要整理的檔>`,把印出的
  `source_lines` / `source_sha256` 兩行連同 `model: <你的模型>` 抄進討論檔的檔頭;
  逐條來源分類看 `python3 scripts/memory.py check-stale --read-only --file <要整理的檔>`。
- **第一段(處置)**:讀那一份記憶檔與它的 `.inbox.md`(前 `source_lines` 行),
  `## 結論` 區對每一行寫 `- L<n>: <保留|升格|移至 reference|歸檔|重複>`(其後可接目的地),
  每一條寫**為什麼**。作者沒寫清楚的那一條,寫出你猜它要守什麼、為什麼仍刪或仍留 —— 沒有第二位替你補看。
- **第二段(收)**:把整理後的新版寫成候選檔 `<候選檔>`,跑合併那一句:
  ```sh
  python3 scripts/memory.py consolidate <要整理的檔> --candidate <候選檔> \
    --discussion discussions/<date>-memory-<你的模型>.md
  ```
  討論檔不是啟動端記的那一份、檔頭 model 對不上、快照對不上、缺某一行的處置、候選有錯層內容,它都拒絕且不動任何檔
  (D-007 / D-013)。通過才換主檔、只消耗那 K 行,並自己發 `memory.consolidated` 事件。

## 整理出來要長什麼樣
- 判準只有一句:**每個 session 開場都會讀，只留值得這個成本的**。寫什麼、留多長由你決定,沒有字數目標(D-053 ⑥)。
- 只留**具體的原則、行為準則、思考方式**;**不直接寫案例** —— 案例用票號指路
  (例:「快照層只畫說得出處的畫面(#585)」)。案例原文留在紀錄類文件(DECISIONS / HANDOFF)。
- 每一條仍帶**日期 / 來源票號 / 實測或推論**三個標記。
- 你覺得**角色卡通用規則**該改(不只這一份記憶):寫一行進你那張角色卡的 `.inbox.md`(規則包標題列那條路徑旁的同名檔,不要自己拼),由下一次整理消化;
  不要順手改模型政策(`board/role-models.json`、`docs/ROLES.md` 的模型表)。

## 收工
合併那一句退出碼 0(snapshot 那 K 行已移進 `.consumed`),再 `ticket.py close <票號>`;整理期間新記的條目留在暫存區,等下一張整理票。
